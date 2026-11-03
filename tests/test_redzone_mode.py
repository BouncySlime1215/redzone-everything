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
    assert out[REDZONE].scored.score == cfg.get("decider.redzone_score") + 0.5 * cfg.get("redzone_mode.redzone_lift")


def test_a_league_you_rank_above_the_nfl_is_not_held_under_a_drive(cfg, clock):
    d = _dec(cfg, clock)
    nba = replace(_sc("nba", 70, League.NBA), factors={"league": 1.0})
    nhl = replace(_sc("nhl", 70, League.NHL), factors={"league": 0.8})
    hot = replace(_sc("nfl", 55, heat=0.8), factors={"drive_heat": 0.8, "league": 0.9})
    out = d._nfl_priority({"nfl": Candidate(hot, "youtubetv:nfl"), "nba": Candidate(nba, "youtubetv:nba"),
                           "nhl": Candidate(nhl, "youtubetv:nhl")})
    assert out["nba"].scored.score == 70                     # NBA weighted above the NFL: untouched
    assert out["nhl"].scored.score == 55 - cfg.get("redzone_mode.priority_gap")


def test_a_drive_on_a_break_does_not_hold_other_sports_down(cfg, clock):
    d = _dec(cfg, clock)
    c = {"nfl": Candidate(_sc("nfl", 55, heat=0.9, is_break=True), "youtubetv:nfl"),
         "nba": Candidate(_sc("nba", 70, League.NBA), "youtubetv:nba")}
    assert d._nfl_priority(c)["nba"].scored.score == 70


def test_on_redzone_a_live_drive_takes_over(cfg, clock):
    d = _dec(cfg, clock)
    d.current, d.since = REDZONE, clock.t - 300
    c = {"a": Candidate(_sc("a", 40, heat=0.7), "youtubetv:a"),
         "b": Candidate(_sc("b", 35, heat=0.9), "youtubetv:b"),
         "cold": Candidate(_sc("cold", 60, heat=0.1), "youtubetv:cold")}
    dec = d._drive_takeover(c, True, None, "", clock.t)
    assert dec and dec.action == "switch" and dec.target == "b"      # the hottest drive, not the best score
    c["b"] = Candidate(_sc("b", 35, heat=0.9, is_break=True), "youtubetv:b")
    assert d._drive_takeover(c, True, None, "", clock.t).target == "a"   # a drive in a break waits
    d.since = clock.t                                                # ...and a drive we just left RedZone for
    assert d._drive_takeover(c, True, None, "", clock.t) is None      # doesn't bounce us back on the next poll
    d.since = clock.t - 300
    assert d._drive_takeover(c, False, None, "", clock.t) is None     # never mid-play on our screen
    d.current = "cold"
    assert d._drive_takeover(c, True, None, "", clock.t) is None     # only RedZone is left for a drive
    d.current = REDZONE
    cfg.data["redzone_mode"]["drive_over_redzone"] = False
    assert d._drive_takeover(c, True, None, "", clock.t) is None


def test_redzone_never_wins_back_the_drive_we_are_watching(cfg, clock):
    d = _dec(cfg, clock)
    d.current = "nfl"
    c = {"nfl": Candidate(_sc("nfl", 30, heat=0.9), "youtubetv:nfl")}
    assert d._with_redzone(c, "youtubetv:redzone")[REDZONE].scored.score == 30
    # Its drive is in a break: the whip-around is the better picture again.
    c["nfl"] = Candidate(_sc("nfl", 30, heat=0.9, is_break=True), "youtubetv:nfl")
    assert d._with_redzone(c, "youtubetv:redzone")[REDZONE].scored.score > 30


def test_your_teams_missed_play_goes_first_and_clears_the_bar(cfg, clock):
    d = _dec(cfg, clock)
    d._yours.add("wsh")
    td = Missed("other", "p1", "TD", "td", clock.t - 60, 80)
    yours = Missed("wsh", "p2", "fumble", "turnover", clock.t - 60, 55, mine=True)
    d.queue += [td, yours]
    c = {"other": Candidate(_sc("other", 30), "youtubetv:other"), "wsh": Candidate(_sc("wsh", 30), "youtubetv:wsh")}
    assert d._best_replay(c, clock.t, None, False) is yours
    # Against live play scored 70 only the TD would clear the bar, but your team's play still goes.
    assert d._best_replay(c, clock.t, None, False, top=70, margin=5) is yours
    # ...except while your team's own game is on screen: its live snaps beat its old ones.
    assert d._best_replay(c, clock.t, None, False, top=70, margin=5, yours=False) is td
    cfg.data["replay"]["yours_first"] = False
    assert d._best_replay(c, clock.t, None, False) is td
