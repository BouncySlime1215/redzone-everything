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


@pytest.fixture(autouse=True)
def _repo_data_in_tmp(tmp_path, monkeypatch):
    """A bare `Store()`, `Config()` or `Engine()` writes nothing into the repo's data/: the database,
    config history and data/sync.jsonl (beside the config) go to tmp. ~19 tests did, which broke
    test_e2e_night's "data untouched" check once deploy.py ran the suite on parallel workers (Nov 7),
    and on a dev checkout they mixed test rows into the real redzone.db."""
    from redzone import config as rz_config
    from redzone import store as rz_store
    d = tmp_path / "_repo_data"
    monkeypatch.setattr(rz_store.Store.__init__, "__defaults__", (d / "redzone.db",))
    monkeypatch.setattr(rz_config.Config.__init__, "__defaults__", (d / "config.yaml", d / "config_history"))


def _is_real_adb(prog) -> bool:
    """The real adb binary (Homebrew, PATH), not a tests/fake_adb.py script under a tmp dir."""
    import os
    import shutil
    import tempfile
    p = os.path.realpath(os.fspath(prog))
    if os.path.basename(p) != "adb":
        return False
    tmp = os.path.realpath(tempfile.gettempdir())
    if p.startswith(tmp + os.sep) or "/pytest-of-" in p:
        return False
    real = {os.path.realpath(x) for x in (shutil.which("adb"), "/opt/homebrew/bin/adb", "/usr/local/bin/adb") if x}
    return p in real or not os.path.exists(p) or os.path.dirname(p) in {os.path.dirname(r) for r in real}


_REAL_HOSTS = ("api.smartthings.com", "auth-global.api.smartthings.com")


@pytest.fixture(autouse=True)
def _no_real_devices(monkeypatch):
    """No test reaches a real Fire TV stick or SmartThings. On the Mac the deploy step runs this suite
    next to the live sticks, so a test that shelled out to the real adb could press keys on Nick's TV.
    A real adb call fails as if adb were missing (what Linux CI already sees); a fake_adb script still runs.
    SmartThings calls fail as a connect error unless the test hands in its own transport."""
    import asyncio
    import subprocess

    import httpx

    real_exec, real_popen = asyncio.create_subprocess_exec, subprocess.Popen.__init__

    async def guarded_exec(program, *args, **kw):
        if _is_real_adb(program):
            raise FileNotFoundError(f"tests may not run the real adb ({program})")
        return await real_exec(program, *args, **kw)

    def guarded_popen(self, args, *a, **kw):
        prog = args if isinstance(args, (str, bytes)) or not hasattr(args, "__iter__") else list(args)[0]
        if not kw.get("shell") and _is_real_adb(prog):
            raise FileNotFoundError(f"tests may not run the real adb ({prog})")
        return real_popen(self, args, *a, **kw)

    real_async, real_sync = httpx.AsyncHTTPTransport.handle_async_request, httpx.HTTPTransport.handle_request

    def _check(request):
        if request.url.host in _REAL_HOSTS:
            raise httpx.ConnectError("tests may not call the real SmartThings", request=request)

    async def guarded_async(self, request):
        _check(request)
        return await real_async(self, request)

    def guarded_sync(self, request):
        _check(request)
        return real_sync(self, request)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", guarded_exec)
    monkeypatch.setattr(subprocess.Popen, "__init__", guarded_popen)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", guarded_async)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", guarded_sync)


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
    priority, as `scripts/deploy.py` does on the Mac (nice 10, or taskpolicy -b on efficiency cores): there a
    budget measures the scheduler, not the code. CI and normal runs still enforce it."""
    import os
    if os.environ.get("RZ_TESTS_LOW_PRIORITY") == "1":
        return
    assert per_s < budget_s, f"{what}: {per_s * 1000:.2f} ms, budget {budget_s * 1000:.1f} ms"
