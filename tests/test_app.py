"""The server and engine, end to end, with no network and no TV."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from redzone.channels import ChannelMap
from redzone.config import Config
from redzone.engine import Engine
from redzone.models import League
from redzone.sources import espn
from redzone.store import Store
from redzone.web.server import _private, build_app

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def engine(tmp_path):
    cfg = Config(user_path=tmp_path / "config.yaml", history_dir=tmp_path / "hist")
    e = Engine(cfg, Store(tmp_path / "rz.db"))
    e.channels = ChannelMap(tmp_path / "channels.json")
    e.control.channels = e.channels
    # Load a real scoreboard so there are games to show.
    for g in espn.parse_scoreboard(League.NFL, json.loads((FIX / "espn_nfl_scoreboard_2026-11-02.json").read_text())):
        e.merger.update("espn_scoreboard", g)
    for g in espn.parse_scoreboard(League.NBA, json.loads((FIX / "espn_nba_scoreboard_2026-11-02.json").read_text())):
        e.merger.update("espn_scoreboard", g)
    e.decide()
    return e


@pytest.fixture
def client(engine):
    return TestClient(build_app(engine))


def test_state_and_index(client):
    r = client.get("/api/state")
    assert r.status_code == 200
    s = r.json()
    assert s["mode"]["dry_run"] is True
    assert len(s["games"]) > 10
    g = s["games"][0]
    for k in ("id", "league", "home", "away", "score", "reason", "where"):
        assert k in g


def test_actions(client, engine):
    gid = next(g["id"] for g in client.get("/api/state").json()["games"])
    for a, v in [("boost", None), ("mute", None), ("unmute", None), ("pause", None), ("resume", None),
                 ("focus", "focus"), ("autopilot", None), ("lock", None), ("unlock", None), ("vote", 1),
                 ("label", "commercial")]:
        r = client.post("/api/action", json={"action": a, "game_id": gid, "value": v}).json()
        assert r["ok"], (a, r)
    assert client.post("/api/action", json={"action": "nonsense"}).json()["ok"] is False


def test_settings_validate_and_rollback(client):
    bad = client.post("/api/settings", json={"changes": {"decider": {"manual_pause_s": 99999}}}).json()
    assert not bad["ok"] and "between" in bad["message"]
    ok = client.post("/api/settings", json={"changes": {"decider": {"manual_pause_s": 50}}}).json()
    assert ok["ok"]
    assert client.get("/api/settings").json()["config"]["decider"]["manual_pause_s"] == 50
    assert client.post("/api/settings/rollback").json()["ok"]


def test_bets_roundtrip(client):
    gid = client.get("/api/state").json()["games"][0]["id"]
    assert not client.post("/api/bets", json={"game_id": gid, "kind": "spread"}).json()["ok"]
    assert client.post("/api/bets", json={"game_id": gid, "kind": "spread", "team": "SEA", "line": "-3"}).json()["ok"]
    bets = client.get("/api/bets").json()
    assert bets and bets[0]["team"] == "SEA"
    assert client.delete(f"/api/bets/{bets[0]['id']}").json()["ok"]


def test_channels_api(client):
    r = client.post("/api/channels", json={"channel": "espn", "link": "https://tv.youtube.com/watch/AAAAAAAAAAA"})
    assert r.json()["ok"]
    chans = client.get("/api/channels").json()["channels"]
    assert next(c for c in chans if c["key"] == "espn")["code"] == "AAAAAAAAAAA"
    assert not client.post("/api/channels", json={"channel": "espn", "link": "nope"}).json()["ok"]


def test_lan_only():
    assert _private("192.168.1.20") and _private("10.0.0.5") and _private("127.0.0.1")
    assert _private("100.110.165.130")       # carrier-grade/campus range used by this network
    assert not _private("8.8.8.8") and not _private(None)


def test_redzone_on_screen_shows_on_the_phone(client, engine):
    from redzone.decider.decider import REDZONE
    engine.decider.current = REDZONE
    now = client.get("/api/state").json()["now"]
    assert now["game_id"] == REDZONE and now["matchup"] == "NFL RedZone"


def test_redzone_only_offered_when_on_the_air(engine):
    # The fixture's slate is a Monday: RedZone isn't on, whatever the settings say.
    assert engine._redzone_target(sunday_ticket=True, dry=True) is None


def test_external_page_cannot_control_tv(client, engine):
    assert not engine.decider.user.paused
    r = client.post('/api/action', json={'action': 'pause'}, headers={'Origin': 'https://unrelated.example'})
    assert r.status_code == 403 and not engine.decider.user.paused
    r = client.post('/api/action', content='{"action":"pause"}', headers={'Content-Type': 'text/plain'})
    assert r.status_code == 415 and not engine.decider.user.paused
    r = client.post('/api/action', json={'action': 'pause'}, headers={'Origin': 'http://testserver'})
    assert r.status_code == 200 and r.json()['ok']


def test_dns_rebinding_host_is_rejected(client):
    assert client.get('/api/state', headers={'Host': 'unrelated.example'}).status_code == 403


def test_external_page_cannot_send_bodyless_mutation(client):
    r = client.post('/api/settings/rollback', headers={'Origin': 'https://unrelated.example'})
    assert r.status_code == 403


def test_tap_a_play_in_the_detail_sheet_calibrates_against_that_play(client, engine):
    """The game sheet's play rows carry the timeline's play id; tapping one calibrates against exactly it."""
    import time
    from types import SimpleNamespace
    from redzone.decider.screen import Event, Timeline

    gid = next(g.game_id for g in engine.merger.games.values() if g.league == League.NBA)
    engine.summaries[gid] = json.loads((FIX / "espn_nba_summary_401909906.json").read_text())
    plays = client.get(f"/api/game/{gid}").json()["plays"]
    assert len(plays) >= 2 and all(p["id"] for p in plays)
    first, second = plays[0]["id"], plays[1]["id"]

    # The same game is on the TV, with two plays (free throws) seconds apart on its timeline.
    engine.cfg.update({"mode": {"dry_run": False}}, record=False)
    dec = engine.decider
    dec.current = gid
    engine.where[gid] = SimpleNamespace(target="youtubetv:test", label="YouTube TV", confidence="high")
    tl = dec.timelines[gid] = Timeline(League.NBA)
    now = time.time()
    tl.events.append(Event(now - 42, "play", "Free throw 1 of 2", pid=second))
    tl.events.append(Event(now - 40, "play", "Free throw 2 of 2", pid=first))

    # Another game's play can't calibrate the stream that's on.
    other = next(g.game_id for g in engine.merger.games.values() if g.game_id != gid)
    r = client.post("/api/action", json={"action": "saw_it", "game_id": other, "value": first}).json()
    assert r["ok"] is False and "on your TV" in r["message"]

    r = client.post("/api/action", json={"action": "saw_it", "game_id": gid, "value": first}).json()
    assert r["ok"], r
    assert "Free throw 2 of 2" in r["message"] and "delay" in r["message"]
    assert engine.delays.get("youtubetv:test").samples
