#!/usr/bin/env python3
"""ads_11 E invariance across pods: does DAI stretch or shift the timeline?

Hypothesis: E = wall - position is constant while YTTV plays (measured). If DAI replaces a pod with
ads of a different total length, YTTV must either (a) keep the timeline (E stays constant: ads are
cut/padded to fit) or (b) shift it (E jumps by the length difference, so D changes after every
break, and every D estimate goes stale at the next pod). This test settles it: it decides whether a
D measured before a break is still valid after it.

Procedure (read-only): media_session polling for --minutes; for each pod compute E at every spot
boundary (wall(updated) - position) and E at the first update after the pod; compare with the
segment median E before the pod.

Pass rule: >=95% of pod boundaries have |E - E_before| <= 0.3 s AND no post-pod jump > 0.5 s
(=> D survives ad breaks; else D must be re-measured after every break).
"""
import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adslib as L  # noqa: E402

ID = "ads_11_E_invariance_pods"


def analyse(ev, logs, plays, args, out, ctx):
    pods = L.find_pods(ev)
    idx, segs = L.seg_index(ev, jump=120.0)  # only real retunes split; DAI jumps stay visible
    devs, jumps = [], []
    for p in pods:
        si = idx.get((p["host"], p["start"]))
        es = [e for e in (segs[si] if si is not None else []) if e["st"] == "playing"]  # same tune only
        before = [e["w"] - e["pos"] for e in es if p["start"] - 3600 <= e["w"] < p["start"]]
        inside = [e["w"] - e["pos"] for e in es if p["start"] <= e["w"] <= p["last"]]
        after = next((e for e in es if e["w"] > p["last"] + 1), None)  # same tune => no retune artefact
        if not before:
            before = inside[:1]
        Eb = st.median(before)
        d = [round(x - Eb, 3) for x in inside]
        devs += d
        j = None
        if after and after["w"] - p["last"] < 900:
            j = round(after["w"] - after["pos"] - Eb, 3)
            jumps.append(j)
        out.w("pod", stick=p["h"], channel=p["sub"], start=L.hhmmss(p["start"]), spots=p["spots"],
              dev=d, post_jump=j, n_before=len(before))
    ok = [abs(x) <= 0.3 for x in devs]
    frac = sum(ok) / len(ok) if ok else 0
    big = [j for j in jumps if abs(j) > 0.5]
    passed = bool(devs) and frac >= 0.95 and not big
    out.summary(passed, pods=len(pods), boundaries=len(devs), frac_within_0_3s=round(frac, 3),
                dev=L.robust([abs(x) for x in devs]), post_jumps=L.robust(jumps), big_jumps=big[:10])


if __name__ == "__main__":
    sys.exit(L.main(ID, __doc__, analyse,
                    "poll media_session and check E = wall - position stays constant through every DAI pod "
                    "(D survives ad breaks) or jumps (D must be re-measured)"))
