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
  3. Stay at least min_dwell_s; at most N switches per 5 minutes.
  4. Only switch for a clear margin that also pays for the switch's dead air.

NFL RedZone (Sunday afternoons, with Sunday Ticket) is a pseudo-game "redzone": commercial-free,
so it's the place to wait out a break when nothing better is live, and home base when nothing
is close. It has no plays of its own to cut out of, so leaving it needs no gate. Plays it shows
are never counted as "seen": we can't tell which ones it showed.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum

from redzone.decider.screen import (AFTER_PLAY_BUFFER_S, PLAY_LEN_S, DelayBook, Event, Gate, Timeline,
                                    break_on_arrival, catchable, leave_gate, resume_play)
from redzone.models import League
from redzone.scoring.engine import Scored

GONE_MAX_HOLD_S = 90.0  # [mid-play guard] a game that vanished without a final: don't wait on a dead feed forever
REDZONE = "redzone"
# Locks where every snap matters, not just the big ones (scoring/engine.py names them).
CRUNCH_LOCKS = {"final two minutes", "crunch time", "overtime", "empty net"}


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
    kind: str = ""            # "better", "break_visit", "return", "chase", "must_see", "ended", "start", "replay"
    replay: "Missed | None" = None


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
        self.user = UserState()
        self.current: str | None = None
        self.since: float = 0.0
        self.stack: deque[str] = deque(maxlen=3)   # where we came from, for "go back"
        self.home_break_visit: str | None = None   # game we left only for its break
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
        self._commit(game_id, kind="user")
        self.user.manual_until = self._clock() + self.cfg.get("decider.manual_pause_s")

    # ----- the decision -----------------------------------------------------

    def decide(self, cands: dict[str, Candidate], redzone: str | None = None) -> Decision:
        """`redzone` is the RedZone channel's tuner target while it's on the air, else None."""
        now = self._clock()
        self._known.update({gid: x for gid, x in cands.items() if x.target})
        cands = self._with_redzone(cands, redzone)
        self._scan_missed(cands, now)
        if self.replaying and now >= self.replay_until:
            self.done_replays.append(self.replaying)
            self.replaying = None
            self.after_replay = True
        d = self._decide(cands, now)
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
        return self._cost(x.target if x else None) + self.cfg.get("replay.seek_s", 6) + self.cfg.get("replay.clip_s", 15)

    def _best_replay(self, cands, now: float, budget: float | None, allow_seen: bool,
                     top: float | None = None, margin: float | None = None) -> Missed | None:
        """The replay to show next. With top: only one worth leaving live play scored top for."""
        games = self._replay_pool(cands)
        pool = [m for m in self.queue if self._replayable(games.get(m.game_id))]
        if allow_seen and not pool:
            recent = now - self.cfg.get("replay.instant_window_s", 900)
            done_ids = {(d.game_id, d.pid) for d in self.done_replays if d.replayed}
            pool = [m for m in self.done_replays if m.seen_live and m.t >= recent
                    and (m.game_id, m.pid) not in done_ids and m.game_id in cands
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
        """Seconds of break left on screen for this game (estimate)."""
        tl = self.timelines.get(gid)
        x = cands.get(gid)
        if tl is None or x is None:
            return 0.0
        d = self.delays.get(x.target or "unknown")
        start, end = tl.last("stop_start"), tl.last("stop_end")
        if start is None:
            return 0.0
        if end is not None and end.seen >= start.seen:
            return end.seen + d.mu - 2 * d.current_sigma(now) - now
        typical = self.cfg.get(f"break_lengths_s.{tl.league.value}.{start.label}", 120)
        return start.seen + d.mu + typical - now

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

    def _start_replay(self, m: Missed, cands, now: float) -> None:
        m.replayed = True
        if m in self.queue:
            self.queue.remove(m)
        self.replaying = m
        self.replay_until = now + self._replay_cost(m, cands) + self._replay_overrun(m, cands, now)
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
        if now < self.frozen_until:
            return Decision(now, "stay", self.current, "holding still: it was flip-flopping", State.FROZEN)
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
        # games, 2 NFL kneel-downs). Stay until the last play has aired, then move.
        if cur is None or self.current not in live or self.current in u.muted:
            # [mid-play guard] ...but not while its last play is still on your screen (see _gone_hold).
            hold = self._gone_hold(cur, now)
            if hold:
                return Decision(now, "stay", self.current, hold, State.WATCHING, hold)
            best = self._best(watchable, exclude=None)
            catch_up = self._catch_up(cands, now, best, "game over")
            if catch_up:
                return catch_up
            if best is None:
                return Decision(now, "stay", self.current, "nothing live", State.IDLE)
            kind = "start" if self.current is None else "ended"
            return Decision(now, "switch", best, self._why(cands[best], "best game on"), State.WATCHING, kind=kind)

        s_cur = cur.scored
        gate = self._gate(cur, now)
        gate_ok = bool(gate and gate.safe)
        gate_txt = gate.why if gate else "no timeline"
        on_break = bool(gate and gate.on_screen_break)
        state = State.BREAK if on_break else State.LOCKED if s_cur.lock else State.WATCHING

        # --- returning home after a break visit (live budget) ---
        home = self.home_break_visit
        focus_on = bool(u.focus_game and u.focus_mode != FocusMode.AUTOPILOT)
        if home and not focus_on and home in watchable and \
                cands[home].scored.score + c.get("decider.switch_margin") < s_cur.score:
            self.home_break_visit = home = None   # what we're on now is clearly better: stay
        if home and home != self.current and home in watchable:
            if self._home_due(home, cands[home], now):
                if gate_ok:
                    return Decision(now, "switch", home, "back to your game before the next play",
                                    state, gate_txt, kind="home")
                return Decision(now, "stay", self.current, f"heading back to your game at the next gap ({gate_txt})",
                                state, gate_txt)

        # --- focus modes ---
        focus = u.focus_game if u.focus_game in cands else None
        if focus and u.focus_mode != FocusMode.AUTOPILOT:
            if self.current != focus:
                if not cands[focus].scored.is_break and gate_ok and not s_cur.lock:
                    return Decision(now, "switch", focus, "back to your focus game", state, gate_txt, kind="home")
                return Decision(now, "stay", self.current, "away from focus game until this moment ends",
                                state, gate_txt)
            if on_break:
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
        chase = self._chase(watchable, s_cur, now) if gate_ok and not s_cur.lock and self._rate_ok(now) else None
        visit_home = self.current if s_cur.score >= c.get("decider.break_destination_min") else None
        if on_break:
            if chase:
                self.home_break_visit = visit_home
                return self._chase_decision(chase, cands, state, gate_txt, now)
            dest = self._break_destination(watchable, exclude=self.current)
            rp = self._replay_option(cands, dest, now)
            if rp:
                self.home_break_visit = visit_home
                return self._replay_decision(rp, state, gate_txt, now)
            if dest and gate_ok and self._rate_ok(now):
                self.home_break_visit = visit_home
                return Decision(now, "switch", dest, self._why(cands[dest], f"{_short(s_cur)} on a break → "),
                                state, gate_txt, kind="break_visit")
        elif self._routine_tail(cur, gate, watchable, now) and self._rate_ok(now):
            self.home_break_visit = visit_home
            return Decision(now, "switch", REDZONE, "break's ending on a routine snap → NFL RedZone",
                            state, gate_txt, kind="break_visit")

        # Chase: a big play just happened somewhere and hasn't aired yet. Go now, see it live.
        settled = self.current == REDZONE or now - self.since >= c.get("decider.chase_min_dwell_s")
        if chase and settled:
            return self._chase_decision(chase, cands, state, gate_txt, now)

        best = self._best(watchable, exclude=self.current)
        if best is None:
            return Decision(now, "stay", self.current, s_cur.reason, state, gate_txt)
        b = cands[best]
        dwell_ok = now - self.since >= c.get("decider.min_dwell_s")
        margin = c.get("decider.switch_margin") + (5 if now < self.margin_bump_until else 0)
        cost = self._cost(b.target)
        lead = b.scored.score - s_cur.score

        # Catch-up between plays: a missed big play is worth more than this ordinary moment.
        if gate_ok and not s_cur.lock and dwell_ok and self._rate_ok(now):
            rp = self._best_replay(cands, now, None, False,
                                   top=max(self._vs_replay(self.current, cands), self._vs_replay(best, cands)),
                                   margin=margin)
            if rp:
                return self._replay_decision(rp, state, gate_txt, now)

        # Go back to where we were if this moment is over and that game is about as good.
        if self.stack and not s_cur.lock and gate_ok and dwell_ok:
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
                            state, gate_txt)
        if not dwell_ok:
            return Decision(now, "stay", self.current, "just got here — giving it a moment", state, gate_txt)
        if not self._rate_ok(now):
            return Decision(now, "stay", self.current, "switch limit — slowing down", state, gate_txt)
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
            ev = catchable(self.timelines[gid], self.delays.get(x.target), now, tune_s=self._cost(x.target) + 3)
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
        back_cost = self._cost(self._target_of.get(self.current)) + 6
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
        if home and home in watchable:
            remaining = self._break_remaining(home, cands, now) - self._cost(self._target_of.get(home)) - 6
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
            if now - self._gone[1] >= GONE_MAX_HOLD_S:
                return None
            return f"lost this game's data — leaving at the next safe gap ({gate.why})"
        return f"leaving at the next gap ({gate.why})"

    # ----- helpers ----------------------------------------------------------

    def _chase_any(self, watchable, now: float, exclude: str | None, floor: float) -> tuple[str, Event] | None:
        """The best game (scoring at least floor) with a big play in the data that hasn't aired yet."""
        chases = []
        for gid, x in watchable.items():
            # Don't skip games "in a break": the play that caused the break hasn't aired yet.
            if gid == exclude or gid not in self.timelines:
                continue
            ev = catchable(self.timelines[gid], self.delays.get(x.target), now, tune_s=self._cost(x.target) + 3)
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
                dead = break_on_arrival(gtl, d, now, now + self._cost(x.target))
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
        if not resumed:
            return not x.scored.is_break
        hd = self.delays.get(x.target or "unknown")
        if self._must_see_resume(home, x):
            return now >= end_ev.seen + hd.mu - 2 * hd.current_sigma(now) - self._cost(x.target) - 6
        return not break_on_arrival(htl, hd, now, now + self._cost(x.target))

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
        best = self._best(watchable, exclude)
        if best and watchable[best].scored.score >= self.cfg.get("decider.break_destination_min"):
            return best
        # RedZone never shows a commercial: any break is better spent there than on an ad.
        return REDZONE if REDZONE in watchable and exclude != REDZONE else None

    def _cost(self, target: str | None) -> float:
        app = (target or "").split(":", 1)[0]
        return float(self.cfg.get(f"decider.switch_cost.{app}", 5))

    def _rate_ok(self, now: float) -> bool:
        if self.history and now - self.history[-1][0] < self.cfg.get("decider.min_gap_s"):
            return False
        recent = [t for t, _, k in self.history if now - t < 300 and k not in ("break_visit", "home", "replay", "live")]
        return len(recent) < self.cfg.get("decider.max_switches_per_5min")

    @staticmethod
    def _why(c: Candidate, lead: str) -> str:
        tags = [t for t in c.scored.tags if t not in c.scored.reason]
        tail = f" · {' · '.join(tags)}" if tags else ""
        sep = "" if lead.endswith("→ ") else ": "
        return f"{lead}{sep}{c.scored.reason}{tail}"

    def _commit(self, target: str, kind: str = "") -> None:
        now = self._clock()
        if self.current and self.current != target:
            if kind == "return" and self.stack and self.stack[-1] == target:
                self.stack.pop()
            elif kind not in ("return", "home"):
                self.stack.append(self.current)
        if kind == "home" and self.stack and self.stack[-1] == target:
            self.stack.pop()
        if kind == "home" and target == self.home_break_visit:
            self.home_break_visit = None
        if kind == "user":
            self.home_break_visit = None
        self.current = target
        self.since = now
        self.history.append((now, target, kind))
        # Black while the tuner works; padded so "did you see it?" errs toward "no" (-> replay).
        tune = self._cost(None if target is None else self._target_of.get(target)) + TUNE_SLACK_S
        self._screen_log.append((now, None))
        self._screen_log.append((now + tune, target))
        self._check_ping_pong(now)

    def _check_ping_pong(self, now: float) -> None:
        """A→B→A twice within 3 minutes means we're flip-flopping: freeze."""
        # Break visits and trips home are A->B->A on purpose; only count discretionary switches.
        h = [g for t, g, k in self.history if now - t < 180 and k not in ("break_visit", "home", "replay", "live")]
        flips = sum(1 for i in range(2, len(h)) if h[i] == h[i - 2] and h[i] != h[i - 1])
        if flips >= 2:
            self.frozen_until = now + self.cfg.get("decider.ping_pong_freeze_s")
            self.margin_bump_until = now + 1800
