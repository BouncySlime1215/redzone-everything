"""AUDIT-12, phone queue card: a double tap or a retried tap lands once.

Why: /api/queue/move ran engine.action on every POST, so "Move up" tapped twice (or retried after a Wi-Fi blip)
moved the game two places. The phone now sends a tap id and the server answers the repeat from its log.
"""
from pathlib import Path

from test_queue_api import FakeEngine, client

STATIC = Path(__file__).resolve().parent.parent / "redzone" / "web" / "static"


def test_same_tap_id_moves_once(tmp_path):
    e = FakeEngine(tmp_path)
    c = client(e)
    body = {"game_id": "g2", "value": "up", "id": "tap-abc12345"}
    first, again = c.post("/api/queue/move", json=body).json(), c.post("/api/queue/move", json=body).json()
    assert first["ok"] and again["ok"] and again["duplicate"] is True
    assert e.calls == [("move", "g2", "up")]


def test_new_tap_id_is_a_new_move(tmp_path):
    e = FakeEngine(tmp_path)
    c = client(e)
    c.post("/api/queue/move", json={"game_id": "g2", "value": "up", "id": "tap-abc12345"})
    c.post("/api/queue/move", json={"game_id": "g2", "value": "up", "id": "tap-def67890"})
    assert len(e.calls) == 2


def test_phone_queue_taps_carry_an_id_and_do_not_overlap():
    js = (STATIC / "rz-queue.js").read_text()
    assert "id: tapId()" in js and "inFlight" in js
