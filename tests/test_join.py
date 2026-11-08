"""[Join API] redzone/tuners/join: plan (YouTube TV 16.8 s per real LEFT, k = round((T - 28.5) / 16.8) >= 1, Nick's
depth sweep Nov 7; Prime any target),
predicted vs measured landing, the error logged. Offline: the fake sticks of the scrub and Prime pause tests."""
import asyncio
import json

import pytest

from redzone.tuners import join as jn
from redzone.tuners import prime_pause as pp
from tests.test_prime_pause import Clock, FakePrime
from tests.test_yttv_seek import CFG, YT, FakeYTTV, reader


# Nick's depth sweep (Nov 7 13:48Z, ESPN, retuned before each join): k real LEFTs -> landed (label read included)
SWEEP = {1: [45.1, 45.8, 44.9], 2: [63.0, 62.1], 4: [94.1, 94.2, 96.5], 6: [128.5, 128.1, 131.6]}


@pytest.mark.parametrize("t,k,pred", [(45, 1, 45.3), (60, 2, 62.1), (75, 3, 78.9), (90, 4, 95.7), (120, 5, 112.5),
                                      (130, 6, 129.3)])
def test_yttv_plan_is_the_depth_fit(t, k, pred):
    p = jn.plan_join("youtubetv:espn", t)
    assert p.method == "scrub" and p.lefts == k and p.presses == k + 1 and p.predicted_s == pytest.approx(pred)
    assert p.expected_err_s == 3


@pytest.mark.parametrize("k,landed", [(k, m) for k, ms in SWEEP.items() for m in ms])
def test_every_sweep_landing_verifies_on_the_depth_fit(k, landed):
    """15 n + 19.2 drifted -3.9 at 45 .. +4.9 at 120 (the 131.6 was off_target). 16.8 k + 28.5 holds them all."""
    from redzone.tuners import join_watchdog as jw
    pred = 16.8 * k + 28.5
    assert abs(landed - pred) < 2.5
    v = jw.verify("youtubetv", pred, landed, pred, read_adds_s=7.5)
    assert v.status == "verified" and v.buffered


def test_a_shallow_target_scrubs_one_left_never_zero():
    """T30 planned zero LEFTs: a scrub that seeks nothing, landed 10-14 s, off_target twice. Now one LEFT, ~45 s."""
    p = jn.plan_join("youtubetv", 30)
    assert p.lefts == 1 and p.presses == 2 and p.predicted_s == pytest.approx(45.3) and "shallowest" in p.reason
    assert jn.achievable("youtubetv", 30) == pytest.approx(45.3) == jn.achievable("youtubetv", 10)


def test_yttv_plan_counts_where_the_stick_already_is():
    assert jn.plan_join("youtubetv", 90, behind_before_s=25).lefts == round((90 - 25 - 28.5) / 16.8)


def test_prime_plan_holds_the_gap_at_any_target():
    p = jn.plan_join("prime:x", 67.0, behind_before_s=6.6)
    assert p.method == "pause" and p.hold_s == pytest.approx(60.4) and p.predicted_s == pytest.approx(67.0)


def test_no_method_and_past_the_cap():
    assert jn.plan_join("espn:x", 60).method == "none"
    assert jn.plan_join("youtubetv", 400).method == "none"
    assert jn.achievable("youtubetv", 80) == pytest.approx(78.9) and jn.achievable("prime", 80) == 80


def test_yttv_join_sends_the_planned_presses_and_logs_the_error(tmp_path):
    s = FakeYTTV(label0=3600.0)

    async def label(_s):
        return 64.9   # landed 57.4, + the label read's own 7.5 s (join.yttv_read_s); predicted 62.1
    log = tmp_path / "joins.jsonl"
    r = asyncio.run(jn.join(s, "youtubetv", 60, cfg=CFG, pkg=YT, read_overlay=reader(s), read_behind=label,
                            log_path=log, label="stickB", channel="espn"))
    # the RIGHT probe (default) leaves the knob at live, then n - 1 = 2 LEFTs: 30 s (Nick, 12f405ab, Nov 7)
    assert r.ok and s.keys.count("KEYCODE_DPAD_RIGHT") == 1 and s.keys.count("KEYCODE_DPAD_LEFT") == 2
    assert s.committed_back == 30.0
    assert r.measured_s == 64.9 and r.error_s == pytest.approx(4.9) and r.pred_error_s == pytest.approx(2.8)
    row = json.loads(log.read_text().splitlines()[-1])
    assert row["stick"] == "stickB" and row["channel"] == "espn" and row["presses"] == 3 and row["lefts"] == 2 and row["error_s"] == 4.9 and row["read_s"] == 7.5
    assert not any(":" in str(v) and "." in str(v) for v in row.values() if isinstance(v, str))   # no address
    assert "RIGHT probe" in row["steps"] and "LEFT x2" in row["steps"]
    assert row["room_s"] == 3600.0   # the bar's label: how far back this program lets the scrub go   # the trace of what the helper did


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
