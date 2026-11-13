"""Characterization of the PARK planner's search (task A-05): every option list it builds, in order, with every value
and the row it chose, and the plan it then keeps, over fixed scenarios, pinned as sha256 digests.

Recorded on the planner BEFORE `Planner._search` was split into named steps (the option list, the scoring of the
options, the choice); the split must leave every digest byte for byte as it was. Numbers are kept exactly (floats as
float.hex), sets sorted, so a change in arithmetic order, iteration order or a tie-break changes a digest.
Scenarios: three hand-made nights of tests/test_park_plan.py on the simulator's planner (defaults; every knob a show on
real sticks sets; a stand-in `unknown` hook with warrants noted), and a recorded dev night through `park_show.Show` on
the replay's fake sticks (the app's own planner call).

Why: WORKPLAN v2 A-05 / track A section 9: a learned scorer will later replace one call over the planner's option
list; the refactor that makes that one call must not move a single decision first.
"""
from __future__ import annotations

import asyncio
import hashlib
import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT.as_posix(), (ROOT / "scripts").as_posix(), (ROOT / "tests").as_posix()):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import park_plan as pp  # noqa: E402
import park_sim as ps  # noqa: E402
from test_park_plan import exact, random_night  # noqa: E402

needs_archive = pytest.mark.skipif(not (ROOT / "data/archive/nba").exists(), reason="pinned eval data not fetched (scripts/fetch_eval_data.py)")


def opt_text(obj) -> str:
    """One exact text for a trace item: floats as float.hex, numpy scalars as Python ones, sets sorted."""
    if obj is None or isinstance(obj, (bool, np.bool_)):
        return repr(None if obj is None else bool(obj))
    if isinstance(obj, (int, np.integer)):
        return str(int(obj))
    if isinstance(obj, (float, np.floating)):
        f = float(obj)
        return "nan" if math.isnan(f) else f.hex()
    if isinstance(obj, str):
        return repr(obj)
    if isinstance(obj, (set, frozenset)):
        return "{" + ",".join(sorted(opt_text(x) for x in obj)) + "}"
    if isinstance(obj, (list, tuple)):
        return "[" + ",".join(opt_text(x) for x in obj) + "]"
    raise TypeError(f"unexpected {type(obj).__name__} in a planner trace")


class SearchLog:
    """Hangs on one planner: after every search, its option list (Planner.trace: second, value of staying, screen
    position, last reported second, every option row, the chosen row) and the plan it then holds."""

    def __init__(self, pl):
        self.pl, self.h, self.searches = pl, hashlib.sha256(), 0
        pl.trace = []
        own = pl._search

        def search(t, S):
            own(t, S)
            self.add(t)
        pl._search = search

    def add(self, t: int) -> None:
        pl = self.pl
        for item in pl.trace:
            self.h.update(opt_text(item).encode())
            self.h.update(b"\n")
        pl.trace.clear()
        state = (t, pl.cmd, pl.go, pl.wr, pl.info, pl.open, pl.firm, pl.land_j, pl.next, pl.searches)
        self.h.update(opt_text(state).encode())
        self.h.update(b"\n")
        self.searches += 1

    def digest(self) -> str:
        return self.h.hexdigest()[:16]


class StandInRate:
    """A deterministic `unknown` hook: the worth of an unreported second from the arguments the planner passes."""

    def bind(self, A, s) -> None:
        self.A = A

    def rate(self, g: int, K: int, t: int) -> float:
        return 0.025 * float(self.A[g].lev[K]) + 0.004 * (g % 2) + 1e-5 * ((K + t) % 7)


def run_sim(seed: int, guard: int, plan, **kw) -> tuple[str, int, list]:
    """The simulator's planner one second at a time over random_night(seed) (as tests/test_park_plan.play steps it)."""
    night, s = random_night(seed), exact(guard)
    pl = pp.Planner(plan, **kw)
    log = SearchLog(pl)
    a, b = night.span
    brain = ps.Foresight()
    st, S = ps.run(night, brain, s, t0=a, t1=a + 1, planner=pl)
    for t in range(a + 1, b):
        st, S = ps.run(night, brain, s, t0=t, t1=t + 1, state=S, stats=st, planner=pl)
    return log.digest(), log.searches, list(st.hops)


SIM_SCENARIOS = {
    # name: (seed, guard, plan, planner knobs)
    "defaults": (1, 2, dict(), dict()),
    "show_knobs": (2, 6, dict(jump_bonus=2.0), dict(skip=False, confirm=2, tune_lag=2, hold_off=1, hold_ready=2, resume_s=1,
                                                    sticky=True, dead_w=0.01, max_wait=40, warrant="need", roll_same=True,
                                                    tune_stick=0.3, join_playing=False)),
    "unknown_hook": (3, 2, dict(jump_bonus=1.0), dict(unknown=StandInRate(), warrant="note")),
}

# Recorded on the planner before the split (integration/next 14eba59d): digest, searches, flips.
SIM_PINNED = {
    "defaults": ("c18a814940092457", 1272, 107),
    "show_knobs": ("2821d8ff15ff9fcc", 1234, 27),
    "unknown_hook": ("14d60410a20c246c", 1277, 137),
}


@pytest.mark.parametrize("name", sorted(SIM_SCENARIOS))
def test_the_option_lists_and_choices_are_as_recorded(name):
    seed, guard, plan, kw = SIM_SCENARIOS[name]
    got, searches, hops = run_sim(seed, guard, pp.Plan(**plan), **kw)
    assert (got, searches, len(hops)) == SIM_PINNED[name]
    assert searches > 100 and len(hops) >= 3            # the scenario really searched and jumped


A05_SHOW_SCENARIOS = {
    # name: (night, games, minutes, park_replay.build options)
    "plain": ("20261023", 2, 30, dict()),
    "director_v1": ("20261021", 3, 20, dict(seen=True, director=True)),
}
# Recorded on the planner before the split (integration/next 14eba59d): digest, searches, jumps carried out.
A05_SHOW_PINNED = {
    "plain": ("8f5ac98733daa3b8", 909, 4),
    "director_v1": ("b24f1f546e1f297a", 619, 7),
}


def run_show(night: str, games: int, minutes: float, build_kw: dict | None = None, **show_kw) -> tuple[str, int, int]:
    """A recorded dev night through park_show.Show on the replay's fake sticks, every search of the show's planner logged."""
    import functools

    from redzone.lab.gym2 import env as gym
    from redzone.noggin.park_show import Show
    pr = gym.replay_module()
    own = pr.Show
    try:
        pr.Show = functools.partial(Show, **show_kw) if show_kw else Show
        b = pr.build(night, games, minutes=minutes, **(build_kw or {}))
    finally:
        pr.Show = own
    log = SearchLog(b.show.pl)
    asyncio.run(b.clock.run(pr.drive(b.show, b.clock, b.rows, b.a, b.b)))
    return log.digest(), log.searches, b.show.jumps_done


@needs_archive
@pytest.mark.parametrize("name", sorted(A05_SHOW_SCENARIOS))
def test_the_show_planner_option_lists_are_as_recorded(name):
    night, games, minutes, build_kw = A05_SHOW_SCENARIOS[name]
    got = run_show(night, games, minutes, build_kw)
    assert got == A05_SHOW_PINNED[name]
    assert got[1] > 100 and got[2] >= 3
