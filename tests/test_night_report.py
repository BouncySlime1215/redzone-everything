"""Night health report (scripts/night_report.py, flag report.nightly): docs/nights/YYYY-MM-DD.md."""
import json
from datetime import date, datetime, timezone


from scripts import collect_live_test as clt
from scripts import night_report as nr
from scripts import publish_live_nights as pl

NIGHT = date(2026, 11, 4)


def _write(root, s):
    (root / NIGHT.isoformat()).mkdir(parents=True)
    (root / NIGHT.isoformat() / "summary.json").write_text(json.dumps(s))


def test_no_night_is_pending(tmp_path):
    p = nr.write(NIGHT, [tmp_path / "none"], tmp_path / "out")
    assert p.name == "2026-11-04.md" and "PENDING: no live night" in p.read_text()


def _night_rows():
    t = datetime(2026, 11, 5, 1, 30, tzinfo=timezone.utc).timestamp()      # 8:30 PM ET
    cuts = [{"ts": t, "to_label": "BOS @ WSH", "from_label": "LAL @ GSW", "enter": "dead", "leave": "dead",
             "enter_after_dead_s": 3.0, "enter_before_play_s": 20.0},
            {"ts": t + 600, "to_label": "NYR @ WSH", "from_label": "BOS @ WSH", "enter": "play", "leave": "dead",
             "enter_why": "faceoff came early (pbp)"}]
    sup = [{"ts": t, "kind": "fix", "rule": "stick_offline", "ok": True, "detail": "192.168.1.181"},
           {"ts": t + 5, "kind": "fix", "rule": "stick_offline", "ok": False}]
    tv = [{"ts": t, "verified": True, "total_s": 1.9}, {"ts": t + 1, "verified": True, "total_s": 2.1}]
    return {"supervisor_cuts.jsonl": cuts, "supervisor.jsonl": sup, "tv_input.jsonl": tv, "traces.jsonl": [],
            "sync.jsonl": [{"kind": "flip", "err": -0.8, "stick": "HDMI1"}, {"kind": "score", "err": 1.6, "stick": "HDMI2"}],
            "sync_probe.jsonl": []}


def test_report_matches_the_live_log_through_publish(tmp_path):
    s = clt.summarize(_night_rows())
    assert s["landings_graded"] == 2 and s["landings_dead"] == 1
    assert s["misses"] == [{"at": "20:40", "game": "NYR @ WSH", "end": "enter", "why": "faceoff came early (pbp)"}]
    assert s["faults_fixed"] == {"stick_offline": {"n": 2, "failed": 1}}
    _write(tmp_path / "mac", s)
    pl.publish([tmp_path / "mac"], tmp_path / "pub")
    text = nr.write(NIGHT, [tmp_path / "pub"], tmp_path / "out").read_text()
    assert "| Mid-play cuts + entries | 0 | 1 of 2 cuts | ❌ FAIL |" in text
    assert "| Landings in dead time | >= 98% | 50.0% (1/2) | ❌ FAIL |" in text
    assert "| 20:40 | NYR @ WSH | enter | faceoff came early (pbp) |" in text
    assert "| HDMI1 | 1 | 0.8 s | 0.8 s |" in text and "| stick_offline | 2 | 1 |" in text
    assert "Press-to-TV p95 | <= 3 s | – (n=0) | ⏳ PENDING" in text
    assert "192.168" not in text


def test_clean_night_passes_and_private_strings_never_publish(tmp_path):
    s = {"cuts_graded": 10, "entries_mid_play": 0, "leaves_mid_play": 0, "landings_graded": 10, "landings_dead": 10,
         "press_samples": 4, "press_to_tv_p95_s": 2.7, "press_to_tv_p50_s": 2.3,
         "misses": [{"at": "21:00", "game": "lan.150 192.168.1.150", "end": "enter", "why": "x"}]}
    c = pl.clean(s)
    assert c["misses"][0]["game"] == "?"
    rows = {a: d for a, _, _, d in nr.lines_graded(c)}
    assert rows["Mid-play cuts + entries"] == rows["Landings in dead time"] == rows["Press-to-TV p95"] == "✅ PASS"


def test_flag_is_off_by_default_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(nr, "OUT", tmp_path / "out")
    assert nr.enabled() is False
    assert nr.main(["--out", str(tmp_path / "out")]) == 0 and not (tmp_path / "out").exists()


def test_last_night_is_the_evening_before_the_morning():
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    assert nr.last_night(datetime(2026, 11, 5, 6, 30, tzinfo=et)) == NIGHT
    assert nr.last_night(datetime(2026, 11, 4, 23, 0, tzinfo=et)) == NIGHT
