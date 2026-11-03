"""Watchdog: hard rules, the guard on every verdict, and reading the app's log."""
import time

from agent import watchdog
from agent.policy import Obs, Verdict, guard, parse_answer, rules


def test_down_or_hung_app_is_restarted():
    assert rules(Obs(api_ok=False, api_fail_streak=2)).action == "restart_service"
    assert rules(Obs(api_ok=False, api_fail_streak=1)) is not None   # one miss: no restart yet
    assert rules(Obs(api_ok=False, api_fail_streak=1)).action != "restart_service"
    assert rules(Obs(live_games=2, log_age_s=600)).action == "restart_service"
    assert rules(Obs(live_games=0, log_age_s=6000)).action == "none"   # quiet night, quiet log


def test_known_noise_is_left_alone_and_unknown_errors_go_to_the_model():
    assert rules(Obs(errors={"ERROR redzone.engine: score bug read failed": 90})).action == "none"
    assert rules(Obs(errors={"ERROR redzone.engine: something new: KeyError: 'x'": 1})) is None


def test_offline_stick_reconnects_then_asks_for_power():
    assert rules(Obs(sticks_offline=["h:5555"], offline_min=2)).action == "reconnect_stick"
    v = rules(Obs(sticks_offline=["h:5555"], offline_min=30))
    assert v.action == "notify" and "power" in v.why


def test_guard_never_restarts_into_crunch_time_or_in_a_loop():
    o = Obs(crunch_on_screen=True)
    assert guard(Verdict("restart_service", "x"), o).action == "notify"
    assert guard(Verdict("restart_service", "x"), Obs(api_ok=False, crunch_on_screen=True)).action == "restart_service"
    assert guard(Verdict("restart_service", "x"), Obs(restarts_last_hour=3)).action == "notify"
    assert guard(Verdict("reconnect_stick", "x", "nope"), Obs()).action == "none"
    assert guard(Verdict("format_disk", "x"), Obs()).action == "notify"


def test_model_answers_are_parsed_defensively():
    assert parse_answer('<think>hmm</think>```json\n{"action": "notify", "why": "x"}\n```').action == "notify"
    assert parse_answer('{"action": "rm -rf", "why": "x"}') is None
    assert parse_answer("no idea") is None


def test_log_window_counts_errors_not_traceback_bodies(tmp_path):
    now = time.time()
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - 30))
    old = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - 5000))
    p = tmp_path / "service.log"
    p.write_text(f"{stamp},1 ERROR redzone.engine: score bug read failed\n"
                 f"{stamp},2 ERROR redzone.engine: score bug read failed\n"
                 f"{stamp},3 ERROR redzone.service:   File \"x.py\", line 3\n"
                 f"{old},4 ERROR redzone.engine: poll failed 12\n")
    assert watchdog.recent_errors(p, now=now) == {"ERROR redzone.engine: score bug read failed": 2}


def test_notes_are_not_repeated_within_the_hour(monkeypatch):
    w = watchdog.Watchdog(dry_run=True, use_model=False, clock=lambda: 1000.0)
    sent = []
    monkeypatch.setattr(w, "_push", sent.append)
    v = Verdict("notify", "stick 1 has been offline 12 min")
    assert w.act(v, Obs()) == "noted for the phone"
    assert w.act(Verdict("notify", "stick 1 has been offline 13 min"), Obs()) == "already told you this hour"
    assert len(sent) == 1


def test_dry_run_never_restarts():
    w = watchdog.Watchdog(dry_run=True, use_model=False)
    assert w.act(Verdict("restart_service", "x"), Obs()).startswith("dry run")
