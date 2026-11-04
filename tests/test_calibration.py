"""[calib.auto] Nightly self-calibration of the timing half (redzone/calibration.py)."""
import json
from datetime import date, timedelta

from redzone import calibration as cal


def _night(root, d, flip, n=40):
    return cal.merge(root, d, {"tv_flip_p50_s": (flip, n, "test"), "tv_flip_max_s": (flip + 0.5, n, "test")})


def test_first_nights_are_accepted_then_an_outlier_is_refused(tmp_path):
    d0 = date(2026, 11, 1)
    for i, x in enumerate([1.89, 1.92, 1.88, 1.95, 1.90]):
        v = _night(tmp_path, d0 + timedelta(days=i), x)
        assert v["tv_flip_p50_s"]["accepted"]
    bad = _night(tmp_path, d0 + timedelta(days=5), 4.8)                 # a night with the TV on Wi-Fi trouble
    assert not bad["tv_flip_p50_s"]["accepted"] and "MADs" in bad["tv_flip_p50_s"]["reason"]
    assert cal.latest(tmp_path)["tv_flip_p50_s"]["value"] == 1.90       # the refused night is never read back
    ok = _night(tmp_path, d0 + timedelta(days=6), 1.97)
    assert ok["tv_flip_p50_s"]["accepted"] and ok["tv_flip_p50_s"]["ref"] == 1.9


def test_too_few_samples_is_refused_and_lane_two_keys_share_the_file(tmp_path):
    d = date(2026, 11, 4)
    cal.merge(tmp_path, d, {"tv_flip_p95_s": (2.1, 3, "test")})
    cal.merge(tmp_path, d, {"yttv_seek_step_s": (15.0, 12, "lane 2"), "delay_s:youtubetv:espn": (26.4, 30, "sync", 1.2)},
              section="sync")
    doc = json.loads((tmp_path / "calib" / "2026-11-04.json").read_text())
    assert not doc["timing"]["tv_flip_p95_s"]["accepted"]
    assert doc["sync"]["yttv_seek_step_s"]["accepted"] and "delay_s:youtubetv:espn" in doc["sync"]
    assert cal.latest(tmp_path, "sync")["yttv_seek_step_s"]["value"] == 15.0 and "yttv_seek_step_s" not in cal.latest(tmp_path)
    assert cal.latest(tmp_path, "sync")["delay_s:youtubetv:espn"]["sd"] == 1.2


def test_the_flip_prior_is_read_back_only_with_calib_auto_on(tmp_path):
    from redzone.config import Config
    cfg = Config(tmp_path / "c.yaml")
    _night(tmp_path, date(2026, 11, 4), 1.85)
    assert cal.flip_prior(cfg, tmp_path) is None
    cfg.update({"calib": {"auto": True}})
    assert cal.flip_prior(cfg, tmp_path) == (1.85, 2.35)


def test_summary_feeds_the_timing_half():
    s = {"switch_samples": 30, "switch_p50_s": 1.89, "switch_p95_s": 1.98, "switch_max_s": 2.02,
         "press_samples": 4, "press_to_tv_p50_s": 2.6, "press_to_tv_p95_s": 3.4}
    m = cal.timing_from_summary(s)
    assert m["tv_flip_p50_s"] == (1.89, 30, "tv_input.jsonl verified total_s") and m["press_to_tv_p95_s"][1] == 4


def test_the_controller_plans_with_the_calibrated_prior(tmp_path):
    from redzone import control as ctl
    from redzone.channels import ChannelMap
    from redzone.config import Config
    c = ctl.Controller(Config(tmp_path / "c.yaml"), ChannelMap(tmp_path / "ch.json"))
    assert c._tv_prior() == (1.89, 2.58)
    c.calibrated_flip = (1.8, 2.1)
    assert c._tv_prior() == (1.8, 2.1)
