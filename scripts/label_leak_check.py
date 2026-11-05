"""Context-only label leak check (grader freeze v17; Nick, Nov 5 1:50 AM).

A label that context alone (clock, period, score gap, crunch/late/garbage, your team) predicts with
AUC >= 0.95 is mostly the rule that made it, not something that happened. CBB's hindsight lock label
did exactly that (context-only AUC 0.960 vs the full head's 0.967), so a head that "beat" the Teacher
there was learning the rule. Any such label fails here and stays out of training and scorecards.

    python scripts/label_leak_check.py rows.jsonl.gz --label target.lock_in=must_see --league cbb \
        --name reasoner_lock --out docs/label_checks/reasoner_lock_cbb.json
    python scripts/label_leak_check.py            # CI: judge every report in docs/label_checks/

Rows are JSON lines with `night`, `league`, the features under `facts` (reasoner moments) or
`features`, and the label at a dotted path. `--label a.b=v` means "a.b == v" is the positive class;
`--label y` means truthy.

The context-only model is stdlib only: every context feature cut into quantile bins, the joint bin's
positive rate (smoothed toward the base rate) fitted on the other night folds (cross-fitted by whole
night, so a night never grades itself). The check also reports every single feature's AUC. The
headline is the larger of the joint and the best single feature.

Reports in docs/label_checks/*.json hold derived numbers only (the repo is public). CI fails on any
report at or above the bar, unless its league's lock label is already out of every scorecard
(agent/distill/evaluate.LOCK_EXCLUDED) and the report says it is the lock label: then it reads EXCLUDED.

Graders never import decider, noggin or reasoner code: CONTEXT below is this grader's own list.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from data_landing_grade import auc  # noqa: E402  (a frozen grader's own Mann-Whitney AUC)

AUC_BAR = 0.95          # Nick: fail any label context-only features predict at AUC >= 0.95
MIN_ROWS = 200          # fewer labelled rows than this (or one class missing): PENDING
BINS = 5
SMOOTH = 5.0            # pseudo-rows pulling a sparse joint bin toward the base rate
FOLDS = 3
REPORTS = ROOT / "docs" / "label_checks"
CONTEXT = ["period", "clock_s", "frac_left", "is_ot", "abs_diff", "in_break",
           "crunch", "garbage", "late", "mine"]


def _open(p: Path):
    return gzip.open(p, "rt") if p.suffix == ".gz" else p.open()


def read_rows(paths: list[Path]) -> list[dict]:
    out = []
    for p in paths:
        with _open(p) as fh:
            out += [json.loads(ln) for ln in fh if ln.strip()]
    return out


def _get(r: dict, path: str):
    v = r
    for k in path.split("."):
        if not isinstance(v, dict):
            return None
        v = v.get(k)
    return v


def label_fn(spec: str):
    """'a.b=v' -> a.b == v; 'a.b' -> truthy."""
    path, _, want = spec.partition("=")
    if want:
        return lambda r: None if _get(r, path) is None else int(str(_get(r, path)) == want)
    return lambda r: None if _get(r, path) is None else int(bool(_get(r, path)))


def _num(v) -> float | None:
    if isinstance(v, bool):
        return float(v)
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _feats(r: dict) -> dict:
    return r.get("facts") or r.get("features") or r


def _fold(night, k: int) -> int:
    return int(hashlib.sha1(str(night).encode()).hexdigest(), 16) % k


def _cuts(xs: list[float]) -> list[float]:
    xs = sorted(xs)
    return sorted({xs[min(len(xs) - 1, int(len(xs) * q / BINS))] for q in range(1, BINS)})


def _bin(v: float | None, cuts: list[float]) -> int:
    if v is None:
        return -1
    return sum(v > c for c in cuts)


def context_auc(rows: list[dict], label, context: list[str] = CONTEXT, k: int = FOLDS) -> dict:
    """{n, base_rate, single: {feature: auc}, joint_auc, max_auc, features}; aucs None when undefined."""
    lab = [(r, y) for r in rows if (y := label(r)) is not None]
    ys = [y for _, y in lab]
    out = {"n": len(lab), "base_rate": round(sum(ys) / len(ys), 4) if ys else None,
           "single": {}, "joint_auc": None, "max_auc": None, "features": []}
    if len(lab) < 2 or len(set(ys)) < 2:
        return out
    vals = {f: [_num(_feats(r).get(f)) for r, _ in lab] for f in context}
    feats = [f for f in context if sum(v is not None for v in vals[f]) >= max(2, len(lab) // 10)]
    out["features"] = feats
    for f in feats:
        sub = [(v, y) for v, y in zip(vals[f], ys) if v is not None]
        a = auc([v for v, _ in sub], [y for _, y in sub])
        if a is not None:
            out["single"][f] = round(max(a, 1 - a), 4)
    nights = [r.get("night", i) for i, (r, _) in enumerate(lab)]
    folds = [_fold(n, k) for n in nights]
    preds = [0.0] * len(lab)
    for fk in range(k):
        tr = [i for i in range(len(lab)) if folds[i] != fk]
        te = [i for i in range(len(lab)) if folds[i] == fk]
        if not tr or not te:
            continue
        cuts = {f: _cuts([vals[f][i] for i in tr if vals[f][i] is not None] or [0.0]) for f in feats}
        key = lambda i: tuple(_bin(vals[f][i], cuts[f]) for f in feats)  # noqa: E731
        rate = sum(ys[i] for i in tr) / len(tr)
        tab: dict = defaultdict(lambda: [0, 0])
        for i in tr:
            c = tab[key(i)]
            c[0] += ys[i]
            c[1] += 1
        for i in te:
            s, n = tab.get(key(i), (0, 0))
            preds[i] = (s + SMOOTH * rate) / (n + SMOOTH)
    j = auc(preds, ys)
    out["joint_auc"] = round(j, 4) if j is not None else None
    cands = [a for a in [out["joint_auc"], *out["single"].values()] if a is not None]
    out["max_auc"] = max(cands) if cands else None
    return out


def judge(rep: dict, excluded: dict | None = None) -> tuple[str, str]:
    """(PASS | FAIL | PENDING | EXCLUDED, detail) for one report."""
    name = f"{rep.get('name', '?')} {str(rep.get('league', '')).upper()}".strip()
    a, n = rep.get("max_auc"), rep.get("n")
    if a is None or (n is not None and n < MIN_ROWS and a < AUC_BAR):
        return "PENDING", f"{name}: {n or 0} labelled rows (needs >= {MIN_ROWS} with both classes)"
    single = rep.get("single") or {}
    top = max(single, key=single.get) if single else None
    best = f", best single {top}={single[top]}" if top else ""
    det = (f"{name}: context-only AUC {a:.3f} (joint {rep.get('joint_auc')}{best}) over {n if n is not None else 'an unrecorded number of'} rows")
    if a < AUC_BAR:
        return "PASS", det + f" < {AUC_BAR}"
    excluded = excluded if excluded is not None else _excluded()
    if rep.get("kind") == "lock" and str(rep.get("league")) in excluded:
        return "EXCLUDED", det + f" >= {AUC_BAR}; out of every scorecard ({excluded[str(rep.get('league'))]})"
    return "FAIL", det + f" >= {AUC_BAR}: context predicts this label; relabel from real outcomes"


def _excluded() -> dict:
    try:
        from agent.distill.evaluate import LOCK_EXCLUDED
    except ImportError:
        return {}
    return dict(LOCK_EXCLUDED)


def judge_reports(folder: Path = REPORTS) -> list[tuple[str, str]]:
    files = sorted(folder.glob("*.json")) if folder.exists() else []
    if not files:
        return [("PENDING", f"no label-check reports in {folder.relative_to(ROOT) if folder.is_relative_to(ROOT) else folder}")]
    return [judge(json.loads(p.read_text())) for p in files]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("rows", nargs="*", type=Path)
    ap.add_argument("--label", default="target.lock_in=must_see")
    ap.add_argument("--league")
    ap.add_argument("--name", default="label")
    ap.add_argument("--kind", default="", help="'lock' for a lock_in label (the only kind EXCLUDED applies to)")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--reports", type=Path, default=REPORTS)
    a = ap.parse_args(argv)
    if a.rows:
        rows = read_rows(a.rows)
        if a.league:
            rows = [r for r in rows if r.get("league") == a.league]
        rep = {"name": a.name, "league": a.league, "label": a.label, "kind": a.kind or
               ("lock" if "lock_in" in a.label else ""), **context_auc(rows, label_fn(a.label))}
        if a.out:
            a.out.parent.mkdir(parents=True, exist_ok=True)
            a.out.write_text(json.dumps(rep, indent=1) + "\n")
        res = [judge(rep)]
    else:
        res = judge_reports(a.reports)
    for st, det in res:
        print(f"{st:8} {det}")
    return 1 if any(st == "FAIL" for st, _ in res) else 0


if __name__ == "__main__":
    sys.exit(main())
