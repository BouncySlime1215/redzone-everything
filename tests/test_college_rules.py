"""College scoring, decider and channel rules: UNC treated like WSH."""
import json
from pathlib import Path

from redzone import standby
from redzone.decider.screen import CONTINUOUS, SILENT_LIVE_S, Timeline, _is_marker, resume_lead
from redzone.models import GameState, League, Phase, Team, pro_period
from redzone.scoring import garbage
from redzone.scoring.engine import Personal, Scorer, crunch_time, game_progress, is_late
from redzone.sources import espn

FIX = Path(__file__).parent / "fixtures"
UNC = Personal(my_teams={League.CBB: {"UNC"}, League.CFB: {"UNC"}, League.WCBB: {"UNC"}})


def cbb(hs, as_, half, clock, lg=League.CBB, gid="c"):
    return GameState(league=lg, game_id=gid, home=Team("UNC", score=hs), away=Team("DUKE", score=as_),
                     phase=Phase.LIVE, period=half, clock_s=clock)


# --- scoring ---------------------------------------------------------------------------------

def test_mens_college_halves_read_as_nba_quarters():
    assert [pro_period(League.CBB, p, c) for p, c in ((1, 1100), (1, 300), (2, 900), (2, 200), (3, 100))] \
        == [1, 2, 3, 4, 5]
    assert pro_period(League.WCBB, 3, 100) == 3 and pro_period(League.NBA, 4, 100) == 4
    assert not is_late(cbb(60, 58, 2, 900)) and is_late(cbb(60, 58, 2, 500))
    assert game_progress(cbb(30, 30, 1, 600)) == 0.25 and game_progress(cbb(60, 60, 2, 0)) == 1.0


def test_mens_college_crunch_and_breaks():
    assert crunch_time(cbb(70, 66, 2, 150))           # final 3:00 within 7
    assert not crunch_time(cbb(70, 66, 1, 150))       # end of the 1st half isn't crunch time
    assert crunch_time(cbb(80, 70, 3, 200))           # OT
    assert standby.crunch_soon(cbb(70, 66, 2, 300)) and not standby.crunch_soon(cbb(70, 66, 1, 300))
    assert standby.break_soon(cbb(30, 30, 1, 45)) and not standby.break_soon(cbb(60, 60, 2, 45))


def test_mens_college_garbage_time_uses_time_left_in_game():
    assert garbage.threshold(cbb(40, 20, 1, 300)) is None    # first half: never garbage
    assert garbage.threshold(cbb(60, 30, 2, 900)) == 30       # early 2nd half = NBA Q3
    assert garbage.threshold(cbb(60, 40, 2, 100)) == 10       # final 2:00 = NBA Q4 2:00
    assert garbage.threshold(cbb(80, 50, 3, 100)) is None     # OT never


def test_unc_gets_my_team_boost_and_crunch_like_wsh(cfg, clock):
    s = Scorer(cfg, clock=clock).score(cbb(70, 68, 2, 90), UNC)
    other = Scorer(cfg, clock=clock).score(cbb(70, 68, 2, 90, gid="d"), Personal())
    assert "UNC" in s.tags and s.personal > 0 and s.score > other.score and s.crunch
    blowout = Scorer(cfg, clock=clock).score(cbb(90, 50, 2, 200), UNC)
    assert blowout.garbage and blowout.personal == 0                  # no boost once decided


def test_womens_college_and_football_score_by_pro_rules(cfg, clock):
    w = Scorer(cfg, clock=clock).score(cbb(60, 58, 4, 60, lg=League.WCBB), UNC)
    assert w.crunch and "UNC" in w.tags
    f = GameState(league=League.CFB, game_id="f", home=Team("UNC", score=24), away=Team("MIA", score=21),
                  phase=Phase.LIVE, period=4, clock_s=100)
    s = Scorer(cfg, clock=clock).score(f, UNC)
    assert s.crunch and "UNC" in s.tags
    f.period, f.clock_s = 5, None                                      # untimed college OT
    assert crunch_time(f)


# --- decider ---------------------------------------------------------------------------------

def test_college_basketball_dead_balls_and_shot_clock():
    assert League.CBB in CONTINUOUS and League.WCBB in CONTINUOUS
    assert SILENT_LIVE_S[League.CBB] == 30.0 and resume_lead(League.CBB) > resume_lead(League.NBA)
    ft1 = espn._kind(League.CBB, {"type": {"text": "MadeFreeThrow"}, "text": "X makes free throw 1 of 2"})
    ft2 = espn._kind(League.CBB, {"type": {"text": "MadeFreeThrow"}, "text": "X makes free throw 2 of 2"})
    assert _is_marker(ft1, League.CBB) and not _is_marker(ft2, League.CBB)
    assert _is_marker("personalfoul", League.CBB) and _is_marker("team_rebound", League.WCBB)


def test_college_replays_feed_the_timeline():
    for lg, name in ((League.CBB, "espn_cbb_summary_401820788.json"), (League.CFB, "espn_cfb_summary_401858282.json")):
        data = json.loads((FIX / name).read_text())
        plays = espn.flatten_plays(lg, data)
        tl = Timeline(lg, clock=lambda: 0.0)
        for i in range(min(150, len(plays))):
            tl.observe(espn.parse_summary(lg, data, upto=i, plays=plays))
        assert tl.events
