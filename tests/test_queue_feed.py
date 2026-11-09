"""Queue feed (WP-12B): known moments, long-lead warnings and failed routes reach the queue's upper rungs.

Why: `_queue_plan` only fed Lock, Next and Pin, so the queue acted on nothing but games Nick picked. These prove the
Tier and Warning rungs fire from the look-ahead and the schedule, stay off the phone, and never cut. No TV, no adb.
"""
import asyncio
import json
import time
import types

from redzone.channels import Where
from redzone.config import Config
from redzone.decider.cuts import Window
from redzone.decider.screen import Delay, Event, Timeline
from redzone.discovery.providers import Pick, Source
from redzone.engine import Engine
from redzone.models import League, Phase, Stoppage
from redzone.noggin import gamequeue as gq
from redzone.store import Store
from test_queue_wire import ShadowControl, cands_of, rig, target  # noqa: F401

BOTH = {"a": 70, "b": 50, "c": 40}


def play(e, gid, kind, ago, big=True, dead_ago=40.0, mu=110.0, sigma=4.0, league=League.NFL, spread=0.0):
    """Game gid has a `kind` play `ago` s back in the feed, a dead ball `dead_ago` s back, a screen mu s behind."""
    now = time.time()
    tl = Timeline(league)
    tl.events.append(Event(now - ago, "play", "x", big, "p1", kind, spread))
    if dead_ago is not None:
        w = Window(now - dead_ago, now - ago - spread - 1.0, "huddle")
        tl.dead = types.SimpleNamespace(marks={"m": 1}, windows=lambda: [w])
    e.decider.timelines[gid] = tl
    e.delays.book[target(gid)] = Delay(mu, sigma, calibrated_at=now)


def plan_of(e, scores=None):
    plans = []
    real = e._queue_plan
    e._queue_plan = lambda *a: (plans.append(real(*a)), plans[-1])[1]
    e.control.t += 1
    e._preload_standby(cands_of(scores or BOTH))
    e._queue_plan = real
    return plans[0]


def test_a_known_touchdown_is_tier_one_and_fills_the_hidden_stick(rig):  # noqa: F811
    e = rig()
    play(e, "c", "td", 20.0)
    plan = plan_of(e)
    assert plan.want.entry.game == "c" and plan.want.rung == gq.Rung.TIER1 and plan.swap
    assert -30.0 < plan.want.entry.join_in_s < -20.0 and plan.want.entry.lookahead_s == 110.0   # window end, less 2 sigma
    assert e.control.calls[-1][0] == target("c")


def test_tier_one_beats_a_pin_and_a_pin_beats_tier_two(rig):  # noqa: F811
    e = rig()
    e.queue_pins[:] = ["b"]
    play(e, "c", "fg", 20.0)
    assert plan_of(e).want.rung == gq.Rung.PIN
    play(e, "c", "td", 20.0)
    assert plan_of(e).want.rung == gq.Rung.TIER1


def test_a_field_goal_is_tier_two_and_a_plain_play_is_nothing(rig):  # noqa: F811
    e = rig()
    play(e, "c", "fg", 20.0)
    assert plan_of(e).want.rung == gq.Rung.TIER2
    play(e, "c", "rush", 20.0, big=False)
    assert plan_of(e).want.rung == gq.Rung.PLANNER


def test_each_league_maps_onto_the_moment_list(rig):  # noqa: F811
    for league, kind, big, tier in ((League.NHL, "goal", True, 1), (League.NHL, "tripping", False, 2),
                                    (League.NHL, "fighting", False, None), (League.NBA, "made_shot", True, 1),
                                    (League.NBA, "three", False, None), (League.MLB, "hr", True, 1)):
        e = rig()
        play(e, "c", kind, 20.0, big=big, league=league)
        got = e._queue_moment("c", target("c"), time.time(), 10.0)
        assert (got[0] if got else None) == tier, (league, kind)


def test_no_clean_dead_ball_or_a_doubtful_delay_gives_no_tier(rig):  # noqa: F811
    e = rig()
    play(e, "c", "td", 20.0, dead_ago=None)
    assert e._queue_moment("c", target("c"), time.time(), 10.0) is None
    play(e, "c", "td", 20.0, sigma=15.0)
    assert e._queue_moment("c", target("c"), time.time(), 10.0) is None


def test_a_moment_the_stick_cannot_reach_takes_no_rung(rig):  # noqa: F811
    e = rig()
    play(e, "c", "td", 20.0, mu=50.0, dead_ago=40.0)   # airs in about 20 s; a tune and buffer is 60 s
    assert plan_of(e).want.rung == gq.Rung.PLANNER


def test_a_long_lead_game_warns_before_its_start_only_when_close(rig):  # noqa: F811
    e = rig()
    now = time.time()
    g = types.SimpleNamespace(game_id="w", phase=Phase.PRE, start_time=now + 150.0, league=League.NFL)
    e.merger.games["w"] = g
    e.where["w"] = Where("prime:w", "Prime", "sure", True)
    plan = plan_of(e)
    assert plan.want.entry.game == "w" and plan.want.rung == gq.Rung.WARNING and plan.swap
    assert e.control.calls[-1][0] == "prime:w"
    g.start_time = now + 3600.0
    assert plan_of(e).want.entry.game != "w"


def test_a_long_lead_game_warns_before_a_known_break_ends(rig):  # noqa: F811
    e = rig()
    now = time.time()
    e.where["b"] = Where("prime:b", "Prime", "sure", True)
    e.merger.games["b"] = types.SimpleNamespace(game_id="b", phase=Phase.LIVE, league=League.NFL)
    tl = Timeline(League.NFL)
    tl.stoppage = Stoppage.HALFTIME
    tl.events.append(Event(now - 700.0, "stop_start", Stoppage.HALFTIME.value))
    e.decider.timelines["b"] = tl
    e.delays.book["prime:b"] = Delay(40.0, 4.0, calibrated_at=now)
    assert e._queue_lead(e.merger.games["b"], "prime:b", now, 180.0) == 140.0
    tl.stoppage = Stoppage.NONE
    assert e._queue_lead(e.merger.games["b"], "prime:b", now, 180.0) is None


def test_no_tier_or_join_time_in_the_phone_snapshot(rig, tmp_path):  # noqa: F811
    e = rig()
    play(e, "c", "td", 20.0)
    assert plan_of(e).want.rung == gq.Rung.TIER1
    cfg = Config(user_path=tmp_path / "real.yaml", history_dir=tmp_path / "real_hist")
    cfg.update({"queue": {"enabled": True}})
    real = Engine(cfg, Store(tmp_path / "real.db"))   # ShadowControl has no snapshot fields: same dict, real snapshot
    real._game_queue = e._game_queue
    text = json.dumps(real.snapshot()["game_queue"]) + e.owners[-1][1]
    for word in ("tier", "join_in", "lookahead", "lead_s"):
        assert word not in text.lower()
    assert e._game_queue["up_next"][0]["reason"] == "up soon"


def test_one_owner_per_tick_and_the_queue_never_cuts(rig):  # noqa: F811
    e = rig()
    assert not hasattr(e.control, "flip")   # a queue that tried to cut would raise
    play(e, "c", "td", 20.0)
    for _ in range(50):
        before = len(e.owners)
        e.control.t += 1
        e._preload_standby(cands_of(BOTH))
        assert len(e.owners) == before + 1
    assert {c[0] for c in e.control.calls} == {target("c")}


def test_flag_off_never_plans_and_never_sets_the_hook(rig):  # noqa: F811
    e = rig(on=False)
    e._queue_plan = lambda *a: (_ for _ in ()).throw(AssertionError("queue planned with the flag off"))
    for _ in range(5):
        e.control.t += 1
        e._preload_standby(cands_of(BOTH))
    assert getattr(e.control, "on_queue_move", None) is None
    on = rig()
    plan_of(on)
    assert on.control.on_queue_move == on._on_queue_move


def test_a_walled_route_goes_off_and_the_queue_fails_over_at_once(rig):  # noqa: F811
    """WP-13's hook: the wall covers this game's Prime routes for the night and the next route is tuned on the next pass."""
    e = rig()
    alts = [Source("prime", "prime:b2", "Prime 2", True), Source("youtubetv", "youtubetv:alt", "Alt", True),
            Source("fubo", "fubo:b", "Fubo", False)]
    e.where["b"] = Pick("prime:b", "Prime", "sure", True, provider="prime", alternates=alts)
    e.queue_pins[:] = ["b"]
    cands = cands_of(BOTH)
    cands["b"] = cands["b"].__class__(cands["b"].scored, "prime:b")
    e.control.t += 1
    e._preload_standby(cands)
    assert e.control.calls[-1][0] == "prime:b"
    entry = gq.Entry(game="prime:b", routes=(gq.Route("prime", "prime:b"),), level=gq.Level.R1)
    move = gq.on_failure(entry, gq.Failure.WALLED)
    calls = len(e.control.calls)

    async def fail():
        e._on_queue_move("prime:b", move, host="b:5555")
        assert len(e.control.calls) == calls   # still inside the preload task: nothing tuned yet
        await asyncio.sleep(0)
    asyncio.run(fail())
    assert {"prime:b", "prime:b2"} <= set(e._queue_off)
    assert "youtubetv:alt" not in e._queue_off
    assert e.control.calls[-1][0] == "youtubetv:alt"
    assert not any(c[0].startswith("prime:") for c in e.control.calls[calls:])


def test_a_move_that_waits_does_not_replan(rig):  # noqa: F811
    e = rig()
    plan_of(e)
    move = gq.Move(gq.Failure.STICK_HUNG, gq.Step.WAIT_STICK, None, advance=False)
    calls = len(e.control.calls)

    async def fail():
        e._on_queue_move(target("b"), move)
        await asyncio.sleep(0)
    asyncio.run(fail())
    assert len(e.control.calls) == calls and not e._queue_off
