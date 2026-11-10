"""The audit's --dead v2 --alpha: catch-up pieces sized by the class and elapsed-time table; hindsight and floor print and write what they did.

Why: sized with the real dead time the route leaves 2,866 value points lost, with today's knowable floor 4,339 (41 fit nights,
simulated); the table of research ledger L1 is the knowable bound that could close part of that gap, and the audit has to size
with it before any risk level is chosen.
"""
import json
from types import SimpleNamespace as NS

import pytest

from redzone.models import League
from scripts import veto_audit as va
from tests.test_exits_catchup import truth
from tests.test_veto_audit_timing import _main, _row

RUSH = {"type": {"text": "Rush"}, "text": "run for 3"}
TEAM = {"type": {"text": "Timeout"}, "text": "Timeout #1 by WSH"}
OFFICIAL = {"type": {"text": "Official Timeout"}, "text": "Official Timeout"}
FLOOR_SAID = "dead floor: catch-up pieces sized by the q10 dead time the live code could know in advance, not the real length\n"
MEASURED_SAID = ("what-if: hidden stick load time 19 s (default 15 s)\n"
                 "timing measured: flip 2.2 s, clip load 19 s, reach capped at 175 s (SMALL SAMPLES, Nov 9, busy Mac)\n")


@pytest.fixture(autouse=True)
def assumed():
    va.use_timing()
    yield
    va.use_timing()


# ----- hindsight and floor are what they were ---------------------------------------------------------------------------------------

def test_the_defaults_know_nothing_of_v2():
    assert va.DEADS == ("hindsight", "floor", "v2") and (va.DEAD, va.RISK) == ("hindsight", 0.01)
    va.use_timing(dead="floor")
    assert (va.DEAD, va.RISK) == ("floor", 0.01)


def test_hindsight_and_floor_print_and_write_byte_for_byte_what_they_did(monkeypatch, tmp_path, capsys):
    rows = [_row()]
    base = {"sets": {}, "rows": rows, "rules": va.merge(rows), "avoidable_lost": {str(r): va.avoidable(rows, r) for r in va.REACHES}}
    text, written, jobs = _main(monkeypatch, tmp_path, capsys)
    assert (text, written, jobs) == (va.table(rows) + "\n", json.dumps(base, indent=1), [("nfl", "hindsight", {}, None, 15.0)])
    assert _main(monkeypatch, tmp_path, capsys, "--alpha", "0.05")[:2] == (text, written)        # --alpha alone changes nothing
    text, written, jobs = _main(monkeypatch, tmp_path, capsys, "--timing", "measured", "--dead", "floor")
    assert text == va.table(rows) + "\n" + MEASURED_SAID + FLOOR_SAID
    sets = {"timing.measured": True}
    assert written == json.dumps({**base, "sets": sets, "prep_s": 19.0, "timing": "measured", "dead": "floor"}, indent=1)
    assert jobs == [("nfl", "hindsight", sets, None, 19.0, "measured", "floor")]
    text, written, jobs = _main(monkeypatch, tmp_path, capsys, "--dead", "floor", "--alpha", "0.02")
    assert text == va.table(rows) + "\n" + FLOOR_SAID and written == json.dumps({**base, "dead": "floor"}, indent=1)
    assert jobs == [("nfl", "hindsight", {}, None, 15.0, "assumed", "floor")]


def test_floor_left_is_untouched_by_the_class():
    for timeout in (TEAM, OFFICIAL):
        rep = NS(league=League.NFL, wall=[100.0, 110.0, 200.0], plays=[RUSH, timeout, RUSH])
        assert va.floor_left(rep, 120.0) == 25.0 and va.floor_left(rep, 160.0) == 0.0 and va.floor_left(rep, 105.0) == 0.0


# ----- v2: the class and the elapsed time ------------------------------------------------------------------------------------------

def test_v2_left_reads_the_class_and_the_seconds_already_run():
    team = NS(league=League.NFL, wall=[100.0, 110.0, 400.0], plays=[RUSH, TEAM, RUSH])
    official = NS(league=League.NFL, wall=[100.0, 110.0, 400.0], plays=[RUSH, OFFICIAL, RUSH])
    assert (va.v2_left(team, 110.0), va.v2_left(official, 110.0)) == (12.0, 29.0)       # at its start, 99% level
    assert (va.v2_left(team, 120.0), va.v2_left(official, 120.0)) == (5.0, 19.0)        # 10 s in
    assert va.v2_left(official, 145.0) == pytest.approx(13.0 - 5.0)                     # 35 s in: the 30 s cell less 5 s
    assert va.v2_left(official, 105.0) == 0.0 and va.v2_left(official, 50.0) == 0.0 == va.v2_left(official, 500.0)
    va.use_timing(dead="v2", alpha=0.10)
    assert (va.v2_left(team, 110.0), va.v2_left(official, 110.0)) == (26.0, 110.0)
    assert va.v2_left(official, 170.0) == 61.0                                           # 60 s in, still 61 s sure at 90%
    assert va.floor_left(official, 170.0) == 0.0                                         # where the pooled floor ran out at 35 s


def test_a_run_of_one_kind_is_one_stoppage_from_its_first_entry():
    rep = NS(league=League.NFL, wall=[100.0, 110.0, 118.0, 400.0], plays=[RUSH, TEAM, OFFICIAL, RUSH])
    assert va.v2_left(rep, 120.0) == 5.0               # the team timeout, 10 s in: not an official one 2 s in (19 s)
    nba = {"type": {"text": "Full Timeout"}, "text": "Wizards Full timeout"}
    shot = {"type": {"text": "Jump Shot"}, "text": "makes 20-foot jumper"}
    hoops = NS(league=League.NBA, wall=[100.0, 110.0, 400.0], plays=[shot, nba, shot])
    assert va.v2_left(hoops, 110.0) == 3.0 and va.v2_left(hoops, 140.0) == 54.0          # nothing at the start, 54 s once 30 s in


def test_v2_sizes_pieces_with_the_table_and_never_past_the_truth(monkeypatch):
    air = va.Air(1000.0, "b", "td", 5.0)
    t = truth(monkeypatch, {"b": [air]})                                   # really 100 s of dead time left
    tally = va.Tally(route_on=True, route2=[(1040.0, "a", True)])
    monkeypatch.setattr(va, "v2_left", lambda rep, st: 20.0)
    monkeypatch.setattr(va, "floor_left", lambda rep, st: (_ for _ in ()).throw(AssertionError("v2 does not read the floor")))
    va.use_timing(dead="v2")
    shown, stats = va.route_shown([air], tally, t, 180)
    assert shown == {} and stats["pieces"] == 1 and stats["floor_over_truth"] == 0     # only the play's piece fits 20 s
    monkeypatch.setattr(va, "v2_left", lambda rep, st: 150.0)             # the table promises more than there really was
    shown, stats = va.route_shown([air], tally, t, 180)
    assert shown == {air: False} and stats["floor_over_truth"] == 1                    # sized by the real 100 s, counted
    monkeypatch.setattr(va, "v2_left", lambda rep, st: 0.0)
    assert va.route_shown([air], tally, t, 180)[1]["pieces"] == 0


def test_hindsight_reads_neither_floor(monkeypatch):
    air = va.Air(1000.0, "b", "td", 5.0)
    t = truth(monkeypatch, {"b": [air]})

    def boom(rep, st):
        raise AssertionError("a knowable floor was read in hindsight")
    monkeypatch.setattr(va, "v2_left", boom)
    monkeypatch.setattr(va, "floor_left", boom)
    assert va.route_shown([air], va.Tally(route_on=True, route2=[(1040.0, "a", True)]), t, 180)[0] == {air: False}


# ----- the option and the risk level ride on the job --------------------------------------------------------------------------------

def test_v2_and_its_alpha_ride_on_the_job_and_are_said(monkeypatch, tmp_path, capsys):
    text, written, jobs = _main(monkeypatch, tmp_path, capsys, "--dead", "v2")
    assert jobs == [("nfl", "hindsight", {}, None, 15.0, "assumed", "v2", 0.01)]
    assert "dead v2, alpha 0.01: " in text and "at the 99% level" in text and "dead floor" not in text
    out = json.loads(written)
    assert (out["dead"], out["alpha"]) == ("v2", 0.01) and "timing" not in out
    text, written, jobs = _main(monkeypatch, tmp_path, capsys, "--timing", "measured", "--dead", "v2", "--alpha", "0.05")
    assert jobs == [("nfl", "hindsight", {"timing.measured": True}, None, 19.0, "measured", "v2", 0.05)]
    assert "dead v2, alpha 0.05: " in text and "at the 95% level" in text
    assert (json.loads(written)["dead"], json.loads(written)["alpha"]) == ("v2", 0.05)


def test_a_worker_takes_the_risk_level_from_its_job(monkeypatch):
    monkeypatch.setattr(va, "replay", lambda *a: (_ for _ in ()).throw(RuntimeError("stop here")))
    with pytest.raises(RuntimeError):
        va.run_one(("nba", "hindsight", {}, 1, 19.0, "measured", "v2", 0.02))
    assert (va.TIMING, va.DEAD, va.RISK, va.FLIP_S) == ("measured", "v2", 0.02, 2.2)
    with pytest.raises(RuntimeError):
        va.run_one(("nba", "hindsight", {}, 1, 19.0, "measured", "floor"))
    assert (va.DEAD, va.RISK) == ("floor", 0.01)


def test_only_the_stored_risk_levels_are_taken(monkeypatch, tmp_path, capsys):
    with pytest.raises(ValueError):
        va.use_timing(dead="v2", alpha=0.03)
    with pytest.raises(SystemExit):
        _main(monkeypatch, tmp_path, capsys, "--dead", "v2", "--alpha", "0.03")
