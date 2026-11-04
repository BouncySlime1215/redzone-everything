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


@pytest.fixture(autouse=True)
def _flip_steady_off(monkeypatch):
    """[SYNC 1] The ~1 s steady-PLAYING wait before a flip would slow every switch test by a second
    and shift their read counts; tests/test_sync_flip.py turns it back on and tests it directly."""
    from redzone import control
    monkeypatch.setattr(control, "FLIP_STEADY_S", 0.0)


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


def assert_speed(per_s: float, budget_s: float, what: str = "per call") -> None:
    """A wall-clock speed budget. Skipped (only this assert) when the suite runs at background
    priority, as `scripts/deploy.py` does on the Mac (taskpolicy -b, efficiency cores): there a
    budget measures the scheduler, not the code. CI and normal runs still enforce it."""
    import os
    if os.environ.get("RZ_TESTS_LOW_PRIORITY") == "1":
        return
    assert per_s < budget_s, f"{what}: {per_s * 1000:.2f} ms, budget {budget_s * 1000:.1f} ms"
