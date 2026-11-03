"""Cut timing: WHEN to leave a game and WHEN to come into one.

Leave inside dead time; enter just before play resumes, never mid-play.

  dead window   the stretch after a play-by-play entry while the ball is dead: free throws, a foul,
                a timeout, a review, an injury, a period end, an NFL huddle, an NHL stoppage before
                the faceoff, MLB between pitches / batters / innings. Each kind has a measured length
                distribution (data/archive, wallclock of the entry -> the real restart).
  resume        the restart: the snap, the inbound, the faceoff, the pitch.

Two questions the decider asks:

  landing(tl, delay, at)     what will the screen show at `at` (when the picture comes up)?
                             "dead" (with how long until play resumes), "play" (mid-play) or
                             "unknown" (the data doesn't cover it).
  plan_entry(...)            enter now as is; enter with a short rewind (YouTube TV DVR) so the
                             picture comes up on the line-up / the dead ball before the play we'd
                             otherwise join mid-way; or wait for the destination's next dead ball.

Everything is in REAL time (feed wallclock); the screen shows real time `t - delay.mu` at wall time t,
give or take 2 sigma. Unsure intervals are always taken on the unsafe side.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from redzone.models import League

# ----------------------------------------------------------------------------- dead windows

_REVIEW = ("challenged", "challenge", "replay official", "reversed", "upheld", "review", "instant replay",
           "call stands", "confirmed")


def after_kind(league: League, kind: str, text: str = "", ttext: str = "") -> str | None:
    """The dead window this play-by-play entry leaves behind, or None if the ball is live after it.

    kind: espn._kind (normalized); text: the play text; ttext: ESPN's raw type text."""
    k, t, tt = (kind or "").lower(), (text or "").lower(), (ttext or "").lower()
    base = league.base
    if base == League.NFL:
        if tt.startswith("two-minute"):
            return "two_minute"
        if tt == "end of half":
            return "halftime"
        if tt in ("end period", "end of regulation", "end of game"):
            return "end_period"
        if "timeout" in tt or "timeout" in k:
            return "team_timeout" if "timeout #" in t or tt == "timeout" else "tv_timeout"
        if k in ("td", "fg") or "touchdown" in t or "field goal is good" in t or "safety" in tt:
            return "score"
        if any(r in t for r in _REVIEW):
            return "review"
        if "injur" in t:
            return "injury"
        if "kickoff" in tt or "kickoff" in k:
            return "kickoff"
        if "punt" in tt or "punt" in k:
            return "punt"
        if k == "turnover" or "intercept" in t or "fumble recovery (opponent)" in tt:
            return "turnover"
        if "penalty" in tt or "penalty" in t:
            return "penalty"
        return "huddle"
    if base == League.NBA:
        if "timeout" in tt or "timeout" in k:
            return "media_timeout" if league.college and ("tv" in t or "media" in t or "official" in tt) else "timeout"
        if tt in ("end period", "end game", "end of game") or "end_period" in k:
            return "end_period"
        if "challenge" in tt or "review" in tt or "instant replay" in tt or "challenge" in t or "review" in t:
            return "review"
        if "team rebound" in t or k == "team_rebound":
            return "out_of_bounds"
        if "free throw" in tt or "freethrow" in tt.replace(" ", "") or "free_throw" in k:
            src = tt if any(c.isdigit() for c in tt) else f"{tt} {t}"
            return None if _last_ft(src) else "free_throw"
        if "foul" in tt or "foul" in k:
            # Shooting fouls go to the line: the longest whistle (1st pct 23 s, 1,750 NBA fouls).
            if "shooting" in tt or "shooting" in t:
                return "shooting_foul"
            if "offensive" in tt:
                return "offensive_foul"
            return "foul"
        if "violation" in tt or "kicked ball" in tt or "delay" in tt or "out of bounds" in tt:
            return "violation"
        if "substitution" in tt or "substitution" in k:
            return "substitution"
        if "jump ball" in tt:
            return None
        return None
    if league == League.NHL:
        if tt == "stoppage":
            if "tv timeout" in t:
                return "tv_timeout"
            if "timeout" in t:
                return "timeout"
            if "challenge" in t or "review" in t:
                return "review"
            if "icing" in t:
                return "icing"
            return "whistle"
        if tt in ("period end", "end of game", "game end"):
            return "intermission"
        if tt == "goal" or k == "goal":
            return "goal"
        if tt in ("hooking", "holding", "tripping", "roughing", "interference", "slashing", "high-sticking",
                  "fighting", "unsportsmanlike conduct", "cross-checking", "delay of game", "too many men",
                  "penalty") or "minor" in t and "penalty" in t:
            return "penalty"
        return None
    if league == League.MLB:
        if "end inning" in tt:
            return "inning"
        if "pitching change" in tt or "pitcher change" in t:
            return "pitching_change"
        if "end batter" in tt or "start batter" in tt:
            return "between_batters"
        if "start inning" in tt:
            return "inning_start"
        return "between_pitches"
    return None


def _last_ft(t: str) -> bool:
    import re
    if "technical" in t or "flagrant" in t:
        return False
    m = re.search(r"(\d) of (\d)", t)
    return bool(m) and m.group(1) == m.group(2)


# Back-compat name for the measuring script.
classify = after_kind

# Markers: bookkeeping entries that name a dead window but aren't a play themselves.
_DISCRETE_MARKERS = {"tv_timeout", "team_timeout", "two_minute", "halftime", "end_period", "inning",
                     "pitching_change", "between_batters", "inning_start"}

# Priority when several entries share one dead stretch (a TD, then the XP, then the TV timeout):
# the longest kind names it.
_RANK = ("halftime", "intermission", "end_period", "inning", "tv_timeout", "media_timeout", "two_minute",
         "review", "score", "pitching_change", "timeout_long", "timeout", "goal", "injury", "punt", "turnover", "penalty",
         "kickoff", "team_timeout", "timeout_short", "icing", "whistle", "shooting_foul", "free_throw", "foul", "offensive_foul", "out_of_bounds", "violation",
         "substitution", "between_batters", "inning_start", "huddle", "between_pitches")

# The shortest each kind of dead window has run (about the 2nd percentile, wallclock of the entry
# -> the real restart; data/archive Nov 2026: 30k NFL plays, 92 NBA / 95 NHL / 230 MLB games). A
# window with no restart in the data yet is surely still dead this long. Kinds whose short tail is
# unreliable (NBA timeouts are sometimes stamped at the restart; a foul without free throws is
# ~5 s) get none.
FLOOR_S = {
    League.NFL: {"halftime": 800.0, "end_period": 165.0, "tv_timeout": 40.0, "two_minute": 70.0, "review": 80.0,
                 "score": 60.0, "kickoff": 30.0, "punt": 38.0, "turnover": 45.0, "penalty": 25.0,
                 "team_timeout": 15.0, "huddle": 14.0, "injury": 18.0},
    # NBA from 1,145 timeouts / 7,400 fouls and free throws with consistent wallclocks (8% of
    # timeouts sit in stretches where ESPN's wallclock froze: see DeadTracker.frozen_until).
    # A quarter's first two timeouts are the long (commercial) ones (5th pct 107 s), later ones
    # can be short.
    League.NBA: {"end_period": 145.0, "halftime": 800.0, "timeout_long": 100.0, "timeout_short": 15.0,
                 "media_timeout": 45.0, "shooting_foul": 22.0, "foul": 15.0, "offensive_foul": 10.0,
                 "free_throw": 12.0, "review": 10.0},
    League.NHL: {"tv_timeout": 130.0, "goal": 35.0, "icing": 19.0, "whistle": 18.0, "penalty": 33.0,
                 "review": 100.0, "timeout": 80.0, "intermission": 135.0},
    League.MLB: {"inning": 115.0, "pitching_change": 60.0, "between_batters": 8.0, "between_pitches": 6.0},
}
# Typical (median) dead time, for the phone and for ranking where to land.
MEDIAN_S = {
    League.NFL: {"halftime": 850.0, "end_period": 196.0, "tv_timeout": 147.0, "two_minute": 160.0, "review": 217.0,
                 "score": 187.0, "kickoff": 41.0, "punt": 172.0, "turnover": 75.0, "penalty": 50.0,
                 "team_timeout": 43.0, "huddle": 34.0, "injury": 80.0},
    League.NBA: {"timeout": 184.0, "end_period": 185.0, "halftime": 933.0, "review": 153.0, "free_throw": 25.0,
                 "foul": 40.0, "out_of_bounds": 15.0, "violation": 14.0, "substitution": 10.0},
    League.NHL: {"tv_timeout": 149.0, "goal": 48.0, "icing": 30.0, "whistle": 37.0, "penalty": 56.0,
                 "review": 200.0, "timeout": 99.0, "intermission": 1129.0},
    League.MLB: {"inning": 173.0, "pitching_change": 178.0, "between_batters": 29.0, "between_pitches": 19.0},
}
# How long a play runs before its logged wallclock (the whistle) on screen.
PLAY_S = {League.NFL: 10.0, League.MLB: 8.0}
AFTER_PLAY_S = 3.0           # celebration / whistle after a discrete play: not a clean landing yet
CLOCK_RESUME_MARGIN_S = 3.0  # a restart worked back from the game clock: wallclock and clock rounding
# Basketball: after an inbound the ball can be live for a full shot clock with nothing logged.
SILENT_S = {League.NBA: 24.0}
FROZEN_HOLD_S = 300.0        # after a frozen-wallclock stretch, distrust windows this long
LEARNED_MAX_S = 1800.0       # a learned restart further out than this is a broken reading
# How long before play resumes the picture should come up: the line-up / set / faceoff circle.
LEAD_S = {League.NFL: 6.0, League.NBA: 4.0, League.NHL: 5.0, League.MLB: 4.0}


def _lg(league: League) -> League:
    if league.base == League.NBA:
        return League.NBA
    if league.base == League.NFL:
        return League.NFL
    return league


def _strongest(a: str | None, b: str | None) -> str | None:
    if a is None or b is None:
        return a or b
    ra = _RANK.index(a) if a in _RANK else len(_RANK)
    rb = _RANK.index(b) if b in _RANK else len(_RANK)
    return a if ra <= rb else b


@dataclass
class Mark:
    """One play-by-play entry, in real time."""
    pid: str
    lo: float               # earliest it could have happened
    hi: float               # latest (== lo with a wallclock)
    after: str | None       # dead window it leaves behind (None: the ball is live after it)
    clock: float | None = None
    period: int | None = None
    faceoff: bool = False


@dataclass
class Window:
    """A dead window in real time: surely dead from `start` until `end` (None: not over in the data)."""
    start: float
    end: float | None
    kind: str
    exact: bool = False     # end worked out from the game clock / a logged faceoff / the next snap


@dataclass
class Landing:
    state: str                    # "dead" | "play" | "unknown"
    kind: str = ""
    resume: float | None = None   # real time play resumes (None: not known yet)
    why: str = ""


@dataclass
class EntryPlan:
    ok: bool
    rewind_s: float = 0.0   # land this far behind the live edge (whole seek steps)
    landing: Landing | None = None
    why: str = ""


def _is_action(lg: League, m: Mark) -> bool:
    if lg in (League.NFL, League.MLB):
        return m.after is not None and m.after not in _DISCRETE_MARKERS
    return m.after is None and not m.faceoff


class DeadTracker:
    """Per-game dead windows, fed from every poll's plays (Timeline.observe)."""

    def __init__(self, league: League):
        self.league = league
        self.marks: dict[str, Mark] = {}
        self._sorted: list[Mark] | None = None
        self._windows: list[Window] | None = None
        self.fresh_at: float | None = None
        self.lag: float = 10.0
        self._timeouts: dict[int | None, int] = {}
        self._last_stamp: tuple | None = None
        self.frozen_until: float = float("-inf")
        # Brain v3's entry point for the newest event: (event wallclock, (p10, p50, p90) seconds to the
        # restart). Used only for the EXPECTED restart of a window the data hasn't closed yet; the
        # "surely dead until" floors that gate cuts and landings never read it.
        self.learned: tuple[float, tuple[float, float, float]] | None = None
        self.learned_q = 1          # which quantile the expected restart uses (0 = p10, 1 = p50)
        self.learned_cap = True     # never later than the table's median (a learned estimate only shortens)

    # ---- input
    def add(self, pid: str, lo: float, hi: float, kind: str, text: str, type_text: str,
            clock: float | None, period: int | None) -> None:
        if not pid or pid in self.marks:
            return
        after = after_kind(self.league, kind, text, type_text)
        if (kind or "").lower() == "lineup_change":
            after = "between_batters"
        tt = (type_text or "").lower()
        lg = _lg(self.league)
        if after == "timeout" and lg == League.NBA:
            # The first two timeouts of a quarter are the long (commercial) ones, whoever calls them.
            n = self._timeouts[period] = self._timeouts.get(period, 0) + 1
            after = "timeout_long" if n <= 2 and not self.league.college else "timeout_short"
        if lo == hi and clock is not None and lg not in (League.NFL, League.MLB):
            # ESPN's wallclock sometimes freezes for a stretch (every entry stamped alike while the
            # game clock runs): real times there are unknown, so no window there is certified.
            last = self._last_stamp
            if (last is not None and abs(lo - last[0]) < 1.0 and last[2] == period
                    and last[1] is not None and abs(last[1] - clock) >= 5.0):
                self.frozen_until = max(self.frozen_until, hi + FROZEN_HOLD_S)
            self._last_stamp = (lo, clock, period)
        self.marks[pid] = Mark(pid, lo, hi, after, clock, period, faceoff=tt in ("face off", "faceoff"))
        self._sorted = self._windows = None
        if len(self.marks) > 600:   # keep the recent past only
            for m in self._order()[:200]:
                self.marks.pop(m.pid, None)
            self._sorted = None

    def learn(self, at: float | None, entry_q) -> None:
        """Brain v3's (p10, p50, p90) seconds from the event stamped `at` until play resumes."""
        try:
            q = tuple(float(x) for x in entry_q)
        except (TypeError, ValueError):
            return
        if at is None or len(q) != 3 or not all(math.isfinite(x) and 0 <= x <= LEARNED_MAX_S for x in q):
            return
        self.learned = (float(at), q)

    def _learned_end(self, w: Window) -> float | None:
        """The model's expected restart for an open window, if its reading is about this window's
        newest event (an older reading says nothing about a stoppage that started since)."""
        if self.learned is None:
            return None
        at, q = self.learned
        newest = self._order()[-1].hi if self.marks else None
        if newest is None or abs(newest - at) > 1.0 or at + 1.0 < w.start - AFTER_PLAY_S:
            return None
        return at + q[self.learned_q]

    def seen(self, fresh_at: float, lag: float) -> None:
        self.fresh_at, self.lag = fresh_at, lag

    def _order(self) -> list[Mark]:
        if self._sorted is None:
            self._sorted = sorted(self.marks.values(), key=lambda m: (m.hi, m.lo))
        return self._sorted

    # ---- dead windows
    def windows(self) -> list[Window]:
        if self._windows is not None:
            return self._windows
        lg = _lg(self.league)
        ms = self._order()
        out: list[Window] = []
        if lg in (League.NFL, League.MLB):
            plen = PLAY_S[lg]
            # Dead from a play's whistle (+ celebration) until the next play's snap. Markers (timeouts,
            # period ends) in between only name the window.
            i = 0
            while i < len(ms):
                m = ms[i]
                j, kind = i + 1, m.after
                while j < len(ms) and not _is_action(lg, ms[j]):
                    kind = _strongest(kind, ms[j].after)
                    j += 1
                start = m.hi + (AFTER_PLAY_S if _is_action(lg, m) else 0.0)
                end = ms[j].lo - plen if j < len(ms) else None
                if end is None or end > start:
                    out.append(Window(start, end, kind or "huddle", exact=end is not None))
                i = j
        else:
            cur: Window | None = None
            c0 = p0 = None
            for m in ms:
                if cur is not None:
                    moved = (m.clock is not None and c0 is not None and m.period == p0 and m.clock < c0 - 0.05)
                    if lg == League.NHL and m.faceoff:
                        cur.end, cur.exact = max(cur.start, m.lo), True
                    elif moved and lg == League.NBA:
                        cur.end, cur.exact = max(cur.start, m.lo - (c0 - m.clock) - CLOCK_RESUME_MARGIN_S), True
                    elif (lg == League.NBA and m.after is None and m.clock is not None and c0 is not None
                          and m.period == p0):
                        # The last free throw: a dead-ball moment itself (the clock hasn't run), but
                        # the ball is live (or inbounded) right after it.
                        cur.end, cur.exact = max(cur.start, m.lo), True
                    elif m.after is None and not m.faceoff:
                        # live action logged, no better evidence: play resumed before it at the latest
                        cur.end = max(cur.start, m.lo - SILENT_S.get(lg, 0.0) - 2.0)
                    if cur.end is not None:
                        out.append(cur)
                        cur = None
                if cur is None and m.after is not None:
                    cur = Window(m.hi, None, m.after)
                    c0, p0 = m.clock, m.period
                elif cur is not None and m.after is not None:
                    cur.kind = _strongest(cur.kind, m.after)
            if cur is not None:
                out.append(cur)
        if self.frozen_until > float("-inf"):
            out = [w if w.start > self.frozen_until else Window(w.start, w.start, w.kind) for w in out]
        self._windows = out
        return out

    def _open_until(self, w: Window) -> float:
        """A window with no restart in the data: surely dead until this real time."""
        lg = _lg(self.league)
        if w.start <= self.frozen_until:
            return w.start     # the feed's wallclock froze around here: nothing is certain
        floor = w.start + FLOOR_S.get(lg, {}).get(w.kind, 0.0)
        if self.fresh_at is None:
            return floor
        # Anything that restarted play before this would already be in the data.
        covered = self.fresh_at - self.lag - PLAY_S.get(lg, 0.0) - SILENT_S.get(lg, 0.0) - (
            0.0 if lg in (League.NFL, League.MLB) else 2.0)
        return max(floor, covered)

    def dead_until(self, x0: float) -> float | None:
        """The screen shows real time x0 (its earliest) now: until what real time is it surely still
        dead? None if x0 isn't inside a dead window."""
        for w in reversed(self.windows()):
            if w.start > x0:
                continue
            until = w.end if w.end is not None else self._open_until(w)
            return until if until > x0 else None
        return None

    def resume_eta(self, x: float) -> float:
        """Real time play is expected to resume after the dead window covering real time x (x itself
        if it's live): the logged restart, else the window's typical (median) length."""
        for w in reversed(self.windows()):
            if w.start > x:
                continue
            if w.end is not None:
                return max(x, w.end)
            table = w.start + MEDIAN_S.get(_lg(self.league), {}).get(w.kind, 30.0)
            learned = self._learned_end(w)
            if learned is not None:
                return max(x, min(learned, table) if self.learned_cap else learned)
            return max(x, table)
        return x

    def landing(self, x0: float, x1: float) -> Landing:
        """What the screen shows across real times [x0, x1] (the arrival, give or take the delay doubt)."""
        lg = _lg(self.league)
        if lg in (League.NFL, League.MLB):
            plen = PLAY_S[lg]
            for m in reversed(self._order()):
                if m.hi + AFTER_PLAY_S < x0 - 600:
                    break
                if _is_action(lg, m) and m.lo - plen - 1.0 <= x1 and x0 <= m.hi + AFTER_PLAY_S:
                    return Landing("play", why="a play is on screen")
        for w in reversed(self.windows()):
            if w.start > x0:
                continue
            until = w.end if w.end is not None else self._open_until(w)
            if x1 <= until:
                return Landing("dead", w.kind, w.end, "dead ball on screen")
            if w.end is not None and x0 < w.end:
                return Landing("play", w.kind, w.end, "play resumes while you land")
            break
        return Landing("unknown" if lg in (League.NFL, League.MLB) else "play", why="play may be live")


def plan_entry(dt: DeadTracker, mu: float, margin: float, now: float, cost: float, *,
               can_rewind: bool, step: float = 10.0, max_back: float = 60.0,
               relaxed: float | None = None, cost_lo: float | None = None,
               urgent: float | None = None) -> EntryPlan:
    """How to come into this game: as is (the picture lands on a dead ball), with a short rewind so
    it lands on the dead ball just before the restart we'd otherwise join mid-play, or not yet.

    mu, margin: the destination's delay and its doubt (2 sigma + the tune's own). The picture comes
    up somewhere between cost_lo (a fast flip) and cost seconds from now, so the whole stretch must
    land dead. With the full margin no landing fits a 30 s huddle once the delay's doubt passes
    ~10 s, so a second pass centres the landing in the dead window with `relaxed` doubt (default:
    none); such a landing is flagged in its `why`."""
    x1 = now + cost - mu
    x0 = now + min(cost, cost if cost_lo is None else cost_lo) - mu
    here = dt.landing(x0 - margin, x1 + margin)
    if here.state == "dead" or (here.state == "unknown" and not can_rewind):
        # Football/baseball with no play known to be on screen: the leave gate's own rule.
        return EntryPlan(True, 0.0, here, here.why)
    strict = _rewind(dt, x0, x1, margin, step, max_back) if can_rewind else None
    if relaxed is not None and relaxed < margin:
        soft = dt.landing(x0 - relaxed, x1 + relaxed)
        if soft.state == "dead" and (strict is None or strict.rewind_s > step):
            return EntryPlan(True, 0.0, soft, soft.why + " (centred)")
        loose = _rewind(dt, x0, x1, relaxed, step, max_back) if can_rewind else None
        # The full margin wins unless it costs more than one extra seek step.
        if loose is not None and (strict is None or strict.rewind_s > loose.rewind_s + step):
            loose.why += " (centred)"
            return loose
    if strict is not None:
        return strict
    if urgent is not None and urgent < min(margin, relaxed if relaxed is not None else margin):
        # Crunch time / your team: every second waiting is a play missed. Take the dead ball the
        # data puts the landing on even if the delay's doubt can't rule out a few seconds' error.
        last = dt.landing(x0 - urgent, x1 + urgent)
        if last.state == "dead":
            return EntryPlan(True, 0.0, last, last.why + " (urgent)")
        quick = _rewind(dt, x0, x1, urgent, step, max_back) if can_rewind else None
        if quick is not None:
            quick.why += " (urgent)"
            return quick
    if here.state == "unknown":
        return EntryPlan(True, 0.0, here, here.why)
    return EntryPlan(False, 0.0, here, f"waiting for a dead ball to come in ({here.why})")


def _rewind(dt: DeadTracker, x0: float, x1: float, m: float, step: float, max_back: float) -> EntryPlan | None:
    """The shortest rewind k (whole seek steps) that puts all of [x0 - k, x1 - k] on a dead ball, doubt m."""
    if max_back < step:
        return None
    for w in reversed(dt.windows()):
        if w.start > x1:
            continue
        until = w.end if w.end is not None else dt._open_until(w)
        if x0 - until > max_back + m:
            break
        # The latest landing in the window: the picture comes up just before the restart.
        k = max(step, math.ceil((x1 + m - until) / step) * step)
        if k > max_back:
            break
        if x0 - k - m < w.start:
            continue
        land = dt.landing(x0 - k - m, x1 - k + m)
        if land.state != "dead":
            continue
        return EntryPlan(True, k, land, f"rewinding {k:.0f}s to the dead ball before the play")
    return None
