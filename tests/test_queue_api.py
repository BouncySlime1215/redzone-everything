"""Phone queue card API (WP-14): top 5 rows with level and reason, no spoilers, pins survive a restart,
hidden when queue.enabled is off. Uses a fake engine: no TV, no adb. Why: the card must be safe to show."""
import json

import time

import pytest
from fastapi.testclient import TestClient

from redzone.web.server import build_app
from redzone import userstate
from redzone.commands import Commands


class Cfg:
    def __init__(self, on):
        self.on = on

    def get(self, key, default=None):
        return self.on if key == "queue.enabled" else default


def card(game, level="R4", reason="up soon", **extra):
    return {"game": game, "level": level, "app": "youtubetv", "prep_s": 4.0, "owner": "moment",
            "reason": reason, "flippable": True, **extra}


class FakeEngine:
    def __init__(self, tmp_path, on=True):
        self.cfg = Cfg(on)
        self._user_state_path = tmp_path / "data" / "user_state.json"
        self.calls = []
        self.commands = Commands()
        self.subscribers = set()

    def snapshot(self):
        s = {"games": [], "health": {}}
        if self.cfg.on:
            s["game_queue"] = {
                "on_screen": card("on"), "hidden": card("h", "R5", "next cut"),
                "up_next": [card(f"g{i}", "R3", "up soon", tier=1, join_at=99, value=0.9, outcome="TD") for i in range(8)],
                "cant_reach": ["x1", "x2"], "want": None, "swap": False, "why": "",
            }
        return s

    def action(self, kind, gid=None, value=None):
        self.calls.append((kind, gid, value))
        return True, "ok"

    def decide(self):
        pass

    def broadcast(self):
        pass


@pytest.fixture
def eng(tmp_path):
    return FakeEngine(tmp_path)


def client(e):
    return TestClient(build_app(e))


def test_top_5_rows_carry_level_and_reason(eng):
    r = client(eng).get("/api/queue").json()
    assert len(r["rows"]) == 5
    assert r["rows"][0]["game"] == "h"
    for row in r["rows"]:
        assert row["level_word"] and row["reason"]
    assert r["cant_reach"] == ["x1", "x2"]


def test_no_known_outcomes_leave_the_server(eng):
    c = client(eng)
    for body in (c.get("/api/queue").text, json.dumps(c.get("/api/state").json()["game_queue_card"])):
        for leak in ("join_at", "outcome", "value", "tier", "TD"):
            assert leak not in body


def test_flag_off_api_and_state_unchanged(tmp_path):
    e = FakeEngine(tmp_path, on=False)
    c = client(e)
    assert c.get("/api/queue").status_code == 404
    assert c.post("/api/queue/pin", json={"game_id": "g1"}).status_code == 404
    assert c.get("/api/state").json() == json.loads(json.dumps(e.snapshot()))
    assert e.calls == []


def test_pin_and_move_call_engine_action(eng):
    c = client(eng)
    assert c.post("/api/queue/pin", json={"game_id": "g1"}).json()["ok"]
    assert c.post("/api/queue/move", json={"game_id": "g2", "value": "up"}).json()["ok"]
    assert c.post("/api/queue/move", json={"game_id": "g2", "value": "sideways"}).json()["ok"] is False
    assert eng.calls == [("pin", "g1", True), ("move", "g2", "up")]


def test_pins_survive_a_restart(tmp_path):
    """Why: one store. The engine saves its pins in user_state.json, so load must keep them (WP-12 + WP-14 merge)."""
    p = tmp_path / "user_state.json"
    userstate.save(p, {"paused": False, "queue_pins": ["g2", "g1", "g2", 7]})
    assert userstate.load(p)["queue_pins"] == ["g2", "g1"]


def test_card_shows_the_engines_pins(eng):
    eng.queue_pins = ["g2"]
    body = client(eng).get("/api/queue").json()
    assert body["pins"] == ["g2"] and [r["game"] for r in body["rows"] if r["pinned"]] == ["g2"]


def test_stale_pins_are_dropped(tmp_path):
    p = tmp_path / "user_state.json"
    userstate.save(p, {"paused": False, "queue_pins": ["g1"]})
    assert "queue_pins" not in (userstate.load(p, now=time.time() + userstate.STALE_S + 60) or {})
