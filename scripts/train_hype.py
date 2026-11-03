"""Train and report hype v2 (see redzone/lab/hypetrain.py for the label and model).

    .venv/bin/python scripts/train_hype.py build [--step 20] [--out data/lab/hype_table.pkl]
    .venv/bin/python scripts/train_hype.py fit [--horizon 120] [--l2 3] [--write]
    .venv/bin/python scripts/train_hype.py ablate     # drop-one-feature table (test AUC / top-1)

`fit` prints, per league on the held-out (later) dates: AUC of the current score vs v2, top-1
"right game on screen" for cross-league polls, and a calibration table. --write saves the
model to redzone/scoring/hype_model.json (what the live scorer loads).
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone.lab import hypetrain as ht  # noqa: E402

MODEL = ROOT / "redzone/scoring/hype_model.json"


def report(T, train, test, models, out=print):
    import numpy as np
    p = ht.predict(models, T)
    out(f"rows {len(T['y'])} (train {train.sum()}, test {test.sum()}), horizon {T['horizon']} s, "
        f"positive rate {T['y'].mean():.3f}")
    out("\nPer league, held-out dates (AUC: does a game about to have a key moment score higher?)")
    out(f"{'league':7s} {'n':>7s} {'pos':>5s} {'AUC cur':>8s} {'AUC v2':>7s} {'logloss v2':>10s}")
    res = {}
    for b in sorted(set(T["base"])):
        m = test & (T["base"] == b)
        if m.sum() == 0:
            continue
        y = T["y"][m]
        a0, a1 = ht.auc(T["raw"][m], y), ht.auc(p[m], y)
        pc = np.clip(p[m], 1e-6, 1 - 1e-6)
        ll = float(-(y * np.log(pc) + (1 - y) * np.log(1 - pc)).mean())
        res[b] = dict(n=int(m.sum()), pos=float(y.mean()), auc_cur=a0, auc_v2=a1, logloss=ll)
        out(f"{b:7s} {m.sum():7d} {y.mean():5.2f} {a0:8.3f} {a1:7.3f} {ll:10.3f}")
    a0, a1 = ht.auc(T["raw"][test], T["y"][test]), ht.auc(p[test], T["y"][test])
    out(f"{'all':7s} {test.sum():7d} {T['y'][test].mean():5.2f} {a0:8.3f} {a1:7.3f}")
    r0 = ht.ranking(T, T["shown"], test)
    r1 = ht.ranking(T, p, test)
    out(f"\nCross-league polls (2+ live games, one about to have a key moment), held-out dates: {r0[1]}")
    out(f"  right game ranked #1: current {100 * r0[0]:.1f}%  v2 {100 * r1[0]:.1f}%")
    out(f"  pairwise ordering:    current {r0[2]:.3f}  v2 {r1[2]:.3f}")
    major = (T["swing"] >= 0.15).astype(float)
    m0 = ht.ranking(T, T["shown"], test, major)
    m1 = ht.ranking(T, p, test, major)
    out(f"  MAJOR moments (win probability moves 15%+), polls {m0[1]}: #1 current {100 * m0[0]:.1f}%  "
        f"v2 {100 * m1[0]:.1f}%  pairwise {m0[2]:.3f} -> {m1[2]:.3f}")
    out("  major-moment AUC by league: " + "  ".join(
        f"{b} {ht.auc(T['raw'][test & (T['base'] == b)], major[test & (T['base'] == b)]):.3f}->"
        f"{ht.auc(p[test & (T['base'] == b)], major[test & (T['base'] == b)]):.3f}" for b in sorted(set(T['base']))))
    out("\nCalibration (held-out): predicted vs actual key-moment rate")
    for b in sorted(set(T["base"])):
        m = test & (T["base"] == b)
        cal = ht.calibration(p[m], T["y"][m])
        out(f"  {b}: " + "  ".join(f"[{lo:.2f}-{hi:.2f}) n={n} {pp:.2f}->{yy:.2f}" for lo, hi, n, pp, yy in cal))
    return dict(leagues=res, top1_cur=r0[0], top1_v2=r1[0], pair_cur=r0[2], pair_v2=r1[2], polls=r0[1],
                major_top1_cur=m0[0], major_top1_v2=m1[0], major_polls=m0[1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "fit", "ablate"])
    ap.add_argument("--out", default=str(ROOT / "data/lab/hype_table.pkl"))
    ap.add_argument("--step", type=float, default=20.0)
    ap.add_argument("--horizon", type=int, default=120)
    ap.add_argument("--l2", type=float, default=3.0)
    ap.add_argument("--drop", default="")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--target", choices=["y", "swing", "mix"], default="mix")
    ap.add_argument("--mix-w", type=float, default=0.15, help="mix = (w * any key moment + swing) / (1 + w)")
    a = ap.parse_args()
    path = Path(a.out)
    if a.cmd == "build":
        path.parent.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        print(ht.build(path, step=a.step), f"{time.time() - t0:.0f}s")
        return
    T = ht.load_table(path, a.horizon)
    T["horizon"] = a.horizon
    T["mix"] = (a.mix_w * T["y"] + T["swing"]) / (1 + a.mix_w)
    train, test = ht.split_by_date(T)
    drop = [x for x in a.drop.split(",") if x]
    models = ht.fit_models(T, train, l2=a.l2, drop=drop, target=a.target)
    if a.cmd == "ablate":
        import numpy as np
        base = report(T, train, test, models, out=lambda *_: None)
        print(f"all features: top1 {100 * base['top1_v2']:.1f}% pair {base['pair_v2']:.3f}")
        for n in T["names"]:
            if n in drop:
                continue
            md = ht.fit_models(T, train, l2=a.l2, drop=drop + [n], target=a.target)
            p = ht.predict(md, T)
            r = ht.ranking(T, p, test)
            aucs = " ".join(f"{b}={ht.auc(p[test & (T['base'] == b)], T['y'][test & (T['base'] == b)]):.3f}"
                            for b in sorted(set(T["base"])))
            print(f"-{n:22s} top1 {100 * r[0]:.1f}% pair {r[2]:.3f}  {aucs}")
        return
    rep = report(T, train, test, models)
    print("\nStrongest weights (standardized), per league:")
    for b, md in models.items():
        ws = sorted(zip(md["names"], md["w"]), key=lambda x: -abs(x[1]))[:12]
        print(f"  {b}: " + ", ".join(f"{n} {w:+.2f}" for n, w in ws))
    if a.write:
        # Final model: refit on ALL dates (the held-out numbers above are the honest estimate).
        import numpy as np
        allm = np.ones(len(T["y"]), bool)
        final = ht.fit_models(T, allm, l2=a.l2, drop=drop, target=a.target)
        p = ht.predict(final, T)
        legacy = np.minimum(100.0, T["X"][:, T["names"].index("legacy")] * 100)
        out = dict(version=2, horizon_s=a.horizon, target=a.target, mix_w=a.mix_w, trained_on=f"{len(T['games'])} archived games, "
                   f"{sorted(set(T['day']))[0]}..{sorted(set(T['day']))[-1]}",
                   leagues=final, score_map=ht.score_map(p[~T["garbage"]], legacy[~T["garbage"]]),
                   holdout=rep)
        MODEL.write_text(json.dumps(out, indent=1))
        print(f"\nwrote {MODEL}")


if __name__ == "__main__":
    main()
