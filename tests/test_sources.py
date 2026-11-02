"""Parsers must survive real responses AND responses with fields ripped out."""
import copy
import glob
import json
import random
from pathlib import Path

import pytest

from redzone import bets
from redzone.channels import ChannelMap, parse_yttv_link
from redzone.models import GameState, League, Phase, Stoppage, Team
from redzone.sources import espn, leagues

FIX = Path(__file__).parent / "fixtures"


def _league(path: str) -> League:
    return League(Path(path).name.split("_")[1])


@pytest.mark.parametrize("path", sorted(glob.glob(str(FIX / "espn_*_scoreboard_*.json"))))
def test_scoreboards_parse(path):
    games = espn.parse_scoreboard(_league(path), json.loads(Path(path).read_text()))
    assert games, "expected games"
    for g in games:
        assert g.home.abbr and g.away.abbr and g.game_id
        assert g.home.logo.startswith("https://")


@pytest.mark.parametrize("path", sorted(glob.glob(str(FIX / "espn_*_summary_*.json"))))
def test_summary_replays_every_play(path):
    lg = _league(path)
    data = json.loads(Path(path).read_text())
    plays = espn.flatten_plays(lg, data)
    assert plays
    last_score = (0, 0)
    for i in range(len(plays)):
        g = espn.parse_summary(lg, data, upto=i, plays=plays)
        assert g is not None
        if g.home.score is not None:
            last_score = (g.home.score, g.away.score)
    assert sum(last_score) > 0
    final = espn.parse_summary(lg, data)
    assert final.phase == Phase.POST


def _mutilate(o, rng, p=0.15):
    if isinstance(o, dict):
        for k in list(o):
            if rng.random() < p:
                del o[k]
            else:
                _mutilate(o[k], rng, p)
    elif isinstance(o, list):
        for x in o:
            _mutilate(x, rng, p)


@pytest.mark.parametrize("seed", range(6))
def test_parsers_survive_missing_fields(seed):
    rng = random.Random(seed)
    for path in sorted(glob.glob(str(FIX / "espn_*_scoreboard_*.json")))[:4]:
        d = json.loads(Path(path).read_text())
        _mutilate(d, rng)
        espn.parse_scoreboard(_league(path), d)
    for path in sorted(glob.glob(str(FIX / "espn_*_summary_*.json"))):
        d = json.loads(Path(path).read_text())
        _mutilate(d, rng, 0.05)
        espn.parse_summary(_league(path), d)


def test_nfl_stoppages_classified():
    d = json.loads((FIX / "espn_nfl_summary_401772931.json").read_text())
    plays = espn.flatten_plays(League.NFL, d)
    kinds = {espn.stoppage_for(League.NFL, p) for p in plays}
    assert {Stoppage.TIMEOUT, Stoppage.SCORE_BREAK, Stoppage.TWO_MINUTE_WARNING, Stoppage.HALFTIME} <= kinds


def test_nhl_situation_codes():
    assert leagues.parse_situation_code("1541", "H", "A") == ("A", "5v4", None)
    assert leagues.parse_situation_code("1451", "H", "A") == ("H", "5v4", None)
    assert leagues.parse_situation_code("0651", "H", "A")[2] == "A"
    assert leagues.parse_situation_code("1531", "H", "A")[:2] == ("A", "5v3")
    assert leagues.parse_situation_code(None, "H", "A") == (None, None, None)
    assert leagues.parse_situation_code("garbage", "H", "A") == (None, None, None)


def test_yttv_link_parsing():
    assert parse_yttv_link("https://tv.youtube.com/watch/D1vdtklRyHg?vp=0gEEEgIwAQ%3D%3D") == "D1vdtklRyHg"
    assert parse_yttv_link("D1vdtklRyHg") == "D1vdtklRyHg"
    assert parse_yttv_link("not a link") is None


def _g(league, broadcasts, home="SEA", away="CHI", start=None):
    g = GameState(league=league, game_id="x", home=Team(home), away=Team(away))
    g.broadcasts = broadcasts
    g.start_time = start
    return g


def test_channel_mapping(tmp_path):
    cm = ChannelMap(tmp_path / "ch.json")
    assert cm.where(_g(League.NFL, ["ESPN"])).target == "youtubetv:espn"
    assert cm.where(_g(League.NFL, ["ESPN"])).tunable is False      # no link pasted yet
    cm.set_code_from_link("espn", "https://tv.youtube.com/watch/AAAAAAAAAAA")
    assert cm.where(_g(League.NFL, ["ESPN"])).tunable is True
    w = cm.where(_g(League.NBA, ["MNMT", "NBA League Pass"], home="ORL", away="WSH"))
    assert w.target.startswith("prime:") and "Monumental" in w.label
    assert cm.where(_g(League.NHL, ["ESPN+", "DSN"])).target == "youtubetv:espnplus"
    cm.set_override("x", "youtubetv:fox")
    assert cm.where(_g(League.NFL, ["ESPN"])).confidence == "you set it"
    assert ChannelMap(tmp_path / "ch.json").overrides["x"] == "youtubetv:fox"  # remembered


def test_bet_sweat():
    g = GameState(league=League.NFL, game_id="b", home=Team("NE", score=20), away=Team("BUF", score=17),
                  phase=Phase.LIVE, period=4, clock_s=60)
    close = bets.sweat(g, {"kind": "spread", "team": "BUF", "line": 3.5})   # covering by 0.5
    blown = bets.sweat(g, {"kind": "spread", "team": "BUF", "line": -24})
    assert close > 0.6 and blown == 0
    assert "covering" in bets.label(g, {"kind": "spread", "team": "BUF", "line": 3.5})
    over_cashed = bets.sweat(g, {"kind": "total", "line": 30, "side": "over"})
    assert over_cashed == 0
