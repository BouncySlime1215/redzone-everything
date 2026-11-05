#!/usr/bin/env python3
"""ui_07 YTTV in-player Stats / Scores panel: clock + score changes vs feed walls.

HYPOTHESIS: during live NBA games YTTV offers a stats/scores panel in the player (button on the overlay
row, e.g. 'Stats' / 'Scores' / 'Box score'; for SportsCenter only Networks/Multiview/Episodes/More to watch
exist). If the panel is spoiler-synced to the PLAYHEAD, the instant a new score appears in it is the
instant that basket is on screen:  D = t_first_seen - play.wall  (play = the feed play that produced
exactly that score; ESPN summary wallclock). If instead it is real-time, D ~ 0..10 s and the panel is
a fast feed, not a delay meter (also useful: log which it is).

PROCEDURE (hidden stick only):
  1. tune --channel; DPAD_DOWN; step focus right along the button row until the focused label matches
     /stats|scores|box score|game ?cast/ (never CENTER on anything else); DPAD_CENTER opens it.
     If no such button in 10 steps: log 'no panel' + the row labels and stop (BACK).
  2. every --every s (>= 1.0): screencap + OCR; parse every standalone integer 0..199 and the clock
     ('Q3 5:42', '3rd 5:42', '45.6'); new score pair (both numbers present, absent last frame) that
     matches exactly one ESPN summary score -> sample D (onset = first frame - half the gap).
     Clock: first frame showing (period, clock) equal to a summary play's -> sample D_clock.
  3. BACK x2, re-tune.

PASS: >= 4 score samples with MAD <= 1.0 s (panel synced; coincidental integers make outliers, so MAD not sd) -- report 'realtime' when median D < 8 s.
"""
import random
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ui_common as U  # noqa: E402

ID = "ui_07_stats_panel"
_INT = re.compile(r"^\d{1,3}$")
_CLK = re.compile(r"\b(\d{1,2}:\d{2}|\d{1,2}\.\d)\b")
_PER = re.compile(r"\b(1st|2nd|3rd|4th|Q[1-4]|OT\d?|[1-4](?:st|nd|rd|th) Qtr)\b", re.I)


def extra(ap):
    ap.add_argument("--every", type=float, default=1.5)
    ap.add_argument("--league", default="nba")


def parse_panel(o: dict) -> dict:
    ints, clock, per = set(), None, None
    for t in o["obs"]:
        s = t["text"].strip()
        for tok in re.split(r"[\s|•·-]+", s):
            if _INT.match(tok) and int(tok) < 200:
                ints.add(int(tok))
        if clock is None and _CLK.search(s):
            clock = _CLK.search(s).group(1)
        if per is None and _PER.search(s):
            m = _PER.search(s).group(1)
            d = re.search(r"\d", m)
            per = int(d.group()) if d and "ot" not in m.lower() else 5
    return dict(ints=ints, clock=clock, period=per)


class Est:
    def __init__(self):
        self.prev_ints, self.prev_w, self.done, self.samples, self.clock_samples = set(), None, set(), [], []

    def feed(self, w, panel, plays):
        onset = w - (w - self.prev_w) / 2 if self.prev_w else w
        new = []
        scores = {}
        for p in plays:
            if p.get("home") is not None and p.get("away") is not None:
                scores.setdefault((p["home"], p["away"]), []).append(p)
        for (h, a), ps in scores.items():
            if (h, a) in self.done or h == a and h == 0:
                continue
            if h in panel["ints"] and a in panel["ints"] and not (h in self.prev_ints and a in self.prev_ints):
                if len(ps) >= 1 and self.prev_w:
                    first = min(ps, key=lambda p: p["wall"])
                    d = onset - first["wall"]
                    if -30 <= d <= 150:
                        new.append(dict(score=[h, a], D=round(d, 2), play=first.get("text", "")[:60]))
                        self.samples.append(d)
                self.done.add((h, a))
        if panel["clock"] and panel["period"]:
            for p in plays:
                if p.get("clock") == panel["clock"] and p.get("period") == panel["period"]:
                    key = ("clk", p["period"], p["clock"])
                    if key not in self.done:
                        self.done.add(key); self.clock_samples.append(onset - p["wall"])
                    break
        self.prev_ints, self.prev_w = panel["ints"], w
        return new


def live(args, g, out):
    if not g.ok():
        return dict(skipped="stick on screen or unknown", pass_=None)
    g.tune(args.channel); time.sleep(4)
    nav = U.navigate_to(g, r"stats|scores|box ?score|game ?cast")
    out.write("nav", **nav)
    if not nav["found"]:
        g.key("BACK"); g.tune(args.channel)
        return dict(panel=False, pass_=False)
    g.key("DPAD_CENTER"); time.sleep(2)
    U.save_recipe("stats_panel", dict(keys=nav["keys"] + ["DPAD_CENTER"], label=nav["text"]))
    est, plays, last = Est(), [], 0.0
    t_end = time.time() + args.minutes * 60
    while time.time() < t_end:
        if time.time() - last > 15 and args.game:
            plays = U.summary_plays(U.espn_summary(args.game, args.league)); last = time.time()
        p = U.WORK / f"{ID}.png"; w = U.screencap(g.host, p); o = U.ocr(p)
        pan = parse_panel(o)
        for s in est.feed(w, pan, plays):
            out.write("sample", **s)
        out.write("frame", clock=pan["clock"], period=pan["period"], n_ints=len(pan["ints"]))
        U.sleep_poll(args.every)
    g.key("BACK", "BACK"); g.tune(args.channel)
    s = U.summarize(est.samples)
    mode = "realtime" if s.get("n") and s["median"] < 8 else "synced"
    ok = s.get("n", 0) >= 4 and s.get("mad", 9) <= 1.0
    return dict(score=s, clock=U.summarize(est.clock_samples), panel_mode=mode, pass_=bool(ok))


def dry(args, out):
    random.seed(3)
    raw = U.load_plays(args.date, True, game=args.game)
    # synthetic summary: running score from made shots in the recorded text
    h = a = 0
    plays = []
    for i, p in enumerate(raw):
        txt = (p.get("text") or "").lower()
        if "makes" in txt or "made" in txt:
            pts = 3 if ("three" in txt or "3-pt" in txt) else (1 if "free throw" in txt else 2)
            if random.random() < 0.5:
                h += pts
            else:
                a += pts
        plays.append(dict(wall=p["wall"], text=p.get("text"), home=h, away=a, period=None, clock=None))
    D_true, est = 31.0, Est()
    if plays:
        t = plays[0]["wall"] + D_true
        while t < plays[-1]["wall"] + D_true:
            vis = [p for p in plays if p["wall"] + D_true <= t]
            ints = {vis[-1]["home"], vis[-1]["away"], 4, 12} if vis else set()
            est.feed(t, dict(ints=ints, clock=None, period=None), plays)
            t += args.every
    s = U.summarize(est.samples)
    out.write("synthetic", D_true=D_true, est=s)
    p, w = U.fixture("overlay")
    pan = parse_panel(U.ocr(p))
    out.write("fixture_parse", png=str(p), ints=sorted(pan["ints"]), clock=pan["clock"], period=pan["period"])
    out.write("would", steps=["tune", "DPAD_DOWN", "RIGHT.. until /stats|scores/", "CENTER", "poll screencap",
                              "BACK BACK", "re-tune"])
    return dict(synthetic_median_err=round(s["median"] - D_true, 2) if s.get("n") else None, synthetic_n=s.get("n"),
                pass_=None)


if __name__ == "__main__":
    sys.exit(U.run(ID, __doc__, live, dry, extra))
