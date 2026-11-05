#!/usr/bin/env python3
"""fusion_06_heldout: does the fused estimate PREDICT anchors it has not seen? (no overfitting)

Hypothesis: if D_edge is a real per-(channel, stick) constant, an estimate fitted only on earlier
anchors predicts each later anchor within its stated sd, and the stated sd is calibrated (about 68% of
held-out anchors inside 1 sigma, >= 90% inside 2 sigma).

Procedure (forward chaining, never looks ahead):
  A. Anchor-by-anchor: for anchor i (time order) fit Kalman on anchors 0..i-1 of the same
     (channel, stick) (needs >= 2), predict D_edge_i; error, z = err / sqrt(sd_fit^2 + sd_i^2).
  B. Night folds (whole nights, noon->noon): train on all earlier nights, test on the night; report the
     MAE per fold, the mean over folds and the WORST fold (release-gate style).
Anchors: results/*.jsonl from every family + recorded ad pods (READ-ONLY live polling for --minutes).
Dry run also scores flagged synthetic anchors.

Pass rule (per group, >= 3 held-out predictions): MAE <= 1.5 s and |err| <= 2 s for >= 90% of the
PRECISE held-out anchors (sd <= 1.5 s; all anchors if none are precise),
2-sigma coverage >= 0.9, worst night fold MAE <= 2.0 s (when >= 2 nights). Read-only.
"""
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fusion_lib as L  # noqa: E402

ID = "fusion_06_heldout"


def score(anchors, out, label):
    rows = []
    for (ch, st), g in sorted(L.group(anchors).items()):
        g = sorted(g, key=lambda a: a.t)
        preds = []
        for i in range(2, len(g)):
            k = L.kalman(g[:i], L.prior_for(ch))
            e = g[i].D_edge - k["mu"]
            z = e / (k["sd"] ** 2 + g[i].sd_edge ** 2) ** 0.5
            preds.append(dict(t=g[i].t, fam=g[i].family, err=e, z=z, sd=g[i].sd_edge))
        nights = sorted({a.night for a in g})
        folds = []
        for j, n in enumerate(nights[1:], 1):
            tr = [a for a in g if a.night < n]
            te = [a for a in g if a.night == n]
            if tr and te:
                k = L.kalman(tr, L.prior_for(ch))
                folds.append(dict(night=n, n=len(te), mae=L.r2(statistics.mean(abs(a.D_edge - k["mu"]) for a in te))))
        if preds:
            # MAE / within-2 s on PRECISE held-out anchors only (sd <= 1.5): a weak anchor's own noise
            # (ad pods, sd ~8 s) would otherwise dominate; coverage uses every anchor.
            prec = [p for p in preds if p["sd"] <= 1.5] or preds
            errs = [abs(p["err"]) for p in prec]
            mae = statistics.mean(errs)
            w2 = sum(e <= 2.0 for e in errs) / len(errs)
            c1 = sum(abs(p["z"]) <= 1 for p in preds) / len(preds)
            c2 = sum(abs(p["z"]) <= 2 for p in preds) / len(preds)
        else:
            mae = w2 = c1 = c2 = None
        worst = max((f["mae"] for f in folds), default=None)
        ok = (len(preds) >= 3 and mae <= 1.5 and w2 >= 0.9 and c2 >= 0.9 and (worst is None or worst <= 2.0))
        row = dict(channel=ch, stick=st, n_pred=len(preds), mae=L.r2(mae), within_2s=L.r2(w2), cover_1sd=L.r2(c1),
                   cover_2sd=L.r2(c2), folds=folds, fold_mean=L.r2(statistics.mean(f["mae"] for f in folds)) if folds else None,
                   fold_worst=worst, pass_=ok, grade="pass" if ok else ("insufficient" if len(preds) < 3 else "fail"),
                   source=label, synthetic=label == "synthetic",
                   preds=[dict(t=L.fmt_t(p["t"]), fam=p["fam"], err=L.r2(p["err"]), z=L.r2(p["z"])) for p in preds][-20:])
        out.write("heldout", **row)
        rows.append(row)
        print(f"   [{label}] {ch:7s} {st:6s} n_pred={len(preds):3d} MAE={row['mae']} within2s={row['within_2s']} "
              f"cov1={row['cover_1sd']} cov2={row['cover_2sd']} folds={len(folds)} worst={worst} -> {row['grade']}")
    return rows


def main(argv=None):
    args = L.make_parser(ID, __doc__).parse_args(argv)
    out = L.Out(args.out, ID, args)
    B = L.build(args)
    L.header(ID, args, B["info"])
    rows = score(L.usable(B["anchors"]), out, "real")
    if B["synth"]:
        score(B["synth"], out, "synthetic")
    tested = [r for r in rows if r["grade"] != "insufficient"]
    ok = [r for r in tested if r["pass_"]]
    out.write("summary", pass_=bool(tested) and len(ok) == len(tested), n_groups=len(tested), n_pass=len(ok))
    print(f"SUMMARY {ID}: held-out prediction passes in {len(ok)}/{len(tested)} real groups -> {args.out}")
    out.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
