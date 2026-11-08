"""[state machines] The decider's implicit states made explicit (redzone/machines/decider.py), and the bugs that the
loose flags allowed: each test here failed before its fix."""
from collections import deque

from redzone.decider.decider import Candidate, Decider, Decision, Missed, State
from redzone.decider.screen import DelayBook, Event
from redzone.models import League
from redzone.scoring.engine import Scored

MU = 35.0


def sc(gid, score, league=League.CFB):
    return Scored(gid, league, score, score, score, 1.0, 0.0, f"reason {gid}", [], False, None, None, 1.0)


def cands(**games):
    return {gid: Candidate(sc(gid, s), f"youtubetv:{gid}") for gid, s in games.items()}


def setup(cfg, clock, jump=True):
    cfg.update({"jump_mode": {"enabled": jump}})
    delays = DelayBook({"youtubetv": MU}, sigma_default=15.0, clock=clock)
    dec = Decider(cfg, delays, clock=clock)
    dec.deadline_cost = lambda target: 4.0
    for gid in ("a", "b", "c"):
        for v in (MU - 0.5, MU, MU + 0.5):
            delays.calibrate(f"youtubetv:{gid}", v)
        dec.timeline(gid, League.CFB)._lags.append(3.0)
    dec.current, dec.since = "a", clock.t - 600
    return dec


def only_normal(dec, d_of):
    """The normal decider proposes d_of(now); every soft filter passes it through (the handoff alone is tested)."""
    dec._decide = lambda cands, now: d_of(now)
    for name in ("_scenario", "_clairvoyant", "_crunch_guard", "_hold_for_big", "_time_entry", "_dwell", "_safe"):
        setattr(dec, name, lambda d, *a, **k: d)
    dec._pick = lambda d, *a, **k: d
    dec._crunch_floor = lambda d, *a, **k: d


def test_after_a_replay_in_jump_mode_the_way_back_to_live_goes(cfg, clock):
    """Bug (a): after a replay, _after_replay's switch (kind live / home / break_visit) isn't in JUMP_KEEPS, so jump mode
    held 'after_replay' while _jump_owns turned the switch into a stay: the screen sat on old replay footage."""
    dec = setup(cfg, clock)
    dec.after_replay = True
    only_normal(dec, lambda now: Decision(now, "switch", "b", "replay finished → b", State.WATCHING, kind="live"))
    d = dec.decide(cands(a=60, b=70))
    assert d.action == "switch" and d.target == "b", d.reason
    assert not dec.after_replay
    assert dec.screen_m.is_("tuning") and d.owner == "replay"


def test_jump_and_its_veto_agree_on_who_stands_aside(cfg, clock):
    """Bug (b): _jump stood aside for 8 conditions, _jump_owns checked 4: during a hands-off pause or a replay the
    rotation neither jumped nor let the normal decider's optional switch go. One owner decision drives both now."""
    opt = Decision(clock.t, "switch", "b", "better", State.WATCHING, kind="better")
    for prep, owner in ((lambda dec: setattr(dec.user, "manual_until", clock.t + 30), "user"),
                        (lambda dec: setattr(dec, "after_replay", True), "replay"),
                        (lambda dec: setattr(dec, "replaying", Missed("a", "p", "TD", "td", clock.t - 30, 75)), "replay")):
        dec = setup(cfg, clock)
        prep(dec)
        assert dec._own(opt, cands(a=60, b=70), clock.t)[0] == owner
        assert dec._jump_owns(opt, cands(a=60, b=70)) is False
    dec = setup(cfg, clock)
    assert dec._own(opt, cands(a=60, b=70), clock.t) == ("jump", "rotation")
    assert dec._jump_owns(opt, cands(a=60, b=70)) is True


def test_jump_modes_veto_keeps_the_home_visit_it_found(cfg, clock):
    """Bug (c): the _jump_owns path cleared the break visit instead of restoring the one from before the tick."""
    dec = setup(cfg, clock)
    dec.home_break_visit, dec.home_return_at = "c", clock.t + 200
    dec._jump = lambda *a, **k: None

    def visit(now):
        dec.home_break_visit, dec.home_return_at = "b", None   # the visit this switch planned
        return Decision(now, "switch", "b", "better", State.WATCHING, kind="better")
    only_normal(dec, visit)
    d = dec.decide(cands(a=60, b=70, c=50))
    assert d.action == "stay" and d.reason.startswith("jump mode")
    assert (dec.home_break_visit, dec.home_return_at) == ("c", clock.t + 200)
    assert dec.home_m.is_("visit_timed")


def test_a_stay_never_leaks_a_new_home_visit(cfg, clock):
    """[home] a filter that turns a switch into a stay without restoring the visit can't leak it any more."""
    dec = setup(cfg, clock, jump=False)

    def visit(now):
        dec._home_set("b", None, "a visit planned by the switch")
        return Decision(now, "stay", "a", "a forgetful filter's stay", State.WATCHING)
    only_normal(dec, visit)
    dec.decide(cands(a=60, b=70))
    assert dec.home_break_visit is None and dec.home_leaks == 1 and dec.home_m.is_("none")


def test_jump_gate_asks_another_games_gate_without_touching_current(cfg, clock):
    """Bug (d): _jump_gate swapped self.current for the call (anything reading it meanwhile saw the wrong game)."""
    dec = setup(cfg, clock)
    dec.timelines["b"].events.append(Event(clock.t - MU - 2, "play", "play"))   # b's play is on screen: gate shut
    seen = []
    dec._leavable = lambda cur, now=None, d=None: seen.append(dec.current) or False
    dec._jump_gate(cands(b=50)["b"], clock.t, gid="b")
    assert seen and set(seen) == {"a"}


def test_jump_holds_restart_at_every_switch(cfg, clock):
    """Bug (e): holds were reset only on a jump, so their counts spanned the normal decider's switches."""
    dec = setup(cfg, clock)
    dec.jump_holds["play_on_screen"] = 5
    dec._commit("b", kind="better")
    assert not dec.jump_holds


def test_owner_is_recorded_on_every_decision(cfg, clock):
    dec = setup(cfg, clock)
    dec.safe_mode = True
    d = dec.decide(cands(a=60, b=70))
    assert d.owner == "safe" and dec.owner_m.is_("safe") and dec.jump_holds["safe_mode"] == 1
    assert dec.machines()["owner"]["state"] == "safe"


def test_confirm_on_a_full_screen_log_drops_one_old_entry_not_two(cfg, clock):
    """Bug: adopt/confirm popped 2 entries assuming _commit pushed exactly 2; on a full log (maxlen 400) the 2
    appends had already evicted 2 old entries, so every confirm shrank the history."""
    dec = setup(cfg, clock)
    dec._screen_log = deque([(clock.t - 1000 + i, "a") for i in range(400)], maxlen=400)
    first = dec._screen_log[1]
    dec.confirm(Decision(clock.t, "switch", "b", "x", State.WATCHING, kind="better"), cands(b=50))
    assert len(dec._screen_log) == 400 and dec._screen_log[0] == first and dec._screen_log[-1] == (clock.t, "b")
    dec.current = None
    dec.adopt("c")
    assert len(dec._screen_log) == 400 and dec._screen_log[-1] == (clock.t, "c")
    assert dec.screen_m.is_("live") and dec._arrival_until == 0.0


def test_screen_machine_walks_tune_replay_and_back(cfg, clock):
    dec = setup(cfg, clock, jump=False)
    dec.current = None
    assert dec.screen_m.is_("unknown")
    dec._commit("a", kind="start")
    assert dec.screen_m.is_("tuning")
    clock.t += 30
    dec._screen_tick(clock.t)
    assert dec.screen_m.is_("live")
    m = Missed("b", "p1", "TD", "td", clock.t - 40, 75)
    dec._start_replay(m, cands(a=60, b=50), clock.t, confirmed=True)
    assert dec.replaying is m and dec.screen_m.is_("replaying")
    dec.abort_replay()
    assert dec.after_replay and dec.replaying is None
    dec.confirm(Decision(clock.t, "switch", "a", "back", State.WATCHING, kind="live"), cands(a=60))
    assert dec.screen_m.is_("live") and not dec.after_replay


def test_keep_standby_window_restarts_after_a_start_lands(cfg, clock):
    """Bug: _bj_gone_since was reset only on a free cut or with nothing held, so after the 120 s keep-standby window
    ran out and the start landed, the stale timestamp made keep-standby expire at once the next time."""
    from tests.test_buffered_jump import cands as bcands, scene, setup as bsetup
    dec, _ = bsetup(cfg, clock)
    scene(dec, clock)
    dec.current = None
    start = Decision(clock.t, "switch", "c", "start", State.WATCHING, "", kind="start")
    assert dec._bj_keep_standby(start, bcands(b=50), clock.t) is not None
    clock.t += 121
    assert dec._bj_keep_standby(start, bcands(b=50), clock.t) is None
    dec._commit("c", kind="start")          # the start lands
    clock.t += 300
    dec.current = None                      # later the screen's game is unknown again
    k = dec._bj_keep_standby(start, bcands(b=50), clock.t)
    assert k is not None and k.action == "stay"
    assert dec.gone_m.is_("keeping")


def test_start_backoff_and_lock_wait_are_machines(cfg, clock):
    dec = setup(cfg, clock)
    dec.current = None
    dec.reject(Decision(clock.t, "switch", "a", "start", State.WATCHING, kind="start"))
    assert dec.start_m.is_("backoff") and dec.start_retry_at == clock.t + 3.0
    dec.start_retry_at = 0.0          # the supervisor's reset
    assert dec.start_m.is_("ready")
    cfg.update({"decider": {"lock_wait": {"enabled": True}}})
    dec.user.locked_game = "b"
    dec.lock_failed(Decision(clock.t, "switch", "b", "x", State.PAUSED, kind="user"), "tune failed")
    assert dec.lock_m.is_("waiting") and dec.lock_m.key == "b"
