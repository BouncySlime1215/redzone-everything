#!/usr/bin/env python3
"""ui_03 Knob pixel position on the progress bar -> playhead wall clock, no bar focus needed.

HYPOTHESIS: the knob's x on the bar is (playhead - prog_start)/(prog_end - prog_start) of the bar
(measured bar: y~880, x 96..1823 at 1920 px => ~1.04 s/px for a 30-min program, ~2.1 s/px for 60 min).
So any frame with the overlay up yields P_knob = prog_start + frac*dur and D_knob = cap_wall - P_knob,
plus C_knob = P_knob - media_position. Works on frames anyone brings up, so it can also run READ-ONLY
on the visible stick (it just waits for the overlay to appear).

PROCEDURE:
  hidden stick: every --every s: DPAD_DOWN, screencap, media_session read; BACK if overlay still up
    after the read (it auto-hides after ~5 s anyway). Re-tune once in the middle.
  read-only (visible / unknown): screencap every 2 s; use only frames that show the overlay.
  Each frame: knob x (white run on the bar row), program window via OCR, D_knob, C_knob.
  Velocity check: knob px/s between consecutive frames vs bar_px/dur (should match while playing).

PASS: >= 6 frames; sd(C_knob) <= 1.5 s; median |velocity / expected - 1| <= 0.15 over pairs >= 20 s apart.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ui_common as U  # noqa: E402

ID = "ui_03_knob_pixel"


def extra(ap):
    ap.add_argument("--every", type=float, default=15.0)


def frame(png, wall, o=None, pos=None):
    o = o or U.ocr(png)
    ov = U.parse_overlay(o, wall)
    bar = U.find_bar(U.Pixels(png))
    r = dict(overlay=ov["overlay"], knob_x=bar and bar["knob_x"], frac=bar and bar["frac"],
             x0=bar and bar["x0"], x1=bar and bar["x1"], prog_start=ov["prog_start"], prog_end=ov["prog_end"])
    if bar and bar.get("frac") is not None and ov["prog_start"] and ov["prog_end"]:
        P = ov["prog_start"] + bar["frac"] * (ov["prog_end"] - ov["prog_start"])
        r.update(P=round(P, 2), D_knob=round(wall - P, 2),
                 px_per_s=(bar["x1"] - bar["x0"]) / (ov["prog_end"] - ov["prog_start"]))
        if pos is not None:
            r["C_knob"] = round(P - pos, 2)
    return r


def velocity(rows):
    ratios = []
    for a, b in zip(rows, rows[1:]):
        if a.get("knob_x") and b.get("knob_x") and a.get("px_per_s") and b["wall"] - a["wall"] >= 20 \
                and a.get("prog_start") == b.get("prog_start"):
            v = (b["knob_x"] - a["knob_x"]) / (b["wall"] - a["wall"])
            ratios.append(v / a["px_per_s"])
    return ratios


def live(args, g, out):
    rows = []
    press = g.ok()
    t_end = time.time() + args.minutes * 60
    mid_done = False
    while time.time() < t_end:
        if press and not mid_done and time.time() > t_end - args.minutes * 30:
            g.tune(args.channel); time.sleep(5); mid_done = True
        if press:
            g.key("DPAD_DOWN")
        p = U.WORK / f"{ID}_{int(time.time())}.png"
        m0 = U.ms_raw(g.host); w = U.screencap(g.host, p); m1 = U.ms_raw(g.host)
        pos = (m0["pos"] + m1["pos"]) / 2 if m0.get("pos") and m1.get("pos") else None
        r = frame(p, w, pos=pos)
        if r["overlay"]:
            r["wall"] = w
            out.write("frame", E=m1.get("E"), **r); rows.append(r)
            if press:
                g.key("BACK")
        U.sleep_poll(args.every if press else 2.0)
    if press:
        g.tune(args.channel)
    sC = U.summarize([r.get("C_knob") for r in rows])
    rat = velocity(rows)
    vr = U.summarize([abs(x - 1) for x in rat])
    ok = len(rows) >= 6 and sC.get("sd", 9) <= 1.5 and vr.get("median", 9) <= 0.15
    return dict(frames=len(rows), C_knob=sC, D_knob=U.summarize([r.get("D_knob") for r in rows]),
                velocity_err=vr, pass_=bool(ok))


def dry(args, out):
    pO, wO = U.fixture("overlay")
    win = U.parse_overlay(U.ocr(pO), wO)
    rows = []
    for name in ("scrub", "overlay", "scrub2"):
        p, w = U.fixture(name)
        r = frame(p, w)
        if r.get("prog_start") is None and win["prog_start"] and r.get("frac") is not None:  # borrow window
            P = win["prog_start"] + r["frac"] * (win["prog_end"] - win["prog_start"])
            r.update(prog_start=win["prog_start"], prog_end=win["prog_end"], P=round(P, 2), D_knob=round(w - P, 2),
                     px_per_s=(r["x1"] - r["x0"]) / (win["prog_end"] - win["prog_start"]))
        r["wall"] = w
        out.write("fixture", png=str(p), **r); rows.append(r)
    rows.sort(key=lambda r: r["wall"])
    return dict(fixture_D_knob=[r.get("D_knob") for r in rows], velocity_ratio=[round(x, 2) for x in velocity(rows)],
                note="fixtures span pause/seek tests: ratio != 1 expected here", pass_=None)


if __name__ == "__main__":
    sys.exit(U.run(ID, __doc__, live, dry, extra))
