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


async def test_espn_backoff_is_capped_at_a_minute():
    clk = Clock()

    def handler(req):
        return httpx.Response(503)

    f = _fetcher(handler, clk)
    for _ in range(12):
        await f.get_json(URL)
        clk.t = f._backoff["site.api.espn.com"].until
    assert f._backoff["site.api.espn.com"].delay == LIVE_MAX_BACKOFF_S
    await f.close()


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
