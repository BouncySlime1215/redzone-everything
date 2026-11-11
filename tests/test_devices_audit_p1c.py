"""Device audit 2026-11-08 (docs/audits/2026-11-08-devices.md), card C: TV and stick fixes, one test each.
Repro for each is in the test's docstring; none touches a real TV, stick or SmartThings."""
import asyncio
import threading
import time

import httpx
import pytest

from redzone import tv as tvmod
from redzone.tuners import adb
from redzone.tuners import providers as more
from redzone.tuners.adb import RunResult, Stick
from redzone.tuners.tuners import CutWindowClosed, TuneResult
from test_providers import Clock, FakeStick
from test_tv_input import FakeCloud, oauth


@pytest.fixture(autouse=True)
def tok(tmp_path, monkeypatch):
    monkeypatch.setattr(tvmod, "TOKEN_FILE", tmp_path / "tok")
    (tmp_path / "tok").write_text("123")
    monkeypatch.setattr(tvmod, "VERIFY_GAP_S", 0.0)


class DeadCloud(FakeCloud):
    """SmartThings after Dec 15: every device call is refused."""

    def handler(self, req):
        if req.url.path.startswith("/v1"):
            self.calls.append((req.method, req.url.path))
            return httpx.Response(401)
        return super().handler(req)


def local_tv(tmp_path, cloud=None, **kw):
    cloud = cloud or DeadCloud()
    tv = tvmod.SamsungTV(host="10.0.0.5", local_trusted=True, st_device="tv-1", auth=oauth(tmp_path, cloud),
                         transport=cloud.transport(), log_path=tmp_path / "in.jsonl", **kw)
    return tv, cloud


# --- DEV-02 ---------------------------------------------------------------------------------

async def test_dev02_dead_cloud_first_falls_through_to_the_working_local_key(tmp_path):
    """Repro: methods=["smartthings","local"], SmartThings 401, local fine -> used to return False on the cloud."""
    tv, cloud = local_tv(tmp_path)
    sent = []

    async def key(k, times=1):
        tv._definite = False
        sent.append(k)
        return True
    tv.key = key
    assert await tv.set_input("HDMI2", methods=["smartthings", "local"])
    assert sent == ["KEY_HDMI2"] and tv.last_input.source == "local" and tv.last_input.trusted


async def test_dev02_a_timeout_is_ambiguous_and_never_tries_a_second_method(tmp_path):
    tv, _ = local_tv(tmp_path)
    calls = []

    async def key(k, times=1):
        tv._definite = False          # timed out: the key may have been delivered
        calls.append(k)
        return False
    tv.key = key
    tv.cloud_enabled = True
    assert not await tv.set_input("HDMI2", methods=["local", "smartthings"])
    assert calls == ["KEY_HDMI2"]


async def test_dev02_a_refused_local_connection_falls_to_the_next_method(tmp_path):
    cloud = FakeCloud(lag=0)
    tv = tvmod.SamsungTV(host="10.0.0.5", local_trusted=True, st_device="tv-1", auth=oauth(tmp_path, cloud),
                         transport=cloud.transport())

    class Refused:
        def send_key(self, k):
            raise ConnectionRefusedError("refused")

        def close(self):
            pass
    tv._tv = Refused()
    assert await tv.set_input("HDMI2", methods=["local", "smartthings"])
    assert tv.last_input.source == "smartthings" and tv.last_input.verified


# --- DEV-03 ---------------------------------------------------------------------------------

async def test_dev03_unreachable_cloud_does_not_hold_a_trusted_local_flip(tmp_path):
    """Repro: cloud ConnectTimeout, trusted local key -> the 8-read verify spun for tens of seconds."""
    cloud = FakeCloud()

    async def handler(req):
        if req.url.path.startswith("/v1"):
            await asyncio.sleep(1.0)               # each cloud read hangs, then times out
            raise httpx.ReadTimeout("slow")
        return cloud.handler(req)
    tv = tvmod.SamsungTV(host="10.0.0.5", local_trusted=True, st_device="tv-1", auth=oauth(tmp_path, cloud),
                         transport=httpx.MockTransport(handler))

    async def key(k, times=1):
        tv._definite = False
        return True
    tv.key = key
    t0 = time.monotonic()
    assert await tv.set_input("HDMI1", methods=["local"])
    assert time.monotonic() - t0 < 3.0 and tv.last_input.trusted


async def test_dev03_a_failed_readback_call_stops_after_one_read(tmp_path):
    tv, cloud = local_tv(tmp_path)

    async def key(k, times=1):
        tv._definite = False
        return True
    tv.key = key
    await tv.set_input("HDMI1", methods=["local"])
    reads = [c for c in cloud.calls if c[0] == "GET"]
    assert len(reads) <= 2                         # one refused call (plus its one renew-and-retry), not 8 rounds


# --- DEV-04 ---------------------------------------------------------------------------------

async def test_dev04_cloud_disabled_by_policy_makes_no_call_reads_no_oauth_and_raises_no_alert(tmp_path):
    class Tripwire(tvmod.SmartThingsAuth):
        def load(self):
            raise AssertionError("the OAuth file must not be read while the cloud is disabled")

        async def token(self, force=False):
            raise AssertionError("no token request while disabled")

    cloud = FakeCloud()
    tv = tvmod.SamsungTV(host="10.0.0.5", local_trusted=True, st_device="tv-1", cloud_enabled=False,
                         auth=Tripwire(tmp_path / "st.json", transport=cloud.transport()),
                         transport=cloud.transport(), hardening=lambda: True)
    assert not tv.cloud_ready and not tv.has_readback and not tv._hardened()
    assert tv.paths() == ["local"] and tv.ready()
    assert tv.cloud_view()["disabled"] and tv.view()["cloud_disabled"]
    assert await tv.input_status() is None and not await tv.st_input("HDMI1")
    await tv.maintain()
    assert cloud.calls == []


async def test_dev04_disabled_cloud_is_never_a_flip_path_even_when_asked(tmp_path):
    tv = tvmod.SamsungTV(st_device="tv-1", cloud_enabled=False, auth=tvmod.SmartThingsAuth(tmp_path / "none.json"))
    assert tv.paths() == [] and not tv.ready()
    assert not await tv.set_input("HDMI1", methods=["smartthings"])
    assert "disabled by policy" in tv.last_error


def test_dev04_from_devices_reads_the_policy_default(tmp_path, monkeypatch):
    """Nick, 2026-11-10 ("use smart things"): the shipped default is on again (the only flip that worked on his TV on
    Nov 9). `two_stick.smartthings_cloud: false` still means disabled by policy."""
    f = tmp_path / "devices.json"
    f.write_text('{"tv": {"ip": "10.0.0.5", "smartthings_device_id": "x"}}')
    assert not tvmod.SamsungTV.from_devices(f).cloud_disabled
    real = tvmod._config.default
    monkeypatch.setattr(tvmod._config, "default", lambda key, d=None: False if key == "two_stick.smartthings_cloud" else real(key, d))
    assert tvmod.SamsungTV.from_devices(f).cloud_disabled


# --- DEV-05 ---------------------------------------------------------------------------------

def test_dev05_a_stale_smartthings_method_does_not_put_the_cloud_first(tmp_path):
    tv, _ = local_tv(tmp_path, method="smartthings")
    assert tv.local_ready and tv.cloud_ready and tv.paths() == ["local", "smartthings"]


def test_dev05_tv_setup_writes_local_when_local_is_proven():
    import inspect
    from redzone import tvsetup
    assert 'tv["input_method"] = "local"' in inspect.getsource(tvsetup)


# --- DEV-07 ---------------------------------------------------------------------------------

async def test_dev07_a_backed_off_local_path_is_probed_by_the_keepalive(tmp_path):
    """Repro: two failed keys -> local_ready False for 300 s and nothing ever probed it."""
    tv = tvmod.SamsungTV(host="10.0.0.5", local_trusted=True, cloud_enabled=False)
    tv._note("local", False)
    tv._note("local", False)
    assert not tv.local_ready and not await tv.warm()

    class Sock:
        def open(self):
            pass

        def close(self):
            pass
    tv._tv = Sock()
    await tv.maintain()
    assert tv.local_ready
    assert tvmod.FAIL_BACKOFF_S <= 60


def test_dev07_reset_backoff_clears_it():
    tv = tvmod.SamsungTV(host="h", local_trusted=True)
    tv._note("local", False)
    tv._note("local", False)
    tv.reset_backoff()
    assert not tv._backed_off("local")


# --- DEV-10 ---------------------------------------------------------------------------------

async def test_dev10_maintain_soon_returns_at_once_and_maintain_is_bounded(tmp_path, monkeypatch):
    tv = tvmod.SamsungTV(host="h", local_trusted=True, cloud_enabled=False)

    async def slow():
        await asyncio.sleep(20)
    tv._maintain = slow
    t0 = time.monotonic()
    tv.maintain_soon()
    tv.maintain_soon()                       # a second call while one runs does nothing
    assert time.monotonic() - t0 < 0.5
    tv._maintain_task.cancel()
    monkeypatch.setattr(tvmod, "MAINTAIN_TIMEOUT_S", 0.05)
    t0 = time.monotonic()
    await tv.maintain()
    assert time.monotonic() - t0 < 1.0


# --- DEV-16 ---------------------------------------------------------------------------------

async def test_dev16_a_key_and_a_warm_never_use_the_socket_at_the_same_time(tmp_path):
    """Repro: warm() and key() ran on two threads over one socket; a drop could close it mid-send."""
    busy, overlap = [], []

    class Sock:
        def _use(self, what):
            if busy:
                overlap.append(what)
            busy.append(what)
            time.sleep(0.15)
            busy.pop()

        def open(self):
            self._use("open")

        def send_key(self, k):
            self._use("key")

        def close(self):
            pass
    tv = tvmod.SamsungTV(host="h", local_trusted=True, cloud_enabled=False)
    tv._tv = Sock()
    warm = asyncio.create_task(tv.warm())
    await asyncio.sleep(0.02)
    assert await tv.key("KEY_HDMI1")
    await warm
    assert overlap == []


async def test_dev16_a_hung_key_keeps_the_socket_until_its_thread_ends_then_drops_it(monkeypatch):
    monkeypatch.setattr(tvmod, "LOCAL_TIMEOUT_S", 0.05)
    gate = threading.Event()
    closed = []

    class Sock:
        def send_key(self, k):
            gate.wait(2)

        def close(self):
            closed.append(1)
    tv = tvmod.SamsungTV(host="h", local_trusted=True, cloud_enabled=False)
    tv._tv = Sock()
    assert not await tv.key("KEY_HDMI1")
    assert closed == [] and tv._local_lock.locked()      # not closed under the sending thread
    gate.set()
    await asyncio.sleep(0.2)
    assert closed == [1] and not tv._local_lock.locked()


# --- DEV-01, DEV-11, DEV-17 (tuners) ---------------------------------------------------------

def test_dev01_peacock_is_not_a_tuner():
    assert "peacock" not in more.EXTRA_TUNERS and not hasattr(more, "PeacockTuner")
    assert "peacock" not in more.register({})


async def test_dev11_a_slow_cold_start_is_not_cut_at_15_s():
    """Repro: `am start -W` was sent with timeout=15, so a 20 s cold start counted as a hang."""
    seen = []

    class Slow(FakeStick):
        async def shell(self, cmd, timeout=8.0):
            if cmd.startswith("am start"):
                seen.append(timeout)
            return await super().shell(cmd, timeout)
    st = Slow({"fubo": "com.fubo.firetv.screen"}, playing=lambda s: bool(s.launches))
    clock = Clock()
    await more.FuboTuner(sleep=clock.sleep, clock=clock).tune(st, "12345", "fubo:12345")
    assert seen and min(seen) >= 30


async def test_dev17_failure_diagnosis_is_bounded_and_skipped_after_the_cut():
    class Inner:
        app = role = "youtubetv"

        async def tune(self, stick, ref, target, may_cut=None):
            return TuneResult(False, target, 1.0, [], "video never started")

    class SlowScreen(FakeStick):
        async def screen_text(self):
            self.screen_reads += 1
            await asyncio.sleep(30)

    st = SlowScreen()
    t0 = time.monotonic()
    more_ = more.DiagnosingTuner(Inner())
    orig = more.DIAGNOSE_TIMEOUT_S
    more.DIAGNOSE_TIMEOUT_S = 0.05
    try:
        res = await more_.tune(st, "A", "youtubetv:espn")
        assert not res.ok and time.monotonic() - t0 < 1.0
        st2 = FakeStick(screen="Sign in")
        res = await more_.tune(st2, "A", "youtubetv:espn", may_cut=lambda: False)
        assert st2.screen_reads == 0                     # the window closed: no screen read
    finally:
        more.DIAGNOSE_TIMEOUT_S = orig


# --- DEV-08 ---------------------------------------------------------------------------------

async def test_dev08_the_hunt_stops_when_the_cut_window_closes_mid_search():
    """Repro: hunt_channel had no cut check, so keys kept going for up to 120 s after the window closed."""
    from test_yttv_search_abc import FX, hunt_channel, load_table, tool, LOCAL_SEARCH_QUERY, MID
    import copy
    from redzone.tuners.prime_search import CapturedScreen
    fx = copy.deepcopy(FX)
    now = [1000.0]

    async def sleep(s):
        now[0] += s
    stick = tool.FixtureStick(fx, lambda: now[0])
    seq = [0]

    async def observe():
        seq[0] += 1
        now[0] += .3
        return CapturedScreen(tool._ocr(fx["scenes"].get(stick.scene, {"boxes": []})), seq[0], now[0])
    def before_key():
        if any(c.startswith("key ") for c in stick.calls):
            raise CutWindowClosed([])                   # the window closed after the first key
    h = await hunt_channel(stick, observe, key="abc", table=load_table(), query=LOCAL_SEARCH_QUERY["abc"],
                           sleep=sleep, clock=lambda: now[0], wall=lambda: MID, collect_codes=False,
                           channel_page=True, before_key=before_key)
    sent = [c for c in stick.calls if c.startswith("key ")]
    assert h.status == "stopped" and "window closed" in h.reason and len(sent) == 1


# --- DEV-14, DEV-15 (adb) --------------------------------------------------------------------

async def test_dev14_a_failed_identify_does_not_make_an_error_string_the_model(monkeypatch):
    st = Stick("10.0.0.9:5555")

    async def shell(cmd, timeout=8.0):
        return RunResult(False, "timeout/error: no reply in 6s", 6.0)
    st.shell = shell
    await st.identify()
    assert st.model == ""


async def test_dev14_an_unlisted_model_that_reports_vega_is_not_controllable():
    st = Stick("10.0.0.9:5555")
    st.state = "connected"

    async def shell(cmd, timeout=8.0):
        if cmd.startswith("getprop"):
            return RunResult(True, "AFTNEW9\n\nVega OS 1.1\nnull\n", 0.1)
        return RunResult(True, "", 0.1)
    st.shell = shell
    await st.identify()
    assert "Vega" in st.os and not st.controllable


async def test_dev15_an_unauthorized_stick_is_not_reconnected_every_tick(monkeypatch):
    """Repro: `adb connect` re-sent at every ~9 s tick, re-raising the allow-debugging prompt over the game."""
    calls = []

    async def fake_adb(*args, timeout=8.0):
        calls.append(args)
        return RunResult(True, "failed to authenticate to 10.0.0.9:5555", 0.1)
    monkeypatch.setattr(adb, "_adb", fake_adb)
    now = [1000.0]
    monkeypatch.setattr(adb.time, "time", lambda: now[0])
    st = Stick("10.0.0.9:5555")
    assert not await st.connect() and st.state == "unauthorized"
    for _ in range(5):
        now[0] += 10                                    # a keepalive tick (~9 s)
        assert not await st.connect()
    assert len(calls) == 1
    now[0] += adb.UNAUTH_RETRY_S                        # five minutes on: one more try
    await st.connect()
    assert len(calls) == 2
