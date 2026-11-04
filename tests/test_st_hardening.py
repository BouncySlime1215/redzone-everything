"""SmartThings failure handling (redzone/tv.py, hardening.smartthings), with a fake cloud (no TV, no network):
- 5xx / timeouts open a breaker: later flips skip the cloud at once (HOME/CEC take over), the
  keepalive's readback is the half-open probe and closes it when the cloud is back;
- a 429 is honoured for its Retry-After;
- a refused sign-in stops the cloud path until tv_setup.py rewrites the OAuth file, raises tv_auth
  in Health, and the supervisor pushes it once;
- a GET retries once after a fast 5xx; a POST never does (the command may have been applied);
- the flag off keeps the old behaviour exactly."""
import json

import httpx
import pytest

from redzone import tv as tvmod
from redzone.platform.health import PlatformHealth
from test_tv_input import FakeCloud, oauth


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class FlakyCloud(FakeCloud):
    """FakeCloud that can answer the devices API with a status, raise, or rate-limit."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.status = None          # e.g. 503 or 429 for every devices-API call
        self.raise_exc = None       # e.g. httpx.ConnectTimeout
        self.retry_after = "7"
        self.api_calls = 0
        self.posts = 0

    def handler(self, req):
        if req.url.path != "/oauth/token":
            self.api_calls += 1
            if req.method == "POST":
                self.posts += 1
            if self.raise_exc is not None:
                raise self.raise_exc("down", request=req)
            if self.status == 429:
                return httpx.Response(429, headers={"retry-after": self.retry_after})
            if self.status:
                return httpx.Response(self.status)
        return super().handler(req)


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(tvmod, "VERIFY_GAP_S", 0.0)
    monkeypatch.setattr(tvmod, "ST_RETRY_GAP_S", 0.0)


def make(tmp_path, hard=True, **kw):
    cloud = FlakyCloud(lag=0, **kw)
    clock = Clock()
    tv = tvmod.SamsungTV(st_device="tv-1", auth=oauth(tmp_path, cloud), transport=cloud.transport(),
                         clock=clock, hardening=lambda: hard)
    return tv, cloud, clock


async def test_cloud_outage_opens_the_breaker_and_later_flips_skip_the_cloud(tmp_path):
    tv, cloud, clock = make(tmp_path)
    assert await tv.set_input("HDMI2", methods=tv.paths())   # healthy first
    cloud.status = 503
    assert not await tv.set_input("HDMI1", methods=tv.paths())
    assert tv.st_breaker.state == "open" and tv.st_fault == "cloud_down"
    assert tv.paths() == []                                   # the controller goes straight to HOME/CEC
    n = cloud.api_calls
    assert await tv.input_status() is None and cloud.api_calls == n   # fails at once, no request
    assert "unreachable" in tv.cloud_blocked()


async def test_keepalive_probe_closes_the_breaker_when_the_cloud_returns(tmp_path):
    tv, cloud, clock = make(tmp_path)
    cloud.raise_exc = httpx.ConnectTimeout
    assert await tv.input_status() is None
    assert await tv.input_status() is None
    assert tv.st_breaker.state == "open"
    clock.t += tvmod.ST_BREAK_S + 1
    assert tv.st_breaker.state == "half-open"
    await tv.maintain()                       # probe while still down: open again, twice as long
    assert tv.st_breaker.state == "open" and tv.st_breaker.retry_in() == pytest.approx(2 * tvmod.ST_BREAK_S)
    cloud.raise_exc = None
    clock.t += 2 * tvmod.ST_BREAK_S + 1
    await tv.maintain()
    assert tv.st_breaker.state == "closed" and tv.st_fault == "" and tv.paths() == ["smartthings"]


async def test_429_is_honoured_for_its_retry_after(tmp_path):
    tv, cloud, clock = make(tmp_path)
    cloud.status, cloud.retry_after = 429, "120"
    assert await tv.input_status() is None
    assert tv.st_fault == "rate_limited" and tv.st_breaker.retry_in() == pytest.approx(120)
    assert tv.paths() == []
    clock.t += 121
    cloud.status = None
    await tv.maintain()
    assert tv.st_breaker.state == "closed" and tv.paths() == ["smartthings"]


async def test_huge_retry_after_is_capped(tmp_path):
    tv, cloud, clock = make(tmp_path)
    cloud.status, cloud.retry_after = 429, "86400"
    await tv.input_status()
    assert tv.st_breaker.retry_in() == pytest.approx(tvmod.RETRY_AFTER_MAX_S)


async def test_a_get_retries_once_after_a_fast_5xx_but_a_post_never_repeats(tmp_path):
    tv, cloud, clock = make(tmp_path)
    await tv.st_discover()
    cloud.status = 502
    n = cloud.api_calls
    assert await tv.input_status() is None
    assert cloud.api_calls - n == 2                     # GET: one retry
    tv.st_breaker.success()
    p = cloud.posts
    assert not await tv.st_input("HDMI2")
    assert cloud.posts - p == 1                          # POST: never repeated


async def test_refused_sign_in_blocks_the_cloud_until_setup_rewrites_the_oauth_file(tmp_path):
    tv, cloud, clock = make(tmp_path)
    cloud.reject_access.add("acc0")
    cloud.revoked = True
    assert not await tv.set_input("HDMI2", methods=tv.paths())
    assert tv.st_fault == "auth" and tv.paths() == []
    assert "tv_setup.py --smartthings" in tv.cloud_blocked()
    n = cloud.api_calls
    await tv.input_status()
    assert cloud.api_calls == n
    # tv_setup.py --smartthings writes a new sign-in
    cloud.revoked = False
    d = json.loads(tv.auth.path.read_text())
    d.update(refresh_token="new", access_token="fresh")
    tv.auth.path.write_text(json.dumps(d))
    import os
    os.utime(tv.auth.path, (clock.t + 5, clock.t + 5))
    assert tv.paths() == ["smartthings"]
    assert await tv.set_input("HDMI2", methods=tv.paths())
    assert tv.st_fault == ""


async def test_sign_in_server_down_is_an_outage_not_an_auth_failure(tmp_path):
    cloud = FlakyCloud(lag=0)
    clock = Clock()

    def handler(req):
        if req.url.path == "/oauth/token":
            return httpx.Response(503)
        return cloud.handler(req)
    tv = tvmod.SamsungTV(st_device="tv-1", auth=oauth(tmp_path, cloud, expires_at=0.0, clock=clock),
                         transport=httpx.MockTransport(handler), clock=clock, hardening=lambda: True)
    tv.auth.transport = httpx.MockTransport(handler)
    assert await tv.input_status() is None
    assert tv.st_fault == "cloud_down"


async def test_flag_off_keeps_the_old_behaviour(tmp_path):
    tv, cloud, clock = make(tmp_path, hard=False)
    cloud.status = 503
    for _ in range(3):
        await tv.input_status()
    assert tv.st_breaker.state == "closed" and tv.st_fault == "" and tv.cloud_blocked() == ""
    cloud.status = 429
    await tv.input_status()
    assert tv.st_fault == "" and tv.paths() == ["smartthings"]


class _Ctl:
    def __init__(self, tv):
        self.tv, self.sticks, self.input_unknown, self.live_control = tv, [], False, False
        self._task, self._flipping, self._lock, self.on_target = None, False, None, None


class _Engine:
    def __init__(self, tv):
        self.control, self._pending_decision, self.cfg = _Ctl(tv), None, None


async def test_health_raises_tv_auth_and_tv_cloud_down(tmp_path):
    tv, cloud, clock = make(tmp_path)
    h = PlatformHealth(_Engine(tv))
    assert [c for c in h.conditions() if c[0].startswith("tv_")] == []
    cloud.status = 503
    await tv.input_status()
    await tv.input_status()
    assert [c[0] for c in h.conditions()] == ["tv_cloud_down"]
    tv._auth_down("refused")
    assert [c[0] for c in h.conditions()] == ["tv_auth"]
    off, _, _ = make(tmp_path / "x", hard=False) if (tmp_path / "x").mkdir() is None else (None, 0, 0)
    off.st_fault = "auth"
    assert PlatformHealth(_Engine(off)).conditions() == []


async def test_supervisor_pushes_tv_auth_once(tmp_path, monkeypatch):
    from redzone import supervisor as sup
    pushed = []

    class S:
        _faults_logged: set = set()
        last_faults: list = []

        def __init__(self, h):
            self.health = h
            self.records = []

        def _record(self, r):
            self.records.append(r)

        async def send_push(self, title, body, priority="default", tags="tv"):
            pushed.append(title)
            return True
    tv, cloud, clock = make(tmp_path)
    tv._auth_down("refused")
    s = S(PlatformHealth(_Engine(tv)))
    s._faults_logged = set()
    await sup.Supervisor.rule_invariants(s, 0.0)
    await sup.Supervisor.rule_invariants(s, 1.0)
    assert pushed == ["RedZone: tv_auth"]
