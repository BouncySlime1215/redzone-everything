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
        {"kind": "user", "decided_at": now - 9, "cut_at": now - 8, "picture_at": now - 6.5,
         "spans": {"queued": 0.1, "flip": 1.9, "to_picture": 2.5, "total": 3.0}},
        {"kind": "user", "decided_at": now - 7},                       # a press that never reached the TV
        {"kind": "auto", "decided_at": now - 6, "cut_at": now - 5, "picture_at": now - 3}])) + "\n")
    out = tmp_path / "out"
    assert c.main(["--data", str(d), "--out", str(out), "--since", str(int(now - 3600))]) == 0
    s = json.loads((out / "summary.json").read_text())
    assert s["switch_samples"] == 2 and s["switch_p95_s"] == 2.4 and s["tv_unverified"] == 1
    assert s["entries_mid_play"] == 1 and s["rows"]["traces.jsonl"] == 3
    assert s["press_samples"] == 1 and s["press_to_tv_p95_s"] == 2.5 and s["press_no_picture"] == 1
    assert s["press_spans"]["flip"] == {"n": 1, "p50_s": 1.9, "p95_s": 1.9} and "total" not in s["press_spans"]
    assert "Press spans" in (out / "summary.md").read_text()
    text = (out / "tv_input.jsonl").read_text() + (out / "sync_probe.jsonl").read_text()
    assert "192.168" not in text and "aa:bb" not in text and "\"total_s\": 9.0" not in text
    assert (d / "tv_input.jsonl").read_text().count("\n") == 5      # source untouched


def test_live_night_rows_landings_and_sync_anchor_error_per_stick(tmp_path):
    import collect_live_test as c
    now = time.time()
    d = tmp_path / "data"
    d.mkdir()
    (d / "supervisor_cuts.jsonl").write_text("\n".join(map(json.dumps, [
        {"ts": now - 50, "enter": "dead", "enter_after_dead_s": 4.0, "enter_before_play_s": 12.0},
        {"ts": now - 40, "enter": "dead", "enter_after_dead_s": 9.0, "enter_before_play_s": 3.0},
        {"ts": now - 30, "enter": "play"}])) + "\n")
    (d / "sync.jsonl").write_text("\n".join(map(json.dumps, [
        {"t": now - 20, "kind": "flip", "target": "youtubetv:espn", "err": -1.5, "stick": "HDMI1"},
        {"t": now - 19, "kind": "score", "target": "youtubetv:espn", "err": 3.0, "stick": "HDMI1"},
        {"t": now - 18, "kind": "flip", "target": "youtubetv:fox", "err": 0.5, "stick": "HDMI2"},
        {"t": now - 17, "kind": "position", "target": "youtubetv:fox", "position": 1.0},
        {"t": now - 99999, "kind": "flip", "err": 50.0, "stick": "HDMI2"}])) + "\n")
    s = c.collect(d, tmp_path / "out", now - 3600, now)
    assert s["landings"] == {"n": 2, "after_dead_p50_s": 9.0, "before_play_min_s": 3.0, "before_play_p50_s": 12.0}
    assert s["sync_anchor_err"]["HDMI1"]["n"] == 2 and s["sync_anchor_err"]["HDMI1"]["p95_s"] == 3.0
    assert s["sync_anchor_err"]["HDMI2"] == {"n": 1, "p50_s": 0.5, "p95_s": 0.5}
    assert (tmp_path / "out" / "summary.json").exists()


def test_the_supervisor_collects_each_live_night_by_itself_once(tmp_path):
    from datetime import date
    from types import SimpleNamespace
    from redzone.supervisor import Supervisor, night_window
    from redzone.config import Config
    night = date(2026, 11, 4)
    lo, hi = night_window(night)
    (tmp_path / "tv_input.jsonl").write_text(json.dumps({"ts": lo + 60, "verified": True, "total_s": 2.0}) + "\n"
                                             + json.dumps({"ts": hi + 60, "verified": True, "total_s": 9.0}) + "\n")
    e = SimpleNamespace(store=None, cfg=Config(tmp_path / "c.yaml"), decider=SimpleNamespace(timelines={}, safe_mode=False))
    sup = Supervisor(e, root=tmp_path, clock=lambda: hi + 3600)
    out = sup.collect_live_night(night)
    s = json.loads((out / "summary.json").read_text())
    assert s["switch_samples"] == 1 and s["switch_p95_s"] == 2.0        # only that night's rows
    assert sup.collect_live_night(night) is None                         # once
