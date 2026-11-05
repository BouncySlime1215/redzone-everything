#!/usr/bin/env python3
"""ads_02 pod vs timeout: D from DAI pod edges vs NBA timeout / restart walls.

Hypothesis: an in-game DAI pod starts a fixed lead-in after the real timeout is called and ends a
fixed lead-out before the real restart. So per break:
    D_start = pod_start_on_stick - timeout_wall      (plays.jsonl 'wall' of the Timeout row)
    D_end   = pod_end_on_stick   - restart_wall      (first live play after the break)
Both are D plus a small bias; their mean over breaks of one tune is D. With >=3 breaks the spread
tells us whether this anchor is good to 1-2 s.

Procedure (read-only): collect media_session updates on the stick for --minutes (adslib.collect_live),
detect pods, read today's data/recordings/<day>/plays.jsonl for the game (--game, else auto from
YTTV title), pair each pod with the break that puts D in 5..90 s (closest to the 29 s prior and
with D_start ~ D_end); a pod caught mid-break (after a tune) is matched on its end only ('partial').

Pass rule: >=3 matched breaks in one channel/stick and sd(D_start) <= 2 s (or sd(D_end) <= 2 s).
Reports D = median of the better edge, and lead pad = D_start - D_end.
"""
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adslib as L  # noqa: E402

ID = "ads_02_pod_vs_timeout"


def analyse(ev, logs, plays, args, out, ctx):
    pods = L.find_pods(ev)
    anc = L.anchors(plays, args.game)
    groups = defaultdict(list)
    for p in pods:
        m = L.match_pod_anchor(p, anc, plays, args.game)
        if not m:
            continue
        a, ds, de, g = m
        row = out.w("match", stick=p["h"], channel=p["sub"], game=g, kind=a["kind"], pod_start=L.hhmmss(p["start"]),
                    anchor=L.hhmmss(a["wall"]), restart=L.hhmmss(a["restart"]), spots=p["spots"],
                    D_start=ds, D_end=de, partial=ds is None,
                    pad=round(ds - de, 2) if de is not None and ds is not None else None)
        groups[(p["h"], p["sub"])].append(row)
    best = None
    for k, rows in groups.items():
        s1 = L.robust([r["D_start"] for r in rows])
        s2 = L.robust([r["D_end"] for r in rows])
        out.w("group", stick=k[0], channel=k[1], D_start=s1, D_end=s2, pad=L.robust([r["pad"] for r in rows]))
        for s in (s1, s2):
            if s.get("n", 0) >= 3 and s.get("sd") is not None and (best is None or s["sd"] < best[1]["sd"]):
                best = (k, s)
    passed = bool(best and best[1]["sd"] <= 2.0)
    out.summary(passed, matched=sum(len(v) for v in groups.values()), pods=len(pods), anchors=len(anc),
                best=dict(stick=best[0][0], channel=best[0][1], **best[1]) if best else None,
                note=None if best else "fewer than 3 pod<->timeout matches (in-game DAI rare in recordings)")


if __name__ == "__main__":
    sys.exit(L.main(ID, __doc__, analyse,
                    "detect DAI pods on the hidden stick and pair each with an NBA timeout/restart from today's "
                    "plays.jsonl: D_start = pod_start - timeout_wall, D_end = pod_end - restart_wall"))
