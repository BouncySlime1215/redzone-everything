"""[RC7 req. 2] scripts/sync_truth.py: the filter's log -> grader v10's sync-truth rows."""
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location("sync_truth", Path(__file__).parent.parent / "scripts/sync_truth.py")
st = importlib.util.module_from_spec(spec)
spec.loader.exec_module(st)

T = 1762310400.0   # 2026-11-05 02:40 UTC: Nov 4 evening in New York


def test_anchors_become_rows_and_taps_never_do(tmp_path):
    lines = [json.dumps(r) for r in [
        {"t": T, "target": "youtubetv:fox", "kind": "clock", "z": 61.0, "err": 1.5, "stick": "HDMI1"},
        {"t": T + 5, "target": "prime:tnf", "kind": "score", "z": 40.0, "err": -3.0, "stick": "HDMI2"},
        {"t": T + 9, "target": "youtubetv:fox", "kind": "tap", "z": 60.0, "err": 0.2, "stick": "HDMI1"},
        {"t": T + 9, "target": "youtubetv:fox", "kind": "flip", "z": 60.0, "err": None, "stick": "HDMI1"},
        {"t": T + 9, "target": "youtubetv:fox", "kind": "flip", "z": 60.0, "err": 0.4},
        {"t": T + 9, "target": "youtubetv:fox", "kind": "position", "position": 12.0},
        "not json"]]
    rows = st.rows_from(lines)
    assert [r["source"] for r in rows] == ["yttv", "anchor140"]
    assert rows[0] == {"stick": "hdmi1", "night": "2026-11-04", "t": T, "est_behind_s": 59.5,
                       "true_behind_s": 61.0, "source": "yttv", "kind": "clock"}
    assert rows[1]["est_behind_s"] == 43.0 and rows[1]["stick"] == "hdmi2"
    assert st.write(rows, tmp_path) == {"2026-11-04": 2}
    assert len((tmp_path / "2026-11-04.jsonl").read_text().splitlines()) == 2
