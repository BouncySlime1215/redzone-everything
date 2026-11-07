"""Mac health alerts (redzone/platform/mac_health.py) and the night ops section (redzone/platform/night_ops.py).

The cases are the real Nov 4-6 failures from the Mac's own logs (numbers only):
  battery 19% at 11:00 PM ET Nov 4 and 13% at 12:44 AM, swap 10.0 GB at 8:13 PM Nov 5, and no switch row
  7:20 -> 8:26 PM ET Nov 6 with 10 live games while command locks stuck at 151-158 s.
"""
import asyncio
import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

from redzone import supervisor as sup
from redzone.platform import night_ops
from redzone.platform.mac_health import MacHealth, Snapshot, options

ET = ZoneInfo("America/New_York")
T = datetime(2026, 11, 6, 19, 20, tzinfo=ET).timestamp()


def run(mh, snaps, step=10.0, t0=T):
    """Feed (seconds_from_t0, Snapshot) pairs; returns [(t, Alert)]."""
    out = []
    for dt, s in snaps:
        for a in mh.check(s, t0 + dt):
            out.append((dt, a))
    return out


def every(seconds, step, **kw):
    return [(t, Snapshot(**kw)) for t in range(0, seconds + 1, step)]


def test_off_by_default():
    assert options()["enabled"] is False
    assert options({"enabled": "yes", "swap_alert_mb": -1})["swap_alert_mb"] == 8000   # bad values keep defaults


def test_nov4_battery_19_then_13_percent_alerts_once_each_level():
    mh = MacHealth(options({"enabled": True}))
    kw = {"live_games": 3, "on_battery": True, "last_switch_ts": T}
    got = run(mh, every(60, 10, battery_pct=19, **kw))
    assert [(a.code, a.level) for _, a in got] == [("battery_low", "high")]
    assert "19%" in got[0][1].text and "plug it in" in got[0][1].text
    assert run(mh, [(70, Snapshot(battery_pct=13, **kw))]) == []           # same level: no repeat yet
    worse = run(mh, [(80, Snapshot(battery_pct=9, **kw))])                  # worse: at once
    assert [(a.code, a.level) for _, a in worse] == [("battery_low", "urgent")]
    assert run(mh, [(90, Snapshot(battery_pct=9, **kw))]) == []
    again = run(mh, [(1900, Snapshot(battery_pct=9, last_switch_ts=T + 1800, live_games=3, on_battery=True))])
    assert [(a.code, a.level) for _, a in again] == [("battery_low", "urgent")]


def test_on_battery_with_games_needs_two_minutes_then_clears_on_power():
    mh = MacHealth(options({"enabled": True}))
    got = run(mh, every(110, 10, live_games=2, on_battery=True, battery_pct=63))
    assert got == []
    got = run(mh, [(120, Snapshot(live_games=2, on_battery=True, battery_pct=63))])
    assert [(a.code, a.level) for _, a in got] == [("on_battery", "warn")]
    got = run(mh, [(130, Snapshot(live_games=2, on_battery=False, battery_pct=63))])
    assert [(a.code, a.cleared) for _, a in got] == [("on_battery", True)]
    # no games: on battery is not an alert (the Mac goes to school)
    mh2 = MacHealth(options({"enabled": True}))
    assert run(mh2, every(600, 10, live_games=0, on_battery=True, battery_pct=63)) == []


def test_nov5_swap_10gb_with_games_after_five_minutes():
    mh = MacHealth(options({"enabled": True, "no_switch_min_live": 99}))
    assert run(mh, every(290, 10, live_games=4, swap_used_mb=10000.0, mem_pressure=2)) == []
    got = run(mh, [(300, Snapshot(live_games=4, swap_used_mb=10000.0, mem_pressure=2))])
    assert [(a.code, a.level) for _, a in got] == [("memory", "warn")]
    assert "10.0 GB swapped out" in got[0][1].text
    # the 8 GB Mac's normal evening (3-6 GB swapped) never alerts
    mh2 = MacHealth(options({"enabled": True, "no_switch_min_live": 99}))
    assert run(mh2, every(1200, 10, live_games=4, swap_used_mb=5800.0, mem_pressure=2)) == []


def test_critical_pressure_is_high_with_games():
    mh = MacHealth(options({"enabled": True}))
    got = run(mh, every(60, 10, live_games=1, mem_pressure=4, swap_used_mb=3000.0))
    assert [(a.code, a.level) for _, a in got] == [("memory", "high")]


def test_nov6_no_switch_for_66_minutes_with_10_live_games():
    mh = MacHealth(options({"enabled": True}))
    last = T   # 7:20 PM ET: the last switch row before the gap
    snaps = [(t, Snapshot(live_games=10, last_switch_ts=last)) for t in range(0, 66 * 60, 10)]
    got = run(mh, snaps)
    codes = [(t, a.code, a.level) for t, a in got]
    assert codes[0] == (1200, "no_switches", "high")          # told at 7:40 PM, not the next morning
    assert [t for t, *_ in codes] == [1200, 3000]               # and again 30 min later while still stuck
    assert "7:20 PM" in got[0][1].text and "10 live games" in got[0][1].text
    # 8:26 PM: a switch lands -> cleared
    done = run(mh, [(66 * 60, Snapshot(live_games=10, last_switch_ts=T + 66 * 60))])
    assert [(a.code, a.cleared) for _, a in done] == [("no_switches", True)]


def test_no_switch_alert_respects_pause_lock_and_one_game():
    for kw in ({"paused": True}, {"held": True}):
        mh = MacHealth(options({"enabled": True, "paused_alert_s": 99999}))
        assert run(mh, every(3600, 30, live_games=6, last_switch_ts=T - 600, **kw)) == []
    mh = MacHealth(options({"enabled": True}))
    assert run(mh, every(3600, 30, live_games=1, last_switch_ts=T - 600)) == []


def test_no_switch_clock_starts_when_games_go_live_not_at_the_last_old_switch():
    mh = MacHealth(options({"enabled": True}))
    old = T - 6 * 3600        # last switch was this afternoon
    assert run(mh, every(1190, 10, live_games=3, last_switch_ts=old)) == []
    got = run(mh, [(1200, Snapshot(live_games=3, last_switch_ts=old))])
    assert [a.code for _, a in got] == ["no_switches"]


def test_paused_ten_minutes_with_games():
    mh = MacHealth(options({"enabled": True}))
    got = run(mh, every(600, 10, live_games=3, paused=True, last_switch_ts=T))
    assert [(t, a.code) for t, a in got] == [(600, "paused")]


def test_sleep_is_an_event_high_only_with_games():
    mh = MacHealth(options({"enabled": True}))
    got = run(mh, [(0, Snapshot(live_games=2, slept_s=720.0, last_switch_ts=T))])
    assert [(a.code, a.level) for _, a in got] == [("slept", "high")] and "12 min" in got[0][1].text
    assert run(mh, [(10, Snapshot(live_games=2, last_switch_ts=T))]) == []      # nothing to clear
    got = run(mh, [(20, Snapshot(live_games=0, slept_s=3600.0))])
    assert [(a.code, a.level) for _, a in got] == [("slept", "info")]


def test_keep_awake_missing_with_games():
    mh = MacHealth(options({"enabled": True}))
    got = run(mh, every(120, 10, live_games=2, keep_awake=False, last_switch_ts=T))
    assert [(t, a.code) for t, a in got] == [(120, "keep_awake")]
    mh2 = MacHealth(options({"enabled": True}))
    assert run(mh2, every(600, 10, live_games=2, keep_awake=None, last_switch_ts=T)) == []   # unknown: quiet


# --- through the supervisor -------------------------------------------------------------------------

def _supervisor(tmp_path, enabled=True, games=3):
    from types import SimpleNamespace
    from conftest import FakeClock
    clock = FakeClock(T)
    toasts, pushes = [], []

    class Cfg:
        def __init__(self, d):
            self.d = d

        def get(self, k, default=None):
            return self.d.get(k, default)

    from redzone.models import Phase
    live = [SimpleNamespace(game_id=str(i), phase=Phase.LIVE, league="nba") for i in range(games)]
    cfg = Cfg({"supervisor.mac_health": {"enabled": enabled}, "supervisor.ntfy_topic": ""})
    gov = SimpleNamespace(last=SimpleNamespace(on_battery=True, battery_pct=19, mem_pressure=2, swap_used_mb=3000.0))

    class Store:
        def recent_switches(self, n=50):
            return [{"ts": T}]
    e = SimpleNamespace(cfg=cfg, merger=SimpleNamespace(games={g.game_id: g for g in live}), governor=gov,
                        decider=SimpleNamespace(user=SimpleNamespace(paused=False, locked_game=None)), hold=None,
                        store=Store(), toast=lambda text, level="info": toasts.append((text, level)),
                        _leagues=lambda: ["nba"])
    s = sup.Supervisor.__new__(sup.Supervisor)
    s.e, s.root, s.clock = e, tmp_path, clock
    s.log_path = tmp_path / "supervisor.jsonl"
    s.mac_health, s._wake = None, None

    async def send_push(title, body, priority="default", tags="tv"):
        pushes.append((title, priority))
        return True
    s.send_push = send_push
    return s, clock, toasts, pushes


def test_supervisor_rule_is_silent_when_off(tmp_path):
    s, clock, toasts, pushes = _supervisor(tmp_path, enabled=False)
    asyncio.run(s.rule_mac_health(clock()))
    assert toasts == [] and pushes == [] and not (tmp_path / "supervisor.jsonl").exists()


def test_supervisor_rule_toasts_pushes_and_logs(tmp_path):
    s, clock, toasts, pushes = _supervisor(tmp_path)
    asyncio.run(s.rule_mac_health(clock()))
    assert toasts and toasts[0][1] == "error" and "19%" in toasts[0][0]
    assert pushes == [("RedZone Mac: battery low", "high")]
    rows = sup.read_jsonl(tmp_path / "supervisor.jsonl")
    assert rows[0]["kind"] == "mac_health" and rows[0]["code"] == "battery_low"
    # the Mac slept 15 min (the tick came 900 s late) -> a sleep alert, pushed
    clock.t += 900
    asyncio.run(s.rule_mac_health(clock()))
    assert any(p[0] == "RedZone Mac: slept" for p in pushes)


# --- night ops ---------------------------------------------------------------------------------------

def _write(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_night_ops_summarizes_joins_fallbacks_drift_errors_and_mac(tmp_path):
    t = T + 60
    _write(tmp_path / "joins.jsonl", [
        {"ts": t, "app": "youtubetv", "method": "scrub", "target_s": 60, "predicted_s": 56.7, "ok": True,
         "measured_s": 57.4, "error_s": -2.6, "pred_error_s": 0.7, "reason": "", "took_s": 15.2},
        {"ts": t + 1, "app": "youtubetv", "method": "scrub", "target_s": 90, "predicted_s": 86.7, "ok": True,
         "measured_s": 108.7, "error_s": 18.7, "pred_error_s": 22.0, "reason": "", "took_s": 16.0},
        {"ts": t + 2, "app": "youtubetv", "method": "scrub", "target_s": 60, "ok": False, "measured_s": None,
         "error_s": None, "reason": "overlay label not read", "took_s": 9.0},
        {"ts": t + 3, "app": "prime", "method": "pause", "target_s": 60, "predicted_s": 60, "ok": True,
         "measured_s": 61.0, "error_s": 1.0, "pred_error_s": 1.0, "took_s": 61.5},
        {"ts": t + 4, "kind": "fallback", "stick": "hidden", "step": "live", "why": "off target 18.7s"},
        {"ts": t + 5, "kind": "drift", "stick": "a", "drift_s": 4.0, "read_cost_s": 7.5, "action": "ok"},
    ])
    _write(tmp_path / "drift.jsonl", [{"ts": t + 6, "drift_s": -12.0, "read_cost_s": 7.5, "action": "reenter"}])
    _write(tmp_path / "supervisor.jsonl", [
        {"ts": t, "kind": "invariant", "code": "stuck_lock"}, {"ts": t + 9, "kind": "invariant", "code": "stuck_lock"},
        {"ts": t, "kind": "fix", "rule": "adb", "ok": False},
        {"ts": t + 1200, "kind": "mac_health", "code": "no_switches", "level": "high",
         "text": "No switch in 20 min with 10 live games (last at 7:20 PM): switching may be stuck", "cleared": False},
        {"ts": t + 4000, "kind": "mac_health", "code": "no_switches", "cleared": True, "text": "Cleared"},
    ])
    s = night_ops.summarize(tmp_path, T, T + 86400)
    y = s["joins"]["youtubetv"]
    assert (y["joins"], y["ok"], y["measured"], y["within_margin"]) == (3, 2, 2, 1)
    assert y["abs_err_max"] == 18.7 and y["failures"] == {"overlay label not read": 1}
    assert s["joins"]["prime"]["within_margin"] == 1
    assert s["fallbacks"]["steps"] == {"live": 1}
    assert s["drift"] == {"reads": 2, "read_cost_s": 15.0, "abs_drift_p50": 8.0, "abs_drift_max": 12.0,
                          "reentries": 1, "to_live": 0}
    assert s["errors"] == {"faults": {"stuck_lock": 2}, "fixes_failed": {"adb": 1}}
    assert s["mac"] == {"no_switches": 1}
    text = "\n".join(night_ops.lines(s))
    assert "## Sync and health" in text and "| youtubetv | 3 | 2 | 2 | 1/2 |" in text
    assert night_ops.phone_line(s).startswith("Sync: Joins 4 (2/3 within 10s)")


def test_night_ops_with_nothing_logged_yet(tmp_path):
    s = night_ops.summarize(tmp_path, T, T + 86400)
    text = "\n".join(night_ops.lines(s))
    assert "Joins: none logged" in text and "Fallbacks: none" in text and "Mac: no alerts" in text
    assert night_ops.phone_line(s) == "Sync: no joins, fallbacks or Mac alerts"


def test_nightly_report_adds_ops_only_when_on(tmp_path):
    _write(tmp_path / "supervisor.jsonl", [
        {"ts": T + 100, "kind": "mac_health", "code": "battery_low", "level": "high",
         "text": "Mac battery at 19% and not charging: plug it in", "cleared": False}])
    night = date(2026, 11, 6)
    off = sup.night_report(night, None, tmp_path)
    assert "Sync and health" not in off["text"] and "ops" not in off["metrics"]
    on = sup.night_report(night, None, tmp_path, ops_cfg={"enabled": True})
    assert "## Sync and health" in on["text"] and on["metrics"]["ops"]["mac"] == {"battery_low": 1}
    assert "Mac battery low: Mac battery at 19%" in on["text"]          # under "Needs you"
    assert "Sync: Mac: battery low 1" in on["phone"]


def test_report_text_has_no_addresses(tmp_path):
    _write(tmp_path / "joins.jsonl", [{"ts": T + 1, "app": "youtubetv", "ok": False, "reason": "adb drop",
                                       "stick": "hidden"}])
    text = "\n".join(night_ops.lines(night_ops.summarize(tmp_path, T, T + 86400)))
    import re
    assert not re.search(r"\b\d{1,3}(\.\d{1,3}){3}\b", text)
