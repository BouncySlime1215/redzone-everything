"""[WV loop item 7] scripts/sync_truth_wv.py: per-stick delay error at each switch landing."""
import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
spec = importlib.util.spec_from_file_location("sync_truth_wv", Path(__file__).parent.parent / "scripts/sync_truth_wv.py")
wv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wv)

T = 1793846400.0   # Nov 4 evening in New York


def _j(rows):
    return [json.dumps(r) for r in rows]


def test_landing_joins_first_anchor_after_picture_and_never_a_tap(tmp_path):
    traces = _j([
        {"action": "switch", "ok": True, "dry_run": False, "to": "401", "target": "youtubetv:espn",
         "picture_at": T, "stick": "10.0.0.9:5555"},
        {"action": "switch", "ok": True, "dry_run": True, "to": "402", "target": "youtubetv:tnt", "picture_at": T},
        {"action": "switch", "ok": False, "to": "403", "target": "youtubetv:tnt", "picture_at": None},
        {"action": "switch", "ok": True, "dry_run": False, "to": "404", "target": "prime:tnf", "picture_at": T + 50},
    ])
    sync = _j([
        {"t": T - 5, "target": "youtubetv:espn", "kind": "clock", "z": 50.0, "err": 9.0, "stick": "HDMI1"},
        {"t": T + 3, "target": "youtubetv:espn", "kind": "tap", "z": 51.0, "err": 4.0, "stick": "HDMI1"},
        {"t": T + 8, "target": "youtubetv:espn", "kind": "clock", "z": 52.0, "err": -2.5, "stick": "HDMI1"},
        {"t": T + 50 + 500, "target": "prime:tnf", "kind": "score", "z": 7.0, "err": 1.0, "stick": "HDMI2"},
    ])
    shadow = _j([{"gid": "401", "league": "nba"}, {"gid": "404", "league": "nfl"}])
    rs = wv.rows(traces, sync, shadow)
    assert [r["gid"] for r in rs] == ["401", "404"]
    a, b = rs
    assert a["league"] == "nba" and a["night"] == "20261104" and a["stick"] == "hdmi1"
    assert (a["delay_true_s"], a["delay_est_s"], a["delay_err_s"]) == (52.0, 54.5, -2.5)
    assert a["source"] == "yttv" and a["anchor_lag_s"] == 8.0
    assert "10.0.0.9" not in json.dumps(rs)          # stick hosts never leave the Mac
    assert b["delay_err_s"] is None and b["stick"] is None   # no anchor in the window: a row, no number
    assert wv.write(rs, tmp_path) == {"nba/20261104": 1, "nfl/20261104": 1}
    assert json.loads((tmp_path / "nba" / "20261104.jsonl").read_text())["delay_err_s"] == -2.5
