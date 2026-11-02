from datetime import date
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from redzone.screen_ocr import FocusBox, OCRResult, TextBox
from redzone.tuners.prime_search import CapturedScreen
from redzone.tuners.yttv_search import dated_watch_evidence, explicit_event_date, finish_yttv_watch, run_yttv_search


WANTED = date(2026, 11, 1)


def watch(label="Watch Nov 1 • WAS vs PHI", caption="Philadelphia Eagles at Washington • Recorded 18 hours ago",
          requested=WANTED, observed_on=date(2026, 11, 2)):
    return dated_watch_evidence(label, home_aliases=("Commanders", "Washington", "WAS"),
                                away_aliases=("Eagles", "Philadelphia Eagles", "PHI"),
                                requested=requested, observed_on=observed_on, recent_recording_caption=caption)


def test_explicit_watch_day_and_recent_same_matchup_support_honestly_inferred_year():
    evidence = watch()
    assert evidence.matches and evidence.year_inferred and not evidence.source_year_visible
    assert evidence.event_date == "2026-11-01" and "inferred" in evidence.reason
    explicit = watch("Watch Nov 1, 2026 • PHI vs WAS", caption="")
    assert explicit.matches and explicit.source_year_visible and not explicit.year_inferred
    catalog = watch(caption="New 19 hours ago • Watch Nov 1...\nWashington\nCommanders")
    assert catalog.matches and catalog.year_inferred


def action_frame(label, *, chooser=False):
    focus = FocusBox(.6125, .26 if chooser else .70, .3625, .08333, 1, label, "neutral_filled_button")
    boxes = [TextBox(label, 1, .67, focus.y + .025, .26, .03)]
    if chooser:
        boxes += [TextBox("How would you like to begin watching?", 1, .63, .08, .31, .03),
                  TextBox("Start from beginning", 1, .67, .31, .25, .03)]
    return OCRResult("ok", tuple(boxes), 1920, 1080, focus_boxes=(focus,), focus_status="unique")


def player_frame():
    return OCRResult("ok", (TextBox("Philadelphia Eagles at Washington ...", 1, .37, .05, .28, .03),
                            TextBox("0:02 / 3:30:45", 1, .87, .05, .10, .03)), 1920, 1080)


async def finish_trial(frames, *, speed=1, stale_at=None, failed_key=None, unchanged=False):
    class PlaybackStick(Stick):
        calls = 0

        async def playback(self, pkg):
            self.calls += 1
            if self.calls == 1 or unchanged:
                return {"state": "playing", "speed": 1, "position": 100000, "updated": 1000}
            return {"state": "playing", "speed": speed, "position": 2000, "updated": 10000}

    st = PlaybackStick(failed_key)
    iterator, previous = iter(frames), None
    now, sequence = 100., 0

    async def sleep(seconds):
        nonlocal now
        now += seconds

    async def observe():
        nonlocal now, sequence, previous
        previous = next(iterator, previous)
        now += .1
        sequence += 1
        return CapturedScreen(previous, sequence - 1 if sequence == stale_at else sequence, now)

    result = await finish_yttv_watch(st, observe, home_aliases=("Washington", "Commanders", "WAS"),
        away_aliases=("Philadelphia Eagles", "Eagles", "PHI"), requested=WANTED, observed_on=date(2026, 11, 2),
        recent_recording_caption="New 19 hours ago • Watch Nov 1... Washington Commanders",
        sleep=sleep, clock=lambda: now)
    return st, result


def replay_frames():
    dated = action_frame("Watch Nov 1 • WAS vs PHI")
    start = action_frame("Start from beginning", chooser=True)
    return [dated, dated, action_frame("Watch key plays", chooser=True), start, start, player_frame()]


async def test_full_replay_uses_exact_watch_and_start_choices_then_new_player_identity():
    st, result = await finish_trial(replay_frames())
    assert st.keys == ["KEYCODE_DPAD_CENTER", "KEYCODE_DPAD_DOWN", "KEYCODE_DPAD_CENTER"]
    assert result.status == "player_title_observed" and result.transport_ready
    assert result.date_matches_request and result.year_inferred and not result.source_year_visible
    assert not result.event_date_verified and not result.video_verified


@pytest.mark.parametrize("speed,unchanged", [(0, False), (1, True)])
async def test_stationary_or_unchanged_transport_cannot_confirm_replay(speed, unchanged):
    st, result = await finish_trial(replay_frames(), speed=speed, unchanged=unchanged)
    assert result.status == "stopped" and not result.player_title
    assert st.keys.count("KEYCODE_DPAD_CENTER") == 2
    assert st.keys.count("KEYCODE_DPAD_DOWN") <= 3  # chooser + at most two overlays


async def test_stale_watch_confirmation_never_selects():
    st, result = await finish_trial(replay_frames(), stale_at=2)
    assert result.status == "stopped" and "stale" in result.reason and not st.keys


async def test_unrecognized_choice_never_gets_second_select():
    frames = replay_frames()
    frames[2] = action_frame("Resume", chooser=True)
    st, result = await finish_trial(frames)
    assert st.keys == ["KEYCODE_DPAD_CENTER"] and "uniquely focused" in result.reason


async def test_failed_watch_delivery_stops_without_retry():
    st, result = await finish_trial(replay_frames(), failed_key="KEYCODE_DPAD_CENTER")
    assert st.keys == ["KEYCODE_DPAD_CENTER"] and "delivery uncertain" in result.reason


@pytest.mark.parametrize("label,caption,requested,observed_on", [
    ("Watch Nov 2 • WAS vs PHI", "Eagles Commanders Recorded 18 hours ago", WANTED, date(2026, 11, 2)),
    ("Watch Nov 1 • DAL vs PHI", "Eagles Commanders Recorded 18 hours ago", WANTED, date(2026, 11, 2)),
    ("Watch Nov 1 • WAS vs PHI", "Cowboys Eagles Recorded 18 hours ago", WANTED, date(2026, 11, 2)),
    ("Watch Nov 1 • WAS vs PHI", "Eagles Commanders Recorded 1 month ago", WANTED, date(2026, 11, 2)),
    ("Watch Nov 1 • WAS vs PHI", "Eagles Commanders Recorded 18 hours ago", WANTED, date(2027, 1, 1)),
    ("Watch Nov 1 • WAS vs PHI", "Eagles Commanders Recorded 18 hours ago", WANTED, date(2026, 10, 31)),
    ("Buy Watch Nov 1 • WAS vs PHI", "Eagles Commanders Recorded 18 hours ago", WANTED, date(2026, 11, 2)),
])
def test_watch_inference_rejects_wrong_matchup_date_age_year_or_purchase(label, caption, requested, observed_on):
    assert not watch(label, caption, requested, observed_on).matches


def menu(caption, *, row="from your library", query="", library=False, background=""):
    focus = FocusBox(.11, .44, .22, .22, 1, "Playing 3:30:45")
    boxes = [TextBox(row.title(), 1, .115, .39, .21, .025)]
    boxes += [TextBox(line, 1, .115, .69 + .045 * i, .205, .03)
              for i, line in enumerate(caption.splitlines())]
    if query:
        boxes.append(TextBox(query, 1, .05, .17, .6, .05))
    if library:
        boxes.append(TextBox("From your library", 1, .115, .90, .21, .025))
    if background:
        boxes.append(TextBox(background, 1, .60, .69, .3, .03))
    return OCRResult("ok", tuple(boxes), 1920, 1080, focus_boxes=(focus,), focus_status="unique")


def start(query="Eagles Commanders"):
    return menu("Girly Air Force\nAdd Crunchyroll to watch", row="top picks", query=query, library=True)


class Stick:
    packages = {"youtubetv": "com.amazon.firetv.youtube.tv"}

    def __init__(self, failed_key=None):
        self.keys = []
        self.urls = []
        self.failed_key = failed_key

    async def ensure_awake(self):
        return False

    async def shell(self, *args, **kwargs):
        return NS(ok=True, out="mWakefulness=Awake")

    async def view(self, url, *args, **kwargs):
        self.urls.append(url)
        return NS(ok=True)

    async def key(self, code):
        self.keys.append(code)
        return NS(ok=code != self.failed_key)


async def trial(frames, *, query="Eagles Commanders", select=False, stale_at=None,
                failed_key=None, max_cards=4):
    st = Stick(failed_key)
    iterator, previous = iter(frames), None
    now, sequence = 100., 0

    async def sleep(seconds):
        nonlocal now
        now += seconds

    async def observe():
        nonlocal now, sequence, previous
        previous = next(iterator, previous)
        now += .1
        sequence += 1
        return CapturedScreen(previous, sequence - 1 if sequence == stale_at else sequence, now)

    result = await run_yttv_search(st, observe, query=query, home="Commanders", away="Eagles",
                                   requested=WANTED, allow_select=select, max_cards=max_cards,
                                   sleep=sleep, clock=lambda: now)
    return st, result


@pytest.mark.parametrize("text", ["Recorded 18 hours ago", "Recorded 1 month ago", "Nov 1, 2026",
                                  "Recorded Nov 1, 2026", "Aired Nov 1, 2026 | Aired Nov 2, 2026"])
def test_recording_age_or_ambiguous_date_is_not_event_date(text):
    assert explicit_event_date(text) is None


def test_explicit_event_date_requires_year_and_valid_calendar_date():
    assert explicit_event_date("Aired Nov 1, 2026") == WANTED
    assert explicit_event_date("Game date: 2026-11-01") == WANTED
    assert explicit_event_date("Aired Feb 30, 2026") is None
    assert explicit_event_date("Aired Nov 1") is None
    assert explicit_event_date("Aired Feb 30, 2026 | Aired Nov 1, 2026") is None


async def test_unrelated_paid_top_pick_is_skipped_for_visible_library_without_select():
    st, result = await trial([start(), menu("Eagles\nCommanders\nNBC 4 Recorded 18 hours ago")], select=True)
    assert st.keys == ["KEYCODE_DPAD_DOWN"]
    assert result.status == "candidate_found" and not result.event_date_verified
    assert not result.selected_card and not result.video_verified
    assert "exact event date is unverified" in result.reason


async def test_bright_unreadable_top_pick_allows_only_one_down_then_requires_library_focus():
    bright = replace(start(), focus_boxes=(), focus_status="none")
    library = menu("Eagles\nCommanders\nRecorded 18 hours ago")
    st, result = await trial([bright, library], select=True)
    assert st.keys == ["KEYCODE_DPAD_DOWN"] and result.status == "candidate_found"
    st, result = await trial([bright, replace(library, focus_boxes=(), focus_status="none")], select=True)
    assert st.keys == ["KEYCODE_DPAD_DOWN"] and result.status == "stopped"
    assert "focus is absent" in result.reason


async def test_actual_clipped_titles_cannot_borrow_teams_from_neighbor_or_query():
    first = menu("Philadelphia Eagles at\nWashington\nNBC 4 Recorded 18 hours ago",
                 background="Commanders Aired Nov 1, 2026")
    second = menu("Washington\nCommanders at\nFOX 5 Recorded 1 month ago", background="Eagles")
    st, result = await trial([start(), first, second])
    assert "omit a requested team" in result.reason
    assert "KEYCODE_DPAD_CENTER" not in st.keys and not result.event_date_verified


async def test_exact_team_catalog_opens_but_relative_recording_is_not_selected():
    team = menu("Philadelphia Eagles", row="top picks", query="Philadelphia Eagles")
    st, result = await trial([team, team, menu("Eagles\nCommanders\nRecorded 18 hours ago", row="completed")],
                             query="Philadelphia Eagles", select=True)
    assert st.keys == ["KEYCODE_DPAD_CENTER"]  # catalog only
    assert result.status == "candidate_found" and not result.selected_card
    assert st.urls == ["https://tv.youtube.com/search?q=Philadelphia%20Eagles"]


async def test_explicit_event_date_and_both_teams_allow_selection_after_fresh_confirmation():
    recording = menu("Eagles at Commanders\nAired Nov 1, 2026")
    st, result = await trial([start(), recording, recording], select=True)
    assert result.status == "selection_sent" and result.event_date_verified
    assert st.keys == ["KEYCODE_DPAD_DOWN", "KEYCODE_DPAD_CENTER"]
    assert not result.video_verified and "remain unverified" in result.reason


async def test_wrong_date_is_skipped_without_selection():
    old = menu("Eagles at Commanders\nAired Oct 1, 2026")
    st, result = await trial([start(), old], select=True)
    assert result.status == "stopped" and not result.event_date_verified
    assert "KEYCODE_DPAD_CENTER" not in st.keys


async def test_changed_confirmation_cannot_select_another_game():
    recording = menu("Eagles at Commanders\nAired Nov 1, 2026")
    other = menu("Cowboys at Eagles\nAired Nov 1, 2026")
    st, result = await trial([start(), recording, other], select=True)
    assert result.status == "stopped" and "changed" in result.reason
    assert "KEYCODE_DPAD_CENTER" not in st.keys


async def test_stale_post_navigation_capture_stops():
    st, result = await trial([start(), menu("Eagles Commanders\nAired Nov 1, 2026")], select=True, stale_at=2)
    assert "stale" in result.reason and st.keys == ["KEYCODE_DPAD_DOWN"]


async def test_no_visible_library_or_exact_team_card_means_no_navigation():
    st, result = await trial([menu("The Blind Side\n2009 PG-13", row="top picks", query="Eagles Commanders")])
    assert not st.keys and "no observed Library" in result.reason


async def test_uncertain_navigation_never_retries():
    st, result = await trial([start()], failed_key="KEYCODE_DPAD_DOWN")
    assert st.keys == ["KEYCODE_DPAD_DOWN"] and "delivery uncertain" in result.reason


async def test_purchase_on_exact_recording_caption_blocks_selection():
    st, result = await trial([start(), menu("Eagles Commanders\nAired Nov 1, 2026\nAdd Sports to watch")], select=True)
    assert "purchase" in result.reason and "KEYCODE_DPAD_CENTER" not in st.keys


async def test_profile_picker_without_configuration_sends_no_guess():
    profile = menu("Who's watching?", query="Eagles Commanders")
    st, result = await trial([profile])
    assert "no preferred profile configured" in result.reason and not st.keys
