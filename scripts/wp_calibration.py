#!/usr/bin/env python3
"""EXP-002: is each league's live win probability calibrated enough to feed the suspense score (EXP-001)?

Serves: North Star "crunch live" / "big plays seen live" through Watch Value (S = E[(dWP)^2] needs a calibrated
WP); owner: lane 1 (Noggin training).

    python scripts/wp_calibration.py nfl nba nhl [--archive data/archive] [--out redzone/noggin/models]

WP = the brain track's wp_home (ESPN's home_win_prob where the feed has it, else brain.analytic_wp), on every
live play of the archive (redzone/lab/reasoner_data rows, the same rows Watch Value trains on). Label = did the
home team win (the summary's header). ECE = 10 equal-width bins, row-weighted, |mean WP - win rate|.

No overfitting: a Platt recalibration (logit q = a + b logit wp, + c late) is fitted on TRAIN nights only,
chosen on VALID. The LOCKED nights are read only with --locked (once, by the integrator, for the gate): a
league whose locked ECE (after the chosen map) is above GATE_ECE keeps S off, and a league with fewer than
MIN_LOCKED_NIGHTS locked nights is PENDING (S off). Without --locked the gate is PENDING everywhere.
Writes wp_cal.v1.json (per league: map, gate) and a report with the dev slices and reliability bins.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

GATE_ECE = 0.05       # docs/experiments.md EXP-002
LATE_FRAC = 0.25      # the last quarter of regulation: the slice S weighs most
BINS = 10
MIN_LOCKED_NIGHTS = 8 # EXP-002: "locked >= 8 nights per league (PENDING below)"


def ece(p: np.ndarray, y: np.ndarray, bins: int = BINS) -> float | None:
    if not len(p):
        return None
    idx = np.clip((p * bins).astype(int), 0, bins - 1)
    err = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            err += m.sum() * abs(p[m].mean() - y[m].mean())
    return round(float(err / len(p)), 4)


def _logit(p):
    p = np.clip(np.asarray(p, float), 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def platt(p: np.ndarray, late: np.ndarray, y: np.ndarray, iters: int = 50, l2: float = 1e-3) -> tuple:
    """(a, b, c): logit q = a + b * logit(p) + c * late * logit(p). Newton with a small ridge toward identity."""
    X = np.column_stack([np.ones(len(p)), _logit(p), late * _logit(p)])
    w = np.array([0.0, 1.0, 0.0])
    prior, reg = w.copy(), l2 * len(y) * np.eye(3)
    for _ in range(iters):
        q = 1 / (1 + np.exp(-(X @ w)))
        g = X.T @ (q - y) + reg @ (w - prior)
        H = (X * (q * (1 - q))[:, None]).T @ X + reg
        step = np.linalg.solve(H, g)
        w = w - step
        if np.abs(step).max() < 1e-8:
            break
    return tuple(round(float(v), 4) for v in w)


def apply(coef, p: np.ndarray, late: np.ndarray) -> np.ndarray:
    if coef is None:
        return p
    a, b, c = coef
    z = a + b * _logit(p) + c * late * _logit(p)
    return 1 / (1 + np.exp(-z))


def home_won(summary: dict) -> int | None:
    comp = ((summary.get("header") or {}).get("competitions") or [{}])[0]
    for c in comp.get("competitors") or []:
        if c.get("homeAway") == "home" and c.get("winner") is not None:
            return int(bool(c["winner"]))
    return None


def reliability(p, y, bins: int = BINS) -> list[dict]:
    """Per bin: rows, mean WP, home win rate (the reliability curve, grading rule 6)."""
    idx = np.clip((p * bins).astype(int), 0, bins - 1)
    return [{"bin": b, "n": int((idx == b).sum()), "wp": round(float(p[idx == b].mean()), 4),
             "won": round(float(y[idx == b].mean()), 4)} for b in range(bins) if (idx == b).any()]


def ece_ci(p, y, groups, n_boot: int = 500, seed: int = 7) -> list | None:
    """95% cluster-bootstrap CI of ECE, resampling whole games (grading rule 4)."""
    ug = np.unique(groups)
    if len(ug) < 2:
        return None
    rng, by = np.random.default_rng(seed), {g: np.flatnonzero(groups == g) for g in ug}
    vals = []
    for _ in range(n_boot):
        ix = np.concatenate([by[g] for g in rng.choice(ug, len(ug))])
        vals.append(ece(p[ix], y[ix]))
    return [round(float(np.percentile(vals, 2.5)), 4), round(float(np.percentile(vals, 97.5)), 4)]


def slices(p, y, late, ot, garbage) -> dict:
    return {"all": ece(p, y), "reliability": reliability(p, y), "late": ece(p[late], y[late]), "ot": ece(p[ot], y[ot]),
            "garbage": ece(p[garbage], y[garbage]), "rows": int(len(p)), "late_rows": int(late.sum())}


def league(lg: str, archive: Path, cfg, locked: bool = False) -> dict:
    from redzone.lab import reasoner_data as RD
    from redzone.lab import splits
    from redzone.models import League
    rows = [r for r in RD.league_rows(League(lg), archive / lg, cfg, {"WSH", "UNC"}, every=1)
            if r.wp_home is not None and not r.in_break]
    won = {}
    for gid in {r.gid for r in rows}:
        f = archive / lg / f"summary_{gid}.json"
        won[gid] = home_won(json.loads(f.read_text())) if f.exists() else None
    rows = [r for r in rows if won.get(r.gid) is not None]
    p = np.array([float(r.wp_home) for r in rows])
    y = np.array([won[r.gid] for r in rows], float)
    fl = np.array([float((r.facts or {}).get("frac_left", np.nan)) for r in rows])
    late = (np.nan_to_num(fl, nan=1.0) <= LATE_FRAC).astype(float)
    ot = np.array([bool((r.facts or {}).get("is_ot")) for r in rows])
    garbage = np.array([bool(r.garbage) for r in rows])
    sp = splits.split_nights({r.night for r in rows})
    part = {n: s for s, ns in (("train", sp.train), ("valid", sp.valid), ("test", sp.test)) for n in ns}
    split = np.array([part[r.night] for r in rows])
    tr, va, te = split == "train", split == "valid", split == "test"
    gids = np.array([r.gid for r in rows])
    coef = platt(p[tr], late[tr], y[tr]) if tr.sum() >= 500 else None
    raw_dev, cal_dev = ece(p[va], y[va]), ece(apply(coef, p[va], late[va]), y[va])
    use = coef if coef is not None and cal_dev is not None and raw_dev is not None and cal_dev < raw_dev else None
    q = apply(use, p, late.astype(float))
    lb = late.astype(bool)
    rep = {"league": lg, "games": len({r.gid for r in rows}),
           "nights": {s: len(getattr(sp, s)) for s in ("train", "valid", "test")},
           "map": {"kind": "platt_late", "coef": list(use)} if use else {"kind": "identity"},
           "dev_raw": slices(p[va], y[va], lb[va], ot[va], garbage[va]),
           "dev": slices(q[va], y[va], lb[va], ot[va], garbage[va])}
    rep["dev"]["ci95_games"] = ece_ci(q[va], y[va], gids[va])
    rep["dev"]["games"] = int(len(np.unique(gids[va])))
    rep["dev"]["nights"] = len(sp.valid)
    n_locked = len(sp.test)
    if not locked:
        rep["gate"] = {"ece_max": GATE_ECE, "status": "PENDING", "why": "locked not read (run with --locked)",
                       "locked_nights": n_locked, "s_enabled": False}
        return rep
    rep["locked_raw"] = slices(p[te], y[te], lb[te], ot[te], garbage[te])
    rep["locked"] = slices(q[te], y[te], lb[te], ot[te], garbage[te])      # scored once, never tuned on
    e = rep["locked"]["all"]
    status = "PENDING" if n_locked < MIN_LOCKED_NIGHTS else ("PASS" if e is not None and e <= GATE_ECE else "FAIL")
    rep["gate"] = {"ece_max": GATE_ECE, "locked_ece": e, "locked_nights": n_locked, "status": status,
                   "s_enabled": status == "PASS"}
    return rep


def main(argv=None) -> int:
    from redzone.config import Config
    ap = argparse.ArgumentParser()
    ap.add_argument("leagues", nargs="+")
    ap.add_argument("--archive", type=Path, default=ROOT / "data" / "archive")
    ap.add_argument("--config", type=Path, default=ROOT / "data" / "eval_config.yaml")
    ap.add_argument("--out", type=Path, default=ROOT / "redzone" / "noggin" / "models" / "wp_cal.v1.json")
    ap.add_argument("--report", type=Path, default=ROOT / "data" / "training" / "wp_calibration_report.json")
    ap.add_argument("--locked", action="store_true", help="score the locked nights (once per release)")
    a = ap.parse_args(argv)
    import tempfile
    text = (a.config.read_text() if a.config.exists() else "") + "\nbrain:\n  enabled: true\n"
    tmp = Path(tempfile.mkdtemp()) / "wp_cal_config.yaml"
    tmp.write_text(text)
    cfg = Config(user_path=tmp)
    rep = {lg: league(lg, a.archive, cfg, a.locked) for lg in a.leagues}
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text(json.dumps(rep, indent=1))
    a.out.write_text(json.dumps({lg: {"map": r["map"], "gate": r["gate"]} for lg, r in rep.items()}, indent=1) + "\n")
    print(json.dumps({lg: {"games": r["games"], "map": r["map"], "dev_raw": r["dev_raw"]["all"],
                           "dev": {k: v for k, v in r["dev"].items() if k != "reliability"}, "gate": r["gate"]}
                      for lg, r in rep.items()}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
