"""Queue failover (WP-12C): a ready backup route on the hidden stick is what a switch to that game resolves to.

Why: the queue could fill the hidden stick with a backup after a game's main route failed, but the decider still wanted
the primary, so nothing could show it. These run the real Engine.decide, _act and commit gate on modeled sticks: the
switch goes to the backup at a dead ball, a mid-play moment still holds, and blocked routes stay off. No TV, no adb.
"""
import dataclasses
import time
import types

import pytest
from test_queue_wire import ShadowControl, cands_of, rig, sc  # noqa: F401
from test_rule1_review import _plan

from redzone.channels import Where
from redzone.decider import gate
from redzone.decider.decider import Candidate, Decision, State
from redzone.decider.screen import Timeline
from redzone.discovery.providers import Pick, Source
from redzone.models import GameState, League, Phase, Team

MAIN, BACKUP = "prime:b", "youtubetv:alt"


class Sticks(ShadowControl):
    """The modeled sticks, plus the door a real switch goes through. Records what was submitted; it never cuts."""
    def __init__(self):
        super().__init__()
        self.down, self.submitted = set(), []
        self.job = ""

    @property
    def hidden_target(self):
        return self.stick.target

    def can_tune(self, t):
        return bool(t) and t not in self.down

    def submit(self, target, back, done, **kw):
        self.submitted.append((target, kw["may_cut"]))

    def deadline_cost(self, t):
        return 3.0

    def __getattr__(self, name):   # what the rest of decide() reads and this test has no view on
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *a, **k: None


def pick(alts=None):
    return Pick(MAIN, "Prime", "sure", True, provider="prime", alternates=alts if alts is not None else [
        Source("youtubetv", BACKUP, "ESPN on YouTube TV", True)])


@pytest.fixture
def scene(rig, monkeypatch):  # noqa: F811
    """Game a on screen, game b's main route down, the queue told to fetch b (a pin): the backup goes on the hidden stick."""
    def make(on=True, route=None, down=True):
        e = rig(on=on)
        e.cfg.update({"commit_gate": {"enabled": True}})
        ctl = e.control = Sticks()
        e.planner.clock = lambda: ctl.t
        ctl.on_target = "youtubetv:cha"
        w = route or pick()
        e.where["a"] = Where("youtubetv:cha", "A", "sure", True)
        e.discovery = types.SimpleNamespace(choose=lambda g, _: w if g.game_id == "b" else e.where["a"],
                                            on_outcome=lambda *a, **k: None)
        e.supervisor = types.SimpleNamespace(route=lambda gid, where: where)
        e.channels = types.SimpleNamespace(where=lambda g, sunday_ticket=False: None, yttv_code=lambda t: None)
        e.merger.games.update({g: GameState(League.NBA, g, Team("WSH", score=50), Team("BOS", score=48), phase=Phase.LIVE,
                                             period=4, clock_s=300.0) for g in "ab"})
        e.merger.is_stale = lambda gid: False
        e.personal = lambda: None
        e._sync_switch_costs = lambda: None
        e.scorer = types.SimpleNamespace(score=lambda g, me: sc(g.game_id, 60), reasoner=None)
        monkeypatch.setattr(e.feeds, "stale", lambda g: False)
        monkeypatch.setattr(e.decider, "_cut_timed", lambda *a: True)
        monkeypatch.setattr(e.decider, "_entry_only", lambda *a: False)
        monkeypatch.setattr(e.decider, "entry_plan", lambda *a, **k: _plan(e.dead_ball))
        monkeypatch.setattr(e, "safe_to_leave", lambda gid, required_s=0.0: True)
        monkeypatch.setattr(e, "_flip_lands_whistle", lambda d, t: True)
        e.dead_ball = True
        e.decider.timelines.update({g: Timeline(League.NBA) for g in "ab"})
        if down:
            ctl.down.add(MAIN)
        e.next = None
        real = e.decider.decide

        def decide(cands, redzone, commit=False, reel=None):
            if e.next is None:
                return dataclasses.replace(real(cands, redzone, commit=commit, reel=reel), action="stay", target="a")
            return e.next
        e.decider.decide = decide
        e.queue_pins[:] = ["b"]
        e.where["b"] = w
        ctl.t += 1
        cands = cands_of({"a": 70, "b": 60})
        cands["b"] = Candidate(cands["b"].scored, MAIN)
        e._preload_standby(cands)
        assert not down or not on or ctl.calls[-1][0] == BACKUP   # the scene: the queue really did fetch the backup
        ctl.t += 60   # the hidden stick has finished loading
        return e
    return make


def switch(e):
    """The next tick decides to go to b: what target did the existing switch resolve to, and may it cut?"""
    e.next = Decision(time.time(), "switch", "b", "better", State.WATCHING, kind="better")
    e.decide()
    target, may_cut = e.control.submitted[-1]
    return target, may_cut


def test_the_backup_is_loaded_and_the_next_switch_flips_to_it_at_a_dead_ball(scene):
    e = scene()
    assert e.control.calls[-1][0] == BACKUP and e.control.standby_ready(BACKUP)
    target, may_cut = switch(e)
    assert target == BACKUP and e.where["b"].target == BACKUP and e.where["b"].tunable
    assert e.where["b"].alternates    # the other routes stay known
    assert may_cut() is True and may_cut.veto == ""


def test_a_mid_play_moment_still_holds_at_the_backup(scene):
    e = scene()
    e.dead_ball = False
    target, may_cut = switch(e)
    assert target == BACKUP
    assert may_cut() is False and may_cut.veto == "landing"
    e.dead_ball = True
    assert may_cut() is True   # the same switch goes at the next dead ball


def test_a_mid_play_leave_still_holds(scene, monkeypatch):
    e = scene()
    monkeypatch.setattr(e, "safe_to_leave", lambda gid, required_s=0.0: False)
    _, may_cut = switch(e)
    assert may_cut() is False and may_cut.veto == "leave"


def test_the_commit_gate_sees_the_backup_as_this_games_route(scene):
    e = scene()
    target, _ = switch(e)
    d = Decision(time.time(), "switch", "b", "better", State.WATCHING, kind="better")
    assert gate.entry_veto(e, d, target) == ""
    assert gate.entry_veto(e, d, MAIN) == "target_moved"   # a stale switch to the dead route is not waved through


@pytest.mark.parametrize("why", ["queue_off", "fail_cool", "not_tunable"])
def test_each_way_the_main_route_goes_down(scene, why):
    e = scene(down=False, route=pick() if why != "not_tunable" else dataclasses.replace(pick(), tunable=False))
    if why == "queue_off":
        e._queue_off[MAIN] = time.time() + 600
    elif why == "fail_cool":
        e._fail_cool[MAIN] = time.time() + 600
    e._queue_loaded["b"] = BACKUP
    e.control.stick.preload(BACKUP, None, e.control.t - 60)
    assert switch(e)[0] == BACKUP


def test_a_healthy_main_route_is_kept_over_a_ready_backup(scene):
    e = scene()
    e.control.down.clear()
    assert switch(e)[0] == MAIN


def test_a_backup_still_loading_is_not_used(scene):
    e = scene()
    e.control.stick.ready_at = e.control.t + 60
    e.decide()
    assert e.where["b"].target == MAIN and not e.control.submitted


@pytest.mark.parametrize("block", ["walled", "failed_switch", "blackout", "gone", "cannot_play"])
def test_a_blocked_backup_stays_off(scene, block):
    e = scene()
    route = {"blackout": pick([Source("youtubetv", BACKUP, "ESPN", True, blackout=True)]), "gone": pick([])}.get(block)
    if route is not None:
        e.discovery.choose = lambda g, _: route if g.game_id == "b" else e.where["a"]
    if block == "walled":
        e._queue_off[BACKUP] = time.time() + 600
    elif block == "failed_switch":
        e._fail_cool[BACKUP] = time.time() + 600
    elif block == "cannot_play":
        e.control.down.add(BACKUP)
    e.decide()
    assert e.where["b"].target == MAIN


def test_a_route_the_queue_did_not_load_for_this_game_is_not_claimed(scene):
    """Another game's channel can be listed as this game's alternate: only the queue's own word makes it this game's."""
    e = scene()
    e._queue_loaded.clear()
    e.decide()
    assert e.where["b"].target == MAIN


def test_the_game_stays_on_its_backup_while_on_screen(scene):
    """The screen's route is the delay every leave reads, even once the main route recovers."""
    e = scene()
    e.decider.current, e.control.on_target = "b", BACKUP
    e.control.stick.preload("youtubetv:cha", None, e.control.t)
    e.control.down.clear()
    e.decide()
    assert e.where["b"].target == BACKUP
    e.decider.current = "a"
    e.control.on_target = "youtubetv:cha"
    e.decide()
    assert e.where["b"].target == MAIN   # off screen and the main route is back


def test_flag_off_resolves_nothing_and_touches_no_stick(scene, monkeypatch):
    e = scene(on=False)
    e._queue_loaded["b"] = BACKUP
    e.control.stick.preload(BACKUP, None, e.control.t - 60)
    monkeypatch.setattr(e, "_queue_failover", lambda *a: pytest.fail("failover ran with the flag off"))
    e.decide()
    assert e.where["b"].target == MAIN
