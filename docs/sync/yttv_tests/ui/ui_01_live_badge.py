#!/usr/bin/env python3
"""ui_01 LIVE badge colour as an at-live-edge detector.

HYPOTHESIS: the 'LIVE' badge in the YTTV player overlay (left of 'Nov 5, 2:30 AM - <program>') is red
when the playhead is at the live edge and gray when behind (YouTube's convention). The 2026-11-05
02:56 fixture shows a gray badge (57,57,57) while the stick was ~40 s behind after pause tests.
If true, one screenshot answers "is this stick at the live edge?" (D = edge delay) with no media math.

PROCEDURE (hidden stick only; per rep):
  1. tune to --channel (deep link, lands at live edge); wait 4 s; DPAD_DOWN reveals the overlay
  2. screencap + OCR + pixel-sample the badge -> colour A; media_session E_live
  3. MEDIA_PAUSE for --pause s, MEDIA_PLAY, wait 3 s, DPAD_DOWN, screencap -> colour B; E_behind
  4. repeat, then re-tune so the stick ends at the live edge
  Read-only fallback (stick on screen or unknown): screencap every 2 s and log any overlay that
  someone else brings up (badge colour + E), no keys.

PASS: in every rep colour A != colour B, A is the same in all reps and B is the same in all reps,
  with behind = E_behind - E_live >= 10 s. (Detector only; it does not give D in seconds.)
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ui_common as U  # noqa: E402

ID = "ui_01_live_badge"


def extra(ap):
    ap.add_argument("--pause", type=float, default=20.0)
    ap.add_argument("--reps", type=int, default=3)


def badge(png, wall, o=None):
    o = o or U.ocr(png)
    ov = U.parse_overlay(o, wall)
    if not ov["live_badge"]:
        return dict(overlay=ov["overlay"], badge=None)
    c = U.badge_color(U.Pixels(png), ov["live_badge"])
    return dict(overlay=True, badge=c["dominant"], rgb=c["avg_rgb"], headline=ov["headline"])


def live(args, g, out):
    pairs = []
    if not g.ok():
        t_end = time.time() + args.minutes * 60
        while time.time() < t_end:
            p = U.WORK / f"{ID}_ro.png"; w = U.screencap(g.host, p); m = U.ms_raw(g.host)
            b = badge(p, w)
            if b["overlay"]:
                out.write("readonly_overlay", E=m.get("E"), **b)
            U.sleep_poll(2.0)
        return dict(mode="read-only", pass_=None)
    for rep in range(args.reps):
        g.tune(args.channel); time.sleep(4)
        e0 = U.ms_raw(g.host).get("E")
        g.key("DPAD_DOWN"); pA, w, o = U.snap(g, f"{ID}_A"); a = badge(pA, w, o)
        g.key("MEDIA_PAUSE"); time.sleep(args.pause); g.key("MEDIA_PLAY"); time.sleep(3)
        e1 = U.ms_raw(g.host).get("E")
        g.key("DPAD_DOWN"); pB, w2, o2 = U.snap(g, f"{ID}_B"); b = badge(pB, w2, o2)
        behind = (e1 - e0) if (e0 and e1) else None
        out.write("rep", rep=rep, A=a, B=b, E_live=e0, E_behind=e1, behind_s=behind)
        pairs.append((a.get("badge"), b.get("badge"), behind))
    g.tune(args.channel)
    ok = (pairs and all(x and y and x != y and (bh or 0) >= 10 for x, y, bh in pairs)
          and len({p[0] for p in pairs}) == 1 and len({p[1] for p in pairs}) == 1)
    return dict(pairs=pairs, pass_=bool(ok), rule="A!=B every rep, A/B consistent, behind>=10 s")


def dry(args, out):
    p, w = U.fixture("overlay")
    b = badge(p, w)
    out.write("fixture", png=str(p), cap_wall=w, **b)
    out.write("would", steps=["tune %s" % args.channel, "DPAD_DOWN", "screencap A", "MEDIA_PAUSE %ss" % args.pause,
                              "MEDIA_PLAY", "DPAD_DOWN", "screencap B", "re-tune"], reps=args.reps)
    return dict(fixture_badge=b.get("badge"), note="fixture stick was ~40 s behind -> expect gray when behind",
                pass_=None)


if __name__ == "__main__":
    sys.exit(U.run(ID, __doc__, live, dry, extra))
