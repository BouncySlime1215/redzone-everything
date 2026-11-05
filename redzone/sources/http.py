"""Polite HTTP for undocumented feeds: caching, ETags, and backoff on 403/429/5xx.

Guardrails:
- Per-host backoff doubles on each 403/429/5xx/network failure (cap 10 min) and resets on
  success. ESPN, the live feed, is capped at 10 s for an outage (5xx / network: retrying it costs
  one request per 10 s, and a longer cap kept the app blind up to a minute after ESPN came back)
  and 1 min when it says we're asking too often (403/429), longer only if Retry-After says so. Failures while already backing off (a burst
  of concurrent requests dying in one Wi-Fi blip) count once.
- A host in backoff is skipped, not hammered; callers get the last good copy.
- Every response is size-capped so a broken feed can't eat memory.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}
MAX_BYTES = 8_000_000
MAX_BACKOFF_S = 600.0
LIVE_MAX_BACKOFF_S = 10.0          # ESPN is the live feed: an outage (5xx/network) is retried every 10 s at most
LIVE_LIMITED_BACKOFF_S = 60.0      # ...but "too many requests" (403/429) backs off up to a minute
FEED_STALE_S = 5.0                 # data this late past its next due poll = stale (see sources/feeds.py)
# Live feeds get the short outage cap: ESPN, plus the league feeds that can be primary or shadow-raced
# (nba_cdn's S3 mirror, NHL api-web, MLB statsapi). A 10-min backoff on a 1 s feed blinds it for a quarter (T6-3).
LIVE_HOSTS = ("espn.com", "nba-prod-us-east-1-mediaops-stats.s3.amazonaws.com", "nhle.com", "mlb.com")
# S3 answers a missing key (a game whose pbp isn't up yet) with 403/404: that backs off that URL only, so a
# pbp miss can't blind the scoreboard on the same host (T6-2). S3 throttling is 503 SlowDown: still per host.
PATH_SCOPED_HOSTS = (".s3.amazonaws.com",)


@dataclass
class _Cached:
    body: object
    etag: str | None
    fetched_at: float
    last_modified: str | None = None   # [sync 5] for If-Modified-Since on hosts that send no ETag


@dataclass
class _Backoff:
    until: float = 0.0
    delay: float = 0.0


class Fetcher:
    def __init__(self, client: httpx.AsyncClient | None = None, clock=time.time):
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(8.0, connect=4.0), follow_redirects=True,
            headers=BROWSER_HEADERS, http2=False,
        )
        self._clock = clock
        self._cache: dict[str, _Cached] = {}
        self._backoff: dict[str, _Backoff] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def close(self) -> None:
        await self._client.aclose()

    def backing_off(self, url: str) -> bool:
        now = self._clock()
        return any(b is not None and b.until > now
                   for b in (self._backoff.get(urlparse(url).netloc), self._backoff.get(url)))

    def fetched_at(self, url: str) -> float | None:
        """When url last returned a fresh 200/304. get_json hands back the cached copy when the
        host is down or backing off, so callers check this to tell fresh data from stale."""
        c = self._cache.get(url)
        return c.fetched_at if c else None

    def is_fresh(self, url: str, max_age_s: float) -> bool:
        t = self.fetched_at(url)
        return t is not None and self._clock() - t <= max_age_s

    def last_good(self, url: str) -> object | None:
        c = self._cache.get(url)
        return c.body if c else None

    async def get_json(self, url: str, headers: dict | None = None,
                       min_interval: float = 0.0) -> object | None:
        """Return parsed JSON, the cached copy, or None. Never raises on network errors."""
        host = urlparse(url).netloc
        cached = self._cache.get(url)
        if cached and self._clock() - cached.fetched_at < min_interval:
            return cached.body
        if self.backing_off(url):
            return cached.body if cached else None
        lock = self._locks.setdefault(url, asyncio.Lock())
        async with lock:
            # Re-check after waiting: a concurrent caller for this URL may have just fetched it, and
            # N games / loops gathered on one URL must cost one request, not N (T6-1).
            cached = self._cache.get(url)
            if cached and self._clock() - cached.fetched_at < min_interval:
                return cached.body
            if self.backing_off(url):
                return cached.body if cached else None
            hdrs = dict(headers or {})
            if cached and cached.etag:
                hdrs["If-None-Match"] = cached.etag
            if cached and cached.last_modified:
                hdrs["If-Modified-Since"] = cached.last_modified
            try:
                r = await self._client.get(url, headers=hdrs)
            except (httpx.HTTPError, OSError) as e:
                log.warning("fetch failed %s: %s", url, e)
                self._bump(host)
                return cached.body if cached else None
            if r.status_code == 304 and cached:
                cached.fetched_at = self._clock()
                self._reset(host, url)
                return cached.body
            if r.status_code in (403, 404) and host.endswith(PATH_SCOPED_HOSTS):
                log.info("HTTP %s from %s (missing key); backing off that URL only", r.status_code, url)
                self._bump(url, r.headers.get("Retry-After"), limited=True, host=host)
                return cached.body if cached else None
            if r.status_code in (403, 429) or r.status_code >= 500:
                log.warning("HTTP %s from %s; backing off", r.status_code, host)
                self._bump(host, r.headers.get("Retry-After"), limited=r.status_code in (403, 429))
                return cached.body if cached else None
            if r.status_code != 200 or len(r.content) > MAX_BYTES:
                return cached.body if cached else None
            try:
                body = r.json()
            except ValueError:
                return cached.body if cached else None
            self._cache[url] = _Cached(body, r.headers.get("ETag"), self._clock(), r.headers.get("Last-Modified"))
            self._reset(host, url)
            return body

    async def get_bytes(self, url: str, max_bytes: int = 4_000_000) -> tuple[bytes, str] | None:
        """Raw bytes (images). Same backoff rules; no in-memory cache (callers cache on disk)."""
        if self.backing_off(url):
            return None
        try:
            r = await self._client.get(url)
        except (httpx.HTTPError, OSError):
            self._bump(urlparse(url).netloc)
            return None
        if r.status_code in (403, 429) or r.status_code >= 500:
            self._bump(urlparse(url).netloc, r.headers.get("Retry-After"), limited=r.status_code in (403, 429))
            return None
        if r.status_code != 200 or len(r.content) > max_bytes:
            return None
        return r.content, r.headers.get("content-type", "application/octet-stream")

    def _bump(self, key: str, retry_after: str | None = None, limited: bool = False,
              host: str | None = None) -> None:
        """key: the host, or one URL for a path-scoped miss (then `host` is its host, for the cap)."""
        now = self._clock()
        b = self._backoff.setdefault(key, _Backoff())
        host = host or key
        if host.endswith(LIVE_HOSTS):
            cap = LIVE_LIMITED_BACKOFF_S if limited else LIVE_MAX_BACKOFF_S
        else:
            cap = MAX_BACKOFF_S
        ra = float(retry_after) if retry_after and retry_after.isdigit() else None
        if b.until > now:
            # Already backing off: this is another request from the same burst. Count once,
            # but still honour a longer Retry-After.
            if ra is not None and now + min(MAX_BACKOFF_S, ra) > b.until:
                b.until = now + min(MAX_BACKOFF_S, ra)
            return
        b.delay = min(cap, max(5.0, b.delay * 2))
        if ra is not None:
            b.delay = min(MAX_BACKOFF_S, max(b.delay, ra))
        b.until = now + b.delay

    def _reset(self, host: str, url: str | None = None) -> None:
        self._backoff.pop(host, None)
        if url:
            self._backoff.pop(url, None)
