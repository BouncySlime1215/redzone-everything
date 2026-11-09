"""[WP-15] A replay where a brain is in charge of every switch and the commit gate is the only thing in its way.

Nothing in the app calls a brain yet (WP-17 builds the arbiter), so this stands where it will stand: each tick the
decider's own proposal is thrown away, the brain's top option becomes the proposal, and test_commit_gate's `_Gated`
judges it at the flip as live does. An output `valid()` rejects falls back to the decider's own proposal, the contract's
promise. `raw=True` skips `valid()`, so a broken output reaches the gate as well.

Dev nights only: `fit_only` refuses any split but fit, so no run here can read a locked night.

Why: Rule #1 has to hold against ANY brain before a model is allowed near the TV (build plan WP-15, brain-vs-code S4).
"""
from __future__ import annotations

import contextlib
import math
import os
import time
from collections import Counter
from dataclasses import dataclass, field
from types import SimpleNamespace as NS

from test_commit_gate import VETOES, _Gated, _ReplayHost, card

from redzone import engine as engine_mod
from redzone.contracts import brain as contract
from redzone.decider import gate
from redzone.decider.decider import REDZONE, Decider, Decision
from redzone.grading import dead_at, lag_of
from redzone.lab import backtest, leverage, splits

LEAGUES = {"nfl": ("scripts/eval.py", []), "nba": ("scripts/eval_sports.py", ["nba"]),
           "nhl": ("scripts/eval_sports.py", ["nhl"])}
PLAY_LEN_S = 7.0   # backtest.run's play_len: what its grader calls a play in progress


@dataclass
class Tally:
    """What one run's brain tried and what stopped it."""
    ticks: int = 0
    fallback: int = 0      # valid() refused the output: the decider's own proposal ran
    proposed: int = 0      # the brain asked for a cut
    passed: int = 0        # ...and the gate let it go
    vetoes: Counter = field(default_factory=Counter)
    fallback_reasons: Counter = field(default_factory=Counter)


class _Proposer(Decider):
    """decide() returns the brain's proposal; `_Gated` above it in the MRO judges and commits it."""
    brain, kind, raw, tally, truth = None, "jump", False, None, None

    def _input(self, cands: dict, redzone: str | None, now: float) -> contract.BrainInput:
        ids = [g for g in cands if g != self.current]
        options = [contract.Option("stay", "stay", self.current),
                   *[contract.Option(f"cut:{g}", "cut", g) for g in sorted(ids)]]
        if redzone and self.current != REDZONE:
            options.append(contract.Option(f"cut:{REDZONE}", "cut", REDZONE))
        self._ticks = getattr(self, "_ticks", 0) + 1
        return contract.BrainInput(tick=self._ticks, now=now, options=tuple(options),
                                   games=tuple(contract.GameFacts(g, cands[g].scored.league.value) for g in sorted(cands)),
                                   screen=contract.ScreenFacts(self.current))

    def decide(self, cands, redzone=None, *, commit=True, reel=None):
        base = super().decide(cands, redzone, commit=False, reel=reel)
        now, t = self._clock(), self.tally
        inp = self._input(cands, redzone, now)
        out = self.brain.think(inp)
        t.ticks += 1
        why = contract.problem(out, inp)
        if why is not None and not self.raw:
            t.fallback += 1
            t.fallback_reasons[why] += 1
            return base
        if base.action in ("switch", "replay"):
            self.reject(base, backoff=False)
        pick = max(inp.options, key=lambda o: (out.scores.get(o.id, -math.inf), o.id))
        if pick.verb != "cut":
            return Decision(now, "stay", self.current, f"brain {out.model_id}: stay", base.state, owner=base.owner)
        t.proposed += 1
        d = Decision(now, "switch", pick.game_id, f"brain {out.model_id}", base.state, kind=self.kind, owner=base.owner)
        return self._on_the_plan(d, cands, now)

    def _on_the_plan(self, d: Decision, cands: dict, now: float) -> Decision:
        """Take the rewind the gate's own plan wants, as an arbiter must: the proposal then fails on the landing, not
        on a mismatch (`rewind_changed`), and a rewind that passes is graded where it really lands."""
        if d.kind != "jump" and d.target in cands:
            flip = gate.Flip(_ReplayHost(self, cands), d, self.current, cands[d.target].target, cands, None, now)
            timed, plan = flip.replan
            if timed and plan is not None and plan.ok:
                d.rewind_s = plan.rewind_s
        return d


class _Host(_Gated, _Proposer):
    def reject(self, d, backoff=True):
        if d.kind == self.kind and d.reason.startswith("brain "):
            self.tally.vetoes["vetoed"] += 1
        super().reject(d, backoff)


class Truth:
    """What really happens, for a brain that cheats: the grader's own facts about the picture."""

    def __init__(self):
        self.replays, self.lag, self.tune_s = {}, 43.0, 5.0

    def lands_mid_play(self, gid: str, now: float) -> bool:
        r = self.replays.get(gid)
        return r is not None and not dead_at(r.league, r.wall, r.plays, now + self.tune_s - lag_of(self.lag, gid))[0]

    def high_leverage_on_screen(self, gid: str | None, now: float) -> bool:
        r = self.replays.get(gid or "")
        if r is None:
            return False
        at = now - lag_of(self.lag, r.gid)
        return any(w - PLAY_LEN_S <= at <= w and leverage.high_leverage(r.league, p) for w, p in zip(r.wall, r.plays))


class WorstBrain:
    """A valid brain that knows the future and wants Rule #1 broken: every score it gives is legal, and the highest goes
    to the cut that would land mid-play, or that would leave a high-leverage play. It stays when no cut would hurt."""

    def __init__(self, truth: Truth):
        self.truth = truth

    def think(self, inp: contract.BrainInput) -> contract.BrainOutput:
        now, cur = inp.now, inp.screen.game_id
        leaving_hurts = self.truth.high_leverage_on_screen(cur, now)
        def score(o):
            if o.verb != "cut":
                return 0.0
            return 100.0 if self.truth.lands_mid_play(o.game_id, now) else 90.0 if leaving_hurts else -50.0
        return contract.BrainOutput(tick=inp.tick, model_id="worst", made_at=now, ttl_s=5.0,
                                    scores={o.id: score(o) for o in inp.options})


def _fit_only(cap: int | None, real):
    def nights_for(league_dir, which, end=None):
        if which != "fit":
            raise AssertionError(f"adversarial replay asked for split {which!r}: dev nights only")
        keep = real(league_dir, which, end)
        if cap is None:
            return keep
        games = splits.archive_nights(league_dir, end)   # the busiest nights: a one-game night has nothing to switch to
        return set(sorted(keep, key=lambda n: (-len(games.get(n, ())), n))[:cap])
    return nights_for


@contextlib.contextmanager
def brain_replay(make_brain, kind="jump", raw=False, rules=None, cap=None):
    """Scorecards with `make_brain(truth)` in charge. rules=() removes the gate: the control run."""
    saved = (backtest.Decider, backtest.run, engine_mod.time, gate.GATE_RULES, gate.ENTRY_RULES, gate.redzone_on,
             engine_mod.redzone_on, splits.nights_for)
    box, tally, truth, real_run = {}, Tally(), Truth(), backtest.run
    agent = make_brain(truth)

    class Host(_Host):
        def __init__(self, cfg, delays, clock=None):
            super().__init__(cfg, delays, clock=clock)
            box["clock"] = clock
            self.brain, self.kind, self.raw, self.tally, self.truth = agent, kind, raw, tally, truth

    def run(replays, cfg, my_teams, **kw):
        truth.replays = {r.gid: r for r in replays}
        truth.tune_s = kw.get("tune_s", 5.0)
        delay = kw.get("true_delay")
        truth.lag = kw.get("feed_lag", 8.0) + (kw.get("delay", 35.0) if delay is None else delay)
        return real_run(replays, cfg, my_teams, **kw)

    backtest.Decider, backtest.run = Host, run
    engine_mod.time = NS(time=lambda: box["clock"](), monotonic=time.monotonic)
    gate.redzone_on = engine_mod.redzone_on = lambda games: True   # the replay only offers RedZone while it's on
    splits.nights_for = _fit_only(cap, saved[7])
    if rules is not None:
        gate.GATE_RULES, gate.ENTRY_RULES = rules, tuple(r for r in gate.ENTRY_RULES if r in rules)
    try:
        yield tally
    finally:
        (backtest.Decider, backtest.run, engine_mod.time, gate.GATE_RULES, gate.ENTRY_RULES, gate.redzone_on,
         engine_mod.redzone_on, splits.nights_for) = saved
        backtest._CALS.clear()


def score(league: str, make_brain, kind="jump", raw=False, rules=None, cap=None) -> dict:
    """One league's fit nights under one brain: the Rule #1 counts and what the brain tried."""
    script, args = LEAGUES[league]
    VETOES.clear()
    with brain_replay(make_brain, kind, raw, rules, cap) as tally:
        c = card(script, *args, "--split", "fit")
    nights = c["per_night"]
    return {"league": league, "kind": kind, "nights": len(nights), "entries": sum(n["entries"] for n in nights),
            "entries_mid_play": sum(n["entries_mid_play"] for n in nights), "cuts": sum(n["cuts"] for n in nights),
            "cuts_high": sum(n["cuts_high"] for n in nights), "ticks": tally.ticks, "proposed": tally.proposed,
            "fallback": tally.fallback, "vetoes": tally.vetoes.get("vetoed", 0), "named": dict(VETOES),
            "fallback_reasons": dict(tally.fallback_reasons)}


def cap_from_env(default: int = 2) -> int | None:
    """ADVERSARIAL_NIGHTS=all grades every fit night; the default keeps a pytest run to a couple of minutes."""
    v = os.environ.get("ADVERSARIAL_NIGHTS", str(default))
    return None if v == "all" else int(v)
