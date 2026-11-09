"""ESPN Unlimited events through YouTube TV (discovery.espn_on_yttv, WP-22), with fakes only.

Why: the route is unproven until a TV night, so these pin the flag-off contract and the can't-reach rules."""
from __future__ import annotations

import json

import pytest

from redzone.channels import (
    ESPN_REASON_TEXT,
    ChannelMap,
    route_class,
)
from redzone.discovery.yttv import (
    ESPN_ROW_UNKNOWNS,
    EventRow,
    apply_espn_rows,
    espn_reach_counts,
    match_espn_rows,
)
from redzone.models import GameState, League, Team

CODE = "AbCdEfGhIjK"
CODE2 = "ZyXwVuTsRqP"


def game(gid="1", league=League.NHL, home=("NYR", "Rangers"), away=("BOS", "Bruins"), broadcasts=("ESPN+",)):
    return GameState(league=league, game_id=gid, home=Team(*home), away=Team(*away),
                     broadcasts=list(broadcasts), start_time=1794000000.0)


@pytest.fixture
def cm(tmp_path):
    m = ChannelMap(tmp_path / "channels.json")
    m.espn_on_yttv = True
    return m


@pytest.fixture
def off(tmp_path):
    return ChannelMap(tmp_path / "channels.json")


def row(title="Bruins at Rangers", code=CODE, r="ESPN Unlimited 1", net="ESPN"):
    return EventRow(title=title, code=code, row=r, network=net)


# --- flag off: today's behaviour --------------------------------------------------------------

def test_flag_defaults_off(off):
    assert off.espn_on_yttv is False


def test_flag_read_from_config_key(off):
    off.apply_config({"discovery.espn_on_yttv": True})
    assert off.espn_on_yttv is True
    off.apply_config({})
    assert off.espn_on_yttv is False


def test_off_placeholder_unchanged(off):
    w = off.where(game())
    assert (w.target, w.label, w.confidence, w.tunable) == ("youtubetv:espnplus", "ESPN+ (event link not found yet)",
                                                            "likely", False)


def test_off_ignores_saved_event_code(off):
    off.espn_events["1"] = CODE
    assert off.where(game()).target == "youtubetv:espnplus"


def test_off_keeps_st_slot_and_espn_app_routes(off):
    off.st_slots["1"] = CODE
    assert off.where(game()).target == f"youtubetv:ev:{CODE}"
    del off.st_slots["1"]
    off.espn_ids["1"] = "abc"
    assert off.where(game()).target == "espn:abc"


def test_off_local_team_still_gets_the_old_placeholder(off):
    assert off.where(game(home=("CAR", "Hurricanes"))).target == "youtubetv:espnplus"


def test_save_without_events_writes_todays_keys(off):
    off.save()
    assert list(json.loads(off.path.read_text())) == ["codes", "overrides", "team_rules", "prime_gti", "st_slots",
                                                      "espn_ids", "st_codes", "saved"]


def test_apply_rows_is_a_no_op_with_flag_off(off):
    m = apply_espn_rows(off, [row()], [game()])
    assert not m.matched and off.espn_events == {} and not off.path.exists()


# --- flag on: the route -----------------------------------------------------------------------

def test_matched_event_tunes_through_yttv(cm):
    cm.espn_events["1"] = CODE
    w = cm.where(game())
    assert w.target == f"youtubetv:ev:{CODE}" and w.tunable and cm.yttv_code(w.target) == CODE
    assert route_class(w.target) == "event"


def test_event_route_is_likely_until_a_tv_night_proves_it(cm):
    cm.espn_events["1"] = CODE
    assert cm.where(game()).confidence == "likely"


def test_can_reach_with_no_row_stays_untunable(cm):
    w = cm.where(game())
    assert w.target == "youtubetv:espnplus" and not w.tunable
    assert cm.cant_reach_reason(game()) == "no_event_row"


def test_events_survive_save_and_load(cm):
    cm.espn_events["1"] = CODE
    cm.save()
    assert ChannelMap(cm.path).espn_events == {"1": CODE}


def test_national_cable_still_wins_over_event(cm):
    cm.codes["espn"] = CODE2
    cm.espn_events["1"] = CODE
    assert cm.where(game(broadcasts=("ESPN", "ESPN+"))).target == "youtubetv:espn"


# --- can-reach rules --------------------------------------------------------------------------

@pytest.mark.parametrize("league,proof", [(League.NHL, "named"), (League.CFB, "named"), (League.CBB, "assumed")])
def test_leagues_that_can_reach(cm, league, proof):
    r = cm.espn_reach(game(league=league))
    assert r.can_reach and r.reason == "no_event_row" and r.proof == proof


@pytest.mark.parametrize("league", [League.NFL, League.NBA, League.MLB])
def test_leagues_not_named_are_refused_with_a_reason(cm, league):
    g = game(league=league)
    r = cm.espn_reach(g)
    assert not r.can_reach and r.reason == "league_not_confirmed"
    assert cm.where(g).target is None
    assert cm.cant_reach_reason(g) == "league_not_confirmed"


def test_local_team_is_a_hard_no_even_with_a_row(cm):
    g = game(home=("CAR", "Hurricanes"))
    cm.espn_events["1"] = CODE
    assert cm.where(g).target is None
    assert cm.cant_reach_reason(g) == "local_blackout"


@pytest.mark.parametrize("name", ["ACC Network Extra", "ACCNX", "SEC Network+", "SECN+"])
def test_extra_feeds_unconfirmed_until_a_row_appears(cm, name):
    g = game(league=League.CBB, broadcasts=(name,))
    assert cm.cant_reach_reason(g) == "feed_unconfirmed"
    cm.espn_events["1"] = CODE
    assert cm.where(g).tunable and cm.cant_reach_reason(g) is None


def test_extra_feed_with_espn_plus_listed_is_ok(cm):
    assert cm.espn_reach(game(league=League.CBB, broadcasts=("ACCNX", "ESPN+"))).can_reach


def test_a_row_proves_a_league_not_named(cm):
    g = game(league=League.MLB)
    cm.espn_events["1"] = CODE
    assert cm.where(g).target == f"youtubetv:ev:{CODE}"


def test_refused_game_keeps_its_espn_app_fallback(cm):
    g = game(home=("CAR", "Hurricanes"))
    cm.espn_ids["1"] = "abc"
    assert cm.where(g).target == "espn:abc"
    assert cm.cant_reach_reason(g) == "espn_app_unproven"


def test_every_reason_has_words(cm):
    assert {"no_event_row", "league_not_confirmed", "feed_unconfirmed", "local_blackout"} <= set(ESPN_REASON_TEXT)


# --- reasons for the other routes -------------------------------------------------------------

def test_reason_is_none_when_tunable(cm):
    cm.codes["fs1"] = CODE
    assert cm.cant_reach_reason(game(broadcasts=("FS1",))) is None


def test_reason_no_channel_code(cm):
    assert cm.cant_reach_reason(game(broadcasts=("FS1",))) == "no_channel_code"


def test_reason_peacock_only(cm):
    assert cm.cant_reach_reason(game(league=League.CBB, broadcasts=("Peacock",))) == "app_not_set_up"


def test_reason_prime_link_missing(cm):
    g = game(league=League.NBA, home=("NYK", "Knicks"), away=("BOS", "Celtics"), broadcasts=("Prime Video",))
    assert cm.cant_reach_reason(g) == "no_event_link"


def test_reason_works_with_flag_off(off):
    assert off.cant_reach_reason(game()) == "no_event_row"


def test_reach_counts_group_by_league(cm):
    gs = [game("1"), game("2", home=("CAR", "Hurricanes")), game("3", league=League.MLB)]
    assert espn_reach_counts(cm, gs) == {"nhl": {"no_event_row": 1, "local_blackout": 1},
                                         "mlb": {"league_not_confirmed": 1}}


# --- row matching -----------------------------------------------------------------------------

def test_row_maps_to_its_game():
    assert match_espn_rows([row()], [game()]).matched == {"1": CODE}


def test_apply_saves_and_routes(cm):
    apply_espn_rows(cm, [row()], [game()])
    assert ChannelMap(cm.path).espn_events == {"1": CODE}


def test_apply_moves_a_reused_code(cm):
    cm.espn_events["old"] = CODE
    apply_espn_rows(cm, [row()], [game()])
    assert cm.espn_events == {"1": CODE}


@pytest.mark.parametrize("title,net", [("Bruins at Rangers on Peacock", "ESPN"), ("Bruins at Rangers", "Peacock ESPN")])
def test_peacock_rows_never_match(title, net):
    m = match_espn_rows([row(title=title, net=net)], [game()])
    assert not m.matched and m.refused == [(title, "peacock")]


@pytest.mark.parametrize("title", ["Bruins at Rangers (Replay)", "Bruins at Rangers Pregame", "Encore: Bruins at Rangers"])
def test_non_live_rows_refused(title):
    m = match_espn_rows([row(title=title)], [game()])
    assert not m.matched and m.refused == [(title, "not_live")]


def test_womens_game_cannot_take_the_mens_slot():
    g = game(league=League.CBB, home=("UNC", "Tar Heels"), away=("DUKE", "Blue Devils"))
    t = "Blue Devils at Tar Heels (Women's)"
    m = match_espn_rows([row(title=t)], [g])
    assert not m.matched and m.refused == [(t, "womens")]


def test_row_outside_espn_is_refused():
    m = match_espn_rows([row(r="Sports", net="NBA TV")], [game()])
    assert m.refused == [("Bruins at Rangers", "not_espn_row")]


def test_bad_code_refused():
    m = match_espn_rows([row(code="short")], [game()])
    assert m.refused == [("Bruins at Rangers", "bad_code")]


def test_unknown_title_refused():
    assert match_espn_rows([row(title="Sportscenter")], [game()]).refused == [("Sportscenter", "no_game")]


def test_shared_school_word_does_not_cross_match():
    a = game("a", league=League.CFB, home=("OSU", "Ohio State"), away=("MSU", "Michigan State"))
    b = game("b", league=League.CFB, home=("PSU", "Penn State"), away=("IOWA", "Iowa"))
    t = "Penn State at Michigan State"
    assert match_espn_rows([row(title=t)], [a, b]).refused == [(t, "no_game")]


def test_same_team_twice_tonight_is_refused():
    a = game("a", home=("NYR", "Rangers"), away=("BOS", "Bruins"))
    b = game("b", home=("NYR", "Rangers"), away=("TOR", "Maple Leafs"))
    assert match_espn_rows([row(title="Rangers vs Bruins")], [a, b]).refused == [("Rangers vs Bruins", "no_game")]


def test_two_feeds_keep_the_first_and_note_the_other():
    m = match_espn_rows([row(), row(title="Bruins at Rangers (Home Feed)", code=CODE2)], [game()])
    assert m.matched == {"1": CODE} and m.alternates == {"1": [CODE2]}


def test_tv_night_unknowns_are_listed():
    assert len(ESPN_ROW_UNKNOWNS) >= 5
