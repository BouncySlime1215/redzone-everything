"""Safety playbooks SAFE-01/02/03/05 (redzone/decider/playbooks.py): each turns a switch into a hold."""
from types import SimpleNamespace as NS

import pytest

from redzone.decider import playbooks as pb
from redzone.decider.cuts import EntryPlan, Landing


class Clock:
    def __init__(self):
        self.wall, self.mono = 1_000_000.0, 500.0

    def time(self):
        return self.wall

    def monotonic(self):
        return self.mono

    def step(self, s, skew=0.0):
        self.wall += s + skew
        self.mono += s


class Cfg(dict):
    def get(self, k, default=None):
        return super().get(k, default)


@pytest.fixture
def clk(monkeypatch):
    c = Clock()
    monkeypatch.setattr(pb, "time", c)
    return c


def world(clk, sigma=1.0, plan=None, stale=False, fresh_age=2.0):
    tl = NS(fresh_at=clk.wall - fresh_age)
    cands = {"A": NS(target="yt:a", stale=False), "B": NS(target="yt:b", stale=stale)}
    dec = NS(current="A", timelines={"A": NS(fresh_at=clk.wall), "B": tl}, _known=cands,
             delays=NS(get=lambda t: NS(current_sigma=lambda now: sigma)),
             entry_plan=lambda gid, c, now: plan)
    return dec, cands


def switch(kind="better", target="B"):
    return NS(action="switch", target=target, kind=kind, reason="x")


def safety(**kw):
    return pb.Safety(Cfg({"playbooks.safe.enabled": True, "polling.live_s": 10, **kw}))


def test_off_by_default():
    assert not pb.Safety(Cfg()).enabled()


def test_safe01_no_cut_into_a_stale_feed(clk):
    s = safety()
    dec, cands = world(clk, fresh_age=25.0)          # 25 s > 10 s cadence + 10 s
    hit = s.check(dec, switch(), cands, clk.wall)
    assert hit and hit[0] == "SAFE-01"
    dec, cands = world(clk, fresh_age=12.0)
    assert s.check(dec, switch(), cands, clk.wall) is None
    dec, cands = world(clk, stale=True)
    assert s.check(dec, switch(), cands, clk.wall)[0] == "SAFE-01"


def test_buttons_and_first_pick_are_never_held(clk):
    s = safety()
    dec, cands = world(clk, stale=True)
    assert s.check(dec, switch(kind="user"), cands, clk.wall) is None
    dec.current = None
    assert s.check(dec, switch(), cands, clk.wall) is None


@pytest.mark.playbook("SAFE-02")
def test_safe02_wide_sigma_needs_a_sure_dead_ball(clk):
    s = safety()
    centred = EntryPlan(True, 0.0, Landing("dead", "huddle"), "dead ball (centred)")
    dec, cands = world(clk, sigma=12.0, plan=centred)
    assert s.check(dec, switch(), cands, clk.wall)[0] == "SAFE-02"
    dec, cands = world(clk, sigma=12.0, plan=None)
    assert s.check(dec, switch(), cands, clk.wall)[0] == "SAFE-02"
    sure = EntryPlan(True, 0.0, Landing("dead", "huddle"), "dead ball")
    dec, cands = world(clk, sigma=12.0, plan=sure)
    assert s.check(dec, switch(), cands, clk.wall) is None
    dec, cands = world(clk, sigma=8.0, plan=centred)
    assert s.check(dec, switch(), cands, clk.wall) is None


def test_safe03_degraded_network_cuts_only_into_long_breaks(clk):
    s = safety()
    s.condition("yt:a", 13.0)                       # 3 buffering events a minute
    huddle = EntryPlan(True, 0.0, Landing("dead", "huddle"), "dead ball")
    dec, cands = world(clk, plan=huddle)
    assert s.check(dec, switch(), cands, clk.wall)[0] == "SAFE-03"
    tv = EntryPlan(True, 0.0, Landing("dead", "tv_timeout"), "dead ball")
    dec, cands = world(clk, plan=tv)
    assert s.check(dec, switch(), cands, clk.wall) is None
    clk.step(400)                                    # the condition is old news
    dec, cands = world(clk, plan=huddle)
    assert s.check(dec, switch(), cands, clk.wall) is None


@pytest.mark.playbook("SAFE-05")
def test_safe05_clock_jump_holds_until_reanchored(clk):
    s = safety()
    s.tick()
    clk.step(1, skew=30.0)                           # NTP fix: wall +31 s, monotonic +1 s
    s.tick()
    dec, cands = world(clk)
    hit = s.check(dec, switch(), cands, clk.wall)
    assert hit and hit[0] == "SAFE-05"
    clk.step(5)
    for tl in dec.timelines.values():
        tl.fresh_at = clk.wall + 1                   # every feed polled since
    assert s.check(dec, switch(), cands, clk.wall) is None
    assert s.skew_at is None


@pytest.mark.playbook("SAFE-05")
def test_safe05_gives_up_after_reanchor_s(clk):
    s = safety(**{"playbooks.safe.reanchor_s": 60})
    s.tick()
    clk.step(1, skew=-20.0)
    s.tick()
    dec, cands = world(clk)
    assert s.check(dec, switch(), cands, clk.wall)[0] == "SAFE-05"
    clk.step(61)
    dec.timelines["B"].fresh_at = clk.wall
    assert s.check(dec, switch(), cands, clk.wall) is None


def test_decider_writes_the_playbook_reason(clk):
    from redzone.decider.decider import Decider, Decision, State
    from redzone.decider.screen import DelayBook
    dec = Decider(Cfg({"playbooks.safe.enabled": True, "polling.live_s": 10}), DelayBook({}, 15.0))
    dec.current = "A"
    cands = {"A": NS(target="yt:a", stale=False), "B": NS(target="yt:b", stale=True)}
    d = Decision(clk.wall, "switch", "B", "better game", State.WATCHING, kind="better")
    out = dec._safe(d, cands, clk.wall, (dec.home_break_visit, dec.home_return_at))
    assert out.action == "stay" and out.target == "A" and out.want == "B"
    assert out.reason.startswith("playbook=SAFE-01")
    assert dec.safety.seen == {"SAFE-01": 1}


@pytest.mark.playbook("SAFE-05")
def test_safe05_backward_jump_waits_for_a_fresh_poll(clk):
    s = safety()
    s.tick()
    dec, cands = world(clk)
    clk.step(1, skew=-30.0)                          # the clock steps back: old stamps look "later"
    s.tick()
    assert s.check(dec, switch(), cands, clk.wall)[0] == "SAFE-05"
    for tl in dec.timelines.values():
        tl.fresh_at = clk.wall
    assert s.check(dec, switch(), cands, clk.wall) is None


@pytest.mark.playbook("NET-01")
def test_net01_offline_holds_every_cut_replays_too(clk):
    s = safety()
    dec, cands = world(clk, fresh_age=40.0)
    dec.timelines["A"] = NS(fresh_at=clk.wall - 40.0)       # the game on screen is stale too: offline
    replay = NS(action="replay", target="B", kind="replay", reason="x")
    assert s.check(dec, replay, cands, clk.wall)[0] == "NET-01"
    assert s.check(dec, switch(), cands, clk.wall)[0] == "NET-01"
    dec.timelines["A"] = NS(fresh_at=clk.wall)               # on screen fresh: a replay is not this rule's
    assert s.check(dec, replay, cands, clk.wall) is None
    assert s.check(dec, switch(), cands, clk.wall)[0] == "SAFE-01"   # ...and the stale target is still SAFE-01
