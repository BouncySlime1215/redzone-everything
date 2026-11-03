import platform

import pytest

from redzone.config import Config
from redzone.models import GameState, League, Phase, Situation, Stoppage, Team


ON_MAC = platform.system() == "Darwin"


def pytest_collection_modifyitems(config, items):
    """`@pytest.mark.mac_only` tests need macOS itself (Swift helpers, Vision, sips). They run on the
    Mac and are skipped, by name, everywhere else, so CI on Linux reports them instead of failing."""
    if ON_MAC:
        return
    skip = pytest.mark.skip(reason="mac_only: needs macOS (runs on the Mac, skipped on Linux CI)")
    for item in items:
        if "mac_only" in item.keywords:
            item.add_marker(skip)


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
