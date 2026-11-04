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

Every call has a timeout. False means unconfirmed, not necessarily unchanged: an ambiguous
delivery never triggers a second TV method (the first one may already have switched). A
successful network write alone never confirms which input the viewer sees.

Each attempt is appended to data/tv_input.jsonl with its command and verify latency.

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
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

import httpx

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
LOCAL_TIMEOUT_S = 6.0
FAIL_BACKOFF_AFTER = 2       # this many failures in a row on one method...
FAIL_BACKOFF_S = 300.0       # ...and it isn't tried for this long (HOME takes over meanwhile)
INPUT_LOG_MAX = 2 * 1024 ** 2
ST_BREAK_AFTER = 2           # cloud failures in a row before the SmartThings breaker opens
ST_BREAK_S = 30.0            # first open; doubles per re-open...
ST_BREAK_MAX_S = 120.0       # ...up to this (a probe is one background read, so keep it short)
RETRY_AFTER_MAX_S = 300.0    # a 429's Retry-After is honoured up to this
RETRY_AFTER_DEFAULT_S = 60.0
AUTH_RETRY_S = 3600.0        # an auth failure is re-tried after this even if the OAuth file never changes
ST_RETRY_GAP_S = 0.3         # before the one retry of an idempotent GET


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

    def load(self) -> dict:
        if self._d is None:
            try:
                self._d = json.loads(self.path.read_text())
            except (OSError, ValueError):
                self._d = {}
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
    st_fault: str = ""                   # "" | "auth" | "rate_limited" | "cloud_down"
    st_fault_detail: str = ""
    st_breaker: Breaker | None = None
    _tv: object = None
    _fails: dict = field(default_factory=dict)      # method -> consecutive failures
    _skip_until: dict = field(default_factory=dict)  # method -> not before
    _auth_failed: tuple | None = None                # (at, oauth file stamp) of the last auth failure

    def __post_init__(self):
        if self.auth is None:
            self.auth = SmartThingsAuth(transport=self.transport)
        if self.st_breaker is None:
            self.st_breaker = Breaker("smartthings", threshold=ST_BREAK_AFTER, backoff_s=ST_BREAK_S,
                                      max_backoff_s=ST_BREAK_MAX_S, clock=lambda: self.clock())

    @classmethod
    def from_devices(cls, path: Path | None = None) -> "SamsungTV":
        f = path or DATA / "devices.json"
        try:
            d = json.loads(f.read_text()) if f.exists() else {}
        except ValueError:
            d = {}
        tv = d.get("tv") or {}
        return cls(host=tv.get("ip"), method=tv.get("input_method"),
                   st_device=tv.get("smartthings_device_id") or tv.get("st_device"),
                   local_trusted=bool(tv.get("local_input_verified")) and bool(tv.get("network_verified")),
                   mac=tv.get("mac") or tv.get("wifi_mac"), log_path=Path(INPUT_LOG))

    # --- which paths can switch right now ------------------------------------------------

    def _backed_off(self, method: str) -> bool:
        return self.clock() < self._skip_until.get(method, 0.0)

    @property
    def local_ready(self) -> bool:
        """A local key we can trust to switch: proven at setup, paired (token saved), not backing off."""
        return bool(self.host and self.local_trusted and TOKEN_FILE.exists()) and not self._backed_off("local")

    @property
    def cloud_ready(self) -> bool:
        return (bool(self.st_device and self.auth and self.auth.configured) and not self._backed_off("smartthings")
                and not self.cloud_blocked())

    # --- SmartThings failure handling (hardening.smartthings) ---------------------------------

    def _hardened(self) -> bool:
        try:
            return bool(self.hardening())
        except Exception:
            return False

    def _auth_blocked(self) -> bool:
        if self._auth_failed is None:
            return False
        at, stamp = self._auth_failed
        if self.clock() - at >= AUTH_RETRY_S or (self.auth and self.auth.stamp() != stamp):
            self._auth_failed = None        # tv_setup.py ran again (or an hour passed): try once more
            if self.auth is not None:
                self.auth._d = None         # re-read the new sign-in, not the cached refused one
            return False
        return True

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
        self._auth_failed = None              # the sign-in worked: any auth fault is over too
        self.st_fault = self.st_fault_detail = ""

    def _cloud_down(self, detail: str) -> None:
        was = self.st_breaker.state
        self.st_breaker.hang()
        self.st_fault, self.st_fault_detail = "cloud_down", detail
        if was != "open" and self.st_breaker.state == "open":
            log.warning("SmartThings %s: HOME/CEC flips for %.0fs", detail, self.st_breaker.retry_in())

    def _rate_limited(self, wait_s: float) -> None:
        b = self.st_breaker
        b.hangs = max(b.hangs, b.threshold - 1)
        b.hang()                                       # open now...
        b.until = max(b.until, self.clock() + wait_s)  # ...for at least as long as SmartThings asked
        self.st_fault, self.st_fault_detail = "rate_limited", f"HTTP 429, retry after {wait_s:.0f}s"
        log.warning("SmartThings rate limit: HOME/CEC flips for %.0fs", b.retry_in())

    def _auth_down(self, detail: str) -> None:
        self._auth_failed = (self.clock(), self.auth.stamp() if self.auth else None)
        if self.st_fault != "auth":
            log.error("SmartThings sign-in refused (%s): HOME/CEC flips until tv_setup.py --smartthings", detail)
        self.st_fault, self.st_fault_detail = "auth", detail
        self.st_breaker.release()

    def cloud_view(self) -> dict:
        return {"fault": self.st_fault, "detail": self.st_fault_detail, "hardening": self._hardened(),
                "breaker": self.st_breaker.view(), "blocked": self.cloud_blocked()}

    @property
    def has_readback(self) -> bool:
        """The TV itself can tell us its input (SmartThings)."""
        return bool(self.st_device and self.auth and self.auth.configured)

    def ready(self) -> bool:
        """Is a TV-side input change worth trying before HOME?"""
        return self.local_ready or self.cloud_ready

    def paths(self) -> list[str]:
        out = []
        if self.method == "smartthings" and self.cloud_ready:
            out.append("smartthings")
        if self.local_ready:
            out.append("local")
        if self.cloud_ready and "smartthings" not in out:
            out.append("smartthings")
        return out

    def view(self) -> dict:
        """For the phone's Health card and scripts/rz.py."""
        last = self.last_input
        return {"local": self.local_ready, "smartthings": self.cloud_ready, "readback": self.has_readback,
                "method": self.method, "last_error": self.last_error, "cloud": self.cloud_view(),
                "last": asdict(last) if last else None}

    def _note(self, method: str, ok: bool) -> None:
        if ok:
            self._fails[method] = 0
            self._skip_until.pop(method, None)
            return
        n = self._fails.get(method, 0) + 1
        self._fails[method] = n
        if n >= FAIL_BACKOFF_AFTER:
            self._skip_until[method] = self.clock() + FAIL_BACKOFF_S
            log.warning("TV input by %s failed %d times in a row: HOME flips for %.0f min", method, n,
                        FAIL_BACKOFF_S / 60)

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

    async def key(self, key: str, times: int = 1) -> bool:
        if not self.host:
            self.last_error = "no TV address saved (run scripts/tv_setup.py)"
            return False

        def send():
            tv = self._local()
            for i in range(times):
                tv.send_key(key)
                if i + 1 < times:
                    time.sleep(0.15)
        for attempt in range(2):
            try:
                await asyncio.wait_for(asyncio.to_thread(send), LOCAL_TIMEOUT_S * 2)
                return True
            except Exception as e:
                self.last_error = f"local key failed: {e}"
                self._drop_local()
                # A kept-open socket the TV closed meanwhile (it slept, Wi-Fi blipped) refuses the
                # write: nothing was delivered, so one fresh connection is safe. A timeout is not.
                if attempt == 0 and _never_sent(e):
                    continue
                return False
        return False

    def _drop_local(self) -> None:
        tv, self._tv = self._tv, None
        try:
            if tv is not None:
                tv.close()
        except Exception:
            pass

    async def warm(self) -> bool:
        """Keepalive: open (or keep open) the remote-control socket so a flip doesn't pay the
        handshake. Never sends a key."""
        if not self.local_ready:
            return False
        try:
            await asyncio.wait_for(asyncio.to_thread(lambda: self._local().open()), LOCAL_TIMEOUT_S * 2)
            return True
        except Exception as e:
            self.last_error = f"TV remote socket: {e}"
            self._drop_local()
            return False

    async def volume_duck(self, steps: int = 4, hold_s: float = 4.0) -> None:
        """Lower the volume for an announcer callout, then put it back."""
        if await self.key("KEY_VOLDOWN", steps):
            await asyncio.sleep(hold_s)
            await self.key("KEY_VOLUP", steps)

    # --- smartthings ---------------------------------------------------------------------

    async def _st(self, method: str, path: str, body: dict | None = None) -> dict | None:
        hard = self._hardened()
        if hard:
            blocked = self.cloud_blocked()
            if blocked:
                self.last_error = blocked
                return None
            if not self.st_breaker.allow():      # half-open: someone else is already probing
                self.last_error = "SmartThings unreachable: probing"
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
        if (not self.st_device or not self.st_capability) and not await self.st_discover():
            return False
        if self.st_inputs and source not in self.st_inputs:
            self.last_error = f"TV doesn't list input {source}; it lists {self.st_inputs}"
            return False
        r = await self._st("POST", f"/devices/{self.st_device}/commands", {"commands": [{
            "component": "main", "capability": self.st_capability, "command": "setInputSource",
            "arguments": [source]}]})
        return r is not None

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
        """Keepalive: keep the SmartThings sign-in fresh and the local socket open. With hardening.smartthings,
        a SmartThings breaker past its wait is probed here (one readback), not by a real flip."""
        try:
            if self.auth and not (self._hardened() and self.cloud_blocked()):
                # A refused refresh here doesn't block the cloud yet: the access token may have
                # hours left. Health warns (tv_auth) so the sign-in can be redone before it runs out.
                await self.auth.maintain()
            if self._hardened() and self.st_device and (
                    self.st_breaker.state == "half-open"
                    or (self.st_fault == "auth" and not self.cloud_blocked())):
                await self.input_status()   # the probe: closes the breaker / clears the fault if it answers
            await self.warm()
        except Exception:
            log.exception("TV keepalive failed")

    async def _verify_input(self, evidence: InputEvidence, reads: int | None = None) -> bool:
        """The TV's own readback (SmartThings) first; else the sticks' CEC view, when the
        controller offers one. Never a sent command alone."""
        t0 = time.monotonic()
        if self.st_device:
            n = reads if reads is not None else (VERIFY_READS if self.has_readback else 3)
            for attempt in range(n):
                evidence.observed = await self.input_status()
                if evidence.observed == evidence.requested:
                    evidence.verified = True
                    evidence.how = "smartthings readback"
                    evidence.detail = "selected TV reports the requested input"
                    evidence.verify_s = time.monotonic() - t0
                    return True
                if self.cloud_blocked():
                    break                         # the cloud went away mid-verify: don't spin on it
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
        evidence.detail = ("command sent; active input has no readback" if not self.st_device
                           else "requested input was not confirmed by the selected TV")
        evidence.verify_s = time.monotonic() - t0
        return False

    # --- the one call the controller uses -------------------------------------------------

    async def set_input(self, hdmi: str, methods: list[str] | None = None) -> bool:
        """Return True only when the selected TV reports the requested input, or a local key
        proven on this TV at setup was delivered (evidence.trusted).

        A sent key is not confirmation. An ambiguous delivery never triggers another method,
        since the original command may already have changed the visible input. `methods` limits
        the paths (the controller passes paths(); None keeps the original any-configured order).
        """
        n = hdmi.upper().replace(" ", "")
        self.last_input = evidence = InputEvidence(n)
        t0 = time.monotonic()
        if not re.fullmatch(r"HDMI[1-9][0-9]*", n):
            self.last_error = evidence.detail = "invalid HDMI input"
            return False
        order = methods if methods is not None else (
            [m for m in ("local", "smartthings") if self.method in (None, m)])
        try:
            for m in order:
                if m == "local" and self.host:
                    evidence.source = "local"
                    c0 = time.monotonic()
                    evidence.accepted = True if await self.key(f"KEY_{n}") else None
                    evidence.cmd_s = time.monotonic() - c0
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
                    if not self.st_device:
                        self.last_error = evidence.detail = "no SmartThings TV explicitly selected"
                        return False
                    c0 = time.monotonic()
                    evidence.accepted = True if await self.st_input(n) else None
                    evidence.cmd_s = time.monotonic() - c0
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
