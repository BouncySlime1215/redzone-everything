"""[one brain, step 3] The play-by-play predicates the live app and the grader share (docs/onebrain-migration.md).
Serves: entries_mid_play (dead_at / clean_leave decide where a cut may land and leave), big_plays_live
(is_big), crunch_live_* (is_crunch), and the feed-anchored sync (is_dead_ball).

Before this, Noggin PICK (live) and the engine's break anchor imported them from redzone/lab/
(cutgrade, sportsgrade, oracle, timing, backtest), so the live path ran lab code. They live here now,
moved verbatim; the lab modules import them back under their old names, so the grader and the live
app answer every question with the same function (exact parity, by identity).

    from redzone.grading import dead_at, clean_leave, is_big, is_crunch, is_dead_ball, SwitchLog, lag_of
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from redzone import crunch
from redzone.decider.screen import CONTINUOUS, _is_marker
from redzone.models import REAL_BREAKS, League, Stoppage
from redzone.sources import espn

PLAY_LEN = {League.NFL: 7.0, League.MLB: 6.0, League.NBA: 0.0, League.NHL: 0.0}
RESUME_LEAD = {League.NBA: 26.0, League.NHL: 5.0}   # play can restart this long before the next logged action

# Play types that mean the ball is dead (whistle, timeout, review, period end). Lower-case substrings.
DEAD_WORDS = ("timeout", "foul", "free throw", "stoppage", "penalty", "end of", "end period", "review",
              "challenge", "injury", "violation", "jump ball", "two-minute", "two minute", "kick",
              "punt", "field goal", "extra point", "touchdown", "goal", "icing", "offside", "turnover",
              "out of bounds")


# ---- switch log and lag (backtest's record of a cut, and real life -> screen) ------------------------

@dataclass
class SwitchLog:
    t: float
    frm: str | None
    to: str
    kind: str
    reason: str
    offset: float = 0.0   # [cut timing] the picture came up this far behind live (a rewind on entry)
    jitter: float = 0.0   # [auto-anchors] of which: the tune's own unknown landing behind the live edge
    arrive_dt: float = 0.0  # [live lab] the picture came up this much later (+) or sooner than tune_s
    leave_dt: float = 0.0   # [seek model] the screen left the old game this much after the decision (a pause cue)
    flash: float = 0.0      # [seek model] s of the LIVE edge shown before an on-screen rewind takes hold
    pos_true0: float | None = None   # [yttv position] the true delay when this tune's position clock started


class Lags(float):
    """Real life -> your screen: one figure (every channel alike), with per-game overrides when the
    channels run different delays (the per-channel delay scenario). Behaves as the plain float."""

    def __new__(cls, default: float, per: dict[str, float] | None = None):
        x = super().__new__(cls, default)
        x.per = dict(per or {})
        return x

    def of(self, gid: str | None) -> float:
        return self.per.get(gid or "", float(self))


def lag_of(lag: float, gid: str | None) -> float:
    """This game's real-life -> screen lag (the plain figure when channels don't differ)."""
    return lag.of(gid) if isinstance(lag, Lags) else lag


# ---- reading one play ------------------------------------------------------------------------------

def raw_type(p: dict) -> str:
    """The play's type text, lower case (cutgrade's reading)."""
    return (espn._get(p, "type", "text") or "").lower()


def play_type(p: dict) -> str:
    """The play's type text, lower case, with ESPN's lineup changes named (sportsgrade's reading)."""
    if (espn._get(p, "alternativeType", "type") == "lineup-change"
            or (espn._get(p, "alternativeType", "text") or "").lower() == "lineup change"):
        return "lineup_change"
    return (espn._get(p, "type", "text") or "").lower()


def play_clock(league: League, p: dict) -> float | None:
    c = espn._clock_s(espn._get(p, "clock", "displayValue"))
    if c is not None and league == League.NHL:
        per = espn._int(espn._get(p, "period", "number")) or 1
        c = max(0.0, (300 if per >= 4 else 1200) - c)
    return c


def marker(t: str) -> bool:
    return any(k in t for k in ("timeout", "end ", "end of", "two-minute", "coin toss", "period start",
                                "start inning", "start batter", "end batter", "end inning", "lineup_change"))


def is_dead_ball(league: str, typ: str) -> bool:
    t = (typ or "").lower()
    return any(w in t for w in DEAD_WORDS)


def is_big(league: League, p: dict, prev: dict | None) -> bool:
    t = play_type(p)
    if league == League.NFL:
        return bool(p.get("scoringPlay")) or "interception" in t or "fumble recovery (opponent)" in t \
            or bool(p.get("isTurnover"))
    if league == League.NHL:
        return t == "goal" or bool(p.get("scoringPlay"))
    if league == League.MLB:
        return bool(p.get("scoringPlay")) or "home run" in t
    if league == League.NBA:
        if not p.get("scoringPlay") or "free throw" in t:
            return False
        per = espn._int(espn._get(p, "period", "number")) or 0
        c = play_clock(league, p)
        if per < 4 or c is None or c > 120 or prev is None:
            return False
        hs, as_ = espn._int(prev.get("homeScore")), espn._int(prev.get("awayScore"))
        return hs is not None and as_ is not None and abs(hs - as_) <= 5
    return False


def is_crunch(league: League, p: dict) -> bool:
    per = espn._int(espn._get(p, "period", "number")) or 0
    c = play_clock(league, p)
    hs, as_ = espn._int(p.get("homeScore")), espn._int(p.get("awayScore"))
    if hs is None or as_ is None:
        return False
    return crunch.inside(crunch.row("graded", league), per, c, abs(hs - as_))   # [one brain] the crunch table


# ---- where a cut may land (dead ball on screen) and leave ------------------------------------------

def _base(league: League) -> League:
    return League.NBA if league.base == League.NBA else league


def plen(league: League) -> float:
    return PLAY_LEN.get(_base(league), 0.0)


def is_action(league: League, p: dict) -> bool:
    """A play that is live action (not bookkeeping or a dead-ball entry)."""
    if league in CONTINUOUS or league.base == League.NBA:
        return espn.stoppage_for(league, p) == Stoppage.NONE and not _is_marker(espn._kind(league, p), league)
    return not (marker(raw_type(p)) and not p.get("scoringPlay"))


def idx(wall: list[float], t: float) -> int:
    lo, hi = 0, len(wall)
    while lo < hi:
        mid = (lo + hi) // 2
        if wall[mid] <= t:
            lo = mid + 1
        else:
            hi = mid
    return lo - 1


def resume_after(league: League, wall: list[float], plays: list[dict], i: int) -> float:
    """Real time play restarts after the dead ball that play i left (inf = never in this game)."""
    if league not in CONTINUOUS and league.base != League.NBA:
        j = next((j for j in range(i + 1, len(plays)) if is_action(league, plays[j])), None)
        return wall[j] - plen(league) if j is not None else float("inf")
    j = next((j for j in range(i + 1, len(plays)) if is_action(league, plays[j])), None)
    if j is None:
        return float("inf")
    if league == League.NHL:
        fo = next((k for k in range(i + 1, j + 1) if raw_type(plays[k]) == "face off"), None)
        return wall[fo] if fo is not None else wall[j] - RESUME_LEAD[League.NHL]
    # Basketball: the inbound isn't logged. The clock that ran before the first clock-moving event.
    per_i = espn._int(espn._get(plays[i], "period", "number"))
    c0 = play_clock(league, plays[i])
    for k in range(i + 1, min(len(plays), j + 40)):
        c = play_clock(league, plays[k])
        if espn._int(espn._get(plays[k], "period", "number")) != per_i:
            break
        if c is not None and c0 is not None and c < c0 - 0.05:
            return max(wall[i], wall[k] - (c0 - c))
    return max(wall[i], wall[j] - RESUME_LEAD[League.NBA])


def dead_at(league: League, wall: list[float], plays: list[dict], st: float) -> tuple[bool, bool, float]:
    """(dead ball on screen, inside a commercial break, seconds until play resumes) at screen time st."""
    i = idx(wall, st)
    if i < 0 or st > wall[-1]:
        return True, False, 0.0
    if league not in CONTINUOUS and league.base != League.NBA:
        pl = plen(league)
        for w, p in zip(wall[max(0, i - 3):i + 4], plays[max(0, i - 3):i + 4]):
            if is_action(league, p) and w - pl <= st <= w:
                return False, False, 0.0
        res = resume_after(league, wall, plays, i)
        brk = espn.stoppage_for(league, plays[i]) in REAL_BREAKS
        return True, brk, max(0.0, min(res, 900.0 + st) - st)
    last = plays[i]
    stop = espn.stoppage_for(league, last)
    res = resume_after(league, wall, plays, i)
    dead = stop != Stoppage.NONE or (bool(last.get("scoringPlay")) and st - wall[i] <= 4.0)
    if dead and st < res:
        # The commercial is whatever started this dead ball (a timeout followed by subs is a break).
        k, brk = i, False
        while k >= 0 and not brk:
            brk = espn.stoppage_for(league, plays[k]) in REAL_BREAKS
            if is_action(league, plays[k]):
                break
            k -= 1
        return True, brk, max(0.0, min(res, 900.0 + st) - st)
    return False, False, 0.0


def clean_leave(league: League, wall: list[float], plays: list[dict], st: float) -> bool:
    """May a cut leave this game at screen time st without walking out mid-play? (sportsgrade's
    _clean_cut rule with a binary search.)"""
    i = idx(wall, st)
    if i == len(wall) - 1 or (i >= 0 and st > wall[-1]):
        return True
    if league in CONTINUOUS:
        if i < 0:
            return True
        last = plays[i]
        nxt = next((wall[j] for j in range(i + 1, len(wall))
                    if espn.stoppage_for(league, plays[j]) == Stoppage.NONE
                    and not _is_marker(espn._kind(league, plays[j]), league)), math.inf)
        dead = espn.stoppage_for(league, last) != Stoppage.NONE or (
            bool(last.get("scoringPlay")) and st - wall[i] <= 4.0)
        return bool(dead and st < nxt - RESUME_LEAD[league])
    pl = PLAY_LEN[league]
    lo = max(0, i - 3)
    for w, p in zip(wall[lo:i + 4], plays[lo:i + 4]):
        if marker(play_type(p)) and not p.get("scoringPlay"):
            continue
        if w - pl <= st <= w:
            return False
    return True
