"""The switch decider: a producer that never cuts you off mid-play.

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

import time
import math
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from redzone.decider.screen import (AFTER_PLAY_BUFFER_S, CONTINUOUS, MIN_BREAK_S, PLAY_LEN_S, DelayBook, Event, Gate,
                                    Timeline, break_on_arrival, catchable, final_play_aired, leave_gate,
                                    resume_play)
from redzone.models import League, Stoppage
from redzone.scoring.engine import Scored

GONE_MAX_HOLD_S = 90.0  # [mid-play guard] a game that vanished without a final: don't wait on a dead feed forever
# Basketball/hockey have no between-play gap to prove, so a vanished game (late West Coast games at the
# ESPN scoreboard rollover after midnight) is held longer before leaving blind.
GONE_MAX_HOLD_CONTINUOUS_S = 300.0
REDZONE = "redzone"
# Locks where every snap matters, not just the big ones (scoring/engine.py names them).
CRUNCH_LOCKS = {"final two minutes", "crunch time", "overtime", "empty net"}
UNSEEN_PLAY_LAG_S = 10.0  # a play this recent may not be in the feed yet
# Halftime / intermission visits: return this long before the break could end on screen.
LONG_BREAKS = {"halftime", "intermission"}
LONG_VISIT_MIN_S = 240.0            # only plan a visit if at least this much break is guaranteed
RETURN_BUFFER_S = 60.0              # visiting football/baseball/RedZone: a clean gap is never far away
RETURN_BUFFER_CONTINUOUS_S = 180.0  # visiting basketball/hockey: allow time to reach a whistle
KICKOFF_TO_SNAP_S = 25.0  # kickoff logged -> next scrimmage play logged, at the quickest
# [pacing] Stoppages that usually end before a plain break visit pays off: in the NFL backtest
# the median such visit lasted 20 s (tune away, tune straight back) and they were the biggest
# source of A->B->A flips. Ride them out at home; chases, catch-ups and crunch moves still go.
SHORT_STOPS = {"timeout", "injury", "review"}


def _short(s: Scored) -> str:
    return "your game"


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
    kind: str = ""            # "better", "break_visit", "return", "chase", "must_see", "ended", "start", "replay", "crunch"
    replay: "Missed | None" = None
    want: str | None = None   # the game it wants to switch to while it waits for a safe moment


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
        # The live controller knows whether THIS target is already ready. App-wide
        # average costs are useful for ranking, but cannot promise a deadline.
        # Offline simulations intentionally keep their configured timing model.
        self.deadline_cost: Callable[[str | None], float] | None = None
        # Scheduled start (epoch s) of the next not-yet-started game with your team, if the
        # engine knows one. None = unknown, and the 'wait for your team' rule stays off.
        self.my_tip_at: Callable[[], float | None] | None = None
        self.user = UserState()
        self.current: str | None = None
        self.since: float = 0.0
        self._arrival_until: float = 0.0   # simulated transport work, not a viewing-time quota
        self.stack: deque[str] = deque(maxlen=3)   # where we came from, for "go back"
        self.home_break_visit: str | None = None   # game we left only for its break
        self.home_return_at: float | None = None    # long break: head back at this time at the latest
        self.history: deque[tuple[float, str, str]] = deque(maxlen=50)
        self.frozen_until = 0.0
        self.margin_bump_until = 0.0
        self.errors: deque[float] = deque(maxlen=10)
        self.safe_mode = False
        self.timelines: dict[str, Timeline] = {}
        self.log: deque[Decision] = deque(maxlen=500)
        self._screen_break: set[str] = set()
        # Catch-up replays
        self.queue: list[Missed] = []
        self.done_replays: deque[Missed] = deque(maxlen=200)
        self.replaying: Missed | None = None
        self.replay_until = 0.0
        self.replay_exit_after = 0.0   # retained if interrupted: old footage may still be mid-play
        self.after_replay = False
        self._screen_log: deque[tuple[float, str | None]] = deque(maxlen=400)  # (from, game | "replay" | None=black)
        self._judged: set[tuple[str, str]] = set()
        self._target_of: dict[str, str] = {}
        # Last known candidate per game. A final drops out of the live candidates, but its last
        # plays still air (and stay on the DVR) for a while: keep them judgeable and replayable.
        self._known: dict[str, Candidate] = {}
        # [mid-play guard] state for leaving a game that ended or dropped out of the feed
        self._known_target: dict[str, str] = {}
        self._gone: tuple | None = None   # ((game, arrived at), when it vanished without a final)
        self._last_target = self._known_target   # (crunch-time code's name for the same memory)
        self._proposal_home: tuple | None = None

    # ----- inputs -----------------------------------------------------------

    def timeline(self, game_id: str, league: League) -> Timeline:
        if game_id not in self.timelines:
            self.timelines[game_id] = Timeline(league, clock=self._clock)
        return self.timelines[game_id]

    def note_error(self) -> None:
        now = self._clock()
        self.errors.append(now)
        recent = [t for t in self.errors if now - t < 300]
        if len(recent) >= 3:
            self.safe_mode = True

    def resume(self) -> None:
        self.safe_mode = False
        self.user.paused = False
        self.frozen_until = 0.0
        self.errors.clear()

    def manual_switch(self, game_id: str) -> None:
        """You picked a game by hand: go there and keep hands off for a bit."""
        self.abort_replay()
        self._commit(game_id, kind="user")
        self.user.manual_until = self._clock() + self.cfg.get("decider.manual_pause_s")

    # ----- the decision -----------------------------------------------------

    def decide(self, cands: dict[str, Candidate], redzone: str | None = None, *, commit: bool = True) -> Decision:
        """`redzone` is the RedZone channel's tuner target while it's on the air, else None."""
        now = self._clock()
        self._known.update({gid: x for gid, x in cands.items() if x.target})
        cands = self._with_redzone(cands, redzone)
        self._scan_missed(cands, now)
        if self.replaying:
            if self.safe_mode or self.user.paused or self.on_screen_at(now) != "replay":
                self.abort_replay()
            elif now >= self.replay_until:
                self.replaying.replayed = True
                self.done_replays.append(self.replaying)
                self.replaying = None
                self.after_replay = True
        home_before = (self.home_break_visit, self.home_return_at)
        d = self._decide(cands, now)
        if not commit and d.action in ("switch", "replay"):
            # The simulator can commit synchronously. A real TV must confirm
            # the command first: a proposal is not a watched game or replay.
            self._proposal_home = (d, self.home_break_visit, self.home_return_at)
            self.home_break_visit, self.home_return_at = home_before
            self.log.append(d)
            return d
        if d.action == "switch" and d.target:
            if d.target == self.current and not self.after_replay:
                d = Decision(now, "stay", self.current, "already here", d.state)
            else:
                self._commit(d.target, kind=d.kind)
                self.after_replay = False
        elif d.action == "replay" and d.replay:
            self._start_replay(d.replay, cands, now)
        self.log.append(d)
        return d

    def confirm(self, d: Decision, cands: dict[str, Candidate]) -> None:
        """Commit an observed playback change, with screen time from confirmation."""
        if self._proposal_home and self._proposal_home[0] is d:
            _, self.home_break_visit, self.home_return_at = self._proposal_home
        self._proposal_home = None
        if d.action == "replay" and d.replay:
            self._start_replay(d.replay, cands, self._clock(), confirmed=True)
        elif d.target:
            self.abort_replay()
            self._commit(d.target, kind=d.kind)
            # _commit's future estimate is for offline simulation. The TV
            # already confirmed: don't fabricate an extra tune or watched time.
            self._screen_log.pop()
            self._screen_log.pop()
            self._screen_log.append((self._clock(), d.target))
            self._arrival_until = 0.0
            self.after_replay = False

    def reject(self, d: Decision) -> None:
        if self._proposal_home and self._proposal_home[0] is d:
            self._proposal_home = None

    # ----- catch-up replays ---------------------------------------------------

    def target_of(self, game_id: str | None) -> str | None:
        """Where a game is on, remembered after it ends (its last plays can still be replayed);
        also RedZone's channel while it's a candidate."""
        x = self._known.get(game_id or "")
        return x.target if x else self._target_of.get(game_id or "")

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
        if not self.cfg.get("replay.enabled", True):
            return
        max_age = self.cfg.get("replay.max_age_s", 1800)
        pool = self._replay_pool(cands)
        for gid, tl in self.timelines.items():
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
                m = Missed(gid, e.pid or e.label, e.label, kind, e.seen, value, seen_live=seen)
                if seen:
                    self.done_replays.append(m)  # eligible only as an "instant replay" when all else is dead
                elif self._replayable(x):
                    self.queue.append(m)
        self.queue = [m for m in self.queue if not m.replayed and now - m.t <= max_age and m.game_id in pool]

    def _urgency(self, m: Missed, now: float) -> float:
        """0 while there's time; ramps to 1 as a missed play nears the end of its replay window."""
        urgent = self.cfg.get("replay.urgent_s", 1200)
        if m.seen_live or urgent <= 0:
            return 0.0
        left = self.cfg.get("replay.max_age_s", 1800) - (now - m.t)
        return max(0.0, min(1.0, 1.0 - left / urgent))

    def _replay_value(self, m: Missed, now: float) -> float:
        age = now - m.t
        fresh = 1.0 - 0.5 * min(1.0, age / self.cfg.get("replay.max_age_s", 1800))
        # Last chance to see it: it climbs past dull live play.
        return m.value * fresh * (0.5 if m.seen_live else 1.0) + URGENT_BONUS * self._urgency(m, now)

    def _replay_cost(self, m: Missed, cands) -> float:
        x = self._replay_pool(cands).get(m.game_id)
        return self._deadline_cost(x.target if x else None) + self.cfg.get("replay.seek_s", 6) + self.cfg.get("replay.clip_s", 15)

    def _best_replay(self, cands, now: float, budget: float | None, allow_seen: bool,
                     top: float | None = None, margin: float | None = None,
                     exclude: str | None = None) -> Missed | None:
        """The replay to show next. With top: only one worth leaving live play scored top for.
        exclude: a game not to replay (e.g. the crunch game you're about to go back to)."""
        games = self._replay_pool(cands)
        pool = [m for m in self.queue if m.game_id != exclude and self._replayable(games.get(m.game_id))]
        if allow_seen and not pool:
            recent = now - self.cfg.get("replay.instant_window_s", 900)
            done_ids = {(d.game_id, d.pid) for d in self.done_replays if d.replayed}
            pool = [m for m in self.done_replays if m.seen_live and m.t >= recent
                    and (m.game_id, m.pid) not in done_ids and m.game_id in cands and m.game_id != exclude
                    and self._replayable(cands[m.game_id])]
        if budget is not None:
            pool = [m for m in pool if self._replay_cost(m, cands) <= budget]
        if top is not None:
            margin = self.cfg.get("decider.switch_margin") if margin is None else margin
            pool = [m for m in pool if self._replay_value(m, now) >= top + margin]

        def rank(m: Missed) -> tuple:
            # Earliest deadline first among last chances (a fresh TD can wait; they can't), else value.
            return (1, -m.t) if self._urgency(m, now) > 0 else (0, self._replay_value(m, now))
        return max(pool, key=rank, default=None)

    def _break_remaining(self, gid: str, cands, now: float) -> float:
        """Conservative break budget, never a typical ad length treated as a deadline."""
        tl = self.timelines.get(gid)
        x = cands.get(gid)
        if tl is None or x is None:
            return 0.0
        d = self.delays.get(x.target or "unknown")
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
        self.replaying = None
        self.replay_until = 0.0
        self.after_replay = True   # the current picture is still old footage until a live tune
        self._screen_log.append((self._clock(), None))

    def _start_replay(self, m: Missed, cands, now: float, *, confirmed: bool = False) -> None:
        self.abort_replay()
        if m in self.queue:
            self.queue.remove(m)
        self.replaying = m
        self.after_replay = False
        # A real controller has already tuned, sought, and begun playback at
        # confirmation. Counting preparation again needlessly holds old footage.
        duration = self.cfg.get("replay.clip_s", 15) if confirmed else self._replay_cost(m, cands)
        self.replay_until = now + duration + self._replay_overrun(m, cands, now)
        self.replay_exit_after = self.replay_until
        if confirmed:
            self._arrival_until = 0.0
        if self.current and self.current != m.game_id and self.current != self.home_break_visit:
            self.stack.append(self.current)
        self.current = m.game_id
        self.since = now
        self.history.append((now, m.game_id, "replay"))
        self._screen_log.append((now, "replay"))

    def _decide(self, cands: dict[str, Candidate], now: float) -> Decision:
        c = self.cfg
        u = self.user
        live = {gid: x for gid, x in cands.items()
                if x.scored.raw > 0 or x.scored.moment > 0 or x.scored.is_break or gid == REDZONE}
        cur = cands.get(self.current) if self.current else None
        watchable = {gid: x for gid, x in live.items()
                     if x.target and not x.stale and gid not in u.muted}

        if self.safe_mode:
            return Decision(now, "stay", self.current, "safe mode: too many errors — tap Resume", State.SAFE)
        if u.paused:
            return Decision(now, "stay", self.current, "auto-switching paused", State.PAUSED)
        if u.locked_game and u.locked_game in cands:
            if self.current != u.locked_game:
                return Decision(now, "switch", u.locked_game, "you locked this game", State.PAUSED, kind="user")
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
            self.home_break_visit = home = None   # what we're on now is clearly better: stay
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
                    self.home_break_visit = focus
                    return self._replay_decision(rp, state, gate_txt, now)
                if dest:
                    self.home_break_visit = focus
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
        visit_home = self.current if s_cur.score >= c.get("decider.break_destination_min") else None
        if on_break:
            if chase:
                self.home_break_visit = visit_home
                return self._chase_decision(chase, cands, state, gate_txt, now)
            planned = self._long_break_visit(watchable, cands, self.current, gate_ok, state, gate_txt, now)
            if planned:
                return planned
            dest = self._break_destination(watchable, exclude=self.current)
            rp = self._replay_option(cands, dest, now)
            if rp:
                self.home_break_visit = visit_home
                return self._replay_decision(rp, state, gate_txt, now)
            if dest and gate_ok and not self._short_stop(self.current, s_cur):
                self.home_break_visit = visit_home
                return Decision(now, "switch", dest, self._why(cands[dest], f"{_short(s_cur)} on a break → "),
                                state, gate_txt, kind="break_visit")
        elif self._routine_tail(cur, gate, watchable, now):
            self.home_break_visit = visit_home
            return Decision(now, "switch", REDZONE, "break's ending on a routine snap → NFL RedZone",
                            state, gate_txt, kind="break_visit")

        # Chase: a big play just happened somewhere and hasn't aired yet. Go now, see it live.
        if chase:
            return self._chase_decision(chase, cands, state, gate_txt, now)

        best = self._best(watchable, exclude=self.current)
        if best is None:
            return Decision(now, "stay", self.current, s_cur.reason, state, gate_txt)
        b = cands[best]
        margin = c.get("decider.switch_margin")
        cost = self._rank_cost(b.target)
        lead = b.scored.score - s_cur.score

        # Catch-up between plays: a missed big play is worth more than this ordinary moment.
        if gate_ok and not s_cur.lock:
            rp = self._best_replay(cands, now, None, False,
                                   top=max(self._vs_replay(self.current, cands), self._vs_replay(best, cands)),
                                   margin=margin)
            if rp:
                return self._replay_decision(rp, state, gate_txt, now)

        # Go back to where we were if this moment is over and that game is about as good.
        if self.stack and not s_cur.lock and gate_ok:
            prev = self.stack[-1]
            if prev in watchable and not cands[prev].scored.is_break and prev not in self._screen_break:
                if cands[prev].scored.score >= s_cur.score - c.get("decider.return_tolerance") and \
                        cands[prev].scored.score >= b.scored.score - margin:
                    return Decision(now, "switch", prev, self._why(cands[prev], "moment's over, back to"),
                                    state, gate_txt, kind="return")

        needed = margin + cost
        if s_cur.lock:
            needed = max(needed, c.get("decider.locked_override_lead"))
        if lead < needed:
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
        floor = -1.0 if self.current == REDZONE else s_cur.score - self.cfg.get("decider.chase_max_deficit")
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
        if self.current is None or (live and live.lock):
            return None
        rp = self._best_replay(cands, now, None, False, top=self._vs_replay(best, cands) if best else 0.0)
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
        return 0.0 if gid == REDZONE else cands[gid].scored.score

    def _replay_decision(self, rp: Missed, state, gate_txt: str, now: float) -> Decision:
        name = "instant replay" if rp.seen_live else "catch-up"
        return Decision(now, "replay", rp.game_id, f"{name}: {rp.label}", State.REPLAY, gate_txt,
                        kind="replay", replay=rp)

    def _after_replay(self, cands, watchable, now: float) -> Decision:
        """A replay just finished; we're on old footage, so go somewhere live (or replay more)."""
        home = self.home_break_visit
        hot = self._crunch_pick(watchable)
        if hot and (home not in watchable or not self._crunch_outranks(cands[home].scored, cands[hot].scored)):
            self.home_break_visit = home = hot   # leaving a replay needs no gap: get to crunch time
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
            rp = self._best_replay(cands, now, None, allow_seen=True)
            if rp:
                return self._replay_decision(rp, State.REPLAY, "everything's on a break", now)
            best = max(watchable, key=lambda g: watchable[g].scored.score, default=None)
            if best is None:
                return Decision(now, "stay", self.current, "nothing live", State.IDLE)
        live = cands[best].scored
        rp = None if live.lock else self._best_replay(cands, now, None, False, top=self._vs_replay(best, cands))
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

    @staticmethod
    def _crunch_rank(s: Scored) -> tuple:
        """When crunch times overlap: your teams and bets first, then the closest score, then the moment."""
        return (round(s.personal), s.closeness, s.score)

    def _crunch_pick(self, watchable: dict[str, Candidate], exclude: str | None = None) -> str | None:
        """The crunch game to be on. Breaks included: a stoppage is the time to get there."""
        hot = [(self._crunch_rank(x.scored), gid) for gid, x in watchable.items()
               if gid != exclude and x.scored.crunch]
        return max(hot)[1] if hot else None

    def _crunch_outranks(self, a: Scored, b: Scored) -> bool:
        """Is crunch time on a worth more than b (crunch or not)?"""
        return a.crunch and (not b.crunch or self._crunch_rank(a) > self._crunch_rank(b))

    def _crunch_go(self, cands, pick: str, s_cur: Scored, on_break: bool, now: float) -> bool:
        """Should we leave what's on for crunch time at `pick` (at the next safe gap)?"""
        if not s_cur.crunch:
            return True   # get there before its first crunch snap
        p = cands[pick].scored
        dp = p.personal - s_cur.personal
        if abs(dp) >= self.cfg.get("decider.crunch.personal_lead", 5):
            return dp > 0   # your team (then your bet) wins an overlap outright
        # Two crunches, same tier: hop only while this one is stopped and that one is live, so its
        # next snap airs first. Never for the last few seconds of a stoppage.
        return on_break and not p.is_break and pick not in self._screen_break and \
            self._break_remaining(self.current, cands, now) >= self.cfg.get("decider.crunch.hop_min_break_s", 20)

    def _crunch_holds(self, s_cur: Scored) -> bool:
        """Stay through this crunch game's stoppages? Always for one you have a stake in (your
        team, your bet). For a neutral one only through the stoppages listed: by default the
        two-minute warning, the way in; every stoppage held is commercials on screen."""
        if s_cur.personal >= self.cfg.get("decider.crunch.hold_personal", 5):
            return True
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
                self.home_break_visit = pick if rp else None
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
        if on_break and gate_ok:
            rp = self._crunch_replay(cands, now, self.current)
            if rp:
                self.home_break_visit = self.current
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
        return self._best_replay(cands, now, self._next_snap_s(gid, cands, now) - back, allow_seen=True, exclude=gid)

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

    def _with_redzone(self, cands: dict[str, Candidate], target: str | None) -> dict[str, Candidate]:
        """Add RedZone as a candidate while it's on the air and you have it turned on."""
        if not target or not self.cfg.get("decider.redzone_channel", False):
            return cands
        return {**cands, REDZONE: Candidate(redzone_scored(self.cfg.get("decider.redzone_score", 45)), target)}

    def _gate(self, cur: Candidate, now: float) -> Gate | None:
        """Can we leave what's on screen? RedZone is a whip-around: there's no play of ours to cut."""
        if self.current == REDZONE:
            return Gate(True, "on RedZone: nothing to cut out of")
        tl = self.timelines.get(self.current)
        if tl is None:
            return None
        return leave_gate(tl, self.delays.get(cur.target or "unknown"), now,
                          self.cfg.get("decider.uncalibrated_sigma_s"))

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
        """[pacing] Is this football game in a kind of break that ends before a visit pays off?
        Not in crunch time: there a timeout is the moment to check the other close game."""
        tl = self.timelines.get(gid) if gid else None
        if tl is None or s.league != League.NFL or s.crunch or s.lock in CRUNCH_LOCKS:
            return False
        start, end = tl.last("stop_start"), tl.last("stop_end")
        return start is not None and start.label in SHORT_STOPS and (end is None or end.seen < start.lo)

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
        if x.scored.league != League.NFL:
            return True
        tl = self.timelines.get(gid)
        snap = resume_play(tl) if tl else None
        return bool(x.scored.lock in CRUNCH_LOCKS or (snap and snap.big))

    def _best(self, watchable: dict[str, Candidate], exclude: str | None) -> str | None:
        options = [(x.scored.score, gid) for gid, x in watchable.items()
                   if gid != exclude and not x.scored.is_break and gid not in self._screen_break]
        return max(options)[1] if options else None

    def _break_destination(self, watchable, exclude) -> str | None:
        """Where to spend the home game's break. If home is a game you can't afford to miss the
        resume of (your team, a bet, your focus game, crunch time), only go somewhere you can
        ALWAYS leave in time: basketball/hockey only have clean exits at whistles, and a visit
        there stranded us for 3 minutes of a Wizards crunch (backtest, Oct 26)."""
        options = watchable
        if exclude and self._sticky_home(exclude, watchable):
            now = self._clock()
            strict = self._visit_needs_exit(exclude, watchable)
            options = {gid: x for gid, x in watchable.items()
                       if gid == REDZONE or (self._exits_often(gid) and (not strict or self._exit_provable(x, now)))}
        best = self._best(options, exclude)
        if best and options[best].scored.score >= self.cfg.get("decider.break_destination_min"):
            return best
        # RedZone never shows a commercial: any break is better spent there than on an ad.
        return REDZONE if REDZONE in watchable and exclude != REDZONE else None

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
        options = {gid: x for gid, x in watchable.items() if gid != home and not x.scored.is_break
                   and gid not in self._screen_break}
        if self._visit_needs_exit(home, watchable):
            # [return trip] Uncalibrated, a visited game's gate opens only at its own long breaks:
            # a WSH halftime visit to an NBA game stranded us through WSH's 3rd quarter and crunch.
            options = {gid: x for gid, x in options.items() if gid == REDZONE or self._exit_provable(x, now)}
        if not options:
            return None
        dest = max(options, key=lambda g: options[g].scored.score)
        buffer = RETURN_BUFFER_S if self._exits_often(dest) or dest == REDZONE else RETURN_BUFFER_CONTINUOUS_S
        if left < buffer + 60:
            return None
        self.home_break_visit = home
        self.home_return_at = now + left - buffer
        return Decision(now, "switch", dest, self._why(options[dest], f"{_short(cands[home].scored)} at the break → "),
                        state, gate_txt, kind="break_visit")

    def _sticky_home(self, gid: str, watchable) -> bool:
        """A game whose next play you must be back for."""
        x = watchable.get(gid)
        if x is None:
            return gid == self.user.focus_game
        s = x.scored
        return (gid == self.user.focus_game or s.crunch or s.lock is not None
                or s.personal >= self.cfg.get("decider.crunch.hold_personal", 5))

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

    def _commit(self, target: str, kind: str = "") -> None:
        now = self._clock()
        self.replay_exit_after = 0.0   # a live switch replaces the old replay picture
        if self.current and self.current != target:
            if kind == "return" and self.stack and self.stack[-1] == target:
                self.stack.pop()
            elif kind not in ("return", "home"):
                self.stack.append(self.current)
        if kind == "home" and self.stack and self.stack[-1] == target:
            self.stack.pop()
        if kind == "home" and target == self.home_break_visit:
            self.home_break_visit = None
            self.home_return_at = None
        if kind == "user":
            self.home_break_visit = None
        self.current = target
        self.since = now
        self.history.append((now, target, kind))
        # Black while the tuner works; padded so "did you see it?" errs toward "no" (-> replay).
        tune = self._deadline_cost(None if target is None else self._target_of.get(target)) + TUNE_SLACK_S
        self._arrival_until = now + tune
        self._screen_log.append((now, None))
        self._screen_log.append((now + tune, target))
