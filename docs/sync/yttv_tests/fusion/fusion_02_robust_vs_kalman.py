#!/usr/bin/env python3
"""fusion_02_robust_vs_kalman: does the answer depend on the estimator? (outlier sensitivity)

Hypothesis: if the anchors are honest, a Huber M-estimate (IRLS, k=1.5), the plain inverse-variance
mean, the weighted median and the gated Kalman (fusion_01) all agree within 1 s. A disagreement means
one or more anchors are outliers (wrong game matched, ad pod mis-timed, UI read during a scrub) and
names them.

Procedure: build the same D_edge anchors as fusion_01 (results/*.jsonl + recorded ad pods, READ-ONLY
live polling if --minutes > 0). Per (channel, stick) compute Kalman, Huber, IV mean, median; list every
anchor whose Huber weight < 0.5 (its residual in sigma, family, time). Dry run also does it on
flagged synthetic anchors with 8% injected outliers.

Pass rule (per group, n >= 3): |Kalman - Huber| <= 1.0 s AND |Huber - median| <= 2.0 s AND
both sds <= 2.0 s. Read-only.
"""
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fusion_lib as L  # noqa: E402

ID = "fusion_02_robust_vs_kalman"


def compare(anchors, out, label, truth=None):
    res = []
    for (ch, st), g in sorted(L.group(anchors).items()):
        xs, sds = [a.D_edge for a in g], [a.sd_edge for a in g]
        k = L.kalman(g, L.prior_for(ch))
        hm, hs, w = L.huber(xs, sds)
        iv, ivs = L.iv_mean(xs, sds)
        med = statistics.median(xs)
        down = [dict(t=L.fmt_t(a.t), fam=a.family, D_edge=L.r2(a.D_edge), resid_sigma=L.r2((a.D_edge - hm) / a.sd_edge),
                     w=L.r2(wi)) for a, wi in zip(g, w) if wi < 0.5]
        ok = (len(g) >= 3 and abs(k["mu"] - hm) <= 1.0 and abs(hm - med) <= 2.0 and k["sd"] <= 2.0 and hs <= 2.0)
        row = dict(channel=ch, stick=st, n=len(g), kalman=L.r2(k["mu"]), kalman_sd=L.r2(k["sd"]), huber=L.r2(hm),
                   huber_sd=L.r2(hs), iv=L.r2(iv), median=L.r2(med), d_kh=L.r2(k["mu"] - hm),
                   downweighted=down, pass_=ok, source=label, synthetic=label == "synthetic")
        if truth and (ch, st) in truth:
            row["huber_err"] = L.r2(hm - truth[(ch, st)])
            row["iv_err"] = L.r2(iv - truth[(ch, st)])
        out.write("compare", **row)
        res.append(row)
        print(f"   [{label}] {ch:7s} {st:6s} n={len(g):3d} kalman={row['kalman']}+-{row['kalman_sd']} "
              f"huber={row['huber']}+-{row['huber_sd']} iv={row['iv']} med={row['median']} "
              f"outliers={len(down)} -> {'PASS' if ok else 'fail'}"
              + (f"  huber_err={row.get('huber_err')} iv_err={row.get('iv_err')}" if truth else ""))
    return res


def main(argv=None):
    args = L.make_parser(ID, __doc__).parse_args(argv)
    out = L.Out(args.out, ID, args)
    B = L.build(args)
    L.header(ID, args, B["info"])
    rows = compare(L.usable(B["anchors"]), out, "real")
    if B["synth"]:
        compare(B["synth"], out, "synthetic", B["truth"])
    ok = [r for r in rows if r["pass_"]]
    out.write("summary", pass_=bool(rows) and len(ok) == len(rows), n_groups=len(rows), n_pass=len(ok))
    print(f"SUMMARY {ID}: {len(ok)}/{len(rows)} real groups estimator-stable -> {args.out}")
    out.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
