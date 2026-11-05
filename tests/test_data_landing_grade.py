"""Tests for the data landing grader proposal (EXP-705)."""
import importlib.util
import json
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "data_landing_grade", Path(__file__).resolve().parent.parent / "scripts" / "data_landing_grade.py")
dlg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dlg)

LIC_B = "ESPN public endpoint; personal-use, not for commercial release"


def row(gid="g1", night="2025-01-01", split="train", **kw):
    r = {"source": "src", "league": "nba", "game_id": gid, "night": night, "split": split,
         "license": "CC-BY-4.0", "kind": "events", "tokens": 100, "unmapped": 0}
    r.update(kw)
    return r


def status(res, prefix):
    return [s for s, m in res if m.startswith(prefix)]


def clean():
    return [row("g1", "2025-01-01", "train", seq=1), row("g2", "2025-01-02", "valid", seq=2),
            row("g3", "2025-01-03", "test", seq=3)]


def test_clean_landing_passes():
    res = dlg.grade(clean(), locked={"nba": set()})
    assert not [r for r in res if r[0] == "FAIL"], res


def test_missing_required_field_fails_schema():
    r = row()
    del r["license"]
    assert dlg.grade([r])[0][0] == "FAIL"


def test_plan_b_without_personal_use_tag_fails():
    rows = clean() + [row("g4", "2025-01-01", "train", plan="B", license="ESPN", seq=4)]
    assert "FAIL" in status(dlg.grade(rows), "D1")
    rows[-1]["license"] = LIC_B
    assert "FAIL" not in status(dlg.grade(rows), "D1")


def test_value_known_after_use_fails_asof():
    rows = clean() + [row(seq=9, asof_ts="2025-01-01T20:00:00Z", use_ts="2025-01-01T19:00:00Z")]
    assert status(dlg.grade(rows), "D2") == ["FAIL"]
    rows[-1]["asof_ts"] = "2025-01-01T18:00:00Z"
    assert status(dlg.grade(rows), "D2") == ["PASS"]


def test_duplicates_over_one_percent_fail():
    rows = clean() + [row("g1", "2025-01-01", "train", seq=1)]
    assert status(dlg.grade(rows), "D3") == ["FAIL"]


def test_game_in_two_splits_fails():
    rows = clean() + [row("g1", "2025-01-01", "valid", seq=7)]
    assert "FAIL" in status(dlg.grade(rows, locked={"nba": set()}), "D4")


def test_locked_nights_come_from_splits_over_the_archive(tmp_path):
    lg = tmp_path / "nba"
    lg.mkdir()
    nights = [f"202501{d:02d}" for d in range(1, 21)]
    for i, n in enumerate(nights):
        (lg / f"scoreboard_{n}.json").write_text(json.dumps({"events": [{"id": f"g{i}"}]}))
    locked = dlg.locked_nights(tmp_path, ["nba"])
    from redzone.lab import splits
    want = set(splits.split_nights(splits.archive_nights(lg)).test)
    assert locked["nba"] == want and want  # the split's own test nights, nothing typed in
    last = sorted(want)[-1]
    bad = [row("gx", f"{last[:4]}-{last[4:6]}-{last[6:]}", "test", seq=1)]
    assert "FAIL" in status(dlg.grade(bad, locked=locked), "D4")
    ok = [row("gy", "2025-01-01", "train", seq=2)]
    assert "FAIL" not in status(dlg.grade(ok, locked=locked), "D4")


def test_league_without_archive_is_pending_not_pass():
    assert "PENDING" in status(dlg.grade(clean(), locked={}), "D4")


def test_split_out_of_time_order_fails():
    rows = [row("g1", "2025-01-05", "train", seq=1), row("g2", "2025-01-02", "test", seq=2)]
    assert "FAIL" in status(dlg.grade(rows), "D4")


def test_synthetic_cap():
    rows = clean() + [row("s1", "2025-01-01", "train", kind="synthetic", tokens=1000, seq=5)]
    assert status(dlg.grade(rows), "D5") == ["FAIL"]
    rows[-1]["tokens"] = 900
    assert status(dlg.grade(rows), "D5") == ["PASS"]


def test_unmapped_vocab_per_league():
    rows = clean()
    rows[0]["unmapped"] = 3
    assert status(dlg.grade(rows), "D6") == ["FAIL"]


def test_leak_scan_flags_perfect_feature():
    rows = [row(f"g{i}", f"2025-01-{i + 1:02d}", "train", seq=i, y=i % 2,
                features={"final_margin": float(i % 2), "noise": float((i * 7) % 5)}) for i in range(20)]
    res = status(dlg.grade(rows, label_col="y"), "D7")
    assert res == ["FAIL"]


def test_auc_ties():
    assert dlg.auc([1, 1, 1, 1], [0, 1, 0, 1]) == 0.5
    assert dlg.auc([0, 1, 2, 3], [0, 0, 1, 1]) == 1.0
    assert dlg.auc([1, 2], [1, 1]) is None
