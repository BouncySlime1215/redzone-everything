#!/usr/bin/env python3
"""ui_04 Progress-bar colour segments: does the bar draw the live edge separately from the playhead?

HYPOTHESIS: when the playhead is behind live, YTTV paints the stretch knob -> live edge in a different
shade (aired-but-unwatched) than the stretch after the live edge (not yet aired). Then
    behind_live_s = (live_edge_x - knob_x) * s_per_px
is readable from one frame, and the live edge's x gives the edge's wall clock in YTTV's timeline:
    D_edge = cap_wall - (prog_start + edge_frac*dur)    (YTTV ingest -> edge delay, should be ~const)
Fixture (stick ~40 s behind): runs = red 96-1356, pink 1357-1539, knob 1540-1579, gray(76) 1584-1823 ->
no separate shade visible at 1 px; this test checks at larger 'behind' values where it must show
(20 s = ~19 px, 60 s = ~58 px on a 30-min program).

PROCEDURE (hidden stick only), for behind in --behinds (default 0, 20, 60 s):
  1. deep-link tune (live edge), wait 4 s; record E0
  2. MEDIA_PAUSE for `behind` s, MEDIA_PLAY, wait 3 s; record E1 (true behind = E1 - E0)
  3. DPAD_DOWN, screencap; classify every pixel of the bar row (red/pink/white/gray_dark/gray_mid/
     gray_light/other); write the run list; edge_x = first boundary right of the knob where the
     shade changes (if any)
  4. BACK; final re-tune.

PASS: for every behind >= 20 s a distinct segment exists right of the knob and
  |segment_len * s_per_px - (E1 - E0)| <= 2 s; at behind = 0 no such segment (or <= 2 px).
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ui_common as U  # noqa: E402

ID = "ui_04_bar_live_edge"


def extra(ap):
    ap.add_argument("--behinds", default="0,20,60")


def segments(png, wall, o=None):
    o = o or U.ocr(png)
    ov = U.parse_overlay(o, wall)
    bar = U.find_bar(U.Pixels(png))
    if not bar:
        return dict(bar=False)
    r = dict(bar=True, runs=bar["runs"], knob_x=bar["knob_x"], x0=bar["x0"], x1=bar["x1"])
    right = [x for x in bar["runs"] if x[1] > bar["knob_x"] + 20 and x[0] != "white"]
    if len(right) >= 2:  # knob .. [shade A] .. [shade B] .. end
        r.update(seg_after_knob=right[0][0], seg_len_px=right[0][2] - right[0][1] + 1, edge_x=right[0][2] + 1)
    else:
        r.update(seg_after_knob=None, seg_len_px=0, edge_x=None)
    if ov["prog_start"] and ov["prog_end"]:
        spp = (ov["prog_end"] - ov["prog_start"]) / max(1, bar["x1"] - bar["x0"])
        r["s_per_px"] = round(spp, 3)
        r["behind_from_bar_s"] = round(r["seg_len_px"] * spp, 2)
        if r["edge_x"]:
            r["D_edge"] = round(wall - (ov["prog_start"] + (r["edge_x"] - bar["x0"]) * spp), 2)
    return r


def live(args, g, out):
    if not g.ok():
        return dict(skipped="stick on screen or unknown", pass_=None)
    res = []
    for b in [float(x) for x in args.behinds.split(",")]:
        g.tune(args.channel); time.sleep(4)
        e0 = U.ms_raw(g.host).get("E")
        if b > 0:
            g.key("MEDIA_PAUSE"); time.sleep(b); g.key("MEDIA_PLAY"); time.sleep(3)
        e1 = U.ms_raw(g.host).get("E")
        g.key("DPAD_DOWN"); p, w, o = U.snap(g, f"{ID}_{int(b)}")
        s = segments(p, w, o)
        true_b = (e1 - e0) if e0 and e1 else None
        err = (s.get("behind_from_bar_s") - true_b) if (true_b is not None and s.get("behind_from_bar_s") is not None) else None
        out.write("frame", behind_set=b, behind_true=true_b, err_s=err, **s)
        res.append((b, s.get("seg_len_px", 0), err))
        g.key("BACK")
    g.tune(args.channel)
    ok = all((bb < 20 and px <= 2) or (bb >= 20 and px > 2 and err is not None and abs(err) <= 2) for bb, px, err in res)
    return dict(results=res, pass_=bool(ok and res))


def dry(args, out):
    p, w = U.fixture("overlay")
    s = segments(p, w)
    out.write("fixture", png=str(p), **s)
    out.write("would", behinds=args.behinds, steps=["tune", "E0", "PAUSE b s", "PLAY", "E1", "DPAD_DOWN", "screencap",
                                                     "classify bar row", "BACK"])
    return dict(fixture_seg_after_knob=s.get("seg_after_knob"), fixture_seg_len_px=s.get("seg_len_px"),
                fixture_D_edge=s.get("D_edge"), pass_=None)


if __name__ == "__main__":
    sys.exit(U.run(ID, __doc__, live, dry, extra))
