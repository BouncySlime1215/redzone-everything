"""AUDIT-12, phone orientation beat: no live score over a replay or a time-shifted stream.

Why: /api/v2/orient reads the LIVE feed (score, clock, situation). When a switch lands on a catch-up replay or a
stick held behind live, the beat flashed the present score over a past play: a spoiler Nick cannot un-see.
"""
from types import SimpleNamespace

from test_app import client, engine  # noqa: F401  (fixtures)
from test_phone_v2 import _late

from redzone.web import phone_v2


def _setup(engine, held=None, replaying=None):
    engine.cfg.update({"phone": {"v2": True, "orient": True}})
    g = _late("nfl", 17, 20, 4, 72, possession="HOM", down=2, distance=7, yards_to_goal=34)
    g.game_id = "g1"
    engine.merger.games["g1"] = g
    engine.decider.current = "g1"
    engine.decider.replaying = replaying
    engine.where["g1"] = SimpleNamespace(target="prime:x", label="Prime")
    engine.control.held.clear()
    if held:
        engine.control.held["prime:x"] = held


def test_beat_shown_when_screen_is_live(client, engine):
    _setup(engine)
    assert client.get("/api/v2/orient?game=g1").json()["beat"]["score"] == "AWY 20 · HOM 17"


def test_beat_hidden_over_a_replay(client, engine):
    _setup(engine, replaying=SimpleNamespace(game_id="g1"))
    assert client.get("/api/v2/orient?game=g1").json()["beat"] is None


def test_beat_hidden_on_a_time_shifted_stick(client, engine):
    _setup(engine, held=90.0)
    assert client.get("/api/v2/orient?game=g1").json()["beat"] is None
    assert phone_v2.screen_behind(engine, "g1") is True
    engine.control.held["prime:x"] = 1.0
    assert client.get("/api/v2/orient?game=g1").json()["beat"] is not None
