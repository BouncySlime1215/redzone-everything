"""EXP-038 grader: overrides within the window after an automatic switch."""
import sqlite3

from redzone.lab import orient


def sw(ts, to, kind="crunch", ok=1):
    return {"ts": ts, "to_game": to, "kind": kind, "ok": ok, "dry_run": 1}


def test_overrides_counts_user_switch_to_another_game_in_window():
    rows = [sw(0, "a"), sw(10, "b", "user"),          # overridden
            sw(100, "c"), sw(140, "d", "user"),        # 40 s later: outside 30 s
            sw(200, "e"), sw(205, "e", "user"),        # same game: not an override
            sw(300, "f", ok=0), sw(301, "g", "user")]  # failed switch doesn't count
    assert orient.overrides(rows, 30) == {"auto": 3, "overridden": 1, "rate": 0.3333}
    assert orient.overrides(rows)["auto"] == 3                                  # window from config (30)
    assert orient.overrides([]) == {"auto": 0, "overridden": 0, "rate": None}


def test_compare_pass_fail_pending():
    assert orient.compare([0.1] * 3, [0.3] * 3)["status"] == "PENDING"        # too few nights
    assert orient.compare([0.10, 0.12, 0.08, 0.10, 0.11, 0.09], [0.30, 0.28, 0.32, 0.30, 0.29, 0.31])["status"] == "PASS"
    assert orient.compare([0.30, 0.31, 0.29], [0.10, 0.11, 0.09])["status"] == "FAIL"
    assert orient.compare([], [0.2])["status"] == "PENDING"


def test_night_switches_reads_read_only(tmp_path):
    db = tmp_path / "r.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE switches (ts REAL, to_game TEXT, kind TEXT, ok INTEGER, dry_run INTEGER)")
    con.executemany("INSERT INTO switches VALUES (?,?,?,?,?)", [(5, "a", "crunch", 1, 1), (50, "b", "user", 1, 1)])
    con.commit()
    con.close()
    assert [r["to_game"] for r in orient.night_switches(db, 0, 10)] == ["a"]
