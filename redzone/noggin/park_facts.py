"""PARK facts: one LIVE basketball game's facts for the PARK planner, built row by row from the ESPN feed, honestly.

Feed it the game's play rows as they arrive (`GameFacts.update(summary)` with each fresh ESPN summary, or `add(row)`).
At any moment it answers from the rows seen so far only:

- `settled`: the feed time before which every second's answer is final. The grader (`redzone.grading.dead_at`) dates a
  restart from the first later row of the same period whose game clock has moved (else 26 s before the next play row,
  `RESUME_LEAD`), so a second is final once the rows that date the stoppage it sits in are in, and a play row after it
  is in (with none, the grader calls the ball dead to the end of the game: a game can end with its clock run out and
  no row logged). Infinite once the game is over. Before `settled` the
  dead and live stretches are exactly the grader's whole-game answer (`scripts/park_ceiling.truth_of`; proven on
  recorded dev nights by `tests/test_park_facts.py` and `scripts/park_facts_check.py`).
- `open`: the stoppage still running at the last row, if no row has dated its end yet: since when, and the earliest
  moment play could have restarted (the anchor less `RESUME_LEAD`: the anchor is the next play row when one is in,
  else the feed's edge). Once any later row of that period shows the clock moved, the stoppage is never open again:
  its restart is dated from that row (ledger L25: reading it the grader's way said "still stopped" until a later play
  row existed; 2 mid-play cuts in 324 flips until the stand-in row fix below).
- shots and their windows, high-leverage spans (`scoring.engine.high_leverage`, the decider's copy of the grader's
  close-and-late rule), leverage rows and graded value (`moments.value2`, `moments.detect`).
- `planner_view(end)`: the game in the dict shape the PARK core's arrays read (`Arr`), with what is not final
  marked: seconds past `settled` come from the rows plus one stand-in play row `ghost` seconds after the last (restarts
  the feed can already date come out as they will stand), the time after the last row is not dead (unknown, so never a
  legal place) except an open stoppage, which runs on with no end.

Also the hard guards the PARK controller asks before any jump (`refusal`), the per-game padding cut into a game's
arrays (`pad_arr`), how far an open stoppage is trusted (`sure_until`), and Eyes's spans put over the feed's
(`merge_eyes`), ported from the local show runner (`tools/park_live/park_live.py`).

Basketball (NBA and college) and [PARK-NFL] football (NFL and college). Football reads the grader's own football rule
(`grading.dead_at`): each action row covers the 7 s before its stamp (`PLAY_LEN`), everything else is dead, and a restart
is the next action row less those 7 s; there is no shot window. One guard on top, never looser than the grader: a dead
stretch more than `play_clock_s` + `snap_slack_s` after an action row, with no stoppage, marker or score row to explain
it, is not trusted (the play clock would have run out; live feeds hold rows stamped minutes late that drag later rows
forward, Nov 10). Hockey facts are not written.
Pure: no device, no network, no file. It calls the grader's functions and never changes them. Nothing in the app
imports it yet: the PARK controller (a later unit) will, behind `park.enabled`, reading the ESPN rows the engine
already polls (`engine.summaries`). Numbers are fields of `FactsParams` (`park.<name>`; rule K2).

Why: the planner was proven on facts read with hindsight from whole recorded games; the app has only the rows so far,
and every fact it is handed must be one it could know at that second, or Rule #1 breaks at the edges.
"""
from __future__ import annotations

import bisect
import math
from collections.abc import Callable
from dataclasses import dataclass, field, fields

from redzone.grading import RESUME_LEAD, dead_at, held_stop, idx, is_action, play_clock, plen, raw_type
from redzone.models import GameState, League, Phase, Stoppage, Team
from redzone.moments import value2
from redzone.moments.detect import detect as detect_moments
from redzone.noggin import arbiter
from redzone.noggin.park_core import stretches
from redzone.noggin.park_timing import GameTiming
from redzone.scoring.engine import high_leverage
from redzone.sources import espn

SCAN_ROWS = 40                     # grading.resume_after looks this many rows past the next play row for a clock drop


@dataclass(frozen=True)
class FactsParams:
    """Facts numbers (seconds). Defaults are park_ceiling's and the show runner's."""
    high_play_s: float = 7.0        # a high-leverage play covers this long before its stamp (veto_audit.PLAY_LEN_S)
    shot_window_s: float = 4.0      # this long after any shot counts as a legal point (the grader gives a made basket 4 s)
    ghost_s: float = 60.0           # the stand-in play row sits this far past the last row
    open_lead_s: float = -1.0       # an open stoppage may have restarted this long before its anchor; < 0 = the grader's RESUME_LEAD
    stop_max: tuple = ()            # the sanity gate: ((stoppage kind, seconds), ...) = the longest a dead ball of each kind can
    #                                 plausibly last; what a dead stretch holds past that is UNKNOWN, not dead. () = no gate (the
    #                                 loader's and the grader's own reading); the show sets park.show.stop_max
    possession_cap_s: float = 0.0   # a dead stretch holding a review's RULING (the stoppage is over), with no row after its last
    #                                 one yet or the next one stamped more than late_row_s later, is dead only this long from its
    #                                 last row; the rest is UNKNOWN (the feed lost or late-stamped the plays that followed). 0 =
    #                                 off (the loader's and the grader's own reading); the show sets park.possession_cap_s
    late_row_s: float = float("inf")   # a row stamped more than this after a row that FOLLOWS it in the feed's order is a late
    #                                 edit (a substitution row re-stamped half an hour on): it takes the stamp of the row before
    #                                 it. The show sets 60 (park.late_row_s). Off here (inf) = the repo loader's own stamps, row
    #                                 for row, which is what the recorded-night checks of these facts compare against
    football_lead_s: float = 15.0   # [PARK-NFL] football: an open stoppage may have restarted this long before the feed's edge
    #                                 (the 7 s play the grader gives a row, plus the show's 8 s feed lag)
    play_clock_s: float = 40.0      # [PARK-NFL] NFL play clock: the next snap comes within this of a whistle unless a row says why
    snap_slack_s: float = 5.0       # [PARK-NFL] ... plus this (spotting the ball, the stamp's own slack)
    play_after_s: float = 12.0      # [PARK-NFL] picture model: ESPN's NFL stamp marks the SNAP (Eyes, Prime TNF replay JAX @ BAL,
    #                                 Nov 11: snap - stamp mean +0.02 s, sd 0.40, worst +1.39, n=41), so the seconds after a
    #                                 stamp are play on the picture. Scrimmage plays, snap to dead ball: run n=20 longest
    #                                 9.54 s, pass complete n=6 longest 9.85, incomplete n=4 longest 5.11. Not legal for this
    #                                 long after a scrimmage stamp (worst snap + longest play, rounded up)
    play_after_special_s: float = 15.0   # [PARK-NFL] kickoffs, punts, field goals, extra points, two-point tries: NOT measured

    def lead(self, league: League) -> float:
        if is_football(league):
            return self.football_lead_s
        return self.open_lead_s if self.open_lead_s >= 0 else float(RESUME_LEAD[league])

    @property
    def clock_cap(self) -> float:
        return self.play_clock_s + self.snap_slack_s

    @classmethod
    def from_config(cls, get: Callable[..., object]) -> "FactsParams":
        """Each field from `park.<name>`, or its default. A bad value keeps the default."""
        kw = {}
        for f in fields(cls):
            d = f.default
            if not isinstance(d, float):
                continue
            v = get(f"park.{f.name}", d)
            kw[f.name] = float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else d
        caps = get("park.show.stop_max", None)
        if isinstance(caps, dict):
            kw["stop_max"] = tuple(sorted((str(k), float(v)) for k, v in caps.items()
                                          if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0))
        return cls(**kw)


FACTS = FactsParams()


def is_football(league: League | str) -> bool:
    return League(league).base == League.NFL


def value_of(kind: str) -> float:
    """A must-see's weight (veto_audit.value_of): a score 5, a red-zone trip or turnover 3, any other 1."""
    return 5.0 if kind in arbiter.SCORE_KINDS else 3.0 if kind in ("rz_trip", "turnover") else 1.0


def is_high_leverage(league: League, p: dict) -> bool:
    """Is the game at a high-leverage moment at this row (close and late, or overtime)? The decider's copy of the
    grader's rule (`scoring.engine.high_leverage`), read from the row's period, clock and score."""
    hs, as_ = espn._int(p.get("homeScore")), espn._int(p.get("awayScore"))
    g = GameState(league, "", Team("H", score=hs), Team("A", score=as_), period=espn._int(espn._get(p, "period", "number")),
                  clock_s=play_clock(league, p))
    return high_leverage(g)


@dataclass(frozen=True)
class OpenStop:
    """The stoppage running at the last row with no end dated yet (feed times)."""
    since: float                    # its first dead second
    row: float                      # the feed time of its latest dead-ball row
    earliest_restart: float         # the soonest play could have restarted, by the grader's own lead
    anchor: str                     # "row": a later play row is in (its clock has not moved); "edge": none yet
    brk: bool                       # a commercial break started it
    basket: bool = False            # it is only a made basket's 4 s (no whistle): it never runs on past them


@dataclass
class Facts:
    """What is known of one game at one moment (feed times; `dead` and `live` are final, closed stretches only)."""
    gid: str
    t0: float
    last: float                     # feed time of the last row
    edge: float                     # the feed has been read up to here
    settled: float                  # every second before this is final
    dead: list[tuple[float, float]] = field(default_factory=list)
    brk: list[bool] = field(default_factory=list)
    live: list[tuple[float, float]] = field(default_factory=list)
    open: OpenStop | None = None
    shots: list[float] = field(default_factory=list)
    shot_windows: list[tuple[float, float]] = field(default_factory=list)
    hl: list[tuple[float, float]] = field(default_factory=list)
    lev: list[tuple] = field(default_factory=list)


def stamp_of(p: dict, last: float | None) -> float | None:
    """One row's feed time as the repo's loader makes it (`Replay.load`): its wallclock, or 5 s after the row before;
    never before the row before."""
    t = espn._ts(p.get("wallclock"))
    t = t if t is not None else (last + 5 if last is not None else None)
    return max(t, last) if t is not None and last is not None else t


def stamps_of(rows: list, late_row_s: float) -> tuple[list, list[int]]:
    """Every row's feed time, with late edits put back: (stamps, the rows that were re-stamped).

    The loader's rule (`stamp_of`) never lets a stamp go backwards. That is right for a feed that slips a second, and
    WRONG for an edit: the feed re-stamps an old row (a substitution, half an hour late; 30 such rows in four games on
    Nov 10) with the time of the edit, and every row after it in the feed's order was then pulled up to that time, so
    half an hour of play read as one long dead ball (live run 4, flip 4: a leave in live play). So a row stamped more
    than `late_row_s` after the EARLIEST stamp of any row that follows it is an edit: it takes the stamp of the row
    before it and drags nothing. Stamps are None for a list whose first row has none (the loader drops such a game)."""
    raw, last = [], None
    for p in rows:
        t = espn._ts(p.get("wallclock"))
        t = t if t is not None else (last + 5 if last is not None else None)
        raw.append(t)
        last = t if t is not None else last
    if not raw or raw[0] is None:
        return raw, []
    fixed, least = [], float("inf")
    after = [0.0] * len(raw)
    for i in range(len(raw) - 1, -1, -1):
        after[i] = least                                     # the earliest stamp of any LATER row
        least = min(least, raw[i])
    for i in range(1, len(raw)):
        if raw[i] > after[i] + late_row_s:
            raw[i] = raw[i - 1]
            fixed.append(i)
    for i in range(1, len(raw)):
        raw[i] = max(raw[i], raw[i - 1])
    return raw, fixed


class GameFacts:
    """One game's facts, built from its rows as they arrive. Rows are only ever appended; if the feed rewrites an
    earlier row, `update` starts over from the first row."""

    def __init__(self, league: League | str, gid: str = "", params: FactsParams = FACTS):
        self.league = League(league)
        self.football = is_football(self.league)
        if self.league.base != League.NBA and not self.football:
            raise ValueError(f"PARK facts are written for basketball and football only, not {self.league.value}")
        self.gid, self.p = str(gid), params
        self.edge_hint: float | None = None       # the feed time the feed has been read up to (FactsFeed sets it before each poll)
        self.data: dict | None = None
        self._cfg2 = value2.config()
        self._reset()

    def _reset(self) -> None:
        self.final = False
        self.plays: list[dict] = []
        self.wall: list[float] = []
        self.raw: list[dict] = []                 # the rows as given (the grader's value code reads the summary's own)
        self.flags: list[int] = []                # per second from t0, FINAL ones only: 0 play, 1 dead, 2 dead in a break
        self.settled_k = 0                        # every row before this one leaves no dead ball, or one whose restart is dated
        self.last_action: int | None = None
        self.unplaced = False                     # the first row had no stamp: the loader drops such a game
        self.restamped = 0                        # late-edit rows put back to the stamp of the row before them (stamps_of)
        self.dragged: list[float] = []            # the stamps USED for rows whose own stamp is more than late_row_s earlier
        self.shots: list[float] = []
        self.hl: list[tuple[float, float]] = []
        self.lev: list[tuple] = []
        self._settled: float | None = None

    # ------------------------------------------------------------------------------------------ rows in
    def update(self, summary: dict, final: bool | None = None) -> int:
        """Take a fresh ESPN summary; returns how many new rows came in. `final` = the game is over (read from the
        summary when not given)."""
        plays = espn.flatten_plays(self.league, summary)
        n = len(self.raw)
        if len(plays) < n or plays[:n] != self.raw or (self.final and len(plays) > n):
            self._reset()
            n = 0
        for p in plays[n:]:
            self.add(p)
        self.data = summary
        if final is None:
            gs = espn.parse_summary(self.league, summary)
            final = gs is not None and gs.phase == Phase.POST
        if final:
            self.set_final()
        return len(plays) - n

    def add(self, p: dict, at: float | None = None) -> None:
        """One more row, in feed order. A row stamped far EARLIER than the row before it shows that an earlier row was a
        late edit which the never-backwards rule let drag the stamps up: every stamp is then made again (`stamps_of`)."""
        if self.final:
            raise ValueError("the game is over: no row may follow")
        own = espn._ts(p.get("wallclock"))
        if at is None and self.wall and own is not None and own < self.wall[-1] - self.p.late_row_s and not self.unplaced:
            rows = self.raw + [p]
            stamps, fixed = stamps_of(rows, self.p.late_row_s)
            if fixed:
                data = self.data
                self._reset()
                self.data = data
                for q, t in zip(rows, stamps):
                    self.add(q, t)
                self.restamped = len(fixed)
                return
        t = stamp_of(p, self.wall[-1] if self.wall else None) if at is None else at
        if (at is None and own is not None and self.wall and self.edge_hint is not None
                and own > self.edge_hint + self.p.late_row_s):
            # a row stamped well past what the feed has been read up to cannot be on time: it is a late edit (night 20261023
            # LAC @ LAL Q1 7:03, re-stamped 19 minutes on, dated the end of a ruled review that far ahead). It takes the
            # stamp of the row before it, and what rests on it is not known (dragged)
            t = self.wall[-1]
            self.dragged.append(t)
            self.restamped += 1
        self.raw.append(p)
        if t is None or self.unplaced:
            self.unplaced = True
            return
        if own is not None and t - own > self.p.late_row_s:
            self.dragged.append(t)                           # used far later than the feed stamped it: what rests on it is not known
        i = len(self.plays)
        self.plays.append(p)
        self.wall.append(t)
        lg = self.league
        if is_action(lg, p):
            self.last_action = i
        if is_high_leverage(lg, p):
            self.hl.append((t - self.p.high_play_s, t))
        hs, as_ = espn._int(p.get("homeScore")), espn._int(p.get("awayScore"))
        if hs is not None and as_ is not None:
            try:
                left, ot = value2.seconds_left(lg.value, espn._int(espn._get(p, "period", "number")) or 0,
                                               espn._clock_s(espn._get(p, "clock", "displayValue")), self._cfg2)
                self.lev.append((t, float(value2.leverage(lg.value, abs(hs - as_), left, ot, self._cfg2)), abs(hs - as_), left))
            except (KeyError, TypeError, ValueError):
                self.lev.append((t, 1.0, abs(hs - as_), -1.0))
        if not self.football and p.get("shootingPlay") and "free throw" not in raw_type(p):
            self.shots.append(t)
        self._settled = None

    def set_final(self) -> None:
        """The game is over: every row is in, so every answer is the grader's whole-game one (the final seconds already
        filled were each dated with a real play row after them, so they stand)."""
        if not self.final:
            self.final, self._settled = True, None

    # ------------------------------------------------------------------------------------------ the grader's restart, and when it is final
    def _dead_row(self, i: int) -> bool:
        """Does row i leave the ball dead (a stoppage, a held stoppage, or a made basket's 4 s)?"""
        lg, p = self.league, self.plays[i]
        if self.football:
            return True                                      # football: after every row the ball is dead until the next snap
        stop = espn.stoppage_for(lg, p)
        if stop == Stoppage.NONE:
            stop = held_stop(lg, self.wall, self.plays, i)
        return stop != Stoppage.NONE or bool(p.get("scoringPlay"))

    def _restart(self, i: int) -> tuple[float | None, int | None, bool]:
        """(restart time, next play row, final) for the dead ball row i leaves, from the rows seen so far, dated the way
        `grading.resume_after` dates it. Dated: a later row of the same period shows the game clock moved (the first one
        pins the restart), or the next play row is in and the grader's look-ahead after it is in or cut by a new period
        (then it is the play row less RESUME_LEAD). Final = dated AND a play row is in after row i: with none, the
        grader calls the ball dead to the end of the game (a game can end with its clock run out and no row logged).
        Undated: (None, the next play row or None, False)."""
        lg, wall, plays, n = self.league, self.wall, self.plays, len(self.plays)
        j = next((k for k in range(i + 1, n) if is_action(lg, plays[k])), None)
        if self.football:                                    # grading.resume_after: the next action row less its play
            return (max(wall[i], wall[j] - plen(lg)), j, True) if j is not None else (None, None, False)
        per_i, c0 = espn._int(espn._get(plays[i], "period", "number")), play_clock(lg, plays[i])
        cut = False
        for k in range(i + 1, n if j is None else min(n, j + SCAN_ROWS)):
            if espn._int(espn._get(plays[k], "period", "number")) != per_i:
                cut = True
                break
            c = play_clock(lg, plays[k])
            if c is not None and c0 is not None and c < c0 - 0.05:
                return max(wall[i], wall[k] - (c0 - c)), j, j is not None
        if j is not None and (cut or c0 is None or j + SCAN_ROWS <= n):
            return max(wall[i], wall[j] - RESUME_LEAD[lg]), j, True
        return None, j, False

    def _ghost(self) -> tuple[list[float], list[dict]]:
        """The rows plus one stand-in play row `ghost_s` after the last (none before the first play row). Its clock is
        blank, so it moves no clock: it only lets the grader date a restart a clock drop already shows."""
        if self.last_action is None:
            return self.wall, self.plays
        g = {**self.plays[self.last_action], "clock": {}, "scoringPlay": False, "shootingPlay": False,
             "homeScore": None, "awayScore": None}
        return self.wall + [self.wall[-1] + self.p.ghost_s], self.plays + [g]

    def _flag(self, ws: list[float], ps: list[dict], st: float) -> int:
        d, b, _ = dead_at(self.league, ws, ps, st)
        if d and self.football and (not self._clock_ok(ws, ps, st) or self._after_snap(ws, ps, st)):
            return 0                                         # past the play clock unexplained, or the play after a snap stamp
        return 2 if d and b else 1 if d else 0

    def play_after(self, p: dict) -> float:
        """[football, picture model] How long after this action row's stamp (its snap) the play may still run."""
        t = raw_type(p)
        special = any(k in t for k in ("kickoff", "punt", "field goal", "extra point", "two-point", "2pt"))
        return self.p.play_after_special_s if special else self.p.play_after_s

    def _after_snap(self, ws: list[float], ps: list[dict], st: float) -> bool:
        """[football, picture model] Is st within its play's play_after after an action row's stamp (the stamp is the snap)?"""
        i = idx(ws, st)
        top = max(self.p.play_after_s, self.p.play_after_special_s)
        while i >= 0 and st - ws[i] <= top:
            if is_action(self.league, ps[i]) and st - ws[i] <= self.play_after(ps[i]):
                return True
            i -= 1
        return False

    def _explained(self, ps: list[dict], a: int, i: int) -> bool:
        """[football] Does a row from action row a to row i explain a long dead ball (a stoppage, a marker such as a
        timeout or quarter end, or a score)?"""
        lg = self.league
        return i > a or espn.stoppage_for(lg, ps[a]) != Stoppage.NONE or bool(ps[a].get("scoringPlay"))

    def _clock_ok(self, ws: list[float], ps: list[dict], st: float) -> bool:
        """[football] Is a dead second st inside what the play clock allows after the last action row before it (or
        explained by a row)? Before the first action row: yes (the kickoff wait is the grader's)."""
        i = idx(ws, st)
        if i < 0:
            return True
        a = i
        while a >= 0 and not is_action(self.league, ps[a]):
            a -= 1
        if a < 0 or st - ws[a] <= self.p.clock_cap:
            return True
        return self._explained(ps, a, i)

    def clock_cap_at(self, since_row: int | None = None) -> float | None:
        """[football] The feed time past which an open stoppage is not trusted (the last action row + the play clock),
        or None when a row explains it (or not football)."""
        if not self.football or not self.plays:
            return None
        i = len(self.plays) - 1
        a = i
        while a >= 0 and not is_action(self.league, self.plays[a]):
            a -= 1
        if a < 0 or self._explained(self.plays, a, i):
            return None
        return self.wall[a] + self.p.clock_cap

    def _settle(self) -> float:
        """Move the final frontier forward over the rows now dated and fill the final seconds; returns `settled`."""
        if self._settled is not None:
            return self._settled
        wall, n = self.wall, len(self.plays)
        if not n:
            self._settled = float("-inf")
            return self._settled
        t0 = wall[0]
        if self.final:
            count, (ws, ps) = int(wall[-1] - t0) + 1, (wall, self.plays)     # park_ceiling.truth_of's own grid
        else:
            k = self.settled_k
            while k < n and (not self._dead_row(k) or self._restart(k)[2]):
                k += 1
            self.settled_k = k
            upto = wall[k] if k < n else wall[-1]          # a later row may still share the last row's second
            count, (ws, ps) = max(0, math.ceil(upto - t0)), self._ghost()
        for j in range(len(self.flags), count):
            self.flags.append(self._flag(ws, ps, t0 + j))
        self._settled = t0 + len(self.flags) if not self.final else float("inf")
        return self._settled

    def _tentative(self) -> list[int]:
        """Final flags, then the seconds up to the last row (and a made basket's 4 s past it) read with the stand-in row."""
        flags = list(self.flags)
        if self.final or not self.plays:
            return flags
        t0, ws, ps = self.wall[0], *self._ghost()
        for j in range(len(flags), int(self.wall[-1] + self.p.shot_window_s - t0) + 1):
            flags.append(self._flag(ws, ps, t0 + j))
        return flags

    # ------------------------------------------------------------------------------------------ answers
    def settled(self) -> float:
        return self._settle()

    def facts(self, edge: float | None = None) -> Facts:
        """What is known now. `edge` = the feed time the feed has been read up to (default: the last row's)."""
        sett = self._settle()
        if not self.plays:
            e = float("-inf") if edge is None else edge
            return Facts(self.gid, e, e, e, sett)
        t0, last = self.wall[0], self.wall[-1]
        edge = max(last, edge if edge is not None else last)
        dead, brk, live = [], [], []
        for f, a, b in _runs(self.flags, t0):
            if b is None and not self.final:
                break                                        # still running at `settled`: its end is not known
            b = last if b is None else b                     # the game is over: the last stretch closes at its last row
            if f:
                dead.append((a, b))
                brk.append(f == 2)
            else:
                live.append((a, b))
        return Facts(self.gid, t0, last, edge, sett, dead, brk, live, self._open(edge), list(self.shots),
                     [(s, s + self.p.shot_window_s) for s in self.shots], list(self.hl), list(self.lev))

    def _open(self, edge: float, flags: list[int] | None = None) -> OpenStop | None:
        """The stoppage running at the last row when no row has dated its end yet (None once any later row of the period
        shows the clock moved, or a play row and the grader's look-ahead after it are in)."""
        if self.final or not self.plays:
            return None
        lg, i = self.league, len(self.plays) - 1
        while i >= 0 and not self._dead_row(i) and not is_action(lg, self.plays[i]):
            i -= 1                                           # bookkeeping rows after a whistle belong to it
        if i < 0 or not self._dead_row(i):
            return None
        res, j, _ = self._restart(i)
        if res is not None:
            return None                                      # its restart is dated: the clock has moved, it is not open
        p, w = self.plays[i], self.wall[i]
        lead = self.p.lead(lg)
        if self.football:                                    # no basket window; the restart can be no earlier than edge - lead
            cap = self.clock_cap_at()
            a = i
            while a >= 0 and not is_action(lg, self.plays[a]):
                a -= 1
            since = max(w, self.wall[a] + self.play_after(self.plays[a])) if a >= 0 else w     # its play after the snap stamp is not dead
            soon = max(since, (self.wall[j] if j is not None else edge) - lead)
            return OpenStop(since, w, soon if cap is None else min(soon, cap), "row" if j is not None else "edge", False)
        only_basket = espn.stoppage_for(lg, p) == Stoppage.NONE and held_stop(lg, self.wall, self.plays, i) == Stoppage.NONE
        if only_basket and edge - w > self.p.shot_window_s:
            return None                                      # a made basket's 4 s are over: play runs unless a row says not
        flags = self._tentative() if flags is None else flags
        t0 = self.wall[0]
        k = min(len(flags) - 1, int(w - t0))
        if k < 0 or not flags[k]:
            since, brk = w, False
        else:
            a = k
            while a > 0 and flags[a - 1]:
                a -= 1
            since, brk = t0 + a, any(f == 2 for f in flags[a:k + 1])
        anchor_t, anchor = (self.wall[j], "row") if j is not None else (edge, "edge")
        soon = max(w, anchor_t - lead)
        return OpenStop(since, w, min(soon, w + self.p.shot_window_s) if only_basket else soon, anchor, brk, only_basket)

    def value(self) -> tuple[list[tuple], list[tuple]]:
        """(v2, big): the graded plays (time, value, replay value) and the must-sees (time, weight), graded on the last
        summary given to `update` with the rows seen so far. ([], []) before any summary."""
        if self.data is None:
            return [], []
        lg, data = self.league.value, {**self.data, "plays": list(self.raw)}
        try:
            v2 = sorted((m.t, float(m.value), float(m.replay)) for m in value2.grade(lg, data, self.gid, cfg=self._cfg2))
        except (KeyError, TypeError, ValueError):
            v2 = []
        big = sorted((m.t, value_of(m.kind)) for m in detect_moments(lg, data, self.gid).moments
                     if arbiter.trigger_rank(lg, m.kind) != arbiter.NOT_FLOOR)
        return v2, big

    def planner_view(self, end: float, edge: float | None = None, with_value: bool = True) -> tuple[dict, float | None]:
        """The game in the PARK core's dict shape (`gid, t0, t1, dead, brk, shots, big, hl, lev, v2`) and the feed time from
        which its dead time is not final (None = all final). Over: the grader's whole-game facts. Not over: the final
        seconds as they stand, later ones read with the stand-in play row; nothing after the last row is dead (but a
        made basket's 4 s) except an open stoppage, which runs to `end`."""
        sett = self._settle()
        v2, big = self.value() if with_value else ([], [])
        if not self.plays:
            return {"gid": self.gid, "t0": end, "t1": end, "dead": [], "brk": [], "shots": [], "big": [], "hl": [], "lev": [],
                    "v2": []}, None
        t0, last = self.wall[0], self.wall[-1]
        g = {"gid": self.gid, "t0": t0, "t1": last if self.final else end, "shots": list(self.shots), "big": big,
             "hl": list(self.hl), "lev": list(self.lev), "v2": v2}
        flags = self._tentative()
        dead, brk = [], []
        for f, a, b in _runs(flags, t0):
            if f:
                dead.append((a, last if b is None and self.final else t0 + len(flags) if b is None else b))
                brk.append(f == 2)
        op = None if self.final else self._open(edge if edge is not None else last, flags)
        if op is not None and not op.basket:                 # (a bare made basket is dead for its 4 s only, whatever the feed does next)
            if dead and dead[-1][0] <= op.since < dead[-1][1] + 1:
                dead[-1] = (dead[-1][0], end)
            else:
                dead.append((op.since, end))
                brk.append(op.brk)
        if self.dragged:
            # the sanity gate, apart from the re-stamping: a dead ball that rests on a row used far later than the feed
            # stamped it is UNKNOWN, not dead (never a place to cut)
            keep = [k for k, (a, b) in enumerate(dead) if not any(a - 1.0 <= w <= b for w in self.dragged)]
            dead, brk = [dead[k] for k in keep], [brk[k] for k in keep]
        if (self.p.stop_max or self.p.possession_cap_s > 0) and not self.final:
            dead = self._capped(dead)
        g["dead"], g["brk"] = dead, brk
        return g, (None if self.final else next((a for a, b in dead if b > sett), None))

    def _capped(self, dead: list) -> list:
        """The sanity gate (`stop_max`), on every dead stretch, final or still open: "no row says play" only means "still
        stopped" for as long as a stoppage of its kind can plausibly last. A stretch longer than that (the feed went quiet,
        lost a row, or its stamps were dragged by a late edit) is dead only up to the cap; the rest is unknown."""
        caps = dict(self.p.stop_max)
        least = caps.get("whistle", min(caps.values())) if caps else float("inf")
        pos = self.p.possession_cap_s
        out, lg = [], self.league
        for a, b in dead:
            i0, i1 = bisect.bisect_left(self.wall, a - 1.0), bisect.bisect_right(self.wall, b)
            kinds = {espn.stoppage_for(lg, self.plays[i]).value for i in range(i0, i1)}
            cap = max([caps.get(k, least) for k in kinds if k != "none"] or [least])
            end = min(b, a + cap)
            if pos > 0 and i1 - 1 >= i0 and any(self._ruled(i) for i in range(i0, i1)):
                nxt = self.wall[i1] if i1 < len(self.wall) else float("inf")
                if nxt - self.wall[i1 - 1] > self.p.late_row_s or nxt == float("inf"):
                    # silence after a short dead ball's last row, with no row yet (or the next one stamped implausibly
                    # late): dead only possession_cap_s from that row, the rest unknown
                    end = min(end, max(a, self.wall[i1 - 1] + pos))
            out.append((a, end))
        return out

    def _ruled(self, i: int) -> bool:
        """Is row i a review's ruling (a challenge overturned, upheld or standing)? The stoppage it explained is over: what
        follows is the inbound and play, so silence after the ruling and the plays it re-logs is not a dead ball for long."""
        p = self.plays[i]
        if espn.stoppage_for(self.league, p) != Stoppage.REVIEW:
            return False
        t = (raw_type(p) + " " + str(p.get("text") or "")).lower()
        return any(w in t for w in ("overturned", "upheld", "stands", "confirmed"))


def _runs(flags: list[int], t0: float) -> list[tuple[int, float, float | None]]:
    """Runs of dead (flag 1 or 2, kept as the highest seen: 2 = a break somewhere in it) and play (0), strung exactly as
    park_ceiling.truth_of strings them: (kind, first second, first second after it), None for a run still going at the
    last flag."""
    out, cur, a, brk = [], None, 0, False
    for k, f in enumerate(flags):
        d = 1 if f else 0
        if cur is None:
            cur, a, brk = d, k, f == 2
        elif d != cur:
            out.append((2 if cur and brk else cur, t0 + a, t0 + k))
            cur, a, brk = d, k, f == 2
        else:
            brk = brk or f == 2
    if cur is not None:
        out.append((2 if cur and brk else cur, t0 + a, None))
    return out


def shift(g: dict, dt: float) -> dict:
    """The same game's view with every time moved by dt (park_ceiling.shift)."""
    return {**g, "t0": g["t0"] + dt, "t1": g["t1"] + dt, "dead": [(a + dt, b + dt) for a, b in g["dead"]],
            "shots": [x + dt for x in g["shots"]], "big": [(t + dt, v) for t, v in g["big"]], "hl": [(a + dt, b + dt) for a, b in g["hl"]],
            "lev": [(x[0] + dt,) + tuple(x[1:]) for x in g["lev"]], "v2": [(x[0] + dt,) + tuple(x[1:]) for x in g["v2"]]}


def on_stick(g: dict, timing: GameTiming, show_delay: float = 0.0) -> dict:
    """The game's view (feed times) moved to the wall time a stick at its live edge shows it, plus the show's own delay:
    every time + K + the live lag (the wide prior's delay when the game has no K) + show_delay (the show runner's
    LiveFeed shift). The padding for this game is `timing.grid_padding(read)`, not part of the move."""
    return shift(g, timing.air_delay() + float(show_delay))


# ---------------------------------------------------------------------------------------------- the guards (from tools/park_live)
def sure_until(open_from: float | None, edge: float, *, floor: float | None = None, lead: float = float(RESUME_LEAD[League.NBA]),
               eyes_hi: float | None = None) -> float:
    """How far dead time that is not final is trusted (grid or feed seconds, whichever the caller uses): up to the feed's
    edge less the grader's restart lead, or up to `floor` (the shortest such a stoppage has lasted) when that is later,
    or to Eyes's last frame when Eyes is watching it (exact). Everything when nothing is open."""
    if open_from is None:
        return float("inf")
    if eyes_hi is not None:
        return eyes_hi
    return max(edge - lead, floor if floor is not None else float("-inf"))


def refusal(A: list, pad: int, t: int, flip: int, know: int, late_cap: float, g1: int, i: int, over: bool, g2: int, j: int,
            sure: list, open_from: list, tail: list | None = None, x1: int = 0, x2: int = 0) -> str:
    """Why this jump may not go ("" = it may). i = the believed second of game g1 the viewer would leave at; j = the
    first believed second of game g2 the viewer would see; `over` = game g1 has ended. `pad` is the engine's padding and
    A[g] already carries each game's own (`pad_arr`); x1 / x2 widen it for a stream whose tune was not read. Dead time
    that is not final (from open_from[g] on) counts only when its whole padding (tail[g]) ends by sure[g]."""
    xs, xh, n = A[g1], A[g2], A[g1].n

    def inside(x, g: int, p: int, pd: int) -> bool:
        if not (0 <= p < n and x.since[p] > pd and x.left[p] >= pd and p + pd <= t + know):
            return False
        return open_from[g] is None or p < open_from[g] or p + (tail[g] if tail else pad) + (pd - pad) <= sure[g]
    if not over:
        if not 0 <= i < n or xs.hlc[min(n, i + pad + x1 + 1)] - xs.hlc[max(0, i - pad - x1)]:
            return "the leave is inside a padded high-leverage span"
        if not inside(xs, g1, i, pad + x1):                  # a LEAVE and a JOIN alike: the grader's dead time only (one mask)
            return "the leave is outside a padded legal window"
    if not inside(xh, g2, j, pad + x2):
        return "the landing is outside a padded legal window"
    if late_cap > 0 and t + flip - j > late_cap:
        return f"the landing is more than {late_cap:g} s behind live"
    return ""


def pad_arr(x, extra: int, eyes: tuple | None = None, grid: tuple = (0, 0)):
    """One game's arrays (the PARK core's `Arr`, built from `planner_view`) with that game's own padding cut in, in
    place: every legal stretch is cut back by `extra` seconds at each end and every high-leverage span widened by it,
    so the engine's one padding (the smallest game's) tests this game at its own. `eyes` = (starts, ends, extra): the
    stretch edges Eyes itself saw, which carry Eyes's own smaller cut. Every other edge is the feed's.
    `grid` = (head, tail): whole seconds also cut off the start and the end of every stretch for the 1 s grid itself
    (`GRID` for a show on real sticks; see there). Each stretch is cut by itself. Returns x."""
    head, tail = grid
    if extra > 0 or head > 0 or tail > 0 or (eyes is not None and eyes[2] > 0):
        _cut(x, extra, eyes or ((), (), 0), head, tail)
    return x


GRID = (1, 2)      # (head, tail): whole seconds cut off the start and the end of every dead stretch for a show on real sticks,
#                    so that the padding in SECONDS really holds on a grid of whole seconds. The arrays hold one flag per
#                    second, read at instants t0, t0 + 1, ... of the game's own clock, and are laid on the night's grid by
#                    cutting each time down to its whole second; a believed position x is used as int(x). So for a stretch
#                    marked on grid seconds m..m'-1 the ball is only KNOWN dead from m + f to m' - 1 + f (f = the game's
#                    sub-second phase on the grid, 0 to 1), and the real x lies in [int(x), int(x) + 1).
#                    Start: x - pad >= m + f needs 1 more second (f can be almost 1).
#                    End: x + pad <= m' - 1 + f needs 2 more (1 for the last flag, 1 for int(x); f can be 0).
#                    Found Nov 10 in the tables simulator: "4 sd" rounded to 2 s was really 1 to 2 s on the grid.
GRID_TAIL = GRID[1]


def _trim(mask: bytearray, extra: int, eyes: tuple, head: int, tail: int) -> bytearray:
    """One mask with every stretch cut back: `extra` + `head` at its start, `extra` + `tail` at its end (Eyes's own cut
    in place of `extra` at an edge Eyes saw). A stretch that runs to the end of the grid keeps its end (nothing says
    when it ends)."""
    n, new = len(mask), bytearray(len(mask))
    starts, ends, own = eyes
    i = 0
    while i < n:
        if not mask[i]:
            i += 1
            continue
        a = i
        while i < n and mask[i]:
            i += 1
        a2 = a + (own if a in starts else extra) + head
        e2 = i if i >= n else i - (own if i in ends else extra) - tail
        if e2 > a2:
            new[a2:e2] = b"\x01" * (e2 - a2)
    return new


def _cut(x, extra: int, eyes: tuple, head: int = 0, tail: int = 0) -> None:
    """pad_arr's cut (x = the game's arrays)."""
    n = x.n
    x.legal = _trim(x.legal, extra, eyes, head, tail)
    x.since, x.left, x.starts, x.ends = stretches(x.legal)
    x._leave = {}
    x.__dict__.pop("_plan_tabs", None)
    if extra > 0 and x.hlc[n]:
        hlc, hl = x.hlc, bytearray(n)
        for i in range(n):
            if hlc[min(n, i + extra + 1)] - hlc[max(0, i - extra)]:
                hl[i] = 1
        x.hl = hl
        x.hlc = [0] * (n + 1)
        for i in range(n):
            x.hlc[i + 1] = x.hlc[i] + hl[i]


def merge_eyes(g: dict, spans: list, base: int, end: float) -> dict:
    """The game's facts with what Eyes saw put over the feed's: inside a span [lo, hi] (grid seconds) the dead time is
    exactly Eyes's clock-stopped stretches; the feed keeps what is before, and what starts after Eyes's last frame.
    A feed stoppage that runs past the last frame survives there only if Eyes saw the clock stopped at that frame."""
    dead = list(zip(g["dead"], g["brk"]))
    for sp in spans:
        lo, hi = base + sp["lo"], base + sp["hi"]
        stopped_at_hi = sp["open"] is not None or any(b >= sp["hi"] for _, b in sp["stops"])
        out = []
        for (a, b), k in dead:
            if b <= lo or a > hi:
                out.append(((a, b), k))
                continue
            if a < lo:
                out.append(((a, lo), k))
            if b > hi and stopped_at_hi and sp["open"] is None:
                out.append(((hi, b), k))
        out += [((base + a, base + b), False) for a, b in sp["stops"]]
        if sp["open"] is not None:
            out.append(((base + sp["open"], end), False))
        dead = sorted(out)
    return {**g, "dead": [x for x, _ in dead], "brk": [k for _, k in dead]}

