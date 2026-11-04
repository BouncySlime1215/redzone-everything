"""Rehearse the Prime multiview calibration without a stick: the probe against the simulated Prime
player (redzone/lab/prime_mv_fake.py), and the morning-after grade on a synthetic night."""
import asyncio
import importlib.util
import json
import sqlite3
import types
from pathlib import Path

import pytest

from redzone.lab.prime_mv_fake import FakePrimeStick
from redzone.tuners import prime_multiview as pmv

ROOT = Path(__file__).resolve().parent.parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def probe(monkeypatch):
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda s, *a, **k: real_sleep(0))
    return _load("prime_multiview_probe")


def _go(probe, stick, tmp_path, rounds=20, label="Suns,Warriors"):
    a = types.SimpleNamespace(gti="g", label=label, rounds=rounds, gap=0, yes=True, fake=True)
    keys = tmp_path / "keys.json"
    res = asyncio.run(probe.probe(stick, a, tmp_path, stick.tune, keys_file=keys))
    return res, keys


def test_rehearsal_learns_the_key_map_and_passes_the_bench(probe, tmp_path):
    res, keys = _go(probe, FakePrimeStick(), tmp_path)
    assert res["error"] is None and res["can_switch"] and res["can_build"]
    assert res["gate_bench"]["switches"] == 20 and res["gate_bench"]["good_pct"] == 100.0
    km = pmv.load_keys(keys)
    assert km.after == "swap" and km.promote == {"0>1": ["right", "ok"], "0>2": ["right", "down", "ok"]}
    assert km.open == ["down", "down", "right", "ok"] and km.add_open == ["down", "ok"]
    assert (tmp_path / "probe.json").exists()


def test_rehearsal_finds_rotate_order(probe, tmp_path):
    res, keys = _go(probe, FakePrimeStick(after="rotate"), tmp_path)
    assert res["error"] is None and pmv.load_keys(keys).after == "rotate"
    assert res["gate_bench"]["good_pct"] == 100.0


def test_unreadable_player_stops_before_any_key_map(probe, tmp_path):
    res, keys = _go(probe, FakePrimeStick(readable=False), tmp_path)
    assert res["player_readable"] is False and res["error"] == "multiview entry not found"
    assert not keys.exists()


def test_a_dropped_switch_stops_the_timed_rounds(probe, tmp_path):
    res, _ = _go(probe, FakePrimeStick(drop_every=5), tmp_path)
    assert res["error"] and "wanted" in res["error"]
    assert res["rounds"][-1]["ok"] is False and res["gate_bench"]["pass"] is False


def test_gate_counts_a_zero_second_switch_as_fast():
    rows = [{"ok": True, "took": 0.0}] * 20
    assert pmv.gate(rows, mid_play_entries=0)["pass"] is True
    assert pmv.gate([{"ok": True}] * 20, mid_play_entries=0)["good"] == 0   # no time = slow


def test_grade_a_synthetic_night(tmp_path):
    grade = _load("prime_multiview_grade")
    db = tmp_path / "redzone.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE switches (ts REAL, frm TEXT, to_game TEXT, target TEXT, kind TEXT, reason TEXT, "
                "event_ts REAL, cmd_ts REAL, confirmed_ts REAL, ok INTEGER, dry_run INTEGER, detail TEXT)")
    for i in range(21):
        d = {"path": "prime_tile", "took": 2.5 if i == 0 else 1.1, "trace": {"id": f"t{i}"}}
        con.execute("INSERT INTO switches VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (1000 + i, None, "g", "prime:x", "better", "", None, None, None, 1, 0, json.dumps(d)))
    con.execute("INSERT INTO switches VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (1100, None, "g", "youtubetv:nbc", "better", "", None, None, None, 1, 0, json.dumps({"path": "tune"})))
    con.commit()
    con.close()
    cuts = tmp_path / "supervisor_cuts.jsonl"
    cuts.write_text("\n".join(json.dumps({"path": "prime_tile", "ts": 1000 + i, "id": f"t{i}", "enter": "dead",
                                          "leave": "dead"}) for i in range(21)))
    sw = grade.tile_switches(db, 0)
    assert len(sw) == 21
    g = grade.grade(sw, grade.graded(cuts, 0))
    assert g["good_pct"] == round(100 * 20 / 21, 1) and g["pass"] is True and g["ungraded"] == 0
    # one cut entered mid-play -> fail; one ungraded switch -> no pass either
    lines = cuts.read_text().splitlines()
    cuts.write_text("\n".join(lines[:-1] + [json.dumps({"path": "prime_tile", "ts": 1020, "id": "t20", "enter": "play"})]))
    assert grade.grade(sw, grade.graded(cuts, 0))["pass"] is False
    cuts.write_text("\n".join(lines[:-1]))
    g = grade.grade(sw, grade.graded(cuts, 0))
    assert g["ungraded"] == 1 and g["pass"] is False
