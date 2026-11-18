"""Walk-forward roles for the corpus: train, valid and holdout by whole season (or whole night when there are too few seasons), per league.

Why: B-02 corpus rule 5 (tracks/B-foundation-model.md): "split by whole game and by season, walk-forward". A model trained on a season must
never be scored on an older one, and a night is never split across roles (the rule of redzone/lab/splits.py, whose `folds` and `leaks`
this module reuses for the forward-chaining folds and the leak check).

A league's split is two cut nights on a time axis: nights before `valid_from` are train, from there to `holdout_from` valid, from
`holdout_from` on holdout. With three or more seasons the cuts are season starts (the newest season or seasons are holdout; the
fraction picks how many); with fewer, the cuts are nights. `gap_days` embargoes the nights just before each cut (their games get no role),
for models whose inputs span days. Only nights of clear games count: tuning-window games sit outside the axis in `tuning` shards.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Iterable, Mapping

from redzone.lab import splits
from redzone.lab.corpus.records import season_of

CORPUS_VALID_FRAC = 0.10
CORPUS_HOLDOUT_FRAC = 0.10
CORPUS_MIN_SEASONS = 3
CORPUS_MIN_NIGHTS = 3
CORPUS_FOLDS = 5


@dataclass(frozen=True)
class LeagueSplit:
    league: str
    mode: str                      # season, night or all-train (too little data to hold anything out)
    valid_from: str | None         # first night of the valid role
    holdout_from: str | None       # first night of the holdout role
    gap_days: int
    nights: int                    # distinct clear nights the cuts were computed from

    def role_of(self, night: str) -> str | None:
        """'train', 'valid' or 'holdout'; None for a night inside an embargo."""
        if self.holdout_from is not None and night >= self.holdout_from:
            return "holdout"
        if self.holdout_from is not None and self.gap_days and night >= _minus(self.holdout_from, self.gap_days):
            return None
        if self.valid_from is not None and night >= self.valid_from:
            return "valid"
        if self.valid_from is not None and self.gap_days and night >= _minus(self.valid_from, self.gap_days):
            return None
        return "train"

    def to_json(self) -> dict:
        return {"mode": self.mode, "valid_from": self.valid_from, "holdout_from": self.holdout_from, "gap_days": self.gap_days,
                "nights": self.nights}


def _minus(night: str, days: int) -> str:
    return (dt.datetime.strptime(night, "%Y%m%d") - dt.timedelta(days=days)).strftime("%Y%m%d")


def plan_league(league: str, nights: Iterable[str], valid_frac: float = CORPUS_VALID_FRAC, holdout_frac: float = CORPUS_HOLDOUT_FRAC,
                gap_days: int = 0) -> LeagueSplit:
    ns = sorted(set(nights))
    if len(ns) < CORPUS_MIN_NIGHTS:
        return LeagueSplit(league, "all-train", None, None, gap_days, len(ns))
    by_season: dict[int, str] = {}
    for n in ns:
        by_season.setdefault(season_of(league, n), n)         # first night of each season label
    if len(by_season) >= CORPUS_MIN_SEASONS:
        firsts = [by_season[s] for s in sorted(by_season)]
        n_h = max(1, round(len(firsts) * holdout_frac))
        n_v = max(1, round(len(firsts) * valid_frac))
        n_h = min(n_h, len(firsts) - 2)
        n_v = min(n_v, len(firsts) - 1 - n_h)
        return LeagueSplit(league, "season", firsts[len(firsts) - n_h - n_v], firsts[len(firsts) - n_h], gap_days, len(ns))
    n_h = min(max(1, round(len(ns) * holdout_frac)), len(ns) - 2)
    n_v = min(max(1, round(len(ns) * valid_frac)), len(ns) - 1 - n_h)
    return LeagueSplit(league, "night", ns[len(ns) - n_h - n_v], ns[len(ns) - n_h], gap_days, len(ns))


def plan(nights_by_league: Mapping[str, Iterable[str]], valid_frac: float = CORPUS_VALID_FRAC, holdout_frac: float = CORPUS_HOLDOUT_FRAC,
         gap_days: int = 0) -> dict[str, LeagueSplit]:
    return {lg: plan_league(lg, ns, valid_frac, holdout_frac, gap_days) for lg, ns in sorted(nights_by_league.items())}


def fold_ranges(nights: Iterable[str], k: int = CORPUS_FOLDS) -> list[dict[str, str]]:
    """Forward-chaining folds over the train and valid nights: fold i fits on everything up to a cut and checks on the next block."""
    return [{"fit_first": fit[0], "fit_last": fit[-1], "check_first": chk[0], "check_last": chk[-1]}
            for fit, chk in splits.folds(list(nights), k)]


def split_problems(by_role: Mapping[str, Iterable[str]], games_by_night: Mapping[str, Iterable[str]] | None = None) -> list[str]:
    """Leaks between the roles of one league: a night in two roles, a valid or holdout night older than a train night, a game id in two
    roles. [] when clean. (redzone/lab/splits.leaks, with holdout standing in for its `test`.)"""
    ns = {r: tuple(sorted(set(by_role.get(r, ())))) for r in ("train", "valid", "holdout")}
    return splits.leaks(splits.Split(ns["train"], ns["valid"], ns["holdout"]), games_by_night)
