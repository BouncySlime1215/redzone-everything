"""[Join API] redzone/tuners/join: plan (YouTube TV steps of 15 s with Nick's n = round((T-10)/15); Prime any target),
predicted vs measured landing, the error logged. Offline: the fake sticks of the scrub and Prime pause tests."""
import asyncio
import json

import pytest

from redzone.tuners import join as jn
from redzone.tuners import prime_pause as pp
from tests.test_prime_pause import Clock, FakePrime
from tests.test_yttv_seek import CFG, YT, FakeYTTV, reader


@pytest.mark.parametrize("t,n,pred", [(60, 3, 64.2), (75, 4, 79.2), (90, 5, 94.2), (30, 1, 34.2)])
def test_yttv_plan_is_nicks_n_and_the_measured_lead(t, n, pred):
    p = jn.plan_join("youtubetv:espn", t)
    assert p.method == "scrub" and p.presses == n and p.predicted_s == pytest.approx(pred) and p.expected_err_s == 3


def test_yttv_plan_counts_where_the_stick_already_is():
    assert jn.plan_join("youtubetv", 90, behind_before_s=25).presses == round((90 - 25 - 10) / 15)


def test_prime_plan_holds_the_gap_at_any_target():
    p = jn.plan_join("prime:x", 67.0, behind_before_s=6.6)
    assert p.method == "pause" and p.hold_s == pytest.approx(60.4) and p.predicted_s == pytest.approx(67.0)


def test_no_method_and_past_the_cap():
    assert jn.plan_join("espn:x", 60).method == "none"
    assert jn.plan_join("youtubetv", 400).method == "none"
    assert jn.achievable("youtubetv", 80) == pytest.approx(94.2) and jn.achievable("prime", 80) == 80


def test_yttv_join_sends_the_planned_presses_and_logs_the_error(tmp_path):
    s = FakeYTTV(label0=3600.0)

    async def label(_s):
        return 64.9   # landed 57.4, + the label read's own 7.5 s (join.yttv_read_s)
    log = tmp_path / "joins.jsonl"
    r = asyncio.run(jn.join(s, "youtubetv", 60, cfg=CFG, pkg=YT, read_overlay=reader(s), read_behind=label,
                            log_path=log, label="stickB", channel="espn"))
    # the RIGHT probe (default) leaves the knob at live, then n - 1 = 2 LEFTs: 30 s (Nick, 12f405ab, Nov 7)
    assert r.ok and s.keys.count("KEYCODE_DPAD_RIGHT") == 1 and s.keys.count("KEYCODE_DPAD_LEFT") == 2
    assert s.committed_back == 30.0
    assert r.measured_s == 64.9 and r.error_s == pytest.approx(4.9) and r.pred_error_s == pytest.approx(0.7)
    row = json.loads(log.read_text().splitlines()[-1])
    assert row["stick"] == "stickB" and row["channel"] == "espn" and row["presses"] == 3 and row["error_s"] == 4.9 and row["read_s"] == 7.5
    assert not any(":" in str(v) and "." in str(v) for v in row.values() if isinstance(v, str))   # no address


def test_yttv_join_without_a_label_is_never_a_measurement(tmp_path):
    s = FakeYTTV(label0=3600.0)
    r = asyncio.run(jn.join(s, "youtubetv", 60, cfg=CFG, pkg=YT, read_overlay=reader(s), log_path=tmp_path / "j"))
    assert r.ok and r.measured_s is None and r.error_s is None and r.estimate_s is not None


def test_prime_join_measures_on_the_timecode(tmp_path, monkeypatch):
    c = Clock()
    s = FakePrime(c)

    async def fake_sleep(x):
        c.t += max(0.0, float(x))
    monkeypatch.setattr(pp.asyncio, "sleep", fake_sleep)

    async def read(_s):
        return float(int(s.position()))
    r = asyncio.run(jn.join(s, "prime", 67.0, behind_before_s=6.6, read_timecode=read, log_path=tmp_path / "j",
                            clock=c))
    assert r.ok and r.plan.hold_s == pytest.approx(60.4)
    assert abs(r.error_s) <= 2.0 and r.measured_s is not None


def test_failure_is_logged_and_never_raises(tmp_path):
    s = FakeYTTV(front="com.amazon.firebat")
    r = asyncio.run(jn.join(s, "youtubetv", 60, cfg=CFG, pkg=YT, read_overlay=reader(s), log_path=tmp_path / "j"))
    assert not r.ok and "not in front" in r.reason and s.keys == []
    assert json.loads((tmp_path / "j").read_text())["ok"] is False
