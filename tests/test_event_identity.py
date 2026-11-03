"""A valid provider URL is not evidence that it belongs to the requested game."""
from copy import deepcopy

import pytest

from redzone.sources.eventlinks import match


START = 1_793_635_200


def airing(ref, **changes):
    return {"league": "mlb", "provider": "espnplus", "source": "espn",
            "away": "New York Yankees", "home": "Boston Red Sox", "ref": ref,
            "espn_event_id": None, "event_id": None, "start": START,
            "feed": "national", "geo_ok": True, **changes}


def find(rows, **options):
    return match(rows, "mlb", {"NYY", "Yankees"}, {"BOS", "Red Sox"}, **options)


def test_conflicting_id_never_falls_back_to_matching_teams_or_enters_alts():
    rows = [airing("wrong-game", espn_event_id="202", geo_ok=True),
            airing("requested-game", espn_event_id=101, geo_ok=False)]
    found = find(rows, espn_event_id="101", expected_start=START)
    assert found["espnplus"]["ref"] == "requested-game"
    assert found["espnplus"]["alts"] == []
    assert find(rows[:1], espn_event_id=101) == {}


def test_exact_id_wins_over_alias_ranking_and_schedule_disagreement():
    rows = [airing("alias", geo_ok=True),
            airing("exact", espn_event_id="101", away="unrecognized", home="names",
                   start=START + 3 * 3600, geo_ok=False)]
    found = find(rows, espn_event_id=101, expected_start=START)
    assert found["espnplus"]["ref"] == "exact"


def test_doubleheader_and_next_day_are_not_same_game_alternatives():
    rows = [airing("first", provider="prime", source="apple", event_id="apple-first"),
            airing("second", provider="prime", source="apple", event_id="apple-second",
                   start=START + 4 * 3600),
            airing("tomorrow", provider="prime", source="apple", event_id="apple-tomorrow",
                   start=START + 86400)]
    assert find(rows) == {}  # team aliases alone cannot identify the requested event
    found = find(rows, expected_start=START + 4 * 3600)
    assert found["prime"]["ref"] == "second"
    assert found["prime"]["alts"] == []


def test_close_duplicate_events_fail_closed_even_when_one_is_nearer():
    rows = [airing("first", source="apple", event_id="first"),
            airing("second", source="apple", event_id="second", start=START + 1800)]
    assert find(rows, expected_start=START) == {}


def test_same_event_home_away_and_other_provider_broadcasts_survive():
    rows = [airing("home", provider="prime", source="apple", event_id="same", feed="home"),
            airing("away", provider="prime", source="apple", event_id="same", feed="away"),
            airing("peacock", provider="peacock", source="apple", event_id="same"),
            airing("pregame-start", espn_event_id="101", start=START - 1800)]
    original = deepcopy(rows)
    found = find(rows, expected_start=START, espn_event_id=101)
    assert set(found) == {"prime", "peacock", "espnplus"}
    assert found["prime"]["ref"] == "home"
    assert [i["ref"] for i in found["prime"]["alts"]] == ["away"]
    assert rows == original


def test_exact_id_airing_anchors_name_only_rows_when_start_not_provided():
    rows = [airing("exact", espn_event_id="101"),
            airing("right", provider="prime", source="apple", event_id="right"),
            airing("wrong-day", provider="prime", source="apple", event_id="wrong",
                   start=START + 86400)]
    assert find(rows, espn_event_id=101)["prime"]["ref"] == "right"


@pytest.mark.parametrize("start", [None, "invalid", float("nan"), float("inf"), True])
def test_name_only_rows_with_unknown_start_cannot_bypass_supplied_schedule(start):
    assert find([airing("unknown-time", start=start)], expected_start=START) == {}


def test_expected_start_is_epoch_not_machine_local_date_and_bad_anchor_fails_closed():
    assert find([airing("right")], expected_start=str(START))["espnplus"]["ref"] == "right"
    assert find([airing("unknown")], expected_start="2026-11-02") == {}


def test_missing_source_ids_do_not_allow_next_day_rows_into_alternatives():
    rows = [airing("today"), airing("tomorrow", start=START + 86400)]
    assert find(rows) == {}
    assert find(rows, expected_start=START)["espnplus"]["alts"] == []
