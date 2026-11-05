"""Time splits for every model and every tuned knob (the user's no-overfitting rule, 2026-11-03).

    train | valid | test          by NIGHT, oldest -> newest, per league
    test  = the newest >= 25 % of nights: LOCKED. Scored only for the release PR, once per release
            candidate (scripts/release_gate.py). Nobody tunes, trains or picks thresholds on it.
    fresh = nights archived after the pin's last night: never seen by anything; reported apart.
    folds = 5 forward-chaining folds over train+valid for every tuning decision: fold k trains on
            blocks 0..k and validates on block k+1. Report the mean AND the worst fold.

Older seasons as dev only (EXP-735, grader v21): a pin may carry `dev_before` {league: YYYYMMDD}. Nights before
that date are train/valid only and never locked; the locked block is then the newest >= 25 % of the nights on or
after it (the current season). A pin without it (eval-data-v1) splits exactly as before. Use `league_split`.

A night is never split across sets, and so a game (all its plays) never is either: every split here
is a set of nights, and a game belongs to the night it started (US Eastern).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

TEST_FRAC = 0.25
VALID_FRAC = 0.15
FOLDS = 5


@dataclass(frozen=True)
class Split:
    train: tuple[str, ...]
    valid: tuple[str, ...]
    test: tuple[str, ...]          # LOCKED

    @property
    def cutoff(self) -> str:
        """The last night anything may learn from (train + valid)."""
        return (self.valid or self.train or ("",))[-1]


def split_nights(nights: Iterable[str], test_frac: float = TEST_FRAC, valid_frac: float = VALID_FRAC,
                 dev_before: str | None = None) -> Split:
    """nights: 'YYYYMMDD' strings. Oldest go to train, the next block to valid, the newest to test.
    dev_before: nights before it can only be train/valid; test is the newest test_frac of the rest."""
    ns = sorted(set(nights))
    n = len(ns)
    if n == 0:
        return Split((), (), ())
    cur = [x for x in ns if dev_before is None or x >= dev_before]
    if not cur:                                  # nothing in the current season yet: all dev, nothing locked
        n_valid = max(1, int(round(n * valid_frac))) if n >= 2 else 0
        return Split(tuple(ns[:n - n_valid]), tuple(ns[n - n_valid:]), ())
    n_test = math.ceil(len(cur) * test_frac - 1e-9)   # at least test_frac of the (current) nights, at least one
    rest = n - n_test
    n_valid = max(1, int(round((n if dev_before is None else len(cur)) * valid_frac))) if rest >= 2 else 0
    n_valid = min(n_valid, max(0, rest - 1)) if dev_before is not None else n_valid
    n_train = rest - n_valid
    return Split(tuple(ns[:n_train]), tuple(ns[n_train:n_train + n_valid]), tuple(ns[n_train + n_valid:]))


def dev_before(league: str) -> str | None:
    """The active pin's dev-only cut for this league (None: plain 25 % newest)."""
    from redzone.lab import evaldata
    lock = evaldata.load_lock(evaldata.LOCK) or {}
    return (lock.get("dev_before") or {}).get(league)


def league_split(league: str, nights: Iterable[str]) -> Split:
    """The split every scorecard, leak check and model uses for this league under the active pin."""
    return split_nights(nights, dev_before=dev_before(league))


def folds(nights: Sequence[str], k: int = FOLDS) -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    """Forward-chaining folds over (train + valid) nights: [(fit nights, check nights)], oldest first.
    Never pass test nights here."""
    ns = sorted(set(nights))
    blocks = k + 1
    if len(ns) < blocks:
        return []
    size = len(ns) / blocks
    edges = [round(i * size) for i in range(blocks + 1)]
    return [(tuple(ns[:edges[i + 1]]), tuple(ns[edges[i + 1]:edges[i + 2]])) for i in range(k)]


def fresh(nights: Iterable[str], pin_end: str) -> tuple[str, ...]:
    """Nights after the pinned window: new test data no model has seen."""
    return tuple(sorted(x for x in set(nights) if x > pin_end))


def leaks(split: Split, games_by_night: Mapping[str, Iterable[str]] | None = None) -> list[str]:
    """Every way the split leaks; [] when clean. Checks: a night in two sets, time order broken
    (a train/valid night after a test night), a game id in two sets."""
    out = []
    sets = {"train": set(split.train), "valid": set(split.valid), "test": set(split.test)}
    for a, b in (("train", "valid"), ("train", "test"), ("valid", "test")):
        both = sets[a] & sets[b]
        if both:
            out.append(f"nights in {a} and {b}: {sorted(both)[:3]}")
    if split.test and (split.train or split.valid) and max(split.train + split.valid) >= min(split.test):
        out.append("time order: a train/valid night is not older than every test night")
    if split.train and split.valid and max(split.train) >= min(split.valid):
        out.append("time order: a train night is not older than every valid night")
    if games_by_night is not None:
        where: dict[str, str] = {}
        for name, ns in sets.items():
            for night in ns:
                for gid in games_by_night.get(night, ()):
                    if where.setdefault(gid, name) != name:
                        out.append(f"game {gid} in {where[gid]} and {name}")
    return out


def causal(feature_times: Iterable[float], decision_t: float, slack_s: float = 0.0) -> list[float]:
    """Feature timestamps later than the decision they feed (future plays leaking in); [] when clean.
    Every model's feature builder asserts this in its tests."""
    return [t for t in feature_times if t is not None and t > decision_t + slack_s]


def archive_nights(league_dir, end: str | None = None) -> dict[str, list[str]]:
    """night (YYYYMMDD of the scoreboard file, US Eastern) -> game ids, from data/archive/<league>/.
    A game listed on two scoreboards belongs to the first (it can never land in two splits)."""
    import json
    from pathlib import Path
    out: dict[str, list[str]] = {}
    seen: set[str] = set()
    for sb in sorted(Path(league_dir).glob("scoreboard_*.json")):
        night = sb.stem.split("_")[-1]
        if end and night > str(end):
            continue
        try:
            evs = json.loads(sb.read_text()).get("events", [])
        except (OSError, ValueError):
            continue
        ids = [str(e.get("id")) for e in evs if e.get("id") and str(e.get("id")) not in seen]
        seen.update(ids)
        if ids:
            out[night] = ids
    return out


SPLITS = ("all", "train", "valid", "test", "fit")   # fit = train + valid (what tuning may read)


def nights_for(league_dir, which: str, end: str | None = None) -> set[str] | None:
    """The nights of one split of an archive (None for 'all'). Scorecards take --split from here."""
    if which == "all":
        return None
    from pathlib import Path
    s = league_split(Path(league_dir).name, archive_nights(league_dir, end))
    return set({"train": s.train, "valid": s.valid, "test": s.test, "fit": s.train + s.valid}[which])
