"""Contract tests: every interface in docs/architecture.md, against today's adapters.

A change in one module that breaks another's view of these shapes fails here first.
"""
from __future__ import annotations

import dataclasses

import pytest

import redzone.contracts as c
from redzone.contracts import legacy
from redzone.models import GameState, League, Phase, Team


def _game(gid="g1", league=League.NHL, phase=Phase.LIVE) -> GameState:
    return GameState(league, gid, Team("BOS", score=2), Team("NYR", score=1), phase=phase, period=3, clock_s=600)


def test_every_flag_has_a_degraded_mode_and_none_is_a_config_key():
    """F-01: the `flags:` block switched nothing (enabled() has no production caller), so it left the YAML.
    The Flag names stay for the DEGRADED table; a flag that gets wired adds its key in the PR that reads it."""
    import yaml
    from redzone.config import DEFAULTS
    data = yaml.safe_load(DEFAULTS.read_text())
    assert "flags" not in data
    for f in c.Flag:
        assert f.value in c.DEGRADED, f


def test_flags_enabled_reads_config():
    class Cfg(dict):
        def get(self, k, d=None):
            return super().get(k, d)
    assert c.enabled(Cfg({"flags.reel": False}), c.Flag.REEL) is False
    assert c.enabled(Cfg(), c.Flag.REEL) is True
    assert c.enabled(None, "reel", default=False) is False


def test_contract_dataclasses_are_frozen():
    for cls in (c.SyncEstimate, c.MomentPrediction, c.BreakForecast, c.ReasonerView,
                c.ReelOffer, c.ReelJump, c.Fault, c.Fix, c.HealthReport, c.TraceEvent,
                c.WorldState, c.GameView, c.Screen, c.GameFeed, c.Clock,
                c.MomentView, c.BreakView, c.SyncView, c.ReelView, c.Option, c.CoreState):
        assert dataclasses.is_dataclass(cls) and cls.__dataclass_params__.frozen, cls


def test_clock_converts_feed_and_screen_time_through_sync():
    est = c.SyncEstimate("youtubetv:espn", 30.0, 4.0, "tap", 100.0)
    clk = c.Clock(1000.0, sync=lambda t: est)
    assert clk.to_screen(900.0, "youtubetv:espn") == 930.0
    assert clk.to_feed(930.0, "youtubetv:espn") == 900.0
    assert clk.on_screen_now("youtubetv:espn") == 970.0
    assert clk.screen_window(900.0, "youtubetv:espn") == (922.0, 938.0)
    assert c.Clock(0.0).estimate("x").source == "default"


def test_legacy_sync_wraps_delaybook():
    from redzone.decider.screen import DelayBook
    book = DelayBook({"youtubetv": 30.0}, 15.0, clock=lambda: 1000.0)
    s = legacy.LegacySync(book)
    assert isinstance(s, c.SyncModel)
    e = s.estimate("youtubetv:espn", 1000.0)
    assert e.delay_s == pytest.approx(30.0) and not e.calibrated
    s.observe("youtubetv:espn", 26.0, "tap", 1000.0)
    e2 = s.estimate("youtubetv:espn", 1000.0)
    assert e2.calibrated and e2.delay_s == pytest.approx(26.0)
    with pytest.raises(ValueError):
        s.observe("youtubetv:espn", 26.0, "mic", 1000.0)


def test_sync_estimate_v2_fields_default_safely():
    e = c.SyncEstimate("prime:x", 33.0, 4.0, "sync", 100.0)
    assert e.calibrated and not e.uncertain
    assert c.SyncEstimate("prime:x", 33.0, 9.0, "sync", 100.0, uncertain=True).uncertain


def test_legacy_breaks_quantiles_are_ordered():
    from redzone.scoring.breaks import BreakBook
    b = legacy.LegacyBreaks(BreakBook(clock=lambda: 0.0))
    assert isinstance(b, c.BreakModel)
    g = _game()
    b.observe(g)
    f = b.forecast(g, 0.0)
    assert 0.0 <= f.p_start <= 1.0
    assert f.length_q10_s <= f.length_q50_s <= f.length_q90_s
    assert f.game_id == "g1"


def test_legacy_moment_without_model_is_empty():
    m = legacy.LegacyMoment(None)
    assert isinstance(m, c.MomentModel)
    p = m.predict(_game(), 5.0)
    assert not p.available and p.game_id == "g1"


def test_switch_paths_use_the_core_type():
    assert c.SwitchPath is __import__("redzone.decider.core", fromlist=["SwitchPath"]).SwitchPath
    a = c.SwitchPath("tv_flip", 2.0, 3.0, preloaded=True)
    b = c.SwitchPath("in_app_tune", 8.0)
    assert {a.kind, b.kind} <= set(c.KINDS)
    assert c.switching.deadline((a, b)) == 3.0
    assert c.switching.deadline((b,)) == 8.0
    assert c.switching.deadline(()) == float("inf")


def test_reel_jump_from_highlight_jump_dict():
    j = c.ReelJump.from_dict({"ev_reel": 5, "ev_stay": 2, "cost": 4.5, "ok": True, "reason": "x"})
    assert j.ok and j.cost_s == 4.5


def test_reasoner_view_expires():
    v = c.ReasonerView("g1", 100.0, ttl_s=60)
    assert v.fresh(160.0) and not v.fresh(161.0)


def test_health_worst():
    f1 = c.Fault("stale_feed", c.Severity.WARN, "feed:espn", "", 0.0)
    f2 = c.Fault("stick_offline", c.Severity.ERROR, "stick:a", "", 0.0)
    assert c.HealthReport(0.0, (f1, f2)).worst() == c.Severity.ERROR
    assert c.HealthReport(0.0).worst() is None


def test_trace_timings():
    t = c.MemoryTrace()
    assert isinstance(t, c.TraceSink)
    for stage, at in ((c.Stage.DECIDED, 10.0), (c.Stage.COMMAND, 10.2), (c.Stage.PICTURE, 14.0)):
        t.emit(c.TraceEvent("sw1", stage, at, "test"))
    assert t.timings("sw1") == pytest.approx({"decided": 0.0, "command": 0.2, "picture": 4.0})
    assert '"stage":"picture"' in t.events[-1].jsonl()
    order = [x.value for x in c.Stage]
    assert order.index("guarded") < order.index("rechecked") < order.index("command")


def _world(screen_game="g1"):
    feed = c.GameFeed(_game(), 0.0, "espn")
    return c.WorldState(c.Clock(0.0), c.freeze({"g1": c.GameView(feed)}),
                        c.Screen(screen_game, "youtubetv:espn", 0.0, True))


def test_world_state_is_read_only():
    w = _world()
    with pytest.raises(TypeError):
        w.games["g2"] = None
    assert list(w.live()) == ["g1"] and w.now == 0.0


def _core_state(gate_safe=False):
    opts = {"a": c.Option("a", moment=c.MomentView(40.0)), "b": c.Option("b", moment=c.MomentView(90.0))}
    return c.CoreState(100.0, "a", opts, gate_safe=gate_safe, gate_why="play on screen")


def test_guards_emit_a_trace_guarded_event_when_they_hold():
    from redzone.decider.core import NoMidPlayGuard
    st = _core_state(gate_safe=False)
    prop = c.DecisionCore(c.Knobs(), guards=[]).decide(st)
    assert prop.verb == "switch" and prop.target == "b"
    t = c.MemoryTrace()
    out = c.traced(st, prop, [NoMidPlayGuard()], t, "sw1")
    assert out.verb == "stay" and out.target == "a"
    ev = t.of("sw1")[0]
    assert ev.stage == c.Stage.GUARDED and ev.detail["held"] and ev.detail["rule"] == "no mid-play cut"
    out2 = c.traced(_core_state(gate_safe=True), prop, [NoMidPlayGuard()], t, "sw2")
    assert out2 is prop and not t.of("sw2")[0].detail["held"]
    assert c.HARD_RULES[2][0] == NoMidPlayGuard().name


def test_views_translate_producers_into_core_views():
    from redzone.scoring.engine import Scored
    sc = Scored("g1", League.NHL, 60.0, 60.0, 50.0, 1.0, 10.0, "", [], False, "power play", None, 0.8, crunch=True)
    pred = c.MomentPrediction("g1", 0.0, 70.0, 0.4, 0.1)
    rv = c.ReasonerView("g1", 0.0, lock_in=True, value_bonus=50.0)
    mv = c.moment_view(sc, pred, rv, now=10.0)
    assert mv.value == 80.0 and mv.p_big_soon == 0.4 and mv.lock == "power play" and mv.lock_in > 0 and mv.crunch
    assert c.moment_view(sc, pred, rv, now=1000.0).value == 60.0       # stale reasoner view ignored
    est = c.SyncEstimate("t", 30.0, 4.0, "tap")
    clk = c.Clock(1000.0, sync=lambda t: est)
    f = c.BreakForecast("g1", 0.0, 1.0, c.BreakKind.COMMERCIAL, True, 100.0, 150.0, 240.0, next_restart_at=1100.0)
    bv = c.break_view(f, clk, "t", started_at=950.0)                    # on screen now: feed 970, 20 s in
    assert bv.on_break and bv.remaining_p10_s == 80.0 and bv.remaining_p50_s == 130.0 and bv.restart_at == 1130.0
    soon = c.break_view(c.BreakForecast("g1", 0.0, 0.3, c.BreakKind.COMMERCIAL, False, 90, 150, 240), clk, "t")
    assert not soon.on_break and soon.break_soon_p == 0.3 and soon.break_soon_s == 150
    assert c.sync_view(est).delay_s == 30.0
    offer = c.ReelOffer("reel:1", 60.0, 30.0, 0.0, ("x", "y"))
    assert c.reel_option(offer).value_override == 30.0
    assert c.reel_option(offer, c.ReelJump(45.0, 20.0, 4.0, True)).value_override == 45.0
    assert c.reel_option(offer, c.ReelJump(45.0, 20.0, 4.0, False)) is None


@pytest.mark.parametrize("action,kind,target,expect", [
    ("stay", "", "g1", "stay"),
    ("switch", "better", "g2", "switch"),
    ("switch", "break_visit", "g2", "visit"),
    ("switch", "return", "g1", "return"),
    ("switch", "better", "reel", "reel"),
    ("replay", "replay", "g2", "replay"),
])
def test_action_from_legacy_decision(action, kind, target, expect):
    from redzone.decider.decider import Decision, State
    d = Decision(0.0, action, target, "why", State.WATCHING, kind=kind)
    a = legacy.action_from_decision(d)
    assert a.verb == expect and a.verb in __import__("redzone.decider.core", fromlist=["VERBS"]).VERBS


def test_tick_timer_records_overrun_and_falls_back():
    t = c.TickTimer()
    assert t.run("feeds", lambda: 1) == 1
    assert t.run("decision", lambda: 1 / 0, fallback="degraded") == "degraded"
    assert t.over["decision"] == float("inf")
    assert set(c.BUDGET_MS) >= {"feeds", "models", "decision", "command"}
