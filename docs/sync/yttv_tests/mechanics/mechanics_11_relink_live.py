#!/usr/bin/env python3
"""mechanics_11 relink_live: how precisely does a re-deep-link put the hidden stick back at the live edge?

Hypothesis: after any offset (pause, rewind) a deep link to the SAME channel returns to exactly the same
landing L as a fresh tune (|L_relink - L_ref| <= 0.3 s) within <= 4 s, so "relink" is our reset-to-known
button. Open question it answers: does a same-channel deep link while already at live re-buffer (new
`updated`, new L) or no-op?

Procedure (live, HIDDEN stick only) for --reps rounds:
  A ref: tune --alt then --channel -> L_ref.
  B same-channel relink while playing at live (no offset) -> L_B, did `updated` change?
  C pause --offset s, then relink while paused -> L_C, relink_s.
  D pause --offset s, play, then relink -> L_D.
  E rewind 2 presses, then relink -> L_E.
  Each: residual = L_x - L_ref, play_s_upd.
Dry run: recorded same-channel re-landings (E jump on the same channel after a non-PLAYING gap) vs the
(stick, channel) median L from channel-change landings.

Pass rule: for C/D/E, p90 |L_x - L_ref| <= 0.3 s and p90 play_s_upd <= 4 s over >= 3 rounds.
"""
import os
import statistics as st
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mech_common as M  # noqa: E402

ID = "mechanics_11_relink_live"


def judge(out, recs):
    by = {}
    for r in recs:
        by.setdefault(r["case"], []).append(r)
    resid, lat = [], []
    for c, xs in sorted(by.items()):
        rs = [x["residual"] for x in xs if x.get("residual") is not None]
        ls = [x["play_s_upd"] for x in xs if x.get("play_s_upd") is not None]
        out("case", case=c, residual=M.summary(rs), play_s_upd=M.summary(ls),
            reloaded=sum(1 for x in xs if x.get("reloaded")))
        if c in ("C", "D", "E", "recorded"):
            resid += [abs(x) for x in rs]
            lat += ls
    sr, sl = M.summary(resid), M.summary(lat)
    ok = None
    if sr.get("n", 0) >= 3:
        ok = sr["p90"] <= 0.3 and (sl.get("n", 0) == 0 or sl["p90"] <= 4)
    out.verdict(ok, "p90 |L_relink - L_ref| <= 0.3 s and p90 relink time <= 4 s (cases C/D/E)", n=sr.get("n", 0))


def dry(args, out):
    rows = M.load_probe(args.dates)
    segs = M.boot_segments(rows)
    ev = M.tune_events(rows, segs)
    ref = {}
    for e in ev:
        if 46000 < e["L"] < 47600 and e["from_ch"] != e["ch"]:
            ref.setdefault((e["host"], e["ch"]), []).append(e["L"])
    med = {k: st.median(v) for k, v in ref.items()}
    recs = []
    for e in ev:
        k = (e["host"], e["ch"])
        if e["from_ch"] == e["ch"] and 46000 < e["L"] < 47600 and k in med:
            r = dict(case="recorded", host=e["host"], ch=e["ch"], t=e["t"], L=e["L"],
                     residual=round(e["L"] - med[k], 3), play_s_upd=None, from_playing=e["from_playing"])
            recs.append(r)
            out("relanding", **r)
    out("would_measure", plan=f"live: cases A-E x{args.reps} on the hidden stick, offset {args.offset}s")
    judge(out, recs)


def live(args, out):
    hosts, _ = M.preflight(args, out, need_action=True)
    if not hosts:
        raise M.Refused("no hidden stick confirmed")
    host = hosts[0]
    alt = args.alt if args.alt != args.channel else "espn2"
    recs = []
    end = time.time() + args.minutes * 60
    with M.Guard(host, out, args.state_url) as g:
        for rep in range(args.reps):
            if time.time() > end:
                break
            M.tune_measure(g, alt)
            time.sleep(4)
            a = M.tune_measure(g, args.channel)
            out("ref", rep=rep, **a)
            if not a.get("ok"):
                continue
            L_ref = a["L"]

            def relink(case):
                time.sleep(1)
                r = M.tune_measure(g, args.channel)  # ok=False -> no fresh PLAYING update (deep link no-op)
                rec = dict(case=case, rep=rep, L=r.get("L"), play_s_upd=r.get("play_s_upd"),
                           residual=None if r.get("L") is None else round(r["L"] - L_ref, 3),
                           reloaded=bool(r.get("ok")))
                recs.append(rec)
                out("relink", **rec)
                time.sleep(3)

            time.sleep(5)
            relink("B")  # same channel, at live, no offset (ok=False => deep link was a no-op)
            g.dispatch("pause")
            time.sleep(args.offset)
            relink("C")
            g.dispatch("pause")
            time.sleep(args.offset)
            g.dispatch("play")
            time.sleep(2)
            relink("D")
            g.dispatch("rewind")
            M.wait_settle(host)
            g.dispatch("rewind")
            M.wait_settle(host)
            relink("E")
    judge(out, recs)


def extra(ap):
    ap.add_argument("--alt", default="tnt")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--offset", type=float, default=15.0, help="pause seconds before relink")


if __name__ == "__main__":
    M.run(ID, __doc__, dry, live, minutes=15, extra=extra)
