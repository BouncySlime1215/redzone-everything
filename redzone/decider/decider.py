"""The switch decider: a producer that never cuts you off mid-play.

Why: Rule #1 in the switch decision: states and locks decide when a cut is allowed, so a play is never cut off (commit 2026-11-02 'Mid-play guard').

States (shown on the phone):
  IDLE       nothing live
  WATCHING   on a game, free to leave at the next safe gap
  LOCKED     a moment is in progress here (red-zone drive, power play...)
  BREAK      your game is in a break on screen; visiting something else
  PAUSED     you paused auto-switching, locked a game, or just did something by hand
  FROZEN     it caught itself flip-flopping; holding still for a few minutes
  SAFE       repeated errors; holding still until you tap Resume

Hard rules, in order:
  1. Never cut away while a play is on screen (screen.leave_gate).
  2. Never leave a LOCKED moment except for a much bigger one, and only at a gate.
  3. Switch when the evidence and value justify it; never overlap a switch in progress.
  4. Only switch for a clear margin that also pays for the switch's dead air.

NFL RedZone (Sunday afternoons, with Sunday Ticket) is a pseudo-game "redzone": commercial-free,
so it's the place to wait out a break when nothing better is live, and home base when nothing
is close. It has no plays of its own to cut out of, so leaving it needs no gate. Plays it shows
are never counted as "seen": we can't tell which ones it showed.
  5. Crunch time (final 2:00 of a one-score game, or OT) comes first: get there during a
     stoppage before its first snap, stay (your team's always), hop only at safe gaps.
"""
from __future__ import annotations

import logging
import time
import math
from collections import Counter, deque
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Callable

from redzone.decider.screen import (AFTER_PLAY_BUFFER_S, CONTINUOUS, MIN_BREAK_S, PLAY_LEN_S, DelayBook, Event, Gate,
                                    Timeline, break_on_arrival, catchable, final_play_aired, leave_gate,
                                    leave_window, resume_lead, resume_play, STALE_DATA_S, _stoppage_reached)
from redzone.decider.cuts import EntryPlan, plan_entry
from redzone.decider import buffered as bj
from redzone.decider import cuts
from redzone.decider.playbooks import Safety
from redzone.decider.scenarios import Scenarios
from redzone.noggin.lookahead import ahead
from redzone.models import REAL_BREAKS, ByLeague, League, Stoppage
from redzone import config as _config
from redzone.decider import core
from redzone.decider.core import BreakView, CoreState, DecisionCore, Knobs, MomentView, Option, SwitchPath
from redzone.scoring.engine import Scored
from redzone.fsm import MachineMap
from redzone.machines import decider as M

log = logging.getLogger(__name__)

GONE_MAX_HOLD_S = 90.0  # [mid-play guard] a game that vanished without a final: don't wait on a dead feed forever
# Basketball/hockey have no between-play gap to prove, so a vanished game (late West Coast games at the
# ESPN scoreboard rollover after midnight) is held longer before leaving blind.
GONE_MAX_HOLD_CONTINUOUS_S = 300.0
REDZONE = "redzone"
REEL = "reel"                 # the catch-up reel (redzone/reel.py): a break filler, never a game
PSEUDO = (REDZONE, REEL)      # not games: no play to cut, no timeline, no entry timing
# Locks where every snap matters, not just the big ones (scoring/engine.py names them).
CRUNCH_LOCKS = {"final two minutes", "crunch time", "overtime", "empty net"}
UNSEEN_PLAY_LAG_S = 10.0  # a play this recent may not be in the feed yet
# Halftime / intermission visits: return this long before the break could end on screen.
LONG_BREAKS = {"halftime", "intermission"}
LONG_VISIT_MIN_S = 240.0            # only plan a visit if at least this much break is guaranteed
RETURN_BUFFER_S = 60.0              # visiting football/baseball/RedZone: a clean gap is never far away
RETURN_BUFFER_CONTINUOUS_S = 180.0  # visiting basketball/hockey: allow time to reach a whistle
# [break visits] How long each kind of break runs on screen: the archive medians (ESPN, Oct-Nov 2026:
# break event -> next live play, minus game clock that ran). Planning only: does a visit pay for its
# trip there AND back? The trip home is still started by the data (the resume shows up about a
# stream delay before it airs), so a break that runs short costs a shorter visit, never a strand;
# no leave gate reads these numbers.
BREAK_LEN_S = ByLeague({
    # official (TV) timeout 143-160 s after a punt/score/turnover; a team timeout ~50 s, usually no ads.
    # A score break is the most reliable ad slot (score -> PAT -> ~2:40 of ads -> kickoff).
    League.NFL: {"official_timeout": 150.0, "timeout": 50.0, "two_minute_warning": 167.0,
                 "end_period": 202.0, "halftime": 857.0, "score_break": 150.0, "review": 60.0, "injury": 60.0},
    # timeouts #1-2 of a period are the long TV ones (median 188 s, n=697); #3+ median 107 s (n=261)
    League.NBA: {"tv_timeout": 188.0, "timeout": 107.0, "end_period": 171.0, "halftime": 933.0, "review": 60.0},
    # official TV timeout median 184 s (n=40); team timeouts median 84 s
    League.CBB: {"tv_timeout": 184.0, "timeout": 84.0, "end_period": 171.0, "halftime": 980.0, "review": 60.0},
    League.WCBB: {"tv_timeout": 174.0, "timeout": 84.0, "end_period": 185.0, "halftime": 983.0, "review": 60.0},
    # TV timeout median 149 s (n=300); after a goal (no TV timeout) median 48 s
    League.NHL: {"timeout": 149.0, "score_break": 48.0, "intermission": 1131.0, "end_period": 60.0,
                 "review": 60.0},
    # half-inning median 175 s (n=733); mid-inning pitching change median 178 s (p10 83)
    League.MLB: {"end_period": 175.0, "pitching_change": 178.0},
})
VISIT_MIN_WATCH_S = 0.0    # a break visit must show more than this much of the other game
# Time to find a clean exit from the visited game on the way back: discrete sports have a gap
# between every play; basketball/hockey need a whistle; RedZone has nothing to cut out of.
VISIT_EXIT_S = 10.0
VISIT_EXIT_CONTINUOUS_S = 25.0
KICKOFF_TO_SNAP_S = 25.0  # kickoff logged -> next scrimmage play logged, at the quickest
# [pacing] Stoppages that usually end before a plain break visit pays off: in the NFL backtest
# the median such visit lasted 20 s (tune away, tune straight back) and they were the biggest
# source of A->B->A flips. Ride them out at home; chases, catch-ups and crunch moves still go.
SHORT_STOPS = {"timeout", "injury", "review"}
# Leagues where a short stop isn't spent on a visit. NBA too (scorecards, Nov 3): mid-play entries
# 44 -> 36 with everything else the same; NHL changed nothing.
SHORT_STOP_LEAGUES = {League.NFL, League.NBA}
# The first pick after a (re)start that didn't land (cancelled, timed out, no safe moment, a failed
# tune): try again after 3 s, then 6, 12, 24... capped at a minute. Never give up: with nothing
# confirmed on screen there is nothing to wait for but the next try.
START_RETRY_S = 3.0
START_RETRY_MAX_S = 60.0
# [cut timing] Every live entry lands on a dead ball (cuts.plan_entry): as is, with a short DVR rewind
# to the dead ball before the play we'd otherwise join mid-way, or after waiting for one. Only a
# switch you made by hand goes straight in.
ENTRY_EXEMPT = {"user", "resume", "skip"}   # resume/skip land on a planned frame of the same tune
PICK_KEEPS = {"user", "crunch", "home", "must_see", "live"}
# [jump mode] switches the normal decider makes that the rotation never overrides (they already go at once)
JUMP_KEEPS = {"user", "crunch", "ended", "start", "chase", "must_see", "drive", "replay", "resume", "skip"}   # [noggin PICK] app-only switch kinds
ENTRY_SLACK_S = 2.0       # the tune's own timing doubt, on top of the delay's 2 sigma
# Once the delay's doubt grows (sigma drifts up between calibrations) no landing fits a 30 s huddle
# with the full margin: then centre it in the dead window with this much doubt. A miss here joins a
# play a few seconds late; it is never a cut.
ENTRY_RELAXED_S = 8.0
# The picture can come up as soon as this after the command (a flip to a ready hidden stick), or as
# late as the target's estimated cost: the whole stretch must land on a dead ball.
ENTRY_FASTEST_S = 4.0
# Crunch time or your team: landing on the dead ball the data shows, with only this much doubt.
ENTRY_URGENT_S = 2.0
# The furthest an entry rewinds to reach a dead ball (YouTube TV): hockey can run a minute and more
# between whistles; RedZone itself airs touchdowns ~16 s late.
ENTRY_MAX_REWIND_S = 120.0
# A stop expected to run at least this much longer on screen is worth a visit (breaks.VISIT_MIN_S).
VISIT_MIN_S = 90.0

def _nhl_ot(s: Scored) -> bool:
    """[nhl pp] NHL overtime / shootout: never left except for crunch time elsewhere."""
    return s.league.base == League.NHL and s.lock == "overtime"


def _holds_replays(s: Scored) -> bool:
    """A live moment that a catch-up replay must not be shown instead of. A power play only holds
    the TV once you're on it: on the way somewhere else it competes on score with the replay, or a
    night of power plays would cut every catch-up chain short (Oct 20 replay: 5 goals never seen)."""
    return bool(s.lock) and s.lock != "power play"


def _nhl_close_third(s: Scored) -> bool:
    """[nhl pp] A one-goal NHL 3rd period: crunch time is coming, and a visit to a game with no
    clean exit can strand you through it (Oct 31 replay: STL @ DET at 7:34 left, back at 1:09)."""
    return s.league.base == League.NHL and s.late and not s.garbage and s.closeness >= 0.9


def _short(s: Scored) -> str:
    return "your game"


def reel_scored(score: float, seconds: float, over: bool = False) -> Scored:
    """The catch-up reel as a candidate. Once it has played through it reads as a break (nothing
    left to see), so the decider moves on by its usual rules."""
    sc = 0.0 if over else score
    return Scored(REEL, League.NFL, sc, sc, sc, 1.0, 0.0, f"catch-up reel: plays you missed ({seconds:.0f} s)",
                  [], over, None, None, 1.0)


def redzone_scored(score: float) -> Scored:
    """RedZone as a candidate: never on a break, never locked, always worth `score`."""
    return Scored(REDZONE, League.NFL, score, score, score, 1.0, 0.0,
                  "NFL RedZone: every score, no commercials", [], False, None, None, 1.0)


class State(str, Enum):
    IDLE = "idle"
    WATCHING = "watching"
    LOCKED = "locked"
    BREAK = "break"
    PAUSED = "paused"
    FROZEN = "frozen"
    SAFE = "safe"
    REPLAY = "replay"


class FocusMode(str, Enum):
    AUTOPILOT = "autopilot"
    FOCUS = "focus"                 # leave only during its breaks
    FOCUS_MUST_SEE = "focus_must_see"  # ...or for something huge elsewhere


@dataclass
class Decision:
    at: float
    action: str               # "stay" | "switch"
    target: str | None        # game_id
    reason: str
    state: State
    gate: str = ""
    kind: str = ""            # "better", "break_visit", "return", "chase", "must_see", "ended", "start", "replay", "crunch", "drive"
    replay: "Missed | None" = None
    want: str | None = None   # the game it wants to switch to while it waits for a safe moment
    rewind_s: float = 0.0     # [cut timing] land this far behind live (whole seek steps) on a dead ball
    cue: str = ""             # [seek model] "pause": the hidden stick cues the rewind before the flip
    defer_s: float = 0.0      # [EXP-803 A1] cut this long after the decision (a break's tail inside one tick)
    jump: dict | None = None  # [jump mode] why this jump: numbers and short codes only (data/jumps.jsonl)
    owner: str = ""           # [owner] which decider owned the TV on this tick (decider.owner: jump, normal, user...)


@dataclass
class Missed:
    """A big play you didn't see live: a catch-up replay candidate."""
    game_id: str
    pid: str
    label: str
    kind: str
    t: float                  # when it happened (wallclock)
    value: float              # how much you'd want to see it (0-100)
    seen_live: bool = False   # True = "instant replay" of something you did see
    replayed: bool = False
    mine: bool = False        # your team / your bet: its catch-up goes before other games' ones
    due_s: float = 0.0        # [replay.redzone_way] replay it within this long of the play (0 = old rule)
    must: bool = False        # [T4] missed because another big play was on screen: head of the queue
    aired: float = 0.0        # when it aired on the screen (wallclock; 0 = unknown, use t)
    retry_at: float = 0.0     # [Nick, Nov 8 live] a failed replay of this play isn't tried again before this
    fails: int = 0            # failed replays of this play (replay.max_fails drops it)

    def back_s(self, now: float, delay_mu: float, play_len: float, lead_s: float = 3.0) -> float:
        """How far to rewind from the live edge (which shows real time now - delay)."""
        return max(0.0, (now - delay_mu) - (self.t - play_len - lead_s))


REPLAY_BASE = {"td": 75, "goal": 75, "safety": 70, "turnover": 65, "hr": 60, "fg": 35}
TUNE_SLACK_S = 3.0  # a tune can stay black this much longer than its estimated cost
URGENT_BONUS = 40.0  # added (ramping up) to a missed play's value as it nears replay.max_age_s


@dataclass
class UserState:
    paused: bool = False
    locked_game: str | None = None
    focus_game: str | None = None
    focus_mode: FocusMode = FocusMode.AUTOPILOT
    muted: set[str] = field(default_factory=set)
    manual_until: float = 0.0


@dataclass
class Candidate:
    scored: Scored
    target: str | None        # tuner target, e.g. "youtubetv:espn"; None = we don't know where it's on
    stale: bool = False


class Decider:
    def __init__(self, cfg, delays: DelayBook, clock=time.time):
        self.cfg = cfg
        self.delays = delays
        self._clock = clock
        # [state machines] redzone/machines/decider.py: each is the one source of truth for its state; the old
        # attribute names (current, replaying, after_replay, home_break_visit, safe_mode, ...) are properties on them
        self.owner_m = M.OWNER.new(clock=clock)          # which decider owns the TV this tick
        self.screen_m = M.DECIDER_SCREEN.new(clock=clock)        # unknown / tuning / live / replaying / after_replay
        self.start_m = M.START.new(clock=clock)          # the first pick after a (re)start, and its backoff
        self.gone_m = M.SCREEN_GONE.new(clock=clock)     # [buffered jump] keeping a buffered standby (was _bj_gone_since)
        self.home_m = M.DECIDER_HOME.new(clock=clock)            # a break visit (home_break_visit / home_return_at)
        self.safe_m = M.SAFE.new(clock=clock)            # safe mode (safe_mode / safe_since)
        self.override_m = M.OVERRIDE.new(clock=clock)    # your strongest override this tick
        self.lock_m = M.LOCK_WAIT.new(clock=clock)       # [lock wait]
        self.entry_m = M.ENTRY_WAIT.new(clock=clock)     # [cut timing] a whistle wait (was _entry_wait)
        self._dark_since: dict[str, float] = {}      # [P1-REVIEW-05] gid -> when an entry first waited on a feed that can't prove a dead ball
        self._dark_said: set[str] = set()            # ...and told the phone once
        self.entry_stuck: str | None = None          # the engine toasts this once: nothing else to go to
        self._restored_screen: tuple[str, float, bool] | None = None   # [P-04] note_restart: (game, saved at, high leverage)
        self.jump_break_m = MachineMap(M.JUMP_BREAK, clock=clock)   # [jump mode] per game (was _jump_break_at)
        self._current: str | None = None
        self._clip: "Missed | None" = None               # the replay clip while screen_m is 'replaying'
        self._home: str | None = None
        self._home_at: float | None = None
        self._arrival: float = 0.0
        self._start_retry_at = 0.0
        self.home_leaks = 0                              # a filter's stay that left a new visit set (restored)
        self.overlap_why: Counter = Counter()   # [GAME-05] overlap calls (audit)
        self.lookahead_why: dict[str, int] = {}   # [GAME-04] outcomes by reason (audit)
        # [possession start] the per-possession jump / stay policy: (decision, candidate, now) -> "jump" |
        # "stay". None = jump (the rule default). Self-taught only (oracle imitation, simulation, play-by-play
        # outcomes); Nick's buttons are controls, never labels (Nov 4 hard rule).
        self.possession_policy = self._possession_pick   # jump unless noggin.possession is on (default off)
        self.possession_log: deque = deque(maxlen=2000)  # [noggin possession] (t, on, target, policy says)
        # [replay and resume] the game paused for a catch-up replay: (game, when, its extra behind-live then)
        self.paused_for: tuple[str, float, float] | None = None
        self.resume_log: list[tuple[float, str, str, float]] = []   # (when, "replay"|"resume"|"skip", game, extra s)
        self._crunch_tail: dict[str, tuple[float, str | None]] = {}   # gid -> (last seen in crunch, target)
        self.scenarios = Scenarios(cfg)   # [scenario tree] GAME-01..03, 21, 22 seeds, flag playbooks.game.enabled (off)
        self.safety = Safety(cfg)   # [playbooks] SAFE-01/02/03/05, flag playbooks.safe.enabled (off)
        # The live controller knows whether THIS target is already ready. App-wide
        # average costs are useful for ranking, but cannot promise a deadline.
        # Offline simulations intentionally keep their configured timing model.
        self.deadline_cost: Callable[[str | None], float] | None = None
        # Seconds to get BACK to a game we're leaving right now (two sticks: it keeps playing on
        # the stick we leave, so one flip). None: plan the trip home like any other switch.
        self.return_cost: Callable[[str | None], float] | None = None
        # [cut timing] When the picture of a switch comes up (seconds after the command), if known
        # better than deadline_cost: a simulator states its own tune model here.
        self.entry_cost: Callable[[str | None], float] | None = None
        # Scheduled start (epoch s) of the next not-yet-started game with your team, if the
        # engine knows one. None = unknown, and the 'wait for your team' rule stays off.
        self.my_tip_at: Callable[[], float | None] | None = None
        self.user = UserState()
        # current: property (screen_m's game)
        self.pick_log: deque = deque(maxlen=2000)   # [noggin PICK] (t, on, pick, app action, app target)
        self.since: float = 0.0
        # _arrival_until: property (screen_m 'tuning' until then; simulated transport work, not a viewing-time quota)
        self.stack: deque[str] = deque(maxlen=3)   # where we came from, for "go back"
        # home_break_visit (game we left only for its break) / home_return_at (long break: head back by then): home_m
        self.history: deque[tuple[float, str, str]] = deque(maxlen=50)
        self.frozen_until = 0.0
        self.margin_bump_until = 0.0
        self.errors: deque[float] = deque(maxlen=10)
        # safe_mode / safe_since: properties on safe_m (when safe mode last turned on, for the timed auto-resume)
        self.timelines: dict[str, Timeline] = {}
        self.log: deque[Decision] = deque(maxlen=500)
        self._screen_break: set[str] = set()
        # Catch-up replays
        self.queue: list[Missed] = []
        self.done_replays: deque[Missed] = deque(maxlen=200)
        # replaying / after_replay: properties on screen_m
        self.replay_until = 0.0
        self.replay_exit_after = 0.0   # retained if interrupted: old footage may still be mid-play
        self._screen_log: deque[tuple[float, str | None]] = deque(maxlen=400)  # (from, game | "replay" | None=black)
        self._judged: set[tuple[str, str]] = set()
        self._target_of: dict[str, str] = {}
        self.reel_s = 0.0   # seconds of the catch-up reel ready (or playing)
        # Last known candidate per game. A final drops out of the live candidates, but its last
        # plays still air (and stay on the DVR) for a while: keep them judgeable and replayable.
        self._known: dict[str, Candidate] = {}
        # Games that were ever yours (your team / bet) tonight: personal fades in garbage time and with
        # closeness, but a missed play from your team's game stays yours (replay.yours_first).
        self._yours: set[str] = set()
        # [leave floor, EXP-208] when the FEED last showed each game close-and-late / OT: the screen runs a
        # delay behind the feed, so it can still be showing that moment after the feed has moved on
        self._high_lev_at: dict[str, float] = {}
        # [mid-play guard] state for leaving a game that ended or dropped out of the feed
        self._known_target: dict[str, str] = {}
        self._gone: tuple | None = None   # ((game, arrived at), when it vanished without a final)
        self._last_target = self._known_target   # (crunch-time code's name for the same memory)
        self._proposal_home: tuple | None = None
        self.start_fails = 0            # start picks in a row that didn't land
        # start_retry_at (the next start pick waits until then): property on start_m
        self._entry_last = 0.0          # last tick of the whistle wait (entry_m: key = target, since = first wait)
        # [core] Every soft choice (which game, visit or not, reel, replay vs live, go back) is scored by
        # the one expected-value function in core.py; the rules left in here are the hard guards.
        self.core = DecisionCore(Knobs.from_cfg(cfg))
        # [lock wait] decider.lock_wait: a locked/held game we can't reach (no channel, or the tune
        # failed) waits with a visible reason and a retry time, then gives the lock up
        self.lock_gid: str | None = None    # the locked game these fields are about
        self.lock_since: float | None = None   # first time it couldn't be reached
        self.lock_retry_at = 0.0
        self.lock_why = ""
        self.lock_expired: str | None = None   # gave up on this locked game: the engine releases it
        # [jump mode, Nick Nov 7] rotation over every live game: when each game was last left, and why the
        # rotation didn't jump on the ticks since the last jump (short codes, for data/jumps.jsonl)
        self.jump_left_at: dict[str, float] = {}
        # [jump mode, overseer Nov 7 18:55] the controller's view of the hidden stick: is this target playing there,
        # ready for a ~3 s flip (None: no such knowledge, e.g. the simulator); and is it a slow search tune (a local)
        self.standby_ready: Callable[[str], bool] | None = None
        self.slow_tune: Callable[[str], bool] | None = None
        # [jump mode] why the rotation didn't jump, since the last switch of any kind (reset in _commit; the tick's owner
        # is in owner_m, so a 'safe_mode' / 'normal_crunch' count says which decider held the TV)
        self.jump_holds: Counter = Counter()
        self.snap_parked: Callable[[str | None], float | None] | None = None   # [buffered jump] the engine's control
        self._bj_pair: tuple[frozenset, float] | None = None   # [buffered jump] the two games on the sticks, since when

    # ----- state machines: the old attribute names ------------------------------------------------------------
    # Defaults at class level, and machines made on first use, so a Decider built without __init__ (unit tests use
    # Decider.__new__) still works.
    _current: str | None = None
    _clip: "Missed | None" = None
    _home: str | None = None
    _home_at: float | None = None
    _arrival: float = 0.0
    _start_retry_at: float = 0.0
    _entry_last: float = 0.0
    home_leaks: int = 0
    _LAZY = {"owner_m": M.OWNER, "screen_m": M.DECIDER_SCREEN, "start_m": M.START, "gone_m": M.SCREEN_GONE, "home_m": M.DECIDER_HOME,
             "safe_m": M.SAFE, "override_m": M.OVERRIDE, "lock_m": M.LOCK_WAIT, "entry_m": M.ENTRY_WAIT}

    def __getattr__(self, name: str):
        spec = Decider._LAZY.get(name)
        clock = self.__dict__.get("_clock", time.time)
        if spec is not None:
            m = self.__dict__[name] = spec.new(clock=clock)
            return m
        if name == "jump_break_m":
            m = self.__dict__[name] = MachineMap(M.JUMP_BREAK, clock=clock)
            return m
        raise AttributeError(f"{type(self).__name__!r} object has no attribute {name!r}")

    # Writes from outside the decider (tests, the recorded session, a restart) land here; the decider's own moves go
    # through screen_m.to() / home_m.to() / ... with a reason. A direct write is a restore: reset() with its name.

    @property
    def current(self) -> str | None:
        """The game on screen (screen_m says whether it's live, landing, a replay or old footage)."""
        return self._current

    @current.setter
    def current(self, gid: str | None) -> None:
        self._current = gid
        if gid is None:
            self.screen_m.reset("unknown", "current cleared")
        elif self.screen_m.is_("unknown"):
            self.screen_m.reset("live", "current set")

    @property
    def replaying(self) -> "Missed | None":
        return self._clip if self.screen_m.is_("replaying") else None

    @replaying.setter
    def replaying(self, m: "Missed | None") -> None:
        self._clip = m
        if m is not None:
            self.screen_m.reset("replaying", "replaying set")
        elif self.screen_m.is_("replaying"):
            self.screen_m.reset("live" if self._current else "unknown", "replaying cleared")

    @property
    def after_replay(self) -> bool:
        return self.screen_m.is_("after_replay")

    @after_replay.setter
    def after_replay(self, on: bool) -> None:
        if on:
            self.screen_m.reset("after_replay", "after_replay set")
        elif self.screen_m.is_("after_replay"):
            self.screen_m.reset("live" if self._current else "unknown", "after_replay cleared")

    @property
    def _arrival_until(self) -> float:
        return self._arrival

    @_arrival_until.setter
    def _arrival_until(self, t: float) -> None:
        self._arrival = t
        if self.screen_m.is_("tuning") and t <= self._clock():
            self.screen_m.to("live" if self._current else "unknown", "arrival confirmed")

    def _screen_tick(self, now: float) -> None:
        """A dry-run / simulated tune lands once its estimated cost has passed."""
        if self.screen_m.is_("tuning") and now >= self._arrival:
            self.screen_m.to("live" if self._current else "unknown", "the tune's estimated arrival passed", now=now)

    @property
    def home_break_visit(self) -> str | None:
        return self._home

    @home_break_visit.setter
    def home_break_visit(self, gid: str | None) -> None:
        self._home_set(gid, self._home_at, "home_break_visit set")

    @property
    def home_return_at(self) -> float | None:
        return self._home_at

    @home_return_at.setter
    def home_return_at(self, t: float | None) -> None:
        self._home_set(self._home, t, "home_return_at set")

    def _home_set(self, gid: str | None, at: float | None, why: str) -> None:
        """The one way a break visit changes: home_m follows (none / visit / visit_timed)."""
        self._home, self._home_at = gid, at   # (a return time without a visit is kept, as before: inert)
        self.home_m.to("none" if gid is None else "visit_timed" if at is not None else "visit", why)

    def _home_restore(self, home_before: tuple, why: str) -> None:
        """A filter turned this tick's switch into a stay: whatever visit the switch planned waits with it."""
        if (self._home, self._home_at) != tuple(home_before):
            self._home_set(home_before[0], home_before[1], f"restored: {why}")

    @property
    def safe_mode(self) -> bool:
        return self.safe_m.is_("safe")

    @safe_mode.setter
    def safe_mode(self, on: bool) -> None:
        self.safe_m.to("safe" if on else "ok", "safe_mode set")

    @property
    def safe_since(self) -> float:
        return self.safe_m.since if self.safe_m.is_("safe") else self._safe_since_off

    @safe_since.setter
    def safe_since(self, t: float) -> None:
        if self.safe_m.is_("safe"):
            self.safe_m.since = t
        else:
            self._safe_since_off = t

    _safe_since_off = 0.0

    @property
    def start_retry_at(self) -> float:
        return self._start_retry_at

    @start_retry_at.setter
    def start_retry_at(self, t: float) -> None:
        self._start_retry_at = t
        if t <= 0.0 and self.start_m.is_("backoff"):
            self.start_m.to("ready", "start backoff cleared")

    @property
    def _entry_wait(self) -> tuple | None:
        """(target, waiting since, last tick) of a whistle wait, or None (entry_m)."""
        m = self.entry_m
        return (m.key, m.since, self._entry_last) if m.is_("waiting") else None

    @_entry_wait.setter
    def _entry_wait(self, w: tuple | None) -> None:
        if w is None:
            self.entry_m.to("idle", "cleared")
            return
        tgt, since, last = w
        if not self.entry_m.is_("waiting") or self.entry_m.key != tgt or self.entry_m.since != since:
            if self.entry_m.is_("waiting"):
                self.entry_m.to("idle", f"a new wait for {tgt}")
            self.entry_m.key = tgt
            self.entry_m.to("waiting", f"switch to {tgt} waits for a dead ball", now=since)
        self._entry_last = last

    @property
    def _jump_break_at(self) -> dict[str, float]:
        """When the feed first said 'break' per game (jump_break_m); read-only view."""
        return {g: m.since for g, m in self.jump_break_m.items() if m.is_("break")}

    @property
    def _bj_gone_since(self) -> float | None:
        return self.gone_m.since if self.gone_m.is_("keeping", "let_go") else None

    def _override_tick(self, now: float) -> None:
        """decider.override: your strongest override, in the order _decide honours them (a view: UserState is what the
        phone writes and redzone/userstate.py persists; this says which part of it is in charge now)."""
        u = self.user
        st, why = (("safe", "safe mode") if self.safe_mode else ("paused", "paused") if u.paused
                   else ("locked", f"locked on {u.locked_game}") if u.locked_game
                   else ("manual", "you just picked a game") if now < u.manual_until
                   else ("focus", f"{getattr(u.focus_mode, 'value', u.focus_mode)} on {u.focus_game}")
                   if u.focus_mode != FocusMode.AUTOPILOT else ("auto", "autopilot"))
        self.override_m.to(st, why, now=now)

    def machines(self, now: float | None = None) -> dict:
        """[state machines] Every decider machine now (snapshot()['machines'], jump_status)."""
        return {"owner": self.owner_m.view(now, history=0), "screen": self.screen_m.view(now, history=0),
                "start": self.start_m.view(now, history=0), "screen_gone": self.gone_m.view(now, history=0),
                "home": {**self.home_m.view(now, history=0), "game": self._home, "return_at": self._home_at},
                "safe": self.safe_m.view(now, history=0), "override": self.override_m.view(now, history=0),
                "lock_wait": self.lock_m.view(now, history=0), "entry_wait": self.entry_m.view(now, history=0),
                "jump_break": self.jump_break_m.view(now), "home_leaks": self.home_leaks}

    # ----- inputs -----------------------------------------------------------

    def timeline(self, game_id: str, league: League) -> Timeline:
        if game_id not in self.timelines:
            tl = self.timelines[game_id] = Timeline(league, clock=self._clock)
            tl.lag_prior = float(_config.for_league(self.cfg, "delay.feed_lag_prior_s", league.value,
                                                    _config.default("delay.feed_lag_prior_s")))
            floors = _config.for_league(self.cfg, "decider.leave.break_floor_s", league.value, None) or {}
            tl.extra_floors = {Stoppage(k): float(v) for k, v in floors.items() if v}
            from redzone.noggin.live import hook_for   # [noggin] Timing v1, off unless noggin.timing.leagues
            tl.dead.noggin = hook_for(self.cfg, league)
            if self._pick_on(league):
                from redzone.noggin.pick import LiveGame
                tl.live = LiveGame(league, game_id, "")
        tl = self.timelines[game_id]
        # [quiet break] read every poll so a /api/settings change applies to games already on
        tl.quiet_cfg = _config.for_league(self.cfg, "decider.leave.quiet_break", league.value, None)
        return tl

    def _pick_on(self, league: League) -> bool:
        """[noggin PICK] on (or in shadow) for this league, with a model to run (PICK itself, or the
        possession policy's PICK-cue model)."""
        c = self.cfg
        if (c.get("noggin.possession.enabled", False) or c.get("noggin.possession.shadow", False)) \
                and league.value in (c.get("noggin.possession.leagues") or []):
            from redzone.noggin.pick import model_for
            if model_for(league.value, name=str(c.get("noggin.possession.model", "cue90"))) is not None:
                return True
        if not (c.get("noggin.pick.enabled", False) or c.get("noggin.pick.shadow", False)):
            return False
        if league.value not in (self.cfg.get("noggin.pick.leagues") or []):
            return False
        from redzone.noggin.pick import model_for
        return model_for(league.value) is not None

    def note_error(self) -> None:
        now = self._clock()
        self.errors.append(now)
        recent = [t for t in self.errors if now - t < 300]
        if len(recent) >= 3 and not self.safe_mode:
            self.safe_m.to("safe", f"{len(recent)} errors in 5 minutes", now=now)

    def auto_resume(self, after_s: float) -> bool:
        """Leave safe mode on its own once it has been on for after_s seconds: nobody is in the
        room to tap Resume, and a transient app hiccup must not end auto-switching for the night.
        Only safe mode is cleared; a pause you set stays. A fault that is still there trips it
        again within three errors."""
        if not self.safe_mode or self._clock() - self.safe_since < after_s:
            return False
        self._safe_since_off = self.safe_since
        self.safe_m.to("ok", f"auto-resumed after {after_s:.0f}s")
        self.errors.clear()
        return True

    def resume(self) -> None:
        if self.safe_mode:
            self._safe_since_off = self.safe_since
            self.safe_m.to("ok", "Resume tapped")
        self.user.paused = False
        self.frozen_until = 0.0
        self.errors.clear()

    def manual_switch(self, game_id: str) -> None:
        """You picked a game by hand: go there and keep hands off for a bit."""
        self.abort_replay()
        self._commit(game_id, kind="user")
        self.user.manual_until = self._clock() + self.cfg.get("decider.manual_pause_s")

    # ----- the decision -----------------------------------------------------

    def decide(self, cands: dict[str, Candidate], redzone: str | None = None, *, commit: bool = True,
               reel: tuple[str, float] | None = None) -> Decision:
        """`redzone` is the RedZone channel's tuner target while it's on the air, else None.
        `reel` is (tuner target, seconds) of a ready catch-up reel, else None."""
        now = self._clock()
        self.core.k = Knobs.from_cfg(self.cfg)   # config can change between ticks (tuner, phone)
        self._known.update({gid: x for gid, x in cands.items() if x.target})
        if self.cfg.get("replay.crunch_block_tail", False):
            now_t = self._clock()
            for gid, x in cands.items():
                if (x.scored.crunch or x.scored.crunch_now) and not getattr(x.scored, "garbage", False):
                    self._crunch_tail[gid] = (now_t, x.target)
        hold = self.cfg.get("decider.crunch.hold_personal", 5)
        self._yours.update(gid for gid, x in cands.items() if x.scored.personal >= hold)
        now_hl = self._clock()
        self._high_lev_at.update({gid: now_hl for gid, x in cands.items() if getattr(x.scored, "high_leverage", False)})
        cands = self._with_redzone(cands, redzone)
        cands = self._with_reel(cands, reel)
        cands = self._nfl_priority(cands)
        self._learn_entries(cands)
        self._scan_missed(cands, now)
        self._screen_tick(now)
        if self.replaying:
            if self.safe_mode or self.user.paused or self.on_screen_at(now) != "replay":
                self.abort_replay()
            elif now >= self.replay_until:
                self._clip.replayed = True
                self.done_replays.append(self._clip)
                self._clip = None
                self.screen_m.to("after_replay", "the replay clip ended", now=now)
        self._override_tick(now)
        home_before = (self.home_break_visit, self.home_return_at)
        cands = self._without_dark_targets(cands, now)
        d0 = d = self._decide(cands, now)
        d = self._scenario(d, cands, now, home_before)
        d = self._clairvoyant(d, cands, now, home_before)
        d = self._crunch_guard(d, cands, now, home_before)
        d = self._pick(d, cands, now)
        d = self._hold_for_big(d, cands, now, home_before)
        d = self._time_entry(d, cands, now, home_before)
        d = self._dwell(d, cands, now, home_before)
        d = self._crunch_floor(d, d0, cands, now, home_before)
        # [owner] exactly one decider owns the TV this tick; the rotation's switch and its veto of the normal
        # decider's optional switches both follow from this one call (they used to check 8 and 4 conditions)
        owner, why = self._own(d, cands, now)
        self.owner_m.to(owner, why, now=now)
        if owner in ("jump", "buffered") and why != "just_arrived":
            j = self._jump(d, cands, now, why)
        else:
            self._count_hold(why)
            j = None
        if j is not None:
            self._home_set(None, None, "a rotation has no home to go back to")
            d = j
        elif self._jump_owns(d, cands, owner, why):
            # [jump mode] the rotation owns every optional switch (a break visit, the way home, a better game):
            # the normal decider's would fight it. Its hard switches (crunch, a finished game, a chase...) still go.
            # The visit that switch planned waits with it, like every other filter's stay.
            self._home_restore(home_before, "jump mode owns optional switches")
            d = Decision(now, "stay", self.current, f"jump mode: {d.reason}", d.state, d.gate, want=d.target)
        if d.action == "switch" and d.kind == "start" and self._restart_hold(cands, now):
            # [P-04] jump mode off too: the rotation's own hold (_jump) only runs when it owns the TV
            self._home_restore(home_before, "restart hold: a stay")
            d = Decision(now, "stay", self.current, f"restart: holding the saved game's leverage ({d.reason})", d.state,
                         d.gate, want=d.target)
        d = self._safe(d, cands, now, home_before)
        if d.action not in ("switch", "replay") and self._home is not None and self._home != home_before[0]:
            # [home] a filter turned a switch into a stay without putting the planned visit back: never leak it
            self.home_leaks += 1
            log.warning("home visit leaked by a stay (%s): restored", d.reason[:80])
            self._home_restore(home_before, "a stay can't start a visit")
        d.owner = owner
        if not commit and d.action in ("switch", "replay"):
            # The simulator can commit synchronously. A real TV must confirm
            # the command first: a proposal is not a watched game or replay.
            self._proposal_home = (d, self.home_break_visit, self.home_return_at)
            self._home_restore(home_before, "a proposal: the visit starts when the TV confirms it")
            self.log.append(d)
            return d
        if d.action == "switch" and d.target:
            if d.target == self.current and not self.after_replay and d.kind != "skip":
                d = Decision(now, "stay", self.current, "already here", d.state, owner=owner)
            else:
                if d.kind in ("resume", "skip"):
                    self.resume_log.append((now, d.kind, d.target, d.rewind_s))
                self._commit(d.target, kind=d.kind)   # after_replay ends here: screen_m -> tuning
                self._land_behind(d.target, d.rewind_s)
        elif d.action == "replay" and d.replay:
            self._pause_for(d, now)
            self._start_replay(d.replay, cands, now)
        self.log.append(d)
        return d

    def forget_screen(self, why: str) -> None:
        """Nothing is known on screen any more (dry run turned off, a recorded session): current None, no replay
        footage, no tune landing. (recorded.py writes the same attributes one by one; they land here too.)"""
        self._current, self._clip, self._arrival = None, None, 0.0
        self._screen_log.clear()
        self.replay_exit_after = 0.0
        self.screen_m.reset("unknown", why)

    def adopt(self, gid: str) -> None:
        """[overseer Nov 7 23:00] The TV already shows gid (read back from the stick after a restart): learn the
        screen game without a tune, so jump mode stops holding 'screen_game_unknown'."""
        self._restored_screen = None
        self._commit(gid, kind="adopt", arrived=True)

    def note_restart(self, gid: str | None, at: float, high_leverage: bool = False) -> None:
        """[P-04] The app restarted with gid on screen (from the engine's saved state; `at` = the saving wall time). Until
        adopt() or buffered_jump.restart_hold_s after it, the unknown screen counts as that game for the leave rule."""
        self._restored_screen = (gid, at, bool(high_leverage)) if gid else None

    def restart_state(self, s=None) -> tuple[str, bool] | None:
        """[P-04] What a restart would need to know: (the game on screen, is it high leverage). s: its fresh Scored."""
        gid = self.current
        if gid is None or gid in PSEUDO:
            return None
        if s is None:
            x = self._known.get(gid)
            s = x.scored if x is not None else None
        lev = s is not None and bool(getattr(s, "high_leverage", False) or s.crunch or self._mine_late(s))
        return gid, lev

    def _restart_hold(self, cands: dict[str, Candidate], now: float) -> bool:
        """[P-04] Don't free-cut away from an unknown screen while it is probably the close, late game we saved."""
        saved = self._restored_screen
        if saved is None or self.current is not None:
            return False
        gid, at, lev = saved
        if now - at > float(self.cfg.get("buffered_jump.restart_hold_s", 120.0)):
            self._restored_screen = None
            return False
        x = cands.get(gid)
        if x is not None:
            lev = bool(getattr(x.scored, "high_leverage", False) or x.scored.crunch or self._mine_late(x.scored))
        return lev

    def confirm(self, d: Decision, cands: dict[str, Candidate]) -> None:
        """Commit an observed playback change, with screen time from confirmation."""
        if self._proposal_home and self._proposal_home[0] is d:
            _, home, at = self._proposal_home
            self._home_set(home, at, f"the TV confirmed the {d.kind or d.action}")
        self._proposal_home = None
        if d.action == "replay" and d.replay:
            self._pause_for(d, self._clock())
            self._start_replay(d.replay, cands, self._clock(), confirmed=True)
        elif d.target:
            self.abort_replay()
            # _commit's future estimate is for offline simulation. The TV already confirmed: no extra tune or
            # watched time (arrived=True logs the picture once, instead of popping the estimate back off)
            self._commit(d.target, kind=d.kind, arrived=True)
            self._land_behind(d.target, d.rewind_s)

    def replay_failed(self, m: Missed, now: float | None = None) -> None:
        """[Nick, Nov 8 live, UGA@MISS] A replay that failed (any path) backs off: it was decided again every tick
        and failed at ~2 Hz ('replay seek failed; kept current game'). replay.fail_retry_s before it may be tried
        again; after replay.max_fails failures it leaves the queue."""
        now = self._clock() if now is None else now
        m.fails += 1
        if m.fails >= int(self.cfg.get("replay.max_fails", 2)):
            self.queue = [q for q in self.queue if q is not m]
            m.retry_at = float("inf")
        else:
            m.retry_at = now + float(self.cfg.get("replay.fail_retry_s", 120))

    def reject(self, d: Decision, backoff: bool = True) -> None:
        """The TV didn't take d. backoff=False: it was superseded by a newer switch (not a failed start)."""
        if self._proposal_home and self._proposal_home[0] is d:
            self._proposal_home = None
        if backoff and d.kind in ("start", "user") and self.current is None:   # a first pick, or a lock/hold restored at start
            self.start_fails += 1
            wait = min(START_RETRY_MAX_S, START_RETRY_S * 2 ** (self.start_fails - 1))
            self._start_retry_at = self._clock() + wait
            self.start_m.to("backoff", f"start pick #{self.start_fails} didn't land: next in {wait:.0f}s")

    def lock_failed(self, d: Decision, why: str) -> None:
        """[lock wait] A switch to your locked game failed for a reason that is the target's
        fault (the engine filters out closed safe windows and cancels): wait before retrying."""
        gid = self.user.locked_game
        if not self.cfg.get("decider.lock_wait.enabled") or d.kind != "user" or not gid or d.target != gid:
            return
        now = self._clock()
        self._lock_note(gid, why, now)
        self.lock_retry_at = now + self.cfg.get("decider.lock_wait.retry_s")

    def _lock_note(self, gid: str, why: str, now: float) -> None:
        if self.lock_gid != gid:
            self.lock_gid, self.lock_since, self.lock_retry_at = gid, None, 0.0
            self.lock_m.to("idle", f"now about {gid}", now=now)
        if self.lock_since is None:
            self.lock_since = now
        self.lock_why = why
        self.lock_m.key = gid
        if self.lock_m.is_("idle"):
            self.lock_m.to("waiting", why, now=now)

    def _lock_reset(self) -> None:
        self.lock_gid, self.lock_since, self.lock_retry_at, self.lock_why = None, None, 0.0, ""
        self.lock_m.key = ""
        self.lock_m.to("idle", "reachable / released")

    def _lock_wait(self, gid: str, x: Candidate, now: float) -> Decision | None:
        """[lock wait] Why a switch to the locked game can't go now, or None to switch.
        Never forever: after decider.lock_wait.max_s unreachable, the lock is given up."""
        if self.lock_gid != gid:
            self._lock_reset()
        if not x.target:
            self._lock_note(gid, "no working channel for it right now", now)
        if self.lock_since is None:
            return None
        waited = now - self.lock_since
        if waited >= self.cfg.get("decider.lock_wait.max_s"):
            self.lock_expired = gid
            self.lock_m.to("expired", f"unreachable for {waited:.0f}s: {self.lock_why}", now=now)
            return Decision(now, "stay", self.current, f"gave up on your locked game after {waited:.0f}s: "
                            f"{self.lock_why}", State.PAUSED, want=gid)
        if x.target and now >= self.lock_retry_at:
            return None
        if not x.target:
            return Decision(now, "stay", self.current, f"waiting on your locked game ({waited:.0f}s): {self.lock_why} "
                            f"— tap another game or Resume auto", State.PAUSED, want=gid)
        return Decision(now, "stay", self.current, f"couldn't get to your locked game ({self.lock_why}) — trying "
                        f"again in {math.ceil(self.lock_retry_at - now)}s", State.PAUSED, want=gid)

    # ----- catch-up replays ---------------------------------------------------

    def target_of(self, game_id: str | None) -> str | None:
        """Where a game is on, remembered after it ends (its last plays can still be replayed);
        also RedZone's channel while it's a candidate."""
        x = self._known.get(game_id or "")
        return x.target if x else self._target_of.get(game_id or "")

    def _parkable(self, m: Missed, now: float) -> bool:
        """[Nick, Nov 8 live, 879d99ed] A YouTube TV replay is a hidden-stick scrub of at most replay.yttv_max_lefts
        15 s steps (~117 s back at the park), so a play older than replay.scrub_max_play_age_s (tune + park time and
        the live-edge delay counted) can never be parked: every catch-up at 164-694 s was refused. Not queued."""
        cap = float(self.cfg.get("replay.scrub_max_play_age_s", 95) or 0)
        if cap <= 0 or self.cfg.get("replay.yttv_seek", "scrub") != "scrub":
            return True
        if (self.target_of(m.game_id) or "youtubetv:").split(":", 1)[0] != "youtubetv":
            return True
        return now - m.t <= cap

    def _replayable(self, x: Candidate | None) -> bool:
        if x is None or not x.target:
            return False
        return x.target.split(":", 1)[0] in self.cfg.get("replay.apps", ["youtubetv"])

    def on_screen_at(self, t: float) -> str | None:
        """What the screen showed at t: a game id, "replay", or None (black, mid-tune)."""
        cur = None
        for at, g in self._screen_log:
            if at <= t:
                cur = g
            else:
                break
        return cur

    def _saw(self, gid: str, start: float, end: float) -> bool:
        """Was this game on screen for the whole stretch? Partly seen counts as missed."""
        if self.on_screen_at(start) != gid:
            return False
        return all(g == gid for at, g in self._screen_log if start < at <= end)

    def _replay_pool(self, cands: dict[str, Candidate]) -> dict[str, Candidate]:
        """Games you can rewind: everything live, plus games that just ended (still on the DVR)."""
        return {**self._known, **cands}

    def _scan_missed(self, cands: dict[str, Candidate], now: float) -> None:
        """After each big play has aired, note whether you saw it. If not, queue a replay."""
        if not self.cfg.get("replay.enabled", True) or self.buffered_on():
            return   # [buffered jump] the hidden stick is the editor's next cut: no catch-up queue (overseer Nov 8 22:15)
        max_age = self.cfg.get("replay.max_age_s", 1800)
        pool = self._replay_pool(cands)
        for gid, tl in self.timelines.items():
            rw = self._rw(gid)
            # Finals too: a game is over in the data ~a minute before its last plays air.
            x = pool.get(gid)
            if x is None:
                continue
            d = self.delays.get(x.target or "unknown")
            play_len = PLAY_LEN_S.get(tl.league) or 8.0
            for e in tl.events:
                if e.kind != "play" or not e.big or (gid, e.pid or e.label) in self._judged:
                    continue
                air_end = e.seen + d.mu
                if now < air_end + 3:
                    continue
                self._judged.add((gid, e.pid or e.label))
                # Seen only if the screen showed this game from the snap to the whistle.
                seen = self._saw(gid, air_end - play_len, air_end)
                if now - e.seen > max_age:
                    continue
                kind = e.play_kind if e.play_kind in REPLAY_BASE else ("td" if "touchdown" in e.label.lower() else
                                                             "turnover" if "intercept" in e.label.lower() else "other")
                value = REPLAY_BASE.get(kind, 40) + min(25.0, x.scored.personal)
                m = Missed(gid, e.pid or e.label, e.label, kind, e.seen, value, seen_live=seen,
                           mine=gid in self._yours, aired=air_end)
                if rw and not seen:
                    m.must = self._collided(gid, air_end, play_len)
                    m.due_s = float(self.cfg.get("replay.collision_s", 180) if m.must
                                    else self.cfg.get("replay.catchup_s", 600))
                if seen:
                    self.done_replays.append(m)  # eligible only as an "instant replay" when all else is dead
                elif self._replayable(x):
                    self.queue.append(m)
        montage_age = max(max_age, self.cfg.get("replay.montage_age_s", 14400))
        self.queue = [m for m in self.queue if not m.replayed and m.game_id in pool
                      and now - m.t <= (montage_age if self._rw(m.game_id) else max_age) and self._parkable(m, now)]

    # ----- [replay.redzone_way] catch-up the RedZone way (Nick, Nov 4) ----------------------------
    # T1 everything dead: replay / reel (as before). T2 lull: nothing live worth staying for and a
    # missed play: it takes the dead ball. T3 "meanwhile": the screen's game just scored and is in
    # its PAT / kickoff / break: what happened elsewhere at the same time goes first. T4 collision:
    # a big play missed because another was on screen is due within replay.collision_s. T5 montage:
    # nothing live: every missed scoring play still on the DVR, however old. Value halves every
    # replay.catchup_s (most worth it within ~10 min); crunch restarts are never given up for one
    # (_holds_replays / _crunch_replay already budget that).

    def _lg(self, x) -> str | None:
        """[league overrides] The league of a candidate or game id, as league_overrides keys it: the
        full league (CBB is "cbb", never NBA's overrides; CFB is "cfb"). None: unknown."""
        if isinstance(x, str):
            x = self._known.get(x)
        return getattr(getattr(getattr(x, "scored", None), "league", None), "value", None)

    def _lc(self, dotted: str, x, default=None):
        """[league overrides] A setting as it reads for the league of this candidate / game id."""
        return _config.for_league(self.cfg, dotted, self._lg(x), default)

    def _rw(self, x=None) -> bool:
        """[RedZone way] for this game's league (default: the game on screen)."""
        return bool(self.cfg.get("replay.enabled", True)
                    and self._lc("replay.redzone_way", self.current if x is None else x, False))

    def _collided(self, gid: str, air_end: float, play_len: float) -> bool:
        """[T4] Was another game's big play on screen while this one aired?"""
        other = self.on_screen_at(air_end - play_len)
        if not other or other == gid or other == "replay":
            return False
        tl = self.timelines.get(other)
        x = self._known.get(other)
        if tl is None or x is None:
            return False
        d = self.delays.get(x.target or "unknown")
        return any(e.kind == "play" and e.big and abs(e.seen + d.mu - air_end) <= 2 * play_len for e in tl.events)

    def _just_scored(self, now: float) -> float | None:
        """[T3] When the game on screen last aired a big play, if within replay.meanwhile_s."""
        gid = self.current
        tl = self.timelines.get(gid or "")
        x = self._known.get(gid or "")
        if tl is None or x is None:
            return None
        d = self.delays.get(x.target or "unknown")
        win = self.cfg.get("replay.meanwhile_s", 120)
        for e in reversed(tl.events):
            if e.kind == "play" and e.big:
                return e.seen if 0 <= now - (e.seen + d.mu) <= win else None
        return None

    def _lull(self, watchable) -> bool:
        """[T2] Nothing live worth staying for: every game under replay.lull_score."""
        lull = self.cfg.get("replay.lull_score", 45)
        return all(x.scored.score < lull and not x.scored.lock and not x.scored.crunch for x in watchable.values())

    def _urgency(self, m: Missed, now: float) -> float:
        """0 while there's time; ramps to 1 as a missed play nears the end of its replay window."""
        urgent = self.cfg.get("replay.urgent_s", 1200)
        if m.seen_live or urgent <= 0:
            return 0.0
        due = self._due(m)
        if due:
            # [redzone way] ramps from half its due time to 1 at the due time, and stays there. The
            # 3-minute window counts from when the play aired, not from when the feed had it.
            w = self._window(m)
            age = self._aired_age(m, now) if w and due == w else now - m.t
            return max(0.0, min(1.0, (age - due / 2) / (due / 2)))
        left = self.cfg.get("replay.max_age_s", 1800) - (now - m.t)
        return max(0.0, min(1.0, 1.0 - left / urgent))

    @staticmethod
    def _aired_age(m: Missed, now: float) -> float:
        """How long ago the play aired on the screen: Nick's 3 minutes run from there (the feed has
        it a whole stream delay earlier, ~2 min with the time-shift buffer)."""
        return now - (m.aired or m.t)

    def _window(self, m: Missed | None = None) -> float:
        """[replay window] Nick, Nov 4: a missed moment is shown within this (2-3 min, the next dead
        moment) or not singly at all; older plays only fold into the montage. 0 = off.
        replay.window_leagues: only these leagues' plays ([] = every league)."""
        w = float(self.cfg.get("replay.window_s", 0) or 0)
        lgs = self.cfg.get("replay.window_leagues") or []
        if w and lgs and m is not None:
            x = self._known.get(m.game_id)
            lg = x.scored.league.base.value if x is not None else None
            if lg not in lgs:
                return 0.0
        return w

    def _due(self, m: Missed) -> float:
        w = self._window(m)
        return min(m.due_s, w) if (m.due_s and w) else (m.due_s or w)

    def _replay_value(self, m: Missed, now: float) -> float:
        age = now - m.t
        fresh = 1.0 - 0.5 * min(1.0, age / self.cfg.get("replay.max_age_s", 1800))
        if m.due_s:
            fresh = max(0.25, 0.5 ** (age / float(self.cfg.get("replay.catchup_s", 600))))
        # Last chance to see it: it climbs past dull live play.
        return m.value * fresh * (0.5 if m.seen_live else 1.0) + URGENT_BONUS * self._urgency(m, now)

    def _replay_cost(self, m: Missed, cands) -> float:
        x = self._replay_pool(cands).get(m.game_id)
        return self._deadline_cost(x.target if x else None) + self.cfg.get("replay.seek_s", 6) + self.cfg.get("replay.clip_s", 15)

    def _best_replay(self, cands, now: float, budget: float | None, allow_seen: bool,
                     top: float | None = None, margin: float | None = None,
                     exclude: str | None = None, yours: bool = True, montage: bool = False) -> Missed | None:
        """The replay to show next. With top: only one worth leaving live play scored top for.
        exclude: a game not to replay (e.g. the crunch game you're about to go back to).
        yours: your team's missed plays clear the `top` bar (replay.yours_first); False while your
        team's own game is on screen, so its live snaps aren't traded for its old ones."""
        crunch_on = not montage and self._crunch_on(cands)
        games = self._replay_pool(cands)
        pool = [m for m in self.queue if m.game_id != exclude and m.retry_at <= now and self._parkable(m, now)
                and self._replayable(games.get(m.game_id))
                and (not crunch_on or self._mine_cant_wait(m, now))]
        if not montage:     # [T5] older plays wait for the end-of-window montage (RedZone way, per league)
            pool = [m for m in pool if not self._rw(m.game_id) or now - m.t <= self.cfg.get("replay.max_age_s", 1800)]
        rw = self._rw()   # the triggers (meanwhile, collisions) follow the game on screen
        if not montage and (top is not None or not self.cfg.get("replay.window_fill", False)):
            # [replay window] past the window a missed play is skipped (montage only); with
            # replay.window_fill it may still fill dead time, but never trades live play for it
            pool = [m for m in pool if not self._window(m) or self._aired_age(m, now) <= self._window(m)]
        scored_at = self._just_scored(now) if rw else None
        meanwhile = (lambda m: scored_at is not None and m.game_id != self.current
                     and abs(m.t - scored_at) <= self.cfg.get("replay.meanwhile_s", 120))
        if allow_seen and not pool:
            recent = now - self.cfg.get("replay.instant_window_s", 900)
            done_ids = {(d.game_id, d.pid) for d in self.done_replays if d.replayed}
            pool = [m for m in self.done_replays if m.seen_live and m.t >= recent and m.retry_at <= now
                    and self._parkable(m, now)
                    and (m.game_id, m.pid) not in done_ids and m.game_id in cands and m.game_id != exclude
                    and self._replayable(cands[m.game_id])]
        if budget is not None:
            pool = [m for m in pool if self._replay_cost(m, cands) <= budget]
        if top is not None:
            margin = self.core.k.switch_margin if margin is None else margin
            # Your team's missed big play clears the bar against ordinary live play (crunch and locked
            # moments never reach here: a replay is only considered between plays of a normal stretch).
            yours = yours and self.cfg.get("replay.yours_first", True)
            pool = [m for m in pool if self._replay_value(m, now) >= top + margin or (yours and m.mine)
                    or (rw and (m.must or meanwhile(m)))]

        yours_first = self.cfg.get("replay.yours_first", True)

        def rank(m: Missed) -> tuple:
            # Your team's missed plays first (replay.yours_first), even against other games' last
            # chances: other games' plays that went urgent used to win every time, so your team's
            # TD could wait out its whole window. Then earliest deadline first among last chances
            # (a fresh TD can wait; they can't), else value.
            mine = int(m.mine and yours_first)
            if rw:   # [T4] collisions, then [T3] "meanwhile", then the most overdue / most valuable
                head = (mine, int(m.must), int(meanwhile(m)))
                if self._urgency(m, now) > 0:
                    return (*head, 1, 0, -m.t)
                return (*head, 0, 0, self._replay_value(m, now))
            if self._urgency(m, now) > 0:
                return (mine, 1, 0, -m.t)
            return (mine, 0, 0, self._replay_value(m, now))
        return max(pool, key=rank, default=None)

    def _mine_cant_wait(self, m: Missed, now: float) -> bool:
        """Your team's missed play may break the crunch block in replay.crunch_block_mine_leagues (its own
        game's league), once this long after it aired (NFL: its 3-min replay window can't wait out crunch)."""
        if not m.mine:
            return False
        lgs = self.cfg.get("replay.crunch_block_mine_leagues") or []
        x = self._known.get(m.game_id)
        lg = getattr(getattr(getattr(getattr(x, "scored", None), "league", None), "base", None), "value", None)
        if lg not in lgs:
            return False
        return now - (getattr(m, "aired", 0.0) or m.t) >= float(self.cfg.get("replay.crunch_block_mine_after_s", 90))

    def _crunch_on(self, cands) -> bool:
        """[crunch block, replay.crunch_block] A catch-up never starts while any live game is in crunch time
        or its run-up (NHL: from 3:00 left in a close 3rd, and OT): its next play may come while the reel
        is on. Cycle 04 review, locked: NHL crunch plays aired during a catch-up 2 -> 10 on the candidate.
        Your team's missed plays wait too, except in replay.crunch_block_mine_leagues once
        replay.crunch_block_mine_after_s has passed since they aired (_mine_cant_wait)."""
        return any((x.scored.crunch or x.scored.crunch_now) and not getattr(x.scored, "garbage", False)
                   and self._lc("replay.crunch_block", x, False)   # [league overrides] the crunch game's league
                   for x in cands.values()) or self._crunch_still_airing(cands, self._clock())

    def _crunch_still_airing(self, cands, now: float) -> bool:
        """[crunch block, replay.crunch_block_tail] A game the feed has already ended in crunch time (an
        OT winner) is still airing for a whole stream delay: its last plays reach the screen up to
        delay mu + 2 sigma after the feed dropped it. Keep the block until then, else a catch-up reel
        starts over the OT goal (NHL dev 20261027: 6 crunch plays aired under the reel)."""
        if not self.cfg.get("replay.crunch_block_tail", False):
            return False
        for gid, (seen, target) in list(self._crunch_tail.items()):
            if gid in cands:
                continue
            d = self.delays.get(target or "unknown")
            if now - seen <= d.mu + 2 * d.current_sigma(now) + float(self.cfg.get("polling.live_s", 10)):
                return True
            del self._crunch_tail[gid]
        return False

    def _break_remaining(self, gid: str, cands, now: float) -> float:
        """Conservative break budget, never a typical ad length treated as a deadline."""
        tl = self.timelines.get(gid)
        x = cands.get(gid)
        if tl is None or x is None:
            return 0.0
        d = self.delays.get(x.target or "unknown")
        old = self._stoppage_remaining(tl, d, now)
        if x.scored.league.base.value not in (self.cfg.get("decider.entry.leagues") or ["nfl"]):
            return old
        # [cut timing] The dead window the screen is in, ended at the real restart (the game clock,
        # the faceoff, the next snap) or, still open, at the shortest that kind has ever run.
        m = 2 * d.current_sigma(now)
        until = tl.dead.dead_until(now - d.mu - m) if tl.dead.marks else None
        return old if until is None else max(old, until + d.mu - m - now)

    def _stoppage_remaining(self, tl: Timeline, d, now: float) -> float:
        start, end = tl.last("stop_start"), tl.last("stop_end")
        if start is None:
            return 0.0
        if end is not None and end.seen >= start.lo:
            return end.seen + d.mu - 2 * d.current_sigma(now) - now
        try:
            floor = MIN_BREAK_S.get(tl.league, {}).get(Stoppage(start.label), 0.0)
        except ValueError:
            floor = 0.0
        if not floor:
            return 0.0
        return start.lo + d.mu + floor - 2 * d.current_sigma(now) - now

    def _replay_overrun(self, m: Missed, cands, now: float) -> float:
        """[mid-play guard] The replay's end is a cut too: play on until the replayed play has ended.

        The rewind is whole seek_step_s presses (control.py rounds UP), so the clip can start up
        to one step early, and if the screen runs 2σ further behind than we think, 2σ earlier
        still. clip_s alone (15 s from 3 s before the snap) can end mid-play. (Padding by the
        exact overshoot instead measured worse on the scorecard, so: the worst case.)"""
        x = cands.get(m.game_id)
        d = self.delays.get((x.target if x else None) or self._known_target.get(m.game_id) or "unknown")
        tl = self.timelines.get(m.game_id)
        play_len = PLAY_LEN_S.get(tl.league, 8.0) if tl else 8.0
        need = play_len + 3.0 + self.cfg.get("replay.seek_step_s", 10) + 2 * d.current_sigma(now) \
            + AFTER_PLAY_BUFFER_S          # 3.0 = Missed.back_s lead_s
        return max(0.0, need - self.cfg.get("replay.clip_s", 15))

    def abort_replay(self) -> None:
        """An interrupted replay is still missed; do not credit its planned duration."""
        m = self.replaying
        if m is None:
            return
        if not m.replayed and not m.seen_live and m not in self.queue:
            self.queue.append(m)
        self._clip = None
        self.replay_until = 0.0
        # the current picture is still old footage until a live tune
        self.screen_m.to("after_replay", f"replay of {m.game_id} interrupted")
        self._screen_log.append((self._clock(), None))

    def _start_replay(self, m: Missed, cands, now: float, *, confirmed: bool = False) -> None:
        self.abort_replay()
        self._land_behind(None, 0.0)   # the replay seeks from the live edge; nothing stays behind
        if m in self.queue:
            self.queue.remove(m)
        self._clip = m
        self.screen_m.to("replaying", f"{'confirmed ' if confirmed else ''}replay: {m.kind} in {m.game_id}", now=now)
        # A real controller has already tuned, sought, and begun playback at
        # confirmation. Counting preparation again needlessly holds old footage.
        duration = self.cfg.get("replay.clip_s", 15) if confirmed else self._replay_cost(m, cands)
        self.replay_until = now + duration + self._replay_overrun(m, cands, now)
        self.replay_exit_after = self.replay_until
        if confirmed:
            self._arrival = 0.0
        if self.current and self.current != m.game_id and self.current != self.home_break_visit:
            self.stack.append(self.current)
        self._current = m.game_id
        self.since = now
        self.history.append((now, m.game_id, "replay"))
        self._screen_log.append((now, "replay"))

    # ----- [cut timing] entering on a dead ball ------------------------------------

    def entry_plan(self, gid: str, cands: dict[str, Candidate], now: float, cost: float | None = None,
                   urgent: bool = False, possession: bool = False) -> EntryPlan | None:
        """How to come into gid right now (None: no timing evidence, e.g. RedZone or no timeline)."""
        tl = self.timelines.get(gid)
        x = cands.get(gid) or self._known.get(gid)
        if tl is None or x is None:
            return self._entry_plan(gid, cands, now, cost, urgent, possession)
        # [EXP-211] an open stoppage counts as dead only as far as a feed this slow would already show the
        # restart: an unmeasured lag reads 10 s, which landed NHL entries after the faceoff (EXP-303).
        tl.dead.lag_floor = float(self._lc("decider.entry.lag_floor_s", x, 0.0) or 0.0)
        try:
            return self._entry_plan(gid, cands, now, cost, urgent, possession)
        finally:
            tl.dead.lag_floor = 0.0

    def _entry_plan(self, gid: str, cands: dict[str, Candidate], now: float, cost: float | None,
                    urgent: bool, possession: bool) -> EntryPlan | None:
        c = self.cfg
        if gid in PSEUDO or not c.get("decider.entry.enabled", True):
            return None
        tl = self.timelines.get(gid)
        x = cands.get(gid) or self._known.get(gid)
        if tl is None or x is None or not x.target or not tl.dead.marks:
            return None
        co = bool(self._lc("decider.entry.confirmed_dead_only", x, False))
        if tl.dead.confirmed_only != co:
            tl.dead.confirmed_only, tl.dead._windows = co, None
        d = self.delays.get(x.target)
        base_mu = d.mu - d.behind     # a fresh tune lands on the live edge
        margin = 2 * d.current_sigma(now) + ENTRY_SLACK_S
        if cost is None:
            cost = self.entry_cost(x.target) if self.entry_cost is not None else self._deadline_cost(x.target)
        if not math.isfinite(cost):
            return None
        can = bool(c.get("decider.entry.rewind", True)) and self._replayable(x)
        from redzone.contracts.switching import seek_model
        sk = seek_model(c)
        cue = self._lc("decider.entry.cue", x, "seek")
        pause = cue == "pause"
        # [EXP-089] these leagues never enter on a landing the data can't call (rule #1)
        unknown_ok = x.scored.league.base.value not in (c.get("decider.entry.unknown_wait_leagues") or [])
        # [EXP-024 conformal floor] no landing on less doubt than tau sigmas (tau calibrated per league on dev)
        floor = 0.0
        if c.get("decider.entry.conformal.enabled"):
            floor = float(c.get(f"decider.entry.conformal.tau.{x.scored.league.base.value}") or 0.0) * d.current_sigma(now)
        lo_cost = min(cost, float(c.get("decider.entry.fastest_s", ENTRY_FASTEST_S)))
        # [D-08] the centred / urgent thin-margin passes only for leagues that passed their gate on them
        thin = x.scored.league.value in (c.get("decider.entry.thin_margin_leagues") or [])
        relaxed = float(c.get("decider.entry.relaxed_margin_s", ENTRY_RELAXED_S)) if thin else None
        urg = float(c.get("decider.entry.urgent_margin_s", ENTRY_URGENT_S)) if urgent and thin else None
        join = self._join_args(x, urgent)
        if cue == "hidden" and can:
            # [seek model] seek on the hidden stick, then flip: the stall passes before the flip and
            # puts the stream just as far behind, so the picture's content doesn't depend on it (no
            # stall doubt); the flip waits for the presses (replay.hidden_seek_keys_s) and the stall (mid).
            plan = plan_entry(tl.dead, base_mu, margin, now, cost + float(self.cfg.get("replay.hidden_seek_keys_s")), can_rewind=can,
                              step=sk.step, max_back=float(c.get("decider.entry.max_rewind_s", ENTRY_MAX_REWIND_S)),
                              relaxed=relaxed,
                              cost_lo=lo_cost + float(self.cfg.get("replay.hidden_seek_keys_s")),
                              urgent=urg,
                              lag=float(self._lc("decider.entry.landing_lag_s", x, 0.0)), possession=possession,
                              unknown_ok=unknown_ok, floor=floor, **join)
            if plan.ok and plan.rewind_s:
                plan.rewind_s += sk.stall_mid
            return plan
        return plan_entry(tl.dead, base_mu, margin, now, cost, can_rewind=can,
                          # [seek model] a pause cue lands where it pauses (half-second grain, no stall)
                          step=float(self.cfg.get("replay.pause_cue_grain_s")) if pause else sk.step,
                          stall=(0.0, 0.0) if pause else (sk.stall_lo, sk.stall_hi),
                          max_back=float(c.get("decider.entry.max_rewind_s", ENTRY_MAX_REWIND_S)),
                          relaxed=relaxed,
                          cost_lo=min(cost, float(c.get("decider.entry.fastest_s", ENTRY_FASTEST_S))),
                          urgent=urg,
                          lag=float(self._lc("decider.entry.landing_lag_s", x, 0.0)), possession=possession,
                          unknown_ok=unknown_ok, floor=floor, **join)

    def _join_args(self, x: Candidate, urgent: bool) -> dict:
        """[landing bars, jump/buffered only] plan_entry's walk / after-play arguments for this league (basketball's
        walk up court after a shot; football's preference for landing after the replay). Normal mode keeps its graded
        dead-ball landings."""
        if not self.jump_on():
            return {}
        walk = cuts.walk_params(self.cfg, x.scored.league)
        after = 0.0
        if x.scored.league.base == League.NFL and not urgent:
            after = float(cuts.join_cfg(self.cfg, "after_play_s"))
        return {"walk": walk, "after_play_s": after}

    def _live_fallback(self) -> bool:
        """[D-01] Rule #1: an entry with no proven dead ball waits, in every league. decider.entry.allow_live_fallback
        (off) brings back the old go-in-live exits (crunch_now live/rewind, max_wait_s, rewind_only, no_wait_leagues)."""
        return bool(self.cfg.get("decider.entry.allow_live_fallback", False))

    def _entry_no_data(self, gid: str, x: Candidate | None) -> bool:
        """[D-01] A live game with no dead windows in the data (first poll, a feed gap): nothing proves a dead ball, so
        the entry waits instead of going in blind. The RedZone channel and an entry-timing kill switch aren't that."""
        if gid in PSEUDO or x is None or not x.target or not self.cfg.get("decider.entry.enabled", True):
            return False
        tl = self.timelines.get(gid)
        return tl is None or not tl.dead.marks

    def _entry_dark(self, gid: str, x: Candidate | None, now: float) -> bool:
        """[P1-REVIEW-05] This game's feed can't prove a dead ball: no marks at all, or the data has gone quiet."""
        if gid in PSEUDO or x is None or not x.target or not self.cfg.get("decider.entry.enabled", True):
            return False
        tl = self.timelines.get(gid)
        return tl is None or not tl.dead.marks or tl.fresh_at is None or now - tl.fresh_at > STALE_DATA_S

    def _without_dark_targets(self, cands: dict[str, Candidate], now: float) -> dict[str, Candidate]:
        """[P1-REVIEW-05] An entry waits on a dark feed (Rule #1 forbids going in blind) only for
        decider.entry.no_data_wait_s: after that the game sits out this pick, so the next game with a provable landing
        gets its turn instead of the screen freezing on a finished game. It comes back the moment its feed does. With
        nothing else to go to the wait goes on (no last resort: Nick's phone, tapped twice, forces it) and says so once.
        Your locked or focused game is never dropped."""
        for gid in list(self._dark_since):
            if gid not in cands or not self._entry_dark(gid, cands[gid], now):
                del self._dark_since[gid]
                self._dark_said.discard(gid)
        wait = float(self.cfg.get("decider.entry.no_data_wait_s", 45.0))
        dropped = {gid for gid, at in self._dark_since.items() if now - at >= wait and gid != self.current
                   and gid not in (self.user.locked_game, self.user.focus_game)}
        if not dropped:
            return cands
        kept = {gid: x for gid, x in cands.items() if gid not in dropped}
        if any(x.target and gid != self.current and not self._entry_dark(gid, x, now) for gid, x in kept.items()):
            return kept
        if self.current not in cands:
            for gid in sorted(dropped - self._dark_said):
                self._dark_said.add(gid)
                self.entry_stuck = gid
        return cands

    def _entry_urgent(self, d: Decision, x: Candidate | None, cands) -> bool:
        """May this entry take the thin-margin landing? The flip's re-check (Engine._entry_valid) asks the same question."""
        # [nhl pp] A power play or a close 3rd makes a game sticky for break visits, but is no reason
        # to come in on a tighter margin: that only buys mid-play entries.
        urgent = d.kind == "crunch" or (x is not None and (x.scored.crunch or
                                                           self._sticky_home(d.target, cands, entry=True)))
        if d.kind == "ended" and self._lc("decider.entry.ended_strict", x, False):
            urgent = False   # [landing lag] the game on screen is over: nothing is lost by a strict landing
        return urgent

    def _cut_timed(self, gid: str | None, cands) -> bool:
        """Cut timing is on for this game's league (decider.entry.leagues). Scorecards, Nov 3: NFL
        big plays live 24.5 -> 29.9% and crunch live +1.3 points, but NBA big plays seen 95.4 -> 90.8%
        and NHL crunch live 87.5 -> 82.7%: football only until the basketball/hockey timing improves."""
        x = cands.get(gid) if gid else None
        if x is None:
            return False
        lg = x.scored.league.base.value
        return lg in (self.cfg.get("decider.entry.leagues") or ["nfl"])

    def _entry_only(self, x: Candidate | None, d: Decision) -> bool:
        """Leagues whose switches wait for a whistle on the way IN only (decider.entry.hold_only),
        never into crunch time or as the night's first pick (nothing on screen to protect). Hockey,
        scorecards Nov 3: mid-play entries 165 -> 15, crunch live 87.5 -> 88.3%, big plays seen
        99.7 -> 99.5%. The rest of cut timing (holding for a big play, stretching a stoppage) cost
        hockey crunch time, so it stays football-only."""
        if x is None:
            return False
        if d.kind == "start" and not self._lc("decider.entry.start_timed", x, False):
            return False   # [EXP-739] off: the night's first pick goes in live (6 of 8 NHL mid-play entries, #302)
        if x.scored.crunch_now and self._crunch_entry_mode(x) == "live" and self._live_fallback():
            return False
        if x.scored.crunch and not x.scored.crunch_now and \
                not self.cfg.get("decider.entry.hold_crunch_runup", True):
            return False
        if x.scored.league.base.value in (self.cfg.get("decider.entry.rewind_only") or []):
            return True    # plan the landing; _time_entry never waits for these (see there)
        if d.kind == "chase" and x.scored.imminent and self._live_fallback():
            # A chase for a goal off a 5-on-3, a fresh power play, a late empty net or a penalty
            # shot: waiting for a whistle misses it (behind the Rule #1 fallback since D-01).
            return False
        return x.scored.league.base.value in (self.cfg.get("decider.entry.hold_only") or [])

    def _possession_start(self, x: Candidate | None, d: Decision) -> bool:
        """[possession start] Nick, Nov 4: this entry cues the hidden stick to the last dead ball before
        the current possession (the time-shift buffer), never lands mid-play and never waits for the
        next break while that dead ball is in reach. decider.entry.possession_start: off | crunch (the
        crunch_now entries) | all (every timed entry)."""
        mode = self._lc("decider.entry.possession_start", x, "off")
        if mode in (None, False, "off", "false") or x is None:
            return False
        if mode == "all":
            return True
        if mode != "crunch":
            return False
        return bool(x.scored.crunch_now) or d.kind == "crunch"

    def _crunch_entry_mode(self, x=None) -> str:
        """[nhl crunch entry] Coming into hockey inside crunch's last 2:00 / OT: "live" (go now),
        "rewind" (land on the last whistle with a rewind when one fits, else go now), or "wait"
        (rewind or wait for a whistle, like any entry)."""
        return str(self._lc("decider.entry.crunch_now", x, "live"))

    def _dwell(self, d: Decision, cands, now: float, home_before) -> Decision:
        """[EXP-213, RC8 switch count] Stay at least decider.dwell.min_s on a game before an optional switch
        (decider.dwell.kinds: a break visit, a chase, a better game...). With the time-shift buffer the decider
        sees every break coming and hops more (NBA B=90 1.58x, NFL B=75 1.80x the live switch rate against a
        1.5x bar). Never holds crunch time, a game that ended, the way home, your taps or a first pick, and
        only ever turns a switch into a stay, so Rule #1 is untouched. 0 = off."""
        if d.action != "switch" or not self.current or self.current in PSEUDO:
            return d
        x = cands.get(self.current)
        min_s = float(self._lc("decider.dwell.min_s", x, 0) or 0)
        if min_s <= 0 or d.kind not in set(self._lc("decider.dwell.kinds", x, None) or ()):
            return d
        left = self.since + min_s - now
        if left <= 0:
            return d
        self._home_restore(home_before, "dwell: a stay")
        state = State.REPLAY if self.after_replay else d.state
        return Decision(now, "stay", self.current, f"just got here: {left:.0f}s before an optional switch: {d.reason}",
                        state, d.gate, want=d.target)

    def _time_entry(self, d: Decision, cands, now: float, home_before) -> Decision:
        """Hold a switch until its picture would come up on a dead ball, or land it with a rewind."""
        if d.action != "switch" or not d.target or d.kind in ENTRY_EXEMPT:
            return d
        x = cands.get(d.target)
        if not self._cut_timed(d.target, cands) and not self._entry_only(x, d):
            return d
        if d.target == self.current and not self.after_replay:
            return d
        urgent = self._entry_urgent(d, x, cands)
        pos = self._possession_start(x, d)
        if pos and self.possession_policy is not None:
            # [possession start] the learned jump / stay policy (lane 1); default (None) is jump
            if self.possession_policy(d, x, now) == "stay":
                self._home_restore(home_before, "time_entry: a stay")
                state = State.REPLAY if self.after_replay else d.state
                return Decision(now, "stay", self.current, f"possession policy: stay: {d.reason}", state,
                                d.gate, want=d.target)
        plan = self.entry_plan(d.target, cands, now, urgent=urgent or pos, possession=pos)
        if plan is None:
            if self._live_fallback() or not self._entry_no_data(d.target, x):
                return d
            # [D-01] nothing proves a dead ball yet: wait for the first dead window in the data (Rule #1)
            self._dark_since.setdefault(d.target, now)   # [P1-REVIEW-05] for no_data_wait_s, then the next game
            self._home_restore(home_before, "time_entry: a stay")
            state = State.REPLAY if self.after_replay else d.state
            return Decision(now, "stay", self.current, f"no play-by-play to time the landing: {d.reason}", state,
                            d.gate, want=d.target)
        if plan.ok and not plan.rewind_s and not (urgent or pos):
            wait = self._break_tail_wait(d.target, x, cands, now)
            if 0 < wait <= float(self.cfg.get("polling.live_s", 10)) and self.cfg.get("decider.entry.break_tail_defer"):
                d.defer_s = round(wait, 1)   # [EXP-803 A1] the tail comes inside this tick: cut then, not a tick late
                wait = 0.0
            if wait > 0:
                # [EXP-728/736] the picture would come up early in the break: hold on what's on screen
                self._home_restore(home_before, "time_entry: a stay")
                state = State.REPLAY if self.after_replay else d.state
                return Decision(now, "stay", self.current, f"break tail: in {wait:.0f}s: {d.reason}", state,
                                d.gate, want=d.target)
        if plan.ok:
            d.rewind_s = plan.rewind_s
            if plan.rewind_s and self._lc("decider.entry.cue", x, "seek") in ("pause", "hidden"):
                d.cue = self._lc("decider.entry.cue", x)
            return d
        fallback = self._live_fallback()   # [D-01] every exit below that goes in live needs it
        if pos:
            pass   # no dead ball in reach behind the picture: wait for one (rule #1), never go live
        elif fallback and x is not None and (x.scored.crunch_now and self._crunch_entry_mode(x) == "rewind"
                                             or x.scored.league.base.value in (self.cfg.get("decider.entry.rewind_only") or [])):
            return d   # no clean landing in reach: crunch time (and rewind-only leagues) don't wait
        if fallback and not pos and x is not None and \
                x.scored.league.base.value in (self.cfg.get("decider.entry.no_wait_leagues") or []):
            # Continuous play: holding a switch for a dead ball missed more than it saved (scorecards,
            # Nov 3); enter live unless a rewind lands it clean (decided above).
            return d
        # [core] A whistle wait can be bounded (decider.entry.max_wait_s.<league>, off by default) where
        # waiting costs the most: a finished game on screen, or a final period. In basketball the next
        # dead ball can be minutes away (NBA Oct 23: a 3.5-minute wait on a final game into crunch time).
        lg = x.scored.league.base.value if x is not None else ""
        cap = _config.for_league(self.cfg, f"decider.entry.max_wait_s.{lg}", self._lg(x))
        w = self._entry_wait
        if w is None or w[0] != d.target or now - w[2] > 3 * float(self.cfg.get("polling.live_s", 10)):
            w = (d.target, now, now)
        self._entry_wait = (d.target, w[1], now)
        if fallback and not pos and cap is not None and now - w[1] >= float(cap) and (
                d.kind == "ended" or (x is not None and getattr(x.scored, "late", False))):
            return d
        # Not yet: whatever the switch planned (a visit's way home) waits with it.
        if self._entry_dark(d.target, x, now):
            self._dark_since.setdefault(d.target, now)   # [P1-REVIEW-05] a dark feed never logs the dead ball we wait for
        self._home_restore(home_before, "time_entry: a stay")
        state = State.REPLAY if self.after_replay else d.state
        return Decision(now, "stay", self.current, f"{plan.why}: {d.reason}", state, d.gate,
                        want=d.target)

    def _break_tail_wait(self, gid: str, x, cands, now: float) -> float:
        """[EXP-728/736, decider.entry.break_tail: leagues] Seconds to hold a switch into a game in a break so
        its picture comes up at most decider.entry.break_tail_s before the restart: the logged restart, else
        the break's early-side length (break_tail_q of EXP-1500's table), both on the early side of the delay.
        0 = go now (no break, off for the league, the game on screen is in crunch, or no length known)."""
        if x is None or not x.target:
            return 0.0
        if x.scored.league.value not in (self.cfg.get("decider.entry.break_tail") or []):
            return 0.0
        cur = cands.get(self.current or "")
        if cur is not None and cur.scored.crunch_now:
            return 0.0          # the game on screen matters now: no reason to linger there, nor to rush
        tl = self.timelines.get(gid)
        if tl is None or not tl.dead.marks:
            return 0.0
        from redzone.decider import break_tail
        cost = self.entry_cost(x.target) if self.entry_cost is not None else self._deadline_cost(x.target)
        if not math.isfinite(cost):
            return 0.0
        d = self.delays.get(x.target)
        base = d.mu - d.behind - 2 * d.current_sigma(now)   # a fresh tune shows the live edge; early side
        arrive = now + cost - base                           # the real time on screen when the picture comes up
        ws = tl.dead.windows()
        i = next((i for i in range(len(ws) - 1, -1, -1) if ws[i].start <= arrive), None)
        if i is None or (ws[i].end is not None and ws[i].end <= arrive):
            return 0.0          # live play on arrival
        # A dead stretch is often several windows back to back (a foul, its free throws, then the timeout):
        # the break is the stretch, its restart the last window's.
        gap = float(self.cfg.get("decider.entry.break_tail_chain_s"))
        while i + 1 < len(ws) and ws[i].end is not None and ws[i + 1].start - ws[i].end <= gap:
            i += 1
        w = ws[i]
        kind = break_tail.WINDOW_KIND.get(w.kind)
        if kind is None:
            return 0.0          # not a break the table knows (a whistle stretch)
        if w.end is not None:
            restart = w.end
        else:
            n = break_tail.length(x.scored.league.base.value, kind, x.target,
                                  str(self.cfg.get("decider.entry.break_tail_q")))
            if w.kind == "timeout_long" and x.scored.league.base == League.NBA \
                    and self.cfg.get("decider.entry.break_tail_tv_split"):
                # [EXP-803 A3] a full NBA timeout carries the TV break: its own early-side length, not the
                # "timeout" row that mixes in short timeouts (q25 126 s vs ~170-210 s, Data F's cdn rows)
                n = float(self.cfg.get("decider.entry.break_tail_tv_timeout_s"))
            if n is None:
                return 0.0
            restart = w.start + n
        return max(0.0, restart - arrive - float(self.cfg.get("decider.entry.break_tail_s")))

    def _scenario(self, d: Decision, cands, now: float, home_before) -> Decision:
        """[scenario tree] The branch of the scenario table that matches the slate (redzone/decider/
        scenarios.py, GAME-01..03, 21, 22), when it disagrees with the generic pick. Nick's buttons, a lock,
        a focus mode, a replay in progress and safe mode win."""
        sc = self.scenarios
        if not sc.enabled() or self.current is None or d.kind == "user" or self.replaying or self.safe_mode \
                or self.user.paused or self.user.locked_game or self.user.focus_mode != FocusMode.AUTOPILOT:
            return d
        plan = sc.step(self, cands, now)
        if plan is None:
            return d
        br, tag = plan.branch, f"playbook={plan.branch['id']}"
        if br["do"] == "hold":
            if d.action in ("switch", "replay") and d.target != self.current:
                sc.seen[br["id"]] = sc.seen.get(br["id"], 0) + 1
                self._home_restore(home_before, "scenario: a stay")
                return Decision(now, "stay", self.current, f"{tag}: your team's crunch time: {d.reason}",
                                d.state, d.gate)
            return d
        b = plan.b
        if b is None or b == self.current or b not in cands:
            return d
        if d.action == "switch" and d.target == b:
            d.reason = f"{tag}: {d.reason}"
            return d
        if d.action == "replay" or d.kind == "crunch":
            return d    # a catch-up reel or crunch time already fills A's break: a visit never displaces it
        gate = self._gate(cands.get(self.current), now) if self.current in cands else None
        if not (gate and gate.safe):
            return d    # rule #1: a branch never cuts out of a play; it acts at A's next safe moment
        sc.seen[br["id"]] = sc.seen.get(br["id"], 0) + 1
        if br.get("back") == "restart":
            self._home_set(plan.a, self._home_at, "scenario")
        kind = "break_visit" if br["do"] == "visit" else "crunch"
        return Decision(now, "switch", b, self._why(cands[b], f"{tag}: "), d.state, d.gate, kind=kind)

    def _safe(self, d: Decision, cands, now: float, home_before) -> Decision:
        """[playbooks SAFE-01/02/03/05] A switch the safety playbooks hold (redzone/decider/playbooks.py)."""
        sf = self.safety
        if not sf.enabled():
            return d
        sf.tick()
        hit = sf.check(self, d, cands, now)
        if hit is None:
            return d
        pid, why = hit
        sf.seen[pid] = sf.seen.get(pid, 0) + 1
        sf.events.append((now, pid, d.target))
        self._home_restore(home_before, "safe: a stay")
        state = State.REPLAY if self.after_replay else d.state
        return Decision(now, "stay", self.current, f"playbook={pid}: {why}: {d.reason}", state, d.gate,
                        want=d.target if d.action == "switch" else None)

    def _lookahead_on(self, x=None) -> bool:
        """noggin.lookahead.enabled, scoped by noggin.lookahead.leagues ([] = every league) to the game x."""
        if not self.cfg.get("noggin.lookahead.enabled", False):
            return False
        lgs = self.cfg.get("noggin.lookahead.leagues") or []
        if not lgs or x is None:
            return True
        return x.scored.league.base.value in lgs

    def _crunch_guard(self, d: Decision, cands, now: float, home_before) -> Decision:
        """[crunch guard, with noggin.lookahead.enabled] A catch-up replay of another game never takes the
        screen off a game inside its crunch window: the replay waits (it stays catchable for 10 min).
        Under tighter sync (the 3 s what-if) these replays cost NHL crunch live 80.2 -> 77.6."""
        if d.action != "replay" or not self._lookahead_on() or self.current is None:
            return d
        cur = cands.get(self.current)
        if cur is None or not self._lookahead_on(cur) or not cur.scored.crunch_now or (d.replay and d.replay.game_id == self.current):
            return d
        self.lookahead_why["crunch_guard"] = self.lookahead_why.get("crunch_guard", 0) + 1
        self._home_restore(home_before, "crunch_guard: a stay")
        return Decision(now, "stay", self.current, f"crunch time here: catch-up waits ({d.reason})", d.state, d.gate)

    def _crunch_floor(self, d: Decision, d0: Decision, cands, now: float, home_before) -> Decision:
        """[crunch floor, SAFE-09] Crunch is a learned state and crunch vs crunch is decided by value (Nick,
        Nov 4 4:15 PM), but two floors hold while the game on screen is inside its crunch window:
        (1) no overlay or model (scenario, clairvoyance, PICK, later the Mind and watch value) turns the app's
            own decision into a leave for a game that isn't in crunch: models choose between crunches;
        (2) a LOW-value game never pulls us out, not even by the app's own switch or a catch-up reel: below
            decider.crunch.floor_low_value (watch value once it ships, else today's score; 0 = off until
            watch value: at 50 on today's score it cost locked NFL crunch live, Nov 4). An ad-break round trip (kind break_visit) and your own press are not leaves.
        Off only with decider.crunch.floor: false."""
        if not self.cfg.get("decider.crunch.floor", True) or d.action not in ("switch", "replay") \
                or d.kind in ("user", "break_visit") or self.replaying or not self.current:
            return d
        cur = cands.get(self.current)
        if cur is None or not cur.scored.crunch_now or getattr(cur.scored, "garbage", False):
            return d
        if d.action == "replay":
            if d.replay is None or d.replay.game_id == self.current:
                return d                   # this game's own instant replay
            x, value = None, float(d.replay.value)
        else:
            x = cands.get(d.target)
            if d.target == self.current or x is None:
                return d
            if x.scored.crunch or x.scored.crunch_now:
                return d                   # crunch vs crunch: decided by value, not by this floor
            value = self._floor_value(x.scored)
        overlay = (d.action, d.target, d.kind) != (d0.action, d0.target, d0.kind)
        low = value < float(self.cfg.get("decider.crunch.floor_low_value", 0) or 0)
        if not (overlay or low):
            return d
        why = "an overlay can't leave crunch for a non-crunch game" if overlay else f"{value:.0f} is a low-value game"
        self.lookahead_why["crunch_floor"] = self.lookahead_why.get("crunch_floor", 0) + 1
        self._home_restore(home_before, "crunch_floor: a stay")
        return Decision(now, "stay", self.current, f"crunch floor: {why} ({d.reason})", d.state, d.gate)

    @staticmethod
    def _floor_value(s) -> float:
        """Lane 1's Scored.watch_value once it ships, else today's score."""
        wv = getattr(s, "watch_value", None)
        return float(wv) if wv is not None else float(s.score)

    def _clairvoyant(self, d: Decision, cands, now: float, home_before) -> Decision:
        """[GAME-04 clairvoyance, noggin.lookahead.enabled] The feed already knows a big play in game X
        that the screen hasn't shown (redzone/noggin/lookahead.py): pre-tune X, and flip so the picture
        comes up on X's last dead ball before the play airs. Only ever turns a "stay" into a visit, only
        when what's on screen has nothing of its own coming, isn't crunch time, a lock or your team, and
        can be left (the leave gate); entry timing and the safety playbooks still apply after it."""
        c = self.cfg
        visit = d.action == "switch" and d.kind == "break_visit"
        if not self._lookahead_on() or not (d.action == "stay" or visit) or self.current is None \
                or self.current in PSEUDO or self.replaying or self.safe_mode or self.user.paused \
                or self.user.locked_game or self.user.focus_mode != FocusMode.AUTOPILOT:
            return d
        cur = cands.get(self.current)
        if cur is None or not cur.target:
            return d
        s = cur.scored
        if not visit and (s.crunch or s.lock or s.personal >= c.get("decider.crunch.hold_personal", 5)):
            return d
        if d.want and d.want in cands and cands[d.want].scored.crunch:
            return d                       # waiting to leave for crunch time: keep pre-tuning it (crunch first)
        maxs = float(c.get("noggin.lookahead.max_sigma_s", 10))
        mine = ahead(self.timelines.get(self.current), self.delays.get(cur.target), now, maxs)
        if mine is not None and mine.big is not None and not visit:
            return d                       # our own big play is on its way: stay for it
        best = None
        for gid, x in cands.items():
            if gid == self.current or gid in PSEUDO or not x.target or x.stale or gid in self.user.muted \
                    or not self._lookahead_on(x):
                continue
            a = ahead(self.timelines.get(gid), self.delays.get(x.target), now, maxs)
            if a is not None and a.big is not None and (best is None or a.big.lo < best[1]):
                best = (gid, a.big.lo, a.big)
        if best is None:
            return d
        gid, tb, ev = best
        x = cands[gid]
        dl = self.delays.get(x.target)
        cost = self.entry_cost(x.target) if self.entry_cost is not None else self._deadline_cost(x.target)
        lead = float(c.get("noggin.lookahead.lead_s", 45))
        tag = f"playbook=GAME-04: {ev.label[:40]} on its way in {_short(x.scored)}"
        # A break visit the generic logic already chose stands unless X's play can be caught from here.
        wait = d if visit else Decision(now, "stay", self.current, f"{tag} (pre-tuning): {d.reason}", d.state,
                                        d.gate, want=gid)
        why = self.lookahead_why
        gate = self._gate(cur, now)
        if not (gate and gate.safe):
            why["gate"] = why.get("gate", 0) + 1
            return wait
        plan = self.entry_plan(gid, cands, now, cost=cost)
        if plan is None or not plan.ok or plan.landing is None or plan.landing.state != "dead":
            why["no_landing"] = why.get("no_landing", 0) + 1
            return wait
        land = now + cost - (dl.mu - dl.behind) - plan.rewind_s      # real time the picture comes up on
        margin = 2 * dl.current_sigma(now) + ENTRY_SLACK_S
        if land > tb - margin:
            why["too_late"] = why.get("too_late", 0) + 1
            return d                       # can't get there before it airs: the catch-up replay's job
        if tb - land > lead:
            why["too_early"] = why.get("too_early", 0) + 1
            return wait                    # too early: a later dead ball is closer to the play
        if visit:
            why["visit_kept" if d.target == gid else "visit_redirected"] = \
                why.get("visit_kept" if d.target == gid else "visit_redirected", 0) + 1
            if d.target == gid:
                d.reason = f"{tag}: {d.reason}"
                return d
            return Decision(now, "switch", gid, f"{tag} ({tb - land:.0f} s after we land)", d.state, d.gate,
                            kind="break_visit")
        why["switch"] = why.get("switch", 0) + 1
        self._home_set(self.current, self._home_at, "a chase: back here after")
        return Decision(now, "switch", gid, f"{tag} ({tb - land:.0f} s after we land)", d.state, d.gate,
                        kind="chase")

    def _pick_scores(self, cands: dict, now: float, model_name: str = "v1", tune: float = 5.0):
        """(cur, scores) from the Reflex PICK model on the live books of every target game, or None when PICK
        can't score this tick (fewer than 2 live games, no model, the buffer shorter than the feed lag p90)."""
        from redzone.noggin import pick as PK
        games, lags = [], {}
        for gid, x in cands.items():
            tl = self.timelines.get(gid)
            if tl is None or tl.live is None or not x.target:
                continue
            fl = sorted(tl._lags)[len(tl._lags) // 2] if tl._lags else tl.lag_est()
            lags[gid] = fl + self.delays.get(x.target).mu
            games.append(tl.live)
        if len(games) < 2:
            return None
        league = games[0].league
        model = PK.model_for(league.value, name=model_name)
        if model is None or min(lags.values()) < PK.FEED_LAG_P90.get(league, 70.0) + tune:
            return None
        mine = {t for g in games if g.gid in self._yours for t in g.matchup.split(" @ ")}
        cur = self.current if self.current in lags else None
        _, scores = PK.live_choice(games, cur, now, lags, mine, tune, model, model.meta.get("info", {}).get("cue_s"))
        return cur, scores, model

    def _possession_pick(self, d: Decision, x: Candidate | None, now: float) -> str:
        """[noggin possession] The per-possession jump / stay policy for a possession-start entry: Reflex PICK
        trained on the possession-start hindsight oracle (oracle.solve_cue, labels from the oracle only,
        never from Nick). "stay" only when the model scores the game on screen above the rule's target by
        the model's margin. Your team's games (both ways) and anything PICK can't score stay the rule's call
        (jump). noggin.possession.enabled (off) acts; .shadow only logs."""
        c = self.cfg
        on, shadow = c.get("noggin.possession.enabled", False), c.get("noggin.possession.shadow", False)
        if not (on or shadow) or not d.target or d.target in self._yours or self.current in self._yours:
            return "jump"
        if x is None or x.scored.league.base.value not in (c.get("noggin.possession.leagues") or []):
            return "jump"
        got = self._pick_scores(self._known, now, str(c.get("noggin.possession.model", "cue90")),
                                float(c.get("noggin.pick.tune_s", 5.0)))
        if got is None:
            return "jump"
        cur, scores, model = got
        say = "jump"
        if cur is not None and d.target in scores and scores[cur] - scores[d.target] > model.margin:
            say = "stay"
        self.possession_log.append((now, cur, d.target, say))
        return say if on else "jump"

    def _pick(self, d: Decision, cands, now: float) -> Decision:
        """[noggin PICK] Reflex PICK (redzone/noggin/pick.py): the game the hindsight-oracle imitation wants on
        screen. Shadow logs it beside the app's own decision. Enabled, mode "breaks" (the default) only lets
        PICK choose where to go while a break is on screen and the app would stay; "stays" acts whenever the app
        stays; "replace" also overrides the app's own switches (except keep_kinds). Never during a replay, never
        over your team (the app's L1 rules) or a crunch window, always through the app's leave gate (Rule #1),
        and only with the time-shift buffer on: PICK reads the screen's next (lag - feed lag) seconds from the
        feed, as it was trained."""
        shadow = self.cfg.get("noggin.pick.shadow", False)
        if not (self.cfg.get("noggin.pick.enabled", False) or shadow) or self.replaying or self.after_replay \
                or d.action == "replay" or d.kind == "user" or self.user.paused or self.safe_mode:
            return d
        from redzone.noggin import pick as PK
        games, lags = [], {}
        for gid, x in cands.items():
            tl = self.timelines.get(gid)
            if tl is None or tl.live is None or not x.target:
                continue
            fl = sorted(tl._lags)[len(tl._lags) // 2] if tl._lags else tl.lag_est()
            lags[gid] = fl + self.delays.get(x.target).mu
            games.append(tl.live)
        if len(games) < 2:
            return d
        league = games[0].league
        model = PK.model_for(league.value, name=str(self.cfg.get("noggin.pick.model", "v1")))
        tune = float(self.cfg.get("noggin.pick.tune_s", 5.0))
        if model is None or min(lags.values()) < PK.FEED_LAG_P90.get(league, 70.0) + tune:
            return d
        mine = {t for g in games if g.gid in self._yours for t in g.matchup.split(" @ ")}
        cur = self.current if self.current in lags else None
        target, scores = PK.live_choice(games, cur, now, lags, mine, tune, model, model.meta.get("info", {}).get("cue_s"))
        self.pick_log.append((now, cur, target, d.action, d.target))
        if shadow and not self.cfg.get("noggin.pick.enabled", False):
            return d
        if (d.action == "switch" and d.target in self._yours) or (d.action == "stay" and self.current in self._yours):
            return d                     # L1: your team's game is the app's call
        if cur is None:
            return d                     # what's on screen isn't a live game PICK scores (ended, pseudo): the app's call
        mode = self.cfg.get("noggin.pick.mode", "breaks")      # breaks | stays (only when the app stays) | replace
        if d.action != "stay" and mode != "replace":
            return d
        if mode == "breaks":
            g = self._gate(cands[self.current], now)
            if not (g and g.safe and g.on_screen_break):
                return d                 # only a break on screen is PICK's to fill
        if d.action == "switch" and d.kind in (self.cfg.get("noggin.pick.keep_kinds") or PICK_KEEPS):
            return d                     # crunch time, the way home, a playbook visit: the app's hard rules
        if self.current in cands and cands[self.current].scored.crunch_now:
            return d                     # never second-guess staying (or leaving) inside a crunch window
        if target and target != self.current:
            # [switch-rate cap] PICK alone never flips again within min_gap_s of the last switch
            gap = float(self.cfg.get("noggin.pick.min_gap_s", 0.0))
            if self.history and now - self.history[-1][0] < gap and not (d.action == "switch" and d.target == target):
                return d if d.action == "stay" else Decision(now, "stay", self.current, "pick: too soon after a switch",
                                                             d.state, d.gate)
            # Rule #1: the app's own leave gate decides when, for whatever is on screen (also a game PICK
            # doesn't score, e.g. one that just ended); wait for it, wanting PICK's game
            on = cands.get(self.current) if self.current else None
            gate = self._gate(on, now) if on is not None else None
            why = f"pick {scores[target]:.2f} vs {scores.get(cur, 0):.2f}"
            if self.current and not (gate and gate.safe and not on.scored.lock):
                return Decision(now, "stay", self.current, f"{why} — waiting: {gate.why if gate else 'no timeline'}",
                                d.state, gate.why if gate else "", want=target)
            return Decision(now, "switch", target, why, d.state, kind="better")
        if d.action == "switch" and cur is not None:
            return Decision(now, "stay", cur, f"pick stays ({scores.get(cur, 0):.2f})", d.state)
        return d

    def _hold_for_big(self, d: Decision, cands, now: float, home_before) -> Decision:
        """[cut timing] A big play is in the data but hasn't finished airing here yet: stay for it.
        The data runs a stream delay ahead of the screen (more after a rewind on entry), so "the
        moment's over" in the data can still be the touchdown on its way to your screen."""
        if d.action not in ("switch", "replay") or d.kind in ("user", "crunch") or not self.current \
                or self.replaying or self.after_replay or d.target == self.current:
            return d
        if not self._cut_timed(self.current, cands):
            return d
        tl = self.timelines.get(self.current)
        x = cands.get(self.current)
        if tl is None or x is None or not x.target:
            return d
        dl = self.delays.get(x.target)
        until = None
        for e in reversed(tl.events):
            if e.kind == "play" and e.big and now < e.seen + dl.mu + 2 * dl.current_sigma(now) + AFTER_PLAY_BUFFER_S:
                until = e.seen + dl.mu + 2 * dl.current_sigma(now) + AFTER_PLAY_BUFFER_S
                label = e.label
                break
        if until is None:
            return d
        self._home_restore(home_before, "hold_for_big: a stay")
        return Decision(now, "stay", self.current, f"staying for it to air here ({until - now:.0f}s): {label}",
                        d.state, d.gate, want=d.target)

    def _learn_entries(self, cands: dict[str, Candidate]) -> None:
        """Brain v3's entry point (seconds to the restart) for each game's newest event, for the
        expected end of a stoppage the data hasn't closed yet (cuts.DeadTracker.resume_eta)."""
        on = self.cfg.get("brain.v3_entry", [])
        if not on:
            return
        for gid, x in cands.items():
            tl = self.timelines.get(gid)
            v = (getattr(x.scored, "brain", None) or {}).get("v3") if x is not None else None
            if tl is None or not v:
                continue
            if isinstance(on, (list, tuple, set)) and x.scored.league.value not in on \
                    and x.scored.league.base.value not in on:
                continue
            try:
                tl.dead.learned_q = 0 if int(self.cfg.get("brain.v3_entry_q", 50)) <= 10 else 1
                tl.dead.learned_cap = bool(self.cfg.get("brain.v3_entry_cap", True))
                tl.dead.learn(v.get("at"), v.get("entry_q"))
            except Exception:  # noqa: BLE001 - a bad reading only loses the learned estimate
                log.exception("brain v3 entry for %s", gid)

    def _resume_eta(self, gid: str, cands, now: float) -> float:
        """Wall time play should be back on screen in gid if we tuned there now."""
        tl, x = self.timelines.get(gid), cands.get(gid)
        if tl is None or x is None or not tl.dead.marks:
            return now
        d = self.delays.get(x.target or "unknown")
        base = d.mu - d.behind
        return tl.dead.resume_eta(now - base) + base

    def _land_behind(self, gid: str | None, rewind_s: float) -> None:
        """The tune now on screen runs rewind_s behind live; every other tune is fresh again."""
        mine = self.target_of(gid) if gid else None
        held = getattr(self.delays, "held", None) or {}   # [time-shift buffer] streams held behind live
        for t, dl in list(self.delays.book.items()):
            if dl.behind != held.get(t, 0.0) and t != mine:
                self.delays.set_behind(t, held.get(t, 0.0))
        if mine:
            self.delays.set_behind(mine, held.get(mine, 0.0) + rewind_s)

    def _decide(self, cands: dict[str, Candidate], now: float) -> Decision:
        c = self.cfg
        u = self.user
        live = {gid: x for gid, x in cands.items()
                if x.scored.raw > 0 or x.scored.moment > 0 or x.scored.is_break or gid in PSEUDO}
        cur = cands.get(self.current) if self.current else None
        watchable = {gid: x for gid, x in live.items()
                     if x.target and not x.stale and gid not in u.muted}

        if self.safe_mode:
            return Decision(now, "stay", self.current, "safe mode: too many errors — tap Resume", State.SAFE)
        if u.paused:
            return Decision(now, "stay", self.current, "auto-switching paused", State.PAUSED)
        if u.locked_game and u.locked_game in cands:
            if self.current != u.locked_game:
                if self.current is None and now < self.start_retry_at:
                    return Decision(now, "stay", None, f"couldn't get to your locked game yet — trying again in "
                                    f"{math.ceil(self.start_retry_at - now)}s", State.PAUSED, want=u.locked_game)
                if c.get("decider.lock_wait.enabled"):
                    wait = self._lock_wait(u.locked_game, cands[u.locked_game], now)
                    if wait:
                        return wait
                return Decision(now, "switch", u.locked_game, "you locked this game", State.PAUSED, kind="user")
            self._lock_reset()
            return Decision(now, "stay", self.current, "locked here by you", State.PAUSED)
        if now < u.manual_until:
            return Decision(now, "stay", self.current, "you just picked this — hands off for a minute", State.PAUSED)
        if now < self._arrival_until:
            return Decision(now, "stay", self.current, "switch in progress", State.WATCHING)
        self._target_of.update({gid: x.target for gid, x in cands.items() if x.target})
        self._known_target.update(self._target_of)  # [mid-play guard] remembered after a game drops out
        self._mark_screen_breaks(watchable, now)
        if self.replaying:
            return Decision(now, "stay", self.current, f"replay: {self.replaying.label}", State.REPLAY,
                            kind="replay", replay=self.replaying)
        if self.after_replay:
            return self._after_replay(cands, watchable, now)
        if not watchable:
            # Every game is over (or untunable): the perfect time to catch up on what you missed —
            # but "nothing watchable" can also mean the feed for the game ON SCREEN went quiet.
            # It may still be mid-play: same leave rules as always (a drop caused a cut in tests).
            hold = self._gone_hold(cur, now) if self.current else None
            if hold:
                return Decision(now, "stay", self.current, hold, State.WATCHING, hold)
            catch_up = self._catch_up(cands, now, None, "nothing live")
            if catch_up:
                return catch_up
            return Decision(now, "stay", self.current, "nothing live that I know how to tune", State.IDLE)

        # Current game is over, gone, or muted: go to the best thing — but a game that just went
        # final in the DATA is still playing its last seconds on SCREEN (~delay behind). Leaving
        # the moment the feed says "final" cut off game-winners in backtests (30 cuts in 92 NBA
        # games, 2 NFL kneel-downs). Stay until the last play has aired, then move — crunch time
        # first, even mid-break: landing in its stoppage is arriving early, not dead air.
        if cur is None or self.current not in live or self.current in u.muted:
            # [mid-play guard] ...but not while its last play is still on your screen (see _gone_hold).
            hold = self._gone_hold(cur, now)
            if hold:
                return Decision(now, "stay", self.current, hold, State.WATCHING, hold)
            hot = self._crunch_pick(watchable)
            best = hot or self._best(watchable, exclude=None)
            if not hot:
                catch_up = self._catch_up(cands, now, best, "game over")
                if catch_up:
                    return catch_up
            if best is None:
                return Decision(now, "stay", self.current, "nothing live", State.IDLE)
            wait = None if hot else self._wait_for_my_tip(cands[best], now)
            if wait:
                return Decision(now, "stay", self.current, wait, State.IDLE)
            kind = "start" if self.current is None else "ended"
            if kind == "start" and now < self.start_retry_at:
                return Decision(now, "stay", None, f"couldn't start yet — trying again in "
                                f"{math.ceil(self.start_retry_at - now)}s", State.WATCHING, want=best)
            return Decision(now, "switch", best, self._why(cands[best], "crunch time" if hot else "best game on"),
                            State.WATCHING, kind=kind)

        s_cur = cur.scored
        gate = self._gate(cur, now)
        gate_ok = bool(gate and gate.safe)
        gate_txt = gate.why if gate else "no timeline"
        on_break = bool(gate and gate.on_screen_break)
        state = State.BREAK if on_break else State.LOCKED if s_cur.lock else State.WATCHING

        # --- returning home after a break visit (live budget) ---
        home = self.home_break_visit
        focus_on = bool(u.focus_game and u.focus_mode != FocusMode.AUTOPILOT)
        if home and not focus_on and home in watchable and (
                cands[home].scored.score + c.get("decider.switch_margin") < s_cur.score
                or self._crunch_outranks(s_cur, cands[home].scored)):
            home = None   # what we're on now is clearly better: stay
            self._home_set(home, self._home_at, "what we're on is clearly better")
        if home and home != self.current and home in watchable:
            if self._home_due(home, cands[home], now):
                if gate_ok:
                    return Decision(now, "switch", home, "back to your game before the next play",
                                    state, gate_txt, kind="home")
                return Decision(now, "stay", self.current, f"heading back to your game at the next gap ({gate_txt})",
                                state, gate_txt, want=home)

        # --- focus modes ---
        focus = u.focus_game if u.focus_game in cands else None
        if focus and u.focus_mode != FocusMode.AUTOPILOT:
            if self.current != focus:
                if not cands[focus].scored.is_break and gate_ok and not s_cur.lock:
                    return Decision(now, "switch", focus, "back to your focus game", state, gate_txt, kind="home")
                return Decision(now, "stay", self.current, "away from focus game until this moment ends",
                                state, gate_txt)
            if on_break:
                planned = self._long_break_visit(watchable, cands, focus, gate_ok, state, gate_txt, now)
                if planned:
                    return planned
                dest = self._break_destination(watchable, exclude=focus)
                rp = self._replay_option(cands, dest, now)
                if rp:
                    self._home_set(focus, self._home_at, "a visit away from your focus game")
                    return self._replay_decision(rp, state, gate_txt, now)
                if dest:
                    self._home_set(focus, self._home_at, "a visit away from your focus game")
                    return Decision(now, "switch", dest, self._why(cands[dest], f"{_short(s_cur)} on a break → "),
                                    state, gate_txt, kind="break_visit")
                return Decision(now, "stay", self.current, "your game is on a break; nothing better to visit",
                                state, gate_txt)
            if u.focus_mode == FocusMode.FOCUS_MUST_SEE and gate_ok and not s_cur.lock:
                best = self._best(watchable, exclude=focus)
                if best:
                    b = cands[best].scored
                    if b.score >= c.get("decider.must_see_score") and b.score >= s_cur.score + c.get("decider.must_see_lead"):
                        return Decision(now, "switch", best, self._why(cands[best], "must-see"),
                                        state, gate_txt, kind="must_see")
            return Decision(now, "stay", self.current, "focused: " + s_cur.reason, state, gate_txt)

        # --- autopilot ---
        crunch = self._crunch_time(cands, watchable, s_cur, on_break, gate_ok, gate_txt, state, now)
        if crunch:
            return crunch
        chase = self._chase(watchable, s_cur, now) if gate_ok and not s_cur.lock else None
        if chase and self._mine_first(s_cur) and cands[chase[0]].scored.personal < s_cur.personal:
            chase = None   # [EXP-214] a big play elsewhere doesn't pull the TV off your team late and close
        # [chained visit] Already away from home on a break visit: the game we're visiting can hit its
        # own break. Never let that become the new "home" — a WSH halftime visit to MIN@CLE chained
        # into an OKC visit at CLE's halftime and lost the way back to WSH for 2 hours (Oct 26 replay).
        away_home = home if (home and home != self.current and home in watchable) else None
        visit_home = away_home or (self.current if s_cur.score >= c.get("decider.break_destination_min") else None)
        if on_break and away_home and gate_ok and not cands[away_home].scored.is_break \
                and away_home not in self._screen_break:
            return Decision(now, "switch", away_home, "this game's on a break — back to your game",
                            state, gate_txt, kind="home")
        if on_break:
            if chase:
                self._home_set(visit_home, self._home_at, "a break visit")
                return self._chase_decision(chase, cands, state, gate_txt, now)
            planned = None if away_home else self._long_break_visit(watchable, cands, self.current, gate_ok,
                                                                     state, gate_txt, now)
            if planned:
                return planned
            dest = self._break_destination(watchable, exclude=self.current, home=away_home)
            rp = self._replay_option(cands, dest, now)
            if rp:
                self._home_set(visit_home, self._home_at, "a break visit")
                return self._replay_decision(rp, state, gate_txt, now)
            if dest and gate_ok and not self._short_stop(self.current, s_cur):
                self._home_set(visit_home, self._home_at, "a break visit")
                return Decision(now, "switch", dest, self._why(cands[dest], f"{_short(s_cur)} on a break → "),
                                state, gate_txt, kind="break_visit")
        elif self._routine_tail(cur, gate, watchable, now):
            self._home_set(visit_home, self._home_at, "a break visit")
            return Decision(now, "switch", REDZONE, "break's ending on a routine snap → NFL RedZone",
                            state, gate_txt, kind="break_visit")

        # [replay and resume] repay the buffer in this break, else a missed play at this dead ball
        if on_break:
            skip = self._skip_ahead(s_cur, on_break, state, gate_txt, now)
            if skip:
                return skip
        if not chase:
            rr = self._resume_replay(cands, s_cur, gate, state, gate_txt, now)
            if rr:
                return rr

        # Chase: a big play just happened somewhere and hasn't aired yet. Go now, see it live.
        if chase:
            return self._chase_decision(chase, cands, state, gate_txt, now)

        # [drive in 20] A drive inside the 20 elsewhere: that's where the touchdown will be.
        d20 = (self._rz_drive(watchable, now) if gate_ok and not s_cur.lock
               and (self.current != REDZONE or self.cfg.get("redzone_mode.drive_in_20_from_redzone", True)) else None)
        if d20 and not self._mine_late(s_cur):
            return Decision(now, "switch", d20, self._why(cands[d20], "inside the 20 →"), state, gate_txt,
                            kind="drive")

        best = self._best(watchable, exclude=self.current)
        if best is None:
            # [replay.when_alone] Nothing else live to switch to (your team's game on a break, say) is
            # no reason to skip the catch-up check below: the missed play still beats this moment.
            if gate_ok and not s_cur.lock and self.cfg.get("replay.when_alone", False):
                rp = self._best_replay(cands, now, None, False, top=self._vs_replay(self.current, cands),
                                       margin=self.core.k.switch_margin, yours=self.current not in self._yours)
                if rp and rp.mine:   # your team's missed play only: other games' wait for a real break
                    return self._replay_decision(rp, state, gate_txt, now)
            return Decision(now, "stay", self.current, s_cur.reason, state, gate_txt)
        b = cands[best]
        margin = self.core.k.switch_margin
        cost = self._rank_cost(b.target)

        # Catch-up between plays: a missed big play is worth more than this ordinary moment.
        if gate_ok and not s_cur.lock:
            lull = self._rw() and self._lull(watchable)
            rp = self._best_replay(cands, now, None, False,
                                   top=-1.0 if lull else max(self._vs_replay(self.current, cands),
                                                             self._vs_replay(best, cands)),
                                   margin=0.0 if lull else margin,
                                   yours=self.current not in self._yours)
            if rp:
                return self._replay_decision(rp, state, gate_txt, now)

        drive = self._drive_takeover(watchable, gate_ok, state, gate_txt, now)
        if drive:
            return drive

        # Your team late and close: never left for a lesser game (Oct 26 replay: WSH @ CHA, down 8 with
        # 8:23 left, was left for a neutral comeback; we got back with 0:33 left and saw 0 of its 13
        # crunch plays). Only crunch time elsewhere takes the TV, and that's decided before this.
        if self._mine_late(s_cur):
            return Decision(now, "stay", self.current, f"your team, late and close — {s_cur.reason}", state, gate_txt)

        # Go back to where we were if this moment is over and that game is about as good.
        if self.stack and not s_cur.lock and gate_ok and not self._mine_first(s_cur, late_only=True):
            prev = self.stack[-1]
            if prev in watchable and not cands[prev].scored.is_break and prev not in self._screen_break:
                st = self._cstate(cands, now)
                if core.ev(st, "return", prev, self.core.k).gain >= 0 and \
                        core.rate(st.options[prev], self.core.k) >= core.rate(st.options[best], self.core.k) - margin:
                    return Decision(now, "switch", prev, self._why(cands[prev], "moment's over, back to"),
                                    state, gate_txt, kind="return")

        if core.ev(self._cstate(cands, now), "switch", best, self.core.k, cost_s=cost).gain < 0:
            return Decision(now, "stay", self.current, s_cur.reason, state, gate_txt)
        if not gate_ok:
            return Decision(now, "stay", self.current, f"want {b.scored.reason} — waiting: {gate_txt}",
                            state, gate_txt, want=best)
        return Decision(now, "switch", best, self._why(b, "better game"), state, gate_txt, kind="better")

    def _ending_still_airing(self, gid: str, now: float, cap_s: float = 150.0) -> float:
        """Seconds until the last thing we saw in this game has aired on screen (0 = done).
        Capped so a game that silently vanished from the feed can't hold us forever."""
        tl = self.timelines.get(gid)
        if tl is None or not tl.events:
            return 0.0
        last = max(e.seen for e in tl.events)
        d = self.delays.get(self._target_of.get(gid) or "unknown")
        until = last + d.mu + 2 * d.current_sigma(now) + 5.0
        if now - last > cap_s + d.mu:
            return 0.0
        return max(0.0, until - now)

    # ----- chase helpers ------------------------------------------------------

    def _chase(self, watchable: dict[str, Candidate], s_cur: Scored, now: float) -> tuple[str, Event] | None:
        """A big play that's in the data but not on screen yet: (game, play) to go see it live.

        From RedZone any game qualifies: RedZone's score isn't a moment you'd be giving up."""
        floor = -1.0 if self.current in PSEUDO else s_cur.score - self.cfg.get("decider.chase_max_deficit")
        best = None
        for gid, x in watchable.items():
            # Don't skip games "in a break": the play that caused the break hasn't aired yet.
            if gid == self.current or gid not in self.timelines or x.scored.score < floor:
                continue
            ev = catchable(self.timelines[gid], self.delays.get(x.target), now, tune_s=self._deadline_cost(x.target) + 3)
            if ev and (best is None or x.scored.score > best[0]):
                best = (x.scored.score, gid, ev)
        return (best[1], best[2]) if best else None

    def _chase_decision(self, chase: tuple[str, Event], cands, state, gate_txt: str, now: float) -> Decision:
        gid, ev = chase
        return Decision(now, "switch", gid, f"catch it live — {ev.label} · {cands[gid].scored.reason}",
                        state, gate_txt, kind="chase")

    # ----- replay helpers -----------------------------------------------------

    def _replay_option(self, cands, dest: str | None, now: float) -> Missed | None:
        """During a break: is a catch-up replay better than the best live game?"""
        if not self.cfg.get("replay.enabled", True) or not self.current:
            return None
        back_cost = self._deadline_cost(self._target_of.get(self.current)) + 6
        budget = self._break_remaining(self.current, cands, now) - back_cost
        # A break costs nothing to leave: no switch margin, and you can still visit dest afterwards.
        dest_score = self._vs_replay(dest, cands) if dest else -1.0
        return self._best_replay(cands, now, budget, allow_seen=dest is None, top=dest_score, margin=0.0)

    def _catch_up(self, cands, now: float, best: str | None, why: str) -> Decision | None:
        """We're leaving the current game anyway (it ended, or nothing's live): replay first?
        Never instead of a locked moment (crunch time, red zone) we could be watching live."""
        live = cands[best].scored if best else None
        if self.current is None or (live and _holds_replays(live)):
            return None
        rp = self._best_replay(cands, now, None, False, top=self._vs_replay(best, cands) if best else 0.0,
                               montage=best is None)
        if rp is None:
            return None
        if self._final_airing(self.current, now):
            return Decision(now, "stay", self.current, "letting the last play finish, then a catch-up",
                            State.WATCHING, "a play is on screen")
        return self._replay_decision(rp, State.REPLAY, why, now)

    def _final_airing(self, gid: str, now: float) -> bool:
        """The data says it's over, but your screen may still be showing the last play."""
        tl = self.timelines.get(gid)
        x = self._known.get(gid)
        last = tl.last("play") if tl else None
        if last is None or x is None:
            return False
        d = self.delays.get(x.target or "unknown")
        return now <= last.seen + d.mu + 2 * d.current_sigma(now) + AFTER_PLAY_BUFFER_S

    def _vs_replay(self, gid: str, cands) -> float:
        """What a catch-up replay has to beat to replace this. RedZone is only filler next to a
        play you actually missed, so any replay beats it."""
        return 0.0 if gid in PSEUDO else cands[gid].scored.score

    def _replay_decision(self, rp: Missed, state, gate_txt: str, now: float) -> Decision:
        name = "instant replay" if rp.seen_live else "catch-up"
        return Decision(now, "replay", rp.game_id, f"{name}: {rp.label}", State.REPLAY, gate_txt,
                        kind="replay", replay=rp)

    # ----- [replay and resume] a replay costs buffer, not live play (planner, Nov 4) ----------------
    # At the screen game's dead ball: pause it, flip to the catch-up (parked on the hidden stick), flip
    # back and unpause on the same frame. Nothing live is missed and we leave and land on one dead
    # ball (rule #1); the screen runs that much further behind live, repaid by skipping ahead in its
    # next commercial / TV timeout (or by any switch away, which comes back fresh). Hardware gate: a
    # paused YouTube TV stick must resume on the same frame (night-lab step, lane 3). Flag replay.resume.

    def _extra_behind(self, gid: str | None) -> float:
        """How far the tune on screen runs behind its usual place (held buffer), in seconds."""
        t = self.target_of(gid) if gid else None
        if not t:
            return 0.0
        held = getattr(self.delays, "held", None) or {}
        return max(0.0, self.delays.get(t).behind - held.get(t, 0.0))

    def _resume_on(self, x) -> bool:
        if not self.cfg.get("replay.resume", False) or x is None:
            return False
        lgs = self.cfg.get("replay.resume_leagues") or []
        return not lgs or x.scored.league.base.value in lgs

    def _resume_replay(self, cands, s_cur: Scored, gate, state, gate_txt: str, now: float) -> Decision | None:
        """A missed play, aired within replay.resume_window_s, replayed by pausing the game on screen
        at this dead ball. Never past the offset cap; never in crunch time unless the cap's
        replay.resume_crunch_share is free (0 = never)."""
        c = self.cfg
        cur = self._known.get(self.current) if self.current else None
        if self.current is None or self.current in PSEUDO or cur is None or not self._resume_on(cur) \
                or s_cur.lock or self.user.locked_game:
            return None
        if not (gate and gate.safe):
            # A pause needs no trip back in time: the stoppage only has to be on screen now, not for the
            # tune_s a visit takes (replay.resume_pause_s: the pause command's own ack, live lab ~0.3 s)
            tl = self.timelines.get(self.current)
            gate = tl and leave_gate(tl, self.delays.get(cur.target or "unknown"), now,
                                     c.get("decider.uncalibrated_sigma_s"),
                                     tune_s=float(c.get("replay.resume_pause_s", 1.0)))
            if not (gate and gate.safe):
                return None
        w = float(c.get("replay.resume_window_s", 180))
        games = self._replay_pool(cands)
        fresh = [m for m in self.queue if m.game_id != self.current and not m.seen_live and not m.replayed
                 and m.retry_at <= now and self._parkable(m, now)
                 and self._aired_age(m, now) <= w and self._replayable(games.get(m.game_id))]
        if not fresh:
            return None
        # your team's first, then the one closest to the end of its 3 minutes
        rp = max(fresh, key=lambda m: (int(m.mine), self._aired_age(m, now)))
        back = self.entry_cost(cur.target) if self.entry_cost is not None else self._deadline_cost(cur.target)
        cost = self._replay_cost(rp, cands) + self._replay_overrun(rp, cands, now) + back
        cap = float(c.get("replay.resume_cap_s", 60))
        if s_cur.crunch or s_cur.crunch_now:
            cap *= float(c.get("replay.resume_crunch_share", 0.0))
        if self._extra_behind(self.current) + cost > cap:
            return None
        return Decision(now, "replay", rp.game_id, f"catch-up (paused {_short(s_cur)} here): {rp.label}",
                        State.REPLAY, gate_txt, kind="replay_resume", replay=rp)

    def _pause_for(self, d: Decision, now: float) -> None:
        """Committing a replay: remember the game it pauses (replay_resume), else nothing to resume."""
        if d.kind == "replay_resume" and self.current and self.current not in PSEUDO and self.replaying is None:
            self.paused_for = (self.current, now, self._extra_behind(self.current))
            self.resume_log.append((now, "replay", self.current, self.paused_for[2]))
        elif self.replaying is None:
            self.paused_for = None

    def _resume_back(self, cands, now: float) -> Decision | None:
        """The replay is over: back to the paused game on the frame we left (rewind = time away)."""
        if self.paused_for is None:
            return None
        gid, at, extra = self.paused_for
        self.paused_for = None
        x = cands.get(gid) or self._known.get(gid)
        if x is None or not x.target:
            return None
        cost = self.entry_cost(x.target) if self.entry_cost is not None else self._deadline_cost(x.target)
        self._home_set(None, self._home_at, "back where the replay paused")
        return Decision(now, "switch", gid, "back where we paused it for the replay", State.WATCHING,
                        "replay finished", kind="resume", rewind_s=extra + (now - at) + cost)

    def _skip_ahead(self, s_cur: Scored, on_break: bool, state, gate_txt: str, now: float) -> Decision | None:
        """Repay: the screen game is in a commercial / TV timeout and runs behind from earlier
        replays: skip ahead inside the break (never past its end, so the landing stays dead)."""
        extra = self._extra_behind(self.current)
        cur = self._known.get(self.current) if self.current else None
        if extra < 1.0 or not on_break or cur is None or not self._resume_on(cur):
            return None
        d = self.delays.get(cur.target or "unknown")
        cost = self.entry_cost(cur.target) if self.entry_cost is not None else self._deadline_cost(cur.target)
        room = self._break_remaining(self.current, self._known, now) - cost - 2 * d.current_sigma(now) - ENTRY_SLACK_S
        jump = min(extra, room)
        if jump < float(self.cfg.get("replay.resume_min_skip_s", 10)):
            return None
        return Decision(now, "switch", self.current, f"skipping {jump:.0f} s of the break to catch up",
                        state, gate_txt, kind="skip", rewind_s=extra - jump)

    def _after_replay(self, cands, watchable, now: float) -> Decision:
        """A replay just finished; we're on old footage, so go somewhere live (or replay more)."""
        back = self._resume_back(cands, now)
        if back is not None:
            return back
        home = self.home_break_visit
        hot = self._crunch_pick(watchable)
        if hot and (home not in watchable or not self._crunch_outranks(cands[home].scored, cands[hot].scored)):
            home = hot   # leaving a replay needs no gap: get to crunch time
            self._home_set(home, self._home_at, "after a replay: crunch time first")
        d20 = self._rz_drive(watchable, now) if self.cfg.get("redzone_mode.drive_in_20_after_replay", False) else None
        if d20 and not (home and home in watchable and cands[home].scored.crunch):
            return Decision(now, "switch", d20, self._why(cands[d20], "inside the 20 →"), State.WATCHING,
                            "replay finished", kind="live")
        if home and home in watchable and cands[home].scored.crunch:
            # Crunch time at home: another replay only if it surely ends before the next snap, never a visit.
            rp = self._crunch_replay(cands, now, home)
            if rp:
                return self._replay_decision(rp, State.REPLAY, "replay finished", now)
            return Decision(now, "switch", home, self._why(cands[home], "crunch time"), State.WATCHING,
                            "replay finished", kind="home")
        if home and home in watchable:
            remaining = self._break_remaining(home, cands, now) - self._deadline_cost(self._target_of.get(home)) - 6
            rp = self._best_replay(cands, now, remaining, allow_seen=False)
            if rp and remaining > 0:
                return self._replay_decision(rp, State.REPLAY, "replay finished", now)
            # Long break left, or only a routine snap to come back for: don't land in the ad.
            if remaining > 25 or not self._home_due(home, cands[home], now):
                dest = self._break_destination(watchable, exclude=home)
                if dest:
                    return Decision(now, "switch", dest, self._why(cands[dest], "your game is still on a break → "),
                                    State.BREAK, "replay finished", kind="break_visit")
            return Decision(now, "switch", home, "back to your game, live", State.WATCHING,
                            "replay finished", kind="home")
        best = self._best(watchable, exclude=None)
        if best is None:
            rp = self._best_replay(cands, now, None, allow_seen=True, montage=not watchable)
            if rp:
                return self._replay_decision(rp, State.REPLAY, "everything's on a break", now)
            # [cut timing] Everything's on a break: land where play resumes soonest, not in the
            # longest commercial (an intermission after a replay sat 18 minutes in the ad).
            timed = self.cfg.get("decider.entry.leagues") or ["nfl"]
            best = min(watchable, key=lambda g: (self._resume_eta(g, watchable, now)
                                                 if watchable[g].scored.league.base.value in timed else 1e9,
                                                 -watchable[g].scored.score), default=None)
            if best is None:
                return Decision(now, "stay", self.current, "nothing live", State.IDLE)
        live = cands[best].scored
        rp = None if _holds_replays(live) else self._best_replay(cands, now, None, False, top=self._vs_replay(best, cands))
        if rp:
            return self._replay_decision(rp, State.REPLAY, "replay finished", now)
        # Heading back to live anyway: land where a big play is about to air, if there is one.
        hit = self._chase_any(watchable, now, None, live.score - self.cfg.get("decider.chase_max_deficit"))
        if hit:
            gid, ev = hit
            return Decision(now, "switch", gid, f"catch it live — {ev.label} · {cands[gid].scored.reason}",
                            State.WATCHING, "replay finished", kind="live")
        return Decision(now, "switch", best, self._why(cands[best], "back to live"), State.WATCHING,
                        "replay finished", kind="live")

    # ----- [mid-play guard] leaving a game that ended or dropped out -------------

    def _gone_hold(self, cur: Candidate | None, now: float) -> str | None:
        """Why we can't leave the current game yet though it's over / gone / scoring zero (None = go).

        A final in the DATA arrives ~35 s before your screen shows the last play (often the
        game-winner): stay until that has aired. A game that merely vanished from the feed may
        still be live: leave only at a gap the gate can prove, or after GONE_MAX_HOLD_S of flying
        blind. Muting it yourself is an instruction, not a guess: go now."""
        gid = self.current
        if gid is None or gid in self.user.muted:
            return None
        tl = self.timelines.get(gid)
        if tl is None:
            return None
        d = self.delays.get((cur.target if cur else None) or self._known_target.get(gid) or "unknown")
        if tl.final_at is not None:
            until = tl.end_t + d.mu + 2 * d.current_sigma(now) + AFTER_PLAY_BUFFER_S
            if now < until:
                return f"game over — staying for the last play to air ({until - now:.0f}s)"
            return None
        gate = leave_gate(tl, d, now, self.cfg.get("decider.uncalibrated_sigma_s"))
        if gate.safe:
            return None
        if cur is None:  # vanished without a final
            if self._gone is None or self._gone[0] != (gid, self.since):
                self._gone = ((gid, self.since), now)
            cap = GONE_MAX_HOLD_CONTINUOUS_S if tl.league in CONTINUOUS else GONE_MAX_HOLD_S
            if now - self._gone[1] >= cap:
                return None
            return f"lost this game's data — leaving at the next safe gap ({gate.why})"
        return f"leaving at the next gap ({gate.why})"

    # ----- crunch time ----------------------------------------------------------

    def _crunch_rank(self, s: Scored) -> tuple:
        """When crunch times overlap: your teams and bets first, then the league order
        (core.league_priority: NFL > CFB > NBA > ...), then the closest score, then the moment.
        With decider.crunch.by_value: the value with those as priors (_crunch_value)."""
        if self.cfg.get("decider.crunch.by_value", False):
            return (self._crunch_value(s),)
        return (round(s.personal), self._value_league_rank(s.league.value), s.closeness, s.score)

    def _crunch_value(self, s: Scored) -> float:
        """[crunch by value, TREE-6 Mac half; Nick Nov 4 4:15 PM] Crunch vs crunch is decided by value: watch
        value once it ships (else today's score), with the league order (NFL = CFB > NBA > NHL, MLB) and your
        teams as strong priors, not walls: decider.crunch.value_league_prior points per league step,
        value_mine_prior for your team / bet. The game left behind stays catchable (its missed plays are
        catch-up candidates; lane 3's hidden-stick hold makes that a buffer catch-up)."""
        c = self.cfg
        hold = c.get("decider.crunch.hold_personal", 5)
        mine = float(c.get("decider.crunch.value_mine_prior")) if s.personal >= hold else 0.0
        wv = getattr(s, "watch_value", None)
        base = float(s.score) if wv is None else float(wv)   # watch value once lane 1 fills it, else the score
        return base + mine + float(c.get("decider.crunch.value_league_prior")) * self._value_league_rank(s.league.value)

    def _value_league_rank(self, league: str) -> int:
        """core.league_rank with NFL = CFB: only used between two crunch games (N3, Nick Nov 8: NFL = CFB only when both
        are in crunch; NFL first otherwise, Nick Nov 4 3:15 PM)."""
        lg = league.lower()
        if lg in ("nfl", "cfb"):
            return max(core.league_rank("nfl", self.core.k), core.league_rank("cfb", self.core.k))
        return core.league_rank(lg, self.core.k)

    def _crunch_pick(self, watchable: dict[str, Candidate], exclude: str | None = None) -> str | None:
        """The crunch game to be on. Breaks included: a stoppage is the time to get there."""
        en = self.cfg.get("playbooks.game.empty_net", False)   # [GAME-09] a pulled goalie counts as crunch
        hot = [(self._crunch_rank(x.scored), gid) for gid, x in watchable.items()
               if gid != exclude and (x.scored.crunch or (en and x.scored.lock == "empty net"))]
        return max(hot)[1] if hot else None

    def _crunch_outranks(self, a: Scored, b: Scored) -> bool:
        """Is crunch time on a worth more than b (crunch or not)?"""
        return a.crunch and (not b.crunch or self._crunch_rank(a) > self._crunch_rank(b))

    def _crunch_go(self, cands, pick: str, s_cur: Scored, on_break: bool, now: float) -> bool:
        """Should we leave what's on for crunch time at `pick` (at the next safe gap)?"""
        p = cands[pick].scored
        if not s_cur.crunch:
            if self._mine_first(s_cur) and p.personal < s_cur.personal:
                # [EXP-214, your-team crunch first] your team late and close is never left for someone else's
                # crunch: it may be about to become its own (CLE @ WSH, Dec 12: left for CHI 0:39, missed 19)
                return False
            return True   # get there before its first crunch snap
        if self.cfg.get("decider.crunch.by_value", False):
            # [crunch by value] jump when the other crunch is worth clearly more (the Bills' last 3 min vs a
            # Bears 1-minute drive); otherwise only the stoppage hop below
            if self._crunch_value(p) >= self._crunch_value(s_cur) + float(self.cfg.get("decider.crunch.value_margin")):
                self.overlap_why["value"] += 1
                return True
            return on_break and not p.is_break and pick not in self._screen_break and \
                self._break_remaining(self.current, cands, now) >= self.cfg.get("decider.crunch.hop_min_break_s", 20)
        dp = p.personal - s_cur.personal
        if abs(dp) >= self.cfg.get("decider.crunch.personal_lead", 5):
            return dp > 0   # your team (then your bet) wins an overlap outright
        lp, lc = (self._value_league_rank(x.league.value) for x in (p, s_cur))   # [N3] NFL = CFB when both are in crunch
        if lp != lc:
            return lp > lc  # [core] the league order decides between crunch times; the other gets a replay after
        if self.cfg.get("playbooks.game.crunch_overlap", False):
            # [GAME-05] Two crunches, same tier: the higher stakes one stays live (sudden death, then
            # inside the 2:00 window, then the closer score); the other is left for the time-shifted
            # stick and the catch-up after.
            hi, lo = self._overlap_stakes(p), self._overlap_stakes(s_cur)
            if hi != lo:
                self.overlap_why["higher" if hi > lo else "stay"] += 1
                return hi > lo
            self.overlap_why["equal"] += 1   # equal stakes: the hop at a stoppage below decides
        # Two crunches, same tier: hop only while this one is stopped and that one is live, so its
        # next snap airs first. Never for the last few seconds of a stoppage.
        return on_break and not p.is_break and pick not in self._screen_break and \
            self._break_remaining(self.current, cands, now) >= self.cfg.get("decider.crunch.hop_min_break_s", 20)

    @staticmethod
    def _overlap_stakes(s: Scored) -> tuple:
        """[GAME-05] Stakes between two same-tier crunches: sudden death, then the graded 2:00
        window, then how close the score is (to a tenth, so noise doesn't flip it)."""
        return (s.lock == "overtime", s.crunch_now, round(s.closeness, 1))

    def _crunch_holds(self, s_cur: Scored) -> bool:
        """Stay through this crunch game's stoppages? Always for one you have a stake in (your
        team, your bet). For a neutral one only through the stoppages listed: by default the
        two-minute warning, the way in; every stoppage held is commercials on screen."""
        if s_cur.personal >= self.cfg.get("decider.crunch.hold_personal", 5):
            return True
        if _nhl_ot(s_cur):
            return True   # [nhl pp] sudden death: the next goal ends it, whenever it comes
        tl = self.timelines.get(self.current)
        start = tl.last("stop_start") if tl else None
        return start is not None and start.label in self.cfg.get("decider.crunch.hold_neutral_breaks", [])

    def _crunch_time(self, cands, watchable, s_cur: Scored, on_break: bool, gate_ok: bool, gate_txt: str,
                     state: State, now: float) -> Decision | None:
        """Final 2:00 of a one-score game, or OT: get there before the first crunch snap, stay
        through its stoppages, and hop between overlapping crunches only at safe gaps.
        None = crunch time has nothing to say; the usual rules apply."""
        pick = self._crunch_pick(watchable, exclude=self.current)
        if pick and self._crunch_go(cands, pick, s_cur, on_break, now):
            if gate_ok:
                # Its stoppage has room: fill it with a replay on the way (no gap needed to leave one).
                rp = self._crunch_replay(cands, now, pick)
                self._home_set(pick if rp else None, self._home_at, "crunch_time")
                if rp:
                    return self._replay_decision(rp, state, gate_txt, now)
                return Decision(now, "switch", pick, self._why(cands[pick], "crunch time"), state, gate_txt,
                                kind="crunch")
            if not s_cur.crunch:
                return Decision(now, "stay", self.current,
                                f"want crunch time: {cands[pick].scored.reason} — waiting: {gate_txt}", state, gate_txt,
                                want=pick)
        if not s_cur.crunch or not self._crunch_holds(s_cur):
            return None
        # Hold. A visit elsewhere can strand you there (late in a slate the only safe gaps are
        # official breaks) while this game snaps; only a replay that surely ends first is worth it.
        if on_break and gate_ok and not _nhl_ot(s_cur):
            rp = self._crunch_replay(cands, now, self.current)
            if rp:
                self._home_set(self.current, self._home_at, "crunch_time")
                return self._replay_decision(rp, state, gate_txt, now)
        return Decision(now, "stay", self.current, f"crunch time: {s_cur.reason}", state, gate_txt)

    def _crunch_replay(self, cands, now: float, gid: str) -> Missed | None:
        """A replay from another game that surely ends before crunch game gid's next snap airs
        (its own broadcast is already replaying its last snap). Long stoppages only, in practice:
        the two-minute warning, a score, a review, the end of regulation."""
        if not self.cfg.get("decider.crunch.replays", True) or not self.cfg.get("replay.enabled", True):
            return None
        # Back on screen before the snap, even if this poll is the last one before it.
        back = self._deadline_cost(self._target_of.get(gid)) + 6 + self.cfg.get("polling.live_s", 10)
        # decider.crunch.instant_replays=false: a crunch stoppage only takes a play you MISSED, never an
        # instant replay of one you saw (the snap it can overrun is worth more than a second look)
        return self._best_replay(cands, now, self._next_snap_s(gid, cands, now) - back,
                                 allow_seen=bool(self._lc("decider.crunch.instant_replays", gid, True)), exclude=gid)

    def _next_snap_s(self, gid: str, cands, now: float) -> float:
        """Seconds until gid's next scrimmage snap could start airing on your screen (early side
        of the delay's error bars). A play already in the data is exact; otherwise the next one
        can't happen before its stoppage's shortest usual length is up."""
        tl, x = self.timelines.get(gid), cands.get(gid)
        if tl is None or x is None:
            return 0.0
        d = self.delays.get(x.target or "unknown")
        to_snap_air = d.mu - 2 * d.current_sigma(now) - PLAY_LEN_S.get(tl.league, 0.0)  # play logged -> snap airs
        nxt = None
        for e in reversed(tl.events):
            if e.kind != "play":
                continue
            if e.seen + to_snap_air <= now:
                continue   # arrival order can differ from event order
            # A kickoff isn't a scrimmage snap; the first one after it is at least a return away.
            earliest = e.lo if e.play_kind != "kickoff" else e.lo + KICKOFF_TO_SNAP_S
            nxt = earliest if nxt is None else min(nxt, earliest)
        if nxt is None:
            nxt = now - UNSEEN_PLAY_LAG_S   # a newer play could already have happened, unseen
            start, end = tl.last("stop_start"), tl.last("stop_end")
            if start is not None and (end is None or end.seen < start.lo):
                sure = self.cfg.get(f"decider.crunch.shortest_break_s.{start.label}", 0)
                nxt = max(nxt, start.lo + sure)
        return nxt + to_snap_air - now

    # ----- [jump mode] --------------------------------------------------------
    # Nick, Nov 7 12:52: "should be 4-5 switches every minute". The normal decider stays on the best game and
    # leaves at safe breaks; jump mode is a rotation over every live tunable game instead. At each dead ball on
    # the game on screen (the delay book, YouTube TV's 34.9 s edge prior when that flag is on, says when it is
    # on screen) it flips to the game that's about to snap or just had a big moment, and no game keeps the
    # screen for more than jump_mode.max_plays plays unless it's crunch time or a scoring drive inside the 20.
    # Rule #1 is untouched: it leaves only at a strict dead ball (no low-leverage mid-play leave) and only
    # picks a game whose picture comes up on a dead ball (jump_lands_dead, checked again at the flip).

    def jump_on(self) -> bool:
        return bool(self.cfg.get("jump_mode.enabled", False))

    def _jump_league(self, x: Candidate | None) -> bool:
        lgs = list(self.cfg.get("jump_mode.leagues") or [])
        if lgs and self.buffered_on():   # [overseer Nov 8 22:15] buffered runs in its own leagues too (NBA, CBB)
            lgs += list(bj.cfg_get(self.cfg, "leagues") or [])
        return x is not None and (not lgs or x.scored.league.value in lgs)

    def _jump_pool(self, cands: dict[str, Candidate]) -> dict[str, Candidate]:
        """Every live tunable game the rotation may visit (its leagues, a timeline, not muted or stale)."""
        u = self.user
        return {gid: x for gid, x in cands.items()
                if gid not in PSEUDO and x.target and not x.stale and gid not in u.muted and gid in self.timelines
                and (x.scored.raw > 0 or x.scored.moment > 0 or x.scored.is_break) and self._jump_league(x)}

    def _jump_hold(self, x: Candidate) -> str | None:
        """Why the game on screen keeps the screen past its play budget (None: it doesn't)."""
        s = x.scored
        hold_personal = self.cfg.get("decider.crunch.hold_personal", 5)
        if (s.crunch or s.crunch_now or s.lock in CRUNCH_LOCKS) and (
                not s.is_break or s.personal >= hold_personal or _nhl_ot(s)):
            return "crunch"   # [D-03] a neutral crunch game keeps the screen only while it's live: every break held is ads
        if self._mine_late(s) and not s.is_break:
            return "mine_late"   # [D-04] your team late and close, not at a break (the ads go to the rotation)
        if self.cfg.get("jump_mode.hold_rz_drive", True) and s.lock == "red zone drive" and not s.garbage:
            return "rz_drive"
        return None

    def _jump_prior(self, s: Scored) -> float:
        """[D-04] Your team and the league order in the rotation's value: without these the snap, big-play and fairness
        bonuses (up to 65 points) outrank both. jump_mode.mine_prior / league_prior (points per league step)."""
        c = self.cfg
        mine = float(c.get("jump_mode.mine_prior", 30)) if s.personal >= c.get("decider.crunch.hold_personal", 5) else 0.0
        return mine + float(c.get("jump_mode.league_prior", 4)) * core.league_rank(s.league.value, self.core.k)

    def _jump_margin(self, d, now: float) -> float:
        """Delay doubt each side of a jump's leave / landing: jump_mode.margin_sigmas x the delay's sigma
        (2 = the rule every other entry uses)."""
        return float(self.cfg.get("jump_mode.margin_sigmas", 2.0)) * d.current_sigma(now)

    def _jump_gate(self, cur: Candidate, now: float, gid: str | None = None, required_s: float | None = None,
                   strict: bool | None = None) -> Gate | None:
        """[Nick Nov 7 1:05, "Rule #1 kept for JOINS"] May the rotation leave gid (default: the game on screen)?
        The normal gate (official breaks, low-leverage leaves per decider.leave.low_leverage) or, in any jump
        league, the play-by-play's dead window between plays: the feed lags the TV and goes quiet in the ads,
        so a gate that waits for the data to 'cover' the screen almost never opens on the game on screen."""
        gid = gid or self.current
        tl = self.timelines.get(gid or "")
        if tl is None:
            return None
        d = self.delays.get(cur.target or "unknown")
        flip = float(self.cfg.get("jump_mode.flip_s", 3.2)) if required_s is None else required_s
        if self._jump_break_on_screen(gid, cur, tl, d, now):
            return Gate(True, "break on screen (jump: nothing to cut)", True)
        if tl.dead.marks:
            m = self._jump_margin(d, now)
            until = tl.dead.dead_until(now - d.mu - m)
            if until is not None and until >= now + flip - d.mu + m:
                return Gate(True, "dead ball on screen (play-by-play)")
        # (this used to swap self.current for the call: another game's gate is asked by name now)
        g = self._gate(cur, now) if gid == self.current else self._gate_at(cur, now, gid)
        if g is not None and g.safe and (self.buffered_on() if strict is None else strict) \
                and g.why.startswith("low-leverage"):
            # [Nick 22:15 Nov 7 pass bar: "Can't leave mid play"] buffered mode leaves only at a dead ball, never on
            # the low-leverage exception
            return Gate(False, "dead balls only (buffered: no mid-play leave)")
        return g

    def _jump_break_on_screen(self, gid: str, cur: Candidate, tl: Timeline, d, now: float) -> bool:
        """[overseer Nov 8 01:16Z: a timeout on MIA@ND held the screen 46 s with the standby ready] A break
        (timeout, end of quarter, injury, review, score) that the FRESH feed is still in, and that has reached
        the screen, is a dead ball for a jump: the feed runs ~mu ahead of the screen, so a break that hasn't
        ended in the data can't have ended on screen, and no visit-and-return length is needed (the rotation
        doesn't come back). The break counts as on screen once the play before it surely finished airing."""
        m = self.jump_break_m[gid]
        if not cur.scored.is_break:
            if m.to("play", "the feed is back in play", now=now):
                self.jump_break_m.prune()
            return False
        m.to("break", "the feed says break", now=now)
        first = m.since
        if tl.fresh_at is None or now - tl.fresh_at > STALE_DATA_S:
            return False
        m = 2 * d.current_sigma(now)

        def resumed(since: float, pid=None) -> bool:   # a play logged after the break began: it's over in the data
            return any(e.kind == "play" and e.seen > since and not (pid and e.pid == pid) for e in tl.events)
        start = tl.last("stop_start")
        if start is not None and not resumed(start.lo, start.pid):
            reached = _stoppage_reached(tl, start, d.mu, m)
        elif not resumed(first):   # no stoppage entry for it: from when the feed first said "break" (it began no later)
            reached = first + d.mu + m + AFTER_PLAY_BUFFER_S
        else:
            return False
        return now >= reached

    def jump_may_leave(self, gid: str | None, cands: dict[str, Candidate], now: float, required_s: float) -> bool:
        """At the flip: the jump may cut away from gid now (the engine's may_cut for a jump)."""
        x = cands.get(gid or "") or self._known.get(gid or "")
        if not gid or gid in PSEUDO or x is None:
            return True
        if gid not in cands and self.buffered_on():
            # [buffered: the screen's game left the feed] no fresh play-by-play left to show a play: a free leave
            # [D-05] the buffered screen runs mu behind the data: a feed dark past STALE_DATA_S still has mu + 2 sigma of
            # play left on the picture
            tl = self.timelines.get(gid)
            d = self.delays.get(x.target or "unknown")
            if tl is None or tl.fresh_at is None or now - tl.fresh_at > STALE_DATA_S + d.mu + 2 * d.current_sigma(now):
                return True
        g = self._jump_gate(x, now, gid=gid, required_s=required_s)
        return bool(g and g.safe)

    def _jump_plays_seen(self, gid: str, x: Candidate, now: float) -> int:
        """Plays that started and finished airing on screen since we landed on gid."""
        tl = self.timelines.get(gid)
        if tl is None:
            return 0
        d = self.delays.get(x.target or "unknown")
        plen = PLAY_LEN_S.get(tl.league, 0.0)
        return sum(1 for e in tl.events if e.kind == "play" and e.play_kind != "kickoff"
                   and e.lo + d.mu - plen >= self.since and e.seen + d.mu <= now)

    def jump_lands_dead(self, gid: str, x: Candidate | None, now: float, cost: float | None = None) -> bool:
        """Rule #1 for a jump: does gid's picture come up on a dead ball (not mid-play, not in an ad)? The
        whole arrival, from the fastest flip to the target's cost plus jump_mode.land_dead_s, give or take the
        delay doubt, must sit inside a play-by-play dead window (an open one only up to its shortest usual
        length, cuts.FLOOR_S). No play-by-play dead windows: the target must be between plays on screen for
        the whole switch (screen.leave_window). Never a rewind (jump_mode.allow_rewind is for later)."""
        tl = self.timelines.get(gid)
        if tl is None or x is None or not x.target:
            return False
        d = self.delays.get(x.target)
        if cost is None:
            cost = self.entry_cost(x.target) if self.entry_cost is not None else self._deadline_cost(x.target)
        if not math.isfinite(cost):
            return False
        if break_on_arrival(tl, d, now, now + cost):
            return False   # a commercial on arrival: not where a rotation lands
        pic = self._bj_pic(gid, x, now)
        at = self.snap_parked(x.target) if pic is not None and self.snap_parked is not None else None
        if at is not None:
            pic = bj.frozen(pic, now, at)   # paused on the line-up: the picture stood still since `at`
        if pic is not None:
            # [buffered jump] the standby plays behind live (scrubbed, label read): its picture is d.mu behind
            ok, _ = bj.lands(pic, now, float(bj.cfg_get(self.cfg, "flip_fast_s")),
                             max(float(bj.cfg_get(self.cfg, "flip_s")), cost),
                             float(bj.cfg_get(self.cfg, "lead_min_s")), float(bj.cfg_get(self.cfg, "margin_sigmas")))
            return ok
        if tl.dead.marks:
            mu = d.mu - d.behind   # a fresh tune or a live standby shows the live edge
            m = self._jump_margin(d, now)
            lo = min(cost, float(self.cfg.get("decider.entry.fastest_s", ENTRY_FASTEST_S)))
            x0 = now + lo - mu - m
            x1 = now + cost + float(self.cfg.get("jump_mode.land_dead_s", 2.0)) - mu + m
            join = self._join_args(x, urgent=False)
            land = tl.dead.landing(x0, x1, walk=join.get("walk"))
            if land.state != "dead":
                return False
            if land.kind != "walk" and join.get("after_play_s"):
                # [landing bars] football: hold for the replay while this same dead window still has room after it
                return cuts.settle_wait(tl.dead, now + cost - mu, m, join["after_play_s"], land) <= 0
            return True
        g = leave_window(tl, d, now, self.cfg.get("decider.uncalibrated_sigma_s"),
                         cost + AFTER_PLAY_BUFFER_S)
        return g.safe and not g.on_screen_break

    def _jump_value(self, gid: str, x: Candidate, cands, now: float) -> tuple[float, dict] | None:
        """Where the rotation goes next: priority (x.scored.score: your teams, close/late, red zone, big plays)
        plus a bonus for a snap about to air on arrival, a big moment just now, and time since we last saw it.
        None: its picture wouldn't come up on a dead ball right now."""
        c = self.cfg
        cost = self.entry_cost(x.target) if self.entry_cost is not None else self._deadline_cost(x.target)
        if gid in self._screen_break or not self.jump_lands_dead(gid, x, now, cost):
            return None
        tl = self.timelines[gid]
        d = self.delays.get(x.target)
        snap_in = self._jump_snap_wait(gid, x, cands, now, cost)   # seconds from landing to its next snap on screen
        snap = 0.0 <= snap_in <= float(c.get("jump_mode.snap_window_s", 25))
        recent = float(c.get("jump_mode.big_recent_s", 60))
        big = any(e.kind == "play" and e.big and e.seen + d.mu >= now - recent for e in tl.events)
        away = now - self.jump_left_at.get(gid, now - float(c.get("jump_mode.rotate_cap_s", 300)))
        away = min(away, float(c.get("jump_mode.rotate_cap_s", 300)))
        v = (x.scored.score + self._jump_prior(x.scored) + float(c.get("jump_mode.snap_bonus", 15)) * snap
             + float(c.get("jump_mode.big_bonus", 20)) * big
             + float(c.get("jump_mode.rotate_per_min", 6)) * away / 60.0)
        return v, {"snap": snap, "snap_in_s": round(snap_in, 1), "big": big, "away_s": round(away),
                   "cost_s": round(cost, 1), "score": round(x.scored.score, 1), "value": round(v, 1)}

    def _jump_snap_wait(self, gid: str, x: Candidate, cands, now: float, after_s: float = 0.0) -> float:
        """Seconds from now + after_s until gid's next snap shows on screen: the play-by-play's expected
        restart (the logged next snap, else the dead window's usual length) when it has dead windows, else the
        early-side estimate (_next_snap_s)."""
        tl = self.timelines.get(gid)
        if tl is not None and tl.dead.marks:
            d = self.delays.get(x.target or "unknown")
            at = now + after_s - (d.mu - (d.behind if gid != self.current else 0.0))
            return tl.dead.resume_eta(at) - at
        return self._next_snap_s(gid, cands, now) - after_s

    def _jump_best(self, cands, now: float, exclude: str | None) -> tuple[str, dict] | None:
        rows, unready, ready = [], 0, 0
        ready_only = bool(self.cfg.get("jump_mode.ready_only", True)) and self.standby_ready is not None
        for gid, x in self._jump_pool(cands).items():
            if gid == exclude:
                continue
            if ready_only and not self.standby_ready(x.target):
                # [overseer Nov 7 18:55] the first live jump tuned a search local on the spot (30 s) and lost the
                # dead ball: jump only onto the hidden stick's ready game; the standby re-tunes for the next one
                unready += 1
                continue
            ready += 1
            r = self._jump_value(gid, x, cands, now)
            if r is not None:
                rows.append((r[0], gid, r[1]))
        if not rows:
            # [overseer Nov 7 20:05] one code per tick: nothing ready on the standby / the ready game is mid-play
            # or in an ad on arrival / no other game at all (they were counted together before)
            self.jump_holds["standby_not_ready" if unready and not ready else "ready_not_dead"
                            if ready_only and ready else "no_game_at_dead_ball"] += 1
            return None
        _, gid, info = max(rows, key=lambda z: z[0])
        return gid, info

    def jump_next(self, cands: dict[str, Candidate], standby: str | None = None,
                  tunable: Callable[[str], bool] | None = None) -> str | None:
        """The game the hidden stick should tune next: the rotation's best other game by priority and time away
        (whether it's at a dead ball right now doesn't matter: by the time it's tuned, it will have moved on).
        Keeps the standby's game unless another beats it by jump_mode.standby_margin (no re-tune churn)."""
        now = self._clock()
        c = self.cfg
        # [overseer Nov 7 18:55] a target the hidden stick can't tune right now (a failed tune's backoff, an app it
        # lacks) is skipped: picking it every tick left the standby empty all afternoon
        pool = {gid: x for gid, x in self._jump_pool(cands).items()
                if gid != self.current and (tunable is None or x.target == standby or tunable(x.target))}
        if not pool:
            return None
        cap = float(c.get("jump_mode.rotate_cap_s", 300))
        per = float(c.get("jump_mode.rotate_per_min", 6))
        slow = float(c.get("jump_mode.search_penalty", 20))

        def val(gid: str) -> float:
            away = min(cap, now - self.jump_left_at.get(gid, now - cap))
            # a code channel tunes in ~3-4 s, a local by search ~11-15 s: the local only for a much bigger moment
            pen = slow if self.slow_tune is not None and self.slow_tune(pool[gid].target) else 0.0
            return pool[gid].scored.score + self._jump_prior(pool[gid].scored) + per * away / 60.0 - pen \
                - (100.0 if gid in self._screen_break else 0.0)
        best = max(pool, key=val)
        keep = next((gid for gid, x in pool.items() if standby and x.target == standby), None)
        if keep is not None and self.buffered_on():
            # [buffered jump] a scrubbed standby is ~60 s of tune + scrub: keep it (ping-pong with the screen) until
            # its game goes to a long stoppage or the pair has been on rotate_every_s
            pair = frozenset((self.current or "", keep))
            if self._bj_pair is None or self._bj_pair[0] != pair:
                self._bj_pair = (pair, now)
            pic = self._bj_pic(keep, pool[keep], now)
            if pic is None or bj.rotate_due(pic, now, now - self._bj_pair[1], self.cfg) is None:
                return keep
        if keep is not None and val(keep) >= val(best) - float(c.get("jump_mode.standby_margin", 8)):
            return keep
        return best

    # ----- [buffered jump] ----------------------------------------------------
    # Both sticks ~60-90 s behind live (scrubbed, label read): the play-by-play already holds every snap and whistle
    # about to air, so the cut points are known in advance (redzone/decider/buffered.py). Only when BOTH pictures
    # are buffered; otherwise jump mode's own rules run (a fresh live-edge tune until its scrub lands).

    def buffered_on(self) -> bool:
        return self.jump_on() and bool(self.cfg.get("buffered_jump.enabled", False))

    def _bj_pic(self, gid: str, x: Candidate | None, now: float) -> bj.Pic | None:
        """gid's picture as the editor sees it, or None: flag off, not a buffered league, no play-by-play dead windows,
        or its stick isn't at least min_behind_s behind live (by the label)."""
        if x is None or not x.target or not self.buffered_on():
            return None
        lgs = bj.cfg_get(self.cfg, "leagues") or []
        tl = self.timelines.get(gid)
        if tl is None or not tl.dead.marks or (lgs and x.scored.league.value not in lgs):
            return None
        d = self.delays.get(x.target)
        if d.behind < float(bj.cfg_get(self.cfg, "min_behind_s")):
            return None
        return bj.Pic(tl.dead, d.mu, d.current_sigma(now), gid, cuts.walk_params(self.cfg, x.scored.league))

    def _bj_lands(self, gid: str, p: bj.Pic, cands: dict[str, Candidate], now: float) -> tuple[bool, float]:
        at = self.snap_parked(cands[gid].target) if self.snap_parked is not None else None
        hp = bj.frozen(p, now, at) if at is not None else p
        return bj.lands(hp, now, float(bj.cfg_get(self.cfg, "flip_fast_s")), self._bj_flip_cost(cands[gid].target),
                        float(bj.cfg_get(self.cfg, "lead_min_s")), float(bj.cfg_get(self.cfg, "margin_sigmas")))

    def _bj_flip_cost(self, target: str | None) -> float:
        """[P1-ROOTCAUSE-04] The slowest arrival a buffered landing is judged with: the same max(flip_s, cost) the flip's
        own check (jump_lands_dead) uses, so a cut decided here isn't vetoed a second later (00:15:41, 3.2 s vs 4.1 s)."""
        flip = float(bj.cfg_get(self.cfg, "flip_s"))
        return max(flip, self._deadline_cost(target)) if self.deadline_cost is not None else flip

    def _bj_screen_gone(self, cands: dict[str, Candidate], now: float) -> Decision | None:
        """The game on screen isn't in the feed any more: jump to a ready standby that lands on a dead ball (buffered
        picture by the play-by-play, else jump mode's own landing check)."""
        ready = self.standby_ready
        for gid, x in self._jump_pool(cands).items():
            if gid == self.current or (ready is not None and not ready(x.target)):
                continue
            p = self._bj_pic(gid, x, now)
            if p is not None:
                ok, h_snap = self._bj_lands(gid, p, cands, now)
            else:
                ok, h_snap = self.jump_lands_dead(gid, x, now, self._bj_flip_cost(x.target)), -1.0
            if not ok:
                continue
            info = {"why": "bj_screen_gone", "buffered": p is not None, "h_snap_s": round(h_snap, 1),
                    "behind_s": round(self.delays.get(x.target).behind, 1), "here_behind_s": 0.0,
                    "gate": "the screen's game left the feed", "holds": dict(self.jump_holds), "snap": False,
                    "big": False, "cost_s": float(bj.cfg_get(self.cfg, "flip_s")), "held_s": round(now - self.since, 1)}
            self.jump_holds = Counter()
            return Decision(now, "switch", gid, f"jump: the game on screen left the feed -> {self._short_gid(gid)}",
                            State.WATCHING, info["gate"], kind="jump", jump=info)
        self.jump_holds["bj_screen_gone_no_landing"] += 1
        return None

    def _bj_keep_standby(self, d: Decision, cands: dict[str, Candidate], now: float) -> Decision | None:
        """[overseer Nov 7 23:43] Screen game unknown or gone, a buffered standby ready: the normal decider's 'start' /
        'ended' switch to ANOTHER game re-tunes that stick and throws the buffer away (live: a held Prime standby lost to
        a WVU tune that never started). Hold it for buffered_jump.keep_standby_s while the free cut waits for its dead
        ball; after that the normal switch goes."""
        if d.action != "switch" or d.kind not in ("start", "ended"):
            return None
        ready = self.standby_ready
        held = [gid for gid, x in self._jump_pool(cands).items() if gid != d.target and gid != self.current
                and (ready is None or ready(x.target)) and self._bj_pic(gid, x, now) is not None]
        if not held:
            self.gone_m.to("present", "no buffered standby to keep", now=now)
            return None
        if self.gone_m.is_("present"):
            self.gone_m.to("keeping", f"a {d.kind} switch would re-tune the buffered standby ({held[0]})", now=now)
        if now - self.gone_m.since > float(self.cfg.get("buffered_jump.keep_standby_s", 120.0)):
            self.gone_m.to("let_go", "keep_standby_s ran out: the normal switch goes", now=now)
            return None
        if self.gone_m.is_("let_go"):
            return None
        self.jump_holds["bj_keep_standby"] += 1
        return Decision(now, "stay", self.current, f"jump mode: keeping the buffered standby ({d.reason})", d.state,
                        d.gate, want=held[0])

    def _buffered_jump(self, cur: Candidate, cands: dict[str, Candidate], now: float):
        """The editor's call: a jump Decision, False (holds; counted as bj_<why>) or None (not both buffered: jump
        mode's own rules decide)."""
        v = self._bj_pic(self.current or "", cur, now)
        ready = self.standby_ready
        hs = [(gid, x) for gid, x in self._jump_pool(cands).items()
              if gid != self.current and (ready is None or ready(x.target))]
        pics = [(gid, p) for gid, x in hs if (p := self._bj_pic(gid, x, now)) is not None]
        if v is None:
            # [bootstrap, overseer Nov 8 21:47] the screen's stick was tuned at the live edge (before the flag, or a failed
            # scrub): once the hidden stick holds a buffered game, go there at the next safe dead ball (jump mode's own
            # leave gate; the landing on the buffered picture), and from then on both pictures are buffered
            # [P1-ROOTCAUSE-01] counted only when a cut is actually wanted: the old every-tick counter fell through to
            # play_on_screen on the same ticks, so the holds read as 126 min when they were 74
            if pics:
                self.jump_holds["bj_bootstrap_wanted"] += 1
            g = self._jump_gate(cur, now, strict=True) if pics else None
            for gid, p in pics:
                ok, h_snap = self._bj_lands(gid, p, cands, now)
                if g is not None and g.safe and ok:
                    info = {"why": "bj_bootstrap", "buffered": True, "h_snap_s": round(h_snap, 1),
                            "behind_s": round(self.delays.get(cands[gid].target).behind, 1), "here_behind_s": 0.0,
                            "gate": g.why, "holds": dict(self.jump_holds), "snap": False, "big": False,
                            "cost_s": float(bj.cfg_get(self.cfg, "flip_s")), "held_s": round(now - self.since, 1)}
                    self.jump_holds = Counter()
                    return Decision(now, "switch", gid, f"jump: onto the buffered picture -> {self._short_gid(gid)}",
                                    State.WATCHING, g.why, kind="jump", jump=info)
            return None
        if not pics:
            self.jump_holds["bj_standby_not_buffered" if hs else "bj_standby_not_ready"] += 1
            return None if hs else False
        seen = self._jump_plays_seen(self.current, cur, now)
        park = bool(bj.cfg_get(self.cfg, "park"))
        best = None
        for gid, p in pics:
            if park:
                at = self.snap_parked(cands[gid].target) if self.snap_parked is not None else None
                c = bj.cut_parked(v, bj.frozen(p, now, at), now, self.cfg, plays_seen=seen,
                                  land_s=self._bj_flip_cost(cands[gid].target)) if at is not None \
                    else bj.Cut(False, "not_parked")
            else:
                c = bj.cut_now(v, p, now, self.cfg, plays_seen=seen, land_s=self._bj_flip_cost(cands[gid].target))
            if c.go and (best is None or c.info.get("h_snap_s", 99) < best[1].info.get("h_snap_s", 99)):
                best = (gid, c)
            elif best is None:
                last = c
        if best is None:
            self.jump_holds[f"bj_{last.why}"] += 1
            return False
        gid, c = best
        held = now - self.since
        info = {**c.info, "why": f"bj_{c.why}", "buffered": True, "held_s": round(held, 1),
                "behind_s": round(self.delays.get(cands[gid].target).behind, 1),
                "here_behind_s": round(self.delays.get(cur.target).behind, 1),
                "gate": "buffered: dead ball on screen (play-by-play)", "holds": dict(self.jump_holds),
                "snap": c.why in ("snap_next", "parked_snap"), "big": False, "cost_s": float(bj.cfg_get(self.cfg, "flip_s"))}
        self.jump_holds = Counter()
        reason = (f"jump: dead ball after {seen} play{'s' if seen != 1 else ''} ({c.why.replace('_', ' ')}) -> "
                  f"{self._short_gid(gid)}: snap in {c.info.get('h_snap_s')}s")
        return Decision(now, "switch", gid, reason, State.WATCHING, info["gate"], kind="jump", jump=info)

    def _own(self, d: Decision, cands: dict[str, Candidate], now: float) -> tuple[str, str]:
        """[owner] (owner, why) for this tick: who owns the TV (decider.owner) and the short code jump_holds counts when
        the rotation stands aside. The one place the handoff is decided: _jump runs only for 'jump' / 'buffered', and
        the rotation vetoes the normal decider's optional switches only then (_jump_owns)."""
        u = self.user
        if self.safe_mode:
            return "safe", "safe_mode"
        if u.paused:
            return "user", "paused"
        if u.locked_game:
            return "user", "locked_game"
        if now < u.manual_until:
            return "user", "manual_pause"
        if u.focus_mode != FocusMode.AUTOPILOT:
            return "user", "focus_mode"
        # A replay owns the way back to live: its after-replay switch (live / home / break_visit) is never a jump-mode
        # stay, or the screen sat on old replay footage (after_replay only clears on a live switch)
        if self.replaying:
            return "replay", "replaying"
        if self.after_replay:
            return "replay", "after_replay"
        if not self.jump_on():
            return "normal", "jump_off"
        cur = cands.get(self.current or "")
        rotation = cur is not None and self.current not in PSEUDO and self._jump_league(cur)
        rot = "buffered" if self.buffered_on() else "jump"
        if now < self._arrival_until:
            # the tune is still landing: the rotation waits; it still owns the optional switches of a rotation game
            return (rot if rotation else "normal"), "just_arrived"
        # [overseer Nov 7 22:40] also current None: after every restart the screen's game is unknown and jump mode held
        # 'screen_game_unknown' for 10+ min (514 ticks on 0b0f8534) with a ready, scrubbed standby. The normal decider
        # already 'start's from an unknown screen; the landing is still timed on a dead ball (Rule #1 on entry).
        if cur is None and (self.current is None or self.current not in PSEUDO) and self.buffered_on() \
                and d.kind not in ("user", "crunch"):
            return "buffered", "screen_gone"
        if d.action in ("switch", "replay") and d.kind in JUMP_KEEPS:
            return "hard", f"normal_{d.kind}"   # crunch time, a finished game, your tap, a chase: it already goes
        if not rotation:
            return "normal", "screen_not_in_rotation" if cur is not None else "screen_game_unknown"
        return rot, "rotation"

    def _jump_owns(self, d: Decision, cands: dict[str, Candidate], owner: str | None = None,
                   why: str | None = None) -> bool:
        """[jump mode] Is d an optional switch out of a rotation game, which the rotation decides instead? Derived from
        the tick's owner (_own), so it can't disagree with _jump about who stands aside."""
        if owner is None:
            owner, why = self._own(d, cands, self._clock())
        if owner not in ("jump", "buffered") or why == "screen_gone":
            return False
        return d.action == "switch" and d.kind not in JUMP_KEEPS and d.kind != "jump"

    def _jump(self, d: Decision, cands: dict[str, Candidate], now: float, why: str | None = None) -> Decision | None:
        """[jump mode] The rotation's switch for this tick, or None (keep the normal decision). Runs only when the
        rotation owns the TV (decide() -> _own); every other owner's stand-aside code is counted in _count_hold."""
        if why is None:
            owner, why = self._own(d, cands, now)
            if owner not in ("jump", "buffered") or why == "just_arrived":
                self._count_hold(why)
                return None
        c = self.cfg
        if why == "screen_gone" and self._restart_hold(cands, now):
            # [P-04] after a restart the unknown screen is probably the close, late game we saved: no free cut, no 'start'
            # switch away from it until adopt() learns the screen or the hold runs out
            self.jump_holds["bj_restart_hold"] += 1
            return Decision(now, "stay", self.current, f"restart: holding the saved game's leverage ({d.reason})", d.state,
                            d.gate, want=d.target)
        if why == "screen_gone" and self._gone_hold(None, now) is not None:
            self.jump_holds["bj_screen_gone_hold"] += 1   # [D-05] the last play is still airing, or the gap isn't proven
            return None   # the normal decider's own 'ended' waits on the same hold
        if why == "screen_gone":
            # [overseer Nov 8 22:15] the screen's game left the feed (over, or dropped from the list): nothing on screen
            # to cut out of, so it's a free cut onto the first ready standby that lands on a dead ball (ahead of the
            # normal 'ended' switch, which doesn't time the landing on a buffered picture)
            gone = self._bj_screen_gone(cands, now)
            if gone is not None:
                self.gone_m.to("present", "free cut onto the ready standby", now=now)
                return gone
            keep = self._bj_keep_standby(d, cands, now)
            if keep is not None:
                return keep
            if d.action in ("switch", "replay") and d.kind in JUMP_KEEPS:
                self.jump_holds[f"normal_{d.kind}"] += 1
                return None   # crunch time, a finished game, your tap, a chase: the normal decider already goes
            self.jump_holds["screen_game_unknown"] += 1
            return None
        cur = cands[self.current]
        hold = self._jump_hold(cur)
        if hold:
            self.jump_holds[hold] += 1
            return None
        if self.buffered_on():
            b = self._buffered_jump(cur, cands, now)
            if b is not None:
                return b or None   # False: the editor holds this tick (counted); a Decision: cut now
        g = self._jump_gate(cur, now)
        if g is None or not g.safe:
            self.jump_holds["play_on_screen"] += 1
            return None
        seen = self._jump_plays_seen(self.current, cur, now)
        held = now - self.since
        on_break = bool(g.on_screen_break or cur.scored.is_break)
        due = on_break or seen >= int(c.get("jump_mode.max_plays", 2)) \
            or held >= float(c.get("jump_mode.max_hold_s", 90))
        best = self._jump_best(cands, now, exclude=self.current)
        if best is None:
            return None   # _jump_best counted why
        gid, info = best
        hot = info["snap"] or info["big"]
        # [Nick: "at EVERY dead ball ... flip if the other stick holds a game whose next snap is coming"] even with
        # no play seen here yet, go where the next snap comes clearly sooner than ours (snap_gain_s): the
        # rotation chases snaps across games instead of sitting through this huddle
        mine = self._jump_snap_wait(self.current, cur, cands, now)
        sooner = info["snap"] and info["snap_in_s"] + info["cost_s"] + float(c.get("jump_mode.snap_gain_s", 10)) < mine
        info["here_snap_s"] = round(mine, 1)
        # [overseer Nov 7 20:05] the standby's game is ready and lands on a dead ball: go at this dead ball (after
        # min_plays here), not after the play budget. Readiness is the bottleneck, so a ready flip isn't wasted.
        ready_go = bool(c.get("jump_mode.go_when_ready", True)) and self.standby_ready is not None \
            and self.standby_ready(cands[gid].target) and seen >= int(c.get("jump_mode.min_plays", 1))
        if not due and not (seen >= int(c.get("jump_mode.min_plays", 1)) and hot) and not sooner and not ready_go:
            self.jump_holds["budget_left"] += 1
            return None
        if on_break:
            why = "break"
        elif seen >= int(c.get("jump_mode.max_plays", 2)):
            why = "play_budget"
        elif held >= float(c.get("jump_mode.max_hold_s", 90)):
            why = "max_hold"
        elif seen >= int(c.get("jump_mode.min_plays", 1)) and hot:
            why = "hot_target"
        else:
            why = "sooner_snap" if sooner else "standby_ready"
        info = {**info, "why": why, "plays_seen": seen, "held_s": round(held, 1), "gate": g.why,
                "holds": dict(self.jump_holds)}
        self.jump_holds = Counter()
        tags = [t for t, on in (("snap coming", info["snap"]), ("big moment", info["big"])) if on]
        reason = (f"jump: dead ball after {seen} play{'s' if seen != 1 else ''} ({why.replace('_', ' ')})"
                  f" -> {self._short_gid(gid)}" + (f": {', '.join(tags)}" if tags else ""))
        return Decision(now, "switch", gid, reason, State.WATCHING, g.why, kind="jump", jump=info)

    def _count_hold(self, why: str) -> None:
        """[overseer Nov 8 01:50Z: "no jump rows, decision text is the normal decider"] every way the rotation stands
        aside is counted (data/jump_status.jsonl holds), not only the waits at a dead ball. Jump mode off: nothing."""
        if self.jump_on() and why not in ("jump_off", "rotation"):
            self.jump_holds[why] += 1

    @staticmethod
    def _short_gid(gid: str) -> str:
        return gid.rsplit(":", 1)[-1][:24]

    # ----- helpers ----------------------------------------------------------

    def _chase_any(self, watchable, now: float, exclude: str | None, floor: float) -> tuple[str, Event] | None:
        """The best game (scoring at least floor) with a big play in the data that hasn't aired yet."""
        chases = []
        for gid, x in watchable.items():
            # Don't skip games "in a break": the play that caused the break hasn't aired yet.
            if gid == exclude or gid not in self.timelines:
                continue
            ev = catchable(self.timelines[gid], self.delays.get(x.target), now, tune_s=self._deadline_cost(x.target) + 3)
            if ev and x.scored.score >= floor:
                chases.append((x.scored.score, gid, ev))
        if not chases:
            return None
        _, gid, ev = max(chases, key=lambda z: z[0])
        return gid, ev

    def _is_hot(self, x: Candidate | None, heat: float | None = None) -> bool:
        """[redzone mode] An NFL game whose drive is alive (scoring/drive.py heat)."""
        if x is None or not self.cfg.get("redzone_mode.enabled", True):
            return False
        s = x.scored
        thr = float(self.cfg.get("redzone_mode.hot_heat", 0.5)) if heat is None else heat
        return s.league.base == League.NFL and not s.garbage and s.factors.get("drive_heat", 0.0) >= thr

    def _hot_live(self, cands: dict[str, Candidate], heat: float | None = None) -> str | None:
        """[redzone mode] The hottest watchable NFL drive (heat >= redzone_mode.hot_heat) whose next
        snap isn't behind a break, on the feed or on screen."""
        hot = [(x.scored.factors.get("drive_heat", 0.0), x.scored.score, gid) for gid, x in cands.items()
               if gid not in PSEUDO and x.target and not x.stale and gid not in self.user.muted
               and self._is_hot(x, heat) and not x.scored.is_break and gid not in self._screen_break]
        return max(hot)[2] if hot else None

    def _rz_drive(self, watchable, now: float) -> str | None:
        """[redzone_mode.drive_in_20] The best tunable NFL game with a live drive inside the opponent's
        20 (the scorer's "red zone drive" lock), not in or about to go to a break, other than the one
        on screen. Most NFL touchdowns are scored from there (valid split: 178 of 321 big plays, 43 %
        of them seen live), and RedZone's producers go there first."""
        if not self.cfg.get("redzone_mode.drive_in_20", False):
            return None
        cap = float(self.cfg.get("redzone_mode.takeover_break_p", 0.5))
        hot = [(x.scored.score, gid) for gid, x in watchable.items()
               if gid != self.current and gid not in PSEUDO and x.target and not x.stale
               and gid not in self.user.muted and x.scored.league.base == League.NFL
               and x.scored.lock == "red zone drive" and not x.scored.is_break
               and gid not in self._screen_break and x.scored.factors.get("break_p", 0.0) < cap]
        return max(hot)[1] if hot else None

    def _drive_takeover(self, watchable, gate_ok: bool, state, gate_txt: str, now: float) -> Decision | None:
        """[redzone mode] On the RedZone channel while a game we can tune has a live drive going: watch
        that drive ourselves, live, instead of whichever game RedZone's producers cut to. Runs after
        crunch time, chases and catch-up replays have had their say; a break visit's trip home still
        comes first (home_due is decided before this). RedZone has no play of ours to cut."""
        if (self.current != REDZONE or not gate_ok or not self.cfg.get("redzone_mode.enabled", True)
                or not self.cfg.get("redzone_mode.drive_over_redzone", True)):
            return None
        if now - self.since < float(self.cfg.get("redzone_mode.min_redzone_s", 20)):
            return None     # just arrived: give the whip-around a moment before chasing a drive
        # A broadcast that is in a break, or about to go to one (the break forecast: a field goal try,
        # a punt coming, the two-minute warning, a score still on its way to the screen), is a
        # commercial waiting to land on us. Stay on RedZone and take the drive over once it's live.
        cap = float(self.cfg.get("redzone_mode.takeover_break_p", 0.5))
        calm = {g: x for g, x in watchable.items() if x.scored.factors.get("break_p", 0.0) < cap}
        gid = self._hot_live(calm, self.cfg.get("redzone_mode.takeover_heat"))
        if gid is None:
            return None
        return Decision(now, "switch", gid, self._why(watchable[gid], "live drive →"),
                        state, gate_txt, kind="drive")

    def _nfl_priority(self, cands: dict[str, Candidate]) -> dict[str, Candidate]:
        """[redzone mode] While an NFL drive is alive (Scored.factors["drive_heat"], scoring/drive.py),
        other sports' non-crunch moments rank under it. Never touches crunch time, your teams/bets,
        your focus game, NFL games themselves, or a league you rank above the NFL
        (score.league_weight, as the scorer already applied it: Scored.factors["league"])."""
        c = self.cfg
        if not c.get("redzone_mode.enabled", True) or not c.get("redzone_mode.nfl_priority", True):
            return cands
        hot = [x.scored for gid, x in cands.items()
               if gid not in PSEUDO and x.target and not x.stale and gid not in self.user.muted
               and not x.scored.is_break and self._is_hot(x)]
        if not hot:
            return cands
        top = max(hot, key=lambda s: s.score)
        nfl_w = float(top.factors.get("league", 1.0))
        ceiling = top.score - float(c.get("redzone_mode.priority_gap", 15))
        keep = c.get("decider.crunch.hold_personal", 5)
        out = {}
        for gid, x in cands.items():
            s = x.scored
            if (s.league.base == League.NFL or gid in PSEUDO or s.crunch or s.lock in CRUNCH_LOCKS
                    or s.personal >= keep or gid == self.user.focus_game or s.score <= ceiling
                    or float(s.factors.get("league", 1.0)) > nfl_w):
                out[gid] = x
                continue
            out[gid] = replace(x, scored=replace(s, score=max(0.0, ceiling), tags=s.tags + ["NFL drive on"]))
        return out

    def _with_redzone(self, cands: dict[str, Candidate], target: str | None) -> dict[str, Candidate]:
        """Add RedZone as a candidate while it's on the air and you have it turned on."""
        if not target or not self.cfg.get("decider.redzone_channel", False):
            return cands
        score = self.cfg.get("decider.redzone_score", 45)
        if self.cfg.get("redzone_mode.enabled", True):
            # [redzone mode] RedZone is showing the hottest drive too: it gets the same drive-heat lift,
            # so a hot drive alone doesn't pull us off a commercial-free whip-around we can leave any time.
            heat = max((x.scored.factors.get("drive_heat", 0.0) for x in cands.values()
                        if x.scored.league.base == League.NFL and not x.scored.garbage), default=0.0)
            score += float(self.cfg.get("redzone_mode.redzone_lift", 8)) * heat
        # The reasoner's lock_in points on an NFL game also go to RedZone: RedZone whips to that
        # game's big play anyway, with no commercials, so a lock-in is never a reason to leave it
        # for one broadcast (Nov 3: MIN @ SEA slate, +370 s of commercials from exactly that).
        lock = max((x.scored.factors.get("reasoner", 0.0) for x in cands.values()
                    if x.scored.league.base == League.NFL and not x.scored.garbage), default=0.0)
        score += max(0.0, min(1.0, float(self.cfg.get("reasoner.redzone_share", 1.0)))) * lock
        if self.cfg.get("redzone_mode.enabled", True):
            # ...but once we ARE on a live drive, RedZone's lift is for that same drive: letting it
            # win here only bounces us back and forth across one drive (the whip-around's producers
            # will cut away from it; we won't). Hold it under what we're watching.
            cur = cands.get(self.current) if self.current else None
            if self._is_hot(cur) and not cur.scored.is_break:
                score = min(score, cur.scored.score)
        return {**cands, REDZONE: Candidate(redzone_scored(score), target)}

    def _gate(self, cur: Candidate, now: float) -> Gate | None:
        """Can we leave what's on screen? RedZone is a whip-around: there's no play of ours to cut."""
        return self._gate_at(cur, now, self.current)

    def _gate_at(self, cur: Candidate, now: float, gid: str | None) -> Gate | None:
        if gid == REDZONE:
            return Gate(True, "on RedZone: nothing to cut out of")
        if gid == REEL:
            return Gate(True, "on the catch-up reel: nothing live to cut")
        tl = self.timelines.get(gid)
        if tl is None:
            return None
        d = self.delays.get(cur.target or "unknown")
        g = leave_gate(tl, d, now, self.cfg.get("decider.uncalibrated_sigma_s"))
        lgs = self.cfg.get("decider.leave.dead_windows") or []
        if not g.safe and cur.scored.league.base.value in lgs and tl.dead.marks:
            # [whistle detection] the play-by-play's own dead windows (cuts.DeadTracker, what entries land
            # on): a whistle whose stoppage never reached the game state (the faceoff came in the same
            # poll) is still a dead ball. Safe only if the whole doubt band on screen is inside it.
            m = 2 * d.current_sigma(now)
            until = tl.dead.dead_until(now - d.mu - m)
            if until is not None and until >= now - d.mu + m + float(self.cfg.get("decider.leave.tune_s")):
                return Gate(True, "dead ball on screen (play-by-play)")
        if not g.safe and getattr(tl, "events", None) and self._leavable(cur, now, d):
            # [cut rules, Nick Nov 4 3:15 PM] Leaving mid-play is fine at LOW leverage (Q1-Q3, a game
            # that isn't close, right after a score): you lose the end of an ordinary play, never an entry.
            return Gate(True, "low-leverage moment: leaving mid-play is OK")
        return g

    def _leavable(self, cur: Candidate, now: float | None = None, d=None) -> bool:
        """[cut rules, decider.leave.low_leverage] May the screen leave this game mid-play? Only at low
        leverage: never in crunch time or its run-up, a locked moment (red zone, power play, OT...), your
        team's game, or a close game late (decider.leave.sticky_*: down the stretch is sticky).
        [leave floor, EXP-208] Keyed on the FULL league (CBB is not NBA). Your team's game is never left
        mid-play, whatever its personal score (Q1 WSH sat under hold_personal), and neither is a
        close-and-late / overtime moment (scoring.engine.high_leverage, the grader's floor)."""
        lgs = self.cfg.get("decider.leave.low_leverage") or []
        s = cur.scored
        if s.league.value not in lgs:
            return False
        if getattr(s, "mine", False) or getattr(s, "high_leverage", False) or s.game_id in self._yours:
            return False
        hl = self._high_lev_at.get(s.game_id)
        if hl is not None and now is not None:
            # the screen shows the feed of ~mu ago (+ its doubt): close-and-late then is still on screen now
            on_screen = (d.mu + 2 * d.current_sigma(now)) if d is not None else 180.0
            if now - hl <= on_screen:
                return False
        if s.crunch or s.crunch_now or s.lock or s.imminent or s.pre_moment:
            return False
        if s.personal >= float(self.cfg.get("decider.crunch.hold_personal", 5)):
            return False
        if s.late and not s.garbage and s.closeness >= float(self.cfg.get("decider.leave.sticky_closeness", 0.5)):
            return False
        return True

    def _mark_screen_breaks(self, watchable: dict[str, Candidate], now: float) -> None:
        """A game whose break is still showing on screen is a bad destination even if the data
        says play resumed: you'd land in the tail of a commercial. Fresh every tick: a stale set
        once hid the only live game for the rest of a slate."""
        self._screen_break = set()
        for gid, x in watchable.items():
            gtl = self.timelines.get(gid)
            if gid == self.current or gtl is None:
                continue
            d = self.delays.get(x.target)
            if self._must_see_resume(gid, x):
                # Worth landing a few seconds early to see the snap that ends the break.
                dead = leave_gate(gtl, d, now, 99.0).on_screen_break
            else:
                dead = break_on_arrival(gtl, d, now, now + self._deadline_cost(x.target))
            if dead:
                self._screen_break.add(gid)

    def _home_due(self, home: str, x: Candidate, now: float) -> bool:
        """Time to head back to the game we left for its break? Stay away as long as possible:
        just before the snap that ends the break airs if that snap is worth seeing, else once
        it has aired (a routine snap isn't worth half a minute of the ad's tail)."""
        htl = self.timelines.get(home)
        end_ev = htl.last("stop_end") if htl else None
        start_ev = htl.last("stop_start") if htl else None
        resumed = end_ev is not None and (start_ev is None or end_ev.seen >= start_ev.seen)
        if self.home_return_at is not None and now >= self.home_return_at:
            return True   # long break: planned trip back, early enough to find a clean exit
        if not resumed:
            return not x.scored.is_break
        hd = self.delays.get(x.target or "unknown")
        if self._must_see_resume(home, x):
            return now >= end_ev.seen + hd.mu - 2 * hd.current_sigma(now) - self._deadline_cost(x.target) - 6
        return not break_on_arrival(htl, hd, now, now + self._deadline_cost(x.target))

    def _short_stop(self, gid: str | None, s: Scored) -> bool:
        """[pacing] Is this football/basketball game in a kind of break that ends before a visit pays off?
        Not in crunch time: there a timeout is the moment to check the other close game."""
        tl = self.timelines.get(gid) if gid else None
        if tl is None or s.league.base not in SHORT_STOP_LEAGUES or s.crunch or s.lock in CRUNCH_LOCKS:
            return False
        start, end = tl.last("stop_start"), tl.last("stop_end")
        short = start is not None and start.label in SHORT_STOPS and (end is None or end.seen < start.lo)
        if short and s.league.base == League.NBA and not tl.dead.marks:
            return False   # no timing data, no proof it's short: an NBA TV timeout runs minutes
        if not short or not tl.dead.marks:
            return short
        # [cut timing] Which kind of stop is it? A team timeout (median 43 s) is over before a visit
        # pays off; an official TV timeout (147 s) or a replay review (217 s) is worth the trip.
        x = self._known.get(gid)
        d = self.delays.get((x.target if x else None) or self._known_target.get(gid) or "unknown")
        screen = self._clock() - d.mu
        return tl.dead.resume_eta(screen) - screen < float(self.cfg.get("decider.entry.visit_min_s", VISIT_MIN_S))

    def _routine_tail(self, cur: Candidate, gate: Gate | None, watchable, now: float) -> bool:
        """The ad is still on screen but the break is too far along to visit a game and get back.
        If what ends it is a routine snap, RedZone beats the ad's tail; we return once it airs."""
        if not gate or not gate.safe or gate.on_screen_break or REDZONE not in watchable \
                or self.current == REDZONE:
            return False
        tl = self.timelines.get(self.current)
        return tl is not None and break_on_arrival(tl, self.delays.get(cur.target or "unknown"), now, now) \
            and not self._must_see_resume(self.current, cur)

    def _must_see_resume(self, gid: str, x: Candidate) -> bool:
        """Is the first snap after this game's break worth a few seconds of ad to see live?
        Yes in crunch time (every snap matters) or when that snap is a big play. Always outside
        football: there the first logged event comes well after play has resumed."""
        if x.scored.league.base != League.NFL:
            return True
        tl = self.timelines.get(gid)
        snap = resume_play(tl) if tl else None
        return bool(x.scored.lock in CRUNCH_LOCKS or (snap and snap.big))

    # ----- [core] the views the decision core scores ---------------------------

    def _option(self, gid: str, x: Candidate) -> Option:
        s = x.scored
        kind = "redzone" if gid == REDZONE else "reel" if gid == REEL else "game"
        # On a break for the core: in the data, or (for a destination) still on screen when we'd land.
        on_break = s.is_break or (gid != self.current and gid in self._screen_break)
        brain = s.brain or {}
        return Option(gid, kind, x.target,
                      MomentView(s.score, float(brain.get("key_soon_p", 0.0) or 0.0), s.lock, s.crunch,
                                 bool(getattr(s, "late", False)), s.garbage, s.personal,
                                 mine_late=self._mine_late(s)),
                      BreakView(on_break, break_soon_p=float(brain.get("break_soon_p", 0.0) or 0.0)),
                      paths=(SwitchPath("tune", self._rank_cost(x.target)),))

    def _cstate(self, cands: dict[str, Candidate], now: float | None = None) -> CoreState:
        opts = {gid: self._option(gid, x) for gid, x in cands.items()}
        cur = self.current
        if cur and cur not in opts and cur in self._known:
            opts[cur] = self._option(cur, self._known[cur])
        return CoreState(self._clock() if now is None else now, cur, opts, home=self.home_break_visit)

    def _predict_points(self, gid: str, x: Candidate) -> float:
        """[noggin predict] L4 term: noggin.predict.points x (P(big play within 60 s of the restart) - the
        league's base rate), bounded to +-points. 0 unless noggin.predict.leagues has this league."""
        lg = x.scored.league
        hooks = self.__dict__.setdefault("_predict_hooks", {})
        if lg not in hooks:
            from redzone.noggin.live import predict_hook_for
            hooks[lg] = predict_hook_for(self.cfg, lg)
        hook, tl = hooks[lg], self.timelines.get(gid)
        if hook is None or tl is None:
            return 0.0
        try:
            r = hook.reading(gid, tl, self._clock())
        except Exception:  # noqa: BLE001 - a bad reading only drops the term
            log.exception("noggin predict for %s", gid)
            return 0.0
        if not r or 60 not in r:
            return 0.0
        pts = float(self.cfg.get("noggin.predict.points", 20))
        base = float((hook.model.info.get("rate") or {}).get("60", 0.0))
        return max(-pts, min(pts, pts * (r[60] - base)))

    def _best(self, watchable: dict[str, Candidate], exclude: str | None) -> str | None:
        """The best destination by the core's rate: never the reel (a break filler), never a game whose
        break is on screen when we'd land."""
        st = self._cstate(watchable)
        opts = [st.options[g] for g in watchable
                if g != exclude and g != REEL and not st.options[g].brk.on_break]
        if not opts:
            return None
        best = max(opts, key=lambda o: core.rank(o, self.core.k)).id
        if all(self._lc("noggin.priority.enabled", watchable[o.id], False) for o in opts):
            # [league overrides] only when every option's league runs it: one scorer per pick, so a
            # mixed slate (NBA + CBB) never compares two scales; otherwise the live rule below.
            # [noggin priority] L1 hard rules first (your team > crunch by league order > open; never a
            # banned app), then score: redzone/noggin/priority.py through redzone/noggin/wire.py.
            from redzone.noggin import wire
            hold = self.cfg.get("decider.crunch.hold_personal", 5)
            yours = self._yours | {g for g in watchable if watchable[g].scored.personal >= hold}
            values = {o.id: core.rate(o, self.core.k) for o in opts}
            for g in values:
                values[g] += self._predict_points(g, watchable[g])
            best = wire.pick(self.cfg, watchable, [o.id for o in opts], yours, values)
            if best is None:
                return None   # every option on a banned app (never Peacock)
            # [nhl pp] the live guard below holds here too: the priority tiers rank a power play
            # (a big moment) over your team late-but-not-crunch, which the live rule never allowed
        if watchable[best].scored.lock == "power play":
            # [nhl pp] A power play is always worth a look, but never over your team late and close
            # (Oct 29 replay: back from a catch-up onto NYI @ MIN's power play instead of WSH, tied late).
            mine = [o for o in opts if o.moment.mine_late]
            if mine:
                return max(mine, key=lambda o: core.rank(o, self.core.k)).id
        return best

    def _break_destination(self, watchable, exclude, home: str | None = None) -> str | None:
        """Where to spend the home game's break. If home is a game you can't afford to miss the
        resume of (your team, a bet, your focus game, crunch time), only go somewhere you can
        ALWAYS leave in time: basketball/hockey only have clean exits at whistles, and a visit
        there stranded us for 3 minutes of a Wizards crunch (backtest, Oct 26)."""
        options = watchable
        now = self._clock()
        anchor = home or exclude   # on a chained visit, the game we must get back to is the real home
        if anchor and self._sticky_home(anchor, watchable):
            strict = self._visit_needs_exit(anchor, watchable)
            options = {gid: x for gid, x in watchable.items()
                       if gid in PSEUDO or (self._exits_often(gid) and (not strict or self._exit_provable(x, now)))}
        # [break visits] Only where the trip there and back leaves something worth watching; and
        # of those, the one that shows the most (score x seconds on screen): an already-loaded
        # stick beats a slightly better game behind a 40 s cold tune.
        watch = {gid: self._visit_watch(exclude, gid, now) for gid in options}
        need = VISIT_MIN_WATCH_S if self._visit_due_by_break_end(exclude) else 0.0
        hold = self.cfg.get("decider.crunch.hold_personal", 5)

        def crunch(x: Candidate) -> bool:
            return bool(x.scored.crunch or x.scored.lock in CRUNCH_LOCKS)

        # A game clearly better than home is no round trip: once there, decide() releases home
        # (score + switch_margin, or crunch that outranks it) and we stay, so the trip back never
        # has to fit. NHL Oct 20: a BOS @ DAL goal break (15 s left) was skipped as "can't fit" for
        # WPG @ STL, up 1 with 3:57 left, which the code before the budget visited and kept; we got
        # there only at 1:16 left.
        home_x = (watchable.get(anchor) or self._known.get(anchor)) if anchor else None
        margin = self.core.k.switch_margin

        def keeper(x: Candidate) -> bool:
            if home_x is None:
                return False
            return (home_x.scored.score + margin < x.scored.score
                    or self._crunch_outranks(x.scored, home_x.scored))

        # Crunch time elsewhere, the user's own team, and a game we'd stay on are worth the trip
        # whatever is left of this break.
        fits = {gid: x for gid, x in options.items()
                if watch[gid] is None or watch[gid] > need or crunch(x) or x.scored.personal >= hold
                or keeper(x)}
        pool = [gid for gid, x in fits.items()
                if gid != exclude and gid != REEL and not x.scored.is_break and gid not in self._screen_break]
        # [core] Worth leaving the commercial for: the core's visit ev (rate against the ad, bar
        # decider.break_destination_min); ranked by the core's window value, rate x seconds on screen.
        st = self._cstate(fits, now)
        k = self.core.k
        worth = [g for g in pool if core.ev(st, "visit", g, k).gain >= 0]
        if worth:
            # Crunch first, then the user's team, and only then score x seconds on screen: a
            # preloaded neutral game must never pull the viewer away from crunch time or from
            # their own team behind a cold (e.g. Prime) tune.
            return max(worth, key=lambda g: (crunch(fits[g]), fits[g].scored.personal >= hold,
                                             core.window_value(st.options[g], k, watch[g] or 1.0),
                                             core.rate(st.options[g], k)))
        # Nothing live worth a visit: the catch-up reel, if it fits in the break with the trip back.
        if REEL in watchable and exclude != REEL and anchor and self._reel_fits(anchor, watchable):
            return REEL
        # RedZone never shows a commercial: any break is better spent there than on an ad.
        if REDZONE in watchable and exclude != REDZONE and REDZONE in fits:
            return REDZONE
        return None

    def _break_len(self, gid: str) -> float | None:
        """Typical (low side) on-screen length of the break this game is in now, by kind."""
        tl = self.timelines.get(gid)
        start = tl.last("stop_start") if tl else None
        if start is None:
            return None
        known = self._known.get(gid)
        league = known.scored.league if known else tl.league
        label = tl.stoppage.value if tl.stoppage in REAL_BREAKS else start.label
        if label == Stoppage.TIMEOUT.value:
            if league.base == League.NFL and tl.break_kind == "official_timeout":
                label = "official_timeout"
            elif league == League.NBA and tl.period_timeouts in (1, 2):
                label = "tv_timeout"   # NBA: a period's first two timeouts carry the long ad break
            elif league.college and league.base == League.NBA and "officialtv" in tl.break_kind:
                label = "tv_timeout"
        return BREAK_LEN_S.get(league, {}).get(label)

    def _visit_left(self, home: str, now: float) -> float | None:
        """Seconds of home's break still to air: when the data shows the resume, exactly that;
        before, its typical length from when it started on screen. None = no idea."""
        tl = self.timelines.get(home)
        start = tl.last("stop_start") if tl else None
        if start is None:
            return None
        d = self.delays.get(self._target_of.get(home) or "unknown")
        end = tl.last("stop_end")
        if end is not None and end.seen >= start.lo:
            return end.seen + d.mu - 2 * d.current_sigma(now) - now
        length = self._break_len(home)
        if length is None:
            return None
        # An expectation, not a deadline: the trip home is triggered by the resume in the data.
        # A break that outlasts its typical length is still on for as long as the data says so:
        # play can't air before the newest data plus the delay (less a quiet restart, 2 sigma).
        typical = start.seen + d.mu + length - now
        floor = d.mu - tl.lag_est() - resume_lead(tl.league) - 2 * d.current_sigma(now)
        return max(typical, floor)

    def _visit_watch(self, home: str | None, dest: str, now: float) -> float | None:
        """Seconds of `dest` a visit during home's break would show: the break left on screen,
        minus the trip there (preloaded: a flip; else a full tune), the trip back, and the time to
        find a clean exit there. None = no budget known (home's break length is unknown)."""
        if not home or home in PSEUDO:
            return None
        left = self._visit_left(home, now)
        if left is None:
            return None
        there = self._deadline_cost(self._target_of.get(dest))
        if not self._visit_due_by_break_end(home):
            # A routine snap ends it: we come back once that snap has finished airing (_home_due ->
            # break_on_arrival), not when the break could first end. Budget the visit to that
            # moment: measuring it to the early edge of the resume (2 sigma early, backdated by the
            # resume lead) called 10-25 s visits negative and skipped them (CLE @ CHI slate).
            return self._routine_back_at(home, left, now) - now - there
        exit_s = 0.0 if dest in PSEUDO else VISIT_EXIT_S if self._exits_often(dest) else VISIT_EXIT_CONTINUOUS_S
        return left - there - self._return_cost(home) - exit_s

    def _routine_back_at(self, home: str, left: float, now: float) -> float:
        """When a visit during a routine-snap break heads home: the moment arriving home no longer
        lands in the break (the same test _home_due uses), less the trip back. On the late side of
        the delay's error bars (+2 sigma): being late here costs at most part of a routine snap,
        which _home_due already gives up, so a visit is skipped only if it can't show anything
        even then."""
        tl = self.timelines[home]
        d = self.delays.get(self._target_of.get(home) or "unknown")
        start, end = tl.last("stop_start"), tl.last("stop_end")
        play = PLAY_LEN_S.get(tl.league, 8.0) + 2.0
        if end is not None and start is not None and end.seen >= start.lo:
            snap = resume_play(tl)
            aired = (snap.seen if snap else end.seen + play) + d.mu
        else:
            aired = now + left + play   # resume not in the data yet: its expected airing, plus the snap
        return aired + 2 * d.current_sigma(now) - self._deadline_cost(self._target_of.get(home))

    def _visit_due_by_break_end(self, home: str | None) -> bool:
        """Must a visit be back before home's break ends on screen? Yes unless a routine football
        snap ends it (then we return once it has aired, see _home_due)."""
        x = self._known.get(home) if home else None
        return x is None or self._must_see_resume(home, x)

    def _return_cost(self, home: str) -> float:
        target = self._target_of.get(home)
        if self.return_cost is not None:
            cost = float(self.return_cost(target))
            return cost if math.isfinite(cost) and cost >= 0 else float("inf")
        return self._deadline_cost(target)

    def _reel_fits(self, home: str, watchable) -> bool:
        if self.reel_s <= 0 or not watchable[REEL].target or watchable[REEL].scored.is_break:
            return False
        now = self._clock()
        pool = {**self._known, **watchable}
        left = self._break_remaining(home, pool, now)
        trip = self._deadline_cost(watchable[REEL].target) + self._deadline_cost(self._target_of.get(home))
        return core.reel_fits(left, self.reel_s, trip, self.core.k)

    def _with_reel(self, cands: dict[str, Candidate], reel: tuple[str, float] | None) -> dict[str, Candidate]:
        """Add the catch-up reel while one is ready and putting it on the TV is turned on."""
        if self.current != REEL:
            self.reel_s = reel[1] if reel else 0.0
        if not self.cfg.get("reel.play", False):
            return cands
        if self.current == REEL:
            target = self._target_of.get(REEL) or (reel[0] if reel else None)
            over = self._clock() - self.since >= self.reel_s
            return {**cands, REEL: Candidate(reel_scored(self.cfg.get("reel.score", 40), self.reel_s, over), target)}
        if not reel:
            return cands
        self._target_of[REEL] = reel[0]
        return {**cands, REEL: Candidate(reel_scored(self.cfg.get("reel.score", 40), reel[1]), reel[0])}

    def _long_break_left(self, gid: str, now: float) -> float | None:
        """Seconds until a halftime/intermission now on screen could possibly end on screen
        (shortest measured length, early side of the delay). None if not in one."""
        tl = self.timelines.get(gid)
        if tl is None:
            return None
        start, end = tl.last("stop_start"), tl.last("stop_end")
        if start is None or start.label not in LONG_BREAKS or (end is not None and end.seen >= start.lo):
            return None
        d = self.delays.get(self._target_of.get(gid) or "unknown")
        floor = MIN_BREAK_S.get(tl.league, {}).get(Stoppage(start.label), 0.0)
        return start.lo + floor + d.mu - 2 * d.current_sigma(now) - now

    def _long_break_visit(self, watchable, cands, home: str | None, gate_ok: bool, state, gate_txt: str,
                          now: float) -> Decision | None:
        """Halftime / intermission: 15-19 minutes of ads. Any live game beats that, even a dull one
        or one we can only leave at a whistle — we plan the trip back early enough to find one."""
        if not home or not gate_ok:
            return None
        left = self._long_break_left(home, now)
        if left is None or left < LONG_VISIT_MIN_S:
            return None
        # [EXP-213] a halftime trip has to be worth two switches: below this score, the ads win (0 = any game)
        floor = float(self._lc("decider.dwell.long_visit_min_score", home, 0) or 0)
        options = {gid: x for gid, x in watchable.items() if gid != home and not x.scored.is_break
                   and gid not in self._screen_break and (gid in PSEUDO or x.scored.score >= floor)}
        if self._visit_needs_exit(home, watchable):
            # [return trip] Uncalibrated, a visited game's gate opens only at its own long breaks:
            # a WSH halftime visit to an NBA game stranded us through WSH's 3rd quarter and crunch.
            options = {gid: x for gid, x in options.items() if gid in PSEUDO or self._exit_provable(x, now)}
        if not options:
            return None
        back = self._return_cost(home)

        def watch(g: str) -> float:   # [break visits] what the visit shows after both trips
            buf = RETURN_BUFFER_S if self._exits_often(g) or g in PSEUDO else RETURN_BUFFER_CONTINUOUS_S
            return left - buf - back - self._deadline_cost(self._target_of.get(g))
        options = {g: x for g, x in options.items() if watch(g) >= 60}
        if not options:
            return None
        st = self._cstate(options, now)
        dest = max(options, key=lambda g: (core.window_value(st.options[g], self.core.k, watch(g)),
                                           core.rate(st.options[g], self.core.k)))
        buffer = RETURN_BUFFER_S if self._exits_often(dest) or dest in PSEUDO else RETURN_BUFFER_CONTINUOUS_S
        # Head back early enough to find a clean exit AND finish the trip before the break ends.
        self._home_set(home, now + left - buffer - back, "long-break visit")
        return Decision(now, "switch", dest, self._why(options[dest], f"{_short(cands[home].scored)} at the break → "),
                        state, gate_txt, kind="break_visit")

    def _mine_first(self, s, late_only: bool = False) -> bool:
        """[EXP-214, decider.crunch.mine_first] Your team's game holds the TV: never left by a catch-up return in
        its final period (late_only, unless garbage time; WSH @ DET, Nov 10 2025: a return to a Q1 visit's origin
        at 8:37 left, back at 0:01 of OT), nor for another game's crunch or a big-play chase while it is late and close."""
        if not _config.for_league(self.cfg, "decider.crunch.mine_first", getattr(getattr(s, "league", None), "value", None),
                                  False):
            return False
        if late_only:
            return bool(getattr(s, "late", False) and not s.garbage
                        and s.personal >= self.cfg.get("decider.crunch.hold_personal", 5))
        return self._mine_late(s)

    def _mine_late(self, s) -> bool:
        """Your team (or your bet) in its final period, not decided and still competitive. [EXP-204]
        Outside crunch time, "close" also needs the leader's win probability at or below the league's
        decider.mine_late_max_lead_wp (1.0 = off): ATL up 12 with 9:09 left is not close (Nick, Nov 4)."""
        if not (getattr(s, "late", False) and not s.garbage
                and s.personal >= self.cfg.get("decider.crunch.hold_personal", 5)
                and s.closeness >= self.cfg.get("decider.mine_late_closeness", 0.2)):
            return False
        cap = float(_config.for_league(self.cfg, "decider.mine_late_max_lead_wp",
                                       getattr(getattr(s, "league", None), "value", None), 1.0) or 1.0)
        wp = getattr(s, "lead_wp", None)
        return bool(getattr(s, "crunch", False) or cap >= 1.0 or wp is None or wp <= cap)

    def _sticky_home(self, gid: str, watchable, entry: bool = False) -> bool:
        """A game whose next play you must be back for. entry=True: one worth an urgent (tighter)
        entry, which a power play or a close NHL 3rd alone is not."""
        x = watchable.get(gid)
        if x is None:
            return gid == self.user.focus_game
        s = x.scored
        soft = s.lock == "power play" or _nhl_close_third(s)
        return (gid == self.user.focus_game or s.crunch
                or (s.lock is not None and not (entry and s.lock == "power play"))
                or s.personal >= self.cfg.get("decider.crunch.hold_personal", 5)
                or (not entry and soft))

    def _wait_for_my_tip(self, x: Candidate, now: float) -> str | None:
        """Your team tips soon: don't start on a neutral basketball/hockey game we couldn't leave.
        Uncalibrated, its gate opens only at its own halftime/intermission, so a 7:00 pick held us
        past a 7:30 tip (risk sweep, Nov 2). A discrete-sport or calibrated pick is fine."""
        if self.my_tip_at is None:
            return None
        try:
            tip = self.my_tip_at()
        except Exception:
            return None
        within = float(self.cfg.get("decider.wait_for_my_team_s", 1800))
        if tip is None or not 0 < tip - now <= within:
            return None
        if x.scored.personal >= self.cfg.get("decider.crunch.hold_personal", 5) \
                or x.scored.league not in CONTINUOUS or self._exit_provable(x, now):
            return None
        return f"waiting for your team (tips in {max(1, round((tip - now) / 60))} min)"

    def _visit_needs_exit(self, home: str, watchable) -> bool:
        """Your game, a bet or your focus game on a break: only visit where we can surely get back
        from. (Neutral crunch homes keep the old rule: there a filtered visit became a 'better' switch.)"""
        if not self.cfg.get("decider.sticky_visit_needs_calibration", True):
            return False
        x = watchable.get(home)
        return home == self.user.focus_game or (
            x is not None and x.scored.personal >= self.cfg.get("decider.crunch.hold_personal", 5))

    def _exit_provable(self, x: Candidate, now: float) -> bool:
        """Is this game's delay known well enough that its leave gate opens between plays/at
        whistles? Above decider.uncalibrated_sigma_s it opens only at official (long) breaks."""
        sig = self.delays.get(x.target or "unknown").current_sigma(now)
        return sig <= float(self.cfg.get("decider.uncalibrated_sigma_s", 8))

    def _exits_often(self, gid: str) -> bool:
        """Discrete sports (football, baseball) have a clean gap between every play."""
        tl = self.timelines.get(gid)
        return tl is not None and tl.league not in CONTINUOUS

    def _cost(self, target: str | None) -> float:
        app = (target or "").split(":", 1)[0]
        return float(self.cfg.get(f"decider.switch_cost.{app}", 5))

    def _rank_cost(self, target: str | None) -> float:
        """Ranking cost: the provider's typical switch, or less when the target is already
        playing on the hidden stick (a ~6 s HDMI flip, not a full tune)."""
        cost = self._cost(target)
        return min(cost, self._deadline_cost(target)) if self.deadline_cost is not None else cost

    def _deadline_cost(self, target: str | None) -> float:
        """Conservative cost for this target's present path, separate from ranking cost."""
        if self.deadline_cost is None:
            return self._cost(target)
        cost = float(self.deadline_cost(target))
        # An invalid live estimate is no evidence we can make a timed entry.
        return cost if math.isfinite(cost) and cost >= 0 else float("inf")

    @staticmethod
    def _why(c: Candidate, lead: str) -> str:
        tags = [t for t in c.scored.tags if t not in c.scored.reason]
        tail = f" · {' · '.join(tags)}" if tags else ""
        sep = "" if lead.endswith("→ ") else ": "
        return f"{lead}{sep}{c.scored.reason}{tail}"

    def _commit(self, target: str, kind: str = "", arrived: bool = False) -> None:
        """A switch lands (simulated: the tune's estimated cost is black first) or was confirmed (arrived=True: the
        TV already shows it, so the screen log gets the game once, at now)."""
        now = self._clock()
        self.replay_exit_after = 0.0   # a live switch replaces the old replay picture
        if kind == "return" and self.stack and self.stack[-1] == target:
            # Popped even when we're already on it (a "return" confirmed as "already on it"): left on
            # the stack, the same return was proposed again every tick, 348 times on the e2e night test.
            self.stack.pop()
        elif self.current and self.current != target and kind not in ("return", "home"):
            self.stack.append(self.current)
        if kind == "home" and self.stack and self.stack[-1] == target:
            self.stack.pop()
        if kind == "home" and target == self.home_break_visit:
            self._home_set(None, None, "back home")
        if kind == "user":
            self._home_set(None, self._home_at, "you picked a game")
        if self.current and self.current != target:
            self.jump_left_at[self.current] = now
        self._current = target
        self.since = now
        self.start_fails, self._start_retry_at = 0, 0.0
        self.start_m.to("ready", f"{kind or 'switch'} landed on {target}", now=now)
        # [buffered jump] a landing ends any keep-standby window: the next unknown/gone screen gets a fresh one
        # (a stale window made keep-standby expire at once the next time)
        self.gone_m.to("present", f"{kind or 'switch'} landed on {target}", now=now)
        # [jump mode] holds count why the rotation didn't jump since the screen last changed, whoever changed it
        self.jump_holds = Counter()
        self.history.append((now, target, kind))
        if arrived:
            self._arrival = 0.0
            self.screen_m.to("live", f"{kind or 'switch'} to {target} confirmed on screen", now=now)
            self._screen_log.append((now, target))
            return
        # Black while the tuner works; padded so "did you see it?" errs toward "no" (-> replay).
        tune = self._deadline_cost(None if target is None else self._target_of.get(target)) + TUNE_SLACK_S
        self._arrival = now + tune
        self.screen_m.to("tuning", f"{kind or 'switch'} to {target} (~{tune:.0f}s)", now=now)
        self._screen_log.append((now, None))
        self._screen_log.append((now + tune, target))
