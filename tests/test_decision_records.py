"""Decision-record export for the Teacher (lane 5 item 2): objective sources only, never Nick's taps."""
import json

from redzone.lab import decision_records as dr
from redzone.store import Store


def _night(tmp_path):
    db = tmp_path / "redzone.db"
    s = Store(db)
    s._x("INSERT INTO decisions VALUES (?,?,?,?,?,?,?)", (1000.0, "switch", "b", "better", "live", "", "Crunch playbook=CRUNCH-02"))
    s._x("INSERT INTO decisions VALUES (?,?,?,?,?,?,?)", (2000.0, "stay", "b", "", "live", "", "best on"))
    s._x("INSERT INTO decisions VALUES (?,?,?,?,?,?,?)", (99999.0, "stay", "z", "", "live", "", "next night"))
    trace = {"trace": {"id": "t1", "path": "swap", "spans": {"to_picture": 2.1234, "dead_air": 0.8}}}
    s.switch(ts=1001.0, frm="a", to_game="b", target="HDMI2", kind="better", reason="Crunch playbook=CRUNCH-02",
             ok=True, dry_run=False, detail=trace)
    s.snapshot([(950.0, "a", "nba", 0.4, 0.2, "meh", ""), (950.0, "b", "nba", 0.9, 0.8, "crunch", "now")])
    # Nick's taps exist in the same database and must never reach a record
    s.feedback("b", -1, "switch", {"terms": {"crunch": 1.0}})
    s.feedback("a", 0, "missed", {"guess": {"play_id": "p9"}})
    data = tmp_path / "data"
    data.mkdir()
    (data / "supervisor_cuts.jsonl").write_text(
        json.dumps({"ts": 1002.0, "id": "t1", "leave": "dead", "leave_why": "timeout", "enter": "play",
                    "enter_why": "live ball"}) + "\nnot json\n")
    (data / "supervisor_moments.jsonl").write_text("\n".join(json.dumps(m) for m in [
        {"ts": 1100.0, "game": "b", "league": "nba", "kind": "3pt", "seen": True, "on_screen": "b"},
        {"ts": 1200.0, "game": "a", "league": "nba", "kind": "dunk", "seen": False, "on_screen": "b"},
        {"ts": 1500.0, "game": "a", "league": "nba", "kind": "dunk", "seen": False, "on_screen": "b"}]) + "\n")
    return db, data


def test_export_joins_objective_sources(tmp_path):
    db, data = _night(tmp_path)
    recs = dr.from_db(db, "2026-11-04", 0.0, 50000.0, data)
    head, rest = recs[0], recs[1:]
    assert head["type"] == "night" and head["schema"] == dr.SCHEMA
    assert head["counts"] == {"decisions": 2, "switches": 1, "moments": 3, "big_seen": 1,
                              "landings_graded": 1, "entries_mid_play": 1, "leaves_mid_play": 0,
                              "alerts": 0, "alerts_nothing_happened": 0, "alerts_nothing_pct": None}
    assert [r["ts"] for r in rest] == sorted(r["ts"] for r in rest)
    d = next(r for r in rest if r["type"] == "decision")
    assert d["playbooks"] == ["CRUNCH-02"] and d["on_screen"] is None
    assert [c["game"] for c in d["candidates"]] == ["b", "a"]        # highest priority first
    o = d["outcome"]                                                   # 5 min after t=1000
    assert (o["big_plays"], o["big_on_target"], o["big_seen"], o["big_missed"]) == (2, 1, 1, 1)
    assert o["missed"] == [{"ts": 1200.0, "game": "a", "kind": "dunk"}]
    stay = [r for r in rest if r["type"] == "decision"][1]
    assert stay["on_screen"] == "b" and stay["candidates"] == []       # snapshot is >3 min old
    sw = next(r for r in rest if r["type"] == "switch")
    assert sw["landing"]["enter"] == "play" and sw["timing"] == {"decide_to_picture_s": 2.123,
                                                                 "dead_air_s": 0.8, "path": "swap"}


def test_no_tap_ever_reaches_a_record(tmp_path):
    db, data = _night(tmp_path)
    recs = dr.from_db(db, "2026-11-04", 0.0, 50000.0, data)
    text = json.dumps(recs)
    body = text.replace('"never": ["feedback", "taps", "votes", "overrides"]', "")
    for leak in ('"vote"', '"feedback"', '"terms"', '"play_id"', '"guess"', '"tap"', '"note"'):
        assert leak not in body
    assert recs[0]["never"] == ["feedback", "taps", "votes", "overrides"]


def test_cli_writes_one_jsonl_and_leaves_the_db_alone(tmp_path):
    import scripts.export_decision_records as cli
    db, data = _night(tmp_path)
    before = db.stat().st_mtime_ns
    assert cli.main(["--db", str(db), "--data", str(data), "--night", "2026-11-04", "--out", str(tmp_path / "o")]) == 0
    lines = (tmp_path / "o" / "2026-11-04.jsonl").read_text().splitlines()
    assert json.loads(lines[0])["night"] == "2026-11-04" and all(json.loads(x)["type"] for x in lines)
    assert db.stat().st_mtime_ns == before
    assert cli.main(["--db", str(db), "--night", "11/04"]) == 2
    assert cli.main(["--db", str(tmp_path / "none.db"), "--night", "2026-11-04"]) == 2


def test_empty_night_still_has_a_header():
    recs = dr.build("2026-11-05", 0.0, 1.0, [], [])
    assert len(recs) == 1 and recs[0]["counts"]["decisions"] == 0


def test_alerts_graded_against_big_plays():
    """EXP-035: a switch whose value crossed the why-tag threshold is an alert; it 'did nothing' when its game
    had no big play in the outcome window. The threshold is the phone's (one number, two places)."""
    from redzone.config import default
    assert default("phone.why_tag_min") == 70

    def sw(ts, to, prov, dry=False):
        return {"ts": ts, "frm": None, "to_game": to, "kind": "better", "reason": "", "ok": 1, "dry_run": dry,
                "detail": json.dumps({"trace": {"id": str(ts), "provenance": prov}})}
    sws = [sw(100.0, "a", {"importance": {"to": 81.0}}),                  # fired, big play at 200: a hit
           sw(1000.0, "b", {"watch_value": {"to": 90}, "importance": {"to": 10}}),   # learned wins: fired, nothing
           sw(2000.0, "c", {"importance": {"to": 40.0}}),                 # under the threshold
           sw(3000.0, "d", {"importance": {"to": 95.0}}, dry=True),       # a dry run never alerts
           sw(4000.0, "e", {})]                                           # no provenance: no alert record
    moments = [{"ts": 200.0, "game": "a", "kind": "td", "seen": True}, {"ts": 1100.0, "game": "z", "kind": "td"}]
    recs = dr.build("n", 0.0, 9e9, [], sws, (), [], moments)                 # the shipped default, 70
    c = recs[0]["counts"]
    assert (c["alerts"], c["alerts_nothing_happened"], c["alerts_nothing_pct"]) == (2, 1, 50.0)
    al = {r["to_game"]: r["alert"] for r in recs if r["type"] == "switch"}
    assert al["a"] == {"value": 81.0, "threshold": 70.0, "fired": True, "nothing_happened": False}
    assert al["b"]["value"] == 90.0 and al["b"]["nothing_happened"] is True
    assert al["c"]["fired"] is False and al["d"]["fired"] is False and al["e"] is None
