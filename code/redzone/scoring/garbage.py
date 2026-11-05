"""Garbage time: the game is decided, so it stops competing for your TV — even your own team's.

Enters when the margin is out of reach for the time left; leaves (with hysteresis) only when
it's genuinely close again, at which point it's a COMEBACK and gets a temporary boost.

What still matters in garbage time (handled elsewhere): a bet that's still live (backdoor
cover, total), and your fantasy players scoring. Those boosts are kept; the game's own
excitement and your-team boost are not.

Thresholds (margin -> time left), loosely after analytics-site definitions (e.g. Cleaning the
Glass for NBA) but made stricter where we have no lineup data:
  NFL  2nd half >= 25 · Q4 <= 8:00 >= 17 · Q4 <= 2:00 >= 12 · Q4 <= 0:45 >= 9 · or win prob >= 99% in Q4
  NBA  Q3 >= 30 · Q4 <= 12:00 >= 25 · <= 9:00 >= 20 · <= 6:00 >= 15 · <= 2:00 >= 10 · <= 0:35 >= 7
  NHL  P2 >= 5 · P3 >= 4 · P3 <= 10:00 >= 3 · P3 <= 1:00 >= 2 with the trailing goalie still in
  MLB  5th+ >= 8 · 7th+ >= 6 · 9th >= 5
Never garbage: overtime/extra innings, or anything before these windows.
"""
from __future__ import annotations

from redzone.models import ByLeague, GameState, League, Phase, pro_period

# (min period, max clock seconds left in that period or None, margin)
_RULES = ByLeague({
    League.NFL: [(3, None, 25), (4, 480, 17), (4, 120, 12), (4, 45, 9)],
    League.NBA: [(3, None, 30), (4, 720, 25), (4, 540, 20), (4, 360, 15), (4, 120, 10), (4, 35, 7)],
    League.NHL: [(2, None, 5), (3, None, 4), (3, 600, 3)],
    League.MLB: [(5, None, 8), (7, None, 6), (9, None, 5)],
})
# How much closer it has to get before it's live again (avoids flickering at the line).
_HYSTERESIS = ByLeague({League.NFL: 8, League.NBA: 6, League.NHL: 1, League.MLB: 2})
_REGULATION = ByLeague({League.NFL: 4, League.NBA: 4, League.NHL: 3, League.MLB: 9})


def threshold(g: GameState) -> int | None:
    """Smallest margin that counts as garbage time right now (None = can't be garbage yet)."""
    per = pro_period(g.league, g.period, g.clock_s)  # men's college halves read as NBA quarters
    if per > _REGULATION[g.league] and g.league != League.MLB:
        return None  # overtime is never garbage
    best = None
    for min_per, max_clock, margin in _RULES[g.league]:
        if per < min_per:
            continue
        if max_clock is not None and per == min_per and (g.clock_s is None or g.clock_s > max_clock):
            continue
        if max_clock is not None and per > min_per and g.league != League.MLB:
            continue  # clock-gated rules apply only within their period
        best = margin if best is None else min(best, margin)
    if g.league == League.NHL and per == 3 and g.clock_s is not None and g.clock_s <= 60 \
            and not g.situation.empty_net:
        best = 2 if best is None else min(best, 2)
    return best


def is_garbage(g: GameState, was: bool = False) -> bool:
    if g.phase != Phase.LIVE or g.score_diff is None:
        return False
    d = abs(g.score_diff)
    thr = threshold(g)
    if g.league.base == League.NFL and (g.period or 0) == 4 and g.home_win_prob is not None:
        if max(g.home_win_prob, 1 - g.home_win_prob) >= 0.99 and d >= 9:
            return True
    if thr is None:
        return False
    if was:
        # Thresholds shrink as the clock runs down, so the "it's close again" band must too:
        # a 5-point NBA game with 1:30 left is crunch time even if it was a blowout earlier.
        band = min(_HYSTERESIS[g.league], max(1, thr // 4))
        return d > thr - band
    return d >= thr
