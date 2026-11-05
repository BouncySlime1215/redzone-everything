#!/usr/bin/env python3
"""ui_02 Scrub-bar time label vs program start -> YTTV's own wall-clock for the playhead.

HYPOTHESIS: with the progress bar focused (no seek), YTTV prints 'MM:SS' above the knob = seconds since
the EPG program start (fixture: '23:36' at 0.7874 of a 2:30-3:00 AM window = 1417 s; label 1416 s).
So the playhead's wall-clock in YTTV's timeline is P = prog_start + label, and
    D_ui = capture_wall - P            (seconds the picture is behind YTTV's clock)
    C    = P - media_position          (maps media_session position to wall clock)
If C is constant for a channel+stick across re-tunes, then D_ui(t) = E(t) - C needs NO screenshots:
media_session alone gives YTTV's delay at 1 s resolution. (D_ui excludes any delay upstream of
YTTV's ingest clock; compare with the plays-anchored D from the other families.)

PROCEDURE (hidden stick only; every --every s for --minutes):
  1. (rep 0 and every --retune-every reps) deep-link tune to --channel, wait 5 s
  2. DPAD_DOWN (overlay up) -> screencap A: program window '2:30 AM - 3:00 AM' + knob pixel
  3. DPAD_UP (focus the bar; recipe 'scrub_focus' from recipes.json overrides) -> screencap B: label
     media_session position read immediately before and after B (midpoint used)
  4. DPAD_DOWN back to the button row, then BACK once (hides overlay; only pressed if the overlay is up)
  Never presses LEFT/RIGHT/CENTER on the bar (that would seek).

PASS: >= 5 label reads; sd(C) <= 1.0 s over all reads INCLUDING >= 1 re-tune; and
  |D_ui(label) - D_ui(knob)| <= 3 s on every read (fixtures: +1.35, +2.17 s; knob reads ~1.5 s early).
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ui_common as U  # noqa: E402

ID = "ui_02_scrub_label"


def extra(ap):
    ap.add_argument("--every", type=float, default=20.0)
    ap.add_argument("--retune-every", type=int, default=3)


def compute(win: dict, lab: dict, bar: dict | None, cap_wall: float, pos: float | None):
    r = dict(label=lab.get("scrub_label"), label_s=lab.get("scrub_s"))
    if not win.get("prog_start"):
        return dict(r, err="no program window")
    ps, pe = win["prog_start"], win["prog_end"]
    if lab.get("scrub_s") is not None:
        P = ps + lab["scrub_s"]
        r.update(P=P, D_label=round(cap_wall - P, 2))
        if pos is not None:
            r["C"] = round(P - pos, 3)
    if bar and bar.get("frac") is not None and pe:
        Pk = ps + bar["frac"] * (pe - ps)
        r.update(D_knob=round(cap_wall - Pk, 2), s_per_px=round((pe - ps) / max(1, bar["x1"] - bar["x0"]), 3))
    if "D_label" in r and "D_knob" in r:
        r["label_minus_knob"] = round(r["D_label"] - r["D_knob"], 2)
    return r


def live(args, g, out):
    if not g.ok():
        return dict(skipped="stick on screen or unknown: this test needs keys", pass_=None)
    rec = U.load_recipes().get("scrub_focus", {}).get("keys") or ["DPAD_UP"]
    Cs, diffs, tunes, n = [], [], 0, 0
    t_end = time.time() + args.minutes * 60
    while time.time() < t_end:
        if n % max(1, args.retune_every) == 0:
            g.tune(args.channel); tunes += 1; time.sleep(5)
        g.key("DPAD_DOWN"); pA, wA, oA = U.snap(g, f"{ID}_A")
        win = U.parse_overlay(oA, wA); bar = U.find_bar(U.Pixels(pA))
        if not g.key(*rec):
            break
        m0 = U.ms_raw(g.host); pB, wB, oB = U.snap(g, f"{ID}_B"); m1 = U.ms_raw(g.host)
        lab = U.parse_overlay(oB, wB)
        pos = (m0["pos"] + m1["pos"]) / 2 if m0.get("pos") and m1.get("pos") else None
        # knob from frame B too (the knob does not move when the bar takes focus)
        barB = U.find_bar(U.Pixels(pB)) or bar
        r = compute(win, lab, barB, wB, pos)
        out.write("read", n=n, tune_no=tunes, E=m1.get("E"), pos=pos, title=m1.get("title"), **r)
        if r.get("C") is not None:
            Cs.append(r["C"])
        if r.get("label_minus_knob") is not None:
            diffs.append(r["label_minus_knob"])
        g.key("DPAD_DOWN")
        _, _, oC = U.snap(g, f"{ID}_C")
        if U.parse_overlay(oC, time.time())["overlay"]:
            g.key("BACK")
        n += 1
        U.sleep_poll(args.every)
    g.tune(args.channel)
    sC = U.summarize(Cs)
    ok = sC.get("n", 0) >= 5 and sC.get("sd", 9) <= 1.0 and tunes >= 2 and all(abs(d) <= 3 for d in diffs)
    return dict(C=sC, label_minus_knob=U.summarize(diffs), tunes=tunes, pass_=bool(ok))


def dry(args, out):
    pO, wO = U.fixture("overlay")
    win = U.parse_overlay(U.ocr(pO), wO)
    res = []
    for name in ("scrub", "scrub2"):
        p, w = U.fixture(name)
        lab = U.parse_overlay(U.ocr(p), w)
        bar = U.find_bar(U.Pixels(p))
        r = compute(win, lab, bar, w, None)
        out.write("fixture", png=str(p), cap_wall=w, **r)
        res.append(r)
    out.write("would", steps=["tune", "DPAD_DOWN", "screencap A", "DPAD_UP", "ms read", "screencap B", "ms read",
                              "DPAD_DOWN", "BACK if overlay"], every_s=args.every)
    return dict(fixture_label_minus_knob=[r.get("label_minus_knob") for r in res],
                fixture_D_label=[r.get("D_label") for r in res],
                note="fixtures were taken mid pause/seek experiments, so D differs; parser check only",
                pass_=None)


if __name__ == "__main__":
    sys.exit(U.run(ID, __doc__, live, dry, extra))
