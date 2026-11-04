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
    from types import SimpleNamespace as NS
    from redzone.models import League, Phase as P
    from redzone.scoring.engine import Scored

    def sc(gid, lg, score, factor="", reason="", crunch=False, is_break=False):
        return Scored(gid, League(lg), score, score, 0.0, 0.0, 0.0, reason, [], is_break, None, None, 0.0,
                      crunch=crunch, factor=factor)
    games = {"a": NS(matchup="A @ B", phase=P.LIVE), "b": NS(matchup="C @ D", phase=P.LIVE), "z": NS(matchup="done", phase=P.POST)}
    old = {"a": sc("a", "nba", 140.0, factor="Tied late", reason="r", crunch=True),
           "b": sc("b", "nhl", -3.0, reason="Break", is_break=True), "z": sc("z", "nhl", 50.0)}
    v = phone_v2.watch_values(old, games)
    assert v["source"] == "stub" and [(r["game_id"], r["wv"], r["why"]) for r in v["games"]] == [("a", 100, "Tied late"), ("b", 0, "Break")]
    old["a"].watch_value, old["a"].watch_why = 63.4, "Comeback brewing"
    old["b"].watch_value = 12
    v = phone_v2.watch_values(old, games)
    assert v["source"] == "learned" and v["games"][0] == {"game_id": "a", "matchup": "A @ B", "league": "nba", "wv": 63,
                                                         "why": "Comeback brewing", "crunch_state": "entering", "stakes_line": "",
                                                         "crunch": True, "is_break": False, "source": "learned"}
    assert v["games"][1]["why"] == "Break"                                             # no watch_why: the reason
    assert phone_v2.watch_values({}, {}) == {"games": [], "source": None}
    old["a"].crunch_now = True
    assert phone_v2.watch_values(old, games)["games"][0]["crunch_state"] == "in"       # stub from the flags
    old["a"].crunch_state = "leaving"                                                  # lane 1's model wins
    assert phone_v2.watch_values(old, games)["games"][0]["crunch_state"] == "leaving"
    old["a"].stakes = 0.7
    assert phone_v2.watch_values(old, games)["games"][0]["stakes_line"] == "Big stakes"           # stub
    old["a"].stakes_line = "AFC 1-seed race, top-5 matchup"                                       # lane 1 wins
    assert phone_v2.watch_values(old, games)["games"][0]["stakes_line"] == "AFC 1-seed race, top-5 matchup"
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
    from redzone.lab import data_ledger as DL
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
    monkeypatch.setattr(phone_v2, "LEDGER", tmp_path / "none.json")
    r = client.get("/api/v2/ledger").json()
    assert r["enabled"] and r["pending"] and r["nfm"]["done"] == 0


def test_provenance_orders_lane3_control_last():
    assert phone_v2.PROV_ORDER[-1] == "control"


def test_ledger_folds_the_kaggle_factory_manifest():
    from redzone.lab import data_ledger as DL
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
