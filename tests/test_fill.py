"""Fill ladder, montage, off-night plan: pure, synthetic dev-night-shaped data (no locked nights, no TV, no adb).

Why: WP-19 must prove the ladder never picks a commercial or a result that spoils a game Nick can still watch,
and that with fill.enabled off nothing about the reel changes.
"""
import dataclasses as dc

from redzone.moments.fill import (ASK_OPTIONS, FillParams, GameInfo, choose_fill, enabled, plan, plan_montage,
                                  plan_off_night)
from redzone.moments.detect import Moment
from redzone.moments.detect import detect as detect_moments
from redzone.reel import Clip, plan_reel

NOW = 1_800_000_000.0


def g(gid, league="nfl", status="live", **kw):
    return GameInfo(gid, league, status, **kw)


def m(gid, tier=1, kind="td", t=NOW - 60, pid=""):
    return Moment(t, gid, tier, kind, pid or f"{gid}-{kind}-{int(t)}")


def clip(i, gid, pts=30.0, age=3600.0, **kw):
    kw.setdefault("seg", 10.0)
    return Clip(f"c{i}", gid, "nfl", f"Player{i} Name{i} touchdown", "u", NOW - age - i, 30.0, pts, **kw)


# -- flag ---------------------------------------------------------------------------------------------------

def test_flag_defaults_off_and_reads_fill_enabled():
    assert enabled(lambda k, d=None: d) is False
    assert enabled(lambda k, d=None: True if k == "fill.enabled" else d) is True
    assert plan(lambda k, d=None: d, [g("a")], [], now=NOW) is None


def test_flag_off_reel_behaviour_unchanged():
    """Importing and calling fill with the flag off leaves plan_reel's picks and Clip data exactly as before."""
    clips = [clip(i, f"g{i % 3}", pts=20 + i) for i in range(8)]
    before = [c.id for c in plan_reel([dc.replace(c) for c in clips], now=NOW, max_age_s=7200)]
    assert plan(lambda k, d=None: d, [g("g0", "nfl", "final")], [m("g0")], now=NOW, clips_ready=True) is None
    plan_montage(clips, [g("g0", status="final")], now=NOW)          # montage works on copies
    assert [c.seg for c in clips] == [10.0] * 8
    assert [c.id for c in plan_reel([dc.replace(c) for c in clips], now=NOW, max_age_s=7200)] == before


# -- the ladder ---------------------------------------------------------------------------------------------

def test_step1_joins_best_dead_ball_game_mine_then_league_order():
    games = [g("nba", "nba", joinable=True), g("nfl", joinable=True), g("mine", "nhl", joinable=True, mine=True)]
    assert choose_fill(games, [], now=NOW).game_id == "mine"
    assert choose_fill(games[:2], [], now=NOW).game_id == "nfl"


def test_never_joins_mid_play_or_a_commercial():
    games = [g("a", joinable=False), g("b", joinable=True, on_break=True)]
    c = choose_fill(games, [], now=NOW)
    assert c.action != "join" and c.game_id is None


def test_commercial_only_night_never_picks_a_commercial_and_ends_at_nick():
    games = [g("a", on_break=True, joinable=True), g("b", "nba", on_break=True)]
    c = choose_fill(games, [], now=NOW, clips_ready=True)
    assert (c.step, c.action, c.options) == (5, "ask_nick", ASK_OPTIONS)


def test_step2_reel_orders_mine_then_tier_then_league_and_needs_clips_ready():
    games = [g("a"), g("b", "nba"), g("c", "nhl", mine=True)]
    ms = [m("b", 1, "clutch_fg"), m("a", 2, "rz_trip"), m("a", 1), m("c", 2, "x")]
    c = choose_fill(games, ms, now=NOW, clips_ready=True)
    assert c.step == 2 and [x.game_id for x in c.moments] == ["c", "a", "b", "a"]
    assert choose_fill(games, ms, now=NOW, clips_ready=False).step == 5


def test_seen_and_stale_moments_are_skipped():
    games = [g("a")]
    old, new = m("a", t=NOW - 5000), m("a", kind="turnover")
    c = choose_fill(games, [old, new], now=NOW, clips_ready=True, seen=[(new.game_id, new.play_id)])
    assert c.step == 5


def test_step3_tier3_only_from_live_games():
    ms = [m("a", 3, "dunk"), m("f", 3, "dunk")]
    c = choose_fill([g("a"), g("f", status="final")], ms, now=NOW, clips_ready=True)
    assert c.step == 3 and [x.game_id for x in c.moments] == ["a"]


def test_step4_scores_are_finals_only_and_never_an_unfinished_result():
    games = [g("live1"), g("pre1", status="pre"), g("fin", "nba", "final"), g("fin2", status="final")]
    c = choose_fill(games, [], now=NOW)
    assert (c.step, c.games) == (4, ("fin2", "fin"))
    assert choose_fill(games, [], now=NOW, scores_shown=True).step == 5


def test_watchable_game_never_leaks_a_moment_or_a_score():
    games = [g("mine", mine=True, watchable=True), g("fin", status="final", watchable=True),
             g("fin_live_tv", "nba", "final", watchable=True)]
    ms = [m("mine"), m("mine", 3, "x"), m("fin")]
    c = choose_fill(games, ms, now=NOW, clips_ready=True)
    assert c.step == 5, c


def test_unknown_game_fails_closed():
    assert choose_fill([], [m("ghost")], now=NOW, clips_ready=True).step == 5


def test_detected_dev_style_play_by_play_feeds_the_ladder():
    play = {"id": "1", "type": {"text": "Passing Touchdown"}, "text": "TD", "scoringPlay": True,
            "wallclock": "2026-10-01T00:00:10Z", "statYardage": 20,
            "start": {"down": 1, "yardsToEndzone": 20, "distance": 10}}
    ms = list(detect_moments("nfl", {"drives": {"previous": [{"plays": [play]}]}}))
    assert [x.kind for x in ms] == ["td", "rz_trip"]
    t = ms[0].t
    c = choose_fill([g("g1")], [dc.replace(x, game_id="g1") for x in ms], now=t + 30, clips_ready=True)
    assert c.step == 2 and c.moments[0].kind == "td"


# -- montage ------------------------------------------------------------------------------------------------

def test_montage_only_finished_unwatchable_games_oldest_first():
    games = [g("f1", status="final"), g("f2", "nba", "final"), g("live"), g("w", status="final", watchable=True)]
    clips = [clip(i, ["f1", "f2", "live", "w", "ghost"][i % 5], pts=30 + i, age=7200 - i * 100) for i in range(30)]
    mt = plan_montage(clips, games, now=NOW)
    assert mt.clips and {c.game_id for c in mt.clips} <= {"f1", "f2"}
    assert [c.published for c in mt.clips] == sorted(c.published for c in mt.clips)
    assert mt.seconds <= 600 and len(mt.clips) >= 6


def test_montage_empty_when_too_few_or_all_small():
    games = [g("f1", status="final")]
    assert plan_montage([clip(i, "f1") for i in range(3)], games, now=NOW).clips == ()
    assert plan_montage([clip(i, "f1", pts=10) for i in range(10)], games, now=NOW).clips == ()


def test_montage_tier1_moment_lifts_a_clip():
    games = [g("a", status="final"), g("b", status="final"), g("c", status="final")]
    clips = [clip(i, "abc"[i % 3], pts=24.0) for i in range(9)]
    clips[0] = dc.replace(clips[0], play_at=NOW - 3000)
    mt = plan_montage(clips, games, now=NOW, moments=[Moment(NOW - 3010, "a", 1, "td")],
                      params=FillParams(montage_max_clips=6, montage_min_clips=6))
    assert "c0" in {c.id for c in mt.clips}


# -- off night ----------------------------------------------------------------------------------------------

def test_off_night_waits_for_a_soon_game_then_replays_then_goes_off():
    fin = [g("f1", status="final")]
    clips = [clip(i, "f1", pts=30 + i) for i in range(10)]
    assert plan_off_night(fin + [g("l")], clips, now=NOW).kind == "not_off"
    w = plan_off_night(fin + [g("p", status="pre", start=NOW + 3600)], clips, now=NOW)
    assert (w.kind, w.next_start) == ("wait", NOW + 3600)
    far = plan_off_night(fin + [g("p", status="pre", start=NOW + 86400)], clips, now=NOW)
    assert far.kind == "reel" and far.seconds <= 180 and far.clips
    assert plan_off_night(fin, clips[:2], now=NOW).kind == "off"
    assert plan_off_night([], clips, now=NOW).kind == "off"      # unknown games: nothing safe to replay


def test_params_bad_value_keeps_default():
    p = FillParams.from_config(lambda k, d=None: {"fill.montage_s": "x", "fill.off_night_s": 90}.get(k, d))
    assert p.montage_s == 600.0 and p.off_night_s == 90.0
