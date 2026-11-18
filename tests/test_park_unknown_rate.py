"""park.unknown_rate_v2 (task A-05): an `unknown` hook handed to `park_show.Show` reaches the planner only with the flag on.

Pinned here: the flag is off by default and only a literal True turns it on; flag on, the hook gets the planner's
documented calls (bind(arrs, setting), rate(game, last reported second, now)) and what the planner uses is held to
0.1x..4x its own guess; a hook that returns exactly the guess decides byte for byte as the planner alone; flag off (or
any value but True), a handed-over hook is never touched and the show decides as before; flag on with nothing handed
over is as before; an object without bind/rate is refused.

Why: task A-05 (redzone-agents/v2/WORKPLAN.md, track A section 9): the seam for a learned worth of an unreported second
must not move a single decision while its flag is off, and must not let a model push the planner past the clamp.
"""
from __future__ import annotations

import functools
import math
import sys
from pathlib import Path

import pytest

from redzone import config
from redzone.lab.gym2 import env as gym
from redzone.noggin import park_plan as pp
from redzone.noggin.park_show import Show

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
A05_RATE_NIGHT, A05_RATE_GAMES, A05_RATE_MINUTES = "20261023", 2, 30
needs_archive = pytest.mark.skipif(not (ROOT / "data/archive/nba").exists(), reason="pinned eval data not fetched (scripts/fetch_eval_data.py)")


class Bomb:
    """Fails the run if anything reads, calls or asks it for anything."""

    def __getattribute__(self, name):
        raise AssertionError(f"the show touched a hook it must ignore ({name})")


class Recording:
    """Keeps every call the planner makes; rate returns `scale(g) x the planner's own guess` (the guess itself at 1)."""

    def __init__(self, unk_w: float, scale=lambda g: 1.0):
        self.unk_w, self.scale = unk_w, scale
        self.binds: list = []
        self.rates: list = []
        self.A = None

    def bind(self, A, s) -> None:
        self.A = A
        self.binds.append((A, s))

    def rate(self, g: int, K: int, t: int) -> float:
        self.rates.append((g, K, t))
        return self.unk_w * self.A[g].lev[K] * self.scale(g)


class Lev:
    def __init__(self, lev):
        self.lev = lev


@functools.lru_cache(maxsize=1)
def _night():
    return gym.replay_module().load_replay(A05_RATE_NIGHT, A05_RATE_GAMES)


def _run(**show_kw):
    """One replay of the night, recorded as the parity gate records it (Show built with `show_kw` added)."""
    pr = gym.replay_module()
    own = pr.Show
    try:
        pr.Show = functools.partial(Show, **show_kw) if show_kw else Show
        rec = gym.Recorder()
        show = pr.replay(A05_RATE_NIGHT, A05_RATE_GAMES, minutes=A05_RATE_MINUTES, say=rec.say, emit=rec.emit, loaded=_night())
    finally:
        pr.Show = own
    return show, gym.digest(gym.decision_list(show, rec))


# ---------------------------------------------------------------------------------------------- the flag and the clamp
def test_the_flag_is_off_by_default_and_read_only_as_a_literal_true(tmp_path):
    assert config.default("park.unknown_rate_v2") is False
    assert pp.unknown_rate_v2_on({}.get) is False and pp.unknown_rate_v2_on({"park.unknown_rate_v2": "yes"}.get) is False
    assert pp.unknown_rate_v2_on({"park.unknown_rate_v2": True}.get) is True
    (tmp_path / "config.yaml").write_text("park:\n  unknown_rate_v2: true\n")
    c = config.Config(user_path=tmp_path / "config.yaml")
    assert c.rejected == [] and pp.unknown_rate_v2_on(c.get) is True


def test_the_clamp_holds_a_rate_to_a_tenth_and_four_times_the_guess():
    assert pp.UNKNOWN_RATE_CLAMP == (0.1, 4.0)
    A = [Lev([0.5, 2.0]), Lev([1.0, 1.0])]
    out = {"v": None}

    class Fixed:
        def bind(self, A, s):
            self.bound = (A, s)

        def rate(self, g, K, t):
            self.asked = (g, K, t)
            return out["v"]

    inner = Fixed()
    c = pp.ClampedUnknownRate(inner, 0.02)
    setting = object()
    c.bind(A, setting)
    assert inner.bound[0] is A and inner.bound[1] is setting
    guess = 0.02 * 2.0                                     # unk_w x game 0's leverage at second 1
    for v, want in ((guess, guess), (guess * 3.9, guess * 3.9), (guess * 100, 4.0 * guess), (guess / 100, 0.1 * guess),
                    (-5.0, 0.1 * guess), (math.inf, guess), (math.nan, guess), (None, guess), ("x", guess)):
        out["v"] = v
        assert c.rate(0, 1, 77) == pytest.approx(want, rel=0, abs=1e-15), v
        assert inner.asked == (0, 1, 77)
    assert (c.calls, c.clamped, c.unusable) == (9, 3, 4)


# ---------------------------------------------------------------------------------------------- on a replayed night (fake sticks)
@needs_archive
def test_flag_off_never_touches_a_handed_over_hook():
    plain_show, plain = _run()
    assert plain_show.pl.unknown is None and plain_show.jumps_done >= 3
    for flag in (False, "true", 1, None):              # only a literal True turns it on
        show, got = _run(unknown_rate_v2=flag, unknown=Bomb())
        assert got == plain and show.pl.unknown is None and show.records == plain_show.records, flag


@needs_archive
def test_flag_on_with_nothing_handed_over_is_as_before():
    _, plain = _run()
    show, got = _run(unknown_rate_v2=True, unknown=None)
    assert got == plain and show.pl.unknown is None


@needs_archive
def test_flag_on_refuses_an_object_without_bind_and_rate():
    with pytest.raises(TypeError, match="park.unknown_rate_v2"):
        _run(unknown_rate_v2=True, unknown=object())


@needs_archive
def test_flag_on_asks_the_hook_with_the_documented_arguments_and_a_guess_hook_decides_as_before():
    _, plain = _run()
    hook = Recording(pp.Plan().unk_w)
    show, got = _run(unknown_rate_v2=True, unknown=hook)
    pl = show.pl
    assert isinstance(pl.unknown, pp.ClampedUnknownRate) and pl.unknown.inner is hook
    assert hook.binds and all(A is pl.A and s is show.s for A, s in hook.binds)
    assert len(pl.A) == A05_RATE_GAMES
    assert len(hook.rates) > 1000 and pl.unknown.calls == len(hook.rates) and pl.unknown.clamped == 0
    ts = [t for _, _, t in hook.rates]
    assert ts == sorted(ts)
    for g, K, t in hook.rates:                          # the game, the last second of it the feed has reported, now
        assert 0 <= g < A05_RATE_GAMES and K == min(pl.n - 2, t + pl.know)
        assert pl.A[g].a0 <= K < pl.A[g].a1
    assert got == plain                                 # the planner's own guess handed back: not one decision moves


@needs_archive
def test_flag_on_holds_a_wild_hook_to_the_clamp(monkeypatch):
    """What the planner gets for each unreported second, logged while the night runs: 4x the guess for a hook that
    says 1000x, a tenth of it for one that says a millionth."""
    got = []
    own = pp.ClampedUnknownRate.rate

    def logged(self, g, K, t):
        v = own(self, g, K, t)
        got.append((g, v, self.unk_w * self.A[g].lev[K]))
        return v
    monkeypatch.setattr(pp.ClampedUnknownRate, "rate", logged)
    _, plain = _run()
    assert got == []                                    # flag off: the clamp is never built
    hook = Recording(pp.Plan().unk_w, scale=lambda g: 1000.0 if g == 0 else 1e-6)
    show, wild = _run(unknown_rate_v2=True, unknown=hook)
    c = show.pl.unknown
    assert c.calls == len(hook.rates) == len(got) > 1000 and c.unusable == 0
    assert {g for g, _, _ in got} == set(range(A05_RATE_GAMES))
    for g, v, base in got:
        assert v == pytest.approx((4.0 if g == 0 else 0.1) * base, rel=1e-12, abs=1e-15)
    assert c.clamped == sum(1 for _, _, base in got if base > 0) > 0
    assert wild != plain                                # and it does reach the plan
