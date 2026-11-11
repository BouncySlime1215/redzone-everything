"""AUDIT-2, web server: an override without a game id is refused, not saved under the id "None".

Why: /api/override did str(body.get("game_id")), so a missing id was stored as an override for the game "None",
survived restarts, and answered "Saved". Same for a link that gave no usable target.
"""
from types import SimpleNamespace

from fastapi.testclient import TestClient

from redzone.web.server import build_app


class FakeEngine:
    def __init__(self):
        self.cfg = SimpleNamespace(get=lambda k, d=None: d)
        self.subscribers = set()
        self.overrides = []
        self.channels = SimpleNamespace(set_override=lambda gid, target: self.overrides.append((gid, target)))
        self._user_state_path = None

    def snapshot(self):
        return {"games": [], "health": {}}

    def decide(self):
        pass

    def broadcast(self):
        pass


def test_override_needs_a_game_id():
    e = FakeEngine()
    c = TestClient(build_app(e))
    for body in ({"target": "youtubetv:espn"}, {"game_id": None, "target": "youtubetv:espn"},
                 {"game_id": "", "target": "youtubetv:espn"}, {"game_id": 5, "target": "youtubetv:espn"}):
        r = c.post("/api/override", json=body)
        assert r.json()["ok"] is False, body
    assert e.overrides == []


def test_override_with_a_game_id_still_saves_and_clears():
    e = FakeEngine()
    c = TestClient(build_app(e))
    assert c.post("/api/override", json={"game_id": "401", "target": "youtubetv:espn"}).json()["ok"]
    assert c.post("/api/override", json={"game_id": "401", "target": None}).json()["ok"]
    assert e.overrides == [("401", "youtubetv:espn"), ("401", None)]
