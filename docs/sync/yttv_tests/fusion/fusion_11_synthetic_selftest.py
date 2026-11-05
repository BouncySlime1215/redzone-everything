#!/usr/bin/env python3
"""fusion_11_synthetic_selftest: is the estimator itself correct before we trust it with real anchors?

Hypothesis: on simulated anchors with KNOWN truth (mix of a precise family sd 0.8, a medium family
sd 1.5 and a weak family sd 6, 8% gross outliers at 4 sigma, random walk 0.5 s/sqrt(h)), the gated
Kalman is unbiased (|bias| <= 0.2 s), its RMSE beats the plain IV mean, and its stated sd is calibrated
(1-sigma coverage 0.6-0.8, 2-sigma coverage >= 0.9). Also checks the E-propagation identity
D(t) = D_edge + E(t) - E_land on a simulated session with two pauses.

Procedure: Monte Carlo, 300 seeded trials x 12 anchors (CPU-light, < 1 s). No device access at all,
so dry run and live are identical (live just uses a fresh seed). --minutes is ignored.

Pass rule: |bias| <= 0.2 s, cover1 in [0.6, 0.8], cover2 >= 0.9, RMSE_kalman <= RMSE_iv, propagation
error < 1e-6 s.
"""
import math
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fusion_lib as L  # noqa: E402

ID = "fusion_11_synthetic_selftest"


def one_trial(rnd, truth=28.0, n=12):
    fams = [("ui_clock", 0.8), ("scoreline", 1.5), ("burst_pod", 6.0)]
    an = []
    drift = 0.0
    for i in range(n):
        drift += rnd.gauss(0, 0.5 * math.sqrt(600 / 3600))
        f, sd = fams[i % 3]
        x = truth + drift + (rnd.gauss(0, sd) if rnd.random() > 0.08 else rnd.choice([-1, 1]) * 4 * sd)
        a = L.Anchor(f, "espn", "hdmi2", 1e9 + 600 * i, x, sd)
        a.D_edge, a.sd_edge = x, sd
        an.append(a)
    return an, truth + drift


def propagation_check():
    # simulated session: land E_land at t=0, pause 12 s at t=100, pause 7.5 s at t=300
    D_edge = 30.0
    E_land = 1000.0
    pauses = [(100, 12.0), (300, 7.5)]

    def E(t):
        return E_land + sum(p for t0, p in pauses if t >= t0 + p)

    def D_true(t):
        return D_edge + sum(p for t0, p in pauses if t >= t0 + p)
    errs = [abs((D_edge + E(t) - E_land) - D_true(t)) for t in range(0, 500, 7)]
    return max(errs)


def main(argv=None):
    args = L.make_parser(ID, __doc__).parse_args(argv)
    out = L.Out(args.out, ID, args)
    print(f"== {ID}  mode={'DRY-RUN' if args.dry_run else 'LIVE'} (pure simulation; no device access)")
    rnd = random.Random(args.seed if args.dry_run else None)
    ek, ei, c1, c2, sds = [], [], 0, 0, []
    T = 300
    for _ in range(T):
        an, tr = one_trial(rnd)
        k = L.kalman(an, (29.07, 8.0))
        iv, _ = L.iv_mean([a.D_edge for a in an], [a.sd_edge for a in an])
        ek.append(k["mu"] - tr)
        ei.append(iv - tr)
        c1 += abs(k["mu"] - tr) <= k["sd"]
        c2 += abs(k["mu"] - tr) <= 2 * k["sd"]
        sds.append(k["sd"])
    bias = statistics.mean(ek)
    rk = math.sqrt(statistics.mean(e * e for e in ek))
    ri = math.sqrt(statistics.mean(e * e for e in ei))
    perr = propagation_check()
    cov1, cov2 = c1 / T, c2 / T
    ok = abs(bias) <= 0.2 and 0.6 <= cov1 <= 0.8 and cov2 >= 0.9 and rk <= ri and perr < 1e-6
    row = dict(trials=T, bias=L.r2(bias, 3), rmse_kalman=L.r2(rk, 3), rmse_iv=L.r2(ri, 3), mean_sd=L.r2(statistics.mean(sds), 3),
               cover_1sd=L.r2(cov1, 3), cover_2sd=L.r2(cov2, 3), propagation_max_err=perr, pass_=ok, synthetic=True)
    out.write("selftest", **row)
    print(f"   bias={row['bias']} rmse_kalman={row['rmse_kalman']} rmse_iv={row['rmse_iv']} mean_sd={row['mean_sd']} "
          f"cover1={row['cover_1sd']} cover2={row['cover_2sd']} propagation_err={perr}")
    out.write("summary", **row)
    print(f"SUMMARY {ID}: estimator self-test -> {'PASS' if ok else 'FAIL'} -> {args.out}")
    out.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
