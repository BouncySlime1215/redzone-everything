"""The TV's own input change as the primary flip, with fakes (no TV, no SmartThings, no sticks):
- SmartThings OAuth: access token renewed with the refresh token (rotated, saved owner-only), a 401
  renews once and retries, an expired sign-in says how to fix it;
- set_input: SmartThings command + READBACK, setup-proven local keys trusted without readback,
  every attempt logged with its latency;
- the controller flips by the TV first: no HOME and no relaunch on either stick, the destination is
  checked still playing; HOME only when the TV path fails; a stick with CEC off gets CEC back for a
  HOME fallback and loses it again; the game we left is kept (not reloaded) when its stick's CEC is off;
- CEC alone must agree twice before we believe the TV moved without us; a TV readback wins."""
import asyncio
import json
import os
import stat

import httpx
import pytest

from redzone import control as ctl
from redzone import tv as tvmod
from redzone.tuners.adb import RunResult
from test_hot_standby import FakeStick, refreshed, two  # noqa: F401  (fixture)


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(ctl, "FLIP_POLL_S", 0.01)
    monkeypatch.setattr(ctl, "RESUME_POLL_S", 0.01)
    monkeypatch.setattr(ctl, "RELAUNCH_AFTER_S", 0.0)
    monkeypatch.setattr(ctl, "RESUME_WAIT_S", 0.2)
    monkeypatch.setattr(ctl, "RESUME_PLAY_WAIT_S", 0.2)
    monkeypatch.setattr(ctl, "TV_PLAYING_WAIT_S", 0.2)
    monkeypatch.setattr(ctl, "CEC_REENABLE_S", 0.0)
    monkeypatch.setattr(ctl, "REFRESH_POLL_S", 0.01)
    monkeypatch.setattr(ctl, "KEEP_CONFIRM_S", 0.02)
    monkeypatch.setattr(tvmod, "VERIFY_GAP_S", 0.0)
    FakeStick.last_input_host = "a:5555"
    yield
    FakeStick.last_input_host = None


# --- a fake SmartThings ------------------------------------------------------------------------

class FakeCloud:
    """Token endpoint + devices API. The TV's input changes `lag` reads after setInputSource."""

    def __init__(self, input="HDMI1", lag=1, expires_in=86400):
        self.input, self.lag, self.expires_in = input, lag, expires_in
        self.pending, self.reads_left = None, 0
        self.calls, self.refreshes, self.revoked, self.reject_access = [], 0, False, set()
        self.n = 0

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.calls.append((req.method, req.url.path))
        if req.url.path == "/oauth/token":
            form = dict(x.split("=", 1) for x in req.content.decode().split("&"))
            if form.get("grant_type") == "refresh_token":
                if self.revoked:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                self.refreshes += 1
            self.n += 1
            return httpx.Response(200, json={"access_token": f"acc{self.n}", "refresh_token": f"ref{self.n}",
                                             "expires_in": self.expires_in, "scope": tvmod.ST_SCOPES})
        tok = req.headers.get("authorization", "").removeprefix("Bearer ")
        if tok in self.reject_access:
            return httpx.Response(401)
        if req.url.path == "/v1/devices":
            return httpx.Response(200, json={"items": [{"deviceId": "tv-1", "label": "Samsung 6 Series (55)",
                                                       "components": [{"capabilities": [{"id": "mediaInputSource"}]}]}]})
        if req.url.path.endswith("/capabilities/mediaInputSource/status"):
            if self.pending and self.reads_left <= 0:
                self.input, self.pending = self.pending, None
            self.reads_left -= 1
            return httpx.Response(200, json={"inputSource": {"value": self.input},
                                             "supportedInputSources": {"value": ["HDMI1", "HDMI2", "digitalTv"]}})
        if req.url.path == "/v1/devices/tv-1/commands":
            body = json.loads(req.content)
            self.pending, self.reads_left = body["commands"][0]["arguments"][0], self.lag
            return httpx.Response(200, json={"results": [{"status": "ACCEPTED"}]})
        return httpx.Response(404)

    def transport(self):
        return httpx.MockTransport(self.handler)


def oauth(tmp_path, cloud, clock=None, **d):
    f = tmp_path / "st.json"
    base = {"client_id": "cid", "client_secret": "sec", "refresh_token": "ref0", "access_token": "acc0",
            "expires_at": 10 ** 12, "refreshed_at": 10 ** 12}
    base.update(d)
    f.write_text(json.dumps(base))
    return tvmod.SmartThingsAuth(f, transport=cloud.transport(), **({"clock": clock} if clock else {}))


async def test_expiring_access_token_is_renewed_with_the_refresh_token_and_saved_private(tmp_path):
    cloud = FakeCloud()
    auth = oauth(tmp_path, cloud, expires_at=100.0, clock=lambda: 50.0)   # 50 s left: renew
    assert await auth.token() == "acc1"
    saved = json.loads(auth.path.read_text())
    assert saved["refresh_token"] == "ref1" and saved["access_token"] == "acc1"   # rotated, kept
    assert stat.S_IMODE(os.stat(auth.path).st_mode) == 0o600
    assert await auth.token() == "acc1" and cloud.refreshes == 1                  # fresh now: no second call


async def test_revoked_sign_in_says_how_to_fix_it_and_keeps_a_still_valid_token(tmp_path):
    cloud = FakeCloud()
    cloud.revoked = True
    auth = oauth(tmp_path, cloud, expires_at=1000.0, clock=lambda: 900.0)   # inside the early-renew window
    assert await auth.token() == "acc0"            # renewal refused, but the old one works another 100 s
    assert "tv_setup.py --smartthings" in auth.last_error
    late = oauth(tmp_path, cloud, expires_at=100.0, clock=lambda: 200.0)
    assert await late.token() is None


async def test_a_refresh_by_another_process_is_picked_up_not_overwritten(tmp_path):
    """Live lab, Nov 4: the running app cached the token file forever. A script (tv_local_probe.py) that
    refreshes rotates the refresh token, and the app would renew with the dead one and lose the TV."""
    cloud = FakeCloud()
    app = oauth(tmp_path, cloud, expires_at=100.0, clock=lambda: 50.0)
    assert app.load()["refresh_token"] == "ref0"                       # cached by the app
    script = tvmod.SmartThingsAuth(app.path, transport=cloud.transport(), clock=lambda: 50.0)
    assert await script.token() == "acc1"                              # the script refreshed: ref0 -> ref1
    assert app.load()["refresh_token"] == "ref1"                       # the app sees the rotation
    assert await app.token() == "acc1"                                 # still fresh for the app: no refresh
    assert cloud.refreshes == 1


async def test_maintain_rolls_the_refresh_token_forward_before_it_ages_out(tmp_path):
    cloud = FakeCloud()
    now = 10 ** 9
    auth = oauth(tmp_path, cloud, expires_at=now + 80000, refreshed_at=now - tvmod.REFRESH_EVERY_S - 1,
                 clock=lambda: now)
    await auth.maintain()
    assert cloud.refreshes == 1
    await auth.maintain()
    assert cloud.refreshes == 1


async def test_smartthings_input_is_read_back_and_logged_with_latency(tmp_path):
    cloud = FakeCloud(lag=2)
    tv = tvmod.SamsungTV(st_device="tv-1", auth=oauth(tmp_path, cloud), transport=cloud.transport(),
                         log_path=tmp_path / "tv_input.jsonl")
    assert tv.cloud_ready and tv.has_readback and tv.paths() == ["smartthings"]
    assert await tv.set_input("HDMI2", methods=tv.paths())
    ev = tv.last_input
    assert ev.verified and ev.how == "smartthings readback" and ev.observed == "HDMI2"
    assert ev.cmd_s is not None and ev.verify_s is not None and ev.total_s >= ev.cmd_s
    rec = json.loads((tmp_path / "tv_input.jsonl").read_text().splitlines()[-1])
    assert rec["requested"] == "HDMI2" and rec["verified"] and rec["total_s"] is not None


async def test_a_401_renews_once_and_retries_the_command(tmp_path):
    cloud = FakeCloud(lag=0)
    cloud.reject_access.add("acc0")
    tv = tvmod.SamsungTV(st_device="tv-1", auth=oauth(tmp_path, cloud), transport=cloud.transport())
    assert await tv.set_input("HDMI2", methods=["smartthings"])
    assert cloud.refreshes == 1


async def test_smartthings_that_never_shows_the_input_is_not_success(tmp_path, monkeypatch):
    monkeypatch.setattr(tvmod, "VERIFY_READS", 3)
    cloud = FakeCloud(lag=99)
    tv = tvmod.SamsungTV(st_device="tv-1", auth=oauth(tmp_path, cloud), transport=cloud.transport())
    assert not await tv.set_input("HDMI2", methods=["smartthings"])
    assert tv.last_input.accepted and not tv.last_input.ok and tv.last_input.observed == "HDMI1"


async def test_two_failures_in_a_row_back_the_method_off(tmp_path, monkeypatch):
    monkeypatch.setattr(tvmod, "VERIFY_READS", 1)
    cloud = FakeCloud(lag=99)
    tv = tvmod.SamsungTV(st_device="tv-1", auth=oauth(tmp_path, cloud), transport=cloud.transport())
    for _ in range(2):
        await tv.set_input("HDMI2", methods=["smartthings"])
    assert not tv.cloud_ready and not tv.ready()


async def test_setup_proven_local_key_is_trusted_without_readback(tmp_path, monkeypatch):
    monkeypatch.setattr(tvmod, "TOKEN_FILE", tmp_path / "tok")
    (tmp_path / "tok").write_text("123")
    tv = tvmod.SamsungTV(host="10.0.0.5", local_trusted=True, auth=tvmod.SmartThingsAuth(tmp_path / "none.json"))
    sent = []

    async def key(k, times=1):
        sent.append(k)
        return True
    tv.key = key
    assert tv.local_ready and tv.paths() == ["local"]
    assert await tv.set_input("HDMI1", methods=tv.paths())
    assert sent == ["KEY_HDMI1"] and tv.last_input.trusted and tv.last_input.how == "setup-proven key"


async def test_unproven_or_contradicted_local_key_is_not_trusted(tmp_path, monkeypatch):
    monkeypatch.setattr(tvmod, "TOKEN_FILE", tmp_path / "tok")
    (tmp_path / "tok").write_text("123")
    tv = tvmod.SamsungTV(host="10.0.0.5", auth=tvmod.SmartThingsAuth(tmp_path / "none.json"))
    assert not tv.local_ready                               # tv_setup.py never watched it switch
    tv.local_trusted = True
    tv.st_device = "tv-1"

    async def key(k, times=1):
        return True

    async def status():
        return "HDMI2"                                      # the TV itself says otherwise
    tv.key, tv.input_status = key, status
    assert not await tv.set_input("HDMI1", methods=["local"])
    assert not tv.last_input.trusted


async def test_cec_corroborates_a_local_key_when_the_tv_has_no_readback(tmp_path, monkeypatch):
    tv = tvmod.SamsungTV(host="10.0.0.5", auth=tvmod.SmartThingsAuth(tmp_path / "none.json"))

    async def key(k, times=1):
        return True

    async def cec(hdmi):
        return hdmi == "HDMI2"
    tv.key, tv.corroborate = key, cec
    assert await tv.set_input("HDMI2", methods=["local"])
    assert tv.last_input.verified and tv.last_input.how == "cec"


# --- the controller ----------------------------------------------------------------------------

def tv_path(c, works=True, ambiguous=False):
    """A TV whose input change works (moves FakeStick.last_input_host) or doesn't."""
    sent = []

    async def set_input(hdmi, methods=None):
        sent.append(hdmi)
        ev = tvmod.InputEvidence(hdmi, source="local", accepted=None if ambiguous else True)
        if works:
            FakeStick.last_input_host = next(h for h, p in c.hdmi.items() if p == hdmi)
            ev.trusted, ev.total_s, ev.how = True, 0.05, "setup-proven key"
        c.tv.last_input = ev
        if not works:
            c.tv.last_error = "TV didn't switch"
        return works
    c.tv.ready = lambda: True
    c.tv.paths = lambda: ["local"]
    c.tv.set_input = set_input
    return sent


def homes(stick):
    return [x for x in stick.log if "KEYCODE_HOME" in x]


def relaunches(stick):
    return [x for x in stick.log if x.startswith("am start")]


async def test_flip_by_the_tv_presses_nothing_on_either_stick(two):
    c, tuner = two
    sent = tv_path(c)
    c.on_target = "youtubetv:fox"
    out = await c.execute("youtubetv:espn", may_cut=lambda: True)
    assert out.ok and sent == ["HDMI2"] and c.main is c.sticks[1]
    assert "by the TV's input (local)" in out.detail and out.resumed is True
    for s in c.sticks:
        assert not homes(s) and not relaunches(s) and "KEYCODE_WAKEUP" not in s.log
    assert c._flip_how == "tv:local" and "tv_flip" in c.latency.view()
    assert c.switch_times()["last_flip"]["how"] == "auto"


async def test_tv_path_failure_falls_back_to_home_on_the_destination_only(two):
    c, _ = two
    sent = tv_path(c, works=False)
    c.on_target = "youtubetv:fox"
    out = await c.execute("youtubetv:espn", may_cut=lambda: True)
    assert out.ok and sent == ["HDMI2"] and c._flip_how == "home"
    assert homes(c.sticks[1]) and not homes(c.sticks[0])


async def test_ambiguous_tv_command_still_completes_by_home_even_if_the_moment_passed(two):
    c, _ = two
    tv_path(c, works=False, ambiguous=True)
    c.on_target = "youtubetv:fox"
    calls = iter([True, True, True, False, False])   # the exit check closes after the TV command
    await c.execute("youtubetv:espn", may_cut=lambda: next(calls, False))
    # The TV may already show HDMI2: pushing the SAME destination is the only safe follow-up.
    assert homes(c.sticks[1]) and not homes(c.sticks[0])


async def test_destination_paused_after_a_tv_flip_gets_media_play(two):
    c, _ = two
    tv_path(c)
    b = c.sticks[1]
    c.on_target = "youtubetv:fox"
    real = c.tv.set_input

    async def set_input(hdmi, methods=None):
        b.playing = "paused"            # it paused anyway as the input changed
        return await real(hdmi, methods)
    c.tv.set_input = set_input

    async def key(*codes):
        b.log.extend(codes)
        if "KEYCODE_MEDIA_PLAY" in codes:
            b.playing = "playing"
        return RunResult(True, "", 0.01)
    b.key = key
    out = await c.execute("youtubetv:espn", may_cut=lambda: True)
    assert out.ok and "media play" in out.detail and not homes(b)


async def test_home_fallback_onto_a_cec_off_stick_turns_cec_on_then_off_again(two, monkeypatch):
    c, _ = two
    b = c.sticks[1]
    c._cec_on = {"a:5555": False, "b:5555": False}
    c.on_target = "youtubetv:fox"
    sleeps = []
    real_sleep = asyncio.sleep

    async def fast_sleep(s, *a):
        sleeps.append(s)
        await real_sleep(0)
    monkeypatch.setattr(ctl.asyncio, "sleep", fast_sleep)
    out = await c.execute("youtubetv:espn", may_cut=lambda: True)
    assert out.ok and c._flip_how == "home"
    on = [i for i, x in enumerate(b.log) if x == ctl.CEC_SET_CMD.format(v=1)]
    home = [i for i, x in enumerate(b.log) if "KEYCODE_HOME" in x]
    assert on and home and on[0] < home[0]
    await asyncio.gather(*c._cec_restore.values())
    assert b.log[-1] == ctl.CEC_SET_CMD.format(v=0) and c._cec_on["b:5555"] is False
    assert not any("hdmi_control_enabled" in x for x in c.sticks[0].log)   # the stick we left: untouched


async def test_game_left_on_a_cec_off_stick_is_kept_not_reloaded(two):
    c, tuner = two
    tv_path(c)
    a = c.sticks[0]
    c._cec_on = {"a:5555": False, "b:5555": False}
    c.on_target = "youtubetv:fox"
    a.remember("youtubetv:fox", {"state": "playing", "pkg": "yttv.pkg", "position": 0, "updated": 0})

    async def playback(package=None):   # keeps running on its own clock
        return {"state": "playing", "pkg": "yttv.pkg", "position": 0, "updated": 0}
    a.playback = playback
    out = await c.execute("youtubetv:espn", may_cut=lambda: True)
    assert out.ok
    await refreshed(c)
    assert c.hidden_target == "youtubetv:fox" and c.hidden_ready
    assert [t for h, t in tuner.calls if h == "a:5555"] == []   # no reload of the game we left


async def test_cec_alone_must_agree_twice_after_our_own_flip(two):
    c, _ = two
    tv_path(c)
    assert not c.tv.has_readback        # no SmartThings sign-in: CEC is all we have
    c.on_target = "youtubetv:fox"
    assert (await c.execute("youtubetv:espn", may_cut=lambda: True)).ok
    assert c.main is c.sticks[1]
    FakeStick.last_input_host = "a:5555"          # a stale flag says stick a
    assert not await c.sync_active_from_cec() and c.main is c.sticks[1]
    FakeStick.last_input_host = "b:5555"
    await c.sync_active_from_cec()
    FakeStick.last_input_host = "a:5555"          # once more: still not twice in a row
    assert not await c.sync_active_from_cec() and c.main is c.sticks[1]
    assert await c.sync_active_from_cec() and c.main is c.sticks[0]


async def test_tv_readback_decides_which_stick_is_on_screen(two, monkeypatch):
    c, _ = two
    monkeypatch.setattr(tvmod.SamsungTV, "has_readback", property(lambda self: True))
    c._flip_gen = 1

    async def status():
        return "HDMI2"
    c.tv.input_status = status
    assert await c.sync_active_from_cec() and c.main is c.sticks[1]
    assert not any("mIsActiveSource" in x for s in c.sticks for x in s.log)


async def test_read_cec_state(two):
    c, _ = two

    async def shell(cmd, timeout=8.0, s=c.sticks[0]):
        s.log.append(cmd)
        return RunResult(True, "0\n" if "hdmi_control_enabled" in cmd else "", 0.01)
    c.sticks[0].shell = shell
    st = await c.read_cec_state()
    assert st["a:5555"] is False and c.cec_off(c.sticks[0]) and not c.cec_off(c.sticks[1])


def test_from_devices_trusts_local_keys_only_when_setup_proved_them(tmp_path):
    f = tmp_path / "devices.json"
    f.write_text(json.dumps({"tv": {"ip": "10.0.0.5", "local_input_verified": True, "network_verified": False}}))
    assert not tvmod.SamsungTV.from_devices(f).local_trusted   # the IP was never checked against the MAC
    f.write_text(json.dumps({"tv": {"ip": "10.0.0.5", "local_input_verified": True, "network_verified": True,
                                    "smartthings_device_id": "tv-1", "input_method": "local"}}))
    t = tvmod.SamsungTV.from_devices(f)
    assert t.local_trusted and t.st_device == "tv-1" and t.method == "local"


async def test_a_socket_the_tv_closed_is_reopened_once_but_a_timeout_is_not_retried(monkeypatch):
    tv = tvmod.SamsungTV(host="10.0.0.5")
    sends = []

    class Remote:
        def __init__(self, fail):
            self.fail = fail

        def send_key(self, k):
            sends.append(k)
            if self.fail:
                raise self.fail

        def close(self):
            pass
    remotes = iter([Remote(BrokenPipeError()), Remote(None)])
    monkeypatch.setattr(tv, "_local", lambda: tv._tv or setattr(tv, "_tv", next(remotes)) or tv._tv)
    assert await tv.key("KEY_HDMI1") and sends == ["KEY_HDMI1", "KEY_HDMI1"]
    sends.clear()
    tv._tv = None
    remotes = iter([Remote(TimeoutError()), Remote(None)])
    assert not await tv.key("KEY_HDMI1") and sends == ["KEY_HDMI1"]
