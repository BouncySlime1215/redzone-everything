#!/usr/bin/env python3
"""ads_04 quarter-end / halftime anchors: long breaks give the cleanest D.

Hypothesis: quarter ends and halftime are the longest, most predictable dead balls. The real start
(period-end row) and real restart (the next period's jump ball / first live play) are both in
plays.jsonl with ms-precise nba_cdn walls. On the stick they are the longest break regimes: the
longest media_session silence-then-burst (DAI pods) and the longest low/high bitrate regime. Because
halftime is ~15 min, mis-pairing is impossible: D = stick_edge - real_edge directly.

Procedure (read-only): collect pods + MediaCodecLogger bitrate for --minutes (run it across the end of
Q2 into Q3 for halftime); find period_end/halftime anchors of the game; for each, look for pod
starts in [wall+5, wall+90] (D_start) and pod/bitrate edges in [restart+5, restart+90] (D_end);
bitrate edge = biggest |log-rate| step (3-sample means) in that window.

Pass rule: >=2 period/halftime anchors with an edge, and the D values agree within 2 s (max-min)
and sit inside the prior 29 +- 16 s (ESPN) / 5..90 s (other channels).
"""
import math
import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adslib as L  # noqa: E402

ID = "ads_04_period_halftime_anchor"


def step_edge(series, lo, hi):
    """Time of the biggest log-bitrate step (mean of 3 after - mean of 3 before) inside [lo, hi]."""
    best = None
    pts = [(t, math.log(k + 1)) for t, k in series]
    for i in range(3, len(pts) - 3):
        t = pts[i][0]
        if not lo <= t <= hi:
            continue
        d = st.fmean(v for _, v in pts[i:i + 3]) - st.fmean(v for _, v in pts[i - 3:i])
        if best is None or abs(d) > abs(best[1]):
            best = (t, d)
    return best


def analyse(ev, logs, plays, args, out, ctx):
    anc = [a for a in L.anchors(plays, args.game) if a["kind"] in ("period_end", "halftime")]
    pods = L.find_pods(ev)
    Ds = []
    for h in ctx["hosts"]:
        series = [(t, k) for t, _, k in L.bitrate_series(logs.get(h, []))]
        hp = [p for p in pods if p["host"] == h]
        for a in anc:
            if not L.stick_on_game(ev, h, a["wall"] + 30, a["game"], plays):
                continue  # this stick was not showing that game then
            ps = [p for p in hp if 5 <= p["start"] - a["wall"] <= 90]
            d_pod = round(ps[0]["start"] - a["wall"], 2) if ps else None
            e1 = step_edge(series, a["wall"] + 5, a["wall"] + 90)
            e2 = step_edge(series, a["restart"] + 5, a["restart"] + 90) if a["restart"] else None
            row = out.w("anchor", stick=h[-8:-5], game=a["game"], kind=a["kind"], wall=L.hhmmss(a["wall"]),
                        restart=L.hhmmss(a["restart"]), D_pod_start=d_pod,
                        D_bitrate_start=round(e1[0] - a["wall"], 1) if e1 else None,
                        step_start=round(e1[1], 2) if e1 else None,
                        D_bitrate_end=round(e2[0] - a["restart"], 1) if e2 else None,
                        step_end=round(e2[1], 2) if e2 else None)
            if d_pod is not None:
                Ds.append(d_pod)
            elif e1 and abs(e1[1]) > 0.3:
                Ds.append(row["D_bitrate_start"])
    s = L.robust(Ds)
    passed = s.get("n", 0) >= 2 and (s["max"] - s["min"]) <= 2 and 5 <= s["median"] <= 90
    out.summary(passed, anchors=len(anc), D=s,
                note="bitrate edges are 5 s quantised: expect +-2.5 s per edge" if Ds else "no edges found")


if __name__ == "__main__":
    sys.exit(L.main(ID, __doc__, analyse,
                    "around each quarter end / halftime of the game, find the stick-side pod start and the "
                    "decoder-bitrate step; D = stick edge - real period-end (and restart) wall"))
