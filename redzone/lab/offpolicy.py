"""[EXP-005] Off-policy grading: how a NEW decider would have done on nights the live one already ran,
from the live decisions' logged option sets and propensities (#186, decider.log_options).

Serves: North Star "% of hindsight-oracle value achieved" (graded off-policy) and "Right game agreement".
Owner: lane 4 (grading tools). Grading only: nothing here decides anything live.

Each logged decision is a Logged row: the options it could have taken (each with the probability p the
live policy gave it; p = 1.0 on the chosen one when nothing was randomized), which one it took, the
reward that followed, and the night. Given the new policy's probabilities over the same options and a
reward model q(option) (the direct method):

    DR = sum_o pi_new(o) q(o)  +  pi_new(a) / p(a) * (r - q(a))       a = the option actually taken

The first term is the model's guess, the second corrects it with what really happened, weighted by how
much more (or less) often the new policy would take the logged action. Unbiased when either the
propensities or q is right. With no exploration (p(a) = 1 and the new policy's pick differs) the
correction is 0 and DR is the direct method; so the estimate can only be as good as q there.

    est = estimate(rows, pi_new, q)              # point, 95 % night-bootstrap CI, per-night values
    est.dr, est.lo, est.hi, est.ips, est.dm
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable, Sequence

Option = dict           # {"a": action, "g": game id, "p": propensity, ...facts}, as #186 logs them
Policy = Callable[["Logged"], Sequence[float]]       # new policy: a probability per option, same order
Model = Callable[["Logged", int], float]             # reward model: q(row, option index)

W_MAX = 20.0            # clip importance weights (a 5 % explore rate gives at most ~20)
BOOT_DRAWS = 2000


@dataclass(frozen=True)
class Logged:
    night: str
    options: tuple                 # tuple of Option dicts
    taken: int                     # index of the option the live policy took
    reward: float                  # what followed (the grader's value for this decision's window)
    context: dict = field(default_factory=dict)

    @property
    def p(self) -> float:
        return float(self.options[self.taken].get("p", 1.0))


@dataclass(frozen=True)
class Estimate:
    dr: float
    lo: float
    hi: float
    ips: float
    dm: float
    n: int
    nights: dict                   # night -> mean DR value that night
    clipped: int                   # rows whose weight hit W_MAX


def _norm(ps: Sequence[float]) -> list[float]:
    ps = [max(0.0, float(x)) for x in ps]
    s = sum(ps)
    return [x / s for x in ps] if s > 0 else [1.0 / len(ps)] * len(ps)


def row_terms(row: Logged, pi_new: Policy, q: Model, w_max: float = W_MAX) -> tuple[float, float, float, bool]:
    """(dr, ips, dm, clipped) for one logged decision."""
    pi = _norm(pi_new(row))
    if len(pi) != len(row.options):
        raise ValueError(f"policy gave {len(pi)} probabilities for {len(row.options)} options")
    qs = [float(q(row, i)) for i in range(len(row.options))]
    dm = sum(p * v for p, v in zip(pi, qs))
    p_log = row.p
    if p_log <= 0:
        raise ValueError("the taken option was logged with propensity 0")
    w = pi[row.taken] / p_log
    clipped = w > w_max
    w = min(w, w_max)
    return dm + w * (row.reward - qs[row.taken]), w * row.reward, dm, clipped


def night_mean_ci(by_night: dict[str, list[float]], draws: int = BOOT_DRAWS, seed: int = 7) -> tuple[float, float, float]:
    """Mean over every row and its 95 % cluster bootstrap over nights (nights resampled whole)."""
    nights = [v for v in by_night.values() if v]
    tot = sum(len(v) for v in nights)
    if not tot:
        return float("nan"), float("nan"), float("nan")
    point = sum(sum(v) for v in nights) / tot
    if len(nights) == 1:
        return point, float("nan"), float("nan")
    rng = random.Random(seed)
    means = []
    for _ in range(draws):
        pick = [nights[rng.randrange(len(nights))] for _ in nights]
        means.append(sum(sum(v) for v in pick) / sum(len(v) for v in pick))
    means.sort()
    return point, means[int(0.025 * draws)], means[min(draws - 1, int(0.975 * draws))]


def estimate(rows: Sequence[Logged], pi_new: Policy, q: Model, w_max: float = W_MAX,
             draws: int = BOOT_DRAWS, seed: int = 7) -> Estimate:
    by: dict[str, list[float]] = {}
    ips = dm = 0.0
    clipped = 0
    for r in rows:
        d, i, m, c = row_terms(r, pi_new, q, w_max)
        by.setdefault(r.night, []).append(d)
        ips += i
        dm += m
        clipped += c
    point, lo, hi = night_mean_ci(by, draws, seed)
    n = len(rows)
    return Estimate(point, lo, hi, ips / n if n else float("nan"), dm / n if n else float("nan"), n,
                    {k: sum(v) / len(v) for k, v in by.items()}, clipped)


def from_decision_log(rec: dict, reward: float, night: str) -> Logged | None:
    """One #186 decision record ({"options": [{"a", "g", "p", ...}], "action", "target", ...}) -> Logged.
    None when it logged no option set (decider.log_options off) or the taken option isn't in it."""
    opts = tuple(rec.get("options") or ())
    if not opts:
        return None
    for i, o in enumerate(opts):
        if o.get("g") == rec.get("target") and o.get("a") == rec.get("action"):
            return Logged(night, opts, i, float(reward), {k: v for k, v in rec.items() if k != "options"})
    return None
