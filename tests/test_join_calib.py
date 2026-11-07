"""[Join self-calibration, Nov 7 item 5] redzone/tuners/join_calib: per channel and stick, bounded, robust, from
data/joins.jsonl. The rows below are the real Nov 5-7 measurements and failures, as join rows."""
import asyncio
import json

import pytest

from redzone.tuners import join as jn
from redzone.tuners import join_calib as jc
from tests.test_yttv_seek import CFG, YT, Cfg, FakeYTTV, reader

NICK_ERRORS = [-4.3, -3.9, -5.3, -1.6, -4.6, -4.6, -7.7, 1.0, 1.6]   # 9 label-measured joins, Nov 7
TARGETS = [60, 75, 90] * 3
CHANNELS = ["nbatv", "espn", "fs1", "accn"]


def row(target, error, channel="espn", stick="stickB", app="youtubetv", ok=True, measured=True, ts=None, **kw):
    p = jn.plan_join(app, target)
    m = round(target + error, 2) if measured else None
    r = {"ts": ts, "app": app, "method": p.method, "target_s": target, "presses": p.presses, "hold_s": p.hold_s,
         "predicted_s": p.predicted_s, "ok": ok, "measured_s": m if ok else None,
         "error_s": round(m - target, 2) if (ok and m is not None) else None,
         "pred_error_s": round(m - p.predicted_s, 2) if (ok and m is not None) else None,
         "channel": channel, "stick": stick}
    r.update(kw)
    return r


def nick_rows(**kw):
    return [row(t, e, channel=CHANNELS[i % 4], **kw) for i, (t, e) in enumerate(zip(TARGETS, NICK_ERRORS))]


def test_nicks_nine_runs_keep_the_shipped_lead_and_say_plus_minus_three():
    c = jc.JoinCalib(nick_rows())
    b = c.bias("youtubetv")
    assert b.source == "app" and b.n == 9 and b.value == pytest.approx(11.7, abs=0.15)
    assert 1.0 <= b.err_s <= 3.0 and not c.alerts()
    assert c.stats("youtubetv").mean_error_s == pytest.approx(-3.27, abs=0.01)


def test_calibration_off_plans_exactly_nicks_rule():
    for t in range(20, 151, 5):
        p = jn.plan_join("youtubetv", t)
        assert p.presses == max(1, round((t - 10) / 15)) and p.bias_source == "shipped" and p.bias_used_s == 11.7


def test_at_the_shipped_lead_the_calibrated_plan_is_nicks_rule_too():
    b = jc.Bias(11.7, 3.0, "app", 9)
    for t in range(20, 151, 5):
        assert jn.plan_join("youtubetv", t, bias=b).presses == jn.plan_join("youtubetv", t).presses


def test_online_replay_from_a_naive_lead_learns_the_measured_one():
    """Before Nick's 9 runs the plan aimed n = round((T-10)/15) at 15 n + 10 (lead 10). Replayed in order, each join
    predicted only from the ones before it: calibration must beat the naive lead out of sample."""
    cfg = Cfg({"join.yttv_lead_s": 10.0})
    rows = []
    for t, e in zip(TARGETS, NICK_ERRORS):
        r = row(t, e)
        r["bias_used_s"] = 11.7   # the measured landing is what it is; the row says which lead its plan used
        rows.append(r)
    out = jc.replay(rows, cfg)
    assert out["n"] == 9 and out["calib_mae_s"] < out["shipped_mae_s"]


def test_gross_rows_never_move_the_bias():
    """Overlay held for an in-between target landed -18.7 s; #391 'asked 90, read 23' (the stick was not at live)."""
    rows = nick_rows() + [row(75, -18.7 - 3.3), row(90, 23 - 90)]
    c = jc.JoinCalib(rows)
    assert c.stats("youtubetv").gross == 2 and c.bias("youtubetv").value == pytest.approx(11.7, abs=0.15)


def test_unmeasured_and_failed_joins_are_counted_not_used():
    """Nov 6 21:45Z: the 75 s / 79.66 s 'hold' was a flat meter (session stopped updating): an estimate, never truth.
    Nov 7 ~1 AM: a program boundary reset the label ('moved 39 s, not 60 s'): the join failed closed."""
    rows = nick_rows() + [row(75, 4.66, measured=False, estimate_s=79.66),
                          row(60, 0, ok=False, reason="knob moved 39s, not 60s")]
    s = jc.JoinCalib(rows).stats("youtubetv")
    assert s.unmeasured == 1 and s.failed == 1 and s.n == 9


def test_bounds_and_a_shift_alert_when_the_app_changes():
    rows = nick_rows() + [row(60, 8.0) for _ in range(20)]   # every join suddenly lands ~11 s later than predicted
    c = jc.JoinCalib(rows)
    b = c.bias("youtubetv")
    assert b.value <= 11.7 + 5 + 1e-9 and b.clamped
    assert any("landed later" in a for a in c.alerts())


def test_one_channel_moves_on_its_own_and_others_stay():
    rows = nick_rows() + [row(60, -1.0, channel="tnt") for _ in range(10)]   # TNT lands 2.3 s later than the rest
    c = jc.JoinCalib(rows)
    tnt, espn = c.bias("youtubetv", "tnt", "stickB"), c.bias("youtubetv", "espn", "stickB")
    assert tnt.source == "app:channel:stick" and tnt.value > espn.value + 1.0
    assert abs(espn.value - c.bias("youtubetv").value) < 1.5


def test_a_single_join_on_a_new_channel_moves_it_a_fifth_of_the_way():
    c = jc.JoinCalib(nick_rows() + [row(60, 2.0, channel="tbs")])
    parent = c.stats("youtubetv")
    ch = c.stats("youtubetv:tbs")
    seen = 56.7 + 5.3 - 56.7 + 11.7          # landed 62.0 vs predicted 56.7: lead seen 17.0
    clipped = min(seen, parent.value + 2 * parent.err_s)
    assert ch.n == 1 and ch.value == pytest.approx((clipped + 4 * parent.value) / 5, abs=0.02)


def test_prime_bias_shortens_the_hold_so_the_landing_is_the_target():
    p = jn.plan_join("prime", 60.0, bias=jc.Bias(1.5, 1.2, "app:channel", 6))
    assert p.hold_s == pytest.approx(58.5) and p.predicted_s == pytest.approx(60.0) and p.expected_err_s == 1.2
    assert jn.plan_join("prime", 60.0).hold_s == 60.0   # calibration off: unchanged


def test_prime_rows_calibrate_within_three_seconds():
    rows = [row(60, 2.0, app="prime", channel="tnf") for _ in range(30)]
    c = jc.JoinCalib(rows)
    assert 0 < c.bias("prime", "tnf", "stickB").value <= 3.0


def test_addresses_never_become_keys():
    c = jc.JoinCalib([row(60, -3, stick="203.0.113.5:5555")])
    assert all("203" not in k for k in c.keys()) and "youtubetv:espn:stick?" in c.keys()


def test_old_rows_are_dropped():
    c = jc.JoinCalib([row(60, -3, ts=0.0)], now=40 * 86400)
    assert c.keys() == []


def test_edge_stats_are_bounded_and_per_channel_and_stick():
    rows = [row(60, -3, d_edge_s=x) for x in (25.0, 26.0, 26.5, 80.0)]
    e = {d["key"]: d for d in jc.JoinCalib(rows).edge_stats()}
    assert e["espn"]["n"] == 3 and e["espn"]["d_edge_s"] == 26.0 and e["espn"]["vs_prior_s"] == pytest.approx(0.1)


def test_join_uses_the_calibration_and_logs_what_it_used(tmp_path):
    log = tmp_path / "joins.jsonl"
    log.write_text("\n".join(json.dumps(r) for r in nick_rows()) + "\n")
    s = FakeYTTV(label0=3600.0)

    async def label(_s):
        return 57.4
    cfg = Cfg({**CFG, "join.calib.enabled": True})
    r = asyncio.run(jn.join(s, "youtubetv", 60, cfg=cfg, pkg=YT, read_overlay=reader(s), read_behind=label,
                            log_path=log, label="stickB", channel="espn", source="canary"))
    last = json.loads(log.read_text().splitlines()[-1])
    assert r.ok and last["bias_source"] in ("app", "app:channel", "app:channel:stick") and last["source"] == "canary"
    assert last["bias_used_s"] == pytest.approx(11.7, abs=1.0)   # ESPN ran ~1 s early in Nick's runs


def test_join_with_calibration_off_is_unchanged(tmp_path):
    s = FakeYTTV(label0=3600.0)
    r = asyncio.run(jn.join(s, "youtubetv", 60, cfg=CFG, pkg=YT, read_overlay=reader(s), log_path=tmp_path / "j"))
    assert r.plan.bias_source == "shipped" and r.plan.presses == 3 and r.plan.predicted_s == 56.7


def test_a_broken_calibration_file_never_blocks_a_join(tmp_path):
    log = tmp_path / "joins.jsonl"
    log.write_text("not json\n{\"app\": \"youtubetv\", \"ok\": true, \"pred_error_s\": \"x\"}\n")
    s = FakeYTTV(label0=3600.0)
    r = asyncio.run(jn.join(s, "youtubetv", 60, cfg=Cfg({**CFG, "join.calib.enabled": True}), pkg=YT,
                            read_overlay=reader(s), log_path=log))
    assert r.plan.presses == 3
