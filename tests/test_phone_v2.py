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


def _g(gid, league, home, away, start, phase="pre", period=None, clock=None):
    from redzone.models import GameState, League, Phase, Team
    return GameState(league=League(league), game_id=gid, home=Team(home), away=Team(away),
                     phase=Phase(phase), start_time=start, period=period, clock_s=clock)


def test_slate_preview_windows_and_crunch_clashes():
    from redzone.noggin.priority import HardRules
    rules = HardRules({"nba": frozenset({"WSH"})}, ("nfl", "cfb", "nba", "cbb", "wcbb", "nhl", "mlb"))
    now = 0.0
    games = {"nba1": _g("nba1", "nba", "WSH", "BOS", 3600.0),             # crunch 3600+135m-25m .. +135m
             "nfl1": _g("nfl1", "nfl", "KC", "DAL", 3600.0 - 3300.0),     # ends 300+190m; crunch last 45 min
             "nhl1": _g("nhl1", "nhl", "PIT", "NYR", 0.0, phase="live", period=3, clock=600.0),
             "done": _g("done", "nhl", "A", "B", 0.0, phase="post")}
    v = phone_v2.slate_preview(games, rules, now)
    by = {r["game_id"]: r for r in v["games"]}
    assert "done" not in by and v["yours"] == 1
    assert by["nba1"]["end"] - by["nba1"]["start"] == 135 * 60
    assert by["nhl1"]["live"] and abs(by["nhl1"]["end"] - 150 * 60 * (1 - 50 / 60)) < 1
    clash = next(c for c in v["clashes"] if set(c["games"]) == {"nba1", "nfl1"})
    assert clash["games"][0] == "nba1" and clash["why"] == "your team"   # your team beats NFL in crunch


def test_progress_by_league():
    assert phone_v2.progress(_g("x", "nba", "A", "B", 0, "live", period=4, clock=0.0)) == 1.0
    assert phone_v2.progress(_g("x", "nfl", "A", "B", 0, "live", period=1, clock=900.0)) == 0.0
    assert phone_v2.progress(_g("x", "mlb", "A", "B", 0, "live", period=5)) == 4 / 9


def test_slate_route(client, engine):
    assert client.get("/api/v2/slate").status_code == 404
    _on(engine)
    r = client.get("/api/v2/slate").json()
    assert r["enabled"] and isinstance(r["games"], list) and isinstance(r["clashes"], list)


def test_oracle_overlay_agreement_and_markers(tmp_path):
    segs = [{"t0": 0.0, "t1": 100.0, "game": "a", "matchup": "A"}, {"t0": 100.0, "t1": 200.0, "game": "b", "matchup": "B"}]
    f = tmp_path / "o.json"
    f.write_text(json.dumps({"objective": "combined", "switches": [{"t": 50.0, "to": "b"}, {"t": 0.0, "to": "a"}]}))
    o = phone_v2.read_oracle(f)
    assert [x["t"] for x in o["switches"]] == [0.0, 50.0]                  # sorted
    moments = [{"ts": 60.0, "game": "b", "kind": "dunk", "seen": False}, {"ts": 150.0, "game": "b", "kind": "3pt", "seen": True},
               {"ts": 500.0, "game": "b", "kind": "late", "seen": True}]   # after the end: dropped
    v = phone_v2.oracle_overlay(segs, o, moments, lambda g: g.upper(), end=200.0)
    assert v["oracle"]["agree_pct"] == 75.0                                  # 50 s of a + 100 s of b, of 200
    assert [(s["game"], s["t1"]) for s in v["oracle"]["segments"]] == [("a", 50.0), ("b", 200.0)]
    assert v["big"] == {"plays": 2, "seen": 1, "missed": 1}
    assert v["markers"][0] == {"ts": 60.0, "game": "b", "matchup": "B", "kind": "dunk", "label": "", "seen": False, "on": "A"}
    none = phone_v2.oracle_overlay(segs, None, [], str, end=200.0)
    assert none["oracle"] is None and none["markers"] == []
    f.write_text("{not json")
    assert phone_v2.read_oracle(f) is None and phone_v2.read_oracle(tmp_path / "missing.json") is None


def test_night_route_carries_the_overlay(client, engine, tmp_path, monkeypatch):
    _on(engine)
    gid = next(iter(engine.merger.games))
    _switch(engine, gid)
    day = phone_v2.night_of(time.time()).isoformat()
    (tmp_path / "oracle").mkdir()
    (tmp_path / "oracle" / f"{day}.json").write_text(json.dumps({"switches": [{"t": time.time() - 5, "to": gid}]}))
    (tmp_path / "m.jsonl").write_text(json.dumps({"ts": time.time(), "game": gid, "kind": "td", "seen": False}) + "\n")
    monkeypatch.setattr(phone_v2, "ORACLE_DIR", tmp_path / "oracle")
    monkeypatch.setattr(phone_v2, "MOMENTS", tmp_path / "m.jsonl")
    r = client.get("/api/v2/night").json()
    assert r["oracle"]["objective"] == "combined" and r["oracle"]["segments"][0]["game"] == gid
    assert r["big"]["missed"] == 1 and r["markers"][0]["on"]


def test_trend_lines_match_the_harness():
    from scripts.northstar import META
    for key, _label, bar, d in phone_v2.TREND_LINES:
        assert key in META and META[key]["bar"] == bar and META[key]["dir"] == d, key


def test_trend_series_and_gap(tmp_path):
    rows = [{"split": "dev", "ts": "t1", "commit": "a", "values": {"mid_play": 3.0, "landings_dead": 96.0},
             "status": {"mid_play": "FAIL", "landings_dead": "FAIL"}},
            {"split": "locked", "ts": "t1", "commit": "a", "values": {"mid_play": 9.0}},
            {"split": "dev", "ts": "t2", "commit": "b", "values": {"mid_play": 1.5, "landings_dead": 99.0},
             "status": {"mid_play": "FAIL", "landings_dead": "PASS"}}]
    v = phone_v2.trend(rows, "dev")
    by = {x["key"]: x for x in v["lines"]}
    assert v["runs"] == 2 and v["commit"] == "b" and v["passing"] == 1
    assert by["mid_play"]["better"] == 1.5 and by["mid_play"]["gap"] == 1.5            # lower is better
    assert by["landings_dead"]["better"] == 3.0 and by["landings_dead"]["gap"] == 0.0
    assert by["pct_of_oracle"]["latest"] is None and by["pct_of_oracle"]["gap"] is None
    assert phone_v2.trend(rows, "locked")["lines"][0]["latest"] == 9.0
    f = tmp_path / "h.jsonl"
    f.write_text(json.dumps(rows[0]) + "\nbroken\n")
    assert len(phone_v2.read_history(f)) == 1 and phone_v2.read_history(tmp_path / "none") == []


def test_trend_route(client, engine):
    assert client.get("/api/v2/trend").status_code == 404
    _on(engine)
    r = client.get("/api/v2/trend").json()                 # the real docs/northstar-history.jsonl
    assert r["enabled"] and r["split"] == "dev" and len(r["lines"]) == len(phone_v2.TREND_LINES)
    assert client.get("/api/v2/trend?split=x").json()["ok"] is False
