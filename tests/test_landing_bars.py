"""[Nick 2026-11-08 landing bars + audit package A] Rule #1 on the way in, for every league; the basketball walk-after-a-shot
landing and its transition divert; football after the play; the rotation's holds and priors; a restart's unknown screen."""
from types import SimpleNamespace as NS

from redzone import config
from redzone.decider import buffered as bj
from redzone.decider import cuts
from redzone.decider.cuts import DeadTracker, plan_entry, shot_kind
from redzone.decider.decider import Candidate, Decider, Decision, State
from redzone.decider.screen import DelayBook, Event
from redzone.models import League
from redzone.scoring.engine import Scored

EDGE = 35.0
MU = EDGE + 79.0
WALK = (14.0, 7.0, 1.0, 2.0)


def sc(gid, score, league=League.NFL, **kw):
    return Scored(gid, league, score, score, score, 1.0, 0.0, f"reason {gid}", [], kw.pop("is_break", False), None, None,
                  kw.pop("closeness", 1.0), **kw)


def cand(gid, score=50, league=League.NFL, target=None, **kw):
    return Candidate(sc(gid, score, league, **kw), target or f"youtubetv:{gid}")


def decider(cfg, clock, *gids, league=League.CFB, jump=True, buffered=True, behind=79.0):
    if jump:
        cfg.update({"jump_mode": {"enabled": True}, "buffered_jump": {"enabled": buffered, "park": False}})
    delays = DelayBook({"youtubetv": EDGE}, sigma_default=15.0, clock=clock)
    dec = Decider(cfg, delays, clock=clock)
    dec.deadline_cost = lambda target: 3.0
    for gid in gids:
        for v in (EDGE - 0.5, EDGE, EDGE + 0.5):
            delays.calibrate(f"youtubetv:{gid}", v)
        delays.set_behind(f"youtubetv:{gid}", behind)
        delays.held[f"youtubetv:{gid}"] = behind
        dec.timeline(gid, league)._lags.append(15.0)
    return dec


def fresh(dec, clock):
    for tl in dec.timelines.values():
        tl.fresh_at = clock.t
        tl.dead.seen(clock.t, 15.0)


def nfl_play(dt, pid, t):
    dt.add(pid, t, t, "rush", "run for 3", "Rush", None, 1)


def made(dt, pid, t):
    dt.add(pid, t, t, "jump_shot", "Curry makes 26-foot three", "Jump Shot", None, 2)


def miss(dt, pid, t):
    dt.add(pid, t, t, "jump_shot", "Curry misses three", "Jump Shot", None, 2)


def dreb(dt, pid, t):
    dt.add(pid, t, t, "defensive_rebound", "Green defensive rebound", "Defensive Rebound", None, 2)


def oreb(dt, pid, t):
    dt.add(pid, t, t, "offensive_rebound", "Green offensive rebound", "Offensive Rebound", None, 2)


def switch(kind, target="b", now=0.0):
    return Decision(now, "switch", target, "r", State.WATCHING, "", kind=kind)


# ----- D-01: no go-in-live exit in any league -----------------------------------------------------------------------

def test_d01_defaults_wait_everywhere():
    assert config.default("decider.entry.crunch_now") == "wait"
    assert config.default("decider.entry.max_wait_s") == {}
    assert config.default("decider.entry.allow_live_fallback") is False


def _timed_stay(cfg, clock, league, base):
    """A crunch entry whose only landing is mid-play, for longer than the old 180 s cap: must stay the whole time."""
    dec = decider(cfg, clock, "a", "b", league=league, jump=False)
    dec._replayable = lambda x: False                  # a Prime picture: no rewind to a dead ball
    tl = dec.timelines["b"]
    x = {"b": cand("b", 90, league, crunch=True, crunch_now=True, late=True)}
    dec.current = "a"
    if base == League.NBA:
        tl.dead.add("p1", clock.t - 400, clock.t - 400, "jump_shot", "x", "Jump Shot", None, 4)   # live, no dead window
    for i in range(4):
        if base != League.NBA:
            nfl_play(tl.dead, f"p{i}", clock.t - EDGE + 3)   # a play on the picture when it would come up
        fresh(dec, clock)
        d = dec._time_entry(switch("crunch", now=clock.t), x, clock.t, (None, None))
        assert d.action == "stay", d.reason
        clock.t += 70
    return d


def test_d01_cfb_crunch_mid_play_waits(cfg, clock):
    _timed_stay(cfg, clock, League.CFB, League.NFL)


def test_d01_cbb_crunch_mid_play_waits_past_the_old_cap(cfg, clock):
    _timed_stay(cfg, clock, League.CBB, League.NBA)


def test_d01_mlb_crunch_mid_play_waits(cfg, clock):
    _timed_stay(cfg, clock, League.MLB, League.MLB)


def test_d01_no_play_by_play_yet_waits(cfg, clock):
    dec = decider(cfg, clock, "a", "b", jump=False)
    dec.current = "a"
    x = {"b": cand("b", 90, League.CFB)}
    d = dec._time_entry(switch("better", now=clock.t), x, clock.t, (None, None))
    assert d.action == "stay" and "no play-by-play" in d.reason


def test_d01_live_fallback_key_brings_the_old_exit_back(cfg, clock):
    cfg.update({"decider": {"entry": {"allow_live_fallback": True}}})
    dec = decider(cfg, clock, "a", "b", jump=False)
    dec.current = "a"
    x = {"b": cand("b", 90, League.CFB)}
    assert dec._time_entry(switch("better", now=clock.t), x, clock.t, (None, None)).action == "switch"


# ----- D-02 ---------------------------------------------------------------------------------------------------------

def test_d02_mlb_is_timed_and_start_waits_after_forget_screen(cfg, clock):
    dec = decider(cfg, clock, "b", league=League.MLB, jump=False)
    tl = dec.timelines["b"]
    tl.dead.add("p1", clock.t - EDGE + 2, clock.t - EDGE + 2, "pitch", "ball", "Pitch", None, 3)
    fresh(dec, clock)
    dec.forget_screen("restart")
    x = {"b": cand("b", 90, League.MLB)}
    d = dec._time_entry(switch("start", now=clock.t), x, clock.t, (None, None))
    assert d.action == "stay"


# ----- D-03 / D-04: the rotation ------------------------------------------------------------------------------------

def test_d03_neutral_crunch_break_lets_the_rotation_jump(cfg, clock):
    dec = decider(cfg, clock, "a")
    assert dec._jump_hold(cand("a", league=League.NFL, crunch=True, crunch_now=True, is_break=True)) is None


def test_d03_crunch_holds_while_live_and_your_team_through_breaks(cfg, clock):
    dec = decider(cfg, clock, "a")
    assert dec._jump_hold(cand("a", league=League.NFL, crunch=True, crunch_now=True)) == "crunch"
    held = cand("a", league=League.NFL, crunch=True, crunch_now=True, is_break=True)
    held.scored.personal = 10.0
    assert dec._jump_hold(held) == "crunch"


def test_d04_your_team_late_and_close_holds_the_screen(cfg, clock):
    dec = decider(cfg, clock, "a")
    x = cand("a", league=League.NFL, late=True, closeness=0.7)
    x.scored.personal = 10.0
    assert dec._jump_hold(x) == "mine_late"


def test_d04_jump_prior_ranks_your_team_and_the_league_order(cfg, clock):
    dec = decider(cfg, clock, "a")
    mine = sc("a", 50, League.NBA)
    mine.personal = 10.0
    neutral = sc("b", 50, League.NBA)
    assert dec._jump_prior(mine) > dec._jump_prior(neutral) + 20
    assert dec._jump_prior(sc("c", 50, League.NFL)) > dec._jump_prior(sc("d", 50, League.CFB))
    assert dec._jump_prior(sc("d", 50, League.CFB)) > dec._jump_prior(sc("e", 50, League.NBA))


# ----- D-05 ---------------------------------------------------------------------------------------------------------

def test_d05_a_feed_dark_for_25s_is_not_an_empty_picture(cfg, clock):
    dec = decider(cfg, clock, "a")
    dec.current = "a"
    tl = dec.timelines["a"]
    nfl_play(tl.dead, "p1", clock.t - 25)   # last play 25 s ago, still on the picture ~110 s behind
    tl.fresh_at = clock.t - 25
    tl.dead.seen(clock.t - 25, 15.0)
    dec._known["a"] = cand("a", league=League.CFB)
    assert dec.jump_may_leave("a", {}, clock.t, 3.2) is False
    tl.fresh_at = clock.t - 2000            # dark past the whole delay
    tl.dead.seen(clock.t - 2000, 15.0)
    assert dec.jump_may_leave("a", {}, clock.t, 3.2) is True


def test_d05_a_final_just_posted_holds_the_free_cut(cfg, clock):
    dec = decider(cfg, clock, "a", "b")
    dec.current = "a"
    tl = dec.timelines["a"]
    nfl_play(tl.dead, "p1", clock.t - 1)
    tl.final_at = clock.t
    tl.end_t = clock.t
    fresh(dec, clock)
    d = dec._jump(Decision(clock.t, "stay", "a", "r", State.WATCHING, ""), {"b": cand("b", league=League.CFB)}, clock.t,
                  "screen_gone")
    assert d is None and dec.jump_holds["bj_screen_gone_hold"] == 1


# ----- D-06 / D-08 / D-09 / D-11 ------------------------------------------------------------------------------------

def test_d06_wcbb_is_not_in_the_buffered_rotation(cfg, clock):
    assert "wcbb" not in bj.BJ_DEFAULTS["leagues"] and "wcbb" not in config.default("buffered_jump.leagues")
    dec = decider(cfg, clock, "w", league=League.WCBB)
    assert "w" not in dec._jump_pool({"w": cand("w", 80, League.WCBB)})


def test_d08_thin_margins_only_for_gated_leagues():
    assert set(config.default("decider.entry.thin_margin_leagues")) == {"nba", "nhl", "nfl"}


def test_d09_nfl_equals_cfb_only_between_two_crunch_games(cfg, clock):
    dec = decider(cfg, clock, "a")
    a, b = sc("a", 50, League.NFL, crunch=True), sc("b", 50, League.CFB, crunch=True)
    assert dec._crunch_rank(a)[1] == dec._crunch_rank(b)[1]
    assert dec._jump_prior(sc("a", 50, League.NFL)) > dec._jump_prior(sc("b", 50, League.CFB))   # NFL first otherwise


def test_d11_rule_one_keys_are_in_the_shipped_config():
    for k in ("enabled", "leagues", "rewind", "fastest_s", "max_rewind_s", "relaxed_margin_s", "urgent_margin_s", "visit_min_s",
              "no_wait_leagues", "allow_live_fallback"):
        assert config.default(f"decider.entry.{k}") is not None, k
    assert config.default("decider.mine_late_closeness") == 0.2


# ----- P-04 ---------------------------------------------------------------------------------------------------------

def test_p04_restart_holds_a_close_late_game_until_adopted(cfg, clock):
    dec = decider(cfg, clock, "a", "b")
    dec.note_restart("a", clock.t, high_leverage=True)
    c = {"b": cand("b", league=League.CFB)}
    d = dec._jump(Decision(clock.t, "switch", "b", "r", State.WATCHING, "", kind="start"), c, clock.t, "screen_gone")
    assert d is not None and d.action == "stay" and dec.jump_holds["bj_restart_hold"] == 1
    clock.t += 121
    assert not dec._restart_hold(c, clock.t)


def test_p04_a_quiet_saved_game_is_not_held_and_adopt_clears(cfg, clock):
    dec = decider(cfg, clock, "a", "b")
    dec.note_restart("a", clock.t, high_leverage=False)
    assert not dec._restart_hold({"a": cand("a", league=League.CFB)}, clock.t)
    dec.note_restart("a", clock.t, high_leverage=True)
    dec.adopt("a")
    assert dec._restored_screen is None


# ----- landing bars: basketball, the walk after a shot --------------------------------------------------------------

def test_shot_kind_reads_nba_and_college_text():
    assert shot_kind(League.NBA, "jump_shot", "X makes three", "Jump Shot") == "made"
    assert shot_kind(League.CBB, "jump_shot", "X made Three Point Jumper.", "JumpShot") == "made"
    assert shot_kind(League.NBA, "jump_shot", "X misses three", "Jump Shot") == "miss"
    assert shot_kind(League.NBA, "defensive_rebound", "X rebound", "Defensive Rebound") == "dreb"
    assert shot_kind(League.NBA, "offensive_rebound", "X rebound", "Offensive Rebound") == "oreb"
    assert shot_kind(League.NFL, "pass", "X makes a catch", "Pass") is None


def test_walk_after_a_made_shot_is_a_landing_never_a_leave():
    dt = DeadTracker(League.NBA)
    made(dt, "s", 1000.0)
    dt.add("n", 1016.0, 1016.0, "jump_shot", "Y misses", "Jump Shot", None, 2)
    dt.seen(1100.0, 10.0)
    assert dt.landing(1002.0, 1012.0, walk=WALK).state == "dead"
    assert dt.landing(1002.0, 1012.0).state != "dead"              # without the bar: mid-possession
    assert dt.dead_until(1005.0) is None                           # never a window to leave on


def test_walk_ends_before_the_next_action_never_a_late_join():
    dt = DeadTracker(League.NBA)
    made(dt, "s", 1000.0)
    dt.add("n", 1009.0, 1009.0, "jump_shot", "Y misses", "Jump Shot", None, 2)
    dt.seen(1100.0, 10.0)
    assert dt.landing(1002.0, 1006.5, walk=WALK).state == "dead"
    assert dt.landing(1002.0, 1008.0, walk=WALK).state != "dead"   # into the next action
    assert dt.landing(1015.0, 1020.0, walk=WALK).state != "dead"   # after the walk: a late join


def test_a_miss_walks_from_the_defensive_rebound_only():
    dt = DeadTracker(League.NBA)
    miss(dt, "m1", 1000.0)
    dreb(dt, "r1", 1002.0)
    dt.add("n", 1020.0, 1020.0, "jump_shot", "Y misses", "Jump Shot", None, 2)
    dt.seen(1100.0, 10.0)
    assert dt.landing(1003.5, 1012.0, walk=WALK).state == "dead"
    dt2 = DeadTracker(League.NBA)
    miss(dt2, "m1", 1000.0)
    oreb(dt2, "r1", 1002.0)
    dt2.add("n", 1020.0, 1020.0, "jump_shot", "Y misses", "Jump Shot", None, 2)
    dt2.seen(1100.0, 10.0)
    assert dt2.landing(1003.5, 1012.0, walk=WALK).state != "dead"  # the same possession goes on


def test_newest_shot_needs_the_feed_to_prove_no_transition():
    dt = DeadTracker(League.NBA)
    made(dt, "s", 1000.0)
    dt.seen(1020.0, 10.0)                                          # nothing logged after it, not covered yet
    assert dt.landing(1002.0, 1012.0, walk=WALK).state != "dead"
    dt.seen(1100.0, 10.0)                                          # covered far past the walk, still nothing logged
    assert dt.landing(1002.0, 1012.0, walk=WALK).state == "dead"


def transition_game(dt):
    """A dead ball, then a shot whose next action comes back in 4 s (the transition play we would have joined)."""
    dt.add("f", 1000.0, 1000.0, "foul", "Y personal foul", "Personal Foul", None, 2)
    dt.add("r", 1012.0, 1012.0, "substitution", "in for Z", "Substitution", None, 2)
    made(dt, "prior", 1030.0)                                      # the previous possession's bucket: a clean walk
    dt.add("n1", 1050.0, 1050.0, "jump_shot", "Y misses", "Jump Shot", None, 2)
    dreb(dt, "reb", 1052.0)                                        # the defense ran: a transition play follows
    made(dt, "fast", 1056.0)                                       # 4 s later: transition


def test_transition_play_is_never_a_landing():
    dt = DeadTracker(League.NBA)
    transition_game(dt)
    dt.seen(1200.0, 10.0)
    assert dt.landing(1053.0, 1055.0, walk=WALK).state != "dead"


def test_transition_diverts_to_the_prior_walk_by_rewind():
    dt = DeadTracker(League.NBA)
    transition_game(dt)
    dt.seen(1200.0, 10.0)
    now = 1100.0
    mu = now - 1053.0                                              # as is, the picture would come up in the transition
    p = plan_entry(dt, mu, 2.0, now, 0.5, can_rewind=True, step=5.0, max_back=60.0, walk=WALK, unknown_ok=False)
    assert p.ok and p.rewind_s > 0 and p.landing.kind == "walk"     # the previous possession's bucket, not the foul
    assert p.landing.resume <= 1044.0


def test_no_rewind_picture_waits_instead_of_landing_in_a_transition():
    dt = DeadTracker(League.NBA)
    transition_game(dt)
    dt.seen(1200.0, 10.0)
    now = 1100.0
    p = plan_entry(dt, now - 1053.0, 2.0, now, 0.5, can_rewind=False, step=5.0, max_back=60.0, walk=WALK, unknown_ok=False)
    assert not p.ok


def test_delay_margin_must_fit_inside_the_walk():
    dt = DeadTracker(League.NBA)
    made(dt, "s", 1000.0)
    dt.add("n", 1016.0, 1016.0, "jump_shot", "Y misses", "Jump Shot", None, 2)
    dt.seen(1100.0, 10.0)
    now, mu = 1100.0, 1100.0 + 0.5 - 1005.0                       # cost 0.5: the picture comes up at 1005
    assert plan_entry(dt, mu, 3.0, now, 0.5, can_rewind=False, walk=WALK, unknown_ok=False).ok
    assert not plan_entry(dt, mu, 8.0, now, 0.5, can_rewind=False, walk=WALK, unknown_ok=False).ok


def test_buffered_lands_in_a_walk_with_its_margin_on_both_pictures():
    dt = DeadTracker(League.NBA)
    made(dt, "s", 1000.0)
    dt.add("n", 1016.0, 1016.0, "jump_shot", "Y misses", "Jump Shot", None, 2)
    dt.seen(1100.0, 10.0)
    now = 1100.0
    p = bj.Pic(dt, now + 2.0 - 1004.0 - 2.0, 1.0, "b", WALK)       # the picture comes up in the walk, sigma 1
    assert bj.lands(p, now, 2.0, 3.2, 2.0, 2.0)[0]
    # no walk allowance: a dead-ball-only picture (normal mode) refuses the same moment
    assert not bj.lands(bj.Pic(dt, p.mu, 1.0, "b"), now, 2.0, 3.2, 2.0, 2.0)[0]


def test_park_lands_a_paused_picture_inside_the_walk():
    dt = DeadTracker(League.NBA)
    made(dt, "s", 1000.0)
    dt.add("n", 1016.0, 1016.0, "jump_shot", "Y misses", "Jump Shot", None, 2)
    dt.seen(1200.0, 10.0)
    h = bj.Pic(dt, 1099.0 - 1002.0, 1.0, "b", WALK)                # shows real 1002 (in the walk) at wall 1099
    pin = bj.park_in(h, 1099.0, None)
    assert pin is not None and pin > 0
    paused_at = 1099.0 + pin
    frozen = bj.frozen(h, paused_at + 5.0, paused_at)              # 5 s into the park, the frame stood still
    assert bj.lands(frozen, paused_at + 5.0, 2.0, 3.2, 2.0, 2.0)[0]


def test_walk_is_off_outside_the_jump_modes_and_basketball(cfg, clock):
    dec = decider(cfg, clock, "b", league=League.NBA, jump=False)
    assert dec._join_args(cand("b", league=League.NBA), urgent=False) == {}
    assert cuts.walk_params(None, League.NFL) is None
    assert cuts.walk_params(None, League.NBA) == WALK
    assert cuts.walk_params(None, League.CBB) == WALK
    assert cuts.walk_params(None, League.WCBB) is None             # N2


def test_decider_passes_the_walk_to_the_buffered_picture_for_cbb_and_nba(cfg, clock):
    dec = decider(cfg, clock, "b", league=League.CBB)
    dec.timelines["b"].dead.add("x", clock.t - 200, clock.t - 200, "foul", "foul", "Personal Foul", None, 1)
    fresh(dec, clock)
    assert dec._bj_pic("b", cand("b", league=League.CBB), clock.t).walk == WALK


# ----- landing bars: football, after the play ------------------------------------------------------------------------

def test_football_waits_past_the_replay_when_the_window_still_has_room():
    dt = DeadTracker(League.NFL)
    nfl_play(dt, "p1", 1000.0)                                     # whistle 1000 (dead from 1003)
    nfl_play(dt, "p2", 1060.0)                                     # snap 1050: a long window
    dt.seen(1200.0, 10.0)
    now = 1100.0
    mu = now + 0.5 - 1010.0                                        # the picture would come up 10 s after the whistle
    wait = plan_entry(dt, mu, 3.0, now, 0.5, can_rewind=False, unknown_ok=False, after_play_s=18.0)
    assert not wait.ok and "replay" in wait.why
    later = plan_entry(dt, now + 0.5 - 1025.0, 3.0, now, 0.5, can_rewind=False, unknown_ok=False, after_play_s=18.0)
    assert later.ok


def test_football_preference_never_blocks_the_only_landing():
    dt = DeadTracker(League.NFL)
    nfl_play(dt, "p1", 1000.0)
    nfl_play(dt, "p2", 1030.0)                                     # snap 1020: no room after the replay
    dt.seen(1200.0, 10.0)
    now = 1100.0
    assert plan_entry(dt, now + 0.5 - 1010.0, 3.0, now, 0.5, can_rewind=False, unknown_ok=False, after_play_s=18.0).ok


def test_football_lands_seconds_before_the_snap_and_never_into_it():
    dt = DeadTracker(League.CFB)
    nfl_play(dt, "p1", 1000.0)
    nfl_play(dt, "p2", 1054.0)                                     # snap 1044
    dt.seen(1200.0, 10.0)
    h = bj.Pic(dt, 40.0, 1.0, "b")
    lead = bj.park_lead_s(h)
    pin = bj.park_in(h, 1050.0, None)                              # the picture shows real 1010
    assert pin is not None
    paused_at = 1050.0 + pin
    frame = paused_at - 40.0
    assert abs(1044.0 - frame - lead) < 1e-6                                  # the pause sits a few seconds before the snap
    assert not bj.lands(bj.Pic(dt, 1045.0 - 1041.0, 1.0, "b"), 1045.0, 2.0, 3.2, 2.0, 2.0)[0]  # late: the snap is airing


def test_long_dead_never_lands_a_break_away_from_its_snap():
    v, h = DeadTracker(League.NFL), DeadTracker(League.NFL)
    now, mu = 10_000.0, 100.0
    x = now - mu
    nfl_play(v, "v1", x - 10)
    nfl_play(v, "v2", x + 200)                                     # V: long way to its next snap
    nfl_play(h, "h1", x - 10)
    nfl_play(h, "h2", x + 110)                                     # H: ~100 s from its snap (its own break)
    for dt in (v, h):
        dt.seen(now, 15.0)
    assert not bj.cut_now(bj.Pic(v, mu, 1.0), bj.Pic(h, mu, 1.0), now).go


# ----- D-08: behaviour, not just the list -----------------------------------------------------------------------------

def _only_urgent_landing(cfg, clock, league):
    """A crunch entry whose landing fits the 2 s urgent margin but not the 2 sigma one (a Prime picture: no rewind)."""
    dec = decider(cfg, clock, "a", "b", league=league, jump=False)
    dec._replayable = lambda x: False
    t0 = clock.t - 42.0                                 # the picture comes up 10 s into the 11 s window after t0's whistle
    nfl_play(dec.timelines["b"].dead, "p1", t0)
    nfl_play(dec.timelines["b"].dead, "p2", t0 + 24.0)  # snap t0 + 14
    fresh(dec, clock)
    dec.current = "a"
    return dec._time_entry(switch("crunch", now=clock.t), {"b": cand("b", 90, league, crunch=True, crunch_now=True)},
                           clock.t, (None, None))


def test_d08_a_gated_league_takes_the_urgent_landing(cfg, clock):
    assert _only_urgent_landing(cfg, clock, League.NFL).action == "switch"


def test_d08_cfb_never_takes_the_thin_margin(cfg, clock):
    assert _only_urgent_landing(cfg, clock, League.CFB).action == "stay"
