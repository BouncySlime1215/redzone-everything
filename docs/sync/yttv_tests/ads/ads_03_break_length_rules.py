#!/usr/bin/env python3
"""ads_03 break length vs NBA rules: is the stick-side break exactly as long as the real one?

Hypothesis: a national-TV NBA full timeout lasts ~2:45 of commercial (real: timeout row -> first live
play ~3:30-4:30 incl. inbound), quarter breaks ~2:10-2:45, halftime 15:00. If YTTV plays the network's
pods 1:1 (no DAI stretching), the stick-side break length equals the real one, and the break can be
used as an anchor at BOTH ends. If DAI swaps a pod for different-length ads, YTTV must pad/trim (slate
or extra spot) and the break end on the stick drifts away from restart + D: then only the break START
is a safe anchor.

Procedure (read-only): from the stick collect pods (media_session cadence) and, as a second view,
decoder-bitrate regimes (MediaCodecLogger, 5 s). For each matched break (ads_02 matching) compare
stick_len = pod_end - pod_start with real_len = restart_wall - timeout_wall and with the nominal NBA
length for its kind. Also print the real-world break-length distribution per kind from plays.jsonl
(always available, even with no stick pods), the reference this test needs.

Pass rule: >=3 matched breaks and median |stick_len - real_len| <= 5 s with MAD <= 2 s
(=> both ends usable). Otherwise report which end is stable.
"""
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adslib as L  # noqa: E402

ID = "ads_03_break_length_rules"
NOMINAL = {"timeout": 165.0, "period_end": 130.0, "halftime": 900.0}


def analyse(ev, logs, plays, args, out, ctx):
    anc = L.anchors(plays, args.game)
    real = defaultdict(list)
    for a in anc:
        if a["len"]:
            real[a["kind"]].append(a["len"])
    for k, v in real.items():
        out.w("real_break_len", kind=k, nominal=NOMINAL.get(k), **L.robust(v))
    diffs = []
    for p in L.find_pods(ev):
        m = L.match_pod_anchor(p, anc, plays, args.game)
        if not m or m[0]["len"] is None or m[1] is None:
            continue  # unmatched or partial pod (no start edge)
        a = m[0]
        stick_len = p["end_est"] - p["start"]
        d = stick_len - a["len"]
        diffs.append(d)
        out.w("break", stick=p["h"], channel=p["sub"], kind=a["kind"], real_len=round(a["len"], 1),
              stick_len=round(stick_len, 1), diff=round(d, 1), nominal=NOMINAL.get(a["kind"]))
    s = L.robust([abs(x) for x in diffs])
    passed = s.get("n", 0) >= 3 and s["median"] <= 5 and s["mad"] <= 2
    out.summary(passed, matched=len(diffs), abs_diff=s,
                real_len_median={k: L.robust(v).get("median") for k, v in real.items()},
                note=None if diffs else "no matched pods; real-world break lengths reported for live use")


if __name__ == "__main__":
    sys.exit(L.main(ID, __doc__, analyse,
                    "measure each stick-side ad break length (media_session pod) and compare with the real "
                    "timeout->restart length from plays.jsonl and NBA nominal lengths"))
