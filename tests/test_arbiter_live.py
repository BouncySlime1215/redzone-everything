"""ARBITER-LIVE: a must-show that aired unseen goes out as an ordinary replay decision, only behind arbiter.enabled.

Why: the max-rate sim's catch-up had no live wiring. These pin the pure pick, that nothing changes with the flag off, and
that the floor, L1, the leave gate and the replay queue's own limits all still come first.
"""
from dataclasses import replace

from redzone.contracts import brain
from redzone.decider.decider import Candidate, Decider, Decision, Missed, State
from redzone.decider.screen import DelayBook, Event
from redzone.models import League
from redzone.noggin import arbiter as A
from redzone.noggin import legal
from redzone.scoring.engine import Scored

NOW = 1000.0
STAY = legal.STAY
SHOW = A.Show(current="on", current_class=2, live_games=3)


def cut(g, cls=2):
    return brain.Option(id=f"cut:{g}", verb="cut", game_id=g, l1_class=cls)


# ------------------------------------------------------------ the pure pick

def test_touchdown_goes_before_a_lesser_must_show_then_the_oldest():
    clips = [("gain", A.trigger_rank("nfl", "gain10"), NOW - 30.0), ("td_new", 0, NOW - 20.0), ("td_old", 0, NOW - 90.0)]
    assert A.catchup_pick(clips, (STAY, cut("x")), SHOW, NOW) == "td_old"


def test_not_a_must_show_is_never_a_catchup():
    assert A.catchup_pick([("x", A.NOT_FLOOR, NOW - 5.0)], (STAY, cut("x")), SHOW, NOW) is None


def test_past_catchup_s_it_is_gone():
    clips = [("td", 0, NOW - 601.0)]
    assert A.catchup_pick(clips, (STAY, cut("x")), SHOW, NOW) is None
    assert A.catchup_pick([("td", 0, NOW - 599.0)], (STAY, cut("x")), SHOW, NOW) == "td"


def test_zero_turns_it_off():
    p = replace(A.DEFAULT_PARAMS, catchup_s=0.0)
    assert A.catchup_pick([("td", 0, NOW - 5.0)], (STAY, cut("x")), SHOW, NOW, p) is None


def test_no_legal_cut_means_the_leave_gate_said_no():
    assert A.catchup_pick([("td", 0, NOW - 5.0)], (STAY,), SHOW, NOW) is None


def test_your_team_or_crunch_keeps_the_tv():
    clips = [("td", 0, NOW - 5.0)]
    assert A.catchup_pick(clips, (STAY, cut("x")), replace(SHOW, current_class=1), NOW) is None
    assert A.catchup_pick(clips, (STAY, cut("x", cls=1)), SHOW, NOW) is None


def test_ceiling_and_last_game_stop_it():
    clips = [("td", 0, NOW - 5.0)]
    assert A.catchup_pick(clips, (STAY, cut("x")), replace(SHOW, cuts_60s=3), NOW) is None
    assert A.catchup_pick(clips, (STAY, cut("x")), replace(SHOW, live_games=1), NOW) is None


# ------------------------------------------------------------ the decider stage

class Cfg:
    def __init__(self, **v):
        self.v = {"arbiter.enabled": True, "decider.crunch.hold_personal": 5, "replay.apps": ["yt"], **v}

    def get(self, k, d=None):
        return self.v.get(k, d)


def scored(**kw):
    s = Scored.__new__(Scored)
    s.__dict__.update(personal=0, crunch=False, crunch_now=False, high_leverage=False, late=False, lock=None, **kw)
    return s


def cand(gid, **kw):
    x = Candidate.__new__(Candidate)
    x.__dict__.update(scored=scored(**kw), target=f"yt:{gid}")
    return x


def timeline(pid="p1", kind="td"):
    return type("T", (), {"league": League.NFL, "events": [Event(NOW - 60.0, "play", "TD", pid=pid, play_kind=kind)]})()


def missed(gid="a", pid="p1", age=60.0, **kw):
    return Missed(gid, pid, "TD", "td", NOW - age, 75.0, **kw)


def make(on=True, **cfg):
    d = Decider.__new__(Decider)
    d.cfg = Cfg(**{"arbiter.enabled": on}, **cfg)
    d.current, d.replaying, d.after_replay, d.safe_mode = "on", False, False, False
    d.user = type("U", (), {"paused": False})()
    d.log, d.history = [], __import__("collections").deque()
    d.timelines, d._yours, d.entry_cost = {"a": timeline()}, set(), None
    d.arbiter_brain, d.arbiter_log = A.NullBrain(), __import__("collections").Counter()
    d.delays = DelayBook.__new__(DelayBook)
    d.delays.get = lambda t: type("D", (), {"mu": 30.0})()
    d._home_restore = lambda *a: None
    d._clock = lambda: NOW
    d._known, d._target_of, d._crunch_tail, d._home = {}, {}, {}, None
    d.queue = [missed()]
    return d


CANDS = {"on": cand("on"), "a": cand("a"), "b": cand("b")}
OPTS = (STAY, cut("a"), cut("b"))


def stay():
    return Decision(NOW, "stay", "on", "nothing better", State.WATCHING)


def stage(d, base=None, opts=OPTS, cands=CANDS):
    d.legal_facts = lambda c, n: None
    real = legal.legal_options
    legal.legal_options = lambda f: opts
    try:
        return d._arbiter(base or stay(), cands, NOW, (None, None))
    finally:
        legal.legal_options = real


def test_flag_off_reads_nothing_and_changes_nothing():
    d = make(on=False)
    base = stay()
    assert stage(d, base) is base and d.queue and not d.arbiter_log


def test_a_missed_touchdown_goes_out_as_an_ordinary_replay_decision():
    d = make()
    out = stage(d)
    assert (out.action, out.kind, out.target) == ("replay", "replay", "a")     # the replay kind the commit gate already judges
    assert out.replay is d.queue[0] and out.gate.startswith("arbiter: catch-up")
    assert d.arbiter_log["catchup"] == 1


def test_a_reachable_live_must_show_goes_first():
    d = make()
    d.timelines["b"] = type("T", (), {"league": League.NFL, "events": [Event(NOW + 40.0, "play", "TD", pid="p2", play_kind="td")]})()
    out = stage(d)
    assert out.action == "switch" and out.target == "b" and out.kind == "better"


def test_a_switch_the_tick_already_chose_is_left_alone():
    d = make()
    base = Decision(NOW, "switch", "b", "better game", State.WATCHING, kind="better")
    assert stage(d, base) is base


def test_a_hard_kind_is_never_touched():
    d = make()
    base = Decision(NOW, "switch", "b", "crunch", State.WATCHING, kind="crunch")
    assert stage(d, base) is base


def test_the_leave_gate_saying_no_keeps_the_stay():
    d = make()
    assert stage(d, opts=(STAY,)).action == "stay"


def test_a_play_not_on_the_floor_is_left_to_the_ordinary_replay_path():
    d = make()
    d.timelines["a"] = timeline(kind="")
    assert stage(d).action == "stay"


def test_a_row_the_timeline_lost_fails_closed():
    d = make()
    d.queue = [missed(pid="gone")]
    assert stage(d).action == "stay"


def test_buffered_jump_keeps_its_hidden_stick():
    d = make(**{"jump_mode.enabled": True, "buffered_jump.enabled": True})
    assert stage(d).action == "stay"


def test_youtube_tv_past_its_scrub_reach_is_not_picked():
    d = make(**{"replay.apps": ["youtubetv"], "replay.scrub_max_play_age_s": 95})
    cands = {**CANDS, "a": Candidate.__new__(Candidate)}
    cands["a"].__dict__.update(scored=scored(), target="youtubetv:a")
    d.queue = [missed(age=200.0)]
    assert stage(d, cands=cands).action == "stay"
    d.queue = [missed(age=60.0)]
    assert stage(d, cands=cands).action == "replay"


def test_high_leverage_or_a_locked_moment_on_screen_keeps_the_screen():
    for kw in ({"high_leverage": True}, {"crunch_now": True}):
        cands = {**CANDS, "on": cand("on")}
        cands["on"].scored.__dict__.update(kw)
        assert stage(make(), cands=cands).action == "stay"
    cands = {**CANDS, "on": cand("on")}
    cands["on"].scored.__dict__.update(lock="red zone")
    assert stage(make(), cands=cands).action == "stay"


def test_a_break_visit_in_progress_keeps_its_way_home():
    d = make()
    d._home = "on"
    assert stage(d).action == "stay"


def test_a_play_seen_live_is_not_a_catchup():
    d = make()
    d.queue = [missed(seen_live=True)]
    assert stage(d).action == "stay"
