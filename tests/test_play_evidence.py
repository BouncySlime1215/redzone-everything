"""Adversarial evidence tests: receipt order and media activity are not play timing."""
import pytest

from redzone.decider.screen import (
    Delay, DelayBook, Event, Timeline, final_play_aired, leave_gate, leave_window,
    resume_play, screen_evidence,
)
from redzone.models import League


@pytest.mark.parametrize("fresh", [None, 170.0])
def test_empty_timeline_does_not_prove_a_gap(fresh):
    tl = Timeline(League.NFL)
    tl.fresh_at = fresh
    gate = leave_gate(tl, Delay(35, 3), 170, 8)
    assert not gate.safe and "unknown" in gate.why
    evidence = screen_evidence(tl, Delay(35, 3), 170, 8)
    assert evidence["status"] == "unknown" and not evidence["safe_to_leave"]


def test_unobserved_break_is_not_transport_freshness():
    tl = Timeline(League.NFL)
    tl.events.append(Event(100, "stop_start", "halftime"))
    assert not leave_gate(tl, Delay(35, 3), 170, 8).safe


def test_late_old_play_cannot_shorten_last_play_before_break():
    tl = Timeline(League.NFL)
    tl.fresh_at = 170
    tl.events.extend([Event(100, "play"), Event(140, "play"),
                      Event(50, "play"), Event(150, "stop_start", "timeout")])
    # The play ending at 140 is airing at 175 +/- 6. The late event at 50
    # must not make this look like a break whose action ended long ago.
    gate = leave_gate(tl, Delay(35, 3), 170, 8)
    assert not gate.safe and gate.why == "a play is on screen"


def test_very_late_event_cannot_hide_newer_play_in_bounded_history():
    tl = Timeline(League.NFL)
    tl.fresh_at = 170
    tl.events.extend([Event(140, "play"), Event(-1000, "play")])
    assert not leave_gate(tl, Delay(35, 3), 170, 8).safe


def test_very_late_event_cannot_hide_break_resume():
    tl = Timeline(League.NFL)
    tl.fresh_at = 170
    tl.events.extend([Event(100, "stop_start", "timeout"),
                      Event(140, "play"), Event(-1000, "play")])
    assert not leave_gate(tl, Delay(35, 3), 170, 8).safe


def test_last_game_play_uses_event_time_not_arrival_order():
    tl = Timeline(League.NFL)
    tl.events.extend([Event(140, "play"), Event(50, "play")])
    assert not final_play_aired(tl, Delay(35, 3), 170)
    assert final_play_aired(tl, Delay(35, 3), 185)
    assert not final_play_aired(Timeline(League.NFL), Delay(35, 3), 185)


def test_first_resume_play_uses_event_time_not_arrival_order():
    tl = Timeline(League.NFL)
    early, late = Event(140, "play"), Event(160, "play")
    tl.events.extend([Event(100, "stop_start", "timeout"),
                      Event(130, "stop_end"), late, early])
    assert resume_play(tl) is early


def test_conflicting_delay_samples_remain_inside_protected_bounds():
    book = DelayBook({"youtubetv": 35}, 15, clock=lambda: 1000)
    for sample in (45, 45, 100):
        delay = book.calibrate("youtubetv:fox", sample)
    margin = 2 * delay.current_sigma(1000)
    assert delay.mu - margin <= 45 <= delay.mu + margin
    assert delay.mu - margin <= 100 <= delay.mu + margin
    assert delay.current_sigma(1000) > 15
    previous = delay.current_sigma(1000)
    book.buffered("youtubetv:fox")
    assert delay.current_sigma(1000) >= previous


def test_screen_evidence_labels_inference_and_exposes_bounds():
    delay = Delay(35, 3, calibrated_at=170)
    missing = screen_evidence(None, delay, 170, 8)
    assert missing["basis"] == "sports_feed_and_delay_model"
    assert missing["feed_age_s"] is None
    assert missing["delay_margin_s"] == 6 and missing["calibrated"]
    tl = Timeline(League.NFL)
    tl.fresh_at = 170
    tl.events.append(Event(140, "play"))
    active = screen_evidence(tl, delay, 170, 8)
    assert active["status"] == "play_possible" and not active["safe_to_leave"]
    tl.events.clear()
    tl.events.append(Event(100, "stop_start", "halftime"))
    stopped = screen_evidence(tl, delay, 170, 8)
    assert stopped["status"] == "break_estimated" and stopped["safe_to_leave"]


def test_exit_window_rejects_play_entirely_between_safe_endpoints():
    tl = Timeline(League.NFL)
    tl.fresh_at = 146
    tl.events.append(Event(60, "play"))
    delay = Delay(100, 1)
    assert leave_gate(tl, delay, 146, 8).safe
    assert leave_gate(tl, delay, 166, 8).safe
    # The whole play airs between those two safe instants.
    assert not leave_window(tl, delay, 146, 8, 20).safe


def test_exit_window_rejects_known_play_start_during_command_latency():
    tl = Timeline(League.NFL)
    tl.fresh_at = 140
    tl.events.append(Event(160, "play"))
    delay = Delay(0, 2)
    # Use a known official break ending at the next play's earliest restart.
    tl.events.append(Event(90, "stop_start", "halftime"))
    delay.mu = 40
    tl.fresh_at = 180
    assert leave_gate(tl, delay, 180, 8).safe
    assert not leave_window(tl, delay, 180, 8, 10).safe


def test_exit_window_does_not_assume_future_feed_refresh():
    tl = Timeline(League.NFL)
    tl.fresh_at = 100
    tl.events.append(Event(0, "play"))
    assert leave_gate(tl, Delay(40, 2), 100, 8).safe
    assert not leave_window(tl, Delay(40, 2), 100, 8, 25).safe


def test_exit_window_accepts_a_supported_long_break():
    tl = Timeline(League.NFL)
    tl.fresh_at = 170
    tl.events.append(Event(100, "stop_start", "halftime"))
    assert leave_window(tl, Delay(35, 3), 170, 8, 8).safe
    assert not leave_window(tl, Delay(35, 3), 170, 8, float("nan")).safe


def test_uncertain_break_cannot_hide_a_possible_resume_inside_its_bounds():
    tl = Timeline(League.NBA)
    tl.fresh_at = 205
    # The missing-wallclock timeout could have started at 60. The shot at
    # 90 could therefore already have resumed play, despite being <98.
    tl.events.extend([Event(90, "play", pid="shot"),
                      Event(98, "stop_start", "timeout", pid="timeout", spread=38)])
    assert not leave_gate(tl, Delay(100, 3), 205, 8).safe


def test_score_causing_its_own_stoppage_does_not_count_as_a_resume():
    tl = Timeline(League.NFL)
    tl.fresh_at = 150
    tl.events.extend([Event(98, "play", pid="td", spread=38),
                      Event(98, "stop_start", "score", pid="td", spread=38)])
    assert leave_gate(tl, Delay(50, 2), 160, 8).safe
