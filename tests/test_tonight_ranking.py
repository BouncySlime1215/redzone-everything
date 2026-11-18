"""Tonight's ranking / big-play / delay-default fixes (risk sweep, Nov 2)."""
import pytest
from pathlib import Path

import yaml

from redzone.decider.decider import Candidate, Decider
from redzone.scoring.engine import Scored
from redzone.decider.screen import DelayBook, Timeline
from redzone.models import GameState, League, Phase, Play, Team


def nba(period, clock_s, hs, as_, plays):
    return GameState(league=League.NBA, game_id="g", home=Team("WSH", score=hs), away=Team("ORL", score=as_),
                     phase=Phase.LIVE, period=period, clock_s=clock_s, recent_plays=plays,
                     last_play=plays[-1] if plays else None, updated_at=1000.0)


def _big_after(period, clock_s, before, after, kind):
    tl = Timeline(League.NBA, clock=lambda: 1000.0)
    tl.observe(nba(period, clock_s, *before, [Play("p0", "jumper", "jump_shot", True, wallclock=990.0)]))
    tl.observe(nba(period, clock_s, *after, [Play("p1", "shot", kind, True, wallclock=998.0)]))
    return [e.big for e in tl.events if e.pid == "p1"]


def test_nba_clutch_field_goal_is_big():
    assert _big_after(4, 90.0, (100, 98), (100, 101), "three") == [True]


def test_nba_routine_baskets_and_free_throws_are_not_big():
    assert _big_after(2, 90.0, (50, 48), (50, 51), "three") == [False]       # not late
    assert _big_after(4, 400.0, (100, 98), (100, 101), "three") == [False]   # not the last 2:00
    assert _big_after(4, 90.0, (110, 90), (110, 93), "three") == [False]     # not close
    assert _big_after(4, 90.0, (100, 98), (100, 99), "free_throw_-_2_of_2") == [False]


def test_ranking_cost_drops_to_a_flip_when_the_target_is_preloaded(cfg, clock):
    cfg.update({"decider": {"switch_cost": {"youtubetv": 15.5}}})
    dec = Decider(cfg, DelayBook({"youtubetv": 40}, 1, clock=clock), clock=clock)
    assert dec._rank_cost("youtubetv:mnf") == 15.5          # no live estimate: static cost
    dec.deadline_cost = lambda t: 7.0 if t == "youtubetv:mnf" else 30.0
    assert dec._rank_cost("youtubetv:mnf") == 7.0           # preloaded on the hidden stick
    assert dec._rank_cost("youtubetv:cold") == 15.5         # cold: never more than the static cost
    assert dec._cost("youtubetv:mnf") == 15.5               # _cost itself is unchanged


def test_espn_plus_has_an_explicit_delay_default():
    root = Path(__file__).resolve().parent.parent
    d = yaml.safe_load((root / "redzone/default_config.yaml").read_text())["delay"]["default"]
    assert d["espn"] == 40


def _c(gid, league, score, personal=0.0):
    return Candidate(Scored(gid, league, score, score, score, 1.0, personal, "x", [], False, None, None, 1.0),
                     f"youtubetv:{gid}")


def test_sticky_home_break_only_visits_games_we_can_leave(cfg, clock):
    dec = Decider(cfg, DelayBook({"youtubetv": 40}, 15, clock=clock), clock=clock)
    dec.timeline("n", League.NFL)
    dec.timeline("h", League.NBA)
    watchable = {"h": _c("h", League.NBA, 60, personal=25), "n": _c("n", League.NFL, 80)}
    assert dec._break_destination(watchable, "h") is None       # uncalibrated: no clean way back
    for s in (40.0, 41.0):
        dec.delays.calibrate("youtubetv:n", s)
    assert dec._break_destination(watchable, "h") == "n"
    cfg.update({"decider": {"sticky_visit_needs_calibration": False}})
    dec.delays.book.clear()
    assert dec._break_destination(watchable, "h") == "n"         # switchable off from config


@pytest.mark.playbook("FEED-07")
def test_vanished_basketball_game_is_held_longer_than_football(cfg, clock):
    from redzone.decider.decider import GONE_MAX_HOLD_CONTINUOUS_S, GONE_MAX_HOLD_S
    from redzone.decider.screen import Event
    cfg.update({"decider": {"entry": {"allow_live_fallback": True}}})   # [D-01] no play-by-play here; this is the hold on a vanished game, not the landing
    dec = Decider(cfg, DelayBook({"youtubetv": 40}, 15, clock=clock), clock=clock)
    assert dec.decide({"a": _c("a", League.NBA, 80), "b": _c("b", League.NBA, 50)}).target == "a"
    tl = dec.timeline("a", League.NBA)
    tl.fresh_at = clock.t
    tl.events.append(Event(clock.t - 5, "play", "jumper"))
    clock.t += 30
    b = {"b": _c("b", League.NBA, 50)}                           # "a" dropped out of the feed (rollover)
    assert dec.decide(b).action == "stay"
    clock.t += GONE_MAX_HOLD_S + 1
    assert dec.decide(b).action == "stay"                         # football would leave blind here
    clock.t += GONE_MAX_HOLD_CONTINUOUS_S
    assert dec.decide(b).action == "switch"


def test_start_waits_for_your_team_instead_of_an_inescapable_neutral_game(cfg, clock):
    cfg.update({"decider": {"entry": {"allow_live_fallback": True}}})   # [D-01] no play-by-play here; this is the wait for your team, not the landing
    dec = Decider(cfg, DelayBook({"youtubetv": 40}, 15, clock=clock), clock=clock)
    nhl = {"det": _c("det", League.NHL, 70)}
    d = dec.decide(nhl, commit=False)
    assert d.action == "switch" and d.target == "det"            # no hook: unchanged
    dec.my_tip_at = lambda: clock.t + 1800                       # WSH tips at 7:30
    d = dec.decide(nhl, commit=False)
    assert d.action == "stay" and d.reason.startswith("waiting for your team")
    dec.my_tip_at = lambda: clock.t + 3600                       # tip is far off: start normally
    assert dec.decide(nhl, commit=False).action == "switch"
    dec.my_tip_at = lambda: clock.t + 600
    nfl = {"mnf": _c("mnf", League.NFL, 70)}                     # a discrete game can be left any gap
    assert dec.decide(nfl, commit=False).action == "switch"
    for s in (40.0, 41.0):
        dec.delays.calibrate("youtubetv:det", s)                 # calibrated: whistles are exits
    assert dec.decide(nhl, commit=False).action == "switch"
