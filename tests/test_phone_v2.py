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


def test_vote_is_tied_to_one_switch_and_feeds_suggestions(client, engine):
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
    # eight 👎 on crunch switches -> a "less" suggestion, never applied
    for _ in range(8):
        client.post("/api/v2/vote", json={"key": sw[0]["key"], "vote": -1})
    p = client.get("/api/v2/priority").json()
    sugg = {s["term"]: s for s in p["suggestions"]}
    assert sugg["crunch"]["suggest"] == "less" and sugg["crunch"]["applied"] is False
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


def test_night_view_segments_events_and_playbooks():
    sw = [{"ts": 100.0, "to_game": "a", "kind": "better", "reason": "Crunch time playbook=CRUNCH-02", "ok": 1, "dry_run": 0},
          {"ts": 400.0, "to_game": "b", "kind": "break_visit", "reason": "a in commercial", "ok": 0, "dry_run": 0}]
    dec = [{"ts": 150.0, "action": "stay", "game": "a", "kind": "", "reason": "best on", "gate": ""},
           {"ts": 200.0, "action": "hold", "game": "a", "kind": "", "reason": "wait for whistle playbook=SAFE-07", "gate": "g"},
           {"ts": 400.0, "action": "switch", "game": "b", "kind": "", "reason": "dup", "gate": ""}]
    taps = [{"ts": 420.0, "game": "b", "tap": "down", "reason": "a in commercial", "switch": "400.000|b"},
            {"ts": 500.0, "game": "a", "tap": "missed", "reason": None, "switch": None}]
    v = phone_v2.night_view(sw, dec, taps, lambda g: g.upper(), end=1000.0)
    assert [(s["matchup"], s["t1"] - s["t0"]) for s in v["segments"]] == [("A", 300.0), ("B", 600.0)]
    assert [e["type"] for e in v["events"]] == ["switch", "decision", "switch", "tap", "tap"]
    assert v["summary"]["playbooks"] == {"CRUNCH-02": 1, "SAFE-07": 1}
    assert v["summary"]["taps"] == {"up": 0, "down": 1, "missed": 1} and v["summary"]["failed"] == 1
    assert v["summary"]["on_air"][0] == {"matchup": "B", "s": 600}


def test_night_route_reads_tonight(client, engine):
    assert client.get("/api/v2/night").status_code == 404
    _on(engine)
    gid = next(iter(engine.merger.games))
    _switch(engine, gid, reason="Crunch time playbook=CRUNCH-02")
    r = client.get("/api/v2/night").json()
    assert r["enabled"] and r["summary"]["switches"] >= 1 and r["day"] in r["nights"]
    assert r["events"][-1]["playbooks"] == ["CRUNCH-02"]
    assert client.get("/api/v2/night?day=2026-13-01").json()["ok"] is False
    assert phone_v2.night_of(phone_v2.night_window(phone_v2.date(2026, 11, 3))[0] + 20 * 3600).isoformat() == "2026-11-03"
