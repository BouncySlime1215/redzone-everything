"""Phone v2 (flag phone.v2, off): reason chips per switch, 👍/👎 tied to one switch,
"I missed that", and the Priority L2 sliders. None of it touches the decider or the TV."""
import json
import time

from test_app import client, engine  # noqa: F401  (fixtures)

from redzone.models import Play
from redzone.web import phone_v2


def _on(engine):
    engine.cfg.update({"phone": {"v2": True}})


def _switch(engine, to, reason="Crunch time: UNC down 2, red zone"):
    engine.store._x("INSERT INTO switches (ts, frm, to_game, target, kind, reason, dry_run, ok, detail) "
                    "VALUES (?,?,?,?,?,?,?,?,?)", (time.time(), None, to, "a", "auto", reason, 1, 1, "{}"))


def test_off_by_default_and_every_route_refuses(client, engine):
    assert client.get("/api/v2/status").json() == {"enabled": False}
    assert client.get("/api/v2/switches").status_code == 404
    assert client.post("/api/v2/vote", json={"key": "x", "vote": 1}).status_code == 404
    assert client.post("/api/v2/missed", json={}).status_code == 404
    assert client.get("/api/v2/priority").status_code == 404
    assert client.post("/api/v2/priority", json={"bets_weight": 2}).status_code == 404


def test_chips_read_the_reason_text():
    keys = [c["key"] for c in phone_v2.chips("Crunch time: UNC down 2, red zone")]
    assert keys[:3] == ["yours", "crunch", "redzone"]
    assert phone_v2.chips("") == [{"key": "best", "label": "Best on"}]
    assert len(phone_v2.chips("your team crunch red zone touchdown power play bet")) == 4


def test_vote_is_tied_to_one_switch_and_never_suggests(client, engine):
    _on(engine)
    gid = next(iter(engine.merger.games))
    _switch(engine, gid)
    sw = client.get("/api/v2/switches").json()["switches"]
    assert sw and sw[0]["vote"] == 0 and sw[0]["chips"][0]["key"] == "yours"
    r = client.post("/api/v2/vote", json={"key": sw[0]["key"], "vote": -1}).json()
    assert r["ok"] and r["vote"] == -1
    assert client.get("/api/v2/switches").json()["switches"][0]["vote"] == -1
    vote, note, ctx = engine.store._x("SELECT vote, note, context FROM feedback").fetchall()[-1]
    assert vote == -1 and note == "switch" and json.loads(ctx)["terms"]["crunch"] == 1.0
    t = engine.store.taps()[-1]
    assert t["tap"] == "down" and t["game"] == gid and t["switch"] == sw[0]["key"] and "Crunch" in t["reason"]
    assert client.post("/api/v2/vote", json={"key": "nope", "vote": 1}).json()["ok"] is False
    assert client.post("/api/v2/vote", json={"key": sw[0]["key"], "vote": 5}).json()["ok"] is False
    # self-taught rule (Nick, Nov 4): eight 👎 on crunch switches suggest and change nothing
    for _ in range(8):
        client.post("/api/v2/vote", json={"key": sw[0]["key"], "vote": -1})
    p = client.get("/api/v2/priority").json()
    assert p["suggestions"] == []
    assert p["prefs"]["bets_weight"] == 1.0


def test_missed_finds_the_big_play_off_screen(client, engine):
    _on(engine)
    gids = list(engine.merger.games)
    engine.decider.current = gids[0]
    now = time.time()
    engine.merger.games[gids[1]].recent_plays.append(Play(id="p9", text="TD pass", kind="td", scoring=True,
                                                          seen_at=now - 120))
    engine.merger.games[gids[0]].recent_plays.append(Play(id="p1", text="TD on screen", kind="td", scoring=True,
                                                          seen_at=now - 30))
    r = client.post("/api/v2/missed", json={}).json()
    assert r["ok"] and r["guess"]["game_id"] == gids[1] and r["guess"]["play_id"] == "p9"
    vote, note, game = engine.store._x("SELECT vote, note, game FROM feedback").fetchall()[-1]
    assert (vote, note, game) == (0, "missed", gids[1])
    assert engine.store.taps()[-1]["tap"] == "missed" and engine.store.taps()[-1]["guess"]["play_id"] == "p9"


def test_missed_ignores_old_and_small_plays():
    class G:
        matchup = "A @ B"
        recent_plays = [Play(id="old", kind="td", scoring=True, seen_at=0.0),
                        Play(id="small", kind="rush", seen_at=995.0)]
    assert phone_v2.miss_candidates({"g": G()}, None, now=1000.0) == []


def test_priority_sliders_save_clamped_into_l2(client, engine):
    _on(engine)
    r = client.post("/api/v2/priority", json={"bets_weight": 9, "switch_frequency": 0.7, "reels": False}).json()
    assert r["ok"] and r["prefs"]["bets_weight"] == 2.0
    p = client.get("/api/v2/priority").json()["prefs"]
    assert p["bets_weight"] == 2.0 and p["switch_frequency"] == 0.7 and p["reels"] is False
    assert client.post("/api/v2/priority", json={"bets_weight": "lots"}).json()["ok"] is False
    assert client.post("/api/v2/priority", json={}).json()["ok"] is False
