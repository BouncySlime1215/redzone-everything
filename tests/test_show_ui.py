"""The phone's Show view (flag show.ui, redzone/web/show.py): read-only routes over the show's state files.

Why: Nick watches the new show from the phone app he already uses. Off = no route, no markup, no script; on =
only the contract's fields leave, as numbers, on/off values and short public names, and a stale, missing or
broken file is a calm answer, never an error.
"""
import json
from pathlib import Path
import time

import pytest
from fastapi.testclient import TestClient

from redzone.web import show
from redzone.web.server import build_app
from test_app import engine  # noqa: F401  (fixture)

NOW = time.time()

FULL = {
    "updated": NOW - 1, "mode": "live",
    "session": {"started": NOW - 1800, "minutes": 30.2, "planned_minutes": 120},
    "sound": {"mute": "muted", "volume": 0, "read_back_at": NOW - 2, "ok": True},
    "on_screen": {"game": "MSU @ DUKE", "league": "cbb", "channel": "espn", "stick": "plus", "input": "HDMI2",
                  "period": "2nd", "clock": "7:43", "score": "61-58", "behind_live_s": 41.0, "since": NOW - 20,
                  "look_s": 21.4, "state": "live play", "secret_field": "x"},
    "parked": {"game": "BOS @ PHI", "league": "nba", "channel": "nbc", "stick": "s2023", "ready": True,
               "parked_for_s": 12.0, "behind_live_s": 55.0},
    "next": {"action": "jump", "to_game": "BOS @ PHI", "in_s": 6.5, "reason": "timeout on the game we are watching"},
    "pace": {"jumps": 37, "per_min_now": 1.4, "per_min_session": 1.23,
             "windows": {"1": 2, "5": 7, "10": 13, "20": 26, "30": 37, "45": None, "60": None, "90": 99}},
    "planned": {"scheduled": 52, "delivered": 37},
    "rule1": {"mid_play_joins": 0, "mid_play_leaves": 0, "high_leverage_leaves": 0, "judged": "replay"},
    "timing": {"padding_s": 2.8, "channels": {"espn": {"k_s": -2.78, "k_spread_s": 0.5, "tune_offset_known": True}}},
    "stay_reasons": {"no safe exit yet": 310, "the other stick is not ready": 120},
    "health": {"tv": "ok", "feed": "ok", "sticks": {"plus": "ok", "s2023": "ok"}, "eyes": "ok", "last_error": ""},
    "games": [{"game": "MSU @ DUKE", "league": "cbb", "channel": "espn", "period": "2nd", "clock": "7:43",
               "score": "61-58", "state": "live play", "value": 0.82, "on_screen": True, "parked": False}],
    "device_ip": "192.168.1.20", "account": "someone@example.com",
}
EYES = {"updated": NOW - 1, "status": "ok", "channel": "espn", "league": "cbb", "frame_y": 1794351785.12,
        "period": 2, "clock_s": 463.0, "clock_running": True, "k": {"espn|cbb": {"k_s": -2.78, "spread_s": 0.5, "n": 5}},
        "reads_per_min": 110, "failed_reads_per_min": 3, "cpu_pct": 9.5, "mem_mb": 520, "frame_path": "/Users/x/f.png"}


def _app(engine, on: bool, folder=None):
    engine.cfg.update({"show": {"ui": on, "state_dir": str(folder) if folder else ""}})
    return TestClient(build_app(engine))


def _keys(node, spec):
    """Every key in node is in spec (recursively)."""
    if isinstance(spec, dict) and isinstance(node, dict):
        for k, v in node.items():
            assert k in spec, k
            _keys(v, spec[k])
    elif isinstance(spec, tuple) and node is not None:
        items = node.values() if spec[0] == "map" else node
        for v in items:
            _keys(v, spec[1])


def test_off_by_default_and_off_means_no_route_no_markup(engine):
    assert engine.cfg.get("show.ui") is False and engine.cfg.get("show.state_dir") == ""
    c = TestClient(build_app(engine))
    assert c.get("/api/show").status_code == 404
    assert c.get("/api/show/events").status_code == 404
    page = c.get("/").text
    assert "rz-show" not in page and page == (show.Path(__file__).parent.parent / "redzone/web/static/index.html").read_text()


def test_on_adds_the_script_once(engine, tmp_path):
    page = _app(engine, True, tmp_path).get("/").text
    assert page.count("/static/rz-show.js") == 1 and page.index("rz-show.js") > page.index("app.js")


def test_turned_off_while_running_answers_404(engine, tmp_path):
    c = _app(engine, True, tmp_path)
    engine.cfg.update({"show": {"ui": False}})
    assert c.get("/api/show").status_code == 404
    assert "rz-show" not in c.get("/").text


def test_full_state_only_contract_fields(engine, tmp_path):
    # stamped when the test runs, not when the file was imported: in a long run NOW can be minutes old by here
    (tmp_path / "state.json").write_text(json.dumps({**FULL, "updated": time.time() - 1}))
    (tmp_path / "eyes_state.json").write_text(json.dumps(EYES))
    s = _app(engine, True, tmp_path).get("/api/show").json()
    assert s["on_screen"]["game"] == "MSU @ DUKE" and s["sound"]["volume"] == 0 and s["pace"]["windows"]["45"] is None
    assert s["next"]["in_s"] == 6.5 and s["eyes"]["k"]["espn|cbb"]["n"] == 5
    assert 0 <= s["stale_s"] < 5
    _keys({k: v for k, v in s.items() if k not in ("eyes", "stale_s")}, show.STATE_SPEC)
    _keys(s["eyes"], show.EYES_SPEC)
    text = json.dumps(s)
    for leak in ("secret_field", "192.168", "example.com", "device_ip", "account", "/Users/", "frame_path", '"90"'):
        assert leak not in text, leak


def test_eyes_read_from_sibling_folder(engine, tmp_path):
    (tmp_path / "show").mkdir()
    (tmp_path / "eyes").mkdir()
    (tmp_path / "show" / "state.json").write_text(json.dumps({"updated": NOW}))
    (tmp_path / "eyes" / "eyes_state.json").write_text(json.dumps(EYES))
    s = _app(engine, True, tmp_path / "show").get("/api/show").json()
    assert s["eyes"]["status"] == "ok"


def test_only_updated(engine, tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({"updated": time.time() - 2}))   # at run time, as above
    s = _app(engine, True, tmp_path).get("/api/show").json()
    assert set(s) == {"updated", "eyes", "stale_s"} and s["eyes"] is None and 1 <= s["stale_s"] < 10


def test_stale_file(engine, tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({**FULL, "updated": NOW - 300}))
    s = _app(engine, True, tmp_path).get("/api/show").json()
    assert s["stale_s"] >= 299 and s["on_screen"]["game"] == "MSU @ DUKE"


@pytest.mark.parametrize("folder", ["missing", "empty_config"])
def test_missing_file_or_folder(engine, tmp_path, folder):
    c = _app(engine, True, tmp_path / "nope" if folder == "missing" else None)
    assert c.get("/api/show").json() == {"eyes": None, "stale_s": None}
    assert c.get("/api/show/events").json() == {"events": []}


@pytest.mark.parametrize("junk", ["{not json", "[1, 2, 3]", "", '{"updated": NaN}', '{"updated": "soon"}', "\x00\xff"])
def test_garbage_state(engine, tmp_path, junk):
    (tmp_path / "state.json").write_text(junk, encoding="utf-8", errors="replace")
    r = _app(engine, True, tmp_path).get("/api/show")
    assert r.status_code == 200
    s = r.json()
    assert s["stale_s"] is None and s.get("updated") is None


def test_wrong_types_become_null(engine, tmp_path):
    bad = {"updated": NOW, "sound": {"mute": 5, "volume": "loud", "ok": "yes"}, "games": "lots", "pace": [1, 2],
           "on_screen": {"look_s": True, "game": "a long name " * 40}}
    (tmp_path / "state.json").write_text(json.dumps(bad))
    s = _app(engine, True, tmp_path).get("/api/show").json()
    assert s["sound"] == {"mute": None, "volume": None, "ok": None}
    assert s["games"] is None and s["pace"] is None
    assert s["on_screen"]["look_s"] is None and len(s["on_screen"]["game"]) <= show.TEXT_MAX


def test_events_since_and_garbage_lines(engine, tmp_path):
    lines = [
        {"t": NOW - 300, "kind": "jump", "from": "BOS @ PHI", "to": "MSU @ DUKE", "reason": "dead ball", "jump_s": 2.1, "look_s": 34.0},
        "not json at all",
        {"t": NOW - 200, "kind": "park", "game": "BOS @ PHI", "channel": "nbc", "took_s": 9.8, "host": "10.0.0.7"},
        [1, 2],
        {"kind": "stay", "reason": "no time"},
        {"t": NOW - 100, "kind": "big_play", "game": "BOS @ PHI", "what": "three to take the lead", "seen": True},
        {"t": NOW - 50, "kind": "alert", "level": "warn", "text": "stick at 192.168.1.40 is slow"},
    ]
    (tmp_path / "events.jsonl").write_text("\n".join(x if isinstance(x, str) else json.dumps(x) for x in lines) + "\n{\"t\": 1")
    c = _app(engine, True, tmp_path)
    allev = c.get("/api/show/events").json()["events"]
    assert [e["kind"] for e in allev] == ["jump", "park", "big_play", "alert"]
    assert "host" not in allev[1] and allev[3]["text"] is None   # an address never leaves
    newer = c.get(f"/api/show/events?since={NOW - 150}").json()["events"]
    assert [e["kind"] for e in newer] == ["big_play", "alert"]
    assert c.get("/api/show/events?since=nonsense").status_code == 200
    assert len(c.get("/api/show/events?since=nan").json()["events"]) == 4
    for e in allev:
        assert set(e) <= set(show.EVENT_SPEC)


def test_clean_text_rules():
    assert show.clean("MSU @ DUKE", show.TEXT) == "MSU @ DUKE"
    for bad in ("192.168.1.2", "me@example.com", "https://x.y", "aa:bb:cc:dd:ee", "A" * 30, "/Users/someone"):
        assert show.clean(bad, show.TEXT) is None, bad
    assert show.clean(float("inf"), show.NUM) is None and show.clean(True, show.NUM) is None


# ---- tonight's real run (Nov 10, run 4): the runner's own files, copied as written --------------------------
REAL = Path(__file__).parent / "fixtures" / "show_2026-11-10-run4"


def test_real_state_nulls_survive_as_null(engine):
    s = show.build_state(REAL, now=1794363017.0 + 3)
    assert s["stale_s"] == 3.0 and s["mode"] == "live"
    assert s["next"] == {"action": "wait", "to_game": None, "in_s": None,
                         "reason": "the landing is outside a padded legal window"}
    assert s["parked"]["parked_for_s"] is None and s["parked"]["ready"] is False
    assert s["pace"]["windows"]["10"] is None and s["pace"]["windows"]["5"] == 4
    assert s["rule1"] == {"mid_play_joins": None, "mid_play_leaves": None, "high_leverage_leaves": None,
                          "judged": "unknown live"}
    assert s["on_screen"]["behind_live_s"] == 134.3 and s["on_screen"]["look_s"] == 42
    assert all(isinstance(v, (int, float)) for v in s["stay_reasons"].values()) and len(s["stay_reasons"]) == 8
    assert max(len(k) for k in s["stay_reasons"]) > 40          # long plain-English reasons are kept whole
    assert s["games"][1]["value"] == 1.1                         # worth can run past 1
    assert s["health"]["eyes"] == "off"


def test_real_state_drops_fields_outside_the_contract(engine):
    s = show.build_state(REAL, now=1794363020.0)
    assert "show_delay_s" not in s["timing"]
    assert set(s["timing"]["channels"]["nbc"]) == {"k_s", "k_spread_s", "tune_offset_known"}   # no "game", "padding_s"
    _keys({k: v for k, v in s.items() if k not in ("eyes", "stale_s")}, show.STATE_SPEC)


def test_real_eyes_status_why_and_restart(engine):
    e = show.build_state(REAL, now=1794363020.0)["eyes"]
    assert e["status"] == "off" and e["why"] == "stopped"
    assert e["last_stop_y"] is None and e["last_restart_y"] == 1794362763.236
    assert e["k"]["espn|cbb|401909564"] == {"k_s": None, "spread_s": None, "n": 0}
    for gone in ("chrome_pid", "chrome_cpu_pct", "mode", "watching", "pm_s"):
        assert gone not in json.dumps(e), gone
    _keys(e, show.EYES_SPEC)


def test_eyes_dir_setting(engine, tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({"updated": NOW}))
    s = _app(engine, True, tmp_path).get("/api/show").json()
    assert s["eyes"] is None
    engine.cfg.update({"show": {"eyes_dir": str(REAL)}})
    assert _app(engine, True, tmp_path).get("/api/show").json()["eyes"]["why"] == "stopped"


def test_real_events_every_kind(engine):
    evs = show.read_events(REAL)
    lines = [ln for ln in (REAL / "events.jsonl").read_text().splitlines() if ln.strip()]
    assert len(evs) == len(lines)
    assert {e["kind"] for e in evs} == {"stay", "park", "jump", "alert"}
    j = [e for e in evs if e["kind"] == "jump"][0]
    assert j == {"t": 1794362776.0, "kind": "jump", "from": "BOS @ PHI", "to": "MSU @ DUKE",
                 "reason": "timeout or break", "jump_s": 2.7, "look_s": 273}
    a = [e for e in evs if e["kind"] == "alert"][0]
    assert a["text"] == "no jump: a tune did not work" and a["level"] == "warn"
    assert [e["kind"] for e in show.read_events(REAL, since=1794362976.0)] == ["jump", "park"]


def test_real_run_through_the_route(engine):
    c = _app(engine, True, REAL)
    s = c.get("/api/show").json()
    assert s["on_screen"]["game"] == "BOS @ PHI" and s["stale_s"] > 0
    assert "undefined" not in json.dumps(s) and "NaN" not in json.dumps(s)
    assert "rz-show.js?v=6" in c.get("/").text
