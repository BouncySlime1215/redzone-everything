"""Which nights the brain may train on (Nick's no-overfitting rule, Nov 3 2026).

The held-out nights are distill's time split of eval-data-v1 (agent/distill/split.py `plan()`:
train oldest, then valid, then test = "dev-test", then locked = the newest >= 25% of nights, never
looked at during development). The brain trains ONLY on nights older than the first non-train
night of its league, from any source: our archive, or public play-by-play. So no brain model ever
saw a valid, dev-test or locked night, nor any game played on or after one.

FIRST_HELD is that first non-train night per league, computed from eval-data-v1's nights with
plan(); tests/test_leakage.py recomputes it from the archive when the data is present, and checks
every shipped brain model's recorded training nights against it.
"""
from __future__ import annotations

# plan() on eval-data-v1 (Nov 3 2026): NFL train 2025-09-04..11-10, valid from 2025-11-13;
# NBA train 10-20..10-26, valid 10-27, dev-test 10-28, locked 10-29..11-01;
# NHL train 10-19..10-24, valid 10-25..26, dev-test 10-27..28, locked 10-29..11-01.
FIRST_HELD = {"nfl": "20251113", "nba": "20261027", "nhl": "20261025"}
# The locked nights start here: never graded during development, only for the release PR.
LOCKED_FROM = {"nfl": "20251208", "nba": "20261029", "nhl": "20261029"}
# Leagues without eval-data-v1 nights: nothing there is held out yet, but a model must never
# have seen a game after the pinned window's newest night.
WINDOW_END = "20261101"

# The last calendar night of a public season label (sportsdataverse / nflverse conventions):
# NFL/CFB name the season by its start year and end in February; NBA/NHL/CBB/WCBB by the end year
# (ending by late June); MLB by its year.
_SEASON_END = {"nfl": lambda s: f"{s + 1}0228", "cfb": lambda s: f"{s + 1}0131",
               "nba": lambda s: f"{s}0630", "nhl": lambda s: f"{s}0630", "cbb": lambda s: f"{s}0430",
               "wcbb": lambda s: f"{s}0430", "mlb": lambda s: f"{s}1130"}


def cutoff(league: str) -> str:
    """Training nights must be strictly before this night."""
    return FIRST_HELD.get(league, WINDOW_END)


def allowed(league: str, night) -> bool:
    return str(night) < cutoff(league)


def dev_test(league: str, night) -> bool:
    """A held-out night we may grade on during development (valid or dev-test, never locked)."""
    n = str(night)
    return cutoff(league) <= n < LOCKED_FROM.get(league, "99999999")


def locked(league: str, night) -> bool:
    return str(night) >= LOCKED_FROM.get(league, "99999999")


def season_allowed(league: str, season: int) -> bool:
    """A whole public season may train only if its last night is before the cutoff."""
    end = _SEASON_END.get(league)
    return end is None or end(int(season)) < cutoff(league)


def first_held_from(nights_by_league: dict[str, list[str]], plan) -> dict[str, str]:
    """FIRST_HELD recomputed with distill's plan() (for the CI check)."""
    out = {}
    for lg, m in plan(nights_by_league).items():
        held = sorted(n for n, s in m.items() if s != "train")
        if held:
            out[lg] = held[0]
    return out
