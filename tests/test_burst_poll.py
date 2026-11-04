"""[sync 5] Burst polling near a likely stoppage, and If-Modified-Since on hosts with no ETag."""
import asyncio

import httpx
from conftest import nfl

from redzone.config import Config
from redzone.engine import Engine
from redzone.scoring.engine import Personal
from redzone.sources.http import Fetcher
from redzone.store import Store


def _engine(tmp_path, **burst):
    e = Engine(Config(user_path=tmp_path / "c.yaml", history_dir=tmp_path / "h"), Store(tmp_path / "db"))
    e.cfg.data["polling"]["burst"].update({"enabled": True, **burst})
    return e


def _live(e, *games):
    for g in games:
        e.merger.update("t", g)
        e.scored[g.game_id] = e.scorer.score(g, Personal())
    return list(games)


def test_burst_is_off_by_default(tmp_path):
    e = Engine(Config(user_path=tmp_path / "c.yaml", history_dir=tmp_path / "h"), Store(tmp_path / "db"))
    live = _live(e, nfl("a", q=2, clock=150.0))          # two-minute warning coming: p 0.8
    e.decider.current = "a"
    assert e._burst_games(live) == set()


def test_burst_on_screen_game_when_a_stoppage_is_likely(tmp_path):
    e = _engine(tmp_path)
    live = _live(e, nfl("a", q=2, clock=150.0), nfl("b", home="NYG", away="PHI", q=1, clock=700.0))
    e.decider.current = "a"
    assert "a" in e._burst_games(live)
    e.cfg.data["polling"]["burst"]["break_p"] = 0.95       # nothing is that likely: no burst
    assert "a" not in e._burst_games(live)


def test_burst_caps_games_and_skips_quiet_ones(tmp_path):
    e = _engine(tmp_path, max_games=1)
    live = _live(e, nfl("a", q=1, clock=700.0), nfl("b", home="NYG", away="PHI", q=1, clock=30.0),
                 nfl("c", home="DET", away="GB", q=3, clock=40.0))
    e.decider.current = "a"                                # quiet mid-quarter: not bursted
    got = e._burst_games(live)
    assert got == set()                                    # the on-screen game holds the one slot


def test_pbp_cadence_follows_the_burst(tmp_path):
    e = _engine(tmp_path, every_s=1.5)
    e._burst = {"a"}
    assert e._pbp_every("a", 3.0) == 1.5 and e._pbp_every("b", 3.0) == 3.0
    e.cfg.data["polling"]["burst"]["every_s"] = 0.2        # floor 1 s: never hammer a feed
    assert e._burst_s() == 1.0


def test_if_modified_since_is_sent_and_304_keeps_the_copy():
    seen = []

    def handler(req):
        seen.append(req.headers.get("If-Modified-Since"))
        if req.headers.get("If-Modified-Since"):
            return httpx.Response(304)
        return httpx.Response(200, json={"n": 1}, headers={"Last-Modified": "Wed, 04 Nov 2026 07:00:00 GMT"})

    async def run():
        f = Fetcher(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        a = await f.get_json("https://cdn.nba.com/x.json")
        b = await f.get_json("https://cdn.nba.com/x.json")
        await f.close()
        return a, b

    a, b = asyncio.run(run())
    assert a == b == {"n": 1}
    assert seen == [None, "Wed, 04 Nov 2026 07:00:00 GMT"]


def test_fastest_feed_per_league_picks_lowest_dead_ball_p50():
    from redzone.lab.timing import LagReport, fastest_feeds
    st = lambda n, p50: {"dead_ball": {"n": n, "p50": p50, "p90": p50 + 10}}   # noqa: E731
    rep = LagReport(by_league={"nhl": {"espn_summary": st(50, 43.1), "nhl": st(50, 16.7)},
                               "nba": {"espn_summary": st(50, 45.0), "nba": st(5, 9.0)}})
    race = {"nhl": {"stop": {"n": 30, "sources": {"nhl": {"first": 25, "first_pct": 83.3},
                                                  "espn_summary": {"first": 5, "first_pct": 16.7}}}}}
    got = fastest_feeds(rep, race)
    assert got["nhl"]["by_lag"]["source"] == "nhl" and got["nhl"]["by_race"]["source"] == "nhl"
    assert got["nba"]["by_lag"]["source"] == "espn_summary"      # 5 dead balls is too few to trust
