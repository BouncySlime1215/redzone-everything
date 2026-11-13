"""Gym v2's episode: the PARK replay of one recorded night, stepped one show tick at a time (task A-01, slice 1).

    env = GymV2("20261023", 4, seed=0)            # identity scenario: the replay exactly as scripts/park_replay.py runs it
    obs = env.reset()
    while True:
        obs, reward, done, info = env.step()
        if done:
            break
    env.digest()                                  # sha256 of the canonical decision list (== replay_digest(...) for identity)

The episode is `park_replay.build` (the app's own `Show` on fake sticks, the PARK core's step function with the planner each second)
driven by the replay's own loop. Stepping never changes what runs: the loop is paused only at the moment the replay's
fake clock is about to wake the next tick (nothing is running then), and `observe` only reads. So with the identity
scenario the decision list (every event the show emits, every line it says, every jump record, every key and command
sent to the fake sticks, the run's result row) is byte for byte `park_replay.replay`'s.

A pause point exists only where the next tick has to wait on the clock; a tick that needs no wait (the first one) runs
inside the step before it. `info["t"]` is the next tick the step stopped in front of.

Research only: nothing under redzone/ outside lab/gym2 imports this package (`tests/test_gym2_parity.py` checks).
Why: every Gym v2 experiment (A-02 onward) is only as good as its world; it must first be the show itself, exactly.
"""
from __future__ import annotations

import asyncio
import hashlib
import heapq
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from redzone.lab.gym2 import slates
from redzone.lab.gym2.mask import Rule1Mask
from redzone.lab.gym2.reward import RewardMeter, Weights, rule1
from redzone.lab.gym2.scenario import IDENTITY, Scenario, log_draw

REPO_DIR = Path(__file__).resolve().parents[3]
YIELD = 0                       # asyncio.sleep(YIELD): let every ready task run once (park_replay.FakeClock does the same)


def replay_module():
    """scripts/park_replay.py, imported the way the tests import it (it puts the repo root on the path itself)."""
    p = (REPO_DIR / "scripts").as_posix()
    if p not in sys.path:
        sys.path.insert(0, p)
    import park_replay
    return park_replay


def canon(obj) -> str:
    """One canonical JSON text: sorted keys, no spaces, anything else by its repr (tuples become lists)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=repr)


@dataclass
class Recorder:
    """The show's `say` and `emit`, each kept as text at the moment it was made (later edits to a dict cannot leak in)."""
    says: list = field(default_factory=list)
    events: list = field(default_factory=list)

    def say(self, line: str) -> None:
        self.says.append(str(line))

    def emit(self, row: dict) -> None:
        self.events.append(canon(row))


def decision_list(show, rec: Recorder) -> dict:
    """Everything the show decided and did on one run, as canonical text (the parity gate compares these)."""
    pr = replay_module()
    rig = show.rig
    return {"events": list(rec.events), "says": list(rec.says), "records": [canon(r) for r in show.records],
            "sent": canon(rig.sent), "warrants": canon(show.warrants), "refused": canon(show.refused),
            "result": canon(pr.result(show))}


def digest(decisions: dict) -> str:
    return hashlib.sha256(canon(decisions).encode()).hexdigest()


def replay_decisions(night: str, games: int = 4, *, league: str = "nba", seed: int = 0, minutes: float = 0.0,
                     loaded: tuple | None = None) -> dict:
    """The reference: `park_replay.replay` as the script runs it, recorded the same way."""
    pr = replay_module()
    rec = Recorder()
    show = pr.replay(night, games, league=league, seed=seed, minutes=minutes, loaded=loaded, say=rec.say, emit=rec.emit)
    return decision_list(show, rec)


def _chain(tm, hooks):
    for h in hooks:
        tm = h(tm)
    return tm


def _step_clock_type(pr):
    """park_replay.FakeClock, plus a mark on the wait that comes before each tick (see GymV2._drive)."""
    class _StepClock(pr.FakeClock):
        def __init__(self, t0: float = 0.0):
            super().__init__(t0)
            self.mark, self.tick_fut = False, None

        async def sleep(self, s: float) -> None:          # FakeClock.sleep, keeping the future of a marked wait
            if s <= 0:
                self.mark = False
                await asyncio.sleep(YIELD)
                return
            fut = asyncio.get_running_loop().create_future()
            self._n += 1
            heapq.heappush(self._q, (self.t + s, self._n, fut))
            if self.mark:
                self.tick_fut, self.mark = fut, False
            await fut
    return _StepClock


class GymV2:
    """One episode of the practice world on a recorded dev night. See the module docstring."""

    def __init__(self, night: str, games: int = 4, *, league: str = "nba", scenario: Scenario = IDENTITY, seed: int = 0,
                 minutes: float = 0.0, loaded: tuple | None = None, draw_log=None, mix: dict | None = None,
                 weights: Weights | None = None, mask_level: str = "strict"):
        """mix: a census mix ({"nba": 2, "cbb": 1}): the slate is composed from recorded fit games (slates.compose, with
        this episode's seed) instead of `night`'s own. weights: the reward terms' weights. mask_level: Rule1Mask's level."""
        self.night, self.games, self.league, self.scenario = str(night), int(games), league, scenario
        self.mix, self.weights, self.mask_level = (dict(mix) if mix else None), weights or Weights(), mask_level
        self.draw_log, self.draw = draw_log, None
        self.seed, self.minutes, self.loaded = int(seed), float(minutes), loaded
        self.loop = self.task = self.show = self.clock = None
        self.done = False
        self.rec = Recorder()

    # ---- the episode ---------------------------------------------------------------------------------------------
    def reset(self) -> dict:
        self.close()
        pr = replay_module()
        self.rec = Recorder()
        sc = self.scenario
        kw = sc.build_kwargs()
        loaded = self.loaded
        if self.mix:
            rs, slate = slates.compose(self.mix, self.seed)
            loaded, self.games = (rs, slate), len(rs)
            hooks = [slates.mixed_timing(rs)] + ([kw["timing"]] if "timing" in kw else [])
            kw["timing"] = lambda tm, _h=hooks: _chain(tm, _h)
        b = pr.build(self.night, self.games, league=self.league, seed=self.seed, minutes=self.minutes, loaded=loaded,
                     say=self.rec.say, emit=self.rec.emit, clock_type=_step_clock_type(pr), **kw)
        if not sc.identity:
            sc.patch(b, pr)
        self.draw = log_draw(self.draw_log, night=self.night, games=self.games, episode_seed=self.seed, scenario=sc,
                             extra={"mix": dict(self.mix)} if self.mix else None)
        self.show, self.clock, self.rows, self.a, self.b = b.show, b.clock, b.rows, b.a, b.b
        self.meter = RewardMeter(self.show.judge, b.a)
        self.loop = asyncio.new_event_loop()
        self.task, self._held, self.done, self.next_t = None, False, False, b.a
        return self.observe()

    def step(self, action=None) -> tuple:
        """Run the show to the next tick that has to wait on the clock (or to the end). No action yet: the show's own
        planner decides (wiring a director's ranking into Planner is task A-02/A-03). The reward is the weighted sum of
        the terms the step earned (reward.py); info carries the terms, the judge's Rule #1 counts and, at the end, the
        mask's audit of every cut the show made."""
        if action is not None:
            raise ValueError("Gym v2 has no actions yet: the show's own planner decides")
        if self.loop is None:
            raise RuntimeError("reset() first")
        if not self.done:
            self.loop.run_until_complete(self._pump())
        if self.done:
            self._finish_loop()
        upto = (self.show.last_t + 1) if self.done and self.show.last_t is not None else self.next_t
        terms = self.meter.read(upto)
        info = {"t": self.next_t, "scenario": self.scenario.name, "terms": terms, "rule1": rule1(self.show.judge)}
        if self.done:
            info["mask_audit"] = self.mask().audit(self.show.judge.cuts)
        return self.observe(), self.weights.total(terms), self.done, info

    def mask(self) -> Rule1Mask:
        """The Rule #1 mask over this episode's recorded games (true seconds on the night's grid)."""
        return Rule1Mask(self.show.replays, self.show.night.base, level=self.mask_level)

    @classmethod
    def from_mix(cls, seed: int, *, scenario: Scenario = IDENTITY, have: set | None = None, **kw) -> "GymV2":
        """An episode on a census-weighted mix (slates.draw_mix) composed with the same seed."""
        mix = slates.draw_mix(seed, have=have)
        return cls(f"mix-{int(seed)}", sum(mix.values()), scenario=scenario, seed=seed, mix=mix, **kw)

    @classmethod
    def from_draw(cls, row: dict, **kw) -> "GymV2":
        """The episode a draw-log line describes, set up to run again exactly."""
        return cls(row["night"], row["games"], scenario=Scenario.from_dict(row["scenario"]), seed=row["episode_seed"],
                   mix=row.get("mix"), **kw)

    def run(self) -> "GymV2":
        if self.loop is None and not self.done:
            self.reset()
        while not self.done:
            self.step()
        return self

    def observe(self) -> dict:
        """What the show holds now (read only)."""
        show = self.show
        S, rig = show.S, show.rig
        return {"t": self.next_t, "on_game": S.sg, "hidden_game": S.hg, "hidden_paused": bool(S.h_paused),
                "jumps": show.jumps_done, "stop": show.stop,
                "streams": [{"game": rig.game[k], "paused": bool(rig.paused[k]), "lag": float(rig.lag[k]),
                             "read": bool(rig.read[k])} for k in (0, 1)]}

    def decisions(self) -> dict:
        if not self.done:
            raise RuntimeError("the episode is not over")
        return decision_list(self.show, self.rec)

    def digest(self) -> str:
        return digest(self.decisions())

    def close(self) -> None:
        if self.loop is not None:
            self._finish_loop()

    # ---- the replay's own loop, paused between ticks -------------------------------------------------------------
    async def _drive(self) -> None:
        """park_replay.drive, with the wait before each tick marked so the pump can stop in front of it."""
        show, clock, rows = self.show, self.clock, self.rows
        try:
            for t in range(self.a, self.b):
                if not show.rig.instant:
                    clock.mark = True
                    await clock.sleep(t - clock.now())
                rows.step(t)
                await show.tick(t)
                if show.stop:
                    show.say(f"STOPPED: {show.stop}. No more jumps.")
                    break
        finally:
            await show.close()

    async def _pump(self) -> None:
        """park_replay.FakeClock.run, stopping (instead of waking it) when the next timer is the wait before a tick."""
        clock = self.clock
        q = clock._q
        if self.task is None:
            self.task = asyncio.ensure_future(self._drive())
        if self._held:
            when, _, fut = heapq.heappop(q)
            clock.t = max(clock.t, when)
            if not fut.done():
                fut.set_result(None)
            self._held = False
        while True:
            for _ in range(12):
                await asyncio.sleep(YIELD)
            if self.task.done():
                self.task.result()
                self.done = True
                return
            if not q:
                raise RuntimeError("everything is waiting on nothing")
            if q[0][2] is clock.tick_fut and not clock.tick_fut.done():
                self._held = True
                self.next_t = int(round(q[0][0]))
                return
            when, _, fut = heapq.heappop(q)
            clock.t = max(clock.t, when)
            if not fut.done():
                fut.set_result(None)

    def _finish_loop(self) -> None:
        """What asyncio.run does at its end: cancel what is left, then close the loop."""
        loop, self.loop = self.loop, None
        if loop is None:
            return
        try:
            left = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for t in left:
                t.cancel()
            if left:
                loop.run_until_complete(asyncio.gather(*left, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            loop.close()
