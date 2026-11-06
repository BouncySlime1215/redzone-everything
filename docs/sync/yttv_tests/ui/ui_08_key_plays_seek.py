#!/usr/bin/env python3
"""ui_08 Key Plays list: each entry's seek target, mapped through the live E, dates the play on screen.

HYPOTHESIS: for live NBA/NFL games YTTV shows a 'Key plays' list in the player; selecting an entry seeks
to that play in the DVR window. The media_session position right after the seek is the play's
stream time pos_k. While watching live this stick shows stream time pos at wall E_live + pos, so the
play would have been on screen at  t_k = E_live + pos_k  and
    D_raw = t_k - play.wall      (play = feed play matched to the entry text by surname + keyword)
YTTV seeks a few seconds BEFORE the play (lead), so D = D_raw + lead; lead is constant if the spread of
D_raw across entries is small. Every entry gives a sample without waiting for new plays.

PROCEDURE (hidden stick only):
  1. deep-link tune --channel; wait 5 s; E_live = mean of 3 media_session reads (must agree within 0.3 s)
  2. for k in 0..--entries-1: DPAD_DOWN; step RIGHT until the focused label matches /key plays|highlights/
     (recipe 'key_plays' if saved); DPAD_CENTER; DPAD_DOWN x k; screencap -> focused entry text;
     DPAD_CENTER (seek); wait 3 s; read position (must be 'playing'); log entry, pos_k, t_k, match, D_raw.
  3. deep-link re-tune (back to the live edge); verify E is within 1 s of E_live.

PASS: >= 3 entries matched to feed plays and max-min of D_raw <= 2.0 s.
"""
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ui_common as U  # noqa: E402
import ui_match as M  # noqa: E402

ID = "ui_08_key_plays_seek"
PAT = r"key ?plays|highlights|key moments"


def extra(ap):
    ap.add_argument("--entries", type=int, default=5)


def open_list(g):
    # saved key lists are not replayed blind (a LEFT/RIGHT on the scrubber seeks): always navigate by focus
    nav = U.navigate_to(g, PAT)
    if nav["found"]:
        g.key("DPAD_CENTER")
        U.save_recipe("key_plays", dict(keys=nav["keys"] + ["DPAD_CENTER"], label=nav["text"]))
    return nav


def live(args, g, out):
    if not g.ok():
        return dict(skipped="stick on screen or unknown", pass_=None)
    g.tune(args.channel); time.sleep(5)
    es = [U.ms_raw(g.host).get("E") for _ in range(3)]
    es = [e for e in es if e]
    if len(es) < 3 or max(es) - min(es) > 0.3:
        return dict(err="E_live unstable", E=es, pass_=False)
    E_live = sum(es) / 3
    samples = []
    for k in range(args.entries):
        nav = open_list(g)
        if not nav.get("found"):
            out.write("no_list", **nav); break
        time.sleep(1.5)
        if k:
            g.key(*(["DPAD_DOWN"] * k))
        p, w, o = U.snap(g, f"{ID}_{k}")
        entry = U.focused_text(o)
        g.key("DPAD_CENTER"); time.sleep(3)
        m = U.ms_raw(g.host)
        if m.get("st") != "playing" or m.get("pos") is None:
            out.write("entry", k=k, entry=entry, err="not playing after seek"); continue
        t_k = E_live + m["pos"]
        plays = U.load_plays(args.date, False, game=args.game)
        pl, sc = M.match_text(entry, t_k, plays, lo=-30, hi=400)
        row = dict(k=k, entry=entry, pos=round(m["pos"], 2), t_k=round(t_k, 2), score=sc)
        if pl:
            row.update(play=pl.get("text"), D_raw=round(t_k - pl["wall"], 2)); samples.append(row["D_raw"])
        out.write("entry", **row)
    g.tune(args.channel); time.sleep(3)
    e_end = U.ms_raw(g.host).get("E")
    s = U.summarize(samples)
    ok = s.get("n", 0) >= 3 and (s["hi"] - s["lo"]) <= 2.0
    return dict(D_raw=s, E_live=E_live, E_after_retune=e_end, pass_=bool(ok))


def dry(args, out):
    random.seed(5)
    plays = U.load_plays(args.date, True, game=args.game)
    D_true, lead = 29.0, 4.0
    picks = [p for p in plays if M.play_tokens(p)[0] and M.play_tokens(p)[1]]
    picks = random.sample(picks, min(args.entries, len(picks)))
    samples = []
    for p in picks:
        entry = M.caption_for(p).replace(" ON THE PLAY", "")
        t_k = p["wall"] + D_true - lead + random.uniform(-0.5, 0.5)
        pl, sc = M.match_text(entry, t_k, plays, lo=-30, hi=400)
        row = dict(entry=entry, truth=p.get("text"), matched=pl and pl.get("text"), D_raw=pl and round(t_k - pl["wall"], 2))
        out.write("synthetic_entry", **row)
        if pl:
            samples.append(row["D_raw"])
    s = U.summarize(samples)
    out.write("would", steps=["tune", "E_live x3", "DPAD_DOWN", "RIGHT..until /key plays/", "CENTER", "DOWN x k",
                              "screencap", "CENTER (seek)", "read pos", "re-tune"], entries=args.entries)
    return dict(synthetic=s, D_true_minus_lead=D_true - lead, pass_=None)


if __name__ == "__main__":
    sys.exit(U.run(ID, __doc__, live, dry, extra))
