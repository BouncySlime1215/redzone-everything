"""The decision core: one ev() function; defaults reproduce the rules it replaced."""
from redzone.decider import core
from redzone.decider.core import (Action, BreakView, CoreState, DecisionCore, Knobs, MomentView, Option,
                                  ReelView, SwitchPath)

K = Knobs()


def opt(gid, value, *, on_break=False, lock=None, cost=3.0, mine_late=False, break_soon_p=0.0):
    return Option(gid, moment=MomentView(value, lock=lock, mine_late=mine_late),
                  brk=BreakView(on_break, break_soon_p=break_soon_p), paths=(SwitchPath("tune", cost),))


def state(cur, *opts, gate=True):
    return CoreState(0.0, cur, {o.id: o for o in opts}, gate_safe=gate, gate_why="a play is on screen")


def test_switch_needs_margin_plus_cost():
    st = state("a", opt("a", 50), opt("b", 62.9, cost=3))
    assert core.ev(st, "switch", "b", K).gain < 0          # 12.9 < 10 + 3
    st = state("a", opt("a", 50), opt("b", 63.0, cost=3))
    assert core.ev(st, "switch", "b", K).gain >= 0         # exactly margin + cost


def test_lock_needs_override_lead_unless_your_team_late():
    st = state("a", opt("a", 50, lock="red zone"), opt("b", 65, cost=1))
    assert core.ev(st, "switch", "b", K).gain < 0          # needs 20
    st = state("a", opt("a", 50, lock="power play"), opt("b", 65, cost=1, mine_late=True))
    assert core.ev(st, "switch", "b", K).gain >= 0         # a lock never holds against your team late


def test_visit_is_scored_against_the_commercial_even_for_the_game_on_screen():
    st = state("a", opt("a", 10, on_break=True), opt("b", 24))
    assert core.ev(st, "visit", "b", K).gain < 0           # below break_destination_min
    assert core.ev(st, "visit", "a", K).gain < 0           # a chained visit can list the current game
    assert core.ev(st, "visit", "b", Knobs(dead_air_penalty=5)).gain >= 0


def test_return_tolerance():
    st = state("a", opt("a", 50), opt("b", 45))
    assert core.ev(st, "return", "b", K).gain >= 0
    assert core.ev(st, "return", "b", Knobs(return_tolerance=4)).gain < 0


def test_break_risk_discounts_destination():
    o = opt("b", 60, break_soon_p=0.5)
    assert core.rate(o, K) == 60
    assert core.rate(o, Knobs(break_risk=1.0)) == 30


def test_reel_fit_uses_cautious_end_and_cuts_down():
    assert core.reel_fits(100, 60, 30, K)
    assert not core.reel_fits(94, 60, 30, K)
    r = ReelView(60, min_length_s=30)
    assert core.fit_reel(80, r, 30, K) == 45
    assert core.fit_reel(60, r, 30, K) == 0.0


def test_guard_never_cuts_mid_play():
    st = state("a", opt("a", 20), opt("b", 90), gate=False)
    a = DecisionCore(K).decide(st)
    assert a.verb == "stay" and a.target == "a" and "no mid-play cut" in a.reason


def test_knobs_read_existing_decider_keys():
    class Cfg(dict):
        def get(self, k, d=None):
            return super().get(k, d)
    k = Knobs.from_cfg(Cfg({"decider.switch_margin": 7, "core.dead_air_penalty": 3}))
    assert k.switch_margin == 7 and k.dead_air_penalty == 3 and k.visit_min == 25
