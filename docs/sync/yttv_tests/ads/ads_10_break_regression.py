#!/usr/bin/env python3
"""ads_10 regression over many breaks: per channel/stick/tune D with learned edge biases.

Hypothesis: every break gives noisy D observations from several edges (pod start vs timeout wall,
pod end vs restart, bitrate step vs timeout wall). Each edge kind has a fixed bias (network lead-in,
inbound delay). Model:  obs = D_tune + bias_kind + noise. With tune fixed effects (D is constant
within a tune: E is constant while playing) and kind biases (pod_start := 0 reference), least squares
over all breaks gives D_tune to <= 1 s when there are >= 6 breaks.

Procedure (read-only): run the ads_02 pairing and the ads_06 bitrate edge (per break, best step in
wall+5..90 s) on everything collected (live: --minutes; dry-run: all recorded days); tune = adslib
segment (title/subtitle change or position jump). Fit by alternating means (tiny, no numpy). Forward-
chaining CV by night: fit biases on earlier nights, predict D_tune on the next night from pod_start
only, report mean and worst-fold |err|.

Pass rule: mean |residual| <= 1.5 s over >= 6 observations from tunes with >= 2 observations, and worst-fold mean |err| <= 2 s when >= 2
nights exist.
"""
import math
import os
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adslib as L  # noqa: E402

ID = "ads_10_break_regression"


def observations(ev, logs, plays, args, hosts):
    anc = L.anchors(plays, args.game)
    segs = L.segments(ev)
    seg_id = {}
    for i, s in enumerate(segs):
        for e in s:
            seg_id[(e["host"], e["w"])] = i
    obs = []
    for p in L.find_pods(ev):
        m = L.match_pod_anchor(p, anc, plays, args.game)
        if not m:
            continue
        a, ds, de, g = m
        tune = seg_id.get((p["host"], p["start"]))
        night = datetime.fromtimestamp(a["wall"] - 6 * 3600).strftime("%Y-%m-%d")
        if ds is not None:
            obs.append(dict(tune=tune, night=night, kind="pod_start", D=ds, stick=p["h"]))
        if de is not None:
            obs.append(dict(tune=tune, night=night, kind="pod_end", D=de, stick=p["h"]))
    for h in hosts:
        series = [(t, math.log(k + 1)) for t, _, k in L.bitrate_series(logs.get(h, []))]
        for a in anc:
            if not L.stick_on_game(ev, h, a["wall"] + 30, a["game"], plays):
                continue
            best = None
            for i in range(3, len(series) - 3):
                t = series[i][0]
                if a["wall"] + 5 <= t <= a["wall"] + 90:
                    d = st.fmean(v for _, v in series[i:i + 3]) - st.fmean(v for _, v in series[i - 3:i])
                    if best is None or abs(d) > abs(best[1]):
                        best = (t, d)
            if best and abs(best[1]) > 0.3:
                e = L.showing(ev, h, best[0])
                tune = seg_id.get((h, e["w"])) if e else None
                night = datetime.fromtimestamp(a["wall"] - 6 * 3600).strftime("%Y-%m-%d")
                obs.append(dict(tune=tune, night=night, kind="bitrate_start", D=round(best[0] - a["wall"], 2),
                                stick=h[-8:-5]))
    return obs


def fit(obs, iters=30):
    bias = defaultdict(float)
    D = {}
    for _ in range(iters):
        by = defaultdict(list)
        for o in obs:
            by[o["tune"]].append(o["D"] - bias[o["kind"]])
        D = {k: st.median(v) for k, v in by.items()}
        bk = defaultdict(list)
        for o in obs:
            bk[o["kind"]].append(o["D"] - D[o["tune"]])
        bias = defaultdict(float, {k: (0.0 if k == "pod_start" else st.median(v)) for k, v in bk.items()})
    res = [o["D"] - D[o["tune"]] - bias[o["kind"]] for o in obs]
    return D, dict(bias), res


def analyse(ev, logs, plays, args, out, ctx):
    obs = observations(ev, logs, plays, args, ctx["hosts"])
    for o in obs:
        out.w("obs", **o)
    if len(obs) < 2:
        out.summary(False, n_obs=len(obs), note="too few break observations")
        return
    D, bias, res = fit(obs)
    per = defaultdict(int)
    for o in obs:
        per[o["tune"]] += 1
    # a tune with one observation fits itself exactly: only multi-observation tunes test the model
    res_multi = [r for o, r in zip(obs, res) if per[o["tune"]] >= 2]
    rs = L.robust([abs(r) for r in res_multi])
    out.w("fit", D_tune={str(k): round(v, 2) for k, v in D.items()}, bias={k: round(v, 2) for k, v in bias.items()},
          residual_abs=rs)
    nights = sorted({o["night"] for o in obs})
    folds = []
    for i in range(1, len(nights)):
        train = [o for o in obs if o["night"] < nights[i]]
        test = [o for o in obs if o["night"] == nights[i]]
        _, b, _ = fit(train)
        truth = defaultdict(list)
        pred = defaultdict(list)
        for o in test:
            truth[o["tune"]].append(o["D"] - b.get(o["kind"], 0.0))
            if o["kind"] == "pod_start":
                pred[o["tune"]].append(o["D"])
        errs = [abs(st.median(pred[k]) - st.median(truth[k])) for k in pred if truth[k]]
        if errs:
            folds.append(dict(night=nights[i], mean_err=round(st.fmean(errs), 2), n=len(errs)))
    for f in folds:
        out.w("fold", **f)
    worst = max((f["mean_err"] for f in folds), default=None)
    passed = rs.get("n", 0) >= 6 and rs["mean"] <= 1.5 and (worst is None or worst <= 2)
    out.summary(passed, n_obs=len(obs), n_obs_multi=len(res_multi), tunes=len(D), residual_abs=rs, folds=folds,
                cv_mean=round(st.fmean(f["mean_err"] for f in folds), 2) if folds else None, cv_worst=worst)


if __name__ == "__main__":
    sys.exit(L.main(ID, __doc__, analyse,
                    "collect pod and bitrate break edges, fit D per tune + bias per edge kind, and forward-chain "
                    "by night (mean + worst fold)"))
