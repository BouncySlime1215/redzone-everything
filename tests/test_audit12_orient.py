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
    engine.control.held.clear()
    assert client.get("/api/v2/orient?game=g1").json()["beat"] is not None


def _watch_setup(engine, held=None, replaying=None):
    from redzone.models import League
    from redzone.scoring.engine import Scored
    _setup(engine, held=held, replaying=replaying)
    s = Scored("g1", League.NFL, 50.0, 50.0, 0.0, 0.0, 0.0, "r", [], False, None, None, 0.0, crunch=True, factor="Tied late")
    s.watch_value, s.watch_why = 60.0, "Go-ahead drive underway"
    engine.scored["g1"] = s
    engine.cfg.update({"phone": {"v2": True, "orient": True, "why_tag_min": 0}})


def test_watch_strip_hides_live_lines_of_the_game_shown_in_the_past(client, engine):
    _watch_setup(engine, held=90.0)
    r = client.get("/api/v2/watch").json()
    row = next(x for x in r["games"] if x["game_id"] == "g1")
    assert row["context"] is None and row["why"] == "" and r["why_tag"] is None


def test_watch_strip_keeps_the_lines_when_the_screen_is_live(client, engine):
    _watch_setup(engine)
    r = client.get("/api/v2/watch").json()
    row = next(x for x in r["games"] if x["game_id"] == "g1")
    assert row["why"] == "Go-ahead drive underway" and r["why_tag"]["text"] == "Go-ahead drive underway"
    assert row["context"] and "down 3" in row["context"]


def test_follow_line_hides_stat_text_of_the_game_shown_in_the_past(client, engine):
    engine.cfg.update({"phone": {"follow": True}, "noggin": {"priority": {"prefs": {"followed_players": ["Jokic"]}}}})
    _watch_setup(engine, held=90.0)
    engine.cfg.update({"phone": {"v2": True, "follow": True}})
    engine.merger.games["g1"].spotlight = {"name": "Nikola Jokic", "text": "Jokic hits the go-ahead three"}
    rows = client.get("/api/v2/follow").json()["live"]
    assert rows and rows[0]["on"] and all(h["text"] == "" for h in rows[0]["players"])
    engine.control.held.clear()
    assert client.get("/api/v2/follow").json()["live"][0]["players"][0]["text"]
