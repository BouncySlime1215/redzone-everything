"""College leagues: UNC as "my team" in men's/women's basketball and football.

Fixtures are real ESPN responses (Nov 2026): the ACC boards for 6-7 Nov 2026 and three UNC games
(@ Duke, MBB 7 Mar 2026; vs Maryland, WBB 22 Mar 2026; vs Miami, football 31 Oct 2026).
"""
import json
from pathlib import Path

from redzone.models import PERIOD_S, REGULATION, ByLeague, GameState, League, Phase, Stoppage, Team
from redzone.sources import espn

FIX = Path(__file__).parent / "fixtures"


def _load(name):
    return json.loads((FIX / name).read_text())


def _replay(lg, name):
    data = _load(name)
    plays = espn.flatten_plays(lg, data)
    return [espn.parse_summary(lg, data, upto=i, plays=plays) for i in range(len(plays))]


# --- leagues ---------------------------------------------------------------------------------

def test_college_leagues_play_by_pro_rules():
    assert League.CBB.base is League.NBA and League.WCBB.base is League.NBA
    assert League.CFB.base is League.NFL and League.NBA.base is League.NBA
    assert League.CBB.college and not League.NBA.college
    assert (REGULATION[League.CBB], PERIOD_S[League.CBB]) == (2, 1200)       # two 20-minute halves
    assert (REGULATION[League.WCBB], PERIOD_S[League.WCBB]) == (4, 600)      # four 10-minute quarters
    assert (REGULATION[League.CFB], PERIOD_S[League.CFB]) == (4, 900)


def test_by_league_table_falls_back_to_pro_row():
    t = ByLeague({League.NBA: 1, League.CBB: 2})
    assert t[League.CBB] == 2 and t[League.WCBB] == 1 and t.get(League.CFB, "x") == "x"
    assert League.WCBB in t and League.CFB not in t


# --- scoreboard: only your teams + ranked games -----------------------------------------------

def test_unc_parses_with_tar_heels_logo_and_rank():
    games = espn.parse_scoreboard(League.CBB, _load("espn_cbb_scoreboard_20261106_acc.json"))
    unc = next(g for g in games if g.involves({"UNC"}))
    t = unc.home if unc.home.abbr == "UNC" else unc.away
    assert t.id == "153" and t.logo == "https://a.espncdn.com/i/teamlogos/ncaa/500/153.png"
    assert "ACC Network" in unc.broadcasts
    uva = next(g for g in games if "UVA" in (g.home.abbr, g.away.abbr))
    assert uva.home.rank == 9 and uva.away.rank is None   # 99 = unranked


def test_college_board_keeps_only_mine_and_ranked():
    games = espn.parse_scoreboard(League.CBB, _load("espn_cbb_scoreboard_20261106_acc.json"))
    kept = {g.matchup for g in games if espn.keep_college(g, {"UNC"})}
    assert kept == {"WOF @ UNC", "NE @ UVA", "WAG @ MIA"}        # 9 ACC games -> 3
    assert len([g for g in games if espn.keep_college(g, set())]) == 2


def test_college_scoreboard_urls_add_conference_groups():
    assert espn.scoreboard_urls(League.NBA, ["2"]) == [espn.scoreboard_url(League.NBA)]
    urls = espn.scoreboard_urls(League.CBB, ["2"])
    assert urls[0].endswith("/basketball/mens-college-basketball/scoreboard")
    assert urls[1].endswith("/basketball/mens-college-basketball/scoreboard?groups=2")
    assert espn.scoreboard_urls(League.CFB, ["1"], "20261107")[1].endswith("scoreboard?dates=20261107&groups=1")


def test_college_logo_fallback_uses_team_id():
    assert espn.logo_url(League.CFB, "UNC", "153").endswith("/teamlogos/ncaa/500/153.png")
    assert espn.logo_url(League.NBA, "WSH") == "https://a.espncdn.com/i/teamlogos/nba/500/wsh.png"


# --- play-by-play: dead balls and breaks in ESPN college text --------------------------------

def test_mens_college_halftime_is_end_of_first_half():
    states = _replay(League.CBB, "espn_cbb_summary_401820788.json")
    half = [g for g in states if g.stoppage == Stoppage.HALFTIME]
    assert len(half) == 1 and half[0].period == 1
    assert sum(g.stoppage == Stoppage.TIMEOUT for g in states) >= 9   # media timeouts (OfficialTVTimeOut)
    assert states[-1].phase == Phase.POST and states[-1].period_type is None


def test_mens_college_free_throws_and_dead_ball_rebounds():
    ft = lambda text: {"type": {"text": "MadeFreeThrow"}, "text": text, "period": {"number": 2}}  # noqa: E731
    assert espn.stoppage_for(League.CBB, ft("Maliq Brown makes free throw 1 of 2")) == Stoppage.WHISTLE
    assert espn.stoppage_for(League.CBB, ft("Maliq Brown makes free throw 2 of 2")) == Stoppage.NONE
    dead = {"type": {"text": "Dead Ball Rebound"}, "text": "Duke Deadball Team Rebound."}
    assert espn.stoppage_for(League.CBB, dead) == Stoppage.WHISTLE
    assert espn._kind(League.CBB, dead) == "team_rebound"
    assert espn.stoppage_for(League.CBB, {"type": {"text": "ShortTimeOut"}, "text": "Duke Timeout"}) == Stoppage.TIMEOUT
    assert espn.stoppage_for(League.CBB, {"type": {"text": "PersonalFoul"}, "text": "Foul on X."}) == Stoppage.WHISTLE


def test_mens_college_overtime_is_period_three():
    data = _load("espn_cbb_summary_401820788.json")
    plays = espn.flatten_plays(League.CBB, data)
    p = dict(plays[-5], period={"number": 3})
    g = espn.parse_summary(League.CBB, data, plays=plays[:-5] + [p], upto=len(plays) - 5)
    assert g.period == 3 and g.period_type == "OT"


def test_womens_college_halftime_is_end_of_second_quarter():
    states = _replay(League.WCBB, "espn_wcbb_summary_401856543.json")
    half = [g for g in states if g.stoppage == Stoppage.HALFTIME]
    assert len(half) == 1 and half[0].period == 2


def test_college_football_halftime_and_scores():
    states = _replay(League.CFB, "espn_cfb_summary_401858282.json")
    half = [g for g in states if g.stoppage == Stoppage.HALFTIME]
    assert len(half) == 1 and half[0].period == 2                       # "End of 2nd quarter."
    assert any(g.stoppage == Stoppage.SCORE_BREAK for g in states)
    assert any(g.situation.possession for g in states)                    # NFL drive parsing applies
    last = states[-1]
    assert (last.home.abbr, last.home.score, last.away.score) == ("UNC", 35, 38)
