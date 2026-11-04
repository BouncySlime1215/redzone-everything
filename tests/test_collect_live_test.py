import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


def test_collects_rows_since_start_masks_lan_and_summarizes(tmp_path):
    import collect_live_test as c
    now = time.time()
    d = tmp_path / "data"
    (d / "recordings" / time.strftime("%Y-%m-%d")).mkdir(parents=True)
    rows = [{"ts": now - 10, "verified": True, "total_s": 1.9, "host": "192.168.1.150"},
            {"ts": now - 5, "verified": True, "total_s": 2.4}, {"ts": now - 90000, "verified": True, "total_s": 9.0},
            {"ts": now - 3, "verified": False}]
    (d / "tv_input.jsonl").write_text("\n".join(map(json.dumps, rows)) + "\nnot json\n")
    (d / "supervisor_cuts.jsonl").write_text(json.dumps({"ts": now - 4, "enter": "play", "leave": "dead"}) + "\n")
    (d / "recordings" / time.strftime("%Y-%m-%d") / "sync_probe.jsonl").write_text(
        json.dumps({"ts": now - 2, "mac": "aa:bb:cc:dd:ee:ff"}) + "\n")
    (d / "traces.jsonl").write_text("\n".join(map(json.dumps, [
        {"kind": "user", "decided_at": now - 9, "cut_at": now - 8, "picture_at": now - 6.5},
        {"kind": "user", "decided_at": now - 7},                       # a press that never reached the TV
        {"kind": "auto", "decided_at": now - 6, "cut_at": now - 5, "picture_at": now - 3}])) + "\n")
    out = tmp_path / "out"
    assert c.main(["--data", str(d), "--out", str(out), "--since", str(int(now - 3600))]) == 0
    s = json.loads((out / "summary.json").read_text())
    assert s["switch_samples"] == 2 and s["switch_p95_s"] == 2.4 and s["tv_unverified"] == 1
    assert s["entries_mid_play"] == 1 and s["rows"]["traces.jsonl"] == 3
    assert s["press_samples"] == 1 and s["press_to_tv_p95_s"] == 2.5 and s["press_no_picture"] == 1
    text = (out / "tv_input.jsonl").read_text() + (out / "sync_probe.jsonl").read_text()
    assert "192.168" not in text and "aa:bb" not in text and "\"total_s\": 9.0" not in text
    assert (d / "tv_input.jsonl").read_text().count("\n") == 5      # source untouched
