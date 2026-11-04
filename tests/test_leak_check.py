"""scripts/leak_check.py (research 05 §3) and the two leaks it was written for."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from redzone.models import League
from redzone.sources import espn
from scripts import leak_check

FIX = Path(__file__).parent / "fixtures"


def test_a_replayed_moment_has_no_final_leader_board():
    """parse_summary(upto=i) used to fill g.leaders from the FINAL summary's leaders block."""
    data = json.loads((FIX / "espn_nfl_summary_401772894.json").read_text())
    assert data.get("leaders"), "fixture has a leader board"
    live = espn.parse_summary(League.NFL, data)
    assert live.leaders                                   # live: the board as of now is fine
    replay = espn.parse_summary(League.NFL, data, upto=20)
    assert replay.leaders == []


def test_night_fixtures_check_fails_on_a_locked_game():
    from redzone.lab.night.run import GAMES
    g = GAMES[0]
    locked = {g.league.lower(): {g.fixture.split("_")[-1]}}
    assert leak_check.check_night_fixtures(locked)[0][0] == "FAIL"
    assert leak_check.check_night_fixtures({"nhl": {"1"}, "nba": {"2"}})[0][0] == "PASS"


def test_the_night_no_longer_replays_the_two_locked_games():
    from redzone.lab.night.run import GAMES
    ids = {g.fixture.split("_")[-1] for g in GAMES}
    assert not ids & {"401892626", "401909906"}           # NHL and NBA, both on locked nights of eval-data-v1


def test_an_expired_allowlist_entry_fails():
    rows = leak_check.check_known_errors(today=date(2099, 1, 1))
    assert rows and all(r[0] == "FAIL" for r in rows)
    assert all(r[0] == "PASS" for r in leak_check.check_known_errors(today=date(2026, 11, 4)))


def test_replay_fields_pass_on_every_fixture():
    rows = leak_check.check_replay_fields()
    assert rows and all(r[0] == "PASS" for r in rows), [r for r in rows if r[0] != "PASS"]


def test_the_whole_check_has_no_failures():
    assert not [r for r in leak_check.run_all() if r[0] == "FAIL"]
