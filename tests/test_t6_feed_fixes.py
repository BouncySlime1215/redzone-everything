"""[T6-4, T6-5] Fixes for the T6 tester's findings on the feed path (lane 5): a naive feed stamp is UTC,
and a dark second feed doesn't flicker a game stale while its freshest feed is fine."""
import asyncio
import time

import httpx
import pytest

from redzone.models import GameState, League, Phase, Stoppage, Team
from redzone.sources import leagues
from redzone.sources.feeds import FeedBook
from redzone.sources.http import Fetcher
from redzone.sources.merge import Merger

ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event=401"
PBP = leagues.NBA_PBP.format(id="0022600123")


class Clock:
    def __init__(self, t=1_000.0):
        self.t = t

    def __call__(self):
        return self.t


def _g(clock_s):
    return GameState(League.NBA, "401", Team("BOS", score=50), Team("NYK", score=48), phase=Phase.LIVE,
                     period=2, clock_s=clock_s, stoppage=Stoppage.NONE)


def test_a_naive_stamp_is_utc_on_an_eastern_mac(monkeypatch):
    if not hasattr(time, "tzset"):
        pytest.skip("needs tzset")
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    try:
        assert leagues._iso_ts("2026-11-06T00:10:00") == 1_793_923_800.0
        assert leagues._iso_ts("2026-11-05T19:10:00-05:00") == 1_793_923_800.0
        assert leagues._iso_ts("") is None and leagues._iso_ts("garbage") is None
    finally:
        monkeypatch.delenv("TZ", raising=False)
        time.tzset()


def _book(dark):
    clk = Clock()

    def handler(req):
        return httpx.Response(503) if dark in str(req.url) else httpx.Response(200, json={"ok": 1})

    f = Fetcher(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), clock=clk)
    m = Merger(clock=clk)
    return clk, f, m, FeedBook(m, f, lambda g: [ESPN, PBP], lambda gid: 3.0, clock=clk)


def test_a_dark_second_feed_does_not_flicker_stale_at_the_poll_boundary():
    async def go():
        clk, f, m, book = _book("amazonaws")
        for i in range(5):
            await f.get_json(ESPN)
            await f.get_json(PBP)
            m.update("espn_summary", _g(600.0 - i * 3))
            clk.t += 3.0
            assert not book.stale("401") and not book.polled_stale("401")
        await f.close()
    asyncio.run(go())


def test_the_freshest_feed_backing_off_still_reads_stale_at_one_cadence():
    async def go():
        clk = Clock()
        state = {"espn": 200}

        def handler(req):
            if "amazonaws" in str(req.url):
                return httpx.Response(503)
            return httpx.Response(state["espn"], json={"ok": 1})

        f = Fetcher(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), clock=clk)
        m = Merger(clock=clk)
        book = FeedBook(m, f, lambda g: [ESPN, PBP], lambda gid: 3.0, clock=clk)
        await f.get_json(ESPN)
        await f.get_json(PBP)
        m.update("espn_summary", _g(600.0))
        state["espn"] = 503
        clk.t += 1.0
        await f.get_json(ESPN)                 # the freshest feed now backs off too: nothing new can come
        clk.t += 2.0
        assert book.stale("401")
        await f.close()
    asyncio.run(go())
