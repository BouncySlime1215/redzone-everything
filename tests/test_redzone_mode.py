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
