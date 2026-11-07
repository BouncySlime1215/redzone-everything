"""[EXP-032] Red team: search the backtest's stress knobs for nights that break the brain.

The fixed stress grid (tests/test_midplay.py, redzone/lab/replaycheck.py) checks a dozen hand-picked
mixes of feed lag, drops, late plays and delay error. This module searches the same knobs on purpose,
within a stated evaluation budget, for a mix that makes the app land mid-play (entries_mid_play > 0)
or leave mid-play at a high-leverage moment (redzone/lab/leverage.py). Each reproducible find is
written up as a candidate for tests/golden/ (docs/grading.md rule 7).

Lab only and OFF by default: nothing in the live app imports it, and scripts/redteam.py runs only
with --enable. It reads dev nights (train / valid); it refuses a locked (test) night.
Pre-registration: docs/prereg/exp-032.md.
"""
from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

ENABLED = False   # the flag: scripts/redteam.py needs --enable

# (name, low, high, base). base = no stress, the clean scorecard's value. true_delay is the stream's
# real delay; the app believes 35 s (backtest.run's `delay`).
AXES: tuple[tuple[str, float, float, float], ...] = (
    ("true_delay", 20.0, 55.0, 35.0),
    ("sigma", 1.0, 8.0, 3.0),
    ("lag_lo", 0.0, 10.0, 0.0),
    ("lag_span", 0.0, 40.0, 0.0),
    ("drop", 0.0, 0.3, 0.0),
    ("late_plays", 0.0, 0.4, 0.0),
    ("no_wallclock", 0.0, 1.0, 0.0),
    ("tune_jitter", 0.0, 8.0, 0.0),
)
SEEDS = 10_000


@dataclass(frozen=True)
class Knobs:
    """One stress mix for backtest.run. All fields at their base = the clean scorecard."""
    true_delay: float = 35.0
    sigma: float = 3.0
    lag_lo: float = 0.0
    lag_span: float = 0.0
    drop: float = 0.0
    late_plays: float = 0.0
    no_wallclock: float = 0.0
    tune_jitter: float = 0.0
    seed: int = 0

    def run_kwargs(self) -> dict:
        """The keyword arguments backtest.run takes for this mix."""
        jitter = None
        if self.lag_lo > 0 or self.lag_span >= 0.5:
            jitter = (round(self.lag_lo, 3), round(self.lag_lo + max(self.lag_span, 0.5), 3))
        return dict(true_delay=round(self.true_delay, 3), sigma=round(self.sigma, 3), lag_jitter=jitter,
                    drop=round(self.drop, 4), late_plays=round(self.late_plays, 4),
                    no_wallclock=round(self.no_wallclock, 4), tune_jitter_s=round(self.tune_jitter, 3),
                    seed=int(self.seed))

    def unit(self) -> list[float]:
        """This mix as a point in [0, 1]^d (seed left out)."""
        return [(getattr(self, n) - lo) / (hi - lo) for n, lo, hi, _ in AXES]

    @classmethod
    def from_unit(cls, u: list[float], seed: int) -> "Knobs":
        vals = {n: lo + min(1.0, max(0.0, x)) * (hi - lo) for (n, lo, hi, _), x in zip(AXES, u)}
        return cls(**{k: round(v, 4) for k, v in vals.items()}, seed=int(seed) % SEEDS)

    @classmethod
    def base(cls, seed: int = 0) -> "Knobs":
        return cls(**{n: b for n, _, _, b in AXES}, seed=seed)


_COMBINED = dict(lag_lo=3.0, lag_span=22.0, drop=0.1, late_plays=0.15, no_wallclock=0.5)

# The fixed grid the red team must beat: the mixes tests/test_midplay.py and replaycheck's
# combined_faults already run (seeds as there).
FIXED_GRID: dict[str, Knobs] = {
    "baseline": Knobs(),
    "delay_ahead": Knobs(true_delay=29.0),
    "delay_behind": Knobs(true_delay=41.0),
    "sigma5_delay25": Knobs(sigma=5.0, true_delay=25.0),
    "lag_jitter": Knobs(lag_lo=3.0, lag_span=22.0, seed=1),
    "drop": Knobs(drop=0.1, seed=2),
    "late_plays": Knobs(late_plays=0.15),
    "no_wallclock": Knobs(no_wallclock=0.5),
    "combined_ahead": Knobs(true_delay=29.0, seed=1, **_COMBINED),
    "combined_faults_s0": Knobs(true_delay=41.0, sigma=5.0, seed=0, **_COMBINED),
    "combined_faults_s1": Knobs(true_delay=41.0, sigma=5.0, seed=1, **_COMBINED),
    "combined_faults_s2": Knobs(true_delay=41.0, sigma=5.0, seed=2, **_COMBINED),
}


@dataclass(frozen=True)
class Outcome:
    """What one backtest of one night under one mix scored (the Rule #1 lines)."""
    entries_mid_play: int = 0
    cuts_high: int = 0            # mid-play leaves at a high-leverage moment
    cuts: int = 0                 # all mid-play leaves (secondary)
    plays_missed_entry: int = 0
    entries: int = 0
    dead_air_pct: float = 0.0

    @property
    def breaks(self) -> bool:
        """The pre-registered failure: a mid-play entry or a high-leverage mid-play leave."""
        return self.entries_mid_play > 0 or self.cuts_high > 0

    @property
    def badness(self) -> float:
        """The search's guide: failures first, then near misses (any leave mid-play, plays missed)."""
        return (1000.0 * (self.entries_mid_play + self.cuts_high) + 10.0 * self.cuts
                + self.plays_missed_entry + self.dead_air_pct / 100.0)


Evaluator = Callable[[Knobs], Outcome]


@dataclass
class Trial:
    knobs: Knobs
    outcome: Outcome
    phase: str


@dataclass
class SearchResult:
    trials: list[Trial] = field(default_factory=list)
    budget: int = 0

    @property
    def first_hit(self) -> int | None:
        """1-based evaluation index of the first breaking mix, None when none broke."""
        return next((i + 1 for i, t in enumerate(self.trials) if t.outcome.breaks), None)

    @property
    def hits(self) -> list[Trial]:
        return [t for t in self.trials if t.outcome.breaks]


def _mutate(u: list[float], rng: random.Random, step: float) -> list[float]:
    return [min(1.0, max(0.0, x + rng.gauss(0.0, step))) for x in u]


def search(evaluate: Evaluator, budget: int, seed: int = 0, step: float = 0.12) -> SearchResult:
    """Spend exactly `budget` evaluations hunting for a breaking mix (deterministic given `seed`).

    1. edges: the base mix, each axis alone at its max, then every axis at max (d + 2 evals);
    2. random: uniform points until 60 % of the budget is spent;
    3. local: Gaussian steps (sd `step` in unit space) around the three worst mixes so far, a new
       seed half of the time.
    """
    rng = random.Random(seed * 1_000_003 + 11)
    out = SearchResult(budget=budget)
    d = len(AXES)
    base_u = Knobs.base().unit()

    def spend(k: Knobs, phase: str) -> None:
        if len(out.trials) < budget:
            out.trials.append(Trial(k, evaluate(k), phase))

    s0 = rng.randrange(SEEDS)
    spend(Knobs.base(s0), "edges")
    for i in range(d):
        u = list(base_u)
        u[i] = 1.0
        spend(Knobs.from_unit(u, s0), "edges")
    spend(Knobs.from_unit([1.0] * d, s0), "edges")
    while len(out.trials) < int(0.6 * budget):
        spend(Knobs.from_unit([rng.random() for _ in range(d)], rng.randrange(SEEDS)), "random")
    while len(out.trials) < budget:
        worst = _worst(out.trials, 3)
        parent = worst[rng.randrange(len(worst))].knobs
        s = rng.randrange(SEEDS) if rng.random() < 0.5 else parent.seed
        spend(Knobs.from_unit(_mutate(parent.unit(), rng, step), s), "local")
    return out


def _worst(trials: list[Trial], k: int) -> list[Trial]:
    """The k trials with the highest badness (earliest first on ties)."""
    order = list(range(len(trials)))
    order.sort(key=lambda i: (-trials[i].outcome.badness, i))
    return [trials[i] for i in order[:k]]


def reproduce(evaluate: Evaluator, k: Knobs, reruns: int = 3) -> bool:
    """Same Rule #1 result on `reruns` fresh reruns of the exact mix (seed included)."""
    first = evaluate(k)
    return first.breaks and all(evaluate(k) == first for _ in range(reruns - 1))


def seed_robustness(evaluate: Evaluator, k: Knobs, n: int = 3) -> float:
    """Fraction of n neighbouring seeds (same mix) that also break: is it the mix or one draw?"""
    return sum(evaluate(Knobs(**{**asdict(k), "seed": (k.seed + j) % SEEDS})).breaks
               for j in range(1, n + 1)) / n


def grid(evaluate: Evaluator) -> dict[str, Outcome]:
    return {name: evaluate(k) for name, k in FIXED_GRID.items()}


def novel(o: Outcome, grid_out: dict[str, Outcome]) -> bool:
    """Does this find fail a Rule #1 line the fixed grid left clean on the same night?"""
    g_entry = any(g.entries_mid_play > 0 for g in grid_out.values())
    g_high = any(g.cuts_high > 0 for g in grid_out.values())
    return (o.entries_mid_play > 0 and not g_entry) or (o.cuts_high > 0 and not g_high)


def distinct_hits(hits: list[Trial], k: int) -> list[Trial]:
    """Up to k breaking mixes, worst first, at least 0.25 apart in unit space."""
    keep: list[Trial] = []
    for t in _worst(hits, len(hits)):
        if all(sum((a - b) ** 2 for a, b in zip(t.knobs.unit(), o.knobs.unit())) ** 0.5 >= 0.25 for o in keep):
            keep.append(t)
        if len(keep) >= k:
            break
    return keep


def hunt_night(evaluate: Evaluator, budget: int, seed: int = 0, reruns: int = 3, max_repro: int = 3) -> dict:
    """One night: the fixed grid, the search, then reproduce up to `max_repro` distinct finds."""
    calls = [0]

    def counted(k: Knobs) -> Outcome:
        calls[0] += 1
        return evaluate(k)

    g = grid(counted)
    res = search(counted, budget, seed)
    findings = []
    for t in distinct_hits(res.hits, max_repro):
        ok = reproduce(counted, t.knobs, reruns)
        findings.append({"knobs": asdict(t.knobs), "run_kwargs": t.knobs.run_kwargs(),
                         "outcome": asdict(t.outcome), "phase": t.phase, "reproducible": ok,
                         "novel_vs_grid": novel(t.outcome, g),
                         "seed_robustness": seed_robustness(counted, t.knobs) if ok else None})
    return {"grid": {n: asdict(o) for n, o in g.items()},
            "grid_breaks": sorted(n for n, o in g.items() if o.breaks),
            "search_budget": budget, "search_first_hit": res.first_hit, "search_hits": len(res.hits),
            "findings": findings, "evals_spent": calls[0]}


def golden_candidate(league: str, night: str, f: dict) -> dict:
    """A tests/golden/ candidate write-up (grading rule 7): the night, the mix, what broke, the pin."""
    o = f["outcome"]
    what = [x for x, n in (("mid-play entry", o["entries_mid_play"]), ("high-leverage leave", o["cuts_high"])) if n]
    return {"league": league, "night": night, "run_kwargs": f["run_kwargs"], "breaks": what,
            "pinned": {"entries_mid_play": o["entries_mid_play"], "cuts_high": o["cuts_high"]},
            "must_reach": {"entries_mid_play": 0, "cuts_high": 0},
            "reproducible": f["reproducible"], "novel_vs_grid": f["novel_vs_grid"],
            "seed_robustness": f["seed_robustness"]}


def bootstrap_ci(values: list[float], n: int = 2000, seed: int = 0, alpha: float = 0.05) -> tuple[float, float]:
    """Percentile bootstrap of the mean over nights (each value = one night)."""
    if not values:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    m = len(values)
    means = [sum(values[rng.randrange(m)] for _ in range(m)) / m for _ in range(n)]
    means.sort()
    return (means[int(alpha / 2 * n)], means[min(n - 1, int((1 - alpha / 2) * n))])


MIN_NIGHTS = 10   # below this a rate over nights is PENDING (grading rule 4)


# ---- the real evaluator: one archived night through backtest.run + sportsgrade -----------------

def dev_nights(arch: Path, which: str, end: str | None) -> list[str]:
    """The nights of a dev split ('train', 'valid' or 'fit'). A locked (test) night is refused."""
    from redzone.lab import splits
    if which not in ("train", "valid", "fit"):
        raise ValueError(f"red team runs on dev nights only (train/valid/fit), not {which!r}")
    s = splits.split_nights(splits.archive_nights(arch, end))
    picked = {"train": s.train, "valid": s.valid, "fit": s.train + s.valid}[which]
    assert not set(picked) & set(s.test), "a locked night leaked into the dev split"
    return list(picked)


def locked_nights(arch: Path, end: str | None) -> set[str]:
    from redzone.lab import splits
    return set(splits.split_nights(splits.archive_nights(arch, end)).test)


def load_night(arch: Path, league, night: str, channels: Path | None = None):
    """(replays, targets) for one archived night, the way scripts/eval_sports.py loads it."""
    from redzone.channels import ChannelMap
    from redzone.lab.backtest import Replay
    from redzone.sources import espn
    evs = json.loads((arch / f"scoreboard_{night}.json").read_text()).get("events", [])
    reps, targets = [], {}
    cmap = ChannelMap(channels) if channels is not None and channels.exists() else None
    for e in evs:
        f = arch / f"summary_{e['id']}.json"
        r = Replay.load(league, f) if f.exists() else None
        if not r:
            continue
        reps.append(r)
        g = espn._parse_event(league, e) if cmap else None
        w = cmap.where(g) if g else None
        app = (w.target or "youtubetv:x").split(":")[0] if w else "youtubetv"
        targets[r.gid] = f"{app}:{r.gid}"
    return reps, targets


def backtest_evaluator(reps, targets, cfg, mine: set[str]) -> Evaluator:
    """Grade a mix against what the screen REALLY showed (true delay), as scripts/eval_sports.py does."""
    from redzone.lab import sportsgrade
    from redzone.lab.backtest import run

    def evaluate(k: Knobs) -> Outcome:
        rep = run(reps, cfg, mine, targets=targets, **k.run_kwargs())
        card = sportsgrade.grade(reps, rep.switches, mine, rep.lag_true, 5.0, rep.t0, rep.t1, rep.replayed)
        cs = card.cut.summary()
        return Outcome(entries_mid_play=int(cs["entries_mid_play"]), cuts_high=len(card.cuts_high),
                       cuts=len(card.cuts), plays_missed_entry=int(cs["plays_missed_entry"]),
                       entries=int(cs["entries"]),
                       dead_air_pct=round(100 * card.dead / max(1.0, card.watched), 2))
    return evaluate
