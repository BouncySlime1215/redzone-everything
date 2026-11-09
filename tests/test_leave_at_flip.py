"""[LEAVE-AT-FLIP] legal.leave_at_flip: a cut the leave or the landing refuses is listed, remembered, and goes only when both allow it.

Why: the veto audit showed the list-time leave gate cost the most must-see value, while the same rule runs again at the flip. These
run the real leave rules (redzone/decider/gate.py) on real Timelines: if the check that keeps a held cut waiting goes away, they fail.
"""
from collections import Counter, deque
from types import SimpleNamespace as NS

import pytest

from redzone.contracts import brain
from redzone.decider import gate
from redzone.decider.decider import Candidate, Decider, Decision, LeaveHold, State
from redzone.decider.screen import Delay, Event, Timeline
from redzone.models import League
from redzone.noggin import arbiter as A
from redzone.noggin import legal
from redzone.scoring.engine import Scored

ROWS = (legal.GameRow("on"), legal.GameRow("a"), legal.GameRow("b"))


def facts(**kw):
    return legal.Facts(**{"now": 100.0, "current": "on", "games": ROWS, "gate_ok": False, "leave_at_flip": True, **kw})


def scored(league=League.NFL, **kw):
    s = Scored.__new__(Scored)
    s.__dict__.update({"personal": 0, "crunch": False, "crunch_now": False, "high_leverage": False, "late": False, "lock": None,
                       "league": league, "garbage": False, "score": 50.0, **kw})
    return s


def cand(gid, league=League.NFL, **kw):
    x = Candidate.__new__(Candidate)
    x.__dict__.update(scored=scored(league, **kw), target=f"yt:{gid}")
    return x


class Cfg:
    def __init__(self, **v):
        self.v = {"arbiter.enabled": True, "legal.leave_at_flip": True, "decider.uncalibrated_sigma_s": 8.0, "legal.leave_wait_s": 60.0, **v}

    def get(self, k, d=None):
        return self.v.get(k, d)


class Scores:
    """A brain with fixed scores; set `.s` between ticks."""
    name = "t"

    def __init__(self, **s):
        self.s = s

    def think(self, inp):
        ids = {o.id for o in inp.options}
        return brain.BrainOutput(tick=inp.tick, model_id="t", made_at=inp.now, ttl_s=5.0, scores={k: v for k, v in self.s.items() if k in ids})


def nfl_screen(fresh):
    """A play that airs on screen around t=130 (35 s behind the feed): shut at 130, open from about 160."""
    tl = Timeline(League.NFL)
    tl.fresh_at = fresh
    tl.events.append(Event(100.0, "play"))
    return tl


def make(tl, flag=True, **cfg):
    d = Decider.__new__(Decider)
    d.cfg = Cfg(**{"legal.leave_at_flip": flag}, **cfg)
    d.current, d.replaying, d.after_replay, d.safe_mode, d.entry_cost = "on", False, False, False, None
    d.user = NS(paused=False)
    d.log, d.history, d._yours, d._known, d._target_of, d._crunch_tail, d._home = [], deque(), set(), {}, {}, {}, None
    d.timelines = {"on": tl}
    d.delays = NS(get=lambda t: Delay(35.0, 1.0))
    d.arbiter_log = Counter()
    d.arbiter_brain = Scores(**{"stay": 10.0, "cut:a": 60.0, "cut:b": 20.0})
    d._clock = lambda: 0.0
    d._home_restore = lambda *a: None
    d._arbiter_plan = lambda *a: None
    d._arbiter_musts = lambda *a: ()
    d._arbiter_show = lambda c, n, p: A.Show(current="on", current_class=2, live_games=3)
    d.facts = {}
    d.legal_facts = lambda c, n: facts(now=n, **{"leave_at_flip": flag, **d.facts})
    return d


CANDS = {"on": cand("on"), "a": cand("a"), "b": cand("b")}


def stay(now):
    return Decision(now, "stay", "on", "nothing better", State.WATCHING)


def tick(d, now, cands=CANDS, base=None):
    if d.timelines["on"].fresh_at is not None:
        d.timelines["on"].fresh_at = now
    out = d._arbiter(base or stay(now), cands, now, (None, None))
    d._leave_settle(out, now)
    return out


# ----- the list -----------------------------------------------------------------------------------------------------

def test_flag_off_the_list_is_todays():
    assert legal.legal_options(facts(leave_at_flip=False)) == (legal.STAY,)
    rows = (legal.GameRow("on"), legal.GameRow("a", landable=False))
    assert [o.id for o in legal.legal_options(facts(leave_at_flip=False, gate_ok=True, games=rows))] == ["stay"]


def test_flag_on_a_shut_gate_or_no_landing_stays_listed():
    rows = (legal.GameRow("on"), legal.GameRow("a", landable=False))
    assert [o.id for o in legal.legal_options(facts(games=rows))] == ["stay", "cut:a"]


@pytest.mark.parametrize("kw", [{"moment_lock": "empty net"}, {"paused": True}, {"safe_mode": True}, {"arrival_until": 200.0},
                                {"games": (legal.GameRow("a", dark=True),)}, {"games": (legal.GameRow("a", tunable=False),)},
                                {"games": (legal.GameRow("a", app="peacock"),)}, {"focus_game": "zz"}])
def test_flag_on_every_other_refusal_still_refuses(kw):
    assert all(o.id != "cut:a" for o in legal.legal_options(facts(**kw)))
    assert legal.refusals(facts(**kw), "a")


def test_refusals_stay_in_step_with_the_list():
    rows = (legal.GameRow("on"), legal.GameRow("a", landable=False))
    assert legal.refusals(facts(games=rows), "a") == ()
    assert legal.refusals(facts(leave_at_flip=False, games=rows), "a") == ("leave_gate", "no_landing")


# ----- the leave rules, from the decider -------------------------------------------------------------------------

def test_flip_gate_and_the_wait_use_the_same_leave_rules():
    assert [n for n, _ in gate.LEAVE_RULES] == ["hold", "ending_airing", "replay_exit", "leave"]
    assert all(r in gate.GATE_RULES for r in gate.LEAVE_RULES)


def nba_hl(clock_running, **kw):
    """An NBA screen whose data reads an open stoppage. With clock_running, the play-by-play proves the clock ran across the leave."""
    tl = Timeline(League.NBA)
    tl.fresh_at = 200.0
    tl.events.append(Event(155.0, "stop_start", "halftime"))
    if clock_running:
        tl.clock_events.extend([(150.0, 4, 120.0, "shot"), (180.0, 4, 90.0, "shot")])
    return tl


def test_high_leverage_play_on_screen_means_no_leave():
    """The hole _leaves_high_leverage_play closes: the window reads open, the clock was running, the game is close and late."""
    d = make(nba_hl(True))
    on = cand("on", League.NBA, high_leverage=True)
    c = {"on": on, "a": cand("a"), "b": cand("b")}
    assert gate.DeciderHost(d, c, 200.0).safe_to_leave("on", required_s=8.0)     # the window alone would let it go
    assert gate.leave_veto(d, Decision(200.0, "switch", "a", "", State.WATCHING, kind="better"), c, 200.0) == "leave"
    out = d._arbiter(stay(200.0), c, 200.0, (None, None))
    assert (out.action, out.want) == ("stay", "a") and d.leave_m.is_("waiting")
    for t in (205.0, 210.0, 215.0):
        d.timelines["on"].clock_events.extend([(t - 20.0, 4, 80.0, "shot"), (t, 4, 70.0, "shot")])
        d.timelines["on"].fresh_at = t
        assert d._arbiter(stay(t), c, t, (None, None)).action == "stay"
    d._leave_settle(stay(215.0), 215.0)
    assert d.leave_m.is_("waiting")


def test_same_screen_without_the_high_leverage_leaves():
    d = make(nba_hl(True))
    c = {"on": cand("on", League.NBA), "a": cand("a"), "b": cand("b")}
    assert gate.leave_veto(d, Decision(200.0, "switch", "a", "", State.WATCHING, kind="better"), c, 200.0) == ""


def test_a_play_on_screen_holds_and_the_wait_ends_in_a_clean_cut():
    """Low leverage: the cut waits out the play, and goes at the dead ball, as the switch the flip judges."""
    d = make(nfl_screen(130.0))
    assert (o := tick(d, 130.0)).action == "stay" and o.want == "a" and "waiting (leave)" in o.reason
    assert d.leave_m.is_("waiting") and d.leave_m.key == "a"
    assert tick(d, 140.0).action == "stay"
    out = tick(d, 175.0)
    assert (out.action, out.target, out.kind, out.leave_held) == ("switch", "a", "better", True)
    assert d.leave_m.is_("idle")


def test_the_pick_is_remembered_not_chosen_again():
    d = make(nfl_screen(130.0))
    tick(d, 130.0)
    d.arbiter_brain.s = {"stay": 10.0, "cut:a": 40.0, "cut:b": 90.0}    # the brain now likes b more; a is still worth it
    assert tick(d, 140.0).want == "a"
    assert tick(d, 175.0).target == "a"


def test_no_landing_waits_and_never_joins_until_one_comes():
    """The screen may be left, but the target has no dead-ball landing: no join, the pick waits, and goes at the next clean one."""
    d = make(nfl_screen(175.0))
    no = (legal.GameRow("on"), legal.GameRow("a", landable=False), legal.GameRow("b"))
    d.facts = {"games": no, "gate_ok": True}
    assert (o := tick(d, 175.0)).action == "stay" and "no_landing" in o.reason and d.leave_m.is_("waiting")
    assert tick(d, 180.0).action == "stay"
    d.facts = {"gate_ok": True}
    assert (tick(d, 185.0).action, d.leave_m.state) == ("switch", "idle")


def test_flip_veto_with_an_open_list_gate_is_held_too():
    """gate:leave: the list-time gate reads open, the flip's leave rule doesn't. Held, not dropped."""
    d = make(nfl_screen(130.0))
    d.facts = {"gate_ok": True}
    assert tick(d, 130.0).action == "stay" and d.leave_m.is_("waiting")
    assert tick(d, 175.0).target == "a"


@pytest.mark.parametrize("dead", [{"games": (legal.GameRow("on"), legal.GameRow("a", dark=True), legal.GameRow("b"))},
                                  {"games": (legal.GameRow("on"), legal.GameRow("a", tunable=False), legal.GameRow("b"))},
                                  {"games": (legal.GameRow("on"), legal.GameRow("b"))},
                                  {"moment_lock": "red zone"}])
def test_target_goes_dead_during_the_wait(dead):
    d = make(nfl_screen(130.0))
    tick(d, 130.0)
    d.facts = dead
    d.arbiter_brain.s = {"stay": 90.0}
    assert tick(d, 175.0).action == "stay"
    assert d.leave_m.is_("idle") and d._leave_pick is None


def test_a_pick_no_longer_worth_it_expires():
    d = make(nfl_screen(130.0))
    tick(d, 130.0)
    d.arbiter_brain.s = {"stay": 90.0, "cut:a": 20.0}
    assert tick(d, 175.0).action == "stay" and d.leave_m.is_("idle")


def test_a_floor_pick_expires_when_its_must_show_is_no_longer_owed():
    d = make(nfl_screen(130.0))
    d._leave_begin(LeaveHold("cut:a", "a", "better", "floor:td", "on", 130.0), "leave", 130.0)
    d.arbiter_brain.s = {"stay": 90.0}
    assert tick(d, 175.0).action == "stay" and d.leave_m.is_("idle")


def test_the_wait_is_bounded():
    d = make(nfl_screen(130.0), **{"legal.leave_wait_s": 20.0})
    tick(d, 130.0)
    d.arbiter_brain.s = {"stay": 90.0}
    assert tick(d, 151.0).action == "stay" and d.leave_m.is_("idle")


def test_the_screen_moving_ends_the_wait():
    d = make(nfl_screen(130.0))
    tick(d, 130.0)
    d._current = "b"
    d.__dict__["current"] = "b"
    d.arbiter_brain.s = {"stay": 90.0}
    assert tick(d, 140.0).action == "stay" and d.leave_m.is_("idle")


def test_a_hard_switch_ends_the_wait_and_goes():
    d = make(nfl_screen(130.0))
    tick(d, 130.0)
    hard = Decision(135.0, "switch", "b", "crunch", State.WATCHING, kind="crunch")
    assert tick(d, 135.0, base=hard) is hard and d.leave_m.is_("idle")


def test_a_later_stage_holding_the_release_keeps_the_pick():
    d = make(nfl_screen(130.0))
    tick(d, 130.0)
    d.timelines["on"].fresh_at = 175.0
    out = d._arbiter(stay(175.0), CANDS, 175.0, (None, None))
    assert out.action == "switch"
    d._leave_settle(stay(175.0), 175.0)    # a later stage (_time_entry, _dwell) turned it into a stay
    assert d.leave_m.is_("waiting")


def test_a_catch_up_replay_still_reads_the_flag_off_list():
    """The catch-up treats an empty list as 'the leave gate said no': the flag's extra cuts must not open it."""
    d = make(nfl_screen(130.0))
    d.arbiter_brain.s = {"stay": 90.0, "cut:a": 20.0}
    seen = []
    d._arbiter_catchup = lambda c, opts, s, n, p: seen.append(opts)
    assert tick(d, 130.0).action == "stay"
    assert seen == [(legal.STAY,)]


def test_flag_off_nothing_waits():
    d = make(nfl_screen(130.0), flag=False)
    assert tick(d, 130.0).action == "stay" and d._leave_pick is None and d.leave_m.is_("idle")


def test_the_engine_sends_a_held_cut_through_the_commit_gate(tmp_path, monkeypatch):
    from test_p1_rootcause import _engine
    e = _engine(tmp_path)
    got = {}
    monkeypatch.setattr(e.control, "submit", lambda target, back, done, **kw: got.update(done=done, **kw))
    asked = []
    monkeypatch.setattr(gate, "commit_gate", lambda *a, **k: asked.append(a[1]) or "leave")
    d = Decision(100.0, "switch", "g2", "x", State.WATCHING, kind="better", leave_held=True)
    assert e.cfg.get("commit_gate.enabled", False) is False
    e._act(d, "g1", {})
    assert got["may_cut"]() is False and got["may_cut"].veto == "leave" and asked == [d]
