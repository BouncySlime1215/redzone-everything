"""[EXP-610] A basketball clock-stopped dead ball, judged from play-by-play (wall time, period, game clock, type).

The rule is lane 3's pre-registered clock truth (scripts/clock_truth_grade.py, /mnt/project-files/lane3/exp741):
an arena moment `a` is dead when it is within 10 s after a whistle event (foul, timeout, violation, free throw, end
of period), when the events around it straddle a period change, or when the game clock ran < 0.3 x wall time between
the events around it. The seconds after a made basket or a defensive rebound are NOT dead (the clock runs, the ball
is inbounded live): that is what the dev grader's dead_at calls dead and EXP-608 landed on (truth-live entries).

Brain code: the live flip (engine may_cut on the scrub path) and the simulator's re-check (lab/backtest
HiddenStick.safe_after, EXP-610) both call `clock_stopped`, so the sim models exactly what live does. It needs the
NEXT event too, so it only works where the picture runs behind the feed (the time-shift buffer)."""
from __future__ import annotations

WHISTLE = ("foul", "timeout", "violation", "free throw", "end of")
WHISTLE_LEAGUES = ("nba", "cbb")   # League.base values the clock rule judges


def _label(ev: list[tuple[float, int, float, str]], a: float) -> str:
    prev = [e for e in ev if e[0] <= a]
    nxt = [e for e in ev if e[0] > a]
    if not prev or not nxt:
        return "ambiguous"
    w1, p1, c1, t1 = prev[-1]
    w2, p2, c2, _ = nxt[0]
    if a - w1 <= 10 and any(k in t1 for k in WHISTLE):
        return "dead"
    if p1 != p2:
        return "dead"
    gap, drop = w2 - w1, c1 - c2
    if drop >= 0.8 * gap - 2:
        return "live"
    if drop < 0.3 * gap:
        return "dead"
    return "ambiguous"


def clock_stopped(ev: list[tuple[float, int, float, str]], a: float, band: float = 3.0) -> bool:
    """Every whole second in [a - band, a + band] is a clock-stopped dead ball. `ev`: (wall epoch, period, game clock
    s left, lower-case type text) of the plays known so far, in wall order. No events or no next event: False."""
    return all(_label(ev, a + d) == "dead" for d in range(-int(band), int(band) + 1))


def clock_secs(v) -> float | None:
    """ESPN's clock displayValue ('5:32', '42.1') or a number -> seconds left; None when unknown."""
    if v is None:
        return None
    v = str(v)
    try:
        if ":" in v:
            m, s = v.split(":")
            return int(m) * 60 + float(s)
        return float(v)
    except ValueError:
        return None
