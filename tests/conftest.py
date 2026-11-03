import pytest

from redzone.config import Config
from redzone.models import GameState, League, Phase, Situation, Stoppage, Team


@pytest.fixture(autouse=True)
def _tv_files_in_tmp(tmp_path, monkeypatch):
    """No test ever reads or writes the real TV token, SmartThings sign-in or input log."""
    from redzone import tv
    monkeypatch.setattr(tv, "TOKEN_FILE", tmp_path / "tv_token.txt")
    monkeypatch.setattr(tv, "OAUTH_FILE", tmp_path / "smartthings_oauth.json")
    monkeypatch.setattr(tv, "INPUT_LOG", tmp_path / "tv_input.jsonl")


@pytest.fixture
def cfg(tmp_path):
    return Config(user_path=tmp_path / "config.yaml", history_dir=tmp_path / "hist")


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return FakeClock()


def nfl(gid="1", home="WSH", away="DAL", hs=0, as_=0, q=1, clock=900.0, pos=None, down=None,
        dist=None, ytg=None, gtg=False, wp=None, stoppage=Stoppage.NONE, **kw):
    return GameState(
        league=League.NFL, game_id=gid, home=Team(home, score=hs), away=Team(away, score=as_),
        phase=Phase.LIVE, period=q, clock_s=clock, stoppage=stoppage, home_win_prob=wp,
        situation=Situation(possession=pos, down=down, distance=dist, yards_to_goal=ytg, goal_to_go=gtg),
        **kw,
    )
