"""Polite HTTP for the history fetchers: one request at a time, at most one per second overall, loud on any failure.

Why: B-01 runs for hours unattended. It must never hammer a host (WORKPLAN: <= 1 request/second overall), never
turn a failed fetch into an empty result (the venv's urllib has no CA bundle: a past builder shipped a silent-empty
fetch exactly there, so every failure here raises), and never retry around a block (a 403 stops the run).

    client = PoliteClient()                       # honest User-Agent, certifi CA bundle when importable
    reply = client.get_json(url, params)          # Reply(status=200, data=<dict>) or raises FetchError / Blocked
    client.download(url, dest)                    # whole file, streamed, sha256, atomic rename

Tests pass `transport=httpx.MockTransport(...)`, a fake `sleep` and a fake `clock`: no network, no real waiting.
"""
from __future__ import annotations

import collections
import datetime as dt
import hashlib
import os
import random
import re
import ssl
import time
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx

UA = "redzone-personal-research/1.0 (personal, non-commercial; polite: at most 1 request/second)"
MIN_INTERVAL = 1.0           # the floor: one request per second overall, whatever the caller asks for
RETRY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
MAX_ATTEMPTS = 6             # per request: 1 try + 5 retries
BACKOFF_BASE_S = 2.0
BACKOFF_CAP_S = 120.0
RETRY_AFTER_CAP_S = 600.0


class FetchError(RuntimeError):
    """A request failed for good (retries used up, bad status, not JSON, TLS). Never swallowed into an empty result."""


class StatusError(FetchError):
    """A status we do not retry (400, 401, 410, ...). `status` says which."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class NotFound(FetchError):
    """A download answered 404 (a season file that does not exist). The caller records it as not_found."""


class Blocked(FetchError):
    """The host refused us (HTTP 403). The run stops; there is no User-Agent change or workaround here."""


@dataclass
class Reply:
    status: int
    url: str
    headers: dict[str, str]
    data: Any = None          # parsed JSON object for a 200 from get_json
    nbytes: int = 0


@dataclass
class Stats:
    requests: int = 0
    retries: int = 0
    bytes: int = 0
    by_status: collections.Counter = field(default_factory=collections.Counter)


def make_ssl_context() -> tuple[ssl.SSLContext, str]:
    """(context, source). certifi's CA bundle when importable, else the platform default. A TLS failure later raises loudly."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where()), "certifi"
    except ImportError:
        return ssl.create_default_context(), "system-default"


def _retry_after(headers: httpx.Headers) -> float | None:
    v = headers.get("retry-after")
    if not v:
        return None
    v = v.strip()
    if re.fullmatch(r"\d+(\.\d+)?", v):
        return float(v)
    try:
        when = parsedate_to_datetime(v)
        return max(0.0, (when - dt.datetime.now(when.tzinfo or dt.timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        return None


class PoliteClient:
    def __init__(self, min_interval: float = MIN_INTERVAL, user_agent: str = UA, *, timeout: float = 60.0,
                 transport: httpx.BaseTransport | None = None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, rng: random.Random | None = None):
        if min_interval < MIN_INTERVAL:
            raise ValueError(f"min_interval {min_interval} is below the floor of {MIN_INTERVAL} s (1 request/second overall)")
        self.min_interval, self.user_agent = float(min_interval), user_agent
        self._sleep, self._clock, self._rng = sleep, clock, rng or random.Random()
        self._last = -1e9
        self.stats = Stats()
        self.tls = "mock"
        kwargs: dict[str, Any] = {"timeout": timeout, "headers": {"User-Agent": user_agent, "Accept": "application/json, */*;q=0.5"},
                                  "follow_redirects": True}
        if transport is not None:
            kwargs["transport"] = transport
        else:
            ctx, self.tls = make_ssl_context()
            kwargs["verify"] = ctx
        self._http = httpx.Client(**kwargs)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "PoliteClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- pacing and retry --------------------------------------------------

    def _pace(self) -> None:
        gap = self.min_interval - (self._clock() - self._last)
        if gap > 0:
            self._sleep(gap)
        self._last = self._clock()

    def _backoff(self, attempt: int, headers: httpx.Headers | None = None) -> float:
        wait = min(BACKOFF_CAP_S, BACKOFF_BASE_S * (2 ** attempt)) * (1 + 0.25 * self._rng.random())
        ra = _retry_after(headers) if headers is not None else None
        return min(RETRY_AFTER_CAP_S, max(wait, ra)) if ra is not None else wait

    def _send(self, url: str, params: dict | None, *, allow_404: bool) -> httpx.Response:
        """One logical request with pacing, retries and backoff. Returns the final response (200, or 404 when allowed)."""
        host = urlsplit(url).netloc
        last = "no attempt"
        for attempt in range(MAX_ATTEMPTS):
            self._pace()
            self.stats.requests += 1
            try:
                resp = self._http.get(url, params=params)
            except httpx.ConnectError as e:
                text = repr(e)
                if "CERTIFICATE" in text.upper() or "SSL" in text.upper():
                    raise FetchError(f"TLS verification failed for {host} (CA bundle: {self.tls}); install certifi: {e}") from e
                last = f"connect error: {e}"
            except httpx.TransportError as e:
                last = f"{type(e).__name__}: {e}"
            else:
                self.stats.bytes += len(resp.content)
                self.stats.by_status[resp.status_code] += 1
                if resp.status_code == 200 or (resp.status_code == 404 and allow_404):
                    return resp
                if resp.status_code == 403:
                    raise Blocked(f"HTTP 403 from {host}: the host refused this client. Stopping; not retrying or changing the User-Agent. {resp.text[:80]!r}")
                if resp.status_code not in RETRY_STATUS:
                    raise StatusError(resp.status_code, f"HTTP {resp.status_code} from {url} params={params}: {resp.text[:120]!r}")
                last = f"HTTP {resp.status_code}"
                if attempt < MAX_ATTEMPTS - 1:
                    self.stats.retries += 1
                    self._sleep(self._backoff(attempt, resp.headers))
                    continue
            if attempt < MAX_ATTEMPTS - 1:
                self.stats.retries += 1
                self._sleep(self._backoff(attempt))
        raise FetchError(f"{url} params={params}: gave up after {MAX_ATTEMPTS} attempts ({last})")

    # ---- public calls ------------------------------------------------------

    def get_json(self, url: str, params: dict | None = None, *, allow_404: bool = False) -> Reply:
        resp = self._send(url, params, allow_404=allow_404)
        hdrs = {k.lower(): v for k, v in resp.headers.items()}
        if resp.status_code == 404:
            return Reply(404, str(resp.url), hdrs, None, len(resp.content))
        if not resp.content:
            raise FetchError(f"{url} params={params}: HTTP 200 with an empty body")
        try:
            data = resp.json()
        except ValueError as e:
            raise FetchError(f"{url} params={params}: HTTP 200 but not JSON ({hdrs.get('content-type')}): {resp.text[:80]!r}") from e
        if not isinstance(data, dict):
            raise FetchError(f"{url} params={params}: expected a JSON object, got {type(data).__name__}")
        return Reply(200, str(resp.url), hdrs, data, len(resp.content))

    def get_text(self, url: str, *, allow_404: bool = True) -> Reply:
        resp = self._send(url, None, allow_404=allow_404)
        hdrs = {k.lower(): v for k, v in resp.headers.items()}
        return Reply(resp.status_code, str(resp.url), hdrs, resp.text if resp.status_code == 200 else None, len(resp.content))

    def download(self, url: str, dest: Path) -> dict[str, Any]:
        """Download one whole file to `dest` atomically; verify Content-Length; return sha256, bytes and validators.
        Retries re-download from the start (the files are 5 to 60 MB). Raises FetchError on any mismatch."""
        host = urlsplit(url).netloc
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f".{dest.name}.{os.getpid()}.tmp")
        last = "no attempt"
        for attempt in range(MAX_ATTEMPTS):
            self._pace()
            self.stats.requests += 1
            try:
                with self._http.stream("GET", url) as resp:
                    self.stats.by_status[resp.status_code] += 1
                    if resp.status_code == 403:
                        raise Blocked(f"HTTP 403 from {host}: refused. Stopping.")
                    if resp.status_code == 404:
                        raise NotFound(f"HTTP 404 for {url}")
                    if resp.status_code != 200:
                        if resp.status_code not in RETRY_STATUS:
                            raise FetchError(f"HTTP {resp.status_code} from {url}")
                        last = f"HTTP {resp.status_code}"
                        wait = self._backoff(attempt, resp.headers)
                    else:
                        h, n = hashlib.sha256(), 0
                        with open(tmp, "wb") as f:
                            for chunk in resp.iter_bytes(1 << 20):
                                f.write(chunk)
                                h.update(chunk)
                                n += len(chunk)
                            f.flush()
                            os.fsync(f.fileno())
                        want = resp.headers.get("content-length")
                        if resp.headers.get("content-encoding") in (None, "", "identity") and want and int(want) != n:
                            raise FetchError(f"{url}: got {n} bytes, Content-Length said {want}")
                        if n == 0:
                            raise FetchError(f"{url}: empty download")
                        os.replace(tmp, dest)
                        self.stats.bytes += n
                        return {"sha256": h.hexdigest(), "bytes": n, "etag": resp.headers.get("etag"),
                                "last_modified": resp.headers.get("last-modified")}
            except httpx.TransportError as e:
                if "CERTIFICATE" in repr(e).upper():
                    raise FetchError(f"TLS verification failed for {host} (CA bundle: {self.tls}); install certifi") from e
                last = f"{type(e).__name__}: {e}"
                wait = self._backoff(attempt)
            finally:
                tmp.unlink(missing_ok=True)
            if attempt < MAX_ATTEMPTS - 1:
                self.stats.retries += 1
                self._sleep(wait)
        raise FetchError(f"{url}: gave up after {MAX_ATTEMPTS} attempts ({last})")


# ---- robots.txt ------------------------------------------------------------

def parse_robots(text: str, path: str) -> tuple[bool, bool]:
    """(allowed, ai_train_ok) for the `User-agent: *` group and this path; longest matching rule wins, * and $ wildcards."""
    rules: list[tuple[bool, str]] = []
    signal, star, in_rules = "", False, False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        k, v = (x.strip() for x in line.split(":", 1))
        k = k.lower()
        if k == "user-agent":
            if in_rules:
                star, in_rules = False, False
            star = star or v == "*"
        else:
            in_rules = True
            if not star:
                continue
            if k in ("allow", "disallow") and v:
                rules.append((k == "allow", v))
            elif k == "content-signal":
                signal += "," + v.replace(" ", "").lower()
    best, allowed = -1, True
    for ok, pat in rules:
        rx = re.escape(pat).replace(r"\*", ".*").replace(r"\$", "$")
        if re.match(rx, path) and len(pat) > best:
            best, allowed = len(pat), ok
    return allowed, "ai-train=no" not in signal


def check_robots(client: PoliteClient, base_url: str, path: str) -> dict[str, Any]:
    """Read a host's robots.txt once and judge `path`. 200 with a disallow or ai-train=no -> verdict 'deny' (the caller refuses).
    404/410 -> 'allow' (RFC 9309: no file, no rules). Other 4xx, notably a 403 CDN deny page -> 'unavailable': the note is
    recorded in the manifest and the source registry decides (RFC 9309 reads a 4xx as no rules). A robots.txt that stays
    unreachable (5xx, network) -> 'deny', as RFC 9309 says."""
    host = f"{urlsplit(base_url).scheme}://{urlsplit(base_url).netloc}"
    try:
        r = client.get_text(host + "/robots.txt", allow_404=True)
    except Blocked:
        return {"host": host, "status": 403, "verdict": "unavailable", "note": "robots.txt answered 403 (a CDN deny page, not a rules file)"}
    except StatusError as e:    # 401, 410 and other 4xx: no usable file, so no rules
        return {"host": host, "status": e.status, "verdict": "unavailable", "note": f"robots.txt answered {e.status}"}
    except FetchError as e:     # 5xx or a dropped connection after every retry: RFC 9309 says treat the host as fully disallowed
        return {"host": host, "status": None, "verdict": "deny", "note": f"robots.txt unreachable after retries, treated as disallow: {e}"}
    if r.status == 200:
        allowed, train_ok = parse_robots(r.data or "", path)
        return {"host": host, "status": 200, "verdict": "allow" if allowed and train_ok else "deny",
                "sha256": hashlib.sha256((r.data or "").encode()).hexdigest()[:16], "ai_train_ok": train_ok}
    if r.status in (404, 410):
        return {"host": host, "status": r.status, "verdict": "allow", "note": "no robots.txt: no rules (RFC 9309)"}
    return {"host": host, "status": r.status, "verdict": "unavailable", "note": f"robots.txt answered {r.status}"}

