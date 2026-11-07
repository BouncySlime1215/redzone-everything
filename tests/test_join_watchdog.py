"""[Join watchdog + drift monitor] Nick's Nov 7 sync order items 1 and 3 (redzone/tuners/join_watchdog.py).
Every case below replays a REAL failure or measurement from the Nov 5-7 stick runs (runbook: join-watchdog.md)."""
import asyncio
import json
from types import SimpleNamespace as NS

import pytest

from redzone.tuners import join as jn
from redzone.tuners import join_watchdog as jw
from tests.test_scrub_entry import Res, _preload, run, scrub  # noqa: F401  (fixture)
from tests.test_control import two  # noqa: F401  (fixture used by scrub)

# overseer's 9 label-measured YouTube TV joins (Nov 7): target, measured
OVERSEER_9 = [(60, 55.7), (60, 56.1), (75, 69.7), (75, 73.4), (90, 85.4), (90, 85.4), (60, 52.3), (75, 76.0),
              (90, 91.6)]


# ------------------------------------------------------------------------------------------------- verify
@pytest.mark.parametrize("target,measured", OVERSEER_9)
def test_the_nine_measured_joins_all_verify(target, measured):
    p = jn.plan_join("youtubetv", target)
    # read by hand (no probe freeze); a probe reading of the same landing carries the read's own 7.5 s
    v = jw.verify("youtubetv:espn", target, measured + p.read_s, p.predicted_s, read_adds_s=p.read_s)
    assert v.status == "verified" and v.buffered and v.action == "accept"


def test_nicks_90s_run_overshoot_is_off_target_then_reseek():
    """Nov 7 05:54Z: asked 90, 6 presses, the overlay was up 37.6 s -> 130.8 s behind (EXP-610)."""
    v = jw.verify("youtubetv", 90, 130.8, jn.plan_join("youtubetv", 90).predicted_s, reseeks_left=1)
    assert v.status == "off_target" and v.action == "reseek" and not v.buffered and v.off_target_s == 40.8
    assert jw.verify("youtubetv", 90, 130.8, None, reseeks_left=0).action == "fallback"


def test_same_channel_deep_link_short_landing_is_caught():
    """#391: a same-channel deep link didn't reset a stick that was behind: asked 90, read 23."""
    assert jw.verify("youtubetv", 90, 23.0, 86.7).status == "off_target"


def test_holding_the_overlay_between_steps_is_caught():
    """overseer Nov 7: holding the overlay for an in-between target landed -18.7 s off once."""
    assert jw.verify("youtubetv", 75, 75 - 18.7, 71.7).status == "off_target"


@pytest.mark.parametrize("m", [None])
def test_no_reading_is_never_buffered(m):
    """Nov 6 21:45Z: a 75 s 'hold' read off media_session was a flat meter (VOID). An estimate is not a reading."""
    v = jw.verify("youtubetv", 75, m, 71.7)
    assert v.status == "unmeasured" and not v.buffered and v.action == "fallback"


@pytest.mark.parametrize("m", [0.4, 4.9, 170.0])
def test_live_edge_or_runaway_is_out_of_range(m):
    """Plain pause snapped back to live (Nov 6); ACC Network stopped near 150 s (runaway)."""
    assert jw.verify("youtubetv", 60, m, 56.7, reseeks_left=1).status == "out_of_range"


def test_prime_pause_within_two_seconds_verifies_and_three_off_does_not_on_the_tight_margin():
    assert jw.verify("prime:x", 67.0, 67.7, 67.0).buffered          # EXP-608: 67.7 for 67
    assert jw.verify("prime:x", 60.0, 63.6, 60.0).buffered          # EXP-609 #389: gained 63.6
    assert jw.verify("prime:x", 60.0, 66.0, 60.0).status == "off_target"


def test_visible_stick_is_never_touched_and_failed_join_falls_back():
    assert jw.verify("youtubetv", 60, 57.0, 56.7, visible=True).action == "none"
    assert jw.verify("youtubetv", 60, None, 56.7, join_ok=False).status == "join_failed"


def test_config_overrides_margins_per_app():
    cfg = {"join.watchdog.margin_s": {"youtubetv": 2.0}}
    assert jw.verify("youtubetv", 60, 55.7, 56.7, cfg=cfg).buffered
    assert jw.verify("youtubetv", 60, 52.3, 56.7, cfg=cfg).status == "off_target"


# ------------------------------------------------------------------------------------------- watched_join
def _fake_join(landings):
    calls = []

    async def join_fn(stick, app, target_s, *, behind_before_s=0.0, **kw):
        calls.append(behind_before_s)
        m = landings[min(len(calls) - 1, len(landings) - 1)]
        plan = jn.plan_join(app, target_s, behind_before_s)
        return NS(ok=m is not False, measured_s=None if m in (None, False) else m, plan=plan)
    return join_fn, calls


def _relinker(ok=True):
    n = []

    async def relink():
        n.append(1)
        return ok
    return relink, n


def test_watched_join_accepts_a_verified_landing_and_logs_a_row(tmp_path):
    jf, calls = _fake_join([64.9])          # a label-probe reading: lands 57.4, + the read's own 7.5 s
    rl, n = _relinker()
    p = tmp_path / "joins.jsonl"
    out = run(jw.watched_join(object(), "youtubetv", 60, relink=rl, join_fn=jf, log_path=p, label="stickB",
                              channel="espn"))
    assert out.state == "buffered" and out.behind_s == 64.9 and out.attempts == 1 and not n
    row = json.loads(p.read_text().splitlines()[-1])
    assert row["kind"] == "watchdog" and row["state"] == "buffered" and row["stick"] == "stickB"


def test_watched_join_reseeks_once_from_live_then_accepts(tmp_path):
    jf, calls = _fake_join([130.8, 95.5])
    rl, n = _relinker()
    out = run(jw.watched_join(object(), "youtubetv", 90, relink=rl, join_fn=jf, log_path=""))
    assert out.state == "buffered" and out.behind_s == 95.5 and out.attempts == 2 and len(n) == 1
    assert calls == [0.0, 0.0]          # the re-seek starts from the live edge


def test_watched_join_off_twice_falls_back_to_live_never_buffered():
    jf, _ = _fake_join([130.8, 23.0])
    rl, n = _relinker()
    out = run(jw.watched_join(object(), "youtubetv", 90, relink=rl, join_fn=jf, log_path=""))
    assert out.state == "live" and out.behind_s == 0.0 and out.fallback == "live" and len(n) == 2


def test_failed_relink_is_unknown_not_live():
    jf, _ = _fake_join([None])
    rl, _ = _relinker(ok=False)
    out = run(jw.watched_join(object(), "youtubetv", 90, relink=rl, join_fn=jf, log_path=""))
    assert out.state == "unknown" and out.behind_s is None and out.fallback == "live_failed"


def test_prime_short_tops_up_from_where_it_is_without_a_relink():
    jf, calls = _fake_join([50.0, 60.5])
    rl, n = _relinker()
    out = run(jw.watched_join(object(), "prime:x", 60, relink=rl, join_fn=jf, log_path=""))
    assert out.state == "buffered" and calls == [0.0, 50.0] and not n


def test_keep_fallback_and_a_visible_stick_never_relink():
    jf, _ = _fake_join([None])
    rl, n = _relinker()
    out = run(jw.watched_join(object(), "youtubetv", 60, relink=rl, join_fn=jf, log_path="",
                              cfg={"join.watchdog.fallback": "keep"}))
    assert out.state == "unknown" and out.fallback == "keep" and not n
    out = run(jw.watched_join(object(), "youtubetv", 60, relink=rl, join_fn=jf, log_path="",
                              still_hidden=lambda: False))
    assert out.state == "unknown" and not n


def test_a_crashing_join_never_claims_a_buffer():
    async def boom(*a, **k):
        raise RuntimeError("adb drop")
    rl, _ = _relinker()
    out = run(jw.watched_join(object(), "youtubetv", 60, relink=rl, join_fn=boom, log_path=""))
    assert out.state == "unknown" and not out.buffered


def test_watched_join_drives_the_real_join_api(tmp_path):
    """End to end on the scrub fake: the real join() + the label reader, verified at 64.9 (57.4 + the read)."""
    from tests.test_yttv_seek import CFG, YT, FakeYTTV, reader
    s = FakeYTTV(label0=3600.0)

    async def label(_s):
        return 64.9
    rl, _ = _relinker()
    out = run(jw.watched_join(s, "youtubetv", 60, relink=rl, cfg=CFG, pkg=YT, read_overlay=reader(s),
                              read_behind=label, log_path=tmp_path / "j.jsonl"))
    assert out.state == "buffered" and out.behind_s == 64.9


# ------------------------------------------------------------------------------------------- drift monitor
class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_never_reads_the_visible_stick_and_waits_first_s():
    c = Clock()
    dm = jw.DriftMonitor(clock=c, log_path="")
    dm.anchor("b|espn", "youtubetv", 90, 82.0)
    c.t = 299
    assert not dm.due("b|espn", hidden=True)
    c.t = 300
    assert not dm.due("b|espn", hidden=False) and dm.due("b|espn", hidden=True)


def test_the_31_minute_espn_soak_reenters_once_the_reads_push_it_past_the_band():
    """Nick's Nov 7 soak: 82, 89, ... 119 s at 5-min reads: no snap, but every read added 7.5 s. With backoff the
    reads come at 5, 15, 35 min; the third would be 104.5 > 90 + 10.5 -> re-enter (the soak itself never would)."""
    c = Clock()
    dm = jw.DriftMonitor(clock=c, log_path="")
    dm.anchor("k", "youtubetv", 90, 82.0)
    seen, b = [], 82.0
    for _ in range(3):
        while not dm.due("k", hidden=True):
            c.t += 10
        b += 7.5
        seen.append((round(c.t / 60), dm.record("k", b).action))
    assert seen == [(5, "ok"), (15, "ok"), (35, "reenter")]
    assert dm.report()["reads"] == 3 and dm.report()["read_cost_s"] == 22.5 and dm.believed("k") is None


def test_plain_pause_snap_to_live_is_snapped():
    c = Clock()
    dm = jw.DriftMonitor(clock=c, log_path="")
    dm.anchor("k", "youtubetv", 60, 60.0)
    c.t = 400
    assert dm.record("k", 1.2).action == "snapped"


def test_program_boundary_label_reset_is_unknown_not_buffered():
    """Nov 7 ~1:00 AM: the label resets at a new program, so a pair read can't be made: no reading."""
    c = Clock()
    dm = jw.DriftMonitor(clock=c, log_path="")
    dm.anchor("k", "youtubetv", 60, 60.0)
    c.t = 400
    assert dm.record("k", None).action == "unknown" and dm.believed("k") is None


def test_a_drift_inside_the_band_reanchors_and_checks_sooner():
    c = Clock()
    dm = jw.DriftMonitor(clock=c, log_path="")
    dm.anchor("k", "youtubetv", 90, 82.0)
    c.t = 300
    v = dm.record("k", 98.0)                     # expected 89.5, read 98 -> drift +8.5, still within 90 +- 10.5
    assert v.action == "ok" and v.drift_s == 8.5 and dm.believed("k") == 98.0
    assert dm.tracks["k"].next_at == 600         # first_s again, not doubled


def test_the_read_budget_caps_reads_per_hour():
    c = Clock()
    dm = jw.DriftMonitor({"join.drift.budget_s_per_h": 15.0, "join.drift.first_s": 60.0,
                          "join.drift.backoff": 1.0}, clock=c, log_path="")
    dm.anchor("k", "youtubetv", 90, 88.0)
    reads = 0
    b = 88.0
    for _ in range(60):                          # an hour, a minute at a time
        c.t += 60
        if dm.due("k", hidden=True):
            reads += 1
            b = 88.0                             # (re-entered each time: it stays near target)
            dm.record("k", b + 7.5 if reads % 2 else b)
            if dm.believed("k") is None:
                dm.anchor("k", "youtubetv", 90, 88.0)
    assert reads == 2 and dm.totals["no_budget"] > 0


def test_shipped_flags_are_off(cfg=None):
    from redzone import config
    assert config.default("join.watchdog.enabled", True) is False
    assert config.default("join.drift.enabled", True) is False


# ---------------------------------------------------------------------------------- control wiring (scrub)
def test_control_watchdog_off_keeps_todays_behavior(scrub):
    c, tuner, hid, calls, result, behind = scrub
    result["r"] = Res(behind_s=75.0)             # 15 s past B 60: today accepted (within max_s + over)
    run(_preload(c, "youtubetv:fox"))
    assert c.hidden_ready and c.held["youtubetv:fox"] == 75.0 and len(calls) == 1


def test_control_watchdog_reseeks_an_off_target_landing_then_accepts(scrub):
    c, tuner, hid, calls, result, behind = scrub
    c.cfg.update({"join": {"watchdog": {"enabled": True}}})
    seq = iter([Res(behind_s=75.0), Res(behind_s=62.0)])
    result["r"] = lambda stick: next(seq)
    run(_preload(c, "youtubetv:fox"))
    assert len(calls) == 2 and c.hidden_ready and c.held["youtubetv:fox"] == 62.0
    # the re-seek re-tuned THROUGH another channel first (#391): espn then fox
    assert tuner.calls[-2:] == [("b:5555", "youtubetv:espn"), ("b:5555", "youtubetv:fox")]


def test_control_watchdog_off_twice_never_shows_buffered(scrub):
    c, tuner, hid, calls, result, behind = scrub
    c.cfg.update({"join": {"watchdog": {"enabled": True}}})
    result["r"] = Res(behind_s=23.0)
    run(_preload(c, "youtubetv:fox"))
    assert len(calls) == 2 and "youtubetv:fox" not in c.held and not c.hidden_ready
    assert hid.scrubbed() is None


def test_control_watchdog_records_on_the_fallback_ladder_when_present(scrub):
    c, tuner, hid, calls, result, behind = scrub
    c.cfg.update({"join": {"watchdog": {"enabled": True}}})
    rows = []
    c.ladder = NS(enabled=False, record=lambda *a: rows.append(a))   # #397: control always has a ladder
    run(_preload(c, "youtubetv:fox"))           # Res() default 67.0 for B 60: verified
    assert rows and rows[0][2:4] == ("buffered", True) and rows[0][5] == 67.0 and rows[0][6] is True


def test_control_drift_tick_rereads_the_hidden_stick_and_reenters_on_drift(scrub, monkeypatch, tmp_path):
    c, tuner, hid, calls, result, behind = scrub
    c.cfg.update({"join": {"drift": {"enabled": True, "first_s": 0}}})
    c.buffer_log_path = tmp_path / "buffer.jsonl"
    reads = iter([69.0, 140.0])

    class Probe:
        def arm(self, stick, behind_before_s=0.0):
            return True

        async def overlay_reader(self, stick):
            return None

        async def __call__(self, stick):
            return 67.0

        async def probe(self, stick, still_hidden=None):
            assert still_hidden()
            return NS(behind_live_s=next(reads))
    c.label_probe = Probe()
    run(_preload(c, "youtubetv:fox"))
    assert c.held["youtubetv:fox"] == 67.0
    run(c._drift_tick())                         # 69.0: expected 74.5 (67 + read 7.5), drift -5.5, held
    assert c.held["youtubetv:fox"] == 69.0 and c.hidden_ready

    async def drifted():
        c.drift.tracks[c._drift_key(hid, "youtubetv:fox")].next_at = 0
        await c._drift_tick()                    # 140: off target -> not buffered, re-entered off screen
        assert "youtubetv:fox" not in c.held or c.held["youtubetv:fox"] == 0
        if c._preload_task is not None:
            await c._preload_task
    run(drifted())
    assert len(calls) == 2 and c.held["youtubetv:fox"] == 67.0      # re-entered (fresh tune + scrub)
    rows = [json.loads(x) for x in (tmp_path / "joins.jsonl").read_text().splitlines()]
    assert [r["action"] for r in rows] == ["ok", "reenter"]
    assert all("5555" not in json.dumps(r) for r in rows)          # no address in the log


# ------------------------------------------------------------------- Nick's stick test, Nov 7 11:35Z (FAIL)
def test_nicks_nbatv_run_counts_the_reads_own_seven_and_a_half_seconds():
    """Hidden stick, NBA TV, target 75: planned 71.7, read 79.52 (off 7.82). The label probe's reading includes its own ~7.5 s
    freeze, so that is a good join, not a re-seek."""
    old = jw.verify("youtubetv", 75, 79.52, 71.7)
    assert old.status == "off_target"                                   # what the stick test hit
    p = jn.plan_join("youtubetv", 75)
    assert p.read_s == 7.5 and p.predicted_s == pytest.approx(79.2)     # the read now lives in the plan
    v = jw.verify("youtubetv", 75, 79.52, p.predicted_s, read_adds_s=p.read_s)
    assert v.buffered and v.off_pred_s == 0.32 and v.off_target_s == -2.98 and v.measured_s == 79.52


@pytest.mark.parametrize("measured", [62.63, 63.15, 63.18])
def test_nicks_three_canary_joins_verify_with_the_read_in_the_plan(measured):
    """Canary, espn, target 60 (Nov 7 11:40Z): 62.63 / 63.15 / 63.18 vs 56.7 without the read (+5.9..+6.5)."""
    p = jn.plan_join("youtubetv", 60)
    assert p.predicted_s == pytest.approx(64.2)
    v = jw.verify("youtubetv", 60, measured, p.predicted_s, read_adds_s=p.read_s)
    assert v.buffered and abs(v.off_pred_s) < 2


def test_watched_join_takes_the_read_cost_from_the_plan_once():
    jf, _ = _fake_join([79.52])
    rl, n = _relinker()
    out = run(jw.watched_join(object(), "youtubetv", 75, relink=rl, join_fn=jf, log_path=""))
    assert out.state == "buffered" and out.behind_s == 79.52 and not n

    async def calibrated(stick, app, target_s, *, behind_before_s=0.0, **kw):
        plan = jn.plan_join(app, target_s, behind_before_s)
        plan.predicted_s, plan.bias_source = 79.0, "channel"           # a calibrated lead (+ the read, in the plan)
        return NS(ok=True, measured_s=79.52, plan=plan)
    out = run(jw.watched_join(object(), "youtubetv", 75, relink=rl, join_fn=calibrated, log_path=""))
    assert out.state == "buffered" and not n                             # not counted twice


def test_prime_reads_add_nothing():
    p = jn.plan_join("prime:x", 60.0)
    assert p.read_s == 0.0
    v = jw.verify("prime:x", 60.0, 60.5, p.predicted_s, read_adds_s=p.read_s)
    assert v.buffered and v.off_pred_s == 0.5


def test_joins_log_follows_rz_joins_log(monkeypatch, tmp_path):
    """The run from a scratch worktree logged into that worktree's data/: RZ_JOINS_LOG points it anywhere."""
    monkeypatch.setenv("RZ_JOINS_LOG", str(tmp_path / "j.jsonl"))
    jw.log_row({"kind": "watchdog", "state": "live"})
    assert json.loads((tmp_path / "j.jsonl").read_text())["state"] == "live"


# --------------------------------------------------- Nick's canary run 1, Nov 7 11:50Z: a LOST PRESS (52.12 vs 64.2)
def test_a_lost_press_is_not_ok_in_the_join_row(tmp_path):
    """3 presses sent, one lost: landed a 15 s step short and the row said ok: true. Now ok is the READ verdict."""
    from tests.test_yttv_seek import CFG, YT, FakeYTTV, reader
    s = FakeYTTV(label0=3600.0)

    async def label(_s):
        return 52.12
    log = tmp_path / "joins.jsonl"
    r = run(jn.join(s, "youtubetv", 60, cfg=CFG, pkg=YT, read_overlay=reader(s), read_behind=label, log_path=log))
    assert not r.ok and r.verdict == "off_target" and "lost press" in r.reason and r.measured_s == 52.12
    row = json.loads(log.read_text().splitlines()[-1])
    assert row["ok"] is False and row["verdict"] == "off_target" and row["pred_error_s"] == pytest.approx(-12.08)


def test_a_good_join_row_is_still_ok_and_verified(tmp_path):
    from tests.test_yttv_seek import CFG, YT, FakeYTTV, reader
    s = FakeYTTV(label0=3600.0)

    async def label(_s):
        return 63.21
    r = run(jn.join(s, "youtubetv", 60, cfg=CFG, pkg=YT, read_overlay=reader(s), read_behind=label,
                    log_path=tmp_path / "j"))
    assert r.ok and r.verdict == "verified"


def test_the_watchdog_reseeks_a_lost_press_then_accepts():
    """The join reports ok=False (verdict off_target) for 52.12; the watchdog still treats it as READ and re-seeks
    from the live edge once, instead of falling straight back to live."""
    seq = iter([52.12, 63.21])
    calls = []

    async def join_fn(stick, app, target_s, *, behind_before_s=0.0, **kw):
        calls.append(behind_before_s)
        m = next(seq)
        plan = jn.plan_join(app, target_s, behind_before_s)
        v = jw.verify(app, target_s, m, plan.predicted_s, read_adds_s=plan.read_s)
        return NS(ok=v.buffered, measured_s=m, plan=plan, verdict=v.status)
    rl, n = _relinker()
    out = run(jw.watched_join(object(), "youtubetv", 60, relink=rl, join_fn=join_fn, log_path=""))
    assert out.state == "buffered" and out.behind_s == 63.21 and out.attempts == 2 and len(n) == 1
    assert [v["status"] for v in out.verdicts] == ["off_target", "verified"]


def test_calibration_keeps_an_off_target_read_as_a_measurement_not_a_failure():
    from redzone.tuners import join_calib as jc
    p = jn.plan_join("youtubetv", 60)
    row = {"app": "youtubetv", "method": "scrub", "ok": False, "verdict": "off_target", "measured_s": 52.12,
           "predicted_s": p.predicted_s, "pred_error_s": -12.08, "bias_used_s": 11.7, "read_s": 7.5,
           "channel": "espn", "stick": "stickB", "target_s": 60, "error_s": -7.88}
    st = jc.JoinCalib([row]).stats("youtubetv")
    assert st.failed == 0 and st.gross == 1          # counted as gross (|err| > 12), never used, not a failed join


# ------------------------------------- Nick's cross-channel run, Nov 7 13:02Z: ESPN2 run 2, the post-commit read was empty
def _unread_then(*reads):
    """A committed join whose own label read returned nothing; later reads of the same probe return `reads`."""
    seq = iter(reads)
    n = []

    async def reader(_stick):
        n.append(1)
        return next(seq)

    async def join_fn(stick, app, target_s, *, behind_before_s=0.0, read_behind=None, **kw):
        plan = jn.plan_join(app, target_s, behind_before_s)
        return NS(ok=True, measured_s=None, plan=plan, verdict="")
    return join_fn, reader, n


def test_reprobe_is_off_by_default_and_falls_back_as_before():
    jf, reader, n = _unread_then(71.0)
    rl, relinks = _relinker()
    out = run(jw.watched_join(object(), "youtubetv", 60, relink=rl, join_fn=jf, read_behind=reader, log_path=""))
    assert out.state == "live" and out.reprobes == 0 and not n and len(relinks) == 1


def test_reprobe_saves_the_join_when_the_second_read_lands():
    """Plan 64.2 (one read); the re-probe adds another ~7.5 s: 63.0 + 7.5 = 70.5 vs 71.7 -> verified."""
    jf, reader, n = _unread_then(70.5)
    rl, relinks = _relinker()
    out = run(jw.watched_join(object(), "youtubetv", 60, relink=rl, join_fn=jf, read_behind=reader, log_path="",
                              cfg={"join.watchdog.reprobe_unmeasured": True}))
    assert out.state == "buffered" and out.behind_s == 70.5 and out.reprobes == 1 and len(n) == 1 and not relinks
    assert [v["status"] for v in out.verdicts] == ["unmeasured", "verified"]
    assert out.verdicts[-1]["predicted_s"] == pytest.approx(71.7) and out.verdicts[-1]["reason"].startswith("re-probe")


def test_reprobe_reads_only_once_then_falls_back():
    jf, reader, n = _unread_then(None, 70.0)
    rl, relinks = _relinker()
    out = run(jw.watched_join(object(), "youtubetv", 60, relink=rl, join_fn=jf, read_behind=reader, log_path="",
                              cfg={"join.watchdog.reprobe_unmeasured": True}))
    assert out.state == "live" and out.reprobes == 1 and len(n) == 1 and len(relinks) == 1


def test_reprobe_off_target_still_reseeks():
    jf, reader, n = _unread_then(90.0)
    rl, relinks = _relinker()
    out = run(jw.watched_join(object(), "youtubetv", 60, relink=rl, join_fn=jf, read_behind=reader, log_path="",
                              cfg={"join.watchdog.reprobe_unmeasured": True}))
    assert out.verdicts[1]["status"] == "off_target" and out.verdicts[1]["action"] == "reseek" and len(relinks) >= 1


def test_no_reprobe_on_a_failed_join_or_a_visible_stick():
    async def failed(stick, app, target_s, *, behind_before_s=0.0, read_behind=None, **kw):
        return NS(ok=False, measured_s=None, plan=jn.plan_join(app, target_s), verdict="")
    _, reader, n = _unread_then(70.5)
    rl, _ = _relinker()
    cfg = {"join.watchdog.reprobe_unmeasured": True}
    run(jw.watched_join(object(), "youtubetv", 60, relink=rl, join_fn=failed, read_behind=reader, log_path="",
                        cfg=cfg))
    jf, reader2, n2 = _unread_then(70.5)
    run(jw.watched_join(object(), "youtubetv", 60, relink=rl, join_fn=jf, read_behind=reader2, log_path="", cfg=cfg,
                        still_hidden=lambda: False))
    assert not n and not n2
