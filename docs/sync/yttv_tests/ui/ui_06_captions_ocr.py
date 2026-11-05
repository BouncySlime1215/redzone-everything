#!/usr/bin/env python3
"""ui_06 Closed captions on the UI layer, OCR'd and matched to plays.

HYPOTHESIS: YTTV's Cobalt app draws 608/708 captions in its UI layer (not in the DRM video plane), so
`screencap` shows them even though the picture is black. Live sports captions trail the audio by a
roughly steady stenographer lag (--cc-lag, default 4 s). Matching caption words (surnames, 'timeout',
'foul', 'three', ...) to feed plays with real walls gives
    D_raw = t_first_seen(caption) - play.wall ;  D = D_raw - cc_lag
NOTE: the 02:55 cc_1..cc_6 screencaps (CC toggled during SportsCenter) showed NO text, so either captions
are composited with the video (fail) or CC wasn't on / no speech. This test settles it.

PROCEDURE (hidden stick for the CC toggle; capture itself is read-only):
  1. if --enable-cc: DPAD_DOWN, step focus along the button row to the 'CC' button (recipe 'cc_button'
     from recipes.json if present, else search by OCR focus), DPAD_CENTER once, then BACK. Verify with
     screencaps that text appears in the lower 40% within 30 s. (Pressing CC again at the end restores it
     unless --leave-cc.)
  2. every --every s (>= 1.0) screencap + OCR; caption lines = text in y 0.55..0.97 that is not an
     overlay label; a line counts once, at its first-seen wall (minus half the capture gap).
  3. plays: today's data/recordings/<date>/plays.jsonl for --game (the app records it live), re-read
     every 30 s; match each new line; log matches.

PASS: >= 8 matched lines and MAD(D_raw) <= 2.0 s. (Then D = median(D_raw) - cc_lag, with cc_lag
  calibrated once against a method that does pass.)
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ui_common as U  # noqa: E402
import ui_match as M  # noqa: E402

ID = "ui_06_captions_ocr"
OVERLAY_WORDS = ("Networks", "Multiview", "Episodes", "More to watch", "LIVE", " AM", " PM", "HD")


def extra(ap):
    ap.add_argument("--every", type=float, default=1.5)
    ap.add_argument("--cc-lag", type=float, default=4.0)
    ap.add_argument("--enable-cc", action="store_true")
    ap.add_argument("--leave-cc", action="store_true")


def caption_lines(o: dict) -> list[str]:
    """Caption-region text; frames with the player overlay up are skipped (titles look like captions)."""
    if U.parse_overlay(o, time.time())["overlay"]:
        return []
    out = []
    for t in o["obs"]:
        s = t["text"].strip()
        if 0.55 <= t["y"] <= 0.97 and len(s) >= 6 and not any(w in s for w in OVERLAY_WORDS):
            out.append(s)
    return out


def toggle_cc(g):
    rec = U.load_recipes().get("cc_button")
    if rec and rec.get("keys"):
        g.key(*rec["keys"])
        return dict(found=True, keys=rec["keys"], via="recipe")
    nav = U.navigate_to(g, r"^\s*c\s*c\s*$", row_key="DPAD_LEFT", max_steps=8)
    if not nav["found"]:
        nav = U.navigate_to(g, r"^\s*c\s*c\s*$", row_key="DPAD_RIGHT", max_steps=8, reveal=())
    if nav["found"]:
        g.key("DPAD_CENTER", "BACK")
        U.save_recipe("cc_button_seen", dict(keys=nav["keys"] + ["DPAD_CENTER"]))
    return nav


def estimate(matches, cc_lag):
    s = U.summarize([m["D_raw"] for m in matches])
    if s.get("n"):
        s["D"] = round(s["median"] - cc_lag, 2)
    return s


def live(args, g, out):
    if args.enable_cc:
        if g.ok():
            out.write("cc_toggle", **toggle_cc(g))
        else:
            out.write("cc_toggle", skipped="stick on screen/unknown: not pressing")
    plays, last_load = [], 0.0
    seen, matches, prev_w = set(), [], None
    t_end = time.time() + args.minutes * 60
    while time.time() < t_end:
        if time.time() - last_load > 30:
            plays = U.load_plays(args.date, False, game=args.game); last_load = time.time()
        p = U.WORK / f"{ID}.png"; w = U.screencap(g.host, p); o = U.ocr(p)
        t_first = w - (w - prev_w) / 2 if prev_w else w
        prev_w = w
        for line in caption_lines(o):
            k = line.upper()
            if k in seen:
                continue
            seen.add(k)
            pl, sc = M.match_text(line, t_first, plays)
            row = dict(line=line, t_seen=round(t_first, 2), score=sc)
            if pl:
                row.update(play=pl["play"], play_text=pl.get("text"), D_raw=round(t_first - pl["wall"], 2))
                matches.append(row)
            out.write("caption", **row)
        U.sleep_poll(args.every)
    if args.enable_cc and not args.leave_cc and g.ok():
        out.write("cc_restore", **toggle_cc(g))
    s = estimate(matches, args.cc_lag)
    ok = s.get("n", 0) >= 8 and s.get("mad", 9) <= 2.0
    return dict(lines=len(seen), est=s, pass_=bool(ok))


def dry(args, out):
    fx = []
    for name in ("cc", "overlay", "blank"):
        p, w = U.fixture(name)
        lines = caption_lines(U.ocr(p))
        out.write("fixture", png=str(p), caption_lines=lines)
        fx.append(len(lines))
    plays = U.load_plays(args.date, True, game=args.game)
    D_true = 29.0
    obs = M.synth_captions(plays, D_true, args.cc_lag, noise=1.0)
    matches, wrong = [], 0
    for ob in obs:
        pl, sc = M.match_text(ob["text"], ob["t"], plays)
        if pl:
            matches.append(dict(D_raw=ob["t"] - pl["wall"]))
            wrong += int(ob["truth"] is not None and pl["play"] != ob["truth"]) + int(ob["truth"] is None)
    s = estimate(matches, args.cc_lag)
    out.write("synthetic", plays=len(plays), captions=len(obs), D_true=D_true, est=s, wrong_matches=wrong)
    return dict(fixture_caption_lines=fx, synthetic_D_err=round(s.get("D", 0) - D_true, 2) if s.get("n") else None,
                synthetic_n=s.get("n"), pass_=None)


if __name__ == "__main__":
    sys.exit(U.run(ID, __doc__, live, dry, extra))
