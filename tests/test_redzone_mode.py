"""RedZone mode: NFL drive priority, the "what you missed" reel, and the scorecard metrics for both."""
from redzone.lab import backtest
from redzone.models import League


def _p(pid, text="run", typ="Rush", ytg=60, down=1, score=False):
    return {"id": pid, "text": text, "type": {"text": typ}, "scoringPlay": score,
            "start": {"down": down, "yardsToEndzone": ytg}}


def _replay(drives):
    plays = [p for d in drives for p in d["plays"]]
    data = {"drives": {"previous": drives}}
    return backtest.Replay(League.NFL, "g", data, plays, [1000.0 + 40 * i for i in range(len(plays))])


def test_hot_drive_plays_are_scoring_or_red_zone_drives_only():
    punt = {"isScore": False, "plays": [_p("1", ytg=75), _p("2", ytg=70), _p("3", "punts", "Punt", 70, 4)]}
    td = {"isScore": True, "plays": [_p("4", ytg=60), _p("5", "TOUCHDOWN", "Passing Touchdown", 30, 2, True),
                                    _p("6", "extra point", "Extra Point Good", 15, 0)]}
    stalled = {"isScore": False, "plays": [_p("7", ytg=25), _p("8", ytg=18, down=2), _p("9", ytg=18, down=3)]}
    r = _replay([punt, td, stalled])
    hot = [r.plays[i]["id"] for i in backtest.hot_drive_plays(r)]
    assert hot == ["4", "5", "7", "8", "9"]   # no punt-fest plays, no PAT (not a scrimmage down)


def test_behind_live_counts_only_replay_screen_time():
    watching = [(0.0, "a"), (100.0, "replay:a"), (130.0, "b"), (200.0, "replay:b")]
    assert backtest.behind_live_seconds(watching, 0.0, 220.0) == 30.0 + 20.0
    assert backtest.behind_live_seconds(watching, 120.0, 210.0) == 10.0 + 10.0


def test_drive_coverage_counts_live_and_other_sport_misses():
    td = {"isScore": True, "plays": [_p("1", ytg=60), _p("2", ytg=40), _p("3", ytg=20)]}
    r = _replay([td])
    other = backtest.Replay(League.NBA, "n", {}, [{"id": "x"}], [0.0])
    lag, play_len, tune = 30.0, 7.0, 5.0
    # On the game for the first play only, then on an NBA game.
    watching = [(900.0, "g"), (1000.0 + 40 + lag - play_len - 10, "n")]
    n, live, elsewhere, rz = backtest.drive_coverage([r, other], watching, lag, play_len, tune, 5000.0)
    assert (n, live, elsewhere, rz) == (3, 1, 2, 0)


# --- drive heat (scoring/drive.py) ---------------------------------------------------------------

from dataclasses import replace  # noqa: E402

from redzone.decider.decider import REDZONE, Candidate, Decider, Missed  # noqa: E402
from redzone.decider.screen import DelayBook  # noqa: E402
from redzone.models import Play  # noqa: E402
from redzone.scoring.drive import DriveBook, DriveScorer, expected_points  # noqa: E402
from redzone.scoring.engine import Scored  # noqa: E402
from tests.conftest import nfl  # noqa: E402


def _play(pid, text="run for 4 yards"):
    return Play(pid, text, "rush")


def test_expected_points_curve_is_monotone_and_anchored():
    assert abs(expected_points(80) - 0.4) < 1e-6 and abs(expected_points(20) - 3.7) < 1e-6
    vals = [expected_points(y) for y in range(99, 0, -1)]
    assert vals == sorted(vals)


def test_a_drive_that_moves_heats_up_and_a_change_of_possession_cools_it(clock):
    book = DriveBook(clock=clock)
    h0 = book.observe(nfl(pos="WSH", down=1, dist=10, ytg=75, last_play=_play("1")))
    clock.t += 30
    h1 = book.observe(nfl(pos="WSH", down=1, dist=10, ytg=45, last_play=_play("2", "pass for 30 yards")))
    clock.t += 30
    h2 = book.observe(nfl(pos="WSH", down=1, dist=10, ytg=18, last_play=_play("3")))
    assert h0.heat < 0.3 < h1.heat < h2.heat and h2.heat >= 0.75
    assert "yard" in h1.why or "driving" in h2.why
    clock.t += 30
    h3 = book.observe(nfl(pos="DAL", down=1, dist=10, ytg=80, last_play=_play("4", "INTERCEPTED")))
    assert h3.heat < 0.3      # the ball changed hands: that drive is over, no slow release


def test_punt_fest_marks_the_game_dead(clock):
    book = DriveBook(clock=clock)
    for i, t in enumerate(["WSH", "DAL"] * 3):
        book.observe(nfl(pos=t, down=1, dist=10, ytg=75, last_play=_play(f"a{i}")))
        clock.t += 60
        h = book.observe(nfl(pos=t, down=4, dist=8, ytg=70, last_play=_play(f"b{i}")))
        clock.t += 60
    assert h.dead == 1.0 and h.heat < 0.5
    h = book.observe(nfl(pos="WSH", down=2, dist=8, ytg=72, last_play=_play("z")))
    assert h.dead > 0 and "empty drives" in h.why


def test_drive_scorer_lifts_a_hot_drive_and_never_touches_garbage_time(cfg, clock):
    sc = DriveScorer(cfg, clock=clock)
    cold = sc.score(nfl(gid="c", q=2, clock=600, pos="WSH", down=1, dist=10, ytg=75, hs=7, as_=3,
                        last_play=_play("1")))
    sc.score(nfl(gid="h", q=2, clock=600, pos="WSH", down=1, dist=10, ytg=80, hs=7, as_=3, last_play=_play("2")))
    clock.t += 30
    hot = sc.score(nfl(gid="h", q=2, clock=560, pos="WSH", down=1, dist=10, ytg=15, hs=7, as_=3,
                       last_play=_play("3", "pass for 65 yards")))
    assert hot.factors["drive_heat"] >= 0.75 and cold.factors["drive_heat"] < 0.3
    assert any("yard" in t for t in hot.tags)
    blow = sc.score(nfl(gid="b", q=4, clock=100, pos="WSH", down=1, dist=10, ytg=10, hs=45, as_=3, wp=0.999,
                        last_play=_play("9")))
    assert blow.garbage and blow.factors["drive_heat"] == 0.0


# --- NFL priority in the decider ---------------------------------------------------------------

def _sc(gid, score, league=League.NFL, heat=0.0, crunch=False, personal=0.0, is_break=False):
    return Scored(gid, league, score, score, score, 1.0, personal, f"reason {gid}", [], is_break,
                  None, None, 1.0, crunch=crunch, factors={"drive_heat": heat})


def _dec(cfg, clock):
    return Decider(cfg, DelayBook({"youtubetv": 40.0}, sigma_default=3.0, clock=clock), clock=clock)


def test_hot_nfl_drive_outranks_other_sports_but_not_crunch_or_your_team(cfg, clock):
    d = _dec(cfg, clock)
    c = {"nfl": Candidate(_sc("nfl", 55, heat=0.8), "youtubetv:nfl"),
         "nba": Candidate(_sc("nba", 70, League.NBA), "youtubetv:nba"),
         "nhl": Candidate(_sc("nhl", 72, League.NHL, crunch=True), "youtubetv:nhl"),
         "wsh": Candidate(_sc("wsh", 68, League.NBA, personal=20), "youtubetv:wsh")}
    out = d._nfl_priority(c)
    assert out["nba"].scored.score == 55 - cfg.get("redzone_mode.priority_gap")
    assert "NFL drive on" in out["nba"].scored.tags
    assert out["nhl"].scored.score == 72 and out["wsh"].scored.score == 68 and out["nfl"].scored.score == 55
    # A cold drive changes nothing.
    c["nfl"] = Candidate(_sc("nfl", 55, heat=0.2), "youtubetv:nfl")
    assert d._nfl_priority(c)["nba"].scored.score == 70
    # ...and neither does a hot drive in garbage time.
    c["nfl"] = Candidate(replace(_sc("nfl", 55, heat=0.9), garbage=True), "youtubetv:nfl")
    assert d._nfl_priority(c)["nba"].scored.score == 70


def test_redzone_channel_gets_the_hottest_drives_lift(cfg, clock):
    d = _dec(cfg, clock)
    c = {"nfl": Candidate(_sc("nfl", 55, heat=0.5), "youtubetv:nfl")}
    out = d._with_redzone(c, "youtubetv:redzone")
    assert out[REDZONE].scored.score == cfg.get("decider.redzone_score") + 0.5 * cfg.get("redzone_mode.heat_points")


def test_no_catch_up_while_a_drive_is_in_scoring_position(cfg, clock):
    d = _dec(cfg, clock)
    m = Missed("old", "p", "TD", "td", clock.t - 60, 80)
    d.queue.append(m)
    c = {"old": Candidate(_sc("old", 30), "youtubetv:old"),
         "rz": Candidate(_sc("rz", 50, heat=0.9), "youtubetv:rz")}
    assert d._best_replay(c, clock.t, None, False) is None
    c["rz"] = Candidate(_sc("rz", 50, heat=0.9, is_break=True), "youtubetv:rz")
    assert d._best_replay(c, clock.t, None, False) is m      # its drive is in a break: catch up now


# --- the "what you missed" reel ------------------------------------------------------------------

def _queue(d, *ms):
    d.queue.extend(ms)
    return {m.game_id: Candidate(_sc(m.game_id, 20), f"youtubetv:{m.game_id}") for m in ms}


def test_reel_is_capped_then_goes_live_but_your_teams_plays_still_show(cfg, clock):
    d = _dec(cfg, clock)
    a, b, w = (Missed("a", "1", "TD a", "td", clock.t - 60, 75), Missed("b", "2", "TD b", "td", clock.t - 50, 75),
               Missed("w", "3", "TD w", "td", clock.t - 40, 60, mine=True))
    c = _queue(d, a, b, w)
    c["live"] = Candidate(_sc("live", 60), "youtubetv:live")
    assert d._best_replay(c, clock.t, None, False) is w          # your team first
    d.reel_n, d.reel_t0 = cfg.get("redzone_mode.reel_max_clips"), clock.t - 30
    assert d._best_replay(c, clock.t, None, False) is w          # past the cap: only your team's
    d.queue.remove(w)
    assert d._best_replay(c, clock.t, None, False) is None       # ...else back to live
    del c["live"]
    assert d._best_replay(c, clock.t, None, False) is not None   # nothing live worth it: keep catching up
    d._commit("a")
    assert d.reel_n == 0


def test_reel_skips_a_game_now_in_crunch_time(cfg, clock):
    d = _dec(cfg, clock)
    m = Missed("a", "1", "TD a", "td", clock.t - 60, 75)
    c = _queue(d, m)
    c["a"] = Candidate(_sc("a", 80, crunch=True), "youtubetv:a")
    assert d._best_replay(c, clock.t, None, False) is None


def test_standby_cues_your_teams_clip_first(clock):
    from redzone.standby import likely_replay
    a = Missed("a", "1", "TD a", "td", clock.t - 60, 90)
    w = Missed("w", "3", "FG w", "fg", clock.t - 40, 40, mine=True)
    assert likely_replay([a, w], clock.t, 1800) is w
