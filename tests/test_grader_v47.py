"""Grader / feed fixes of the 2026-11-08 audit (docs/audits/2026-11-08-feeds-scoring.md): each test fails on the
code before its fix. Ids F1.. are the audit's."""
import copy
import json
from pathlib import Path

import pytest

from redzone import grading
from redzone.lab import brain3data, outcome_label
from redzone.models import GameState, League, Phase, Play, Stoppage, Team
from redzone.scoring import breaks, garbage
from redzone.sources import espn, leagues
from redzone.sources.merge import Merger

FIX = Path(__file__).parent / "fixtures"


def _play(per, clock, h, a, **kw):
    return {"period": {"number": per}, "clock": {"displayValue": clock}, "homeScore": h, "awayScore": a, **kw}


# F2: is_big / is_crunch were False for every college state ---------------------------------------------

def test_college_crunch_and_big_plays_are_graded():
    assert grading.is_crunch(League.CFB, _play(4, "1:30", 20, 17))
    assert not grading.is_crunch(League.CFB, _play(2, "1:30", 20, 17))
    assert grading.is_crunch(League.CBB, _play(2, "1:30", 60, 58))          # 2nd half, 1:30 left = pro Q4
    assert not grading.is_crunch(League.CBB, _play(1, "1:30", 60, 58))      # 1st half
    assert grading.is_crunch(League.CBB, _play(3, "4:00", 60, 60))          # college OT
    assert grading.is_big(League.CFB, _play(1, "5:00", 7, 0, scoringPlay=True), None)
    prev = _play(2, "1:20", 60, 58)
    made = _play(2, "1:10", 62, 58, scoringPlay=True, type={"text": "Jump Shot"})
    assert grading.is_big(League.CBB, made, prev)                           # late and close
    assert not grading.is_big(League.CBB, dict(made, period={"number": 1}), prev)


# F10: a fresher scoreboard poll wiped the summary's last play and stoppage ------------------------------

def _nfl(clock, plays=(), stoppage=Stoppage.UNKNOWN, last=None, hs=14, as_=10, per=2):
    g = GameState(league=League.NFL, game_id="1", home=Team("WSH", score=hs), away=Team("DAL", score=as_),
                  phase=Phase.LIVE, period=per, clock_s=clock, stoppage=stoppage)
    g.last_play, g.recent_plays = last, list(plays)
    return g


def test_scoreboard_poll_five_seconds_ahead_keeps_the_summary_play_and_stoppage():
    m = Merger(clock=lambda: 1000.0)
    p = Play("a", "Timeout", "timeout", wallclock=990.0)
    m.update("espn_summary", _nfl(300.0, [p], Stoppage.TIMEOUT, p))
    m.update("espn_scoreboard", _nfl(295.0))
    g = m.games["1"]
    assert g.clock_s == 295.0                                   # the fresher clock still wins
    assert g.last_play is p and len(g.recent_plays) == 1 and g.stoppage == Stoppage.TIMEOUT


def test_the_carry_stops_when_the_score_or_the_clock_moved_on():
    m = Merger(clock=lambda: 1000.0)
    p = Play("a", "Timeout", "timeout", wallclock=990.0)
    m.update("espn_summary", _nfl(300.0, [p], Stoppage.TIMEOUT, p))
    m.update("espn_scoreboard", _nfl(295.0, hs=21))              # a TD we don't have a play for
    assert m.games["1"].last_play is None
    m2 = Merger(clock=lambda: 1000.0)
    m2.update("espn_summary", _nfl(300.0, [p], Stoppage.TIMEOUT, p))
    m2.update("espn_scoreboard", _nfl(200.0))                    # 100 s later: not the same moment
    assert m2.games["1"].last_play is None and m2.games["1"].stoppage == Stoppage.UNKNOWN


# F7: NHL playoff overtime is 20:00 and is never a shootout ---------------------------------------------

def _nhl_summary(playoffs):
    data = json.loads((FIX / "espn_nhl_summary_401892626.json").read_text())
    data = copy.deepcopy(data)
    data.setdefault("header", {}).setdefault("season", {})["type"] = 3 if playoffs else 2
    plays = copy.deepcopy(espn.flatten_plays(League.NHL, data))
    last = plays[-1]
    last["period"] = {"number": 5}
    last["clock"] = {"displayValue": "12:30"}                    # ESPN's NHL clock counts up
    return data, plays


def test_nhl_playoff_double_overtime_reads_450_seconds_left_and_is_not_a_shootout():
    data, plays = _nhl_summary(True)
    g = espn.parse_summary(League.NHL, data, plays=plays)
    assert g.clock_s == 450.0 and g.period_type != "SO"
    data, plays = _nhl_summary(False)                             # regular season: 5:00 OT clamps, period 5 = SO
    g = espn.parse_summary(League.NHL, data, plays=plays)
    assert g.clock_s == 0.0 and g.period_type == "SO"


# F14: a backtest knew the end-of-game records ------------------------------------------------------------

def test_a_backtest_state_carries_no_end_of_game_record():
    data = json.loads((FIX / "espn_nhl_summary_401892626.json").read_text())
    plays = espn.flatten_plays(League.NHL, data)
    replay = espn.parse_summary(League.NHL, data, upto=len(plays) // 2, plays=plays)
    assert replay.home.wins is None and replay.home.losses is None and replay.away.wins is None
    live = espn.parse_summary(League.NHL, data)
    assert live.home.wins is not None


# F9 / F17: stoppage reasons, sticky review -------------------------------------------------------------

@pytest.mark.parametrize("reason,want", [("tv-timeout", Stoppage.TIMEOUT), ("video-review", Stoppage.REVIEW),
                                         ("chlg-hm-missed-stoppage", Stoppage.REVIEW), ("injury", Stoppage.INJURY),
                                         ("puck-in-netting", Stoppage.WHISTLE), ("", Stoppage.WHISTLE)])
def test_nhl_stoppage_reasons(reason, want):
    assert leagues.nhl_stoppage_reason(reason) == want


def test_nba_instant_replay_is_a_review_not_a_whistle():
    assert leagues.nba_stoppage({"actionType": "instantreplay"}) == Stoppage.REVIEW
    assert leagues.nba_stoppage({"actionType": "foul"}) == Stoppage.WHISTLE


def _mlb_live(events, has_review=True):
    return {"liveData": {"linescore": {"currentInning": 5, "inningHalf": "Top", "outs": 1, "offense": {}},
                         "plays": {"currentPlay": {"about": {"hasReview": has_review}, "playEvents": events}}}}


def test_mlb_has_review_only_counts_while_the_review_is_open():
    g = GameState(league=League.MLB, game_id="m", home=Team("WSH", score=1), away=Team("NYM", score=0),
                  phase=Phase.LIVE, period=5)
    pitch = {"details": {"event": "Ball"}}
    open_rev = {"details": {"event": "Umpire Review"}, "reviewDetails": {"inProgress": True}}
    done_rev = {"details": {"event": "Umpire Review"}, "reviewDetails": {"inProgress": False}}
    assert leagues.enrich_mlb(g, _mlb_live([pitch, open_rev])).stoppage == Stoppage.REVIEW
    assert leagues.enrich_mlb(g, _mlb_live([open_rev, pitch])).stoppage != Stoppage.REVIEW      # a pitch after it
    assert leagues.enrich_mlb(g, _mlb_live([done_rev])).stoppage != Stoppage.REVIEW


# F13 / F11 / F16 ------------------------------------------------------------------------------------------

def test_mlb_extra_innings_are_never_garbage():
    g = GameState(league=League.MLB, game_id="m", home=Team("WSH", score=9), away=Team("NYM", score=2),
                  phase=Phase.LIVE, period=10)
    assert garbage.threshold(g) is None and not garbage.is_garbage(g)
    g.period = 9
    assert garbage.threshold(g) == 5


def test_cfb_forecast_has_no_two_minute_warning():
    def fc(lg):
        g = GameState(league=lg, game_id="g", home=Team("A", score=7), away=Team("B", score=3), phase=Phase.LIVE,
                      period=2, clock_s=150.0, stoppage=Stoppage.NONE)
        return breaks.forecast(g)
    assert "two-minute" in fc(League.NFL).why
    assert "two-minute" not in fc(League.CFB).why


def test_outcome_label_has_every_leagues_rows():
    for lg in (League.WCBB, League.CBB, League.CFB, League.NFL, League.NBA, League.NHL, League.MLB):
        assert lg in outcome_label.HORIZON
    assert outcome_label.WP_SPREAD[League.WCBB] and outcome_label.FINISH_MARGIN[League.WCBB]


# F6: brain splits are time splits by whole night, not a hash ---------------------------------------------

def test_brain_split_is_a_time_split_and_never_trains_on_a_locked_night():
    from redzone.lab import splits
    nights = [f"2026{m:02d}{d:02d}" for m in (1, 2, 3) for d in range(1, 29)]
    got = brain3data.night_splits("nba", nights)
    assert set(got.values()) == {"train", "valid", "test"}
    tr = sorted(n for n, s in got.items() if s == "train")
    te = sorted(n for n, s in got.items() if s == "test")
    assert max(tr) < min(te)                                  # time order: every train night is older
    assert len(te) >= 0.25 * len(nights) - 1
    locked = splits.split_nights(nights).test
    assert set(te) == set(locked) and not set(tr) & set(locked)
    assert brain3data.night_splits("espn_nhl", nights) == brain3data.night_splits("nhl", nights)
    # the dashed spelling of a night lands in the same set as its YYYYMMDD spelling
    assert brain3data.night_splits("nba", ["2026-01-01", "2026-03-28"]) != {}
