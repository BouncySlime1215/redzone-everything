"""Cut timing: WHEN to leave a game and WHEN to come into one.

Why: Rule #1: leave inside dead time, enter just before play resumes, never mid-play (commit 2026-11-03 'Enter on a dead ball'; EXP-024, EXP-089, EXP-211).

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
from dataclasses import dataclass, field

from redzone.models import PERIOD_S, REGULATION, ByLeague, League
from redzone.sources.espn import OT_S

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
    # timeout_long / media_timeout: the kinds DeadTracker now produces; the old "timeout" key would never be read (AUDIT-8)
    League.NBA: {"timeout": 184.0, "timeout_long": 184.0, "media_timeout": 184.0, "end_period": 185.0, "halftime": 933.0, "review": 153.0, "free_throw": 25.0,
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
SILENT_S = ByLeague({League.NBA: 24.0, League.CBB: 30.0, League.WCBB: 30.0})   # college: a 30 s shot clock
FROZEN_HOLD_S = 300.0        # after a frozen-wallclock stretch, distrust windows this long
LEARNED_MAX_S = 1800.0       # a learned restart further out than this is a broken reading
# How long before play resumes the picture should come up: the line-up / set / faceoff circle.
LEAD_S = {League.NFL: 6.0, League.NBA: 4.0, League.NHL: 5.0, League.MLB: 4.0}
# [Nick 2026-11-08 landing bars] jump_mode.join: basketball may join mid-possession only in the walk up court right
# after a shot, never into a transition play; football joins after the play, preferably after its replay.
LANDING_DEFAULTS = {"after_shot": ["nba", "cbb"],   # leagues whose picture may come up in the walk up court after a shot
                 "walk_s": 14.0,                 # the walk: the picture is up within this long after the shot/rebound
                 "transition_s": 7.0,            # the next action this soon after the shot: a transition play, no landing
                 "after_play_s": 18.0,           # football: prefer the picture up this long after the whistle (the replay)
                 "walk_after_s": 1.0,            # the shot (or rebound) is stamped ~1 s before the ball is clear
                 "walk_guard_s": 2.0}            # the next action's stamp can lead its start: the walk ends this far before it


def _lg(league: League) -> League:
    if league.base == League.NBA:
        return League.NBA
    if league.base == League.NFL:
        return League.NFL
    return league


def _period_length(league: League, period: int) -> float:
    """Seconds on the clock at the start of this basketball period."""
    return float(OT_S[league] if period > REGULATION[league] else PERIOD_S[league])


def join_cfg(cfg, key: str):
    """[landing bars] jump_mode.join.<key>, or its default."""
    v = cfg.get(f"jump_mode.join.{key}") if cfg is not None and hasattr(cfg, "get") else None
    return LANDING_DEFAULTS[key] if v is None else v


Walk = tuple[float, float, float, float]   # (walk_s, transition_s, walk_after_s, walk_guard_s)


def walk_params(cfg, league: League) -> Walk | None:
    """(walk_s, transition_s, walk_after_s, walk_guard_s) when this league's picture may come up in the walk after a shot, else None."""
    if league.base != League.NBA or league.value not in (join_cfg(cfg, "after_shot") or []):
        return None
    return (float(join_cfg(cfg, "walk_s")), float(join_cfg(cfg, "transition_s")),
            float(join_cfg(cfg, "walk_after_s")), float(join_cfg(cfg, "walk_guard_s")))


def shot_kind(league: League, kind: str, text: str = "", ttext: str = "") -> str | None:
    """Basketball: "made" / "miss" for a shot (a last free throw counts, earlier ones are dead balls),
    "dreb" / "oreb" for a player rebound, else None."""
    if league.base != League.NBA:
        return None
    k, t, tt = (kind or "").lower(), (text or "").lower(), (ttext or "").lower()
    if "team_rebound" in k or "team rebound" in t or "timeout" in tt:
        return None
    if "rebound" in tt or "rebound" in k:
        return "oreb" if "offensive" in tt or "offensive" in k else "dreb" if "defensive" in tt or "defensive" in k else None
    if " makes " in f" {t} " or " made " in f" {t} ":
        return "made"
    if " misses " in f" {t} " or " missed " in f" {t} ":
        return "miss"
    return None


def _strongest(a: str | None, b: str | None) -> str | None:
    if a is None or b is None:
        return a or b
    ra = _RANK.index(a) if a in _RANK else len(_RANK)
    rb = _RANK.index(b) if b in _RANK else len(_RANK)
    return a if ra <= rb else b


# [data gap] marks that follow a dead ball but don't prove one on their own: the stoppage itself is missing
WEAK_OPEN = frozenset({"substitution"})


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
    shot: str | None = None   # basketball: made / miss / dreb / oreb (shot_kind)
    ft: bool = False          # basketball: a free throw (a made last one is shot "made", but it is not a made basket: gate._post_make_windows)


@dataclass
class Window:
    """A dead window in real time: surely dead from `start` until `end` (None: not over in the data)."""
    start: float
    end: float | None
    kind: str
    exact: bool = False     # end worked out from the game clock / a logged faceoff / the next snap
    marks: list = field(default_factory=list)   # (stamp, kind) of each dead-ball entry in it, the opener first (floor_from_marks)


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
    slack: float | None = None   # [EXP-024] the widest delay error (s, either way) the landing still lands dead


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
        self._first_clock: dict[int | None, float | None] = {}   # period -> clock of the first entry seen in it
        self._last_stamp: tuple | None = None
        self.frozen_until: float = float("-inf")
        # Brain v3's entry point for the newest event: (event wallclock, (p10, p50, p90) seconds to the
        # restart). Used only for the EXPECTED restart of a window the data hasn't closed yet; the
        # "surely dead until" floors that gate cuts and landings never read it.
        self.learned: tuple[float, tuple[float, float, float]] | None = None
        self.learned_q = 1          # which quantile the expected restart uses (0 = p10, 1 = p50)
        self.learned_cap = True     # never later than the table's median (a learned estimate only shortens)
        # [noggin] Noggin Timing v1 (redzone/noggin/live.py): a calibrated bound for open windows in place
        # of FLOOR_S / MEDIAN_S. None = today's tables (noggin.timing.leagues off for this league).
        self.noggin = None
        # [data gap] open a dead window only on a confirmed stoppage: a substitution with no stoppage
        # logged before it means the play that stopped the game is missing from the data, so how long
        # it has been dead (and when it restarted) is unknown (NBA GS @ DEN, Nov 4: a 70 s rewind
        # into such a gap landed mid-play). Set from decider.entry.confirmed_dead_only.
        self.confirmed_only = False
        # [EXP-211] the feed lag a landing assumes at least (decider.entry.lag_floor_s, set per plan by the
        # decider): until a game's lag is measured it reads 10 s, but NHL's real feed lag runs ~50 s.
        self.lag_floor = 0.0
        # [cuts.floor_from_marks, off] an open window is surely dead until the latest (entry stamp + that entry's own floor) over
        # its entries, not the first entry's stamp + the strongest kind's floor; a noggin bound is read given the dead time the
        # latest entry's stamp proves. Set by the decider from config.
        self.floor_from_marks = False
        self._walks: dict[Walk, list[Window]] = {}

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
        self._first_clock.setdefault(period, clock)
        if after == "timeout" and lg == League.NBA:
            # The first two timeouts of a quarter are the long (commercial) ones, whoever calls them.
            n = self._timeouts[period] = self._timeouts.get(period, 0) + 1
            # ... a count that only holds for a period we saw start: ESPN's feed hands over the last 8 plays only.
            after = "timeout_long" if n <= 2 and not self.league.college and self._saw_start(period) else "timeout_short"
        if lo == hi and clock is not None and lg not in (League.NFL, League.MLB):
            # ESPN's wallclock sometimes freezes for a stretch (every entry stamped alike while the
            # game clock runs): real times there are unknown, so no window there is certified.
            last = self._last_stamp
            if (last is not None and abs(lo - last[0]) < 1.0 and last[2] == period
                    and last[1] is not None and abs(last[1] - clock) >= 5.0):
                self.frozen_until = max(self.frozen_until, hi + FROZEN_HOLD_S)
            self._last_stamp = (lo, clock, period)
        self.marks[pid] = Mark(pid, lo, hi, after, clock, period, faceoff=tt in ("face off", "faceoff"),
                               shot=shot_kind(self.league, kind, text, type_text), ft="freethrow" in tt.replace(" ", ""))
        self._sorted = self._windows = None
        self._walks.clear()
        if len(self.marks) > 600:   # keep the recent past only
            for m in self._order()[:200]:
                self.marks.pop(m.pid, None)
            self._sorted = None
            self._walks.clear()

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
                    out.append(Window(start, end, kind or "huddle", exact=end is not None,
                                      marks=[(start, m.after)] + [(x.hi, x.after) for x in ms[i + 1:j]]))
                i = j
        else:
            cur: Window | None = None
            c0 = p0 = None
            for m in ms:
                if cur is not None:
                    moved = (m.clock is not None and c0 is not None and m.period == p0 and m.clock < c0 - 0.05)
                    if lg == League.NHL and m.faceoff:
                        cur.end, cur.exact = max(cur.start, m.lo), True
                    elif moved and lg in (League.NBA, League.NHL):
                        # [AUDIT-8] hockey too: a faceoff the feed missed must not leave live hockey looking dead
                        cur.end, cur.exact = max(cur.start, m.lo - (c0 - m.clock) - CLOCK_RESUME_MARGIN_S), True
                    elif lg in (League.NBA, League.NHL) and (ran := self._ran_in_later_period(m, p0)) is not None:
                        # The next period's first entry we hold, a dead ball or a live action: its clock dates the restart.
                        cur.end, cur.exact = max(cur.start, m.lo - ran - CLOCK_RESUME_MARGIN_S), True
                    elif (lg == League.NBA and m.after is None and m.clock is not None and c0 is not None
                          and m.period == p0):
                        # The last free throw: a dead-ball moment itself (the clock hasn't run), but
                        # the ball is live (or inbounded) right after it.
                        cur.end, cur.exact = max(cur.start, m.lo), True
                    elif m.after is None and not m.faceoff:
                        # live action logged, no better evidence: play resumed before it at the latest
                        cur.end = max(cur.start, m.lo - SILENT_S.get(self.league, 0.0) - 2.0)
                    if cur.end is not None:
                        out.append(cur)
                        cur = None
                if cur is None and m.after is not None and not (self.confirmed_only and m.after in WEAK_OPEN):
                    cur = Window(m.hi, None, m.after, marks=[(m.hi, m.after)])
                    c0, p0 = m.clock, m.period
                elif cur is not None and m.after is not None:
                    cur.kind = _strongest(cur.kind, m.after)
                    cur.marks.append((m.hi, m.after))
            if cur is not None:
                out.append(cur)
        if self.frozen_until > float("-inf"):
            out = [w if w.start > self.frozen_until else Window(w.start, w.start, w.kind) for w in out]
        self._windows = out
        return out

    def _saw_start(self, period: int | None, within: float = 60.0) -> bool:
        """Did the first entry we hold for this basketball period come within its first minute? Else timeouts may
        have gone by before we joined it, and this one can't be called the period's first or second."""
        first = self._first_clock.get(period)
        return first is not None and period is not None and first >= _period_length(self.league, period) - within

    def _ran_in_later_period(self, m: Mark, p0: int | None) -> float | None:
        """Basketball and hockey: the game clock already run when this entry of a LATER period than the window's was logged, or
        None when it isn't one, or the clock hasn't moved yet (a substitution before the tip)."""
        if m.clock is None or m.period is None or p0 is None or m.period <= p0:
            return None
        ran = _period_length(self.league, m.period) - m.clock
        return ran if ran > 0.05 else None

    def _open_until(self, w: Window) -> float:
        """A window with no restart in the data: surely dead until this real time."""
        lg = _lg(self.league)
        if w.start <= self.frozen_until:
            return w.start     # the feed's wallclock froze around here: nothing is certain
        covered = self._covered()
        # [cuts.floor_from_marks] the ball was dead at the stamp of every dead-ball entry in the window: a free throw 1 of 2 or a
        # timeout logged after the foul proves it dead that much longer, where a later entry used to only rename the window
        marked = max(at for at, _ in w.marks) if self.floor_from_marks and w.marks else None
        if self.noggin is not None:
            proven = self._proven(w, covered) if marked is None else max(self._proven(w, covered), marked - w.start)
            nb = self.noggin.lower_until(self, w, proven)     # the bound given it has been dead at least `proven` seconds
            if nb is not None:
                until = max(nb, w.start if covered is None else covered)
                return until if marked is None else max(until, marked)
        floor = w.start + FLOOR_S.get(lg, {}).get(w.kind, 0.0)
        if marked is not None:
            table = FLOOR_S.get(lg, {})     # each floor was measured from its own entry's stamp to the restart
            floor = max(at + table.get(kind, 0.0) for at, kind in w.marks)
        if covered is None:
            return floor
        return max(floor, covered)

    def _covered(self) -> float | None:
        """Real time up to which the data would already show a restart (None: no fresh poll yet)."""
        if self.fresh_at is None:
            return None
        lg = _lg(self.league)
        return self.fresh_at - max(self.lag, self.lag_floor) - PLAY_S.get(lg, 0.0) - SILENT_S.get(self.league, 0.0) - (
            0.0 if lg in (League.NFL, League.MLB) else 2.0)

    @staticmethod
    def _proven(w: Window, covered: float | None) -> float:
        """Seconds the data proves window w has stayed dead."""
        return 0.0 if covered is None else max(0.0, covered - w.start)

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
            if self.noggin is not None:
                med = self.noggin.median_until(self, w, self._proven(w, self._covered()))
                if med is not None:
                    table = med
            learned = self._learned_end(w)
            if learned is not None:
                return max(x, min(learned, table) if self.learned_cap else learned)
            return max(x, table)
        return x

    def walks(self, walk: Walk) -> list[Window]:
        """[landing bars] Basketball: the walk up court after each made shot or defensive rebound, in real time. Each
        window needs its next action in the data (or a fresh feed covering the whole window with none logged): a next
        action inside transition_s makes it a transition play, and no window. Never a dead window: nothing may LEAVE on
        one (dead_until and windows() don't see these)."""
        ms = self._order()
        cached = self._walks.get(walk)
        if cached is None:
            cached = self._walks[walk] = self._closed_walks(ms, walk)
        tail = self._open_walk(ms, walk)
        return cached if tail is None else cached + [tail]

    def _walk_opener(self, ms: list[Mark], i: int) -> bool:
        m = ms[i]
        return m.shot in ("made", "dreb") and m.hi > self.frozen_until

    def _next_action(self, ms: list[Mark], i: int) -> Mark | None:
        """The next entry after ms[i] that ends the walk: substitutions and the like happen during it."""
        for m in ms[i + 1:]:
            if m.hi > ms[i].hi and m.after != "substitution":
                return m
        return None

    def _closed_walks(self, ms: list[Mark], walk: Walk) -> list[Window]:
        walk_s, transition_s, after_s, guard_s = walk
        out: list[Window] = []
        for i in range(len(ms)):
            if not self._walk_opener(ms, i):
                continue
            nxt = self._next_action(ms, i)
            if nxt is None:
                continue               # the open tail: _open_walk (the data can't rule out a transition yet)
            t = ms[i].hi
            if nxt.lo - t < transition_s:
                continue               # a transition play: never land here (the prior safe point instead)
            start = t + after_s
            end = min(t + walk_s, nxt.lo - guard_s)
            if end > start:
                out.append(Window(start, end, "walk", exact=True))
        return out

    def _open_walk(self, ms: list[Mark], walk: Walk) -> Window | None:
        """The newest shot with nothing logged after it, only once the feed proves no action followed for the whole walk."""
        walk_s, after_s = walk[0], walk[2]
        i = len(ms) - 1
        while i >= 0 and ms[i].after == "substitution":
            i -= 1
        if i < 0 or not self._walk_opener(ms, i):
            return None
        covered = self._covered()
        t = ms[i].hi
        if covered is None or covered < t + walk_s:
            return None
        return Window(t + after_s, t + walk_s, "walk", exact=True)

    def entry_windows(self, walk: Walk | None = None) -> list[Window]:
        """Every window a picture may come up in: the dead windows, plus the walks after shots when walk is given."""
        ws = self.windows()
        if not walk or _lg(self.league) != League.NBA:
            return ws
        return sorted([*ws, *self.walks(walk)], key=lambda w: w.start)

    def landing(self, x0: float, x1: float, walk: Walk | None = None) -> Landing:
        """What the screen shows across real times [x0, x1] (the arrival, give or take the delay doubt). walk: also
        count the walk up court after a shot as a landing ("dead", kind "walk"): entries only, never a leave."""
        got = self._landing(x0, x1)
        if got.state != "dead" and walk and _lg(self.league) == League.NBA:
            for w in reversed(self.walks(walk)):
                if w.start > x0:
                    continue
                if x1 <= w.end:
                    return Landing("dead", "walk", w.end, "the walk up court after a shot")
                break
        return got

    def _landing(self, x0: float, x1: float) -> Landing:
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
               urgent: float | None = None, lag: float = 0.0, possession: bool = False,
               stall: tuple[float, float] = (0.0, 0.0), unknown_ok: bool = True,
               floor: float = 0.0, walk: Walk | None = None,
               after_play_s: float = 0.0) -> EntryPlan:
    """How to come into this game: as is (the picture lands on a dead ball), with a short rewind so
    it lands on the dead ball just before the restart we'd otherwise join mid-play, or not yet.

    mu, margin: the destination's delay and its doubt (2 sigma + the tune's own). The picture comes
    up somewhere between cost_lo (a fast flip) and cost seconds from now, so the whole stretch must
    land dead. With the full margin no landing fits a 30 s huddle once the delay's doubt passes
    ~10 s, so a second pass centres the landing in the dead window with `relaxed` doubt (default:
    none); such a landing is flagged in its `why`.

    lag: [landing lag] a fresh tune comes up as much as this further behind the live edge, never ahead
    (one-sided). Each pass's early side covers at least `lag`: a full 2-sigma margin usually already
    does (the honest-sim doubt covers the tune's own landing), a thin one (centred, urgent) does not.

    possession: [possession start] never wait for the next dead ball while one is in reach behind the
    picture: after the urgent pass, cue to the last dead ball before the current possession with no
    doubt beyond the landing lag (the hidden stick is positioned there before the flip).

    stall: [seek model] the picture lands k + stall behind for a rewind of k (whole steps); a pause cue
    on the hidden stick passes step ~0.5 and no stall (it plays up to the dead ball and pauses there).

    unknown_ok: [EXP-089] False: a landing the data can't call ("play may be live") never enters as is;
    it takes a rewind to a known dead ball or waits (rule #1).

    floor: [EXP-024 conformal floor] no pass lands on less doubt than this (s): the centred, urgent and
    possession passes are raised to it, and an unknown landing never enters as is. The plan's `slack`
    is scored either way, for calibration.

    walk: [landing bars] walk_params: basketball may also land in the walk up court after a shot, as is or
    by a rewind to the nearest earlier safe point (a transition play is skipped, whichever safe point is soonest).

    after_play_s: [landing bars] football: a landing this soon after the whistle would catch the replay's first seconds:
    wait while a later landing in the same window still fits (a preference: it never blocks the only landing)."""
    if floor > 0:
        unknown_ok = False
        margin = max(margin, floor)
        relaxed = None if relaxed is None else max(relaxed, floor)
        urgent = None if urgent is None else max(urgent, floor)
    plan = _plan_entry(dt, mu, margin, now, cost, can_rewind=can_rewind, step=step, max_back=max_back,
                       relaxed=relaxed, cost_lo=cost_lo, urgent=urgent, lag=lag, possession=possession,
                       stall=stall, unknown_ok=unknown_ok, floor=floor, walk=walk)
    if plan.ok and not plan.rewind_s and after_play_s > 0 and plan.landing is not None \
            and plan.landing.state == "dead" and plan.landing.kind != "walk":
        wait = settle_wait(dt, now + cost - mu, margin, after_play_s, plan.landing)
        if wait > 0:
            return EntryPlan(False, 0.0, plan.landing, f"waiting {wait:.0f}s for the replay ({plan.landing.why})")
    if plan.ok:
        x1 = now + cost - mu
        a = now + min(cost, cost if cost_lo is None else cost_lo) - mu - lag
        if plan.rewind_s:
            slo, shi = stall
            k = plan.rewind_s - (slo + shi) / 2
            a, x1 = a - k - shi, x1 - k - slo
        plan.slack = landing_slack(dt, a, x1)
    return plan


def settle_wait(dt: DeadTracker, x1: float, margin: float, after_play_s: float, land: Landing) -> float:
    """[landing bars] Seconds to hold a football landing that would come up in the replay after the whistle, if the
    same dead window still has room for a landing after it; 0: go now (the preference never blocks the only landing)."""
    for w in reversed(dt.windows()):
        if w.start > x1:
            continue
        settled = w.start + after_play_s
        until = w.end if w.end is not None else dt._open_until(w)
        return max(0.0, settled - x1) if x1 < settled and settled + margin <= until else 0.0
    return 0.0


def landing_slack(dt: DeadTracker, a: float, b: float, cap: float = 60.0) -> float:
    """[EXP-024] The widest m (s) for which all of [a - m, b + m] lands dead: 0 when [a, b] doesn't."""
    if dt.landing(a, b).state != "dead":
        return 0.0
    lo, hi = 0.0, cap
    if dt.landing(a - hi, b + hi).state == "dead":
        return cap
    while hi - lo > 0.25:
        m = (lo + hi) / 2
        lo, hi = (m, hi) if dt.landing(a - m, b + m).state == "dead" else (lo, m)
    return lo


def _plan_entry(dt: DeadTracker, mu: float, margin: float, now: float, cost: float, *,
                can_rewind: bool, step: float, max_back: float, relaxed: float | None, cost_lo: float | None,
                urgent: float | None, lag: float, possession: bool, stall: tuple[float, float],
                unknown_ok: bool, floor: float, walk: Walk | None = None) -> EntryPlan:
    x1 = now + cost - mu
    x00 = now + min(cost, cost if cost_lo is None else cost_lo) - mu

    def lo(m: float) -> float:              # the early edge for margin m, widened to cover the landing lag
        return x00 - max(0.0, lag - m)
    x0 = lo(margin)
    here = dt.landing(x0 - margin, x1 + margin, walk=walk)
    if here.state == "dead" or (here.state == "unknown" and not can_rewind and unknown_ok):
        # Football/baseball with no play known to be on screen: the leave gate's own rule.
        return EntryPlan(True, 0.0, here, here.why)
    strict = _rewind(dt, x0, x1, margin, step, max_back, stall, walk) if can_rewind else None
    if relaxed is not None and relaxed < margin:
        soft = dt.landing(lo(relaxed) - relaxed, x1 + relaxed, walk=walk)
        if soft.state == "dead" and (strict is None or strict.rewind_s > step + stall[1]):
            return EntryPlan(True, 0.0, soft, soft.why + " (centred)")
        loose = _rewind(dt, lo(relaxed), x1, relaxed, step, max_back, stall, walk) if can_rewind else None
        # The full margin wins unless it costs more than one extra seek step.
        if loose is not None and (strict is None or strict.rewind_s > loose.rewind_s + step):
            loose.why += " (centred)"
            return loose
    if strict is not None:
        return strict
    if urgent is not None and urgent < min(margin, relaxed if relaxed is not None else margin):
        # Crunch time / your team: every second waiting is a play missed. Take the dead ball the
        # data puts the landing on even if the delay's doubt can't rule out a few seconds' error.
        last = dt.landing(lo(urgent) - urgent, x1 + urgent, walk=walk)
        if last.state == "dead":
            return EntryPlan(True, 0.0, last, last.why + " (urgent)")
        quick = _rewind(dt, lo(urgent), x1, urgent, step, max_back, stall, walk) if can_rewind else None
        if quick is not None:
            quick.why += " (urgent)"
            return quick
    if possession and can_rewind:
        cue = _rewind(dt, lo(floor), x1, floor, step, max_back, stall, walk)
        if cue is not None:
            cue.why += " (possession start)"
            return cue
    if here.state == "unknown" and unknown_ok:
        return EntryPlan(True, 0.0, here, here.why)
    return EntryPlan(False, 0.0, here, f"waiting for a dead ball to come in ({here.why})")


def _rewind(dt: DeadTracker, x0: float, x1: float, m: float, step: float, max_back: float,
            stall: tuple[float, float] = (0.0, 0.0), walk: Walk | None = None) -> EntryPlan | None:
    """The shortest rewind k (whole seek steps) that puts all of [x0 - k - stall, x1 - k - stall] on a
    dead ball, doubt m. [seek model] each seek also stalls the picture stall[0]..stall[1] s, which lands
    it that much further behind: the plan holds the whole range and rewind_s is k + the mid stall."""
    slo, shi = stall
    if max_back < step:
        return None
    for w in reversed(dt.entry_windows(walk)):
        if w.start > x1:
            continue
        until = w.end if w.end is not None else dt._open_until(w)
        if x0 - until > max_back + m:
            break
        # The latest landing in the window: the picture comes up just before the restart.
        k = max(step, math.ceil((x1 + m - slo - until) / step - 1e-9) * step)
        if k > max_back:
            break
        if x0 - k - shi - m < w.start:
            continue
        land = dt.landing(x0 - k - shi - m, x1 - k - slo + m, walk=walk)
        if land.state != "dead":
            continue
        back = k + (slo + shi) / 2
        return EntryPlan(True, back, land, f"rewinding {back:.0f}s to the dead ball before the play")
    return None
