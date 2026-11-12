"""Samsung TV: switch HDMI inputs (two-stick mode) and duck the volume (announcer).

The TV's own input change is the fast flip: the stick we flip to keeps playing (no HOME, no
Fire TV launcher, no relaunch), and the stick we leave keeps playing too when its HDMI-CEC is
off (scripts/stick_cec.py). Two ways to send it; scripts/tv_setup.py proves which one works:

  1. local       samsungtvws over the home network (port 8002, a token saved after you press
                 "Allow" on the TV once). KEY_HDMI1/KEY_HDMI2 are a no-op on some models, so a
                 local key only counts once tv_setup.py has watched it switch THIS TV
                 (devices.json tv.local_input_verified). The connection is kept open: a key on an
                 open socket is ~50 ms, a fresh handshake ~1 s.
  2. smartthings Samsung's cloud (mediaInputSource.setInputSource). The Mac never needs to reach
                 the TV. Authorized by OAuth with a refresh token (data/smartthings_oauth.json, never
                 in git), renewed automatically; personal tokens made after Dec 2024 expire after
                 24 h and remain only a fallback (SMARTTHINGS_TOKEN in .env). Capability and input
                 names are READ from the TV, never guessed. Requires an explicitly selected device
                 ID, never the first TV in the account. Its input READBACK is the strongest
                 evidence we have of what the viewer sees, so it also verifies local keys.

SmartThings is ON again (Nick, 2026-11-10: "use smart things"; on Nov 9 it was the only flip that worked on his
TV, 9 of 9 against 0 of 3 for the local key). It was dropped on 2026-11-08 because free access ends 2026-12-15
and the $0 rule forbids a paid plan, so another flip path is needed by then. `two_stick.smartthings_cloud: false`
puts the cloud in the state "disabled by policy": no request, no probe, no OAuth file read, no Health alert, and
the planner (cloud_ready / ready()) stops counting it; the OAuth file is neither read nor deleted while it is off.

Every call has a timeout. False means unconfirmed, not necessarily unchanged: an ambiguous
delivery (a timeout) never triggers a second TV method (the first one may already have switched);
a definite non-delivery (refused connection, 401/403/429, breaker open, no token) falls through to
the next method (DEV-02). A
successful network write alone never confirms which input the viewer sees.

Each attempt is appended to data/tv_input.jsonl with its command and verify latency.

Sound (Nick, Nov 10: "Volume must be 1000% off"). `sound_guard.must_be_silent` stops, inside this class, every key
and SmartThings command that can raise the volume or unmute (KEY_VOLUP, KEY_MUTE, volumeUp, unmute, setVolume above
0), and volume_duck with them. `sound_guard.enabled` attaches redzone/platform/sound_guard.py: mute and volume 0
read back before the first switch and after every switch, a periodic check, and an incident (TV off, switching
blocked) when silence is required and not read back. Both off = no sound call at all.

SmartThings failure handling (`hardening.smartthings`, off until a live night proves it):
  401          renew the access token once and retry (always on). Still 401, or the refresh token
               refused (400/401): st_fault "auth". The cloud path stops (HOME/CEC flips take over)
               until data/smartthings_oauth.json changes (tv_setup.py --smartthings) or an hour passes,
               and Health raises tv_auth (ERROR), which the supervisor pushes once.
  429          st_fault "rate_limited": no SmartThings call until Retry-After (capped 300 s).
  5xx, network st_fault "cloud_down": a circuit breaker (redzone/platform/breaker.py) opens after two
  or timeout   in a row, 30 s doubling to 2 min. While open every call fails at once (0 s, no 8 s
               timeouts inside a flip), so flips go straight to HOME/CEC. The keepalive's readback
               is the half-open probe: the cloud comes back without a real flip paying for it.
               A GET that got a fast 5xx or never connected is retried once. A POST never is: the
               command may have been applied, and the readback decides.

Why: recorded in docs/memory/lessons.md (hardening.smartthings breaker); first added in 56d8adbf (2026-11-02) as "Two-stick hot standby: tune hidden stick, flip input only at a safe moment (CEC, TV remote, Smart...".
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import socket
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

import httpx

from redzone import config as _config
from redzone.machines.control import SMARTTHINGS
from redzone.platform.breaker import Breaker

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
TOKEN_FILE = DATA / "tv_token.txt"
OAUTH_FILE = DATA / "smartthings_oauth.json"
INPUT_LOG = DATA / "tv_input.jsonl"
ST_API = "https://api.smartthings.com/v1"
ST_TOKEN_URL = "https://auth-global.api.smartthings.com/oauth/token"
ST_AUTHORIZE_URL = "https://api.smartthings.com/oauth/authorize"
ST_SCOPES = "r:devices:* x:devices:*"
REFRESH_EARLY_S = 600.0      # renew the access token this long before it expires
REFRESH_EVERY_S = 6 * 3600   # ...and at least this often, so the refresh token never ages out unused
VERIFY_READS = 8             # SmartThings readback: this many reads...
VERIFY_GAP_S = 0.3           # ...this far apart (the TV reports its new input in ~1 s)
# DEV-03: the whole readback; a flip never waits longer than this for a cloud answer
VERIFY_DEADLINE_S = float(_config.default("two_stick.tv_verify_deadline_s", 2.5))
LOCAL_TIMEOUT_S = 6.0
# DEV-16: a key waits this long for a warm()/other key on the shared socket
LOCAL_LOCK_WAIT_S = float(_config.default("two_stick.tv_local_lock_wait_s", 3.0))
# DEV-10: one keepalive's TV upkeep is cut after this
MAINTAIN_TIMEOUT_S = float(_config.default("two_stick.tv_maintain_timeout_s", 20.0))
FAIL_BACKOFF_AFTER = 2       # this many failures in a row on one method...
FAIL_BACKOFF_S = 45.0        # ...and it isn't tried for this long (HOME takes over meanwhile); maintain() probes
                             # a backed-off local path, so a blip of the TV or Wi-Fi no longer costs 5 minutes (DEV-07)
INPUT_LOG_MAX = 2 * 1024 ** 2
ST_BREAK_AFTER = 2           # cloud failures in a row before the SmartThings breaker opens
ST_BREAK_S = 30.0            # first open; doubles per re-open...
ST_BREAK_MAX_S = 120.0       # ...up to this (a probe is one background read, so keep it short)
RETRY_AFTER_MAX_S = 300.0    # a 429's Retry-After is honoured up to this
RETRY_AFTER_DEFAULT_S = 60.0
AUTH_RETRY_S = 3600.0        # an auth failure is re-tried after this even if the OAuth file never changes
ST_RETRY_GAP_S = 0.3         # before the one retry of an idempotent GET

# Sound (Nick, Nov 10: "Volume must be 1000% off"). With sound_guard.must_be_silent on, nothing that can raise the
# volume or unmute leaves this class: these keys (KEY_MUTE toggles, so it can unmute) and these SmartThings commands.
LOUD_KEYS = frozenset({"KEY_VOLUP", "KEY_MUTE"})
SILENT_ST_COMMANDS = {"audiovolume": {"setvolume", "volumedown"}, "audiomute": {"mute", "setmute"}}


def silent(s: dict | None) -> bool:
    """Silent means muted AND volume 0, both read back from the TV. Anything unread is not silent."""
    s = s or {}
    v = s.get("volume")
    return s.get("mute") == "muted" and isinstance(v, (int, float)) and not isinstance(v, bool) and v == 0


def loud_command(capability: str, command: str, args: list | None) -> str:
    """Why this SmartThings command could make the TV audible ("" = it cannot). Any audio capability whose
    command is not on the short silent list counts (volumeUp, unmute, setVolume above 0, unknown ones)."""
    cap = capability.split(".")[-1].lower()
    cmd = command.lower()
    if cap not in SILENT_ST_COMMANDS and not cap.startswith(("audio", "volume", "mute")):
        return ""
    if cmd not in SILENT_ST_COMMANDS.get(cap, set()):
        return f"{capability}.{command}"
    a = list(args or [])
    if cmd == "setvolume" and not (a and isinstance(a[0], (int, float)) and not isinstance(a[0], bool) and a[0] == 0):
        return f"{capability}.setVolume {a[0] if a else '?'}"
    if cmd == "setmute" and (not a or a[0] != "muted"):
        return f"{capability}.setMute {a[0] if a else '?'}"
    return ""


def _saved_settings() -> Callable[[str, object], object]:
    """The saved settings (data/config.yaml over the defaults), read once when the app builds its TV. A file that
    cannot be read falls back to the shipped defaults (sound guard off)."""
    try:
        return _config.Config().get
    except Exception:
        log.exception("TV: settings unreadable; sound settings use the shipped defaults")
        return _config.default


def _retry_after(r: httpx.Response) -> float:
    try:
        v = float(r.headers.get("retry-after", ""))
    except ValueError:
        v = RETRY_AFTER_DEFAULT_S
    return max(1.0, min(RETRY_AFTER_MAX_S, v))


def _env(name: str) -> str | None:
    if os.environ.get(name):
        return os.environ[name]
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith(f"{name}=") and line.split("=", 1)[1].strip():
                return line.split("=", 1)[1].strip()
    return None


def _write_private(path: Path, data: dict) -> None:
    """Atomic, owner-only (it holds credentials)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _never_sent(e: Exception) -> bool:
    """The write was refused outright (closed socket), so the key never reached the TV."""
    if isinstance(e, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
        return True
    return type(e).__name__ in ("WebSocketConnectionClosedException", "ConnectionClosed")


def _local_never_sent(e: Exception) -> bool:
    """A local key that definitely did not reach the TV (so another method is safe): the write was refused, or
    the connection never opened. A timeout is NOT here: the key may have been delivered."""
    if _never_sent(e):
        return True
    if isinstance(e, (TimeoutError, socket.timeout)) or "Timeout" in type(e).__name__:
        return False
    return isinstance(e, OSError) or type(e).__name__ in (
        "ConnectionFailure", "WebSocketBadStatusException", "WebSocketAddressException")


class _SocketBusy(asyncio.TimeoutError):
    """Timed out WAITING for the local socket, so nothing of ours ran: a definite non-delivery."""


@dataclass
class InputEvidence:
    """Transport acceptance and the TV's reported input are separate facts."""
    requested: str
    source: str = ""
    accepted: bool | None = False  # None: delivery is unknown (timeout / lost reply)
    verified: bool = False         # the TV (SmartThings readback) or the sticks (CEC) say it's on `requested`
    trusted: bool = False          # a local key tv_setup.py proved on this TV, delivered (no readback here)
    observed: str | None = None
    detail: str = ""
    how: str = ""                  # what verified it: "smartthings readback" | "cec" | "setup-proven key"
    cmd_s: float | None = None     # command sent -> TV/API acknowledged
    verify_s: float | None = None  # acknowledged -> confirmed
    total_s: float | None = None

    @property
    def ok(self) -> bool:
        return self.verified or self.trusted


class SmartThingsAuth:
    """OAuth tokens for SmartThings, renewed with the refresh token (rotated on every use and saved
    at once). Falls back to a personal token in .env (SMARTTHINGS_TOKEN), which expires in 24 h."""

    def __init__(self, path: Path | None = None, transport: httpx.AsyncBaseTransport | None = None,
                 clock: Callable[[], float] = time.time):
        self.path = path if path is not None else OAUTH_FILE   # read at call time (tests point it elsewhere)
        self.transport = transport
        self.clock = clock
        self.last_error = ""
        self.last_kind = ""      # why the last token call failed: "" | "auth" (refused) | "down" (5xx/network/429)
        self._lock = asyncio.Lock()
        self._d: dict | None = None
        self._stamp: tuple | None = None   # (mtime_ns, size) of the file self._d came from

    def load(self) -> dict:
        """The saved tokens, re-read whenever the file changed on disk. Another process (tv_setup.py,
        scripts/archive/tv_local_probe.py) may refresh, and SmartThings rotates the refresh token on every refresh:
        a copy cached here would then hold a dead refresh token and the app would lose the TV at
        its next renewal."""
        try:
            st = self.path.stat()
            stamp = (st.st_mtime_ns, st.st_size)
        except OSError:
            stamp = None
        if self._d is None or stamp != self._stamp:
            try:
                self._d = json.loads(self.path.read_text())
            except (OSError, ValueError):
                self._d = self._d if self._d is not None else {}   # a torn read keeps the last good copy
            self._stamp = stamp
        return self._d

    @property
    def configured(self) -> bool:
        d = self.load()
        return bool(d.get("refresh_token") and d.get("client_id") and d.get("client_secret")) or bool(_env("SMARTTHINGS_TOKEN"))

    @property
    def oauth(self) -> bool:
        d = self.load()
        return bool(d.get("refresh_token") and d.get("client_id"))

    def save(self, d: dict) -> None:
        d = {**d, "saved": self.clock()}
        _write_private(self.path, d)
        self._d = d
        try:
            st = self.path.stat()
            self._stamp = (st.st_mtime_ns, st.st_size)
        except OSError:
            self._stamp = None

    def _basic(self, d: dict) -> str:
        return "Basic " + base64.b64encode(f"{d['client_id']}:{d.get('client_secret', '')}".encode()).decode()

    async def exchange_code(self, client_id: str, client_secret: str, code: str, redirect_uri: str) -> bool:
        """One-time: the code from the browser redirect -> access + refresh token (tv_setup.py)."""
        d = {"client_id": client_id, "client_secret": client_secret, "redirect_uri": redirect_uri}
        return await self._token_call(d, {"grant_type": "authorization_code", "code": code,
                                          "redirect_uri": redirect_uri, "client_id": client_id})

    async def refresh(self) -> bool:
        async with self._lock:
            d = dict(self.load())
            if not self.oauth:
                self.last_error = "SmartThings OAuth not set up (run scripts/tv_setup.py --smartthings)"
                return False
            return await self._token_call(d, {"grant_type": "refresh_token", "client_id": d["client_id"],
                                              "refresh_token": d["refresh_token"]})

    async def _token_call(self, d: dict, form: dict) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10, transport=self.transport) as c:
                r = await c.post(ST_TOKEN_URL, data=form, headers={"Authorization": self._basic(d)})
        except httpx.HTTPError as e:
            self.last_error = f"SmartThings sign-in server unreachable: {e}"
            self.last_kind = "down"
            return False
        try:
            body = r.json()
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        if r.status_code in (400, 401) and form["grant_type"] == "refresh_token":
            self.last_error = ("SmartThings sign-in expired or was revoked "
                               f"({body.get('error', r.status_code)}): run scripts/tv_setup.py --smartthings")
            self.last_kind = "auth"
            return False
        if r.status_code >= 300 or not body.get("access_token"):
            self.last_error = f"SmartThings token request failed: HTTP {r.status_code} {body.get('error', '')}".strip()
            self.last_kind = "down" if (r.status_code >= 500 or r.status_code == 429) else "auth"
            return False
        now = self.clock()
        d.update(access_token=body["access_token"],
                 refresh_token=body.get("refresh_token") or d.get("refresh_token"),
                 expires_at=now + float(body.get("expires_in") or 86400), refreshed_at=now,
                 scope=body.get("scope", d.get("scope", ST_SCOPES)))
        self.save(d)
        self.last_error = ""
        self.last_kind = ""
        return True

    async def token(self, force: bool = False) -> str | None:
        d = self.load()
        if self.oauth:
            now = self.clock()
            fresh = (d.get("access_token") and float(d.get("expires_at") or 0) - now > REFRESH_EARLY_S)
            if force or not fresh:
                if not await self.refresh():
                    # A still-valid access token keeps working while the sign-in server is down.
                    d = self.load()
                    if not force and d.get("access_token") and float(d.get("expires_at") or 0) > now:
                        return d["access_token"]
                    return None
                d = self.load()
            return d.get("access_token")
        pat = _env("SMARTTHINGS_TOKEN")
        if not pat:
            self.last_error = "SmartThings not set up (run scripts/tv_setup.py --smartthings)"
            self.last_kind = "auth"
        return pat

    def stamp(self) -> float | None:
        """When the OAuth file last changed (tv_setup.py rewrote it): an auth failure is retried then."""
        try:
            return self.path.stat().st_mtime
        except OSError:
            return None

    async def maintain(self) -> None:
        """Keepalive: renew well before expiry, and at least every few hours (refresh tokens that sit
        unused for 30 days stop working; one that's used keeps rolling forward)."""
        if not self.oauth:
            return
        d = self.load()
        now = self.clock()
        if (float(d.get("expires_at") or 0) - now < REFRESH_EARLY_S * 2
                or now - float(d.get("refreshed_at") or 0) > REFRESH_EVERY_S):
            await self.refresh()


class _CloudBreaker(Breaker):
    """The SmartThings breaker: also opens for a 429's Retry-After (its own fields, not poked from outside).
    TODO(platform): move open_for into redzone/platform/breaker.Breaker."""

    def open_for(self, wait_s: float) -> None:
        self.hangs = max(self.hangs, self.threshold - 1)
        self.hang()                                       # open now...
        self.until = max(self.until, self.clock() + wait_s)  # ...for at least as long as the server asked


@dataclass
class SamsungTV:
    host: str | None = None
    method: str | None = None            # "local" | "smartthings" once one is proven
    st_device: str | None = None
    st_capability: str | None = None
    st_inputs: list[str] = field(default_factory=list)
    local_trusted: bool = False          # tv_setup.py watched KEY_HDMIx switch THIS TV
    mac: str | None = None
    last_error: str = ""
    last_input: InputEvidence | None = None
    auth: SmartThingsAuth | None = None
    transport: httpx.AsyncBaseTransport | None = None   # tests: a fake SmartThings
    log_path: Path | None = None
    clock: Callable[[], float] = time.time
    # Optional corroboration from the sticks (controller): "does the stick on `hdmi` say it's on screen?"
    corroborate: Callable[[str], Awaitable[bool | None]] | None = None
    # hardening.smartthings (the controller passes a live reader so the phone's toggle applies at once)
    hardening: Callable[[], bool] = lambda: False
    st_fault: str = ""                   # "" | "auth" | "rate_limited" | "cloud_down" (machine tv.smartthings: st_m)
    st_fault_detail: str = ""
    st_breaker: Breaker | None = None
    # Policy: from_devices() reads two_stick.smartthings_cloud (on again since 2026-11-10, Nick; false = dropped);
    # a plain SamsungTV(...) keeps the cloud so the fake-cloud tests and tv_setup.py still work.
    cloud_enabled: bool = True
    # Sound (sound_guard.*): a settings reader (dotted key, fallback) and the guard. A plain SamsungTV(...) reads the
    # shipped defaults (all off); from_devices() reads the saved settings once and attaches a guard only when
    # sound_guard.enabled is on. No guard = no sound call of any kind.
    sound_settings: Callable[[str, object], object] = _config.default
    sound_guard: object = None                      # redzone.platform.sound_guard.SoundGuard
    _tv: object = None
    _definite: bool = False                         # the last local key / cloud call certainly was not delivered
    _drop_pending: bool = False                     # close the local socket as soon as its worker thread ends
    _maintain_task: object = None
    _fails: dict = field(default_factory=dict)      # method -> consecutive failures
    _skip_until: dict = field(default_factory=dict)  # method -> not before
    _auth_failed: tuple | None = None                # (at, oauth file stamp) of the last auth failure (st_m "auth")
    _st_ok: bool = True                              # the last _st() call got an answer
    _local_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)

    def __post_init__(self):
        if self.auth is None:
            self.auth = SmartThingsAuth(transport=self.transport)
        if self.st_breaker is None:
            self.st_breaker = _CloudBreaker("smartthings", threshold=ST_BREAK_AFTER, backoff_s=ST_BREAK_S,
                                            max_backoff_s=ST_BREAK_MAX_S, clock=lambda: self.clock())

    @classmethod
    def from_devices(cls, path: Path | None = None) -> "SamsungTV":
        f = path or DATA / "devices.json"
        try:
            d = json.loads(f.read_text()) if f.exists() else {}
        except ValueError:
            d = {}
        tv = d.get("tv") or {}
        out = cls(host=tv.get("ip"), method=tv.get("input_method"),
                  st_device=tv.get("smartthings_device_id") or tv.get("st_device"),
                  local_trusted=bool(tv.get("local_input_verified")) and bool(tv.get("network_verified")),
                  mac=tv.get("mac") or tv.get("wifi_mac"), log_path=Path(INPUT_LOG),
                  cloud_enabled=bool(_config.default("two_stick.smartthings_cloud", False)),
                  sound_settings=_saved_settings())
        if out.sound_settings("sound_guard.enabled", False):
            from redzone.platform.sound_guard import SoundGuard
            out.sound_guard = SoundGuard(out)
        return out

    # --- sound (sound_guard.*) ---------------------------------------------------------------

    def must_be_silent(self) -> bool:
        try:
            return bool(self.sound_settings("sound_guard.must_be_silent", False))
        except Exception:
            return True                     # an unreadable setting never lets sound out

    def sound_blocked(self) -> str:
        """Why no input switch or key may go to the TV ("" = they may): an open sound incident."""
        g = self.sound_guard
        return g.blocked_why() if g is not None else ""

    def _refuse_sound(self, what: str) -> str:
        """Why `what` (a key or a SmartThings command) must not be sent ("" = it may). Sets last_error."""
        blocked = self.sound_blocked()
        why = (f"sound incident open ({blocked}): {what} not sent" if blocked else
               f"sound_guard.must_be_silent: {what} not sent" if self.must_be_silent() else "")
        if why:
            self.last_error = why
            self._definite = True
            log.warning("TV: %s", why)
        return why

    async def st_command(self, capability: str, command: str, args: list | None = None) -> bool:
        """One SmartThings command to the selected TV (sound guard: mute, volume, power). True = accepted."""
        if not self.st_device:
            self.last_error = "no SmartThings TV selected"
            self._definite = True
            return False
        r = await self._st("POST", f"/devices/{self.st_device}/commands", {"commands": [{
            "component": "main", "capability": capability, "command": command, "arguments": list(args or [])}]})
        return r is not None

    async def _st_value(self, capability: str, attr: str):
        r = await self._st("GET", f"/devices/{self.st_device}/components/main/capabilities/{capability}/status")
        try:
            return r[attr]["value"]
        except (KeyError, TypeError):
            return None

    async def sound_state(self, input_timeout_s: float = 10.0) -> dict:
        """Power, mute, volume and input read back from the TV (SmartThings). None = could not be read."""
        if not self.st_device or not self.has_readback:
            return {"power": None, "mute": None, "volume": None, "input": None}
        power, mute, volume = await asyncio.gather(self._st_value("switch", "switch"),
                                                   self._st_value("audioMute", "mute"),
                                                   self._st_value("audioVolume", "volume"))
        try:
            inp = await asyncio.wait_for(self.input_status(), input_timeout_s)
        except Exception:
            inp = None
        return {"power": power, "mute": mute, "volume": volume, "input": inp}

    # --- which paths can switch right now ------------------------------------------------

    def _backed_off(self, method: str) -> bool:
        return self.clock() < self._skip_until.get(method, 0.0)

    @property
    def local_ready(self) -> bool:
        """A local key we can trust to switch: proven at setup, paired (token saved), not backing off."""
        return bool(self.host and self.local_trusted and TOKEN_FILE.exists()) and not self._backed_off("local")

    @property
    def cloud_disabled(self) -> bool:
        """SmartThings is off by policy (N1). Checked FIRST everywhere so nothing probes, reads the OAuth
        file or raises an alert while it is off."""
        return not self.cloud_enabled

    @property
    def cloud_ready(self) -> bool:
        if self.cloud_disabled:
            return False
        return (bool(self.st_device and self.auth and self.auth.configured) and not self._backed_off("smartthings")
                and not self.cloud_blocked())

    # --- SmartThings failure handling (hardening.smartthings) ---------------------------------

    def _hardened(self) -> bool:
        if self.cloud_disabled:
            return False   # no breaker, no probe, no Health alert (platform/health.py asks this first)
        try:
            return bool(self.hardening())
        except Exception:
            return False

    def _auth_blocked(self) -> bool:
        """Is the cloud still blocked by a refused sign-in? A pure check: the re-arm is _auth_rearm."""
        got = self._auth_failed
        if got is None:
            return False
        at, stamp = got
        return not (self.clock() - at >= AUTH_RETRY_S or (self.auth and self.auth.stamp() != stamp))

    def _auth_rearm(self) -> None:
        """tv_setup.py ran again (or an hour passed) since the sign-in was refused: try once more, with the new
        sign-in read fresh (not the cached refused one). Called before a cloud call, never inside a check."""
        if self.st_m.is_("auth") and not self._auth_blocked():
            self.st_m.to("auth_retry", why="OAuth file changed or AUTH_RETRY_S passed")
            if self.auth is not None:
                self.auth._d = None

    def cloud_blocked(self) -> str:
        """Why no SmartThings call should go out right now ("" = it may). Only with hardening.smartthings."""
        if not self._hardened():
            return ""
        if self._auth_blocked():
            return "SmartThings sign-in refused: run scripts/tv_setup.py --smartthings"
        b = self.st_breaker
        if b.state == "open":
            return (f"SmartThings {'rate-limited' if self.st_fault == 'rate_limited' else 'unreachable'}"
                    f": retry in {b.retry_in():.0f}s")
        return ""

    def _cloud_ok(self) -> None:
        if self.st_fault in ("cloud_down", "rate_limited") or self.st_breaker.state != "closed":
            log.info("SmartThings answering again")
        self.st_breaker.success()
        self.st_fault_detail = ""
        self.st_m.to("ok", why="SmartThings answered")   # the sign-in worked: any auth fault is over too

    def _cloud_down(self, detail: str) -> None:
        was = self.st_breaker.state
        self.st_breaker.hang()
        self.st_fault_detail = detail
        self.st_m.to("cloud_down", why=detail)
        if was != "open" and self.st_breaker.state == "open":
            log.warning("SmartThings %s: HOME/CEC flips for %.0fs", detail, self.st_breaker.retry_in())

    def _rate_limited(self, wait_s: float) -> None:
        b = self.st_breaker
        _CloudBreaker.open_for(b, wait_s)   # open now, for at least as long as SmartThings asked
        self.st_fault_detail = f"HTTP 429, retry after {wait_s:.0f}s"
        self.st_m.to("rate_limited", why=self.st_fault_detail)
        log.warning("SmartThings rate limit: HOME/CEC flips for %.0fs", b.retry_in())

    def _auth_down(self, detail: str) -> None:
        if self.st_fault != "auth":
            log.error("SmartThings sign-in refused (%s): HOME/CEC flips until tv_setup.py --smartthings", detail)
        self._auth_failed = (self.clock(), self.auth.stamp() if self.auth else None)   # -> st_m "auth"
        self.st_fault_detail = detail
        self.st_breaker.release()

    def cloud_view(self) -> dict:
        if self.cloud_disabled:
            return {"fault": "", "detail": "disabled by policy (SmartThings is dropped, local key + CEC)",
                    "disabled": True, "hardening": False, "breaker": self.st_breaker.view(), "blocked": "",
                    "machine": self.st_m.view(history=3)}
        return {"fault": self.st_fault, "detail": self.st_fault_detail, "hardening": self._hardened(),
                "breaker": self.st_breaker.view(), "blocked": self.cloud_blocked(),
                "machine": self.st_m.view(history=3)}

    @property
    def has_readback(self) -> bool:
        """The TV itself can tell us its input (SmartThings)."""
        if self.cloud_disabled:
            return False
        return bool(self.st_device and self.auth and self.auth.configured)

    def ready(self) -> bool:
        """Is a TV-side input change worth trying before HOME?"""
        return self.local_ready or self.cloud_ready

    def paths(self) -> list[str]:
        # Local first whenever it is proven and healthy (DEV-05): a stale devices.json input_method of
        # "smartthings" must not make the dead cloud the primary path.
        out = []
        if self.local_ready:
            out.append("local")
        if self.cloud_ready:
            out.append("smartthings")
        return out

    def view(self) -> dict:
        """For the phone's Health card and scripts/rz.py."""
        last = self.last_input
        out = {"local": self.local_ready, "smartthings": self.cloud_ready, "readback": self.has_readback,
               "method": self.method, "cloud_disabled": self.cloud_disabled, "last_error": self.last_error, "cloud": self.cloud_view(),
               "last": asdict(last) if last else None}
        if self.sound_guard is not None:
            out["sound"] = self.sound_guard.view()
        return out

    def reset_backoff(self, method: str | None = None) -> None:
        """Forget a backoff (a wake / network recovery says the TV is reachable again). DEV-07."""
        for m in ([method] if method else list(self._skip_until)):
            self._skip_until.pop(m, None)
            self._fails[m] = 0

    def _note(self, method: str, ok: bool) -> None:
        if ok:
            self._fails[method] = 0
            self._skip_until.pop(method, None)
            return
        n = self._fails.get(method, 0) + 1
        self._fails[method] = n
        if n >= FAIL_BACKOFF_AFTER:
            self._skip_until[method] = self.clock() + FAIL_BACKOFF_S
            log.warning("TV input by %s failed %d times in a row: HOME flips for %.0f s (probed meanwhile)", method, n,
                        FAIL_BACKOFF_S)

    def _log(self, ev: InputEvidence) -> None:
        if self.log_path is None:
            return
        try:
            if self.log_path.exists() and self.log_path.stat().st_size > INPUT_LOG_MAX:
                lines = self.log_path.read_text().splitlines()[-2000:]
                self.log_path.write_text("\n".join(lines) + "\n")
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a") as f:
                f.write(json.dumps({"at": round(self.clock(), 3), **asdict(ev)}) + "\n")
        except OSError:
            log.exception("couldn't write the TV input log")

    # --- local (samsungtvws) ---------------------------------------------------------

    def _local(self):
        if self._tv is None:
            from samsungtvws import SamsungTVWS
            TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            self._tv = SamsungTVWS(host=self.host, port=8002, token_file=str(TOKEN_FILE),
                                   name="RedZone Everything", timeout=LOCAL_TIMEOUT_S)
        return self._tv

    async def _run_local(self, fn, timeout: float, wait_s: float):
        """Run `fn` (blocking samsungtvws calls) on a worker thread, one at a time (DEV-16): warm() and key()
        share ONE socket, and wait_for cannot cancel a thread. The lock is released when the THREAD ends, not
        when we stop waiting, and a socket drop requested meanwhile happens then too."""
        try:
            await asyncio.wait_for(self._local_lock.acquire(), wait_s)
        except asyncio.TimeoutError:
            raise _SocketBusy("the remote socket is in use") from None
        task = asyncio.ensure_future(asyncio.to_thread(fn))

        def done(t):
            if not t.cancelled():
                t.exception()                      # consume it: the awaiting side may be gone
            self._local_lock.release()
            if self._drop_pending:
                self._drop_pending = False
                self._drop_local()
        task.add_done_callback(done)
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout)
        except asyncio.TimeoutError:
            self._drop_pending = True              # the thread may still be on the socket: close it afterwards
            raise

    async def key(self, key: str, times: int = 1) -> bool:
        self._definite = False
        if self.sound_guard is not None or key.upper() in LOUD_KEYS:
            # An open sound incident stops every key; must_be_silent stops the keys that can make sound.
            if (self.sound_blocked() or key.upper() in LOUD_KEYS) and self._refuse_sound(key):
                return False
        if not self.host:
            self.last_error = "no TV address saved (run scripts/tv_setup.py)"
            self._definite = True
            return False

        def send():
            tv = self._local()
            for i in range(times):
                tv.send_key(key)
                if i + 1 < times:
                    time.sleep(0.15)
        for attempt in range(2):
            busy = False
            try:
                await self._run_local(send, LOCAL_TIMEOUT_S * 2, LOCAL_LOCK_WAIT_S)
                return True
            except Exception as e:
                # Timed out WAITING for the lock (nothing of ours ran) vs timed out RUNNING (may have been sent).
                busy = isinstance(e, _SocketBusy)
                self.last_error = ("TV remote socket busy (a keepalive or key is still using it)" if busy
                                   else f"local key failed: {e or type(e).__name__}")
                if not busy:
                    self._drop_local_if_idle()
                # A kept-open socket the TV closed meanwhile (it slept, Wi-Fi blipped) refuses the
                # write: nothing was delivered, so one fresh connection is safe. A timeout is not.
                if attempt == 0 and _never_sent(e):
                    continue
                self._definite = busy or _local_never_sent(e)
                return False
        return False

    def _drop_local_if_idle(self) -> None:
        if self._local_lock.locked():
            self._drop_pending = True               # a worker thread still holds the socket: close it when it ends
        else:
            self._drop_local()

    def _drop_local(self) -> None:
        tv, self._tv = self._tv, None
        try:
            if tv is not None:
                tv.close()
        except Exception:
            pass

    @property
    def local_capable(self) -> bool:
        """local_ready without the failure backoff: what maintain() may probe."""
        return bool(self.host and self.local_trusted and TOKEN_FILE.exists())

    async def warm(self, probe: bool = False) -> bool:
        """Keepalive: open (or keep open) the remote-control socket so a flip doesn't pay the
        handshake. Never sends a key. probe=True also tries a backed-off path (DEV-07) and clears
        the backoff when the socket opens."""
        if not (self.local_capable if probe else self.local_ready):
            return False
        if self._local_lock.locked():
            return True                             # a key (or another warm) is using the socket right now
        try:
            await self._run_local(lambda: self._local().open(), LOCAL_TIMEOUT_S * 2, 0.1)
            if probe:
                if self._backed_off("local"):
                    log.info("TV remote socket answers again: local keys are back")
                self._note("local", True)
            return True
        except Exception as e:
            self.last_error = f"TV remote socket: {e}"
            self._drop_local_if_idle()
            return False

    async def volume_duck(self, steps: int = 4, hold_s: float = 4.0) -> None:
        """Lower the volume for an announcer callout, then put it back. Nothing in the app calls it. Refused while
        sound_guard.must_be_silent is on or a sound incident is open: putting the volume back is KEY_VOLUP."""
        if self.must_be_silent() or self.sound_blocked():
            self._refuse_sound("volume_duck (KEY_VOLUP)")
            return
        if await self.key("KEY_VOLDOWN", steps):
            await asyncio.sleep(hold_s)
            await self.key("KEY_VOLUP", steps)

    # --- smartthings ---------------------------------------------------------------------

    async def _st(self, method: str, path: str, body: dict | None = None) -> dict | None:
        self._definite = False
        self._st_ok = False
        if self.cloud_disabled:                      # policy N1: no request, no OAuth read
            self.last_error = "SmartThings is disabled by policy (local key + CEC)"
            self._definite = True
            return None
        if method != "GET" and body and self._st_refused(body):
            return None
        r = await self._st_gated(method, path, body)
        self._st_ok = r is not None
        return r

    def _st_refused(self, body: dict) -> bool:
        """A SmartThings command that must not go out: one that can make sound while must_be_silent is on, or,
        while a sound incident is open, anything but power off, volume 0 and mute (no input change, no power on)."""
        cmds = body.get("commands") if isinstance(body, dict) else None
        if not isinstance(cmds, list):
            return False
        blocked = self.sound_blocked()
        silent_on = None
        for c in cmds:
            if not isinstance(c, dict):
                continue
            cap, cmd, args = str(c.get("capability", "")), str(c.get("command", "")), c.get("arguments")
            loud = loud_command(cap, cmd, args if isinstance(args, list) else None)
            if blocked:
                calming = (cap == "switch" and cmd == "off") or (cap.split(".")[-1].lower() in SILENT_ST_COMMANDS
                                                                 and not loud)
                if not calming:
                    return bool(self._refuse_sound(f"{cap}.{cmd}"))
            if loud:
                if silent_on is None:
                    silent_on = self.must_be_silent()
                if silent_on:
                    return bool(self._refuse_sound(loud))
        return False

    async def _st_gated(self, method: str, path: str, body: dict | None = None) -> dict | None:
        hard = self._hardened()
        if hard:
            self._auth_rearm()
            blocked = self.cloud_blocked()
            if blocked:
                self.last_error = blocked
                self._definite = True
                return None
            if not self.st_breaker.allow():      # half-open: someone else is already probing
                self.last_error = "SmartThings unreachable: probing"
                self._definite = True
                return None
        try:
            return await self._st_call(method, path, body, hard)
        finally:
            if hard:
                self.st_breaker.release()        # a cancelled probe must not hold the slot

    async def _st_call(self, method: str, path: str, body: dict | None, hard: bool) -> dict | None:
        token = await self.auth.token() if self.auth else _env("SMARTTHINGS_TOKEN")
        if not token:
            self.last_error = (self.auth.last_error if self.auth and self.auth.last_error
                               else "SmartThings not set up (run scripts/tv_setup.py --smartthings)")
            self._definite = True
            if hard and self.auth is not None:
                if self.auth.last_kind == "down":
                    self._cloud_down("sign-in server unreachable")
                elif self.auth.configured:
                    self._auth_down(self.auth.last_error or "no access token")
            return None
        renewed = retried = False
        while True:
            try:
                async with httpx.AsyncClient(timeout=8, transport=self.transport) as c:
                    r = await c.request(method, ST_API + path, json=body,
                                        headers={"Authorization": f"Bearer {token}"})
            except httpx.HTTPError as e:
                self.last_error = f"SmartThings unreachable: {e}"
                self._definite = isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout))   # never connected
                if hard:
                    # Never connected: nothing was sent, so even a POST may go again once.
                    if not retried and isinstance(e, httpx.ConnectError):
                        retried = True
                        await asyncio.sleep(ST_RETRY_GAP_S)
                        continue
                    self._cloud_down(f"unreachable ({type(e).__name__})")
                return None
            if r.status_code == 401 and not renewed and self.auth and self.auth.oauth:
                # The access token went stale early (revoked, clock skew): renew once and retry.
                renewed = True
                token = await self.auth.token(force=True)
                if token:
                    continue
            if 400 <= r.status_code < 500:
                self._definite = True                 # refused before anything was applied (401/403/429...)
            if r.status_code == 401:
                self.last_error = ("SmartThings sign-in expired: run scripts/tv_setup.py --smartthings"
                                   if not (self.auth and self.auth.oauth) else
                                   (self.auth.last_error or "SmartThings refused the renewed sign-in"))
                if hard:
                    if self.auth is not None and self.auth.last_kind == "down":
                        self._cloud_down("sign-in server unreachable")
                    else:
                        self._auth_down(self.last_error)
                return None
            if hard and r.status_code == 429:
                wait = _retry_after(r)
                self.last_error = f"SmartThings HTTP 429 (rate limited, retry after {wait:.0f}s)"
                self._rate_limited(wait)
                return None
            if hard and r.status_code >= 500:
                self.last_error = f"SmartThings HTTP {r.status_code}"
                if method == "GET" and not retried:
                    retried = True                # a read is safe to repeat
                    await asyncio.sleep(ST_RETRY_GAP_S)
                    continue
                self._cloud_down(f"HTTP {r.status_code}")
                return None
            if hard:
                self._cloud_ok()                  # any real answer, even a 4xx: the cloud is up
            if r.status_code >= 300:
                self.last_error = f"SmartThings HTTP {r.status_code}"
                return None
            try:
                return r.json() if r.content else {}
            except ValueError:
                return {}

    async def st_discover(self) -> bool:
        """Read capabilities for the explicitly selected TV; never select the first cloud TV."""
        if not self.st_device:
            self.last_error = "no SmartThings TV selected; save this TV's smartthings_device_id first"
            return False
        devs = await self._st("GET", "/devices")
        for d in (devs or {}).get("items", []):
            if d.get("deviceId") != self.st_device:
                continue
            caps = {c["id"] for comp in d.get("components", []) for c in comp.get("capabilities", [])}
            cap = "samsungvd.mediaInputSource" if "samsungvd.mediaInputSource" in caps else \
                "mediaInputSource" if "mediaInputSource" in caps else None
            if not cap:
                continue
            self.st_capability = cap
            st = await self._st("GET", f"/devices/{d['deviceId']}/components/main/capabilities/{cap}/status")
            if cap == "samsungvd.mediaInputSource":
                self.st_inputs = [x.get("id") for x in (st or {}).get("supportedInputSourcesMap", {}).get("value") or []]
            else:
                self.st_inputs = list((st or {}).get("supportedInputSources", {}).get("value") or [])
            return True
        if not self.last_error:
            self.last_error = "selected SmartThings TV is unavailable or has no input-source capability"
        return False

    async def st_input(self, source: str) -> bool:
        self._definite = False
        if (not self.st_device or not self.st_capability) and not await self.st_discover():
            self._definite = True            # nothing was sent
            return False
        if self.st_inputs and source not in self.st_inputs:
            self.last_error = f"TV doesn't list input {source}; it lists {self.st_inputs}"
            self._definite = True
            return False
        r = await self._st("POST", f"/devices/{self.st_device}/commands", {"commands": [{
            "component": "main", "capability": self.st_capability, "command": "setInputSource",
            "arguments": [source]}]})
        return r is not None   # on None, self._definite says whether the command certainly never went out

    async def input_status(self) -> str | None:
        """Read the selected TV's input. Missing or unreadable evidence stays unknown."""
        if (not self.st_device or not self.st_capability) and not await self.st_discover():
            return None
        status = await self._st("GET", f"/devices/{self.st_device}/components/main/capabilities/"
                                f"{self.st_capability}/status")
        source = (status or {}).get("inputSource")
        value = source.get("value") if isinstance(source, dict) else None
        return re.sub(r"\s+", "", value).upper() if isinstance(value, str) else None

    async def maintain(self) -> None:
        """Keepalive: keep the SmartThings sign-in fresh (only while the cloud is enabled) and the local
        socket open. A backed-off local path is probed here, so it comes back without a flip paying for it
        (DEV-07). With hardening.smartthings, a breaker past its wait is probed here too. Bounded by
        MAINTAIN_TIMEOUT_S (DEV-10); the controller should use maintain_soon()."""
        try:
            await asyncio.wait_for(self._maintain(), MAINTAIN_TIMEOUT_S)
        except asyncio.TimeoutError:
            log.warning("TV keepalive took over %.0f s; cut", MAINTAIN_TIMEOUT_S)
        except Exception:
            log.exception("TV keepalive failed")

    def maintain_soon(self) -> None:
        """Start maintain() in the background and return at once (DEV-10): a TV in standby or off must not hold
        the stick reconnects, CEC sync and watchdogs that follow in the keepalive. One at a time."""
        if self.sound_guard is not None:
            self.sound_guard.ensure_running()       # the periodic sound check (sound_guard.enabled only)
        t = self._maintain_task
        if t is not None and not t.done():
            return
        self._maintain_task = asyncio.get_running_loop().create_task(self.maintain())

    async def _maintain(self) -> None:
        if not self.cloud_disabled:
            if self._hardened():
                self._auth_rearm()
            if self.auth and not (self._hardened() and self.cloud_blocked()):
                # A refused refresh here doesn't block the cloud yet: the access token may have
                # hours left. Health warns (tv_auth) so the sign-in can be redone before it runs out.
                await self.auth.maintain()
            if self._hardened() and self.st_device and (
                    self.st_breaker.state == "half-open"
                    or (self.st_fault == "auth" and not self.cloud_blocked())):
                await self.input_status()   # the probe: closes the breaker / clears the fault if it answers
        await self.warm(probe=True)

    async def _verify_input(self, evidence: InputEvidence, reads: int | None = None) -> bool:
        """The TV's own readback (SmartThings) first; else the sticks' CEC view, when the
        controller offers one. Never a sent command alone."""
        t0 = time.monotonic()
        use_cloud = bool(self.st_device) and not self.cloud_disabled
        if use_cloud:
            n = reads if reads is not None else (VERIFY_READS if self.has_readback else 3)
            for attempt in range(n):
                remaining = VERIFY_DEADLINE_S - (time.monotonic() - t0)
                if remaining <= 0:
                    break                         # DEV-03: the readback never holds a flip past its deadline
                try:
                    evidence.observed = await asyncio.wait_for(self.input_status(), remaining)
                except asyncio.TimeoutError:
                    evidence.observed = None
                    break
                if evidence.observed == evidence.requested:
                    evidence.verified = True
                    evidence.how = "smartthings readback"
                    evidence.detail = "selected TV reports the requested input"
                    evidence.verify_s = time.monotonic() - t0
                    return True
                if self.cloud_blocked() or not self._st_ok:
                    break                         # the cloud went away or the call failed: don't spin on it
                if attempt < n - 1:
                    await asyncio.sleep(VERIFY_GAP_S if self.has_readback else 0.25)
        if self.corroborate is not None and evidence.accepted:
            try:
                seen = await self.corroborate(evidence.requested)
            except Exception:
                log.exception("CEC corroboration failed")
                seen = None
            if seen:
                evidence.verified = True
                evidence.how = "cec"
                evidence.detail = "the stick on that input says it's on screen (CEC)"
                evidence.verify_s = time.monotonic() - t0
                return True
        evidence.detail = ("command sent; active input has no readback" if not use_cloud
                           else "requested input was not confirmed by the selected TV")
        evidence.verify_s = time.monotonic() - t0
        return False

    # --- the one call the controller uses -------------------------------------------------

    async def set_input(self, hdmi: str, methods: list[str] | None = None) -> bool:
        """Return True only when the selected TV reports the requested input, or a local key
        proven on this TV at setup was delivered (evidence.trusted).

        A sent key is not confirmation. An ambiguous delivery never triggers another method,
        since the original command may already have changed the visible input. `methods` limits
        the paths (the controller passes paths(); None = local first, then the cloud when it is enabled).

        With a sound guard (sound_guard.enabled): checked before the first switch of the session (refused unless
        silence is read back), refused outright while an incident is open, and checked again after every switch.
        """
        g = self.sound_guard
        if g is None:
            return await self._set_input(hdmi, methods)
        g.ensure_running()
        if not await g.before_switch():
            self.last_input = InputEvidence(hdmi.upper().replace(" ", ""),
                                            detail=f"sound guard: {g.blocked_why() or 'silence not read back'}")
            self.last_error = self.last_input.detail
            self._definite = True
            return False
        try:
            return await self._set_input(hdmi, methods)
        finally:
            await g.after_switch()

    async def _set_input(self, hdmi: str, methods: list[str] | None = None) -> bool:
        n = hdmi.upper().replace(" ", "")
        self.last_input = evidence = InputEvidence(n)
        t0 = time.monotonic()
        if not re.fullmatch(r"HDMI[1-9][0-9]*", n):
            self.last_error = evidence.detail = "invalid HDMI input"
            return False
        order = methods if methods is not None else (
            [m for m in ("local", "smartthings") if m == "local" or not self.cloud_disabled])
        try:
            for m in order:
                if m == "local" and self.host:
                    evidence.source = "local"
                    c0 = time.monotonic()
                    sent = await self.key(f"KEY_{n}")
                    definite = not sent and self._definite
                    evidence.accepted = True if sent else (False if definite else None)
                    evidence.cmd_s = time.monotonic() - c0
                    if definite:
                        # DEV-02: the key certainly never reached the TV (refused connection, closed socket,
                        # socket busy), so the next method is safe.
                        self._note("local", False)
                        self.last_error = evidence.detail = self.last_error or "local key not delivered"
                        continue
                    ok = await self._verify_input(evidence)
                    if not ok and evidence.accepted and self.local_trusted:
                        # No readback on this TV (or it lagged): a key setup watched switch this TV
                        # is trusted once delivered, unless the TV itself reported another input.
                        if evidence.observed is None:
                            evidence.trusted = True
                            evidence.how = "setup-proven key"
                            evidence.detail = "key delivered; proven on this TV by tv_setup.py (no readback)"
                            ok = True
                    self._note("local", ok)
                    if ok:
                        self.method = self.method or "local"
                    else:
                        self.last_error = evidence.detail
                    return ok   # delivered or ambiguous: never a second method on top
                if m == "smartthings":
                    evidence.source = "smartthings"
                    if self.cloud_disabled:
                        evidence.detail = self.last_error = "SmartThings is disabled by policy (local key + CEC)"
                        continue
                    if not self.st_device:
                        self.last_error = evidence.detail = "no SmartThings TV explicitly selected"
                        continue
                    c0 = time.monotonic()
                    sent = await self.st_input(n)
                    definite = not sent and self._definite
                    evidence.accepted = True if sent else (False if definite else None)
                    evidence.cmd_s = time.monotonic() - c0
                    if definite:
                        # 401/403/429, breaker open, no token, refused connection: nothing was applied.
                        self._note("smartthings", False)
                        evidence.detail = self.last_error
                        continue
                    if await self._verify_input(evidence):
                        self.method = "smartthings"
                        self._note("smartthings", True)
                        return True
                    self._note("smartthings", False)
                    self.last_error = evidence.detail if evidence.accepted else (self.last_error or evidence.detail)
                    return False
            if not evidence.detail:
                evidence.detail = self.last_error = self.last_error or "no TV input path set up"
            return False
        finally:
            evidence.total_s = time.monotonic() - t0
            self._log(evidence)


# --- tv.smartthings: st_fault and _auth_failed are views of one machine (SamsungTV.st_m) -------------------

_FAULT_STATE = {"": "ok", "auth": "auth", "rate_limited": "rate_limited", "cloud_down": "cloud_down"}


def _st_machine(self: SamsungTV):
    m = self.__dict__.get("st_m")
    if m is None:
        m = self.__dict__["st_m"] = SMARTTHINGS.new(clock=lambda: self.clock())
    return m


def _get_st_fault(self: SamsungTV) -> str:
    s = _st_machine(self).state
    return "" if s == "ok" else "auth" if s.startswith("auth") else s


def _set_st_fault(self: SamsungTV, value: str) -> None:
    _st_machine(self).to(_FAULT_STATE[value or ""], why=self.__dict__.get("st_fault_detail", "") or value or "ok")


def _get_auth_failed(self: SamsungTV) -> tuple | None:
    return self.__dict__.get("_auth_at") if _st_machine(self).is_("auth") else None


def _set_auth_failed(self: SamsungTV, value: tuple | None) -> None:
    m = _st_machine(self)
    if value is None:
        if m.is_("auth"):
            m.to("auth_retry", why="auth fault cleared")
        return
    self.__dict__["_auth_at"] = tuple(value)
    m.to("auth", why="sign-in refused")


SamsungTV.st_m = property(_st_machine)
SamsungTV.st_fault = property(_get_st_fault, _set_st_fault)
SamsungTV._auth_failed = property(_get_auth_failed, _set_auth_failed)
