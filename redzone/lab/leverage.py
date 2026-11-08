"""Leverage floor for a mid-play LEAVE (Nick, Nov 4 3:15 PM ET).

Joining mid-play is never allowed. Leaving mid-play is allowed at low leverage (right after a score, in
transition, Q1-Q3, a game that isn't close). It is never allowed at a high-leverage moment: close and late,
or crunch. This module is the rule floor the graders use to split the North Star line. The learned
leverage score (lane 2, self-taught from the oracle) may only make it stricter.
"""
from __future__ import annotations

from redzone.models import PERIOD_S, League, pro_period
from redzone.sources import espn

# (final period, seconds left in it, max margin) that count as close + late; overtime always counts
CLOSE_LATE = {League.NFL: (4, 300, 8), League.NBA: (4, 360, 6), League.NHL: (3, 300, 1)}


def _left_s(league: League, p: dict) -> float | None:
    c = espn._clock_s(espn._get(p, "clock", "displayValue"))
    if c is not None and league.base == League.NHL:   # ESPN's NHL clock counts up
        per = espn._int(espn._get(p, "period", "number")) or 1
        c = max(0.0, (300 if per >= 4 else PERIOD_S[league]) - c)
    return c


def high_leverage(league: League, p: dict | None) -> bool:
    """Is the game showing this play at a high-leverage moment (no mid-play leave allowed)?"""
    if not p:
        return False
    per = espn._int(espn._get(p, "period", "number")) or 0
    hs, as_ = espn._int(p.get("homeScore")), espn._int(p.get("awayScore"))
    if hs is None or as_ is None:
        return False
    d = abs(hs - as_)
    base = league.base                                 # [grader v47] college reads its pro league's rule
    if base == League.MLB:
        return per >= 7 and d <= 2
    rule = CLOSE_LATE.get(base)
    if rule is None:
        return False
    last, secs, margin = rule
    left = _left_s(league, p)
    q = pro_period(league, per, left)                  # college halves read as NBA quarters
    if q > last:
        return True                                   # overtime
    return q == last and left is not None and left <= secs and d <= margin
