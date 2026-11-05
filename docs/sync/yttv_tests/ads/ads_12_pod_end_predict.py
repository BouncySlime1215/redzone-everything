#!/usr/bin/env python3
"""ads_12 pod-end prediction: when does the picture return to the game?

Hypothesis: the end of an ad pod is a switching-relevant edge (return to game == restart + D). The
pod's spot grid (10/15/30 s) plus the last spot's start predicts the return; recordings suggest a
final short media_session update when program resumes (e.g. 5.0 s after the last 15 s spot start on
11-04 18:29:04 -> 18:29:09). If the 'return' update reliably fires, pod_end is measured, not guessed,
and (with ads_02) D_end = return_update - restart_wall.

Procedure (read-only): media_session polling for --minutes; for each pod: predicted end =
last boundary + median spot gap; observed return = first update after the last boundary within 40 s
that breaks the grid (gap < 8 s or > 17 s) or changes title. err = observed - predicted.

Pass rule: >=3 pods with an observed return and median |err| <= 2 s, or a stable 'return-update'
lag (MAD <= 1 s) that can be used as a measured pod end.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adslib as L  # noqa: E402

ID = "ads_12_pod_end_predict"


def analyse(ev, logs, plays, args, out, ctx):
    pods = L.find_pods(ev)
    errs, lags = [], []
    for p in pods:
        es = [e for e in ev if e["host"] == p["host"] and p["last"] < e["w"] <= p["last"] + 40]
        ret = es[0] if es else None
        err = lag = None
        if ret:
            lag = round(ret["w"] - p["last"], 2)
            err = round(ret["w"] - p["end_est"], 2)
            errs.append(err)
            lags.append(lag)
        out.w("pod", stick=p["h"], channel=p["sub"], start=L.hhmmss(p["start"]), spots=p["spots"],
              gaps=p["gaps"], predicted_end=L.hhmmss(p["end_est"]), observed_return=L.hhmmss(ret["w"]) if ret else None,
              return_lag=lag, err=err, return_title_changed=(ret["title"] != p["title"]) if ret else None)
    se, sl = L.robust([abs(x) for x in errs]), L.robust(lags)
    passed = se.get("n", 0) >= 3 and (se["median"] <= 2 or sl["mad"] <= 1)
    out.summary(passed, pods=len(pods), with_return=len(errs), abs_err=se, return_lag=sl)


if __name__ == "__main__":
    sys.exit(L.main(ID, __doc__, analyse,
                    "poll media_session; for each DAI pod predict the return-to-program time from the spot grid "
                    "and compare with the first non-grid update after the pod"))
