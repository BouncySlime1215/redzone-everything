"""Feed-arrival log core (redzone/lab/feedlag.py): row kinds, parsing, noticing new rows, the day files. No network."""
import json
from datetime import datetime

import pytest

from redzone.lab import feedlag
from redzone.models import League
from tests.feedlag_fakes import NIGHT_START, cdn_action, cdn_pbp, espn_play, espn_summary

T0 = NIGHT_START


@pytest.mark.parametrize("type_text,scoring,kind", [
    ("Jump Shot", True, "shot_made"), ("Jump Shot", False, "shot_miss"), ("LayUpShot", True, "shot_made"),
    ("Free Throw - 1 of 2", True, "free_throw"), ("MadeFreeThrow", False, "free_throw"),
    ("Personal Foul", False, "foul"), ("Shooting Foul", False, "foul"), ("Offensive Charge", False, "foul"),
    ("Offensive Foul Turnover", False, "turnover"), ("Bad Pass\nTurnover", False, "turnover"),
    ("Full Timeout", False, "timeout"), ("OfficialTVTimeOut", False, "timeout"),
    ("End Period", False, "period_end"), ("End Game", False, "game_end"), ("Jumpball", False, "jumpball"),
    ("Substitution", False, "sub"), ("Defensive Rebound", False, "rebound"), ("Steal", False, "steal"),
    ("Block Shot", False, "block"), ("Coach's Challenge (Stands)", False, "review"), ("Kicked Ball", False, "violation"),
    ("Something New", False, "other"),
])
def test_espn_kind_covers_the_vocabulary(type_text, scoring, kind):
    got = feedlag.espn_kind(espn_play("1", type_text, T0, scoring=scoring))
    assert got == kind and got in feedlag.ROW_KINDS


@pytest.mark.parametrize("action,sub,shot,kind", [
    ("3pt", "Jump Shot", "Made", "shot_made"), ("2pt", "Layup", "Missed", "shot_miss"), ("heave", "jumpshot", None, "shot_miss"),
    ("freethrow", "1 of 2", "Made", "free_throw"), ("foul", "personal", None, "foul"), ("timeout", "full", None, "timeout"),
    ("period", "start", None, "period_start"), ("period", "end", None, "period_end"), ("game", "end", None, "game_end"),
    ("substitution", "in", None, "sub"), ("instantreplay", "", None, "review"), ("memo", "", None, "other"),
])
def test_cdn_kind(action, sub, shot, kind):
    assert feedlag.cdn_kind(cdn_action(1, T0, action, sub, shot)) == kind


def test_espn_rows_carry_stamp_period_clock_and_the_shows_own_dead_ball_label():
    rows = feedlag.espn_rows(League.NBA, espn_summary([
        espn_play("a1", "Personal Foul", T0, period=3, clock="4:05"),
        espn_play("a2", "Jump Shot", T0 + 5, scoring=True, clock="3:58.5"),
        {"type": {"text": "no id"}},                        # a play without an id is skipped, not fatal
    ]))
    assert [r.row for r in rows] == ["a1", "a2"]
    assert rows[0].stamp == int(T0) and rows[0].per == 3 and rows[0].clk == 245.0
    assert rows[0].stop == "whistle" and rows[1].stop == "none"


def test_cdn_rows_carry_the_feeds_publish_time_and_ignore_garbage():
    rows = feedlag.cdn_rows(cdn_pbp([cdn_action(7, T0, "foul", "personal", edited=T0 + 4.5), {"noaction": 1}]))
    assert len(rows) == 1 and rows[0].row == "7" and rows[0].kind == "foul" and rows[0].stop == "whistle"
    assert rows[0].pub == pytest.approx(T0 + 4.5, abs=0.01) and rows[0].stamp == pytest.approx(T0, abs=0.01)
    assert feedlag.cdn_rows(None) == [] and feedlag.espn_rows(League.NBA, "oops") == []


def row(i, kind="foul"):
    return feedlag.FeedRow(str(i), T0 + i, kind, "t", "none", 1, 600.0, None)


def test_a_game_already_under_way_logs_its_history_as_baseline_not_as_arrivals():
    tr = feedlag.ArrivalTracker(feedlag.SRC_ESPN, "nba", "g1")
    first = tr.observe([row(1), row(2)], sent=T0 + 100, received=T0 + 100.3)
    assert [e["ev"] for e in first] == ["game"] and first[0]["baseline"] == 2
    again = tr.observe([row(1), row(2), row(3)], sent=T0 + 110, received=T0 + 110.2)
    assert [e["row"] for e in again] == ["3"]
    assert again[0]["prev"] == T0 + 100 and again[0]["t"] == pytest.approx(T0 + 110.2)      # published inside (prev, t]
    assert tr.observe([row(1), row(2), row(3)], sent=T0 + 120, received=T0 + 120.1) == []     # nothing is logged twice


def test_a_game_seen_before_the_tip_has_no_history_and_missing_polls_bound_the_first_rows():
    tr = feedlag.ArrivalTracker(feedlag.SRC_CDN, "nba", "g1", gid="0022600230", primed=True)
    tr.empty_poll(sent=T0 + 50)                               # the feed's file did not exist yet
    ev = tr.observe([row(1), row(2)], sent=T0 + 60, received=T0 + 60.4)
    assert [e["row"] for e in ev] == ["1", "2"] and all(e["prev"] == T0 + 50 for e in ev)
    assert ev[0]["gid"] == "0022600230"


def test_restart_remembers_logged_rows_so_they_are_not_logged_again(tmp_path):
    log = feedlag.DayLog(tmp_path)
    tr = feedlag.ArrivalTracker(feedlag.SRC_ESPN, "nba", "g1")
    tr.observe([row(1)], T0, T0 + 0.1)
    log.append("2026-11-12", tr.observe([row(1), row(2)], T0 + 10, T0 + 10.1))
    known = log.known("2026-11-12")
    assert known == {(feedlag.SRC_ESPN, "g1"): {"2"}}
    tr2 = feedlag.ArrivalTracker(feedlag.SRC_ESPN, "nba", "g1", known=known[(feedlag.SRC_ESPN, "g1")])
    first = tr2.observe([row(1), row(2), row(3)], T0 + 100, T0 + 100.1)       # first look after the restart: history
    assert [e["ev"] for e in first] == ["game"] and first[0]["baseline"] == 2    # rows 1 and 3; row 2 is already logged
    assert [e["row"] for e in tr2.observe([row(1), row(2), row(3), row(4)], T0 + 110, T0 + 110.1)] == ["4"]


def test_the_log_is_append_only_json_lines_and_holds_no_text_or_names(tmp_path):
    log = feedlag.DayLog(tmp_path / "feedlag")
    rows = feedlag.espn_rows(League.NBA, espn_summary([espn_play("p1", "Jump Shot", T0, text="Zorblax Quimby makes a jumper")]))
    tr = feedlag.ArrivalTracker(feedlag.SRC_ESPN, "nba", "g1", primed=True)
    log.append("2026-11-12", tr.observe(rows, T0 + 1, T0 + 2))
    log.append("2026-11-12", [{"ev": "stop", "t": T0 + 3}])
    text = log.path("2026-11-12").read_text()
    lines = [json.loads(x) for x in text.splitlines()]
    assert [x["ev"] for x in lines] == ["row", "stop"]
    assert "Zorblax" not in text and "makes a jumper" not in text
    assert set(lines[0]) <= {"ev", "t", "prev", "src", "lg", "game", "row", "kind", "typ", "stop", "stamp", "per", "clk",
                             "pub", "gid"}
    log.append("2026-11-12", [{"ev": "start", "t": T0 + 9}])
    assert len(log.path("2026-11-12").read_text().splitlines()) == 3 and text in log.path("2026-11-12").read_text()


def test_a_night_runs_until_6am_eastern():
    at = lambda *a: datetime(*a, tzinfo=feedlag.EASTERN).timestamp()        # noqa: E731
    assert feedlag.slate_date(at(2026, 11, 12, 19, 0)) == "2026-11-12"
    assert feedlag.slate_date(at(2026, 11, 13, 1, 30)) == "2026-11-12"     # a 1 AM finish is still last night's
    assert feedlag.slate_date(at(2026, 11, 13, 5, 59)) == "2026-11-12"
    assert feedlag.slate_date(at(2026, 11, 13, 6, 1)) == "2026-11-13"
