"""Fit and grade how the break forecast mixes the rules with the brain (``breaks.blend``).

``scripts/eval_break_blend.py`` is the CLI. Rows come from the live code path (espn.parse_summary ->
BreakBook rules + brain v2 ``break_soon_p``), one per play, on our own ESPN archive; labels come from
the espn_<league> training table (``scripts/build_training.py espn_<league>``).

[no overfitting] Which nights may be read:
  * never the LOCKED test nights of the archive split (redzone/lab/splits.py);
  * never a night the brain itself trained or early-stopped on (``BRAIN_SEEN_THROUGH``);
  * the rest ("eligible") is cut into forward-chaining folds by whole night: each fold fits on the
    older nights and is graded on the next block. ECE is reported on those held-out blocks only
    (mean and worst fold). The shipped coefficients are fitted on every eligible night.

The blend is logistic stacking in log-odds where the rules have evidence (rules p > 0):
    logit q = a + b * logit(p_brain) + c * logit(p_rules)
and the brain alone where they don't. (0, 1, 0) is "the brain alone everywhere".

Why: recorded in docs/memory/lessons.md (ship a break blend only if it beats the brain alone on mean and worst fold); first added in 64a410ff (2026-11-04) as "jsonfind: accept() per caller, trailing-comma repair, cut-off think tag; malformed/nested tests".
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from redzone.lab import brainfit, datasets, splits

# The last night each league's brain v2 (brain_models/<league>.npz) saw in training or early
# stopping (brainfit.time_split: older seasons train, the newest season's first half validates).
#   NFL: train 2023-2024, valid = first half of 2025 by schedule (weeks 1-9, through MNF Nov 3 2025).
#   NBA / NHL: sportsdataverse seasons 2025 and 2026 (ending June 2026); the archive starts Oct 2026.
BRAIN_SEEN_THROUGH = {"nfl": "20251104", "nba": "20260701", "nhl": "20260701"}
FOLDS = 3
L2 = 1e-2


@dataclass
class Rows:
    night: np.ndarray     # 'YYYYMMDD' per row
    y: np.ndarray         # break_soon label (0/1)
    rules: np.ndarray     # breaks.forecast().p
    now: np.ndarray       # the break is already in the data (rules keep it, never blended)
    brain: np.ndarray     # brain v2 break_soon_p

    def take(self, m) -> "Rows":
        return Rows(self.night[m], self.y[m], self.rules[m], self.now[m], self.brain[m])

    def __len__(self) -> int:
        return len(self.y)


def collect(league: str, archive: Path | None = None, root: Path | None = None, end: str | None = None) -> Rows:
    """Every live play row of every archived game (play rows only: not in a break, not game end,
    the same rows brainfit.live_eval grades)."""
    from redzone.config import Config
    from redzone.models import League, Phase
    from redzone.scoring import brain as B
    from redzone.scoring import breaks
    from redzone.sources import espn

    lg = League(league)
    archive = archive or (datasets.ROOT / "data" / "archive" / league)
    night_of = {gid: nt for nt, ids in splits.archive_nights(archive, end).items() for gid in ids}
    lab = datasets.load(f"espn_{league}", columns=["game_id", "seq", "break_soon", "in_break", "stoppage"], root=root)
    lab = lab[lab.game_id.astype(str).isin(night_of)]
    labels = {(str(g), int(s)): (b, ib, st) for g, s, b, ib, st in
              zip(lab.game_id, lab.seq, lab.break_soon, lab.in_break, lab.stoppage)}
    Config(user_path=datasets.ROOT / "data" / "_none.yaml")   # same defaults as the app
    out = []
    end_code = datasets.STOPPAGE_CODE["game_end"]
    for gid in sorted(night_of, key=lambda g: (night_of[g], g)):
        path = archive / f"summary_{gid}.json"
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        plays = espn.flatten_plays(lg, data)
        wall = [espn._ts(p.get("wallclock")) for p in plays]
        clock = {"t": 0.0}
        brain = B.Brain(version=2)
        book = breaks.BreakBook(clock=lambda: clock["t"])
        last_t = 0.0
        for i in range(len(plays)):
            last_t = wall[i] or (last_t + 5.0)
            clock["t"] = last_t
            g = espn.parse_summary(lg, data, upto=i, plays=plays)
            if g is None or g.phase != Phase.LIVE:
                continue
            lab_i = labels.get((gid, i))
            if lab_i is None:
                continue
            book.observe(g)
            f = breaks.forecast(g, book.clocks(g.game_id, g.period))
            r = brain.read(g)
            if r.break_soon_p is None or lab_i[1] != 0 or lab_i[2] == end_code:
                continue
            out.append((night_of[gid], float(lab_i[0]), f.p, f.now, r.break_soon_p))
    if not out:
        z = np.zeros(0)
        return Rows(np.zeros(0, dtype="<U8"), z, z, z.astype(bool), z)
    n, y, fr, now, b = zip(*out)
    return Rows(np.array(n), np.array(y, float), np.array(fr, float), np.array(now, bool), np.array(b, float))


def eligible_nights(league: str, archive: Path | None = None, end: str | None = None) -> list[str]:
    """Archive nights that are neither LOCKED test nights nor nights the brain saw."""
    archive = archive or (datasets.ROOT / "data" / "archive" / league)
    s = splits.league_split(Path(archive).name, splits.archive_nights(archive, end))
    seen = BRAIN_SEEN_THROUGH.get(league, "99999999")
    return [n for n in s.train + s.valid if n > seen]


# --- the blend (vectorised; breaks.blend is the per-forecast twin, tested to agree) ---------------

def _logit(p):
    p = np.clip(np.asarray(p, float), 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def apply(coef, r: Rows) -> np.ndarray:
    """What breaks.blend gives each row with these coefficients (None = the legacy 50/50 mix)."""
    if coef is None:   # the old blend: 0.5 * p + 0.5 * rules (so 0.5 * p when the rules see nothing)
        q = 0.5 * r.brain + 0.5 * r.rules
    else:
        a, b, c = coef
        z = a + b * _logit(r.brain) + c * _logit(r.rules)
        q = np.where(r.rules > 0, 1 / (1 + np.exp(-z)), r.brain)
    return np.where(r.now, r.rules, q)


def fit(r: Rows, l2: float = L2, iters: int = 50) -> tuple[float, float, float]:
    """Logistic regression (Newton / IRLS, small ridge) on the rows where the rules have evidence
    and the break isn't already in the data. Too few such rows: the brain alone."""
    m = (r.rules > 0) & ~r.now
    if m.sum() < 200 or len(np.unique(r.y[m])) < 2:
        return (0.0, 1.0, 0.0)
    X = np.column_stack([np.ones(m.sum()), _logit(r.brain[m]), _logit(r.rules[m])])
    y = r.y[m]
    prior = np.array([0.0, 1.0, 0.0])          # shrink toward "the brain alone"
    reg = l2 * len(y) * np.eye(3)              # ridge on all three: some rule levels are all-1 (separable)

    def loss(w):
        z = X @ w
        return float(np.sum(np.logaddexp(0, z) - y * z) + 0.5 * (w - prior) @ reg @ (w - prior))

    w = prior.copy()
    cur = loss(w)
    for _ in range(iters):
        p = 1 / (1 + np.exp(-np.clip(X @ w, -30, 30)))
        g = X.T @ (p - y) + reg @ (w - prior)
        h = (X * (p * (1 - p))[:, None]).T @ X + reg
        step = np.linalg.solve(h, g)
        t = 1.0
        while t > 1e-4 and loss(w - t * step) > cur:   # damped Newton: never step uphill
            t /= 2
        w = w - t * step
        new = loss(w)
        if cur - new < 1e-9 * max(1.0, abs(cur)):
            break
        cur = new
    return tuple(round(float(v), 4) for v in w)


# rules alone, brain alone, the original 50/50 probability mix, the fixed log-odds 0.5 mix #51 shipped
# (brain alone without rule evidence), and the fitted per-league log-odds blend.
METHODS = ("rules", "brain", "legacy_blend", "logodds_05", "fitted_blend")


def predict(method: str, r: Rows, coef=None) -> np.ndarray:
    if method == "rules":
        return r.rules
    if method == "brain":
        return apply((0.0, 1.0, 0.0), r)
    if method == "legacy_blend":
        return apply(None, r)
    if method == "logodds_05":
        return apply((0.0, 0.5, 0.5), r)
    return apply(coef, r)


def grade(r: Rows, nights: list[str], folds: int = FOLDS) -> dict:
    """Held-out ECE / Brier / AUC per method over forward-chaining folds of `nights`, plus the
    coefficients fitted on all of them (what ships)."""
    keep = np.isin(r.night, nights)
    r = r.take(keep)
    per = {m: [] for m in METHODS}
    fold_rows = []
    for fit_n, check_n in splits.folds(nights, folds):
        tr, te = r.take(np.isin(r.night, fit_n)), r.take(np.isin(r.night, check_n))
        if not len(te):
            continue
        coef = fit(tr)
        fold_rows.append({"fit": [fit_n[0], fit_n[-1]], "check": [check_n[0], check_n[-1]], "rows": len(te),
                          "coef": coef})
        for m in METHODS:
            rep = brainfit.binary_report(te.y, predict(m, te, coef))
            per[m].append(rep)
            fold_rows[-1][m] = rep["ece"]
    out = {"nights": len(nights), "rows": len(r), "first": nights[0] if nights else None,
           "last": nights[-1] if nights else None, "folds": fold_rows, "coef": fit(r)}
    for m, reps in per.items():
        if reps:
            out[m] = {"ece_mean": round(float(np.mean([x["ece"] for x in reps])), 4),
                      "ece_worst": round(float(np.max([x["ece"] for x in reps])), 4),
                      "brier_mean": round(float(np.mean([x["brier"] for x in reps])), 4),
                      "auc_mean": round(float(np.mean([x["auc"] for x in reps])), 4)}
    # Ship the fitted blend only where it beats the brain alone on held-out nights (mean AND worst fold).
    fb, br = out.get("fitted_blend"), out.get("brain")
    wins = fb and br and fb["ece_mean"] <= br["ece_mean"] and fb["ece_worst"] <= br["ece_worst"]
    out["ship"] = dict(zip("abc", out["coef"] if wins else (0.0, 1.0, 0.0)))
    return out
