"""[tracker item 5] Fast league feeds in shadow: parsers, the poller's politeness, and the lag race."""
import asyncio
import copy
import json
from pathlib import Path

from redzone.config import Config
from redzone.models import GameState, League, Phase, Team
from redzone.sources import fastfeeds, leagues

FIX = Path(__file__).parent / "fixtures"


def _fx(name):
    return json.loads((FIX / name).read_text())


def _g(league, gid, away, home):
    return GameState(league=League(league), game_id=gid, home=Team(home), away=Team(away), phase=Phase.LIVE)


def test_nba_rows_prime_then_new_actions_carry_their_real_time():
    pbp = _fx("nba_cdn_playbyplay_0022600157.json")
    early = copy.deepcopy(pbp)
    early["game"]["actions"] = early["game"]["actions"][:-3]
    seen, g = {}, _g("nba", "401", "AWY", "HOM")
    assert fastfeeds.nba_rows(g, early, seen, 100.0) == []                      # first look = history
    rows = fastfeeds.nba_rows(g, pbp, seen, 200.0)
    assert len(rows) == 3 and all(r["seen"] == 200.0 and r["real"] and r["source"] == "nba_cdn_fast" for r in rows)
    assert fastfeeds.nba_rows(g, pbp, seen, 300.0)[0]["seen"] == 200.0          # first-seen is kept, not re-stamped


def test_nhl_rows_have_no_real_time_and_prime_first():
    pbp = _fx("nhl_api_gamecenter_2026020229_play-by-play.json")
    early = copy.deepcopy(pbp)
    early["plays"] = early["plays"][:-2]
    seen, g = {}, _g("nhl", "402", "UTA", "PHI")
    assert fastfeeds.nhl_rows(g, early, seen, 10.0) == []
    rows = fastfeeds.nhl_rows(g, pbp, seen, 20.0)
    assert len(rows) == 2 and all(r["real"] is None and r["source"] == "nhl_api_fast" for r in rows)


def test_mlb_rows_pitches_and_at_bats_with_end_times():
    live = _fx("mlb_api_feed_live_813024.json")
    early = copy.deepcopy(live)
    early["liveData"]["plays"]["allPlays"] = early["liveData"]["plays"]["allPlays"][:-1]
    seen, g = {}, _g("mlb", "403", "LAD", "TOR")
    assert fastfeeds.mlb_rows(g, early, seen, 1.0) == []
    rows = fastfeeds.mlb_rows(g, live, seen, 2.0)
    last = live["liveData"]["plays"]["allPlays"][-1]
    assert len(rows) == len(last["playEvents"]) + 1                              # every pitch + the finished at-bat
    assert rows[-1]["event"].endswith(".end") and rows[-1]["kind"] == "Grounded Into DP" and rows[-1]["real"]
    assert all(r["period"] == 11 for r in rows)
    assert fastfeeds.mlb_rows(g, None, {}, 1.0) == [] and fastfeeds.mlb_rows(g, {"x": 1}, {"_primed": 0.0}, 1.0) == []


def test_mlb_schedule_index_matches_espn_abbreviations():
    idx = fastfeeds.mlb_index(_fx("mlb_api_schedule_2025-11-01_broadcasts.json"))
    assert fastfeeds.mlb_match(_g("mlb", "x", "LAD", "TOR"), idx)["gamePk"] == 813024
    assert fastfeeds.mlb_match(_g("mlb", "x", "CHW", "TOR"), idx) is None


class FakeFetcher:
    def __init__(self, bodies):
        self.bodies, self.calls = bodies, []

    async def get_json(self, url, headers=None, min_interval=0.0):
        self.calls.append((url, min_interval))
        for key, body in self.bodies.items():
            if key in url:
                return body
        return None


def _poller(tmp_path, games, bodies, **cfg):
    c = Config(user_path=tmp_path / "config.yaml", history_dir=tmp_path / "hist")
    if cfg:
        c.update({"fast_feeds": cfg})
    rows = []
    f = FakeFetcher(bodies)
    return fastfeeds.FastFeeds(c, lambda: games, rows.extend, fetcher=f, clock=lambda: 50.0), f, rows


def test_off_by_default_and_polls_nothing(tmp_path):
    games = {"401": _g("nba", "401", "LAL", "BOS")}
    ff, f, rows = _poller(tmp_path, games, {})
    assert ff.on() is False
    asyncio.run(asyncio.wait_for(_one_round(ff), 1))
    assert f.calls == [] and rows == []


async def _one_round(ff):
    if ff.on():
        await ff.tick()


def test_on_polls_nba_at_one_second_and_writes_new_events(tmp_path):
    sb = _fx("nba_cdn_odds_todaysGames.json")   # any dict: the index is patched below
    pbp = _fx("nba_cdn_playbyplay_0022600157.json")
    early = copy.deepcopy(pbp)
    early["game"]["actions"] = early["game"]["actions"][:-2]
    games = {"401": _g("nba", "401", "LAL", "BOS"), "402": _g("nhl", "402", "VGK", "BOS")}
    ff, f, rows = _poller(tmp_path, games, {"todaysScoreboard": sb, "playbyplay": early},
                          enabled=True, leagues=["nba"])
    gm = {"gameId": "0022600157", "gameStatus": 2}
    orig = leagues.nba_index
    leagues.nba_index = lambda _sb: {("LAL", "BOS"): gm}
    try:
        asyncio.run(ff.tick())
        f.bodies["playbyplay"] = pbp
        asyncio.run(ff.tick())
    finally:
        leagues.nba_index = orig
    assert len(rows) == 2 and ff.counts == {"nba_cdn_fast": 2}
    pbp_calls = [mi for url, mi in f.calls if "playbyplay" in url]
    assert pbp_calls and all(abs(mi - 0.9) < 1e-9 for mi in pbp_calls)          # nba_s 1 s (x0.9 so a tick never skips)
    assert not any("nhle" in url for url, _ in f.calls)                          # NHL not in fast_feeds.leagues


def test_max_games_caps_each_league(tmp_path):
    games = {f"g{i}": _g("nhl", f"g{i}", f"A{i}", f"H{i}") for i in range(5)}
    ff, _, _ = _poller(tmp_path, games, {}, enabled=True, max_games=2)
    assert [g.game_id for g in ff.live(League.NHL)] == ["g0", "g1"]


def test_race_lags_and_nhl_goal_lead():
    import importlib.util
    spec = importlib.util.spec_from_file_location("ffr", Path(__file__).parents[1] / "scripts" / "fast_feed_race.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    fast = [{"league": "nba", "game": "1", "source": "nba_cdn_fast", "seen": 105.0, "real": 100.0},
            {"league": "nba", "game": "1", "source": "nba_cdn_fast", "seen": 207.0, "real": 200.0},
            {"league": "nhl", "game": "2", "source": "nhl_api_fast", "seen": 1000.0, "real": None, "kind": "goal"}]
    espn = [{"league": "nba", "game": "1", "source": "espn_summary", "lag": 45.0},
            {"league": "nhl", "game": "2", "source": "espn_summary", "t": 1030.0, "type": "Goal", "lag": None}]
    r = m.race(fast, espn)
    assert r["nba"]["nba_cdn_fast"] == {"n": 2, "p50": 7.0, "p90": 7.0}
    assert r["nba"]["espn_summary"]["p50"] == 45.0
    assert r["nhl"]["nhl_goal_lead_s"]["p50"] == 30.0                           # api-web saw the goal 30 s before ESPN
