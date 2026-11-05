"""T6 tester (Nov 5): the faster league feeds (nba_cdn liveData, NHL api-web, MLB statsapi) and the polite
Fetcher under them. No network: every request goes to an httpx.MockTransport with a fake clock.

What is checked, per the T6 order:
  1. ETag / If-Modified-Since / 304 handling and backoff, so a 1 s poll never hammers an endpoint.
  2. First-seen timestamps: stamped once, at the poll that first carried the play, never moved by later polls.
  3. Clock alignment: feed wallclocks parse to the right UTC epoch whatever the machine's time zone.
  4. Failover: when the fast feed goes dark the old feed (ESPN) owns the game again, and comes back after.

Tests marked xfail are FINDINGS against the current code (reported to the builders); each says what to fix.
They are non-strict so integration/next stays green; drop the marker when the fix lands.
"""
from __future__ import annotations

import asyncio
import os
import time

import httpx
import pytest

from redzone.models import GameState, League, Phase, Stoppage, Team
from redzone.sources import leagues
from redzone.sources.feeds import FeedBook
from redzone.sources.http import LIVE_MAX_BACKOFF_S, MAX_BACKOFF_S, Fetcher
from redzone.sources.merge import OWNER_ERROR_HOLD_S, STOPPAGE_OWNER_FRESH_S, Merger

NBA_PBP = leagues.NBA_PBP.format(id="0022600123")
NBA_BOARD = leagues.NBA_SCOREBOARD
NHL_PBP = leagues.NHL_PBP.format(id="2026020229")
MLB_LIVE = leagues.MLB_LIVE.format(pk="776655")
ESPN_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event=401"


class Clock:
    def __init__(self, t: float = 1_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


class Server:
    """A fake feed: one ETag that changes when `version` does; 304 to a matching If-None-Match.
    `status` overrides everything (503, 403, 429 ...)."""

    def __init__(self, etag_header=True, lm_header=False):
        self.version = 1
        self.status: int | None = None
        self.retry_after: str | None = None
        self.etag_header, self.lm_header = etag_header, lm_header
        self.log: list[tuple[str, dict]] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.log.append((str(req.url), dict(req.headers)))
        if self.status is not None:
            h = {"Retry-After": self.retry_after} if self.retry_after else {}
            return httpx.Response(self.status, headers=h)
        etag = f'"v{self.version}"'
        lm = f"Thu, 05 Nov 2026 23:00:{self.version:02d} GMT"
        if self.etag_header and req.headers.get("If-None-Match") == etag:
            return httpx.Response(304)
        if not self.etag_header and self.lm_header and req.headers.get("If-Modified-Since") == lm:
            return httpx.Response(304)
        h = {}
        if self.etag_header:
            h["ETag"] = etag
        if self.lm_header:
            h["Last-Modified"] = lm
        return httpx.Response(200, json={"v": self.version}, headers=h)

    def hits(self, url: str | None = None) -> int:
        return sum(1 for u, _ in self.log if url is None or u == url)


def fetcher(server: Server, clock: Clock) -> Fetcher:
    return Fetcher(client=httpx.AsyncClient(transport=httpx.MockTransport(server)), clock=clock)


# ---------------------------------------------------------------- 1. ETag / 304 / backoff

@pytest.mark.parametrize("url", [NBA_PBP, NHL_PBP, MLB_LIVE])
async def test_etag_is_sent_back_and_304_keeps_the_copy_and_counts_as_fresh(url):
    clk, srv = Clock(), Server()
    f = fetcher(srv, clk)
    assert await f.get_json(url) == {"v": 1}
    clk.t += 1.0
    assert await f.get_json(url) == {"v": 1}
    assert srv.log[1][1].get("if-none-match") == '"v1"'
    assert f.fetched_at(url) == clk.t            # a 304 is a fresh answer: FeedBook must not call it stale
    srv.version = 2
    clk.t += 1.0
    assert await f.get_json(url) == {"v": 2}
    await f.close()


async def test_last_modified_only_host_gets_if_modified_since():
    clk, srv = Clock(), Server(etag_header=False, lm_header=True)
    f = fetcher(srv, clk)
    await f.get_json(NBA_PBP)
    clk.t += 1.0
    assert await f.get_json(NBA_PBP) == {"v": 1}
    assert srv.log[1][1].get("if-modified-since") == "Thu, 05 Nov 2026 23:00:01 GMT"
    assert "if-none-match" not in srv.log[1][1]
    await f.close()


async def test_one_second_poll_is_at_most_one_request_per_second():
    """The engine calls get_json(url, min_interval=every - 0.5). At every = 1 s and a loop that wakes
    every 0.25 s (burst wake-ups, several games), the endpoint still sees <= 1 request per second."""
    clk, srv = Clock(), Server()
    f = fetcher(srv, clk)
    for _ in range(240):                  # 60 s of 0.25 s wake-ups
        await f.get_json(NBA_PBP, min_interval=1.0 - 0.5)
        clk.t += 0.25
    assert srv.hits() <= 60 * 2           # min_interval 0.5 s: never more than 2 / s
    # ...and every request after the first is conditional, so an unchanged feed costs a 304, not a body.
    assert all("if-none-match" in h for _, h in srv.log[1:])
    await f.close()


async def test_min_interval_one_second_poll_matches_the_order():
    """Nick's order: 1 s ETag polling. With min_interval = 1 - 0.5 the real rate can reach 2 / s;
    this pins what the engine actually sends at polling.nba_pbp_s = 1 with a 1 s loop."""
    clk, srv = Clock(), Server()
    f = fetcher(srv, clk)
    for _ in range(60):
        await f.get_json(NBA_PBP, min_interval=1.0 - 0.5)
        clk.t += 1.0
    assert srv.hits() == 60
    await f.close()


@pytest.mark.xfail(reason="FINDING T6-1: concurrent get_json on one URL (asyncio.gather over games / sticks) "
                          "waits on the per-URL lock, then fetches again without re-checking min_interval: "
                          "N callers = N requests in the same instant. Re-check the cache inside the lock.",
                   strict=False)
async def test_concurrent_callers_for_one_url_make_one_request():
    clk, srv = Clock(), Server()

    async def slow(req):                  # a real request yields to the loop while in flight
        await asyncio.sleep(0.01)
        return srv(req)

    f = Fetcher(client=httpx.AsyncClient(transport=httpx.MockTransport(slow)), clock=clk)
    await asyncio.gather(*(f.get_json(NBA_BOARD, min_interval=5) for _ in range(4)))
    assert srv.hits() == 1
    await f.close()


async def test_5xx_backs_off_and_sends_nothing_while_backing_off():
    clk, srv = Clock(), Server()
    f = fetcher(srv, clk)
    assert await f.get_json(NHL_PBP) == {"v": 1}
    srv.status = 503
    clk.t += 1
    assert await f.get_json(NHL_PBP) == {"v": 1}   # last good copy, never None mid-game
    n = srv.hits()
    for _ in range(4):                               # 4 s of 1 s polls inside the first 5 s backoff
        clk.t += 1
        await f.get_json(NHL_PBP)
    assert srv.hits() == n                           # nothing sent while backing off
    assert f.backing_off(NHL_PBP)
    clk.t += 1.01
    await f.get_json(NHL_PBP)
    assert srv.hits() == n + 1                       # one probe after the backoff
    await f.close()


async def test_backoff_doubles_and_resets_on_a_304():
    clk, srv = Clock(), Server()
    f = fetcher(srv, clk)
    await f.get_json(NHL_PBP)
    srv.status = 503
    waits = []
    for _ in range(4):
        start = clk.t
        await f.get_json(NHL_PBP)
        while f.backing_off(NHL_PBP):
            clk.t += 0.5
        waits.append(round(clk.t - start))
    assert waits == [5, 10, 20, 40]
    srv.status = None
    await f.get_json(NHL_PBP)                        # 304 (same ETag) clears the backoff
    assert not f.backing_off(NHL_PBP)
    await f.close()


async def test_429_retry_after_is_honoured():
    clk, srv = Clock(), Server()
    f = fetcher(srv, clk)
    srv.status, srv.retry_after = 429, "30"
    await f.get_json(MLB_LIVE)
    clk.t += 29
    assert f.backing_off(MLB_LIVE)
    clk.t += 1.5
    assert not f.backing_off(MLB_LIVE)
    await f.close()


async def test_a_burst_of_failures_counts_once():
    clk, srv = Clock(), Server()
    f = fetcher(srv, clk)
    srv.status = 503
    await f.get_json(NHL_PBP)
    other = leagues.NHL_PBP.format(id="2026020230")    # a second game on the same host, same instant
    await f.get_json(other)
    clk.t += 5.01
    assert not f.backing_off(NHL_PBP)                  # 5 s, not 10: the burst was one failure
    await f.close()


@pytest.mark.xfail(reason="FINDING T6-2: backoff is per HOST. The NBA scoreboard and every game's playbyplay "
                          "share one S3 host, and S3 answers 403 for a key that doesn't exist yet (pbp before "
                          "tip, or the unverified playbyplay_{id} path). One 403 then blinds the NBA scoreboard "
                          "for 60 s+ (doubling to 10 min). Back off per URL for 403/404-style misses, or keep "
                          "the scoreboard out of a pbp miss.", strict=False)
async def test_a_missing_pbp_key_does_not_blind_the_nba_scoreboard():
    def handler(req: httpx.Request) -> httpx.Response:
        if "playbyplay" in str(req.url):
            return httpx.Response(403)
        return httpx.Response(200, json={"scoreboard": {"games": []}})

    clk = Clock()
    f = Fetcher(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), clock=clk)
    await f.get_json(NBA_PBP)
    clk.t += 1
    assert await f.get_json(NBA_BOARD) == {"scoreboard": {"games": []}}
    await f.close()


@pytest.mark.xfail(reason="FINDING T6-3: LIVE_HOSTS is only espn.com, so a 5xx blip on a fast feed made "
                          "primary (nba_cdn S3 mirror, api-web.nhle.com, statsapi.mlb.com) doubles up to "
                          "MAX_BACKOFF_S = 600 s. As a primary feed it should get the live cap (10 s) like ESPN.",
                   strict=False)
@pytest.mark.parametrize("url", [NBA_PBP, NHL_PBP, MLB_LIVE])
async def test_a_fast_primary_feed_is_retried_within_the_live_cap(url):
    clk, srv = Clock(), Server()
    f = fetcher(srv, clk)
    srv.status = 503
    longest = 0.0
    for _ in range(8):
        start = clk.t
        await f.get_json(url)
        while f.backing_off(url):
            clk.t += 0.5
        longest = max(longest, clk.t - start)
    assert longest <= LIVE_MAX_BACKOFF_S + 0.5
    await f.close()


def test_the_never_hammer_caps_exist():
    assert LIVE_MAX_BACKOFF_S >= 5 and MAX_BACKOFF_S >= LIVE_MAX_BACKOFF_S


async def test_oversized_or_broken_body_keeps_the_last_good_copy():
    clk = Clock()
    state = {"mode": "ok"}

    def handler(req):
        if state["mode"] == "big":
            return httpx.Response(200, content=b"x" * 9_000_000)
        if state["mode"] == "html":
            return httpx.Response(200, content=b"<html>blocked</html>")
        return httpx.Response(200, json={"v": 1})

    f = Fetcher(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), clock=clk)
    await f.get_json(NBA_PBP)
    for mode in ("big", "html"):
        state["mode"] = mode
        clk.t += 1
        assert await f.get_json(NBA_PBP) == {"v": 1}
    await f.close()


# ---------------------------------------------------------------- 2. first-seen

def _nba(actions):
    return {"game": {"actions": actions}}


def _act(n, t="2026-11-06T00:10:00.0Z", **kw):
    a = {"actionNumber": n, "actionType": "2pt", "description": f"play {n}", "timeActual": t,
         "scoreHome": "10", "scoreAway": "8", "clock": "PT05M00.00S", "period": 1}
    a.update(kw)
    return a


def test_nba_first_seen_is_the_poll_that_first_carried_it_and_never_moves():
    seen: dict[str, float] = {}
    assert leagues.nba_plays(_nba([_act(1)]), seen, now=100.0) == []        # history
    out = leagues.nba_plays(_nba([_act(1), _act(2)]), seen, now=101.0)
    assert [(p.id, p.seen_at) for p in out] == [("nba2", 101.0)]
    # the same action re-published with an edit (score fixed, text changed): first-seen stays 101
    out = leagues.nba_plays(_nba([_act(1), _act(2, description="edited", scoreHome="11"), _act(3)]), seen, now=104.0)
    assert {p.id: p.seen_at for p in out} == {"nba2": 101.0, "nba3": 104.0}


def test_nba_first_look_with_no_actions_still_primes():
    """Tip-off: the first pbp body is empty. The next action must be stamped, not swallowed as history."""
    seen: dict[str, float] = {}
    assert leagues.nba_plays(_nba([]), seen, now=100.0) == []
    out = leagues.nba_plays(_nba([_act(1)]), seen, now=103.0)
    assert [(p.id, p.seen_at) for p in out] == [("nba1", 103.0)]


def test_nba_actions_without_number_are_skipped_not_stamped():
    seen: dict[str, float] = {}
    leagues.nba_plays(_nba([_act(1)]), seen, now=100.0)
    out = leagues.nba_plays(_nba([_act(1), {"actionType": "period"}, _act(2)]), seen, now=101.0)
    assert [p.id for p in out] == ["nba2"]


def test_nhl_first_seen_is_the_poll_that_first_carried_it_and_never_moves():
    def ev(i, key="shot-on-goal"):
        return {"eventId": i, "typeDescKey": key, "timeRemaining": "12:00", "periodDescriptor": {"number": 1}}
    seen: dict[str, float] = {}
    assert leagues.nhl_plays({"plays": [ev(1)]}, seen, now=50.0) == []
    out = leagues.nhl_plays({"plays": [ev(1), ev(2, "goal")]}, seen, now=53.0)
    assert [(p.id, p.seen_at, p.wallclock) for p in out] == [("nhl2", 53.0, None)]   # no fabricated wallclock
    out = leagues.nhl_plays({"plays": [ev(1), ev(2, "goal"), ev(3, "faceoff")]}, seen, now=56.0)
    assert {p.id: p.seen_at for p in out} == {"nhl2": 53.0, "nhl3": 56.0}


def test_cached_copy_during_an_outage_never_restamps_a_play():
    """The Fetcher hands back the last good body while a host backs off; re-parsing it yields the same
    plays with their original first-seen, never a new one."""
    seen: dict[str, float] = {}
    body = _nba([_act(1)])
    leagues.nba_plays(_nba([]), seen, now=10.0)
    first = leagues.nba_plays(body, seen, now=11.0)
    again = leagues.nba_plays(body, seen, now=40.0)
    assert [p.seen_at for p in first] == [p.seen_at for p in again] == [11.0]


def test_recorder_writes_first_seen_not_write_time(tmp_path):
    import json

    from redzone.recorder import Recorder
    r = Recorder(tmp_path)
    g = GameState(League.NBA, "401", Team("BOS"), Team("NYK"), phase=Phase.LIVE)
    seen: dict[str, float] = {}
    leagues.nba_plays(_nba([]), seen, now=1_000.0)
    wall = leagues._iso_ts("2026-11-06T00:10:00.0Z")
    plays = leagues.nba_plays(_nba([_act(1)]), seen, now=wall + 7.5)
    r.feed_plays(g, plays)
    rows = [json.loads(x) for p in tmp_path.rglob("plays.jsonl") for x in p.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["t"] == pytest.approx(wall + 7.5, abs=0.01)
    assert rows[0]["lag"] == pytest.approx(7.5, abs=0.01) and rows[0]["source"] == "nba_cdn"


# ---------------------------------------------------------------- 3. clock alignment

def test_nba_time_actual_parses_to_utc_epoch():
    assert leagues._iso_ts("2026-11-06T00:10:00Z") == 1_793_923_800.0
    assert leagues._iso_ts("2026-11-06T00:10:00.4Z") == pytest.approx(1_793_923_800.4)
    assert leagues._iso_ts("2026-11-06T00:10:00.40+00:00") == pytest.approx(1_793_923_800.4)
    assert leagues._iso_ts("2026-11-05T19:10:00-05:00") == 1_793_923_800.0
    assert leagues._iso_ts("") is None and leagues._iso_ts("garbage") is None and leagues._iso_ts(None) is None


@pytest.mark.xfail(reason="FINDING T6-4: _iso_ts on a stamp with no offset ('2026-11-06T00:10:00') returns "
                          "LOCAL time, so on the Mac (ET) a naive feed stamp is off by 4-5 h. Feed stamps are "
                          "UTC: treat a naive one as UTC (or reject it).", strict=False)
def test_a_naive_stamp_is_read_as_utc_not_mac_local_time(monkeypatch):
    if not hasattr(time, "tzset"):
        pytest.skip("needs tzset")
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    try:
        assert leagues._iso_ts("2026-11-06T00:10:00") == 1_793_923_800.0
    finally:
        monkeypatch.delenv("TZ", raising=False)
        time.tzset()


def test_game_clock_parses():
    assert leagues._iso_clock("PT11M42.50S") == pytest.approx(702.5)
    assert leagues._iso_clock("PT00M05.00S") == pytest.approx(5.0)
    assert leagues._iso_clock("12:00") is None


def test_lags_are_non_negative_for_a_sane_feed():
    """A play first seen before it happened means a clock problem (Mac clock behind, or a bad stamp)."""
    seen: dict[str, float] = {}
    wall = leagues._iso_ts("2026-11-06T00:10:00.0Z")
    leagues.nba_plays(_nba([]), seen, now=wall - 60)
    p = leagues.nba_plays(_nba([_act(1)]), seen, now=wall + 4.0)[0]
    assert p.seen_at - p.wallclock == pytest.approx(4.0)


def test_epoch_math_is_tz_independent(monkeypatch):
    """The same Z stamp gives the same epoch on a UTC CI box and an ET Mac."""
    if not hasattr(time, "tzset"):
        pytest.skip("needs tzset")
    vals = []
    for tz in ("UTC", "America/New_York", "America/Los_Angeles"):
        monkeypatch.setenv("TZ", tz)
        time.tzset()
        vals.append(leagues._iso_ts("2026-11-06T00:10:00.4Z"))
    monkeypatch.delenv("TZ", raising=False)
    time.tzset()
    assert len(set(vals)) == 1


# ---------------------------------------------------------------- 4. failover to the old feed

def _g(stoppage, clock_s=300.0):
    return GameState(League.NBA, "401", Team("BOS", score=50), Team("NYK", score=48), phase=Phase.LIVE,
                     period=2, clock_s=clock_s, stoppage=stoppage)


def test_fast_feed_owns_while_fresh_then_espn_takes_over_then_hands_back():
    clk = Clock()
    m = Merger(clock=clk, stoppage_owner={League.NBA: ["nba", "espn_summary"]})
    m.update("nba", _g(Stoppage.WHISTLE))
    assert m.owner(League.NBA, "401") == "nba"
    # nba_cdn goes dark; ESPN keeps polling
    for _ in range(int(STOPPAGE_OWNER_FRESH_S) + 2):
        clk.t += 1
        m.update("espn_summary", _g(Stoppage.NONE))
    assert m.owner(League.NBA, "401") == "espn_summary"
    assert m.games["401"].stoppage == Stoppage.NONE       # the old feed decides, not a frozen whistle
    # nba_cdn returns
    clk.t += 1
    m.update("nba", _g(Stoppage.TIMEOUT, clock_s=290.0))
    assert m.owner(League.NBA, "401") == "nba"
    assert [o for _, _, o in m.owner_log] == ["nba", "espn_summary", "nba"]


def test_an_error_on_the_fast_feed_fails_over_at_once():
    clk = Clock()
    m = Merger(clock=clk, stoppage_owner={League.NBA: ["nba", "espn_summary"]})
    m.update("nba", _g(Stoppage.WHISTLE))
    m.update("espn_summary", _g(Stoppage.WHISTLE))
    m.record_error("nba")
    clk.t += 0.5
    assert m.owner(League.NBA, "401") == "espn_summary"
    clk.t += OWNER_ERROR_HOLD_S + 0.5
    m.update("nba", _g(Stoppage.WHISTLE))
    assert m.owner(League.NBA, "401") == "nba"


async def test_a_dark_fast_feed_does_not_make_the_game_stale_while_espn_is_fresh():
    """With nba_cdn in the game's URL list (primary on) and its host 503-ing, ESPN's fresh summary keeps
    the game enterable mid-cadence: the fast feed going dark degrades to the old latency."""
    clk = Clock()

    def handler(req):
        return httpx.Response(503) if "amazonaws" in str(req.url) else httpx.Response(200, json={"ok": 1})

    f = Fetcher(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), clock=clk)
    m = Merger(clock=clk)
    book = FeedBook(m, f, lambda g: [ESPN_SUMMARY, NBA_PBP], lambda gid: 3.0, clock=clk)
    for i in range(20):
        await f.get_json(ESPN_SUMMARY)
        await f.get_json(NBA_PBP)
        m.update("espn_summary", _g(Stoppage.NONE, clock_s=600.0 - i * 3))
        clk.t += 1.0
        assert not book.stale("401")          # 1 s after a good ESPN poll
        clk.t += 2.0
    await f.close()


@pytest.mark.xfail(reason="FINDING T6-5: FeedBook.stale() flags the game when ANY of its URLs backs off and the "
                          "freshest copy is >= one cadence old. With primary on, a backing-off nba_cdn makes a game "
                          "with a perfectly fresh ESPN summary read stale at every poll boundary (age == cadence), "
                          "so it flickers unenterable. Only the freshest URL's backoff should count, or use "
                          "age > cadence.", strict=False)
async def test_a_dark_fast_feed_does_not_flicker_the_game_stale_at_the_poll_boundary():
    clk = Clock()

    def handler(req):
        return httpx.Response(503) if "amazonaws" in str(req.url) else httpx.Response(200, json={"ok": 1})

    f = Fetcher(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), clock=clk)
    m = Merger(clock=clk)
    book = FeedBook(m, f, lambda g: [ESPN_SUMMARY, NBA_PBP], lambda gid: 3.0, clock=clk)
    for i in range(5):
        await f.get_json(ESPN_SUMMARY)
        await f.get_json(NBA_PBP)
        m.update("espn_summary", _g(Stoppage.NONE, clock_s=600.0 - i * 3))
        clk.t += 3.0                           # exactly one cadence: the next ESPN poll is due now
        assert not book.stale("401")
    await f.close()


def test_poll_cadence_never_below_one_second():
    from redzone.config import Config
    c = Config()
    assert float(c.get("polling.nba_pbp_s")) >= 1.0
    assert float(c.get("polling.nhl_pbp_s")) >= 1.0


def test_primary_alone_in_the_order_still_falls_back_to_espn_when_dark():
    """The engine builds stoppage_owner {NBA: "nba"} for sources.nba_primary (no ESPN in the order). When nba_cdn
    goes quiet nobody owns the game and ESPN's own stoppage stands: the old behaviour, not a frozen whistle."""
    clk = Clock()
    m = Merger(clock=clk, stoppage_owner={League.NBA: "nba"})
    m.update("nba", _g(Stoppage.WHISTLE))
    m.update("espn_summary", _g(Stoppage.WHISTLE))
    clk.t += STOPPAGE_OWNER_FRESH_S + 1
    assert m.owner(League.NBA, "401") is None
    g = m.update("espn_summary", _g(Stoppage.NONE, clock_s=280.0))
    assert g.stoppage == Stoppage.NONE


def test_engine_feed_urls_add_the_fast_feed_only_when_primary(tmp_path):
    from redzone.config import Config
    from redzone.engine import Engine
    from redzone.store import Store
    cfg = Config()
    e = Engine(cfg, Store(tmp_path / "rz.db"))
    g = GameState(League.NBA, "401", Team("BOS"), Team("NYK"), phase=Phase.LIVE)
    e._nba_ids["401"] = "0022600123"
    e._nba_primary = False
    assert NBA_PBP not in e._feed_urls(g)                    # shadow: ESPN alone decides freshness
    e._nba_primary = True
    assert NBA_PBP in e._feed_urls(g)


def test_no_scraped_tokens_in_feed_urls():
    for u in (leagues.NBA_PBP, leagues.NBA_SCOREBOARD, leagues.NHL_PBP, leagues.NHL_SCORE,
              leagues.MLB_LIVE, leagues.MLB_SCHEDULE):
        low = u.lower()
        assert u.startswith("https://")
        assert not any(k in low for k in ("token", "apikey", "api_key", "auth", "signature", "sig="))
    assert os.environ.get("RZ_NO_NET", "1")                 # these tests never touch the network
