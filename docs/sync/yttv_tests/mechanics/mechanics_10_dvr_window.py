#!/usr/bin/env python3
"""mechanics_10 dvr_window: how far back can the hidden stick rewind, and is L the DVR window length?

Hypothesis: YTTV live position is measured from the start of a sliding DVR window, and L ~ 46,790-46,800 s
(= 13.0 h) because the window is 13 h minus a 0-10 s live-edge hold-back. Then (a) repeated rewind
presses stop moving at position ~0 (window start), not earlier; (b) the reachable window length
(pos at live - min reachable pos) ~= L; (c) L - 46,800 is the per-stick/channel live-edge hold-back in s.
Rewinding the full window by keys may be impractical; the test therefore also fits the per-press step and
extrapolates, and records the overlay scrub label (OCR) at the deepest point.

Procedure (live, HIDDEN stick only): tune --channel, settle, record L and pos_now; press media_session
dispatch rewind up to --max-presses times (wait_settle after each; stop when the position stops decreasing
for 2 presses or --minutes is up); record min pos and per-press steps; optional OCR capture at the deepest
point (--ocr); re-tune to live at the end.
Dry run: per (stick, channel), recorded landing L vs 46,800 (hold-back estimate), and the deepest position
ever seen relative to the same session's landing (from E jumps), as a lower bound on the window.

Pass rule: hypothesis confirmed when the hold-back L - 46,800 is stable per (stick, channel) (sd <= 0.5 s) and,
live, either position bottoms out within 30 s of 0 or the extrapolated window is within 5% of L.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mech_common as M  # noqa: E402

ID = "mechanics_10_dvr_window"


def dry(args, out):
    rows = M.load_probe(args.dates)
    segs = M.boot_segments(rows)
    groups = {}
    for e in M.tune_events(rows, segs):
        if 46000 < e["L"] < 47600 and (e["from_ch"] != e["ch"] or not e["from_playing"]):
            groups.setdefault((e["host"], e["ch"]), []).append(e["L"] - 46800)
    stable = []
    for (h, ch), xs in sorted(groups.items()):
        s = M.summary(xs)
        out("holdback", host=h, ch=ch, L_minus_46800=s)
        if s["n"] >= 3:
            core = sorted(xs)[len(xs) // 4: len(xs) - len(xs) // 4] or xs
            stable.append(M.summary(core)["sd"] <= 0.5)
    deepest = {}
    for r in M.yt_rows(rows):
        p = r["ms"]["position"] / 1000
        k = (r["host"], M.chan_key(r["ms"].get("subtitle")))
        deepest[k] = min(deepest.get(k, 1e12), p)
    for k, v in sorted(deepest.items()):
        out("min_position_seen", host=k[0], ch=k[1], min_pos_s=round(v, 1),
            note="positions < ~46,790 at landing would mean a shorter window or VOD/DVR content")
    out("would_measure", plan=f"live: up to {args.max_presses} rewind presses on hidden stick, min position, "
                              "step sizes, extrapolated window vs L")
    out.verdict(all(stable) if stable else None, "hold-back L-46800 stable (IQR-core sd <= 0.5 s) per (stick,channel)",
                groups=len(stable))


def live(args, out):
    hosts, _ = M.preflight(args, out, need_action=True)
    if not hosts:
        raise M.Refused("no hidden stick confirmed")
    host = hosts[0]
    end = time.time() + args.minutes * 60
    with M.Guard(host, out, args.state_url) as g:
        land = M.tune_measure(g, args.channel)
        out("landing", **land)
        r0, _ = M.wait_settle(host, hold_s=2.0)
        if r0 is None:
            raise M.Refused("no media_session after tune")
        start_pos, prev, steps, flat = r0["pos_now"], r0, [], 0
        minpos = start_pos
        for i in range(args.max_presses):
            if time.time() > end:
                break
            g.dispatch("rewind")
            r1, settle = M.wait_settle(host, hold_s=1.5, timeout=12)
            if r1 is None:
                break
            step = (r1["pos_now"] - prev["pos_now"]) - (r1["t"] - prev["t"])
            steps.append(step)
            minpos = min(minpos, r1["pos_now"])
            out("press", i=i, step=round(step, 3), pos_now=round(r1["pos_now"], 3), settle_s=settle, st=r1["st"])
            flat = flat + 1 if abs(step) < 0.5 else 0
            if flat >= 2:
                break
            prev = r1
        if args.ocr:
            import mechanics_09_ui_probe as U
            U.capture(host, "dvr_deepest", out)
        reach = start_pos - minpos
        s = M.summary([x for x in steps if abs(x) >= 0.5])
        out("relink", **M.tune_measure(g, args.channel))
    L = land.get("L")
    extrap = None
    if s.get("n") and s["mean"] < 0:
        extrap = L / abs(s["mean"]) if L else None  # presses needed to traverse a window of length L
    bottomed = flat >= 2 and minpos < 30
    out("summary", L=L, holdback=L and round(L - 46800, 3), reach_s=round(reach, 2), min_pos=round(minpos, 2),
        step=s, presses_to_cross_L=extrap and round(extrap), bottomed_out=bottomed)
    ok = None
    if bottomed:
        ok = abs(reach - (start_pos - 0)) < 30
    out.verdict(ok, "rewind bottoms out within 30 s of position 0 (window = L); None if the window was not "
                    "reached (see presses_to_cross_L)")


def extra(ap):
    ap.add_argument("--max-presses", type=int, default=40)
    ap.add_argument("--ocr", action="store_true", help="OCR the overlay at the deepest point")


if __name__ == "__main__":
    M.run(ID, __doc__, dry, live, minutes=10, extra=extra)
