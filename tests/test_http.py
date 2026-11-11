import pytest
import asyncio

import httpx

from redzone.sources.http import LIVE_MAX_BACKOFF_S, Fetcher

URL = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard"


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _fetcher(handler, clk):
    return Fetcher(httpx.AsyncClient(transport=httpx.MockTransport(handler)), clock=clk)


async def test_concurrent_failures_back_off_once():
    clk = Clock()

    def handler(req):
        raise httpx.ConnectTimeout("blip", request=req)

    f = _fetcher(handler, clk)
    await asyncio.gather(*(f.get_json(f"{URL}?g={i}") for i in range(6)))
    b = f._backoff["site.api.espn.com"]
    assert b.delay == 5.0 and b.until == clk.t + 5.0
    await f.close()


async def test_espn_outage_backoff_is_capped_at_ten_seconds():
    clk = Clock()

    def handler(req):
        return httpx.Response(503)

    f = _fetcher(handler, clk)
    for _ in range(12):
        await f.get_json(URL)
        clk.t = f._backoff["site.api.espn.com"].until
    assert f._backoff["site.api.espn.com"].delay == LIVE_MAX_BACKOFF_S
    await f.close()


@pytest.mark.playbook("NET-01")
async def test_cached_copy_during_outage_is_not_fresh():
    clk = Clock()
    up = {"ok": True}

    def handler(req):
        if up["ok"]:
            return httpx.Response(200, json={"events": []})
        raise httpx.ConnectError("down", request=req)

    f = _fetcher(handler, clk)
    assert await f.get_json(URL) == {"events": []}
    assert f.is_fresh(URL, 10)
    up["ok"] = False
    clk.t += 30
    assert await f.get_json(URL) == {"events": []}       # callers still get the last good copy
    assert not f.is_fresh(URL, 10) and f.fetched_at(URL) == 1000.0
    await f.close()


async def test_espn_saying_too_many_requests_still_backs_off_a_minute():
    from redzone.sources.http import LIVE_LIMITED_BACKOFF_S
    clk = Clock()
    f = _fetcher(lambda req: httpx.Response(429), clk)
    for _ in range(12):
        await f.get_json(URL)
        clk.t = f._backoff["site.api.espn.com"].until
    assert f._backoff["site.api.espn.com"].delay == LIVE_LIMITED_BACKOFF_S == 60.0
    await f.close()


@pytest.mark.playbook("NET-03")
async def test_one_host_failing_dns_backs_off_alone_and_the_others_keep_answering():
    """NET-03: one source's name stops resolving. Only that host backs off; every other source's host
    keeps being fetched fresh (the merger then fails over: FEED-01)."""
    clk = Clock()
    nhl = "https://api-web.nhle.com/v1/score/now"

    def handler(req):
        if req.url.host == "api-web.nhle.com":
            raise httpx.ConnectError("[Errno 8] nodename nor servname provided, or not known", request=req)
        return httpx.Response(200, json={"events": []})

    f = _fetcher(handler, clk)
    assert await f.get_json(nhl) is None
    assert f.backing_off(nhl) and not f.backing_off(URL)
    for _ in range(3):
        clk.t += 1.0
        assert await f.get_json(URL) == {"events": []} and f.is_fresh(URL, 2.0)
    assert "site.api.espn.com" not in f._backoff or f._backoff["site.api.espn.com"].delay == 0
    await f.close()
