"""EXP-027: KL surprise over the full outcome distribution values catch-up replays, never cut-ins.
Pre-registration: docs/prereg/exp-027.md. The hook is OFF by default and harness-only."""
import copy

import numpy as np
import pytest

from redzone.decider.decider import Decider, Missed
from redzone.decider.screen import Event
from redzone.models import League
from redzone.noggin import surprise as S
from tests.test_decider import calibrate, cands, dec, start_on  # noqa: F401  (fixture)


# ----- the outcome distribution and KL --------------------------------------------------------------

@pytest.mark.parametrize("lg, period, clock", [("nba", 4, 30), ("nba", 5, 200), ("nfl", 4, 120), ("nfl", 5, 400),
                                               ("cbb", 2, 60), ("nhl", 3, 90), ("nhl", 4, 200), ("nhl", 5, 0)])
@pytest.mark.parametrize("diff", [-3, -1, 0, 1, 7])
def test_outcome_dist_is_a_distribution(lg, period, clock, diff):
    p = S.outcome_dist(lg, diff, period, clock)
    assert p.shape == (6,) and np.all(p >= 0) and p.sum() == pytest.approx(1.0)


def test_kl_is_zero_for_no_change_and_positive_for_a_score():
    assert S.play_surprise("nba", (80, 80), (80, 80), 4, 30) == pytest.approx(0.0, abs=1e-6)
    assert S.play_surprise("nba", (80, 80), (83, 80), 4, 30) > 0.3


def test_regulation_tie_puts_mass_on_overtime_and_shootout():
    p = S.outcome_dist("nhl", 0, 3, 1)       # tied, a second left in the 3rd
    assert p[S.H_OT] + p[S.A_OT] + p[S.H_SO] + p[S.A_SO] > 0.95
    assert p[S.H_SO] > 0.15 and p[S.A_SO] > 0.15


def test_late_tying_goal_beats_an_early_one_and_an_insurance_goal():
    late_tie = S.play_surprise("nhl", (1, 2), (2, 2), 3, 60)
    early_tie = S.play_surprise("nhl", (1, 2), (2, 2), 1, 1000)
    insurance = S.play_surprise("nhl", (3, 1), (4, 1), 3, 60)
    assert late_tie > early_tie and late_tie > insurance


def test_overtime_and_shootout_winners_are_surprising():
    ot_winner = S.play_surprise("nhl", (2, 2), (3, 2), 4, 150)
    so_winner = S.play_surprise("nhl", (2, 2), (3, 2), 5, 0)
    assert ot_winner > 1.0
    assert so_winner == pytest.approx(1.0, abs=1e-6)          # a coin flip resolved: exactly one bit
    assert S.outcome_dist("nhl", 1, 4, 100)[S.H_OT] == 1.0   # an OT goal ends it: no shootout mass
    walkoff = S.play_surprise("nba", (100, 101), (102, 101), 4, 1)
    assert walkoff > 2.0                                       # a buzzer-beater from behind


def test_bonus_is_capped_and_zero_for_no_surprise(cfg):
    assert S.bonus(cfg, None) == 0.0 and S.bonus(cfg, 0.0) == 0.0
    assert S.bonus(cfg, 0.5) == pytest.approx(10.0)
    assert S.bonus(cfg, 9.0) == pytest.approx(S.POINTS_PER_BIT * S.CAP_BITS)


def test_event_surprise_reads_the_timeline(dec, clock):  # noqa: F811
    tl = dec.timeline("b", League.NBA)
    t = clock.t
    tl.scores.append(Event(t - 100, "score", "jumper", pid="p0", score=(99, 101)))
    tl.events.append(Event(t, "play", "three", big=True, pid="p1", play_kind="3pt"))
    tl.scores.append(Event(t, "score", "three", pid="p1", score=(102, 101)))
    tl.clocks.append((t, 4, 3.0))
    bits = S.event_surprise(tl, "p1")
    assert bits == pytest.approx(S.play_surprise("nba", (99, 101), (102, 101), 4, 3.0))
    assert S.event_surprise(tl, "nope") is None
    tl.clocks.clear()
    assert S.event_surprise(tl, "p1") is None                 # no clock near the play: no guess


# ----- the hook: OFF by default, writes only Missed.value ---------------------------------------------

@pytest.fixture
def hooked():
    sink: dict = {}
    un = S.install(Decider, sink=sink)
    yield sink
    un()


def _queue_late_three(dec, clock):  # noqa: F811
    tl = dec.timeline("b", League.NBA)
    t = clock.t - 100
    tl.scores.append(Event(t - 60, "score", "jumper", pid="p0", score=(99, 101)))
    tl.events.append(Event(t, "play", "three", big=True, pid="p1", play_kind="3pt"))
    tl.scores.append(Event(t, "score", "three", pid="p1", score=(102, 101)))
    tl.clocks.append((t, 4, 3.0))
    m = Missed("b", "p1", "three", "other", t, 40.0)
    dec.queue = [m]
    return m


def test_off_by_default(cfg):
    assert cfg.get(S.FLAG) in (None, False) and not S.enabled(cfg)


def test_installed_but_off_changes_nothing(dec, clock, hooked):  # noqa: F811
    m = _queue_late_three(dec, clock)
    dec._scan_missed({}, clock.t)
    assert m.value == 40.0 and not hasattr(m, "surprise_bits") and hooked["valued"] == 0


def test_on_values_only_missed_plays_and_never_live_candidates(dec, clock, hooked):  # noqa: F811
    dec.cfg.data.setdefault("noggin", {})["surprise"] = {"enabled": True}
    m = _queue_late_three(dec, clock)
    seen = Missed("b", "p9", "dunk", "other", clock.t - 50, 40.0, seen_live=True)
    dec.queue.append(seen)
    cs = cands(a=30, b=20)
    before = copy.deepcopy({g: vars(x.scored) for g, x in cs.items()})
    S.apply(dec)
    assert m.value == pytest.approx(40.0 + S.bonus(dec.cfg, m.surprise_bits)) and m.value > 40.0
    assert seen.value == 40.0                                   # instant replays of seen plays: untouched
    assert {g: vars(x.scored) for g, x in cs.items()} == before  # live scores: untouched
    v = m.value
    S.apply(dec)
    assert m.value == v                                          # valued once, never compounded


def test_no_cut_ins_surprise_only_ever_starts_a_replay(cfg, clock, hooked):
    """The pre-registered guardrail: 0 switches started by surprise outside a replay. Twin deciders take
    the same ticks, one with surprise on. Every decision where they differ is a replay in the surprise
    twin, and the run-time log counts 0 cut-ins."""
    from redzone.decider.screen import DelayBook
    from tests.test_decider import with_fresh_timing

    def make(on: bool):
        c = copy.deepcopy(cfg)
        if on:
            c.data.setdefault("noggin", {})["surprise"] = {"enabled": True}
        d = Decider(c, DelayBook({"youtubetv": 40.0}, sigma_default=15.0, clock=clock), clock=clock)
        with_fresh_timing(d, clock)
        for g in "ab":
            calibrate(d, f"youtubetv:{g}")
        return d

    t_start = clock.t
    off, on = make(False), make(True)
    for d in (off, on):
        clock.t = t_start
        start_on(d, clock, "a", {"b": 30})
    t_run = clock.t
    for d in (off, on):
        _queue_late_three(d, clock)
    trace = {}
    for name, d in (("off", off), ("on", on)):
        clock.t = t_run
        out = []
        for score_a in (60, 55, 50, 45, 40, 35, 30, 25):
            clock.t += 15
            out.append(d.decide(cands(a=score_a, b=30)))
        trace[name] = out
    # Until the twins first differ they saw identical states; the first difference is the surprise
    # twin STARTING a replay (after it, the screens differ, so later decisions may differ too).
    first = next(((x, y) for x, y in zip(trace["off"], trace["on"]) if (x.action, x.target) != (y.action, y.target)),
                 None)
    assert first is not None, "surprise should have changed something here"
    y = first[1]
    assert y.action == "replay" and y.replay is not None and y.replay.surprise_bonus > 0
    # ...and no surprise-valued replay ever rode on a non-replay action (a cut-in into live play).
    assert hooked["cutins"] == 0 and hooked["replays"] >= 1
    for d in trace["on"]:
        if d.action != "replay" and d.kind != "replay":     # not starting or holding a replay
            assert d.replay is None or getattr(d.replay, "surprise_bonus", 0) == 0
