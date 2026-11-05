"""[EXP-005] Off-policy exploration (decider.explore): rare near-tie picks between switches that are already
safe, with propensities logged. North Star: % of oracle (off-policy grading). Owner: lane 2 (noggin logic).
Nick Nov 4 7:21 PM: approved with limits; ships OFF until the v3 locked set lands."""
import dataclasses

import pytest

from redzone.decider.decider import Decision, Missed, State
from tests.test_decider import calibrate, cands, dec, sc, start_on  # noqa: F401


class _Always:
    """An rng that always explores and always draws `pick`."""

    def __init__(self, pick):
        self.pick = pick

    def random(self):
        return 0.0

    def choice(self, seq):
        assert self.pick in seq
        return self.pick


def _on(dec, rate=0.05, margin=3, log=True):
    dec.cfg.update({"decider": {"explore": {"enabled": True, "rate": rate, "margin": margin, "seed": 0},
                                "log_options": log}}, record=False)


def _sw(dec, target="b", kind="better"):
    return Decision(dec._clock(), "switch", target, "test", State.WATCHING, kind=kind)


def _ready(dec, clock):
    calibrate(dec, "youtubetv:a")
    start_on(dec, clock, "a", {})


def test_off_by_default(dec, clock):
    assert not dec.cfg.get("decider.explore.enabled")
    _ready(dec, clock)
    d = _sw(dec)
    assert dec._explore(d, cands(a=20, b=60, c=59)) is d and d.propensity == 1.0 and dec.explore_count == 0


def test_rate_is_capped_at_five_percent(dec, clock):
    _ready(dec, clock)
    _on(dec, rate=0.5)
    dec._explore(_sw(dec), cands(a=20, b=60, c=59))
    assert dec._explore_probs["b"] == pytest.approx(1 - 0.05 + 0.025)
    assert dec._explore_probs["c"] == pytest.approx(0.025)
    assert sum(dec._explore_probs.values()) == pytest.approx(1.0)


def test_near_ties_only_within_the_margin(dec, clock):
    _ready(dec, clock)
    _on(dec, margin=3)
    assert dec._explore_set(_sw(dec), cands(a=20, b=60, c=58, e=50), 3) == ["c"]


@pytest.mark.parametrize("flag", [{"crunch": True}, {"crunch_now": True}, {"late": True},
                                  {"personal": 10.0}, {"lock": "red zone drive"}])
def test_hard_exclusions_on_any_side(dec, clock, flag):
    """Crunch, down the stretch, your team (personal boost) or a lock: no exploring, whether it's the game
    we're on, the game chosen, or the near tie."""
    _ready(dec, clock)
    _on(dec)
    x = lambda g, v: dataclasses.replace(sc(g, v), **flag)  # noqa: E731
    assert dec._explore_set(_sw(dec), cands(a=x("a", 20), b=60, c=59), 3) == []
    assert dec._explore_set(_sw(dec), cands(a=20, b=x("b", 60), c=59), 3) == []
    assert dec._explore_set(_sw(dec), cands(a=20, b=60, c=x("c", 59)), 3) == []
    assert dec._explore_set(_sw(dec), cands(a=20, b=60, c=59), 3) == ["c"]


def test_never_while_a_catch_up_waits(dec, clock):
    _ready(dec, clock)
    _on(dec)
    dec.queue.append(Missed("e", "p1", "TD", "score", clock.t, 80.0))
    assert dec._explore_set(_sw(dec), cands(a=20, b=60, c=59), 3) == []


@pytest.mark.parametrize("kind", ["crunch", "return", "chase", "must_see", "start", "ended"])
def test_only_ordinary_switches(dec, clock, kind):
    _ready(dec, clock)
    _on(dec)
    assert dec._explore_set(_sw(dec, kind=kind), cands(a=20, b=60, c=59), 3) == []


def test_never_under_a_user_lock_or_pause(dec, clock):
    _ready(dec, clock)
    _on(dec)
    dec.user.paused = True
    assert dec._explore_set(_sw(dec), cands(a=20, b=60, c=59), 3) == []


def test_an_explored_pick_is_marked_and_logged_with_its_propensity(dec, clock):
    _ready(dec, clock)
    _on(dec)
    dec._explore_rng = _Always("c")
    out = dec._explore(_sw(dec), cands(a=20, b=60, c=59))
    assert (out.action, out.target, out.kind) == ("switch", "c", "explore")
    assert out.propensity == pytest.approx(0.025) and dec.explore_count == 1
    opts = {(o["a"], o["g"]): o["p"] for o in dec._option_set(out, cands(a=20, b=60, c=59))}
    assert opts[("stay", "a")] == 0.0
    assert opts[("switch", "c")] == pytest.approx(0.025) and opts[("switch", "b")] == pytest.approx(0.975)
    assert sum(opts.values()) == pytest.approx(1.0)


def test_the_explored_switch_still_goes_through_the_safety_overlays(dec, clock):
    """_explore runs before _time_entry and _safe, so the explored pick gets the same never-join-mid-play
    checks as any other switch."""
    _ready(dec, clock)
    _on(dec)
    seen = []
    real = dec._safe

    def spy(d, *a, **k):
        seen.append((d.target, d.kind))
        return real(d, *a, **k)
    dec._safe = spy
    dec._explore_rng = _Always("c")
    d = dec.decide(cands(a=20, b=80, c=79))
    assert ("c", "explore") in seen
    assert d.target == "c" or d.action != "switch"
