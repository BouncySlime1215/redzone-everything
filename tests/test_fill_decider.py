"""The fill ladder as the decider calls it (WP-18 wiring WP-19): facts proven, or the ladder fails closed. Fakes only.

Why: fill.plan is only as safe as joinable, on_break and watchable; the decider must supply them, flag off must do nothing.
"""
from redzone.decider.decider import REEL, Candidate, Decider, Decision, State
from redzone.decider.screen import DelayBook
from redzone.models import League
from redzone.moments import fill
from redzone.scoring.engine import Scored


def sc(gid, *, is_break=False, **kw):
    return Scored(gid, League.NFL, 30.0, 30.0, 30.0, 1.0, 0.0, f"r {gid}", [], is_break, None, None, 1.0, **kw)


def rig(cfg, clock, *, on=True):
    cfg.data["fill"] = {"enabled": on}
    delays = DelayBook({"youtubetv": 35.0}, sigma_default=4.0, clock=clock)
    dec = Decider(cfg, delays, clock=clock)
    dec.entry_cost = lambda t: 3.0
    for gid in ("a", "b"):
        for v in (34.5, 35.0, 35.5):
            delays.calibrate(f"youtubetv:{gid}", v)
    x = clock.t - 35.0
    for gid in ("a", "b"):
        tl = dec.timeline(gid, League.NFL)
        tl.fresh_at = clock.t
        tl.dead.seen(clock.t, 5.0)
        tl.dead.add(f"{gid}1", x - 20, x - 20, "rush", "run for 3", "Rush", None, 1)
        tl.dead.add(f"{gid}2", x + 80, x + 80, "rush", "run for 3", "Rush", None, 1)
    dec.current, dec.since = "a", clock.t - 100
    return dec


def stay(clock):
    return Decision(clock.t, "stay", "a", "x", State.WATCHING)


def games(a_break=True, b_break=False, **b_kw):
    return {"a": Candidate(sc("a", is_break=a_break), "youtubetv:a"), "b": Candidate(sc("b", is_break=b_break, **b_kw), "youtubetv:b")}


def test_flag_off_the_ladder_is_never_asked(cfg, clock, monkeypatch):
    dec = rig(cfg, clock, on=False)
    monkeypatch.setattr(fill, "plan", lambda *a, **k: (_ for _ in ()).throw(AssertionError("called with the flag off")))
    d = dec._fill(stay(clock), games(), clock.t, "normal")
    assert d.action == "stay" and dec.fill_choice is None


def test_on_a_break_the_ladder_joins_a_game_at_a_dead_ball(cfg, clock):
    dec = rig(cfg, clock)
    d = dec._fill(stay(clock), games(), clock.t, "normal")
    assert d.action == "switch" and d.target == "b" and d.kind == "jump" and dec.fill_choice.action == "join"


def test_never_joins_a_game_on_a_break(cfg, clock):
    dec = rig(cfg, clock)
    d = dec._fill(stay(clock), games(b_break=True), clock.t, "normal")
    assert d.action == "stay" and dec.fill_choice.action != "join"


def test_joinable_is_proven_not_assumed(cfg, clock):
    dec = rig(cfg, clock)
    dec.timelines["b"].dead.marks.clear()
    dec.timelines["b"].dead._sorted = dec.timelines["b"].dead._windows = None
    infos = dec._fill_games(games(), clock.t, False)
    assert not next(g for g in infos if g.game_id == "b").joinable
    assert dec._fill(stay(clock), games(), clock.t, "normal").action == "stay"


def test_the_game_on_screen_is_not_a_join(cfg, clock):
    dec = rig(cfg, clock)
    assert not next(g for g in dec._fill_games(games(a_break=False), clock.t, False) if g.game_id == "a").joinable


def test_a_game_that_is_yours_is_watchable_so_it_never_spoils(cfg, clock):
    dec = rig(cfg, clock)
    assert next(g for g in dec._fill_games(games(mine=True), clock.t, False) if g.game_id == "b").watchable
    assert not next(g for g in dec._fill_games(games(), clock.t, False) if g.game_id == "b").watchable


def test_no_fill_while_the_screen_game_is_live(cfg, clock):
    dec = rig(cfg, clock)
    assert dec._fill(stay(clock), games(a_break=False), clock.t, "normal").action == "stay" and dec.fill_choice is None


def test_a_tap_or_replay_owner_is_left_alone(cfg, clock):
    dec = rig(cfg, clock)
    assert dec._fill(stay(clock), games(), clock.t, "user").action == "stay" and dec.fill_choice is None


def test_no_cut_when_the_screen_is_not_at_a_dead_ball(cfg, clock):
    dec = rig(cfg, clock)
    dec._jump_gate = lambda *a, **k: None
    assert dec._fill(stay(clock), games(), clock.t, "normal").action == "stay"
    assert dec.fill_choice.action == "join"


def test_a_reel_step_cuts_to_the_reel_only_when_one_is_ready(cfg, clock):
    dec = rig(cfg, clock)
    c = games(b_break=True)
    assert dec._fill(stay(clock), c, clock.t, "normal").target != REEL
    c[REEL] = Candidate(sc(REEL), "reel:x")
    import dataclasses as dc
    from redzone.moments.detect import Moment
    dec._fill_moments = lambda *a: [Moment(clock.t - 60, "b", 1, "td", "p1")]
    d = dec._fill(stay(clock), c, clock.t, "normal")
    assert dc.is_dataclass(d) and d.action == "switch" and d.target == REEL


def test_a_league_with_no_tier_list_feeds_no_moments(cfg, clock):
    from redzone.decider.screen import Event
    dec = rig(cfg, clock)
    tl = dec.timeline("w", League.WCBB)
    tl.events.append(Event(clock.t - 60, "play", "shot", True, "p", "make"))
    assert dec._fill_moments({"w": Candidate(sc("w"), "youtubetv:w")}, clock.t) == []
