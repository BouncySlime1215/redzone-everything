"""AUDIT-9: one malformed row in a night's jsonl never takes the whole night ops report down."""
import json

from redzone.platform import night_ops


def test_rows_with_unreadable_ts_or_attempts_are_skipped_not_fatal(tmp_path):
    rows = [{"ts": 150, "kind": "watchdog", "state": "buffered", "attempts": 2},
            {"ts": "soon", "kind": "watchdog", "state": "live"},
            {"ts": [1], "kind": "watchdog"},
            {"ts": 160, "kind": "watchdog", "state": "live", "attempts": "many"},
            ["not", "a", "row"], {"kind": "watchdog"}]
    (tmp_path / "joins.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    s = night_ops.summarize(tmp_path, 100.0, 200.0)
    assert s["watchdog"]["joins"] == 2
    assert s["watchdog"]["reseeks"] == 1
