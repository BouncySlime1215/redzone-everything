"""The show layer over the decider: the thread ledger's hold and cut, added to the chain without changing it.

Why: D-ADDICT mechanic M1 (closure-first hold and cut, docs/agents/context/2026-11-09-addicting-show.md section 3.2). The
decider's own chain keeps Rule #1, the landing rules and the arbiter's floor. This subclass only (1) gives each live game
its open question, (2) adds a capped bonus to a game whose question is about to close, and (3) turns an optional cut into a
stay while the question on screen is still open. Nothing here can create an entry: a hold is a stay; a bonus changes which
game the chain prefers, and the chain still checks every landing.

Flags (default_config.yaml, both default false): `threads.enabled` runs the ledger; `threads.closure_first` lets it act.
Flag off, decide and _safe are the parent's own calls: decisions are hash-identical (scripts/thread_replay.py).
"""
from __future__ import annotations

import dataclasses as dc
import time
from collections import Counter
from collections.abc import Mapping

from redzone.decider.decider import CRUNCH_LOCKS, PSEUDO, Candidate, Decider, Decision
from redzone.models import GameState
from redzone.moments import threads as T


class ShowDecider(Decider):
    """A Decider that also keeps the thread ledger. The caller hands it each tick's game states in `show_states`."""

    def __init__(self, cfg, delays, clock=time.time):
        super().__init__(cfg, delays, clock=clock)
        self.show_states: Mapping[str, GameState] = {}
        self.threads = T.Ledger()
        self.thread_holds: Counter = Counter()
        self.session_end_at: float | None = None     # The Close (phone.commit, not built yet): past it nothing here acts

    def _threads_on(self) -> bool:
        return bool(self.cfg.get("threads.enabled", False))

    def _threads_params(self) -> T.Params:
        return T.Params.from_config(self.cfg.get)

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
            self.threads.tick(self.show_states, self.current, now, p, self._air_lag_s(p))
            if p.closure_first and not self._past_end(now):
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

    def _safe(self, d: Decision, cands, now: float, home_before) -> Decision:
        if self._threads_on() and d.action == "switch":
            p = self._threads_params()
            if p.closure_first and not self._past_end(now):
                held = self._thread_hold(d, cands, now, p)
                if held is not None:
                    self._home_restore(home_before, f"thread hold: {held}")
                    d = Decision(now, "stay", self.current, f"thread hold: {held} ({d.reason})", d.state, d.gate, want=d.target)
        return super()._safe(d, cands, now, home_before)

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
