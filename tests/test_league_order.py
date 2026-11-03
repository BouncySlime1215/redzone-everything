"""Your league order (NFL > CFB > NBA > NCAA > NHL > MLB) and the config loader that keeps one bad
setting from wiping the rest."""
import dataclasses

import yaml
from conftest import nfl

from redzone.config import Config
from redzone.models import League
from redzone.scoring.engine import Personal, Scorer


def _as(g, league):
    return dataclasses.replace(g, league=league)


def test_one_sport_night_is_scored_as_without_an_order(cfg, clock):
    s = Scorer(cfg, clock=clock)
    g = nfl("a", "KC", "BUF", 14, 14, q=4, clock=500, pos="KC", down=1, dist=10, ytg=30, wp=0.5)
    alone = s.score(g, Personal())
    assert alone.factors["league"] == 1.0


def test_equal_moment_goes_to_the_league_you_like_more(cfg, clock):
    s = Scorer(cfg, clock=clock)
    g = nfl("a", "KC", "BUF", 14, 14, q=4, clock=500, pos="KC", down=1, dist=10, ytg=30, wp=0.5)
    football = s.score(g, Personal())
    hockey = s.score(_as(dataclasses.replace(g, game_id="b"), League.NHL), Personal())
    # Both leagues on the same night: NFL is the top league on, so it's 1.0.
    assert s.score(g, Personal()).factors["league"] == 1.0
    assert hockey.factors["league"] < 1.0
    assert football.raw / football.moment > hockey.raw / hockey.moment   # q4 is OT in hockey: per unit of moment


def test_a_league_off_tonight_stops_counting(cfg, clock):
    s = Scorer(cfg, clock=clock)
    g = nfl("a", "KC", "BUF", 14, 14, q=4, clock=500, pos="KC", down=1, dist=10, ytg=30, wp=0.5)
    s.score(g, Personal())
    clock.t += Scorer.LEAGUE_LIVE_S + 1
    hockey = s.score(_as(dataclasses.replace(g, game_id="b"), League.NHL), Personal())
    assert hockey.factors["league"] == 1.0


def test_your_teams_follow_your_league_order(cfg, clock):
    s = Scorer(cfg, clock=clock)
    me = Personal(my_teams={League.NFL: {"WSH"}, League.NHL: {"WSH"}})
    g = nfl("a", "WSH", "NYG", 14, 14, q=4, clock=500, wp=0.5)
    commanders = s.score(g, me).personal
    capitals = s.score(_as(dataclasses.replace(g, game_id="b"), League.NHL), me).personal
    assert commanders > capitals > 0


def test_one_bad_setting_costs_only_itself(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump({"decider": {"switch_margin": 12, "made_up_key": 3},
                                 "score": {"league_weight": {"nfl": 9.0, "nba": 1.1}}}))
    c = Config(user_path=p, history_dir=tmp_path / "h")
    assert c.get("decider.switch_margin") == 12
    assert c.get("score.league_weight.nba") == 1.1
    assert c.get("score.league_weight.nfl") == 1.15        # out of range: default kept
    assert len(c.rejected) == 2


def test_unreadable_file_falls_back_to_defaults_and_says_so(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("decider: [unclosed")
    c = Config(user_path=p, history_dir=tmp_path / "h")
    assert c.overrides == {} and c.rejected
    assert c.get("decider.switch_margin") == 10
