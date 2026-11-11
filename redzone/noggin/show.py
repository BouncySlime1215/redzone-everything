"""The show layer over the decider: the thread ledger's hold and cut, added to the chain without changing it.

Why: D-ADDICT mechanic M1 (closure-first hold and cut, docs/agents/context/2026-11-09-addicting-show.md section 3.2). The
decider's own chain keeps Rule #1, the landing rules and the arbiter's floor. This subclass only (1) gives each live game
its open question, (2) adds a capped bonus to a game whose question is about to close, and (3) turns an optional cut into a
stay while the question on screen is still open. Nothing here can create an entry: a hold is a stay; a bonus changes which
game the chain prefers, and the chain still checks every landing.

Flags (default_config.yaml, both default false): `threads.enabled` runs the ledger; `threads.closure_first` lets it act.
Flag off, decide and _safe are the parent's own calls: decisions are hash-identical (scripts/thread_replay.py).

`threads.arrival.enabled` (also default false, needs `threads.enabled`) ranks games by how soon their question is answered
on the picture and how much it matters (redzone/moments/arrival.py), so the show gets there before the payoff. Points only,
never above a must-show, a crunch or your-team game; the chain still checks every landing and every leave.
"""
from __future__ import annotations

import dataclasses as dc
import time
from collections import Counter
from collections.abc import Mapping

from redzone.decider.decider import CRUNCH_LOCKS, PSEUDO, Candidate, Decider, Decision
from redzone.models import GameState
from redzone.moments import arrival as A
from redzone.moments import threads as T


class ShowDecider(Decider):
    """A Decider that also keeps the thread ledger. The caller hands it each tick's game states in `show_states`."""

    def __init__(self, cfg, delays, clock=time.time):
        super().__init__(cfg, delays, clock=clock)
        self.show_states: Mapping[str, GameState] = {}
        self.threads = T.Ledger()
        self.thread_holds: Counter = Counter()
        self.arrival_book = A.Book()
        self.arrival_cuts: Counter = Counter()
        self.arrival_log: list[tuple[float, str, str]] = []   # (when, game, question's kind) for scripts/thread_replay.py
        self.arrival_lifts: dict[str, str] = {}      # this tick: the games lifted by arrival points -> their question's kind
        self.session_end_at: float | None = None     # The Close (phone.commit, not built yet): past it nothing here acts

    def _threads_on(self) -> bool:
        return bool(self.cfg.get("threads.enabled", False))

    def _threads_params(self) -> T.Params:
        return T.Params.from_config(self.cfg.get)

    def _arrival_on(self) -> bool:
        return self._threads_on() and bool(self.cfg.get("threads.arrival.enabled", False))

    def _past_end(self, now: float) -> bool:
        return self.session_end_at is not None and now >= self.session_end_at

    def _air_lag_s(self, p: T.Params) -> float:
        """How long the picture runs behind the feed on the game on screen: the answer the feed has is still to air."""
        cur = self._known.get(self.current or "")
        mu = self.delays.get(cur.target if cur is not None and cur.target else "unknown").mu
        return max(0.0, mu - p.feed_lag_s)

    def decide(self, cands: dict[str, Candidate], redzone: str | None = None, *, commit: bool = True,
               reel: tuple[str, float] | None = None) -> Decision:
        if self._threads_on():
            now, p = self._clock(), self._threads_params()
            before = dict(self.threads.open) if self._arrival_on() else None
            self.threads.tick(self.show_states, self.current, now, p, self._air_lag_s(p))
            if before is not None:
                self.arrival_book.tick(before, self.threads, self.show_states, now)
                self.arrival_lifts = {}
            if not self._past_end(now):
                if before is not None:
                    cands = self._with_arrival_scores(cands, now, p, A.Params.from_config(self.cfg.get))
                elif p.closure_first:
                    cands = self._with_thread_scores(cands, now, p)
        return super().decide(cands, redzone, commit=commit, reel=reel)

    def _with_thread_scores(self, cands: dict[str, Candidate], now: float, p: T.Params) -> dict[str, Candidate]:
        """A copy of the candidates: a game with a thread about to close reads higher; the game on screen just after its
        answer aired reads lower. Scores only, and only when the game is not crunch or high leverage for the fade."""
        out = {}
        for gid, x in cands.items():
            s, delta = x.scored, 0.0
            if gid == self.current:
                if not (s.crunch or s.crunch_now or s.high_leverage or s.lock in CRUNCH_LOCKS or s.mine):
                    delta = -T.fade_after_answer(self.threads, gid, now, p)
            else:
                delta = T.closure_bonus(self.threads, gid, now, p)
            out[gid] = dc.replace(x, scored=dc.replace(s, score=s.score + delta)) if delta else x
        return out

    def _big(self, s) -> bool:
        return bool(s.crunch or s.crunch_now or s.high_leverage or s.mine or s.lock in CRUNCH_LOCKS)

    def _air_of(self, target: str | None, feed_lag_s: float) -> float:
        """How far this target's picture runs behind the feed: the seconds the show has before the feed's answer airs."""
        return max(0.0, self.delays.get(target).mu - feed_lag_s) if target else 0.0

    def _tune_of(self, target: str | None) -> float:
        if self.entry_cost is not None:
            return float(self.entry_cost(target))
        return float(self._deadline_cost(target)) if target else 0.0

    def _with_arrival_scores(self, cands: dict[str, Candidate], now: float, p: T.Params, ap: A.Params) -> dict[str, Candidate]:
        """A copy of the candidates: a game reads higher while its question is about to be answered on its picture. Scores
        only, and capped: with a must-show owed elsewhere nothing is added, and no game is lifted past a must-show, a crunch,
        a high-leverage or a your-team game, or to the score that would make it a must-see by itself."""
        musts = {m.game_id for m in self._arbiter_musts(cands, now, self._arbiter_params())}
        roof = float(self.cfg.get("decider.must_see_score", 92)) - 1.0
        out = {}
        for gid, x in cands.items():
            g = self.show_states.get(gid)
            s, delta = x.scored, 0.0
            if gid not in PSEUDO and g is not None and x.target and not (musts - {gid}):
                pts, t = A.arrival_points(self.arrival_book, self.threads, g, now, air_s=self._air_of(x.target, p.feed_lag_s),
                                          tune_s=self._tune_of(x.target), on_screen=gid == self.current, params=ap)
                ceiling = min([roof] + [o.scored.score - 1.0 for h, o in cands.items() if h != gid and h not in PSEUDO
                                        and (h in musts or self._big(o.scored))])
                delta = max(0.0, min(pts, ceiling - s.score))
                if delta and t is not None and gid != self.current:
                    self.arrival_lifts[gid] = t.kind
            if p.closure_first and gid == self.current and not self._big(s):
                delta -= T.fade_after_answer(self.threads, gid, now, p)
            out[gid] = dc.replace(x, scored=dc.replace(s, score=s.score + delta)) if delta else x
        return out

    def _safe(self, d: Decision, cands, now: float, home_before) -> Decision:
        d = self._safe_threads(d, cands, now, home_before)
        d = super()._safe(d, cands, now, home_before)
        if self._arrival_on() and d.action == "switch" and d.target in self.arrival_lifts:
            self.arrival_cuts[self.arrival_lifts[d.target]] += 1
            self.arrival_log.append((now, d.target, self.arrival_lifts[d.target]))
        return d

    def _safe_threads(self, d: Decision, cands, now: float, home_before) -> Decision:
        if self._threads_on() and d.action == "switch":
            p = self._threads_params()
            if p.closure_first and not self._past_end(now):
                held = self._thread_hold(d, cands, now, p)
                if held is not None:
                    self._home_restore(home_before, f"thread hold: {held}")
                    d = Decision(now, "stay", self.current, f"thread hold: {held} ({d.reason})", d.state, d.gate, want=d.target)
        return d

    def _thread_hold(self, d: Decision, cands, now: float, p: T.Params) -> str | None:
        """The kind the proposed cut waits on, or None. Only an optional cut to a real game, with no must-show or crunch owed."""
        gid = self.current
        cur = cands.get(gid or "")
        if cur is None or gid in PSEUDO or d.target in PSEUDO or d.target == gid:
            return None
        musts = self._arbiter_musts(cands, now, self._arbiter_params())
        to = cands.get(d.target or "")
        target_big = to is not None and (to.scored.crunch or to.scored.crunch_now or to.scored.high_leverage or to.scored.mine
                                         or to.scored.lock in CRUNCH_LOCKS)
        facts = T.HoldFacts(d.kind, bool(cur.scored.is_break), any(m.game_id != gid for m in musts), bool(target_big))
        got = T.hold_for(self.threads, gid, cur.scored.league, now, facts, p)
        if got is None:
            return None
        kind, thread = got
        self.threads.note_hold(gid, now, thread, p)
        self.thread_holds[kind] += 1
        return kind
