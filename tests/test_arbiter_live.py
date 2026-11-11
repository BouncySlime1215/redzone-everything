"""ARBITER-LIVE: a must-show that aired unseen goes out as an ordinary replay decision, only behind arbiter.enabled.

Why: the max-rate sim's catch-up had no live wiring. These pin the pure pick, that nothing changes with the flag off, and
that the floor, L1, the leave gate and the replay queue's own limits all still come first.
"""
from dataclasses import replace

from redzone.contracts import brain
from redzone.decider import decider as dec_mod
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
    d._arbiter_plan = lambda *a: None       # no clips: this one is about the catch-up
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


# ------------------------------------------------------------ MUST-LIVE: plan_clips on a buffered picture

def clip(i, game, rank=A.TRIGGER_ORDER.index("fg"), opens=NOW + 5.0, last=NOW + 60.0, ready=NOW + 5.0):
    return A.Clip(i, game, rank, opens, last, 30.0, ready)


def test_the_plan_airs_the_clip_that_dies_first_and_still_gets_the_touchdown():
    clips = [clip("fg", "a"), clip("td", "b", rank=0, opens=NOW + 45.0, last=NOW + 95.0, ready=NOW + 45.0)]
    assert A.plan_game(clips, NOW, 3.2, 15.0) == "a"


def test_a_touchdown_outweighs_a_run_that_cannot_hold_it():
    clips = [clip("fg", "a", last=NOW + 20.0), clip("td", "b", rank=0, opens=NOW + 10.0, last=NOW + 20.0, ready=NOW + 10.0)]
    assert A.plan_game(clips, NOW, 3.2, 15.0) == "b"


def test_the_stick_keeps_its_game_on_a_tie():
    clips = [clip("x", "a"), clip("y", "b")]
    assert A.plan_game(clips, NOW, 40.0, 15.0, keep_game="b") == "b"
    assert A.plan_game(clips, NOW, 40.0, 15.0, keep_game="a") == "a"


def test_no_clips_no_plan():
    assert A.plan_game([], NOW, 3.2, 15.0) is None


def test_planned_orders_the_floor_and_cannot_add_to_it():
    musts = [A.Must("t", "b", "td", 1, 0, NOW + 100.0), A.Must("f", "a", "fg", 2, 6, NOW + 100.0)]
    inp = brain.BrainInput(tick=1, now=NOW, options=(STAY, cut("a"), cut("b"), cut("c")))
    assert A.choose(inp, None, musts=musts, show=SHOW).option.id == "cut:b"
    assert A.choose(inp, None, musts=musts, show=SHOW, planned="cut:a").option.id == "cut:a"
    assert A.choose(inp, None, musts=musts, show=SHOW, planned="cut:c").option.id == "cut:b"     # c owes nothing


def test_leaving_a_play_is_off_by_default_and_the_plan_is_on():
    assert A.DEFAULT_PARAMS.save_leave is False and A.DEFAULT_PARAMS.plan is True
    assert A.Params.from_config({"arbiter.save_leave": True}.get).save_leave is True


def event(age, kind, pid):
    return Event(NOW - age, "play", kind, pid=pid, play_kind=kind)


def rows_legal(**kw):
    return lambda c, n: legal.Facts(now=n, current="on", games=tuple(legal.GameRow(g, app="yt", **kw) for g in c if g != "on"))


def buffered(on=True, **cfg):
    """Two pool games: a field goal on a dying sooner, a touchdown on b. The trigger order says b, the plan says a."""
    d = make(on, **{"jump_mode.enabled": True, "buffered_jump.enabled": True, **cfg})
    d.timelines = {"a": type("T", (), {"league": League.NFL, "events": [event(60.0, "fg", "f1")]})(),
                   "b": type("T", (), {"league": League.NFL, "events": [event(20.0, "td", "t1")]})()}
    d.delays.get = lambda t: type("D", (), {"mu": 110.0, "behind": 75.0})()
    d.entry_cost, d.legal_facts = (lambda t: 15.0), rows_legal()
    d._screen_log, d.arbiter_plan_memo = [], (None, None)
    return d


POOL = {"a": cand("a"), "b": cand("b")}


def test_the_hidden_stick_takes_the_plans_game_not_the_trigger_orders():
    assert buffered()._arbiter_preload(POOL, None, NOW) == "a"


def test_plan_off_is_the_trigger_order():
    assert buffered(**{"arbiter.plan": False})._arbiter_preload(POOL, None, NOW) == "b"


def test_no_buffered_picture_is_the_trigger_order():
    assert buffered(**{"buffered_jump.enabled": False})._arbiter_preload(POOL, None, NOW) == "b"


def test_the_plan_only_takes_a_game_legal_options_allows():
    d = buffered()
    real = rows_legal()
    d.legal_facts = lambda c, n: replace(real(c, n), banned_apps=frozenset({"yt"}))
    assert d._arbiter_plan(POOL, None, NOW, A.DEFAULT_PARAMS) is None
    d = buffered()
    d.legal_facts = lambda c, n: replace(real(c, n), games=tuple(replace(g, dark=g.game_id == "a") for g in real(c, n).games))
    assert d._arbiter_preload(POOL, None, NOW) == "b"


def test_a_stick_may_prepare_during_a_play_but_a_cut_may_not_skip_the_rate_cap():
    d = buffered()
    real = rows_legal()
    d.legal_facts = lambda c, n: replace(real(c, n), gate_ok=False, last_switch_at=NOW - 1.0, min_gap_s=30.0)
    assert d._arbiter_stick_legal(POOL, NOW) == {"a", "b"}
    assert d._arbiter_flip_legal(POOL, NOW) == set()
    d.legal_facts = lambda c, n: replace(real(c, n), gate_ok=False)
    assert d._arbiter_flip_legal(POOL, NOW) == {"a", "b"}         # the buffered editor proved the dead ball on screen
    d.legal_facts = lambda c, n: replace(real(c, n), moment_lock="red zone")
    assert d._arbiter_flip_legal(POOL, NOW) == set()


def test_a_play_the_screen_showed_is_not_planned():
    d = buffered()
    d._screen_log = [(NOW - 500.0, "a"), (NOW + 500.0, "on")]
    assert [c.game_id for c in d._arbiter_plan_clips(POOL, None, NOW, A.DEFAULT_PARAMS)] == ["b"]


def test_a_play_past_the_rewind_is_not_planned_for_a_new_park():
    d = buffered()
    d.timelines["a"].events = [event(100.0, "fg", "f1")]
    assert [c.game_id for c in d._arbiter_plan_clips(POOL, None, NOW, A.DEFAULT_PARAMS)] == ["b"]
    assert sorted(c.game_id for c in d._arbiter_plan_clips(POOL, "a", NOW, A.DEFAULT_PARAMS)) == ["a", "b"]     # already parked


def test_the_plan_is_asked_again_only_when_a_new_clip_arrives(monkeypatch):
    d = buffered()
    calls = []
    real = A.plan_game
    monkeypatch.setattr(A, "plan_game", lambda *a, **k: calls.append(1) or real(*a, **k))
    assert d._arbiter_preload(POOL, None, NOW) == "a"
    assert d._arbiter_preload(POOL, None, NOW + 1.0) == "a" and len(calls) == 1
    d.timelines["b"].events = [*d.timelines["b"].events, event(5.0, "td", "t2")]
    d._arbiter_preload(POOL, None, NOW + 2.0)
    assert len(calls) == 2


def leave_setup(on=True, **cfg):
    """On screen: a low-leverage play nobody owes. Hidden and ready: b, whose touchdown has about 10 s left."""
    d = buffered(on, **cfg)
    d.timelines["b"].events = [event(115.0, "td", "t1")]
    d.timelines["on"] = type("T", (), {"league": League.NFL, "events": [Event(NOW - 110.0, "play", "run", pid="r1", play_kind="")]})()
    d.standby_ready, d._bj_pic = (lambda t: True), (lambda g, x, n: object())
    d._jump_pool = lambda c: {g: x for g, x in c.items() if g != "on"}
    return d


LEAVE = {"on": cand("on"), "b": cand("b")}


def test_leaving_a_play_needs_its_own_switch():
    assert leave_setup()._arbiter_save_leave("on", LEAVE, NOW) is None


def test_with_the_switch_a_dying_touchdown_may_leave_a_low_leverage_play():
    assert leave_setup(**{"arbiter.save_leave": True})._arbiter_save_leave("on", LEAVE, NOW) == "b"


def test_never_from_a_high_leverage_or_crunch_or_locked_game():
    for kw in ({"high_leverage": True}, {"crunch": True}, {"crunch_now": True}, {"lock": "red zone"}):
        on = cand("on")
        on.scored.__dict__.update(kw)
        assert leave_setup(**{"arbiter.save_leave": True})._arbiter_save_leave("on", {**LEAVE, "on": on}, NOW) is None


def test_never_out_of_a_play_the_floor_owes():
    d = leave_setup(**{"arbiter.save_leave": True})
    d.timelines["on"].events = [Event(NOW - 110.0, "play", "TD", pid="r1", play_kind="td")]
    assert d._arbiter_save_leave("on", LEAVE, NOW) is None


def test_no_dying_clip_no_leave():
    d = leave_setup(**{"arbiter.save_leave": True})
    d.timelines["b"].events = [event(20.0, "td", "t1")]
    assert d._arbiter_save_leave("on", LEAVE, NOW) is None


def test_arbiter_off_never_leaves_a_play():
    d = leave_setup(False, **{"arbiter.save_leave": True})
    assert d._arbiter_save_leave("on", LEAVE, NOW) is None


def test_the_flips_leave_check_opens_only_with_the_switch():
    for flag, want in ((False, False), (True, True)):
        d = leave_setup(**{"arbiter.save_leave": flag})
        d._jump_gate = lambda *a, **k: type("G", (), {"safe": False})()
        assert d.jump_may_leave("on", LEAVE, NOW, 3.2) is want


# ---- the editor's own cut

def editor(monkeypatch, why="play_on_screen", lands=True, **cfg):
    """The buffered editor at a tick where the play on screen holds the flip (`why`), b hidden, parked and ready."""
    d = leave_setup(**cfg)
    d.user = type("U", (), {"paused": False, "muted": set()})()
    d.since, d.snap_parked, d.jump_holds = NOW - 30.0, (lambda t: NOW), __import__("collections").Counter()
    d._bj_pic = lambda g, x, n: object()
    d._slate_allows, d._jump_plays_seen, d._bj_flip_cost = (lambda g, n: True), (lambda *a: 1), (lambda t: 3.2)
    d._bj_lands = lambda g, p, c, n: (lands, 4.0)
    monkeypatch.setattr(dec_mod.bj, "frozen", lambda h, n, at: h)
    monkeypatch.setattr(dec_mod.bj, "cut_parked", lambda *a, **k: dec_mod.bj.Cut(False, why))
    return d


def test_the_editor_holds_for_the_play_without_the_switch(monkeypatch):
    d = editor(monkeypatch)
    assert d._buffered_jump(LEAVE["on"], LEAVE, NOW) is False and d.jump_holds["bj_play_on_screen"] == 1


def test_with_the_switch_the_editor_cuts_as_an_ordinary_jump(monkeypatch):
    out = editor(monkeypatch, **{"arbiter.save_leave": True})._buffered_jump(LEAVE["on"], LEAVE, NOW)
    assert (out.action, out.target, out.kind, out.jump["why"]) == ("switch", "b", "jump", "bj_save_leave")   # "jump": the commit gate's path


def test_even_with_the_switch_it_never_joins_mid_play(monkeypatch):
    assert editor(monkeypatch, lands=False, **{"arbiter.save_leave": True})._buffered_jump(LEAVE["on"], LEAVE, NOW) is False


def test_the_switch_only_answers_a_play_on_screen(monkeypatch):
    d = editor(monkeypatch, why="min_plays", **{"arbiter.save_leave": True})
    assert d._buffered_jump(LEAVE["on"], LEAVE, NOW) is False


def test_the_editor_holds_when_legal_options_has_no_cut_to_the_stick(monkeypatch):
    d = editor(monkeypatch)
    real = rows_legal()
    d.legal_facts = lambda c, n: replace(real(c, n), banned_apps=frozenset({"yt"}))
    assert d._buffered_jump(LEAVE["on"], LEAVE, NOW) is False and d.jump_holds["bj_not_legal"] == 1


def test_arbiter_off_the_editor_reads_no_legal_options(monkeypatch):
    d = editor(monkeypatch)
    d.cfg.v["arbiter.enabled"] = False
    d.legal_facts = None      # would raise if read
    assert d._buffered_jump(LEAVE["on"], LEAVE, NOW) is False and d.jump_holds["bj_play_on_screen"] == 1
