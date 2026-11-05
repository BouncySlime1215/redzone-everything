import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


def test_pick_warm_is_the_ready_standby_and_cold_is_another_live_game():
    import press_probe as pp
    st = {"now": {"game_id": "a"}, "standby": {"game_id": "b", "ready": True},
          "games": [{"id": g, "phase": ph} for g, ph in (("a", "live"), ("b", "live"), ("c", "live"), ("d", "pre"))]}
    assert pp.pick(st, "warm", None) == ("b", "warm")
    assert pp.pick(st, "cold", None) == ("c", "cold")
    assert pp.pick(st, "cold", "c") is None and pp.pick(st, "any", "c") == ("b", "warm")
    st["standby"]["ready"] = False
    assert pp.pick(st, "warm", None) is None and pp.pick(st, "any", None) == ("c", "cold")


def test_a_press_splits_into_where_its_seconds_went(tmp_path):
    """Segment split: tap -> decider (intake), lock, tune, dead-ball wait, flip, resume, phone ack."""
    import json
    import sqlite3

    import press_probe as pp
    db = tmp_path / "redzone.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE switches (ts REAL, frm TEXT, to_game TEXT, target TEXT, kind TEXT, reason TEXT, "
                "event_ts REAL, cmd_ts REAL, confirmed_ts REAL, ok INTEGER, dry_run INTEGER, detail TEXT)")
    tr = {"decided_at": 100.3, "picture_at": 102.9,
          "spans": {"queued": 0.1, "tune": None, "safe_wait": 0.0, "flip": 1.9, "resume": 0.4}}
    con.execute("INSERT INTO switches VALUES (100.3,'a','b','youtubetv:espn','user','',NULL,100.3,102.9,1,0,?)",
                (json.dumps({"trace": tr}),))
    con.execute("INSERT INTO switches VALUES (100.4,'a','b','youtubetv:espn','better','',NULL,100.4,103,1,0,'{}')")
    con.commit()
    con.close()
    got = pp.trace_for(db, "b", 100.0, 110.0)
    assert got == tr and pp.trace_for(db, "c", 100.0, 110.0) is None and pp.trace_for(tmp_path / "no.db", "b", 0, 1) is None
    seg = pp.segments(100.0, 3.2, got)
    assert seg == {"intake": 0.3, "queued": 0.1, "tune": None, "safe": 0.0, "flip": 1.9, "resume": 0.4, "ack": 0.3}
    assert pp.segments(100.0, None, None) == {k: None for k in pp.SEGMENTS}
    rows = [{"kind": "warm", "tap_to_tv_s": 3.2, "segments": seg}, {"kind": "cold", "tap_to_tv_s": None, "segments": seg}]
    summ = pp.summarize_segments(rows)
    assert summ["warm"]["flip"] == {"p50_s": 1.9, "p95_s": 1.9} and "tune" not in summ["warm"] and summ["cold"] == {}


def test_real_presses_grade_press_to_tv_p95_once_there_are_enough(tmp_path):
    import json

    import northstar as ns
    d = tmp_path / "press_probe"
    d.mkdir()
    warm = [{"kind": "warm", "tap_to_tv_s": 2.4, "segments": {"flip": 1.9}}] * 19
    (d / "a.json").write_text(json.dumps({"presses": warm}))
    got = ns.press_samples([d, tmp_path / "missing"])
    assert len(got) == 19
    row = ns.press_row(None, got)
    assert row["status"] == ns.PENDING and row["value"] == 2.4 and "flip 1.90" in row["detail"]
    (d / "b.json").write_text(json.dumps({"presses": [{"kind": "cold", "tap_to_tv_s": 2.9}]}))
    row = ns.press_row(None, ns.press_samples([d]))
    assert row["status"] == ns.PENDING and "Prime" in row["detail"]                # 20 real, but no Prime press yet
    (d / "p.json").write_text(json.dumps({"presses": [{"kind": "warm", "tap_to_tv_s": 2.7, "app": "prime"}] * 5}))
    assert ns.press_row(None, ns.press_samples([d]))["status"] == ns.PASS            # 25 real incl. 5 Prime, p95 <= 3 s
    (d / "c.json").write_text(json.dumps({"presses": [{"kind": "cold", "tap_to_tv_s": 8.5}] * 3}))
    assert ns.press_row(None, ns.press_samples([d]))["status"] == ns.FAIL            # p95 8.5 s
    (d / "c.json").write_text(json.dumps({"presses": [{"kind": "cold", "tap_to_tv_s": None}]}))
    assert ns.press_row(None, ns.press_samples([d]))["status"] == ns.FAIL            # one never reached the TV
    drill = {"runs": [{"go_now_s": [2.0, 2.1]}], "summary": {}}
    sim = ns.press_row(drill, warm)                                                  # too few real: the sim decides
    assert sim["status"] == ns.PENDING and "real presses on Nick's TV" in sim["detail"]
