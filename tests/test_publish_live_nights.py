import json

from scripts import publish_live_nights as pl


def _summary(**kw):
    s = {"switch_samples": 12, "switch_p95_s": 2.2, "entries_mid_play": 0, "press_samples": 5,
         "press_to_tv_p95_s": 2.9, "picture_within_3s": "11/12", "since": "2026-11-04T18:00:00",
         "landings": {"n": 9, "after_dead_p50_s": 3.1, "before_play_min_s": 4.0, "before_play_p50_s": 12.5},
         "sync_anchor_err": {"HDMI1": {"n": 30, "p50_s": 0.8, "p95_s": 2.1}},
         "rows": {"tv_input.jsonl": 12, "traces.jsonl": 40}}
    s.update(kw)
    return s


def _night(root, night, s):
    (root / night).mkdir(parents=True)
    (root / night / "summary.json").write_text(json.dumps(s))


def test_publishes_aggregates_only(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    _night(src, "2026-11-04", _summary(host="192.168.1.150", path="/Users/x/data", note="10.0.0.5"))
    log = pl.publish([src], out)
    assert log[0]["status"] == "published" and {"host", "path", "note"} <= set(log[0]["dropped"])
    got = json.loads((out / "2026-11-04" / "summary.json").read_text())
    assert got["switch_p95_s"] == 2.2 and got["landings"]["n"] == 9
    assert got["sync_anchor_err"]["HDMI1"]["p95_s"] == 2.1
    text = (out / "2026-11-04" / "summary.json").read_text()
    assert "192.168" not in text and "/Users" not in text and "10.0.0.5" not in text


def test_refuses_odd_strings_and_stick_names(tmp_path):
    c = pl.clean(_summary(picture_within_3s="lan 192.168.1.2", since="nick's mac",
                          sync_anchor_err={"192.168.1.150": {"n": 1, "p95_s": 1.0}, "HDMI2": {"n": 2, "p95_s": "x"}},
                          rows={"../secret": 3, "sync.jsonl": 4}))
    assert "picture_within_3s" not in c and "since" not in c
    assert set(c["sync_anchor_err"]) == {"HDMI2"} and c["sync_anchor_err"]["HDMI2"] == {"n": 2}
    assert c["rows"] == {"sync.jsonl": 4}


def test_keeps_published_night_and_skips_bad_folders(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    _night(src, "2026-11-04", _summary())
    _night(src, "not-a-night", _summary())
    pl.publish([src], out)
    (src / "2026-11-04" / "summary.json").write_text(json.dumps(_summary(switch_p95_s=9.9)))
    assert pl.publish([src], out)[0]["status"] == "already published"
    assert json.loads((out / "2026-11-04" / "summary.json").read_text())["switch_p95_s"] == 2.2
    assert not (out / "not-a-night").exists()
    pl.publish([src], out, force=True)
    assert json.loads((out / "2026-11-04" / "summary.json").read_text())["switch_p95_s"] == 9.9


def test_check_writes_nothing(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    _night(src, "2026-11-05", _summary())
    assert pl.publish([src], out, check=True)[0]["status"] == "would publish"
    assert not out.exists()
