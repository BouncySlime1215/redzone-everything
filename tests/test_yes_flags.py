"""[YES-1] The flags that take false refusals out of the leave gate, the legal list and the arbiter. Every one ships OFF.

Why: Nick, Nov 10: "the code keeps saying no... a no is rare, like 95% yes", with the guarantee that touchdowns and big plays must air. Five
inspections measured which refusals were logic errors (research ledger L6); each fix sits behind its own flag, and flag off the night decides as before.
"""
import dataclasses
import itertools

import pytest
import yaml

from redzone import measured_timing as mt
from redzone.decider import gate
from redzone.decider.cuts import EntryPlan
from redzone.config import _deep_merge
from redzone.decider.decider import PSEUDO, Candidate, Decider, Decision, State
from redzone.decider.screen import Event, Timeline, leave_window
from redzone.models import League, Phase
from redzone.noggin import legal
from tests.test_exits_catchup import cand, host
from tests.test_legal import _c, _dec
from tests.test_timing_truth import _night_decisions

from pathlib import Path

CONFIG = yaml.safe_load((Path(mt.__file__).parent / "default_config.yaml").read_text())
ALL = ["nfl", "cfb", "nba", "cbb", "nhl", "mlb"]
# The fixture night with the arbiter choosing and a held pick allowed to wait: the paths the flags live on.
CTX = {"arbiter.enabled": True, "legal.leave_at_flip": True, "legal.leave_low_leverage": ALL}
OFF = {"gate.leave_unaired_row_only_ended": False, "gate.leave_margin_measured": False, "gate.leave_dead_window": False,
       "legal.lock_dead_ball": False, "legal.facts_urgent": False,
       "gate.ending_last_play": False}        # every new flag at its shipped default


def night(ctx=None, **sets):
    return _night_decisions(**{**(CTX if ctx is None else ctx), **sets})


def flag(key):
    node = CONFIG
    for part in key.split("."):
        node = node[part]
    return node


def test_every_flag_ships_at_its_old_behaviour():
    assert {k: flag(k) for k in OFF} == OFF


@pytest.mark.parametrize("ctx", [{}, CTX], ids=["shipped", "arbiter"])
def test_the_defaults_spelled_out_decide_the_night_as_no_flag_at_all(ctx):
    after = night(ctx)
    assert len(after[0]) > 500 and after[1]
    assert night(ctx, **OFF) == after


def flip(d, cands, *, held=False, now=200.0):
    dec = Decision(now, "switch", "a", "", State.WATCHING, kind="better", leave_held=held)
    return gate.Flip(gate.DeciderHost(d, cands, now), dec, "on", "yt:a", cands, None, now)


def between_plays(**cfg):
    """test_exits_catchup's host, 6 s later with a fresh poll: the play (logged 165, 35 s delay) is off the screen at 206, but the
    'last row still airing' timer (165 + 35 + 2 sigma + 5) runs to 207."""
    d = host((), **cfg)
    d.timelines["on"].fresh_at = 206.0
    d.replay_until = d.replay_exit_after = 0.0
    return d


CANDS = {"on": cand(), "a": cand()}


# ----- step 2: gate.leave_unaired_row_only_ended --------------------------------------------------------------------------------------

ROW = "gate.leave_unaired_row_only_ended"


def _old_leave_open(dec, gid, target, phase, now, uncalibrated_sigma, required_s=0.0):
    """gate.leave_open as it was before YES-1, word for word."""
    if not gid or gid in PSEUDO:
        return True
    if dec.replaying or dec.after_replay:
        return now >= max(dec.replay_until, dec.replay_exit_after)
    if gid not in dec.timelines:
        return False
    if dec._ending_still_airing(gid, now) > 0:
        return False
    if phase == Phase.POST:
        return True
    return leave_window(dec.timelines[gid], dec.delays.get(target or "unknown"), now, uncalibrated_sigma, required_s).safe


def test_flag_off_a_live_games_unaired_row_refuses_before_the_window_is_asked():
    d = between_plays()
    assert d._ending_still_airing("on", 206.0) > 0
    assert leave_window(d.timelines["on"], d.delays.get("yt:on"), 206.0, 8.0, 8.0).safe          # the window itself is open
    assert gate.leave_refusal(d, "on", "yt:on", Phase.LIVE, 206.0, 8.0, 8.0) == "unaired_row"
    assert gate._leave(flip(d, CANDS, now=206.0)) is True


def test_flag_on_a_live_game_goes_straight_to_the_leave_window():
    d = between_plays(**{ROW: True})
    assert gate.leave_refusal(d, "on", "yt:on", Phase.LIVE, 206.0, 8.0, 8.0) == ""
    assert gate._leave(flip(d, CANDS, now=206.0)) is False
    assert gate.leave_refusal(d, "on", "yt:on", Phase.LIVE, 200.0, 8.0, 8.0) == "window"         # mid-play: the window still refuses
    assert gate._leave(flip(d, CANDS, now=200.0)) is True


@pytest.mark.parametrize("phase", [Phase.POST, None, Phase.PRE, Phase.UNKNOWN])
def test_flag_on_a_game_that_ended_or_is_missing_still_waits_for_its_last_row(phase):
    d = between_plays(**{ROW: True})
    assert gate.leave_refusal(d, "on", "yt:on", phase, 206.0, 8.0, 8.0) == "ended_airing"
    assert gate.leave_refusal(d, "on", "yt:on", phase, 208.0, 8.0, 8.0) in ("", "window")     # aired: POST is free, anything else asks the window


def test_flag_off_the_night_decides_as_the_old_leave_rule(monkeypatch):
    after = night()
    monkeypatch.setattr(gate, "leave_refusal", lambda *a, **k: "" if _old_leave_open(*a, **k) else "refused")
    assert night() == after


# ----- step 3: gate.leave_margin_measured ---------------------------------------------------------------------------------------------

NEED = "gate.leave_margin_measured"


def snap_coming(**cfg):
    """Between plays at 230 with the next snap already in the data (logged 215, on air from 240): open for a 3.2 s flip, shut for 8 s."""
    d = between_plays(**{ROW: True, **cfg})
    d.timelines["on"].events.append(Event(215.0, "play"))
    d.timelines["on"].fresh_at = 230.0
    return d


def test_flag_off_the_leave_window_is_asked_for_8_s():
    d = snap_coming()
    f = flip(d, CANDS, now=230.0)
    assert gate._leave_need(f, 8.0) == 8.0 and gate._leave(f) is True and gate.leave_cause(f) == "window"
    assert leave_window(d.timelines["on"], d.delays.get("yt:on"), 230.0, 8.0, 3.2).safe


def test_flag_on_it_is_asked_for_the_flips_own_bound():
    d = snap_coming(**{NEED: True})
    f = flip(d, CANDS, now=230.0)
    assert gate._leave_need(f, 8.0) == f.host._leave_margin() == 3.2
    assert gate._leave(f) is False and gate.leave_cause(f) == ""
    late = flip(d, CANDS, now=235.0)                         # 235 + 3.2 reaches the snap's doubt band (240 - 2 sigma - 1)
    assert gate._leave(late) is True and gate.leave_cause(late) == "window"
    d.entry_cost = lambda target: 5.0                        # a slower measured switch asks for more, never less than the flip
    assert gate._leave_need(flip(d, CANDS, now=230.0), 8.0) == 5.0


def test_flag_on_the_host_is_asked_with_its_own_margin_and_a_jump_is_untouched():
    from tests.test_commit_gate import AT, T, _d, _host
    seen = []
    for cfg in ({}, {NEED: True}):
        h = _host(**cfg)
        h.safe_to_leave = lambda gid, required_s=0.0: seen.append(required_s) or True
        h.decider.jump_may_leave = lambda gid, live, now, req: seen.append(("jump", req)) or True
        assert gate._leave(gate.Flip(h, _d(), "a", T, {}, None, AT)) is False
        assert gate._leave(gate.Flip(h, _d(kind="jump"), "a", T, {}, None, AT)) is False
    assert seen == [8.0, ("jump", 3.2), 3.2, ("jump", 3.2)]


def test_flag_off_the_night_decides_as_with_the_8_s_window(monkeypatch):
    after = night()
    monkeypatch.setattr(gate, "_leave_need", lambda f, usual: usual)
    assert night() == after


# ----- step 4: gate.leave_dead_window -------------------------------------------------------------------------------------------------

DEAD = "gate.leave_dead_window"
NBA = {"on": cand(League.NBA), "a": cand()}


def at_the_line(**cfg):
    """Basketball, a shooting foul logged at 160: with a 35 s delay (sigma 1) the screen at 200 shows 165 +- 2, inside the dead window
    the foul opened (surely dead 22 s, to 182). The plain window knows no stoppage: 'play is live'."""
    d = host((), **cfg)
    tl = Timeline(League.NBA)
    tl.fresh_at = 200.0
    tl.events.append(Event(150.0, "play"))
    tl.dead.add("f1", 160.0, 160.0, "", "shooting foul on A", "Shooting Foul", 500.0, 1)
    tl.dead.seen(200.0, 10.0)
    d.timelines["on"] = tl
    d.replay_until = d.replay_exit_after = 0.0
    return d


def test_flag_off_basketball_at_the_line_still_answers_play_is_live():
    d = at_the_line()
    f = flip(d, NBA)
    assert d._ending_still_airing("on", 200.0) == 0 and d.dead_window_leave("on", "yt:on", 200.0, 3.2)
    assert gate._dead_window_leave(f) is False and gate._leave(f) is True and gate.leave_cause(f) == "window"


def test_flag_on_a_proven_dead_window_opens_the_leave():
    d = at_the_line(**{DEAD: True})
    assert gate._leave(flip(d, NBA)) is False
    assert gate._leave(flip(d, NBA, now=215.0)) is True          # 215: the flip would end at screen time 183.2 + doubt, past the sure 182
    d.timelines["on"].dead.marks.clear()
    assert gate._leave(flip(d, NBA)) is True                     # no dead-ball data: nothing proves it


def test_flag_on_a_running_game_clock_refuses_at_every_leverage():
    d = at_the_line(**{DEAD: True})
    d.timelines["on"].clock_events.extend([(162.0, 1, 500.0, "foul"), (170.0, 1, 494.0, "jump shot")])     # the clock ran from 164
    for kw in ({}, {"high_leverage": True}):
        f = flip(d, {"on": cand(League.NBA, **kw), "a": cand()})
        assert gate._clock_ran_on_leave(f, 3.2) is True and gate._dead_window_leave(f) is False and gate._leave(f) is True
    d.timelines["on"].clock_events.clear()
    d.timelines["on"].clock_events.extend([(140.0, 1, 520.0, "shot"), (160.0, 1, 500.0, "foul")])          # it ran before the whistle only
    assert gate._leave(flip(d, NBA)) is False


@pytest.mark.parametrize("phase", [Phase.POST, Phase.PRE, None])
def test_flag_on_only_a_live_game_is_left_on_a_dead_window(phase):
    d = at_the_line(**{DEAD: True})
    f = flip(d, NBA)
    if phase is None:
        del f.host.merger.games["on"]
    else:
        f.host.merger.games["on"].phase = phase
    assert gate._dead_window_leave(f) is False


def test_flag_on_the_window_must_cover_the_flip_with_doubt_on_both_sides():
    d = at_the_line(**{DEAD: True})
    assert d.dead_window_leave("on", "yt:on", 200.0, 3.2) is True
    assert d.dead_window_leave("on", "yt:on", 196.0, 3.2) is False       # screen 161 - 2 sigma: may still be before the whistle
    assert d.dead_window_leave("on", "yt:on", 200.0, 16.0) is False      # 200 + 16 - 35 + 2 = 183: past the sure 182
    assert d.dead_window_leave("gone", "yt:on", 200.0, 3.2) is False and d.dead_window_leave(None, None, 200.0, 3.2) is False


def test_flag_off_the_night_decides_as_with_the_plain_window_alone(monkeypatch):
    after = night()
    monkeypatch.setattr(gate, "_plain_leave", lambda f, usual: bool(f.host.safe_to_leave(f.before, required_s=usual)))
    assert night() == after


# ----- step 5: legal.lock_dead_ball ---------------------------------------------------------------------------------------------------

LOCK = "legal.lock_dead_ball"
ROWS = (legal.GameRow("on"), legal.GameRow("a"), legal.GameRow("big", over_lock=True))


def lock_facts(**kw):
    return legal.Facts(**{"now": 100.0, "current": "on", "games": ROWS, "moment_lock": "red zone drive", **kw})


def cuts(f):
    return [o.game_id for o in legal.legal_options(f) if o.verb == "cut"]


def test_flag_off_any_lock_hides_every_cut_whatever_else_is_known():
    for kw in ({}, {"lock_high": True}, {"gate_ok": False, "leave_at_flip": True}):
        f = lock_facts(**kw)
        assert legal.legal_options(f) == (legal.STAY,) and "moment_lock" in legal.refusals(f, "big")


def test_flag_on_a_lock_that_is_not_high_leverage_hides_nothing():
    f = lock_facts(lock_dead_ball=True)
    assert cuts(f) == ["a", "big"] and legal.refusals(f, "a") == ()
    assert legal.legal_options(lock_facts(lock_dead_ball=True, gate_ok=False)) == (legal.STAY,)       # the leave gate still decides when
    assert cuts(lock_facts(lock_dead_ball=True, gate_ok=False, leave_at_flip=True)) == ["a", "big"]   # ...or the pick waits for the flip


def test_flag_on_a_high_leverage_lock_gives_way_only_at_a_dead_ball_to_a_much_bigger_moment():
    f = lock_facts(lock_dead_ball=True, lock_high=True)
    assert cuts(f) == ["big"]                                              # dead ball on screen, and it outranks the lock by the lead
    assert legal.refusals(f, "a") == ("moment_lock",) and legal.refusals(f, "big") == ()
    for mid_play in ({"gate_ok": False}, {"gate_ok": False, "leave_at_flip": True}):
        f = lock_facts(lock_dead_ball=True, lock_high=True, **mid_play)
        assert legal.legal_options(f) == (legal.STAY,) and "moment_lock" in legal.refusals(f, "big")   # never mid-play, held or not


@pytest.mark.parametrize("flag,high,gate_ok,at_flip,lock", list(itertools.product((False, True), (False, True), (False, True), (False, True),
                                                                                   ("", "overtime"))))
def test_refusals_are_empty_exactly_when_the_cut_is_listed(flag, high, gate_ok, at_flip, lock):
    f = lock_facts(lock_dead_ball=flag, lock_high=high, gate_ok=gate_ok, leave_at_flip=at_flip, moment_lock=lock)
    for g in ("a", "big"):
        assert (legal.refusals(f, g) == ()) == (g in cuts(f))


def _locked(d, cfg, flag, **scored_kw):
    cfg.data = _deep_merge(cfg.data, {"legal": {"lock_dead_ball": flag}})
    d.current = "a"
    on = _c("a", 50.0, "youtubetv:a")
    cands = {"a": Candidate(dataclasses.replace(on.scored, lock="red zone drive", **scored_kw), on.target),
             "b": _c("b", 69.0, "youtubetv:b"), "c": _c("c", 70.0, "youtubetv:c")}
    return d.legal_facts(cands)


def test_the_decider_says_which_lock_is_high_leverage_and_which_cut_outranks_it(cfg, clock):
    d = _dec(cfg, clock)
    f = _locked(d, cfg, True)
    assert (f.moment_lock, f.lock_dead_ball, f.lock_high) == ("red zone drive", True, False)
    assert {g.game_id: g.over_lock for g in f.games} == {"a": False, "b": False, "c": True}      # decider.locked_override_lead is 20
    assert _locked(d, cfg, True, high_leverage=True).lock_high is True
    d._high_lev_at["a"] = clock.t - 30.0            # close and late 30 s ago in the feed: a 40 s delay still shows it
    assert _locked(d, cfg, True).lock_high is True
    d._high_lev_at["a"] = clock.t - 300.0
    assert _locked(d, cfg, True).lock_high is False


def test_flag_off_the_decider_hands_over_the_lock_and_nothing_new(cfg, clock, monkeypatch):
    monkeypatch.setattr(Decider, "_high_leverage_on_screen", lambda *a: (_ for _ in ()).throw(AssertionError("read with the flag off")))
    f = _locked(_dec(cfg, clock), cfg, False, high_leverage=True)
    assert (f.moment_lock, f.lock_dead_ball, f.lock_high) == ("red zone drive", False, False) and not any(g.over_lock for g in f.games)


def test_flag_off_the_night_never_asks_about_the_lock(monkeypatch):
    after = night()
    monkeypatch.setattr(Decider, "_high_leverage_on_screen", lambda *a: (_ for _ in ()).throw(AssertionError("read with the flag off")))
    monkeypatch.setattr(legal, "lock_hides", lambda lock, rule=False, *a: bool(lock) if not rule else 1 / 0)
    assert night() == after


# ----- step 6: legal.facts_urgent -----------------------------------------------------------------------------------------------------

URGENT = "legal.facts_urgent"


def _planned(cfg, clock, **sets):
    """The (game, urgent, possession, cost) of every landing plan legal_facts asks for, and its facts. A plan is ok only on the urgent bar."""
    for k, v in sets.items():
        node = v
        for part in reversed(k.split(".")):
            node = {part: node}
        cfg.data = _deep_merge(cfg.data, node)
    d = _dec(cfg, clock)
    d.current = "a"
    d.entry_cost = lambda target: 5.0
    asked = []
    d.entry_plan = lambda gid, cands, now, cost=None, urgent=False, possession=False: (
        asked.append((gid, urgent, possession, cost)), EntryPlan(ok=urgent, why="t"))[1]
    hockey = _c("b", 60.0, "youtubetv:b")
    cands = {"a": _c("a", 50.0, "youtubetv:a"), "c": _c("c", 55.0, "youtubetv:c"),
             "b": Candidate(dataclasses.replace(hockey.scored, league=League.NHL, crunch=True), hockey.target)}
    for gid, x in cands.items():
        d.timeline(gid, x.scored.league)
    return asked, d.legal_facts(cands)


def test_flag_off_the_list_plans_every_landing_with_neither_bar(cfg, clock):
    asked, f = _planned(cfg, clock)
    assert asked == [("a", False, False, None), ("c", False, False, None), ("b", False, False, None)]
    assert not any(g.landable for g in f.games)


def test_flag_on_the_list_plans_a_crunch_entry_on_the_urgent_bar_like_the_flip(cfg, clock):
    asked, f = _planned(cfg, clock, **{URGENT: True})
    assert asked == [("a", False, False, 5.0), ("c", False, False, 5.0), ("b", True, False, 5.0)]     # hockey into crunch: whistle-timed, urgent
    assert {g.game_id: g.landable for g in f.games} == {"a": False, "c": False, "b": True}


def test_flag_on_an_entry_the_flip_does_not_time_keeps_the_lists_plan(cfg, clock):
    asked, _ = _planned(cfg, clock, **{URGENT: True, "decider.entry.hold_only": []})
    assert asked[-1] == ("b", False, False, None)


def test_flag_on_the_plan_is_the_flips_own_replan(cfg, clock, monkeypatch):
    seen = []
    real = gate.Flip.replan.func
    monkeypatch.setattr(gate.Flip, "replan", property(lambda self: (seen.append((self.d.target, self.d.kind, self.before)), real(self))[1]))
    _planned(cfg, clock, **{URGENT: True})
    assert seen == [("a", "better", "a"), ("c", "better", "a"), ("b", "better", "a")]


def test_flag_off_the_night_never_builds_a_flip_for_the_list(monkeypatch):
    after = night()
    monkeypatch.setattr(Decider, "_flip_plan", lambda *a: (_ for _ in ()).throw(AssertionError("read with the flag off")))
    assert night() == after


# ----- step 7: gate.ending_last_play --------------------------------------------------------------------------------------------------

ENDING = "gate.ending_last_play"


def _old_ending_still_airing(self, gid, now, cap_s=150.0):
    """Decider._ending_still_airing as it was before YES-1, word for word."""
    tl = self.timelines.get(gid)
    if tl is None or not tl.events:
        return 0.0
    last = max(e.seen for e in tl.events)
    d = self.delays.get(self._target_of.get(gid) or "unknown")
    until = last + d.mu + 2 * d.current_sigma(now) + 5.0
    if now - last > cap_s + d.mu:
        return 0.0
    return max(0.0, until - now)


def final_horn(league=League.NFL, **cfg):
    """The last play logged at 165, the end-of-game row at 185; a 35 s delay, sigma 1."""
    d = host((), **cfg)
    tl = Timeline(league)
    tl.fresh_at = 200.0
    tl.events.extend([Event(165.0, "play"), Event(185.0, "stop_start", "end_period")])
    d.timelines["on"] = tl
    return d


@pytest.mark.parametrize("league", [League.NFL, League.CFB, League.MLB, League.NBA, League.NHL])
def test_flag_off_the_timer_runs_from_the_newest_row_of_any_kind(league):
    d = final_horn(league)
    assert d._ending_still_airing("on", 200.0) == 185.0 + 35.0 + 2.0 + 5.0 - 200.0 == _old_ending_still_airing(d, "on", 200.0)


@pytest.mark.parametrize("league", [League.NFL, League.CFB, League.MLB])
def test_flag_on_football_and_baseball_wait_for_the_last_play_and_its_buffer(league):
    d = final_horn(league, **{ENDING: True})
    assert d._ending_still_airing("on", 200.0) == 165.0 + 35.0 + 2.0 + 3.0 - 200.0
    assert d._ending_still_airing("on", 204.9) > 0 and d._ending_still_airing("on", 205.0) == 0      # never before the play is off the screen


@pytest.mark.parametrize("league", [League.NBA, League.CBB, League.NHL])
def test_flag_on_basketball_and_hockey_keep_the_old_timer(league):
    d = final_horn(league, **{ENDING: True})
    assert d._ending_still_airing("on", 200.0) == _old_ending_still_airing(d, "on", 200.0) == 27.0


def test_flag_on_a_game_with_no_play_row_keeps_the_old_timer_and_the_cap_still_frees_it():
    d = final_horn(**{ENDING: True})
    tl = d.timelines["on"]
    tl.events.clear()
    tl.events.append(Event(185.0, "stop_start", "end_period"))
    assert d._ending_still_airing("on", 200.0) == 27.0
    tl.events.append(Event(10.0, "play"))                    # a play long ago: past the 150 s cap it holds nothing
    assert d._ending_still_airing("on", 200.0) == 0


def test_flag_on_the_ending_rule_frees_an_ended_game_when_its_last_play_has_aired():
    cands = {"a": cand()}                                    # the game on screen has left the list: ended
    for cfg, veto in (({}, True), ({ENDING: True}, False)):
        d = final_horn(**cfg)
        assert gate._ending_airing(flip(d, cands, now=206.0)) is veto
    assert gate._ending_airing(flip(final_horn(**{ENDING: True}), cands, now=204.0)) is True


def test_flag_off_the_night_decides_as_the_old_timer(monkeypatch):
    after = night()
    monkeypatch.setattr(Decider, "_ending_still_airing", _old_ending_still_airing)
    assert night() == after
