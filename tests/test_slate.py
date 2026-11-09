"""Slate modes, scenes, the basketball leave and the wiring into the rotation (WP-18). Fakes only: no TV, no adb.

Why: the slate may only ever shrink the rotation's choices, must never hide a must-show, and the walk after a shot is a
way to leave a basketball game only at low leverage (Rule #1). Flag off must be today's rotation.
"""
from dataclasses import replace

from redzone.decider import buffered as bj
from redzone.decider.cuts import DeadTracker
from redzone.decider.decider import Candidate, Decider
from redzone.decider.screen import DelayBook, Timeline, _gain
from redzone.models import GameState, League, Phase, Play, Situation, Team
from redzone.moments import slate as S
from redzone.scoring.engine import Scored

NOW = 10_000.0
P = S.Params(enabled=True)


def f(gid, league="nfl", **kw):
    return S.Facts(gid, league, **kw)


def scene(fs, **kw):
    return S.plan(fs, now=kw.pop("now", NOW), **kw)


# ------------------------------------------------------------ modes

def test_modes_by_how_many_games_are_live():
    assert S.mode_of([], 0, P) == S.OFF
    assert S.mode_of([f("a")], 1, P) == S.SINGLE
    assert S.mode_of([f("a")], 3, P) == S.ENDING      # one left after more: the ending
    assert S.mode_of([f(str(i), "nba") for i in range(3)], 3, P) == S.WHIP
    assert S.mode_of([f(str(i)) for i in range(5)], 5, P) == S.BLOCK
    assert S.mode_of([f(str(i)) for i in range(16)], 16, P) == S.BLOCK
    assert S.mode_of([f(str(i)) for i in range(17)], 17, P) == S.FILTERED


def test_one_game_left_or_single_has_nothing_to_whip_to():
    s = scene([f("a")], peak=4)
    assert s.mode == S.ENDING and s.games == frozenset() and not S.may_cut_to(s, "a")


def test_anchor_holds_the_tv_outside_its_breaks():
    fs = [f("nf"), f("n1", "nba"), f("n2", "nhl")]
    assert scene(fs).mode == S.ANCHOR
    assert scene(fs).games == {"nf"}
    on_break = [f("nf", on_break=True), f("n1", "nba"), f("n2", "nhl")]
    assert scene(on_break).games == {"nf", "n1", "n2"}


def test_two_nfl_games_are_a_whip_not_an_anchor():
    assert scene([f("a"), f("b"), f("n", "nba")]).mode == S.WHIP


def test_filtered_block_prefers_your_team_and_crunch():
    fs = [f(f"g{i}") for i in range(20)] + [f("mine", "nhl", mine=True), f("hot", "nhl", crunch=True)]
    ranked = [x.game_id for x in S.relevant(fs, P)]
    assert ranked[:2] == ["mine", "hot"] and len(ranked) == 8
    s = scene(fs)
    assert s.mode == S.FILTERED and {"mine", "hot"} <= s.games


# ------------------------------------------------------------ a few games for a few minutes, then catch-up

def _five():
    return [f(str(i)) for i in range(5)]


def test_block_holds_a_few_games_for_a_few_minutes():
    s = scene(_five())
    assert s.mode == S.BLOCK and s.phase == S.HOLD and len(s.games) == P.block_size
    later = scene(_five(), now=NOW + P.hold_s - 1, block=s.block)
    assert later.block == s.block and later.phase == S.HOLD and later.games == s.games


def test_block_then_catch_up_then_the_next_block():
    s = scene(_five())
    cu = scene(_five(), now=NOW + P.hold_s + 1, block=s.block)
    assert cu.phase == S.CATCH_UP and cu.block == s.block
    nxt = scene(_five(), now=NOW + P.hold_s + P.catchup_s + 1, block=s.block)
    assert nxt.phase == S.HOLD and nxt.block != s.block and set(nxt.block.games) & {"3", "4"}


def test_catch_up_adds_the_games_with_a_big_play_you_missed():
    fs = [*_five()[:4], f("4", unseen=True)]
    s = scene(fs)
    assert "4" not in s.games
    cu = scene(fs, now=NOW + P.hold_s + 1, block=s.block)
    assert cu.phase == S.CATCH_UP and "4" in cu.games


def test_a_finished_block_starts_a_new_one():
    s = scene(_five())
    others = [f("7"), f("8"), f("9"), f("10"), f("11")]
    nxt = scene(others, block=s.block)
    assert nxt.block != s.block and len(nxt.games) == 3 and nxt.games <= {"7", "8", "9", "10", "11"}


# ------------------------------------------------------------ the floor and late narrowing

def test_every_mode_keeps_the_floor():
    fs = [*_five(), f("mine", mine=True), f("cr", crunch=True), f("td", owed=True)]
    s = scene(fs)
    assert {"mine", "cr", "td"} <= s.games


def test_late_narrowing_keeps_only_close_games_and_the_floor():
    fs = [f("a", close=True, late=True), f("b", late=True), f("c", owed=True), f("d")]
    s = scene(fs)
    assert s.late and "a" in s.games and "c" in s.games and "b" not in s.games and "d" not in s.games
    off = S.plan(fs, now=NOW, params=replace(P, late_narrow=False))
    assert not off.late and "d" in off.games


def test_no_narrowing_before_the_slate_is_close():
    s = scene([f("a", late=True), f("b")])
    assert not s.late and {"a", "b"} <= s.games


# ------------------------------------------------------------ good scenes

def test_ping_pong_needs_three_games_and_a_quiet_return():
    left = {"b": NOW - 20}
    assert S.returns_too_soon(left, "b", NOW, 3, False, P)
    assert not S.returns_too_soon(left, "b", NOW, 2, False, P)      # two games: ping-pong is the show
    assert not S.returns_too_soon(left, "b", NOW, 3, True, P)       # a must-show may come back early
    assert not S.returns_too_soon(left, "c", NOW, 3, False, P)
    assert not S.returns_too_soon(left, "b", NOW + 45, 3, False, P)


def test_a_cut_needs_a_payoff():
    assert S.pays_off(True, False, False, False, P)
    assert S.pays_off(False, False, True, False, P)
    assert S.pays_off(False, False, False, True, P)
    assert not S.pays_off(False, False, False, False, P)
    assert S.pays_off(False, False, False, False, replace(P, payoff=False))


# ------------------------------------------------------------ Timeline yardage for the floor

def test_football_word_reads_gains_and_down_tries():
    assert S.football_word("Rush", 12, 1, 10) == "gain10"
    assert S.football_word("Pass Reception", 6, 3, 5) == "big3rd"
    assert S.football_word("Pass Reception", 4, 3, 2) == ""           # short conversion: not big3rd
    assert S.football_word("Rush", 1, 4, 1) == "4th_try"
    assert S.football_word("Punt", 50, 4, 8) == ""
    assert S.football_word("Field Goal Good", None, 4, 8) == ""
    assert S.football_word("Kickoff Return", 40, None, None) == ""


def test_gain_comes_from_the_play_text():
    assert _gain("J.Williams left tackle to PHI 46 for 7 yards (C.DeJean).") == 7
    assert _gain("x pass to y for no gain") == 0
    assert _gain("x sacked for -8 yards") == -8
    assert _gain("PENALTY on DAL, No Play for 10 yards") is None
    assert _gain("Timeout #1 by DAL") is None


def _g(down, dist, ytg, plays):
    return GameState(league=League.NFL, game_id="g", home=Team("A", score=0), away=Team("B", score=0), phase=Phase.LIVE,
                     period=1, clock_s=800.0, situation=Situation(down=down, distance=dist, yards_to_goal=ytg),
                     recent_plays=plays, last_play=plays[-1] if plays else None)


def _p(pid, t, txt):
    return Play(pid, txt, "rush", False, wallclock=t, type_text="Rush")


def test_timeline_carries_the_situation_before_the_play(clock):
    tl = Timeline(League.NFL, clock=clock)
    clock.t = 1000.0
    tl.observe(_g(3, 5, 30, [_p("1", 900, "x run for 2 yards")]))     # the first poll is history, its situation is the prior
    clock.t = 1010.0
    tl.observe(_g(1, 10, 18, [_p("1", 900, "x run for 2 yards"), _p("2", 1005, "x run for 12 yards")]))
    e = tl.events[-1]
    assert (e.gain, e.down, e.to_go, e.to_goal) == (12, 3, 5, 30)


def test_two_plays_in_one_poll_are_not_given_a_down(clock):
    tl = Timeline(League.NFL, clock=clock)
    clock.t = 1000.0
    tl.observe(_g(3, 5, 30, [_p("1", 900, "x run for 2 yards")]))
    clock.t = 1010.0
    tl.observe(_g(1, 10, 18, [_p("1", 900, "x"), _p("2", 1005, "x run for 12 yards"), _p("3", 1006, "x run for 3 yards")]))
    ev = list(tl.events)[-2:]
    assert [e.down for e in ev] == [None, None] and [e.gain for e in ev] == [12, 3]


# ------------------------------------------------------------ wiring into the decider

def sc(gid, league, *, is_break=False, **kw):
    return Scored(gid, league, 30.0, 30.0, 30.0, 1.0, 0.0, f"reason {gid}", [], is_break, None, None, 1.0, **kw)


def _rig(cfg, clock, leagues=None, slate=True):
    cfg.update({"jump_mode": {"enabled": True}})
    cfg.data["slate"] = {"enabled": slate}
    delays = DelayBook({"youtubetv": 35.0}, sigma_default=4.0, clock=clock)
    dec = Decider(cfg, delays, clock=clock)
    dec.entry_cost = lambda t: 3.0
    games = {}
    for gid, lg in (leagues or {"a": League.NFL, "b": League.NFL, "c": League.NFL}).items():
        tl = dec.timeline(gid, lg)
        tl.fresh_at = clock.t
        games[gid] = Candidate(sc(gid, lg), f"youtubetv:{gid}")
    return dec, games


def test_flag_off_keeps_nhl_and_mlb_out_and_builds_no_scene(cfg, clock):
    dec, games = _rig(cfg, clock, {"a": League.NFL, "n": League.NHL}, slate=False)
    assert not dec._jump_league(games["n"])
    dec._slate_tick = lambda *a: (_ for _ in ()).throw(AssertionError("no slate work with the flag off"))
    assert dec._slate_allows("n", clock.t) and dec.slate_scene is None


def test_turning_the_flag_off_clears_the_scene(cfg, clock):
    dec, games = _rig(cfg, clock, {str(i): League.NFL for i in range(5)})
    dec.current = "0"
    dec._slate_tick(games, clock.t)
    assert dec.slate_scene is not None
    cfg.data["slate"] = {"enabled": False}
    dec.decide(games)
    assert dec.slate_scene is None and dec._slate_allows("4", clock.t)


def test_flag_on_puts_nhl_and_mlb_in_the_rotation(cfg, clock):
    dec, games = _rig(cfg, clock, {"a": League.NFL, "n": League.NHL, "m": League.MLB})
    assert dec._jump_league(games["n"]) and dec._jump_league(games["m"])
    dec.current = "a"
    dec._slate_tick(games, clock.t)
    assert dec.slate_scene.mode == S.ANCHOR    # one NFL game plus lower leagues


def test_rotation_only_cuts_inside_the_scene(cfg, clock):
    dec, games = _rig(cfg, clock, {str(i): League.NFL for i in range(5)})
    dec.current = "0"
    dec._slate_tick(games, clock.t)
    out = set(games) - dec.slate_scene.games
    assert out and all(not dec._slate_allows(g, clock.t) for g in out)
    assert dec.jump_holds["slate_out_of_scene"] == len(out)


def test_cut_decided_with_no_payoff_is_held(cfg, clock):
    dec, games = _rig(cfg, clock, {"a": League.NFL, "b": League.NFL})
    dec.current = "a"
    dec.since = clock.t - 200
    dec._slate_tick(games, clock.t)
    dec._jump_gate = lambda *a, **k: type("G", (), {"safe": True, "why": "w", "on_screen_break": False})()
    dec._jump_best = lambda c, n, exclude=None: ("b", {"snap": False, "big": False, "snap_in_s": 40.0, "cost_s": 3.0})
    dec._jump_snap_wait = lambda *a, **k: 5.0
    assert dec._jump(_stay(dec), games, clock.t, "rotation") is None
    assert dec.jump_holds["slate_no_payoff"] == 1


def _stay(dec):
    from redzone.decider.decider import Decision, State
    return Decision(dec._clock(), "stay", dec.current, "x", State.WATCHING)


# ------------------------------------------------------------ basketball: leave after a shot, low leverage only

WALK = (14.0, 7.0, 1.0, 2.0)


def _dt():
    return DeadTracker(League.NBA)


def _shot(dt, pid, t, text="X makes 3-pt jump shot"):
    dt.add(pid, t, t, "made_shot", text, "Three Point Jump Shot", None, 1)


def _pic(dt, mu=80.0, leave=True):
    return bj.Pic(dt, mu, 1.0, "a", WALK, WALK if leave else None)


def _walk_scene(gap):
    """V: a made shot on screen, the next action `gap` s later. H: a long dead ball."""
    dt = _dt()
    now = 500.0
    x = now - 80.0
    _shot(dt, "s", x - 4.0)
    dt.add("n", x - 4.0 + gap, x - 4.0 + gap, "shot", "Y misses shot", "Jump Shot", None, 1)
    hd = _dt()
    hd.add("t", x - 60.0, x - 60.0, "timeout", "Timeout", "Timeout", None, 1)
    hd.add("r", x + 30.0, x + 30.0, "shot", "Y misses", "Jump Shot", None, 1)
    return dt, hd, now


def test_walk_leave_requires_the_flag_and_a_clean_walk():
    dt, _, now = _walk_scene(20.0)
    assert bj.leaves(_pic(dt, leave=True), now, now + 3.2, 2.0)
    assert not bj.leaves(_pic(dt, leave=False), now, now + 3.2, 2.0)


def test_a_transition_play_is_never_a_walk_to_leave():
    dt, _, now = _walk_scene(4.0)     # the next action 4 s after the shot: transition
    assert not bj.leaves(_pic(dt, leave=True), now, now + 3.2, 2.0)


def test_cut_now_leaves_in_the_walk_and_counts_the_time_to_the_next_action():
    dt, hd, now = _walk_scene(20.0)
    v = _pic(dt, leave=True)
    h = bj.Pic(hd, 80.0, 1.0, "b")
    c = bj.cut_now(v, h, now, None, plays_seen=1)
    assert c.go or c.why != "play_on_screen"


def test_walk_leave_is_only_at_low_leverage(cfg, clock):
    dec, _ = _rig(cfg, clock, {"a": League.NBA})
    low = Candidate(sc("a", League.NBA), "youtubetv:a")
    assert dec._may_leave_on_walk("a", low, clock.t)
    for high in (sc("a", League.NBA, high_leverage=True), sc("a", League.NBA, crunch=True), sc("a", League.NBA, crunch_now=True),
                 sc("a", League.NBA, mine=True)):
        assert not dec._may_leave_on_walk("a", Candidate(high, "youtubetv:a"), clock.t)
    dec._high_lev_at["a"] = clock.t - 5.0
    assert not dec._may_leave_on_walk("a", low, clock.t)            # it was high leverage moments ago


def test_walk_leave_off_with_the_flag_or_the_param(cfg, clock):
    dec, _ = _rig(cfg, clock, {"a": League.NBA}, slate=False)
    x = Candidate(sc("a", League.NBA), "youtubetv:a")
    assert not dec._may_leave_on_walk("a", x, clock.t)
    cfg.data["slate"] = {"enabled": True, "leave_after_shot": False}
    assert not dec._may_leave_on_walk("a", x, clock.t)
    y = Candidate(sc("a", League.NFL), "youtubetv:a")
    cfg.data["slate"] = {"enabled": True, "leave_after_shot": True}
    assert not dec._may_leave_on_walk("a", y, clock.t)              # football has real dead balls
