"""Phone v2 (flag phone.v2, off): reason chips per switch, 👍/👎 tied to one switch,
"I missed that", and the Priority L2 sliders. None of it touches the decider or the TV."""
import json
import time

import pytest

from test_app import client, engine  # noqa: F401  (fixtures)

from redzone.models import Play
from redzone.web import phone_v2


@pytest.fixture(autouse=True)
def _tmp_reel_requests(tmp_path, monkeypatch):
    monkeypatch.setattr(phone_v2, "REEL_REQUESTS", tmp_path / "reel_requests.jsonl")


def _on(engine):
    engine.cfg.update({"phone": {"v2": True}})


def _switch(engine, to, reason="Crunch time: UNC down 2, red zone"):
    engine.store._x("INSERT INTO switches (ts, frm, to_game, target, kind, reason, dry_run, ok, detail) "
                    "VALUES (?,?,?,?,?,?,?,?,?)", (time.time(), None, to, "a", "auto", reason, 1, 1, "{}"))


def test_off_by_default_and_every_route_refuses(client, engine):
    assert client.get("/api/v2/status").json() == {"enabled": False, "commit": False, "orient": False, "worth": True,
                                                     "follow": False, "last_night": False}
    assert client.get("/api/v2/worth").status_code == 200        # EXP-040 is ON by default from Nov 5 (works with v2 off)
    engine.cfg.update({"phone": {"worth": False}})
    assert client.get("/api/v2/worth").status_code == 404 and client.post("/api/v2/worth", json={"rating": 4}).status_code == 404
    assert client.get("/api/v2/switches").status_code == 404
    assert client.post("/api/v2/vote", json={"key": "x", "vote": 1}).status_code == 404
    assert client.post("/api/v2/missed", json={}).status_code == 404
    assert client.get("/api/v2/priority").status_code == 404
    assert client.post("/api/v2/priority", json={"bets_weight": 2}).status_code == 404
    assert client.get("/api/v2/follow").status_code == 404
    assert client.get("/api/v2/lastnight").status_code == 404


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
    req = r["reel_request"]                                   # record only: reels are off by default
    assert req["play_id"] == "p9" and req["state"] == "recorded_reels_off" and req["due_by"] - req["ts"] == 180.0
    assert "reels are off" in r["message"]
    assert json.loads(phone_v2.REEL_REQUESTS.read_text().splitlines()[-1])["game_id"] == gids[1]


def test_reel_request_shape():
    g = {"game_id": "g", "matchup": "A @ B", "play_id": "p", "kind": "td", "text": "x" * 300, "ago_s": 40}
    r = phone_v2.reel_request(g, 1000.0, reel_play=True)
    assert r["state"] == "queued" and r["due_by"] == 1180.0 and len(r["text"]) == 160 and r["play_ago_s"] == 40
    assert phone_v2.reel_request(None, 1000.0, True) is None


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


def test_clash_alert_only_for_your_team_soon():
    slate = {"games": [{"game_id": "w", "yours": True}, {"game_id": "x", "yours": False}, {"game_id": "y", "yours": False}],
             "clashes": [{"t0": 100.0, "t1": 900.0, "games": ["x", "y"], "on_tv": "X", "second": ["Y"], "why": "league order"},
                         {"t0": 1000.0, "t1": 1800.0, "games": ["w", "x"], "on_tv": "W", "second": ["X"], "why": "your team"}]}
    a = phone_v2.clash_alert(slate, now=0.0)
    assert a["on_tv"] == "W" and a["in_s"] == 1000 and a["now"] is False      # not the x/y clash
    assert phone_v2.clash_alert(slate, now=1200.0)["now"] is True
    assert phone_v2.clash_alert(slate, now=0.0, ahead=500.0) is None
    assert phone_v2.clash_alert(slate, now=2000.0) is None


def test_slate_route_carries_the_alert(client, engine):
    _on(engine)
    assert "alert" in client.get("/api/v2/slate").json()


def test_watch_values_learned_field_or_stub():
    from redzone.models import GameState, League, Phase as P, Team
    from redzone.scoring.engine import Scored

    def sc(gid, lg, score, factor="", reason="", crunch=False, is_break=False):
        return Scored(gid, League(lg), score, score, 0.0, 0.0, 0.0, reason, [], is_break, None, None, 0.0,
                      crunch=crunch, factor=factor)
    def gs(gid, a, h, ph):
        return GameState(league=League.NBA, game_id=gid, home=Team(h), away=Team(a), phase=ph)
    games = {"a": gs("a", "A", "B", P.LIVE), "b": gs("b", "C", "D", P.LIVE), "z": gs("z", "Y", "Z", P.POST)}
    old = {"a": sc("a", "nba", 140.0, factor="Tied late", reason="r", crunch=True),
           "b": sc("b", "nhl", -3.0, reason="Break", is_break=True), "z": sc("z", "nhl", 50.0)}
    v = phone_v2.watch_values(old, games)
    assert v["source"] == "stub" and [(r["game_id"], r["wv"], r["why"]) for r in v["games"]] == [("a", 100, "Tied late"), ("b", 0, "Break")]
    old["a"].watch_value, old["a"].watch_why = 63.4, "Comeback brewing"
    old["b"].watch_value = 12
    v = phone_v2.watch_values(old, games)
    assert v["source"] == "learned" and v["games"][0] == {"game_id": "a", "matchup": "A @ B", "league": "nba", "wv": 63,
                                                         "why": "Comeback brewing", "crunch_state": "entering", "stakes_line": "",
                                                         "stakes_rough": False,
                                                         "crunch": True, "is_break": False, "source": "learned", "context": None}
    assert v["games"][1]["why"] == "Break"                                             # no watch_why: the reason
    assert phone_v2.watch_values({}, {}) == {"games": [], "source": None}
    old["a"].crunch_now = True
    assert phone_v2.watch_values(old, games)["games"][0]["crunch_state"] == "in"       # stub from the flags
    old["a"].crunch_state = "leaving"                                                  # lane 1's model wins
    assert phone_v2.watch_values(old, games)["games"][0]["crunch_state"] == "leaving"
    old["a"].stakes = 0.7
    g0 = phone_v2.watch_values(old, games)["games"][0]
    assert g0["stakes_line"] == "Big stakes" and g0["stakes_rough"] is True                       # stub
    old["a"].stakes_line = "AFC 1-seed race, top-5 matchup"                                       # lane 1 wins
    g0 = phone_v2.watch_values(old, games)["games"][0]
    assert g0["stakes_line"] == "AFC 1-seed race, top-5 matchup" and g0["stakes_rough"] is False
    old["b"].crunch_state = "bogus"
    assert phone_v2.watch_values(old, games)["games"][1]["crunch_state"] is None


def test_watch_route(client, engine):
    assert client.get("/api/v2/watch").status_code == 404
    _on(engine)
    r = client.get("/api/v2/watch").json()
    assert r["enabled"] and isinstance(r["games"], list)


def test_plan_marks_catchups_and_interleaves():
    sw = [{"ts": 1.0, "to_game": "a", "kind": "better", "reason": "x", "ok": 1},
          {"ts": 2.0, "to_game": "b", "kind": "replay", "reason": "catch-up plan=crunch-overlap", "ok": 1},
          {"ts": 3.0, "to_game": "reel", "kind": "better", "reason": "reel", "ok": 0, "dry_run": 1},
          {"ts": 4.0, "to_game": "c", "kind": "break_visit", "reason": "a in commercial", "ok": 0},
          {"ts": 5.0, "to_game": "a", "kind": "return", "reason": "back", "ok": 1}]
    m = phone_v2.plan_marks(sw, lambda g: g.upper())
    assert [(x["type"], x["matchup"]) for x in m] == [("catchup", "B"), ("catchup", "Catch-up reel"), ("interleave", "C"), ("interleave", "A")]
    assert m[0]["plan"] == "crunch-overlap" and m[1]["ok"] is True and m[2]["ok"] is False


def test_night_route_carries_plan_marks(client, engine):
    _on(engine)
    gid = next(iter(engine.merger.games))
    engine.store._x("INSERT INTO switches (ts, frm, to_game, target, kind, reason, dry_run, ok, detail) "
                    "VALUES (?,?,?,?,?,?,?,?,?)", (time.time(), None, gid, "a", "replay", "catch-up", 0, 1, "{}"))
    r = client.get("/api/v2/night").json()
    assert r["plan_counts"]["catchup"] >= 1 and r["plan_marks"][-1]["type"] == "catchup"


def test_provenance_from_trace_fields_or_stub():
    trace = {"trace": {"id": "s1", "decided_at": 100.0, "kind": "crunch", "steps": [["safe_done", 3.0], ["flip_start", 3.2]],
                       "spans": {"safe_wait": 3.0}, "path": "flip", "picture_confirmed": True, "picture_at": 105.0,
                       "enter_delay": {"mu": 44.0, "sigma": 2.0}}}
    row = {"ts": 100.0, "frm": "a", "to_game": "b", "kind": "crunch", "reason": "Crunch time playbook=CRUNCH-02 plan=overlap",
           "ok": 1, "dry_run": 0, "detail": json.dumps(trace)}
    v = phone_v2.provenance_view(row, lambda g: g.upper())
    assert v["source"] == "stub" and v["key"] == "100.000|b" and v["to"] == "B"
    assert [c["stage"] for c in v["chain"]] == ["decided", "guarded", "command", "picture"]
    assert [p["module"] for p in v["parts"]] == ["decider", "playbook", "planner", "sync", "guard"]
    trace["trace"]["provenance"] = {"planner": "overlap", "watch_value": {"to": 88}, "extra": 1}
    row["detail"] = json.dumps(trace)
    v = phone_v2.provenance_view(row, str)
    assert v["source"] == "trace" and [p["module"] for p in v["parts"]] == ["watch_value", "planner", "extra"]
    row["detail"] = "{}"
    assert phone_v2.provenance_view(row, str)["chain"] == [] and phone_v2.provenance_view(row, str)["has_trace"] is False


def test_provenance_route(client, engine):
    assert client.get("/api/v2/provenance?key=1|a").status_code == 404
    _on(engine)
    gid = next(iter(engine.merger.games))
    _switch(engine, gid)
    sw = client.get("/api/v2/switches").json()["switches"][0]
    r = client.get(f"/api/v2/provenance?key={sw['key']}").json()
    assert r["ok"] and r["source"] == "stub" and r["parts"][0]["module"] == "decider"
    assert client.get("/api/v2/provenance?key=nope").json()["ok"] is False
    assert client.get("/api/v2/provenance?key=1.0|zzz").json()["ok"] is False


def test_held_view_holding_coming_back_and_catchup():
    from redzone.decider.decider import Missed
    q = [Missed("c", "p1", "Goal", "goal", t=900.0, value=80, replayed=True),
         Missed("c", "p2", "Ovechkin one-timer", "goal", t=950.0, value=90, mine=True, due_s=180.0)]
    v = phone_v2.held_view("b", True, 84.04, "b", 1240.0, q, "a", str.upper, now=1000.0)
    assert v["held"] == {"game_id": "b", "matchup": "B", "ready": True, "behind_s": 84.0}
    assert v["back"]["in_s"] == 240 and v["back"]["text"] == "back to B within 4 min"
    assert v["catchup"]["label"] == "Ovechkin one-timer" and v["catchup"]["due_in_s"] == 130 and v["queued"] == 1
    assert phone_v2.held_view(None, False, 0.0, None, None, [], "a", str, 0.0) is None
    assert phone_v2.held_view(None, False, 0.0, "a", None, [], "a", str, 0.0)["back"] is None   # already home


def test_held_route(client, engine):
    assert client.get("/api/v2/held").status_code == 404
    _on(engine)
    r = client.get("/api/v2/held").json()
    assert r["enabled"] and "view" in r


def test_ledger_view_pending_then_folded(tmp_path):
    from redzone.web import ledger as DL
    v = DL.view(DL.read(tmp_path / "missing.json"))
    assert v["pending"] and [r["league"] for r in v["rows"]][:3] == ["nfl", "cfb", "nba"]
    assert v["totals"]["pct_of_target"] == 0.0 and len(v["nfm"]["milestones"]) == len(DL.DEFAULT_MILESTONES)
    assert "PENDING" in DL.table(v)
    man = {"nfl": {"source": "nflverse", "license": "CC-BY", "seasons": "2002-2025", "windows": 1200,
                   "splits": {"train": {"games": 90, "nights": 30}, "test": {"games": 10, "nights": 4}}}, "bad": 3}
    led = DL.fold_manifest({"leagues": {"nfl": {"real_tokens": 4e8}}}, man, "2026-11-04T22:00Z")
    nfl = led["leagues"]["nfl"]
    assert (nfl["games"], nfl["nights"], nfl["windows"], nfl["real_tokens"]) == (100, 34, 1200, 4e8)
    led["leagues"]["xfl"] = {"synthetic_tokens": 1e8}
    led["nfm"] = {"milestones": [{"name": "pretrain", "status": "running"}, {"name": "x", "status": "weird"}, {"bad": 1}]}
    v = DL.view(led)
    assert not v["pending"] and v["rows"][-1]["league"] == "xfl" and v["totals"]["pct_of_target"] == 50.0
    assert [m["status"] for m in v["nfm"]["milestones"]] == ["running", "next"] and v["updated"] == "2026-11-04T22:00Z"
    assert "| NFL | nflverse (CC-BY) | 400.0M |" in DL.table(v)


def test_ledger_script_folds_a_manifest(tmp_path, capsys):
    import json
    import scripts.data_ledger as cli
    led, man = tmp_path / "led.json", tmp_path / "manifest.json"
    man.write_text(json.dumps({"nba": {"source": "nba_api", "windows": 7, "splits": {}}}))
    assert cli.main(["--ledger", str(led), "--manifest", str(man)]) == 0
    assert json.loads(led.read_text())["leagues"]["nba"]["windows"] == 7 and "| NBA | nba_api |" in capsys.readouterr().out
    assert cli.main(["--ledger", str(led), "--manifest", str(tmp_path / "nope.json")]) == 2


def test_ledger_route(client, engine, tmp_path, monkeypatch):
    assert client.get("/api/v2/ledger").status_code == 404
    _on(engine)
    monkeypatch.setattr(phone_v2.ledger_mod, "LEDGER_FILE", tmp_path / "none.json")
    r = client.get("/api/v2/ledger").json()
    assert r["enabled"] and r["pending"] and r["nfm"]["done"] == 0


def test_provenance_orders_lane3_control_last():
    assert phone_v2.PROV_ORDER[0] == "decider" and phone_v2.PROV_ORDER[-1] == "control"   # lane 3 #173 keys


def test_ledger_folds_the_kaggle_factory_manifest():
    from redzone.web import ledger as DL
    man = {"total_tokens": 3_000_000,
           "leagues": {"nba": {"seasons": {"2023": {"tokens": {"games": 60}}, "2024": {"tokens": {"games": 40}}}}},
           "ledger": {"nba": {"tokens_real": 3_000_000, "sources": {"nba_cdn": 1_000_000, "nba_api": 2_000_000},
                              "selfplay_rows_synthetic": 500, "moments_rows_real": 9000, "crunch_event_share_pct": 12.5,
                              "ot_games": 5, "comeback_games": 10, "blowout_games": 25, "no_wall_games": 0}}}
    keep = {"nfm": {"milestones": [{"name": "pretrain", "status": "running"}]}}
    led = DL.fold_manifest(keep, man, "t")
    nba = led["leagues"]["nba"]
    assert nba["source"] == "nba_api, nba_cdn" and nba["seasons"] == "2023-2024" and nba["games"] == 100
    assert (nba["real_tokens"], nba["synthetic_tokens"], nba["windows"]) == (3e6, 0, 9000)
    assert (nba["crunch_share"], nba["ot_share"], nba["comeback_share"], nba["blowout_share"]) == (0.125, 0.05, 0.1, 0.25)
    assert led["nfm"] == keep["nfm"] and led["total_tokens"] == 3_000_000
    row = next(r for r in DL.view(led)["rows"] if r["league"] == "nba")
    assert not row["pending"] and "| NBA | nba_api, nba_cdn | 3.0M | 0 | 9,000 | 12% | 5% / 10% / 25% |" in DL.table(DL.view(led))


def test_capture_view_counts_provenance_autolog_held_and_report():
    import json
    prov = {"trace": {"id": "t", "provenance": {"guard": {"ok": True}, "decider": {"kind": "better"}, "zz": 1}}}
    sws = [{"ts": 1.0, "detail": json.dumps(prov)}, {"ts": 2.0, "detail": json.dumps({"trace": {"id": "u"}})},
           {"ts": 3.0, "detail": "{}"}]
    summary = {"rows": {"traces.jsonl": 2, "bad": "x"}, "cuts_graded": 2, "entries_mid_play": 0, "switch_p95_s": 2.2}
    v = phone_v2.capture_view("2026-11-04", sws, summary, [{"kind": "drift"}, {"kind": "recovered"}, {"kind": "drift"}],
                              "2026-11-04.md", {"HDMI2": 62.46})
    assert (v["switches"], v["with_trace"], v["with_provenance"]) == (3, 2, 1)
    assert [k["module"] for k in v["provenance_keys"]] == ["decider", "guard", "zz"]        # PROV_ORDER, then the rest
    assert v["autolog"]["rows"] == {"traces.jsonl": 2} and v["autolog"]["entries_mid_play"] == 0
    assert v["held"] == {"now": {"HDMI2": 62.5}, "log": {"drift": 2, "recovered": 1}}
    assert [c["ok"] for c in v["checks"]] == [True, False, True] and v["gold"] is False
    empty = phone_v2.capture_view("2026-11-05", [], None, [], None)
    assert empty["autolog"] is None and empty["gold"] is False and empty["held"]["now"] == {}


def test_capture_routes(client, engine, tmp_path, monkeypatch):
    assert client.get("/api/v2/capture").status_code == 404
    _on(engine)
    monkeypatch.setattr(phone_v2, "LIVE_NIGHTS", tmp_path / "ln")
    monkeypatch.setattr(phone_v2, "REPORTS", (tmp_path / "rep",))
    monkeypatch.setattr(phone_v2, "BUFFER_LOG", tmp_path / "buffer.jsonl")
    gid = next(iter(engine.merger.games))
    _switch(engine, gid)
    r = client.get("/api/v2/capture").json()
    assert r["enabled"] and r["switches"] == 1 and r["report"] is None and r["autolog"] is None
    night = r["night"]
    (tmp_path / "ln" / night).mkdir(parents=True)
    (tmp_path / "ln" / night / "summary.json").write_text('{"rows": {"traces.jsonl": 1}, "cuts_graded": 1}')
    (tmp_path / "rep").mkdir()
    (tmp_path / "rep" / f"{night}.md").write_text("# report")
    r = client.get(f"/api/v2/capture?day={night}").json()
    assert r["autolog"]["cuts_graded"] == 1 and r["report"] == f"{night}.md"
    assert client.get(f"/api/v2/capture/report?day={night}").json() == {"ok": True, "day": night, "text": "# report"}
    assert client.get("/api/v2/capture?day=x").json()["ok"] is False
    assert client.get("/api/v2/capture/report?day=2020-01-01").json()["ok"] is False


def test_crunch_timeline_graded_and_from_provenance(tmp_path):
    import json
    f = tmp_path / "c.json"
    f.write_text(json.dumps({"tol_s": 60, "games": {
        "a": {"learned": [[100, 400], [500, 900]], "oracle": [[130, 950]]},       # entry -30 ok, exit -50 ok
        "b": {"learned": [[100, 200]], "oracle": [[100, 500]]},                    # exit -300: a miss
        "c": {"learned": [], "oracle": [[0, 10]]}, "d": {"learned": [[5, 1]]},    # never on; a bad span drops
        "e": {"learned": [[7, 7]], "oracle": [[7, 7]]}}}))                          # one-row span (t0 == t1) stays
    c = phone_v2.read_crunch(f)
    assert c["games"]["d"]["learned"] == [] and phone_v2.read_crunch(tmp_path / "none.json") is None
    assert c["games"]["e"]["learned"] == [[7.0, 7.0]]
    v = phone_v2.crunch_timeline(c, [], str.upper)
    by = {g["game"]: g for g in v["games"]}
    assert (by["a"]["entry_s"], by["a"]["exit_s"], by["a"]["exit_ok"]) == (-30.0, -50.0, True)
    assert by["b"]["exit_ok"] is False and by["c"]["exit_ok"] is False and "exit_ok" not in by["d"]
    assert (v["source"], v["exits"], v["exits_ok"], v["entries_ok"]) == ("graded", 4, 2, 3)
    assert v["games"][0]["game"] in ("b", "c")                                     # misses first
    sw = [{"ts": 10.0, "to_game": "x", "frm": "y",
           "detail": json.dumps({"trace": {"id": "t", "provenance": {"crunch_state": {"to": "in", "from": "leaving"}}}})},
          {"ts": 20.0, "to_game": "y", "frm": None, "detail": json.dumps({"trace": {"provenance": {"crunch_state": "entering"}}})},
          {"ts": 30.0, "to_game": "z", "detail": json.dumps({"trace": {"provenance": {"crunch_state": "bogus"}}})}]
    v = phone_v2.crunch_timeline(None, sw, str.upper)
    pts = {g["game"]: g["points"] for g in v["games"]}
    assert v["source"] == "provenance" and pts == {"x": [{"ts": 10.0, "state": "in"}],
                                                   "y": [{"ts": 10.0, "state": "leaving"}, {"ts": 20.0, "state": "entering"}]}
    assert phone_v2.crunch_timeline(None, [], str)["source"] is None


def test_slate_watch_values_only_when_on_and_learned():
    from redzone.scoring.engine import Scored
    from redzone.models import League
    def sc(gid, wv=None, why="", rise=None):
        s = Scored(gid, League.NBA, 50.0, 50.0, 0.0, 0.0, 0.0, "", [], False, None, None, 0.0)
        s.watch_value, s.watch_why, s.watch_rising = wv, why, rise
        return s
    rows = lambda: [{"game_id": "a", "live": True}, {"game_id": "b", "live": True}, {"game_id": "c", "live": False},
                    {"game_id": "d", "live": True}]
    scored = {"a": sc("a", 71.6, "Tied late", 3.44), "b": sc("b", 40.0, "", -0.5), "c": sc("c", 90.0), "d": sc("d")}
    assert all("wv" not in r for r in phone_v2.slate_watch(rows(), scored, on=False))     # flag off: hidden
    by = {r["game_id"]: r for r in phone_v2.slate_watch(rows(), scored, on=True)}
    assert by["a"]["wv"] == {"value": 72, "why": "Tied late", "rising": 3.4, "arrow": "up"}
    assert by["b"]["wv"]["arrow"] == "" and "wv" not in by["c"] and "wv" not in by["d"]   # not live / no model
    scored["b"].watch_rising = -2.0
    assert phone_v2.slate_watch(rows(), scored, on=True)[1]["wv"]["arrow"] == "down"


def test_catchup_queue_holds_and_waiting():
    from types import SimpleNamespace as NS
    from redzone.decider.decider import Missed
    rows = [{"ts": 100.0, "kind": "left_held", "target": "H2", "behind_s": 0.0},
            {"ts": 160.0, "kind": "left_resumed", "target": "H2", "behind_s": 60.0},
            {"ts": 200.0, "kind": "lost", "target": "H1"},                               # STK-06 row: not a left hold
            {"ts": 300.0, "kind": "left_expired", "target": "H9"},                       # never held: ignored
            {"ts": 400.0, "kind": "left_held", "target": "H2", "behind_s": 5.0}]
    q = [Missed("b", "p1", "Goal", "goal", 380.0, 50.0), Missed("a", "p2", "TD", "td", 390.0, 70.0, mine=True, due_s=180.0),
         Missed("a", "p3", "old", "td", 10.0, 70.0, replayed=True), Missed("c", "p4", "FG", "fg", 395.0, 30.0, must=True)]
    sc = {"a": NS(is_break=True), "b": NS(is_break=False)}
    v = phone_v2.catchup_queue(rows, {"H2": "a"}, "H2", True, {"H2": 30.04}, q, sc, str.upper, now=430.0)
    assert v["hidden"] == {"game_id": "a", "matchup": "A", "target": "H2", "ready": True, "behind_s": 30.0, "dead_now": True}
    first, second = v["holds"]                                                             # newest first
    assert (first["open"], first["age_s"], first["end"]) == (True, 35.0, None)
    assert (second["end"], second["age_s"], second["open"]) == ("resumed", 60.0, False)
    assert [i["game_id"] for i in v["queue"]] == ["c", "a", "b"]                          # must, then yours, then oldest
    assert v["queue"][1]["due_in_s"] == 140 and v["queue"][0]["dead_now"] is None and v["queue"][2]["dead_now"] is False
    assert v["counts"] == {"held": 2, "resumed": 1, "expired": 0, "lost": 0, "queued": 3, "yours": 1}
    assert phone_v2.catchup_queue([], {}, None, False, {}, [], {}, str, 0.0)["hidden"] is None


def test_catchup_route(client, engine, tmp_path, monkeypatch):
    assert client.get("/api/v2/catchup").status_code == 404
    _on(engine)
    monkeypatch.setattr(phone_v2, "BUFFER_LOG", tmp_path / "none.jsonl")
    r = client.get("/api/v2/catchup").json()
    assert r["enabled"] and r["counts"]["held"] == 0 and isinstance(r["queue"], list) and "on_screen" in r


def test_night_scorecard_lines_and_gate():
    nights = [("2026-11-04", {"landed_mid_play": 0, "high_leverage_leaves": 0, "dead_air_pct": 22.5}, {"entries_mid_play": 3}),
              ("2026-11-02", None, {"entries_mid_play": 2}),
              ("2026-11-03", {"mid_play_entries": 1, "plays_missed_entry": 4}, None),
              ("2026-11-01", None, None)]                                                  # nothing measured: dropped
    v = phone_v2.night_scorecard(nights)
    assert v["nights"] == ["2026-11-02", "2026-11-03", "2026-11-04"] and v["last"] == "2026-11-04"
    by = {ln["key"]: ln for ln in v["lines"]}
    assert [p["v"] for p in by["mid_play_entries"]["points"]] == [2.0, 1.0, 0.0]          # report wins over auto-log
    assert by["mid_play_entries"]["status"] == "PASS" and by["mid_play_entries"]["nights_at_bar"] == 1
    assert by["plays_missed_entry"]["latest"] is None and by["plays_missed_entry"]["status"] is None
    assert by["dead_air_pct"]["status"] == "OVER" and by["high_leverage_leaves"]["measured"] == 1
    assert v["gate"] is True
    nights[0][1]["high_leverage_leaves"] = 1
    assert phone_v2.night_scorecard(nights)["gate"] is False
    assert phone_v2.night_scorecard([])["gate"] is None


def test_scorecard_route(client, engine, tmp_path, monkeypatch):
    import json
    assert client.get("/api/v2/scorecard").status_code == 404
    _on(engine)
    monkeypatch.setattr(phone_v2, "REPORTS", (tmp_path / "rep",))
    monkeypatch.setattr(phone_v2, "LIVE_NIGHTS", tmp_path / "ln")
    assert client.get("/api/v2/scorecard").json()["nights"] == []
    (tmp_path / "rep").mkdir()
    (tmp_path / "rep" / "2026-11-03.json").write_text(json.dumps({"landed_mid_play": 1}))
    (tmp_path / "ln" / "2026-11-04").mkdir(parents=True)
    (tmp_path / "ln" / "2026-11-04" / "summary.json").write_text(json.dumps({"entries_mid_play": 0}))
    r = client.get("/api/v2/scorecard?n=5").json()
    assert r["nights"] == ["2026-11-03", "2026-11-04"] and r["gate"] is True


def test_why_tag_only_over_the_threshold():
    from redzone.models import League
    from redzone.scoring.engine import Scored
    s = Scored("a", League.NBA, 65.0, 65.0, 0.0, 0.0, 0.0, "Close late", [], False, None, None, 0.0, factor="tied, 2:10 left")
    assert phone_v2.why_tag(s, 70.0) is None and phone_v2.why_tag(None, 70.0) is None    # 65 < 70
    s.score = 74.6
    assert phone_v2.why_tag(s, 70.0) == {"text": "tied, 2:10 left", "value": 75, "threshold": 70.0, "source": "stub"}
    assert phone_v2.why_tag(s, 80.0) is None                                            # the config number decides
    s.watch_value, s.watch_why = 40.0, "Quiet stretch"                                   # learned wins, and it's under
    assert phone_v2.why_tag(s, 70.0) is None
    s.watch_value = 88.0
    assert phone_v2.why_tag(s, 70.0)["text"] == "Quiet stretch" and phone_v2.why_tag(s, 70.0)["source"] == "learned"


def test_status_says_whether_commit_controls_are_on(client, engine):
    _on(engine)
    assert client.get("/api/v2/status").json() == {"enabled": True, "commit": False, "orient": False, "worth": True,
                                                     "follow": False, "last_night": False}   # phone.commit default off
    engine.cfg.update({"phone": {"commit": True}})
    assert client.get("/api/v2/status").json()["commit"] is True


def test_slate_complete_only_when_nothing_live_or_left():
    from types import SimpleNamespace as NS
    from redzone.models import League, Phase as P
    rules = phone_v2.HardRules()
    post = NS(phase=P.POST, league=League.NBA)
    assert phone_v2.slate_preview({"a": post}, rules, 0.0)["complete"] is True
    assert phone_v2.slate_preview({}, rules, 0.0)["complete"] is False                     # no games tonight at all
    live = NS(phase=P.LIVE, league=League.NBA, start_time=0.0, period=4, clock_s=60.0, matchup="X",
              home=NS(abbr="A"), away=NS(abbr="B"))
    v = phone_v2.slate_preview({"a": post, "b": live}, rules, 0.0)
    assert v["complete"] is False and v["finished"] == 1


def _late(league, home, away, period, clock, **sit):
    from redzone.models import GameState, League, Phase, Situation, Team
    return GameState(league=League(league), game_id="g", home=Team("HOM", score=home), away=Team("AWY", score=away),
                     phase=Phase.LIVE, period=period, clock_s=clock, half=sit.pop("half", None), situation=Situation(**sit))


def test_context_line_late_and_close_per_league():
    cl = phone_v2.context_line
    assert cl(_late("nfl", 20, 17, 4, 72, possession="AWY", down=2, distance=7, yards_to_goal=34,
                    timeouts_away=0)) == "AWY down 3, 1:12 left, AWY ball, 2nd & 7 at the 34, no timeouts"
    assert cl(_late("nba", 100, 100, 4, 48)) == "Tied, 0:48 left"
    assert cl(_late("nba", 98, 101, 4, 30, timeouts_home=2)) == "HOM down 3, 0:30 left, 2 timeouts"
    assert cl(_late("nhl", 2, 3, 3, 90, empty_net="HOM")) == "HOM down 1, 1:30 left, goalie pulled"
    assert cl(_late("mlb", 4, 3, 9, None, half="Bottom", outs=2, runners=(True, False, True))) == \
        "AWY down 1, bottom 9th, 2 outs, 2 on"
    assert cl(_late("cbb", 60, 62, 2, 40, timeouts_home=1)) == "HOM down 2, 0:40 left, 1 timeout"


def test_context_line_none_when_not_late_or_not_close():
    cl = phone_v2.context_line
    assert cl(None) is None
    assert cl(_late("nfl", 30, 10, 4, 60)) is None        # two scores up
    assert cl(_late("nba", 100, 99, 2, 30)) is None       # first half
    assert cl(_late("nba", 100, 99, 4, 600)) is None      # 10:00 left, not late yet
    assert cl(_late("mlb", 1, 0, 5, None)) is None        # middle innings
    assert "OT" in cl(_late("nhl", 1, 1, 4, 200))


def test_worth_view_asks_only_after_slate_complete_and_probes_weekly():
    d = phone_v2.date(2026, 11, 8)
    v = phone_v2.worth_view({}, d, False, 3, 7)
    assert v["complete"] is False and v["probe_due"] is False and v["rating"] is None
    assert phone_v2.worth_view({}, d, True, 3, 7)["probe_due"] is True                  # never probed yet
    rows = {"2026-11-04": {"night": "2026-11-04", "rating": 4, "agency": 4, "regret": False}}
    assert phone_v2.worth_view(rows, d, True, 3, 7)["probe_due"] is False               # probed 4 nights ago
    assert phone_v2.worth_view(rows, phone_v2.date(2026, 11, 11), True, 3, 7)["probe_due"] is True
    rows["2026-11-08"] = {"night": "2026-11-08", "rating": 2}
    assert phone_v2.worth_view(rows, d, True, 3, 7)["rating"] == 2


def test_worth_row_validates_and_keeps_earlier_fields():
    d = phone_v2.date(2026, 11, 8)
    assert phone_v2.worth_row({"rating": 6}, d, 1.0, None) is None
    assert phone_v2.worth_row({"rating": True}, d, 1.0, None) is None
    assert phone_v2.worth_row({"regret": "yes"}, d, 1.0, None) is None
    assert phone_v2.worth_row({}, d, 1.0, None) is None
    r = phone_v2.worth_row({"rating": 4}, d, 1.0, None)
    assert r == {"night": "2026-11-08", "ts": 1.0, "rating": 4}
    assert phone_v2.worth_row({"agency": 5, "regret": False}, d, 2.0, r) == {
        "night": "2026-11-08", "ts": 2.0, "rating": 4, "agency": 5, "regret": False}


def test_worth_routes_own_flag_works_with_v2_off(client, engine, tmp_path, monkeypatch):
    monkeypatch.setattr(phone_v2, "WORTH_LOG", tmp_path / "worth.jsonl")
    engine.cfg.update({"phone": {"worth": True}})
    assert client.get("/api/v2/status").json()["worth"] is True
    assert client.get("/api/v2/slate").status_code == 404                                # v2 still off
    v = client.get("/api/v2/worth").json()
    assert v["enabled"] is True and "complete" in v and v["rating"] is None
    assert client.post("/api/v2/worth", json={"rating": 0}).status_code == 400
    assert client.post("/api/v2/worth", json={"rating": 5}).json()["ok"] is True
    assert client.post("/api/v2/worth", json={"agency": 3}).json()["row"]["rating"] == 5
    assert client.get("/api/v2/worth").json()["rating"] == 5
    assert len(phone_v2.WORTH_LOG.read_text().splitlines()) == 2


def test_orientation_beat_per_league():
    from redzone.models import Phase as P
    ob = phone_v2.orientation
    b = ob(_late("nfl", 17, 20, 4, 72, possession="HOM", down=2, distance=7, yards_to_goal=34))
    assert b["score"] == "AWY 20 · HOM 17" and b["clock"] == "Q4 1:12" and b["fact"] == "HOM ball, 2nd & 7 at the 34"
    assert b["context"].startswith("HOM down 3")
    assert ob(_late("nhl", 1, 1, 2, 600, power_play="AWY", strength="5v4"))["fact"] == "AWY power play (5v4)"
    assert ob(_late("nhl", 1, 1, 4, 200))["clock"] == "OT 3:20"
    assert ob(_late("cbb", 30, 28, 1, 300))["clock"] == "1st half 5:00"
    m = ob(_late("mlb", 2, 2, 7, None, half="top", outs=1, runners=(False, False, False)))
    assert m["clock"] == "Top 7th" and m["fact"] == "1 out, bases empty" and m["context"] is None
    early = ob(_late("nba", 10, 8, 1, 500))
    assert early["context"] is None and early["fact"] == ""                 # early: facts only, no question
    g = _late("nba", 1, 1, 1, 1)
    g.phase = P.POST
    assert ob(g) is None and ob(None) is None


def test_orient_route_needs_both_flags(client, engine):
    _on(engine)
    assert client.get("/api/v2/orient").status_code == 404                  # phone.orient off
    engine.cfg.update({"phone": {"v2": True, "orient": True}})
    assert client.get("/api/v2/status").json()["orient"] is True
    r = client.get("/api/v2/orient?game=nope").json()
    assert r["enabled"] is True and r["beat"] is None and r["beat_s"] == 3


def test_also_close_lists_other_late_close_games_in_league_order():
    games = {"on": _late("nfl", 20, 17, 4, 60), "nhl": _late("nhl", 1, 1, 3, 90), "nba": _late("nba", 99, 100, 4, 30),
             "blow": _late("nba", 130, 90, 4, 30), "early": _late("nfl", 7, 7, 1, 600)}
    for gid, g in games.items():
        g.game_id = gid
    order = ("nfl", "cfb", "nba", "cbb", "wcbb", "nhl", "mlb")
    rows = phone_v2.also_close(games, "on", order)
    assert [r["game_id"] for r in rows] == ["nba", "nhl"]                     # NFL on screen; NBA before NHL
    assert rows[0]["context"] == "HOM down 1, 0:30 left"
    assert len(phone_v2.also_close({**games, "on": games["on"]}, None, order)) == phone_v2.ALSO_CLOSE_MAX


def test_watch_also_close_behind_its_flag(client, engine):
    _on(engine)
    assert client.get("/api/v2/watch").json()["also_close"] == []
    engine.cfg.update({"phone": {"v2": True, "also_close": True}})
    assert client.get("/api/v2/watch").json()["also_close"] == []                # no late, close games in the fixture


def test_catchup_view_spoiler_safe_decider_order_and_visible_cap():
    from redzone.decider.decider import Missed
    q = [Missed("b", "p1", "Ovechkin goal, WSH leads 3-2", "goal", 380.0, 50.0),
         Missed("a", "p2", "Allen 34-yd TD to Diggs", "td", 390.0, 70.0, mine=True, due_s=180.0),
         Missed("c", "p4", "Tucker 52-yd FG good", "fg", 395.0, 30.0, must=True)]
    v = phone_v2.catchup_queue([], {}, None, False, {}, q, {}, str.upper, now=430.0, view={"visible": 2})
    assert [i["game_id"] for i in v["queue"]] == ["b", "a"]                              # the decider's order, first 2
    assert v["more"] == 1 and v["spoiler_safe"] is True and v["counts"]["queued"] == 3 and v["counts"]["yours"] == 1
    text = json.dumps(v)
    for leak in ("Ovechkin", "Allen", "Tucker", "goal", "td", "fg", "3-2"):
        assert leak not in text, leak                                                   # nothing spoils the play
    h = phone_v2.held_view(None, False, 0.0, None, None, q, None, str.upper, 430.0, spoiler_safe=True)
    assert h["catchup"]["label"] == phone_v2.SPOILER_SAFE_LABEL and "Ovechkin" not in json.dumps(h)
    off = phone_v2.catchup_queue([], {}, None, False, {}, q, {}, str.upper, now=430.0)
    assert off["spoiler_safe"] is False and off["more"] == 0 and len(off["queue"]) == 3


def test_follow_names_are_cleaned():
    assert phone_v2.follow_names(["  Nikola  Jokic", "nikola jokic", "x", "Tatum; drop", "Bryce Harper", 3]) == \
        ["Nikola Jokic", "Bryce Harper"]                                        # trimmed, deduped, safe text only
    assert phone_v2.follow_names("Josh Allen, Ovechkin") == ["Josh Allen", "Ovechkin"]
    assert len(phone_v2.follow_names([f"Player {chr(65 + i)}" for i in range(20)])) == phone_v2.FOLLOW_MAX
    assert phone_v2.follow_names({"a": 1}) is None and phone_v2.follow_names([]) == []


def test_followed_in_finds_spotlight_leader_and_flag_without_namesakes():
    g = _late("nfl", 20, 17, 4, 60)
    g.spotlight = {"name": "Josh Allen", "short": "J. Allen", "text": "Allen 12-yd TD run"}
    g.leaders = [{"short": "N. Jokic", "stat": "38 PTS"}]
    g.player_flags = ["Ovechkin 2 G"]
    rows = {r["player"]: r for r in phone_v2.followed_in(g, ["Josh Allen", "Keenan Allen", "Nikola Jokic",
                                                              "Kevin Jokic", "Alex Ovechkin", "Allen"])}
    assert rows["Josh Allen"]["where"] == "spotlight" and rows["Josh Allen"]["text"] == "Allen 12-yd TD run"
    assert rows["Nikola Jokic"] == {"player": "Nikola Jokic", "where": "leader", "text": "N. Jokic 38 PTS"}
    assert rows["Alex Ovechkin"]["where"] == "flag"
    assert rows["Allen"]["where"] == "spotlight"                           # a surname alone matches the surname
    assert "Keenan Allen" not in rows and "Kevin Jokic" not in rows          # full name / initial must agree
    assert phone_v2.followed_in(g, []) == [] and phone_v2.followed_in(None, ["Josh Allen"]) == []


def test_follow_view_lists_live_games_on_screen_first():
    from redzone.models import Phase as P
    a, b, done = _late("nba", 99, 100, 4, 30), _late("nhl", 1, 1, 3, 90), _late("nba", 1, 1, 1, 1)
    a.leaders, b.player_flags, done.player_flags = [{"name": "Nikola Jokic"}], ["Ovechkin 2 G"], ["Jokic 40 pts"]
    done.phase = P.POST
    rows = phone_v2.follow_view({"a": a, "b": b, "done": done}, ["Nikola Jokic", "Ovechkin"], "b")
    assert [r["game_id"] for r in rows] == ["b", "a"] and rows[0]["on"] is True   # final games never listed


def test_follow_route_needs_both_flags_and_saves_to_the_prefs_key(client, engine):
    _on(engine)
    assert client.get("/api/v2/follow").status_code == 404                    # phone.follow off
    assert client.post("/api/v2/follow", json={"players": ["Nikola Jokic"]}).status_code == 404
    engine.cfg.update({"phone": {"v2": True, "follow": True}})
    assert client.get("/api/v2/status").json()["follow"] is True
    r = client.post("/api/v2/follow", json={"players": ["Nikola Jokic", "nikola jokic", "Alex Ovechkin"]}).json()
    assert r["ok"] is True and r["players"] == ["Nikola Jokic", "Alex Ovechkin"]
    assert engine.cfg.get("noggin.priority.prefs.followed_players") == ["Nikola Jokic", "Alex Ovechkin"]
    got = client.get("/api/v2/follow").json()
    assert got["enabled"] is True and got["players"] == ["Nikola Jokic", "Alex Ovechkin"] and got["live"] == []
    assert client.post("/api/v2/follow", json={"players": 5}).json()["ok"] is False
    assert client.post("/api/v2/follow", json={"players": []}).json()["message"] == "Cleared"
    assert engine.cfg.get("noggin.priority.prefs.bets_weight") == 1.0       # the other prefs are untouched



def test_release_that_ran_reads_deploys_and_rollbacks():
    names = {"e8bbf8e": "RC5.1", "cf480a4": "RC6"}
    st = {"good": {"commit": "e8bbf8e0000"},
          "history": [{"commit": "e8bbf8e0000", "from": "4602c630000", "result": "deployed", "at": 50.0},
                      {"commit": "cf480a40000", "from": "e8bbf8e0000", "result": "rolled_back", "at": 150.0}]}
    r = phone_v2.release_that_ran(st, 100.0, 200.0, names)
    assert r["commit"] == "e8bbf8e" and r["name"] == "RC5.1"                 # RC6 rolled back: RC5.1 ran
    assert r["changes"] == [{"result": "rolled_back", "commit": "cf480a4", "name": "RC6", "from": "e8bbf8e",
                             "from_name": "RC5.1", "at": 150.0}]
    assert phone_v2.release_that_ran(st, 0.0, 100.0, names)["name"] == "RC5.1"   # before the rollback
    assert phone_v2.release_that_ran(st, 0.0, 100.0, names)["changes"][0]["result"] == "deployed"
    assert phone_v2.release_that_ran({"good": {"commit": "abcdef123"}}, 0, 1, {}) == \
        {"commit": "abcdef1", "name": None, "changes": []}                   # no history: the last good commit
    assert phone_v2.release_that_ran({}, 0, 1, names) is None and phone_v2.release_that_ran(None, 0, 1, {}) is None
    failed = {"history": [{"commit": "cf480a40000", "result": "rollback_failed", "at": 150.0}]}
    assert phone_v2.release_that_ran(failed, 100.0, 200.0, names)["commit"] is None   # unknown, never guessed


def test_last_night_card_pending_is_never_zero():
    c = phone_v2.last_night_card("2026-11-04", None, None, None, None)
    assert c["mid_play_entries"] is None and c["dead_air_pct"] is None and c["rule1"] is None
    assert c["measured"] is False and c["worth"] is None and c["release"] is None
    c = phone_v2.last_night_card("2026-11-04", {"dead_air_pct": 3.4, "high_leverage_leaves": 0},
                                 {"entries_mid_play": 1}, {"rating": 4}, {"commit": "e8bbf8e"})
    assert c["mid_play_entries"] == 1.0 and c["rule1"] is False and c["worth"] == 4 and c["dead_air_pct"] == 3.4
    assert phone_v2.last_night_card("n", {}, {}, {"rating": 9}, None)["worth"] is None


def test_lastnight_route_reads_the_files(client, engine, tmp_path, monkeypatch):
    from datetime import timedelta
    _on(engine)
    assert client.get("/api/v2/lastnight").status_code == 404                  # phone.last_night off
    engine.cfg.update({"phone": {"v2": True, "last_night": True}})
    assert client.get("/api/v2/status").json()["last_night"] is True
    night = (phone_v2.night_of(time.time()) - timedelta(days=1)).isoformat()
    rep = tmp_path / "reports"
    rep.mkdir()
    (rep / f"{night}.json").write_text(json.dumps({"mid_play_entries": 0, "high_leverage_leaves": 0, "dead_air_pct": 2.5}))
    (tmp_path / "worth.jsonl").write_text(json.dumps({"night": night, "rating": 5}) + "\n")
    t0, _ = phone_v2.night_window(phone_v2.date.fromisoformat(night))
    (tmp_path / "state.json").write_text(json.dumps({"history": [{"commit": "9322c28ff", "result": "deployed", "at": t0 + 60}]}))
    (tmp_path / "releases.json").write_text(json.dumps({"9322c28": "RC6.1"}))
    monkeypatch.setattr(phone_v2, "REPORTS", (rep,))
    monkeypatch.setattr(phone_v2, "LIVE_NIGHTS", tmp_path / "none")
    monkeypatch.setattr(phone_v2, "WORTH_LOG", tmp_path / "worth.jsonl")
    monkeypatch.setattr(phone_v2, "DEPLOY_STATE", tmp_path / "state.json")
    monkeypatch.setattr(phone_v2, "RELEASE_NAMES", tmp_path / "releases.json")
    r = client.get("/api/v2/lastnight").json()
    assert r["night"] == night and r["worth"] == 5 and r["mid_play_entries"] == 0.0 and r["rule1"] is True
    assert r["release"]["name"] == "RC6.1" and r["release"]["changes"][0]["result"] == "deployed"
    assert isinstance(r["show"], bool)
