from datetime import date
from types import SimpleNamespace as NS

import pytest

from redzone.tuners.prime_search import CapturedScreen, detail_verified, empty_search, player_overlay, replay_card, run_prime_search


def frame(text, focus=None):
    boxes = (NS(text=text, x=.65, y=.2, width=.28, height=.03),)
    if "00:00" in text and "Watch" not in text and "Included" not in text:
        boxes = (NS(text=text, x=.05, y=.05, width=.5, height=.03),
                 NS(text="00:00 02:30:00", x=.05, y=.85, width=.5, height=.03))
    return NS(read_ok=True, status="ok", text=text, focus_status="unique" if focus else "none",
              focus_boxes=(NS(text=focus, x=.65, y=.36, width=.3, height=.083,
                              source="neutral_filled_button" if focus.startswith("Watch") or focus == "Resume" else "neutral_rectangle_border"),)
              if focus else (), observations=boxes)


class Stick:
    packages = {"prime": "com.amazon.firebat"}

    def __init__(self):
        self.keys = []

    async def ensure_awake(self, may_cut=None):
        if may_cut is not None:
            may_cut()
        await self.key("KEYCODE_WAKEUP")
        return True

    async def view(self, *args, **kwargs):
        return NS(ok=True)

    async def shell(self, *args, **kwargs):
        return NS(ok=True, out="mWakefulness=Awake" if "dumpsys power" in args[0] else "")

    async def key(self, key):
        self.keys.append(key)
        return NS(ok=True)

    async def playback(self, package):
        return {"state": "playing"}


async def no_sleep(_):
    pass


def test_replay_card_requires_both_teams_date_and_replay_not_upcoming():
    wanted = date(2026, 11, 1)
    assert replay_card("REPLAY Clippers vs. Warriors Sun, Nov 1", "Warriors", "Clippers", wanted)
    assert not replay_card("UPCOMING Clippers vs. Warriors Nov 1", "Warriors", "Clippers", wanted)
    assert not replay_card("REPLAY Clippers vs. Warriors Oct 30", "Warriors", "Clippers", wanted)
    assert not replay_card("REPLAY Clippers vs. Lakers Nov 1", "Warriors", "Clippers", wanted)
    assert not replay_card("REPLAY Clippers vs. Warriors Nov 1 2025", "Warriors", "Clippers", wanted)


async def trial(frames, *, play=False, stale_at=None, initial=None):
    stick = Stick()
    sequence = iter([*(initial if initial is not None else [frame("Clear local history Movie picks Rent or buy a favorite")]), *frames])
    now, counter, previous = 100., 0, None

    async def sleep(seconds):
        nonlocal now
        now += seconds

    async def observe():
        nonlocal now, counter, previous
        previous = next(sequence, previous)
        now += .1
        counter += 1
        return CapturedScreen(previous, counter - 1 if counter == stale_at else counter, now)

    result = await run_prime_search(stick, observe, query="Clippers", home="Warriors", away="Clippers",
                                    requested=date.today().replace(month=11, day=1), allow_play=play,
                                    sleep=sleep, clock=lambda: now)
    return stick, result


async def test_matching_card_selected_only_after_stable_focus_read():
    card = "REPLAY Clippers vs. Warriors Sun, Nov 1"
    stick, result = await trial([frame("Clippers Top Sports"), frame(card, card), frame(card, card),
                                 frame("Clippers vs. Warriors Included with your NBA League Pass subscription")])
    assert result.status == "recording_opened"
    assert stick.keys.count("KEYCODE_DPAD_CENTER") == 1
    assert stick.keys[0] == "KEYCODE_WAKEUP"


async def test_focus_changing_between_read_and_selection_never_presses_center():
    card = "REPLAY Clippers vs. Warriors Sun, Nov 1"
    stick, result = await trial([frame("Clippers Top Sports"), frame(card, card),
                                 frame("UPCOMING Raptors vs. Clippers", "UPCOMING Raptors vs. Clippers")])
    assert result.status == "stopped"
    assert "KEYCODE_DPAD_CENTER" not in stick.keys


async def test_unreadable_focus_does_not_blindly_open_first_result():
    stick, result = await trial([frame("Clippers Top Sports"), frame("REPLAY Clippers vs. Warriors Nov 1")])
    assert result.status == "stopped"
    assert "KEYCODE_DPAD_CENTER" not in stick.keys


async def test_detail_page_playing_background_session_is_not_success_or_blind_play():
    card = "REPLAY Clippers vs. Warriors Sun, Nov 1"
    stick, result = await trial([frame("Clippers Top Sports"), frame(card, card), frame(card, card),
                                 frame(f"Clippers vs. Warriors Nov 1, {date.today().year} Included with your NBA League Pass subscription")], play=True)
    assert result.status == "stopped" and "playback button" in result.reason
    assert stick.keys.count("KEYCODE_DPAD_CENTER") == 1


async def test_payment_wall_blocks_selection_even_with_matching_card():
    card = "REPLAY Clippers vs. Warriors Sun, Nov 1"
    stick, result = await trial([frame("Clippers Top Sports"), frame(card + " Subscribe now", card)])
    assert result.status == "stopped"
    assert "KEYCODE_DPAD_CENTER" not in stick.keys


async def test_exact_replay_button_then_player_title_is_observed_without_video_claim():
    card = "REPLAY Clippers vs. Warriors Sun, Nov 1"
    detail = f"Clippers vs. Warriors Nov 1, {date.today().year} Included with your NBA League Pass subscription Watch Replay Warriors Broadcast"
    stick, result = await trial([frame("Clippers Top Sports"), frame(card, card), frame(card, card),
                                 frame(detail, "Watch Replay Warriors Broadcast"),
                                 frame(detail, "Watch Replay Warriors Broadcast"),
                                 frame("Clippers vs. Warriors Warriors Broadcast 00:00 02:30:00")], play=True)
    assert result.status == "player_title_observed" and not result.video_verified
    assert stick.keys.count("KEYCODE_DPAD_CENTER") == 2


async def test_title_overlay_with_zero_speed_does_not_confirm_playback(monkeypatch):
    async def stationary(self, package):
        return {"state": "playing", "speed": 0.0}
    monkeypatch.setattr(Stick, "playback", stationary)
    card = "REPLAY Clippers vs. Warriors Sun, Nov 1"
    detail = frame(f"Clippers vs. Warriors Nov 1, {date.today().year} Included with your NBA League Pass subscription",
                   "Watch Replay Warriors Broadcast")
    stick, result = await trial([frame("Clippers Top Sports"), frame(card, card), frame(card, card),
                                 detail, detail, frame("Clippers vs. Warriors 00:00 02:30:00")], play=True)
    assert result.status == "stopped"
    assert not result.video_verified
    assert stick.keys.count("KEYCODE_DPAD_CENTER") == 2


async def test_cold_prime_picker_selects_configured_exact_profile_before_query(monkeypatch):
    from test_profile_selection import picker
    async def read_profile(self, *args, **kwargs):
        self.preferred_profiles = {"prime": "Taylor"}
        return NS(ok=True, out=picker("Taylor"))
    monkeypatch.setattr(Stick, "preferred_profiles", {"prime": "Taylor"}, raising=False)
    monkeypatch.setattr(Stick, "run", read_profile, raising=False)
    card = "REPLAY Clippers vs. Warriors Sun, Nov 1"
    stick, result = await trial([frame("Clippers Top Sports"), frame(card, card), frame(card, card),
                                 frame("Clippers vs. Warriors Included with your NBA League Pass subscription")],
                                initial=[frame("Who's watching? Taylor Guest"),
                                         frame("Clear local history Movie picks")])
    assert result.status == "recording_opened"
    actions = [step["action"] for step in result.steps]
    assert actions.index("profile") < actions.index("query")
    assert actions.count("open_search_after_profile") == 1
    assert stick.keys.count("KEYCODE_DPAD_CENTER") == 2  # exact profile, then exact dated card


async def test_unreadable_prime_profile_never_types_or_selects(monkeypatch):
    async def unreadable(self, *args, **kwargs):
        return NS(ok=True, out="")
    monkeypatch.setattr(Stick, "preferred_profiles", {"prime": "Taylor"}, raising=False)
    monkeypatch.setattr(Stick, "run", unreadable, raising=False)
    stick, result = await trial([], initial=[frame("Who's watching? Taylor Guest")])
    assert result.status == "stopped" and "profile controls are not readable" in result.reason
    assert not any(step["action"] == "query" for step in result.steps)
    assert "KEYCODE_DPAD_CENTER" not in stick.keys


async def test_prime_picker_remaining_after_selection_never_selects_twice(monkeypatch):
    from test_profile_selection import picker
    async def read_profile(self, *args, **kwargs):
        return NS(ok=True, out=picker("Taylor"))
    monkeypatch.setattr(Stick, "preferred_profiles", {"prime": "Taylor"}, raising=False)
    monkeypatch.setattr(Stick, "run", read_profile, raising=False)
    stick, result = await trial([], initial=[frame("Who's watching? Taylor Guest")])
    assert result.status == "stopped" and "no second OK" in result.reason
    assert stick.keys.count("KEYCODE_DPAD_CENTER") == 1
    assert not any(step["action"] == "query" for step in result.steps)


async def test_lingering_watch_button_does_not_get_a_duplicate_center():
    card = "REPLAY Clippers vs. Warriors Sun, Nov 1"
    detail = frame(f"Clippers vs. Warriors Nov 1, {date.today().year} Included with your NBA League Pass subscription",
                   "Watch Replay Warriors Broadcast")
    stick, result = await trial([frame("Clippers Top Sports"), frame(card, card), frame(card, card),
                                 *([detail] * 12)], play=True)
    assert result.status == "stopped"
    assert stick.keys.count("KEYCODE_DPAD_CENTER") == 2


async def test_black_player_frame_waits_and_uses_only_one_nonselect_overlay_key():
    card = "REPLAY Clippers vs. Warriors Sun, Nov 1"
    detail = frame(f"Clippers vs. Warriors Nov 1, {date.today().year} Included with your NBA League Pass subscription",
                   "Watch Replay Warriors Broadcast")
    empty = NS(read_ok=False, status="empty", text="", focus_status="none", focus_boxes=(), observations=())
    stick, result = await trial([frame("Clippers Top Sports"), frame(card, card), frame(card, card),
                                 detail, detail, empty, frame("Clippers vs. Warriors 00:00 02:30:00")], play=True)
    assert result.status == "player_title_observed"
    assert stick.keys.count("KEYCODE_DPAD_DOWN") == 1  # title appeared without an overlay probe
    assert stick.keys.count("KEYCODE_DPAD_CENTER") == 2


async def test_wrong_detail_year_stops_before_watch_even_with_matching_search_card():
    card = "REPLAY Clippers vs. Warriors Sun, Nov 1"
    detail = frame("Clippers vs. Warriors Nov 1, 2024 Included with your NBA League Pass subscription",
                   "Watch Replay Warriors Broadcast")
    stick, result = await trial([frame("Clippers Top Sports"), frame(card, card), frame(card, card), detail], play=True)
    assert "date and year" in result.reason
    assert stick.keys.count("KEYCODE_DPAD_CENTER") == 1


def test_retained_query_or_focused_result_is_not_a_search_reset():
    fresh = frame("Clear local history Movie picks Rent or buy a favorite")
    assert empty_search(fresh)
    fresh.observations = (NS(text="Clippers", x=.07, y=.08),)
    assert not empty_search(fresh)
    assert not empty_search(frame("Clear local history Movie picks Top Sports Clippers"))


async def test_unconfirmed_wake_stops_before_search_launch():
    class DreamingStick(Stick):
        async def shell(self, *args, **kwargs):
            return NS(ok=True, out="mWakefulness=Dreaming")

        async def view(self, *args, **kwargs):
            pytest.fail("must not launch search while still dreaming")

    async def observe():
        pytest.fail("must not proceed to OCR while still dreaming")

    result = await run_prime_search(DreamingStick(), observe, query="Clippers", home="Warriors", away="Clippers",
                                    requested=date.today(), sleep=no_sleep)
    assert result.status == "stopped" and "Awake" in result.reason


def test_detail_airing_times_are_not_player_progress():
    detail = frame(f"Clippers vs. Warriors Nov 1, {date.today().year} 8:30 PM Live at 10:00 PM Included with your NBA League Pass subscription Watch Replay",
                   "Watch Replay Warriors Broadcast")
    assert detail_verified(detail, "Warriors", "Clippers", date.today().replace(month=11, day=1))
    assert not player_overlay(detail, "Warriors", "Clippers")


def test_wrong_foreground_panel_cannot_borrow_matchup_and_date_from_background():
    detail = frame("Clippers vs. Warriors Nov 1, 2026 Included with your subscription Lakers vs. Raptors Nov 2, 2026",
                   "Watch Replay Lakers Broadcast")
    detail.observations = (
        NS(text="Clippers vs. Warriors Nov 1, 2026 Included with your subscription", x=.32, y=.15, width=.28, height=.1),
        NS(text="Lakers vs. Raptors Nov 2, 2026 Included with your subscription", x=.65, y=.2, width=.28, height=.1),
    )
    assert not detail_verified(detail, "Warriors", "Clippers", date(2026, 11, 1))


async def test_rating_then_late_title_can_succeed_with_two_bounded_overlay_probes():
    card = "REPLAY Clippers vs. Warriors Sun, Nov 1"
    detail = frame(f"Clippers vs. Warriors Nov 1, {date.today().year} Included with your NBA League Pass subscription",
                   "Watch Replay Warriors Broadcast")
    rating = frame("RATED all 00:00 02:27:13")
    stick, result = await trial([frame("Clippers Top Sports"), frame(card, card), frame(card, card), detail, detail,
                                 *([rating] * 24), frame("Clippers vs. Warriors 00:00 02:27:13")], play=True)
    assert result.status == "player_title_observed"
    assert stick.keys.count("KEYCODE_DPAD_DOWN") == 3  # sports row + two delayed overlay probes
    assert stick.keys.count("KEYCODE_DPAD_CENTER") == 2
    assert result.event_date_verified and not result.video_verified


async def test_permanently_empty_player_stops_after_deadline_without_reselecting_watch():
    card = "REPLAY Clippers vs. Warriors Sun, Nov 1"
    detail = frame(f"Clippers vs. Warriors Nov 1, {date.today().year} Included with your NBA League Pass subscription",
                   "Watch Replay Warriors Broadcast")
    empty = NS(read_ok=False, status="empty", text="", focus_status="none", focus_boxes=(), observations=())
    stick, result = await trial([frame("Clippers Top Sports"), frame(card, card), frame(card, card), detail, detail, empty], play=True)
    assert result.status == "stopped" and result.last_completed_stage == "watch_replay_selected"
    assert stick.keys.count("KEYCODE_DPAD_DOWN") == 3
    assert stick.keys.count("KEYCODE_DPAD_CENTER") == 2
    assert 30 < result.elapsed_s < 40


async def test_stale_capture_after_watch_cannot_prove_player_identity():
    card = "REPLAY Clippers vs. Warriors Sun, Nov 1"
    detail = frame(f"Clippers vs. Warriors Nov 1, {date.today().year} Included with your NBA League Pass subscription",
                   "Watch Replay Warriors Broadcast")
    stick, result = await trial([frame("Clippers Top Sports"), frame(card, card), frame(card, card), detail, detail,
                                 frame("Clippers vs. Warriors 00:00 02:27:13")], play=True, stale_at=7)
    assert result.status == "stopped" and "stale" in result.reason
    assert stick.keys.count("KEYCODE_DPAD_CENTER") == 2


async def test_search_history_can_load_before_recommendations_without_typing_early():
    card = "REPLAY Clippers vs. Warriors Sun, Nov 1"
    stick, result = await trial([frame("Clippers Top Sports"), frame(card, card), frame(card, card),
                                 frame("Clippers vs. Warriors")], initial=[frame("Clear local history a b Clear"),
                                 frame("Clear local history Movie picks Rent or buy a favorite")])
    assert result.status == "recording_opened"
    actions = [step["action"] for step in result.steps]
    assert actions.index("query") > actions.index("read") + 1


async def test_nonreset_query_waits_boundedly_then_stops_without_typing():
    stick, result = await trial([], initial=[frame("Clippers Top Sports")])
    assert result.status == "stopped" and "no typing" in result.reason
    assert all(step["action"] != "query" for step in result.steps)
    assert stick.keys == ["KEYCODE_WAKEUP"]


async def test_query_suggestions_can_precede_sports_results_without_duplicate_query():
    card = "REPLAY Clippers vs. Warriors Sun, Nov 1"
    stick, result = await trial([frame("Clippers Clippers Game Tonight"), frame("Clippers Top Sports"),
                                 frame(card, card), frame(card, card), frame("Clippers vs. Warriors")])
    assert result.status == "recording_opened"
    assert sum(step["action"] == "query" for step in result.steps) == 1


async def test_previously_started_recording_can_use_exact_focused_resume():
    card = "REPLAY Clippers vs. Warriors Sun, Nov 1"
    detail = frame(f"Clippers vs. Warriors Nov 1, {date.today().year} Included with your NBA League Pass subscription",
                   "Resume")
    stick, result = await trial([frame("Clippers Top Sports"), frame(card, card), frame(card, card), detail, detail,
                                 frame("Clippers vs. Warriors 00:00 02:27:13")], play=True)
    assert result.status == "player_title_observed" and result.event_date_verified
    assert stick.keys.count("KEYCODE_DPAD_CENTER") == 2


async def test_recorded_authorization_revoked_during_read_blocks_next_mutation():
    stick = Stick()
    active, counter = True, 0
    now = 100.

    async def sleep(seconds):
        nonlocal now
        now += seconds

    async def observe():
        nonlocal active, counter
        active = False
        counter += 1
        return CapturedScreen(frame("Clear local history Movie picks"), counter, now)

    result = await run_prime_search(stick, observe, query="Clippers", home="Warriors", away="Clippers",
                                    requested=date.today(), sleep=sleep, clock=lambda: now, may_run=lambda: active)
    assert result.status == "stopped" and "authorization changed" in result.reason
    assert not any(step["action"] == "query" for step in result.steps)
    assert stick.keys == ["KEYCODE_WAKEUP"]
