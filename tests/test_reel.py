"""Catch-up reel: which plays, how long each, and where the live angle of a clip ends."""
import pytest
import asyncio

import numpy as np

from redzone import reel
from redzone.reel import Clip, ClipCache, Reeler, clips_from_summary, find_live_end, headline_points, live_window, plan_reel

NOW = 1_800_000_000.0


def _clip(i, pts=24.0, game=None, league="nfl", age=300.0, seg=8.0, mine=False, head=None):
    return Clip(f"c{i}", game or f"g{i}", league, head or f"Player {i} scores a touchdown", "u", NOW - age, 30, pts, mine, seg)


def test_headlines_rank_the_plays_you_need_to_see():
    assert headline_points("Game-winning field goal as time expires") > headline_points("Caleb Williams fires a dart for a TD")
    assert headline_points("Faulk nets power-play goal") > headline_points("Bassey throws down big flush")
    assert headline_points("Coach talks about the loss") == 0


def test_summary_clips_skip_recaps_interviews_and_long_videos():
    data = {"header": {"competitions": [{"competitors": [{"team": {"abbreviation": "WSH"}}, {"team": {"abbreviation": "DAL"}}]}]},
            "videos": [
                {"id": 1, "headline": "Daniels hits McLaurin for a TD", "duration": 30, "originalPublishDate": "2026-11-03T01:00:00Z",
                 "links": {"source": {"href": "a.mp4", "HD": {"href": "a_hd.mp4"}}}},
                {"id": 2, "headline": "Commanders beat Cowboys 31-20", "duration": 40, "originalPublishDate": "2026-11-03T02:00:00Z",
                 "links": {"source": {"href": "b.mp4"}}},
                {"id": 3, "headline": "Interception seals it", "duration": 300, "originalPublishDate": "2026-11-03T02:00:00Z",
                 "links": {"source": {"href": "c.mp4"}}},
                {"id": 4, "headline": "Fumble!", "duration": 20, "links": {}},
                "garbage",
            ]}
    got = clips_from_summary(data, "nfl", "g1", {"WSH"})
    assert [c.id for c in got] == ["1"]
    assert got[0].url == "a_hd.mp4" and got[0].mine


def test_reel_is_five_plus_clips_in_30_to_60_seconds():
    clips = [_clip(i, seg=11.0) for i in range(8)]
    r = plan_reel(clips, now=NOW)
    assert len(r) >= 5
    assert 30 <= sum(c.seg for c in r) <= 60.0 + 1e-6
    assert all(c.seg >= reel.SEG_MIN_S for c in r)


def test_no_thin_reel():
    assert plan_reel([_clip(i) for i in range(4)], now=NOW) == []


def test_your_team_leads_and_seen_or_stale_plays_are_left_out():
    clips = [_clip(i) for i in range(6)] + [_clip(9, pts=5, mine=True), _clip(10, pts=60, age=4000)]
    r = plan_reel(clips, now=NOW, seen=lambda c: c.id == "c0")
    assert r[0].id == "c9"
    assert "c0" not in [c.id for c in r] and "c10" not in [c.id for c in r]


def test_a_reel_is_a_tour_not_one_games_recap():
    clips = [_clip(i, game="same", pts=30) for i in range(5)] + [_clip(10 + i, pts=20) for i in range(5)]
    r = plan_reel(clips, now=NOW)
    assert sum(c.game_id == "same" for c in r) <= 2


def _frames(end_s, fps=4, n=40, top=False):
    rng = np.random.default_rng(0)
    f = rng.integers(0, 255, (n, 216, 384)).astype(np.uint8)       # moving picture everywhere
    band = slice(0, 40) if top else slice(176, 216)
    f[: int(end_s * fps), band, 100:300] = 200                        # a still overlay until end_s
    return f


def test_live_angle_ends_when_the_score_bug_goes():
    assert find_live_end(_frames(7.5)) == 7.5
    assert find_live_end(_frames(9.0, top=True)) == 9.0                 # hockey: bug at the top


def test_no_overlay_means_no_guess():
    rng = np.random.default_rng(1)
    assert find_live_end(rng.integers(0, 255, (40, 216, 384)).astype(np.uint8)) is None
    assert find_live_end(np.zeros((3, 216, 384), np.uint8)) is None


def test_live_window_keeps_the_play_end_and_drops_the_setup():
    assert live_window(7.25, 30) == (0.0, 8.0)
    start, length = live_window(20.0, 40)
    assert length == reel.SEG_MAX_S and start + length == 20.75
    assert live_window(None, 40) == (0.0, reel.FALLBACK_S)
    assert live_window(1.0, 40)[1] == reel.SEG_MIN_S


def test_a_clip_that_fails_is_skipped_and_not_retried_every_poll(tmp_path):
    async def fail(url, dest):
        return False
    cache = ClipCache(root=tmp_path, fetch=fail, min_free_mb=0, clock=lambda: NOW)
    c = _clip(1)
    assert asyncio.run(cache.prepare(c)) is False
    assert c.id in cache.failed
    calls = []

    async def count(url, dest):
        calls.append(url)
        return False
    cache._fetch = count
    assert asyncio.run(cache.prepare(c)) is False and calls == []


@pytest.mark.playbook("MAC-08")
def test_no_room_on_disk_means_no_download(tmp_path):
    async def boom(url, dest):
        raise AssertionError("must not download")
    cache = ClipCache(root=tmp_path, fetch=boom, min_free_mb=10 ** 12)
    assert asyncio.run(cache.prepare(_clip(1))) is False


def test_reeler_off_does_nothing(cfg):
    cfg.update({"reel": {"enabled": False}})
    assert asyncio.run(Reeler(cfg).refresh()) is None


def test_clip_is_matched_to_its_play():
    plays = [(NOW - 900, "Jayden Daniels pass short right to Terry McLaurin for 12 yards", False),
             (NOW - 600, "Jayden Daniels pass deep left to Terry McLaurin for 41 yds for a TOUCHDOWN", True),
             (NOW + 60, "Terry McLaurin catch after the clip posted", False)]
    assert reel.match_play("Daniels hits McLaurin for a TD", NOW, plays) == NOW - 600
    assert reel.match_play("Wild finish in Dallas", NOW, plays) is None


def test_reel_endpoint_says_when_nothing_is_ready(tmp_path):
    from fastapi.testclient import TestClient
    from redzone.config import Config
    from redzone.engine import Engine
    from redzone.store import Store
    from redzone.web.server import build_app
    e = Engine(Config(user_path=tmp_path / "c.yaml", history_dir=tmp_path / "h"), Store(tmp_path / "db.sqlite"))
    c = TestClient(build_app(e))
    assert c.get("/reel.mp4").status_code == 404
    assert c.get("/api/reel").json()["clips"] == []
