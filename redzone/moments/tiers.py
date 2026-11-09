"""The one per-league tier list: which kind of moment is must-show (1), setup (2) or highlight (3).

Why: three graders and the phone each kept their own list of "big" plays (BIG_KINDS in web/phone_v2 and
reasoner/brief, Timeline.events[].big). The show engine needs one list that detectors fill and the queue
reads (build plan WP-07). The graders stay as they are; GRADER_ALIASES maps each of their words onto this
vocabulary and a test keeps the mapping honest.

    tier_of("nfl", "td")          # 1
    must_show("cfb", "gain10")    # True: Nick's football rule makes every major play must-show
    kinds("nhl", 1)               # ("goal", "penalty_shot")
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

TIER_MUST, TIER_SETUP, TIER_HIGHLIGHT = 1, 2, 3
_PATH = Path(__file__).with_name("tiers.yaml")

# Words the frozen graders use (web/phone_v2.BIG_KINDS, reasoner/brief.BIG_KINDS) -> (league family, our kind).
# None = a grader word with no detector here yet (said plainly, not silently dropped).
GRADER_ALIASES: dict[str, tuple[str, str] | None] = {
    "td": ("football", "td"), "fg": ("football", "fg"), "turnover": ("football", "turnover"),
    "interception": ("football", "turnover"), "fumble": ("football", "turnover"), "pick6": ("football", "td"),
    "safety": ("football", "turnover"),
    "goal": ("hockey", "goal"), "penalty_shot": ("hockey", "penalty_shot"),
    "hr": ("baseball", "hr"), "grand_slam": ("baseball", "hr"),
    "dunk": ("hoops", "dunk"), "three": ("hoops", "three"),
    "and1": None, "buzzer": None,
}


@lru_cache(maxsize=1)
def _load() -> dict:
    return yaml.safe_load(_PATH.read_text())


def family(league: str) -> str:
    """Tier-list family of a league value ('nfl' -> 'football'). KeyError for a league we do not show."""
    return _load()["leagues"][str(league).lower()]


def kinds(league: str, tier: int) -> tuple[str, ...]:
    return tuple(_load()[family(league)][f"tier{tier}"])


def tier_of(league: str, kind: str) -> int | None:
    """1, 2 or 3 for a kind in this league's list; None when the league's list does not have it."""
    for t in (TIER_MUST, TIER_SETUP, TIER_HIGHLIGHT):
        if kind in kinds(league, t):
            return t
    return None


def must_show(league: str, kind: str) -> bool:
    """True when the product must show it live or replay it. Wider than tier 1 for football (Nick's rule)."""
    return kind in _load()[family(league)]["must_show"]
