"""The audit's --timing measured|assumed and --dead floor|hindsight: the defaults print and write exactly what they did.

Why: the zero-loss audit sized catch-up clips with a 3.2 s flip, a 15 s load and the real dead-ball length known afterwards; the TV
measured 2.2 s and 19 s (Nov 9, small samples) and the live code only ever knows a floor (anchor-mode plan, section 7).
"""
import json
import sys
from types import SimpleNamespace as NS

import pytest

from redzone.models import League
from scripts import veto_audit as va
from tests.test_exits_catchup import truth


@pytest.fixture(autouse=True)
def assumed():
    va.use_timing()
    yield
    va.use_timing()


def test_the_defaults_are_todays_numbers():
    assert (va.TIMING, va.DEAD, va.FLIP_S, va.PREP_S, va.REACH_CAP_S) == ("assumed", "hindsight", 3.2, 15.0, float("inf"))


def test_measured_reads_the_timing_config():
    va.use_timing("measured")
    assert (va.FLIP_S, va.PREP_S, va.REACH_CAP_S) == (2.2, 19.0, 175.0)
    va.use_timing("measured", sets={"timing.clip_prep_s": 25.0})
    assert va.PREP_S == 25.0
    va.use_timing("measured", prep_s=40.0)                 # an explicit --prep-s still wins
    assert va.PREP_S == 40.0
    with pytest.raises(ValueError):
        va.use_timing("guessed")


def test_measured_caps_the_reach_at_175_s(monkeypatch):
    air = va.Air(1000.0, "b", "td", 5.0)                    # its clip starts on air at 990 (7 s play, 3 s lead-in)
    t = truth(monkeypatch, {"b": [air]})
    tally = va.Tally(route_on=True, route2=[(1165.0, "a", True)])
    assert va.route_shown([air], tally, t, 180)[0] == {air: False}     # up at 1168.2: 178.2 s late, inside 180
    va.use_timing("measured")
    assert va.route_shown([air], tally, t, 180)[0] == {}               # up at 1167.2: 177.2 s, past the measured 175 s reach


def test_measured_flips_fit_a_shorter_dead_ball(monkeypatch):
    air = va.Air(1000.0, "b", "td", 5.0)
    t = truth(monkeypatch, {"b": [air]}, dead=(True, False, 18.0))    # 18 s: the play's 11 s fits with 2.2 s flips, not 3.2 s ones
    tally = va.Tally(route_on=True, route2=[(1040.0, "a", True)])
    assert va.route_shown([air], tally, t, 180)[1]["pieces"] == 0
    va.use_timing("measured")
    assert va.route_shown([air], tally, t, 180)[1]["pieces"] == 1


def test_floor_sizes_pieces_with_what_the_live_code_could_know(monkeypatch):
    air = va.Air(1000.0, "b", "td", 5.0)
    t = truth(monkeypatch, {"b": [air]})                               # really 100 s of dead time left
    monkeypatch.setattr(va, "floor_left", lambda rep, st: 20.0)       # but only 20 s were sure in advance
    tally = va.Tally(route_on=True, route2=[(1040.0, "a", True)])
    shown, stats = va.route_shown([air], tally, t, 180)
    assert shown == {air: False} and stats["pieces"] == 1             # hindsight: the whole clip in one go
    va.use_timing(dead="floor")
    shown, stats = va.route_shown([air], tally, t, 180)
    assert shown == {} and stats["pieces"] == 1 and stats["floor_over_truth"] == 0     # floor: only the play's piece fits


def test_a_floor_above_the_truth_is_clipped_and_counted(monkeypatch):
    air = va.Air(1000.0, "b", "td", 5.0)
    t = truth(monkeypatch, {"b": [air]}, dead=(True, False, 25.0))
    monkeypatch.setattr(va, "floor_left", lambda rep, st: 60.0)
    va.use_timing(dead="floor")
    shown, stats = va.route_shown([air], va.Tally(route_on=True, route2=[(1040.0, "a", True)]), t, 180)
    assert stats["floor_over_truth"] == 1 and stats["pieces"] == 1 and shown == {}   # sized by the real 25 s, never more


def test_floor_left_reads_the_stoppage_that_began_the_dead_ball():
    rush, timeout = {"type": {"text": "Rush"}, "text": "run for 3"}, {"type": {"text": "Timeout"}, "text": "Timeout #1 by WSH"}
    rep = NS(league=League.NFL, wall=[100.0, 110.0, 200.0], plays=[rush, timeout, rush])
    assert va.floor_left(rep, 120.0) == 25.0           # NFL timeout q10 35 s, 10 s of it already ran
    assert va.floor_left(rep, 160.0) == 0.0            # past the floor: nothing is sure any more
    assert va.floor_left(rep, 105.0) == 0.0            # after a plain play: no measured kind, no guarantee
    assert va.floor_left(rep, 50.0) == 0.0 and va.floor_left(rep, 300.0) == 0.0     # off the air


def _row():
    row = va.new_row()
    row["league"], row["brain"], row["card"] = "nfl", "hindsight", {"entries_mid_play": 0, "entries": 0, "cuts_high": 0, "mid_play_leaves": 0}
    row["refused"]["gate:leave"], row["lost"]["gate:leave"] = 3, 5.0
    return json.loads(json.dumps(row))


def _main(monkeypatch, tmp_path, capsys, *args):
    jobs = []
    monkeypatch.setattr(va, "run_one", lambda job: jobs.append(job) or _row())
    out = tmp_path / "out.json"
    monkeypatch.setattr(sys, "argv", ["veto_audit.py", "--leagues", "nfl", "--brains", "hindsight", "--workers", "1", "--json", str(out), *args])
    assert va.main() == 0
    return capsys.readouterr().out, out.read_text(), jobs


def test_the_defaults_print_and_write_what_they_did(monkeypatch, tmp_path, capsys):
    text, written, jobs = _main(monkeypatch, tmp_path, capsys)
    rows = [_row()]
    assert text == va.table(rows) + "\n"
    assert written == json.dumps({"sets": {}, "rows": rows, "rules": va.merge(rows), "avoidable_lost": {str(r): va.avoidable(rows, r) for r in va.REACHES}},
                                 indent=1)
    assert jobs == [("nfl", "hindsight", {}, None, 15.0)]
    for flags in (("--timing", "assumed", "--dead", "hindsight"), ("--prep-s", "15")):
        assert _main(monkeypatch, tmp_path, capsys, *flags)[:2] == (text, written)


def test_measured_and_floor_ride_on_the_job_and_are_said(monkeypatch, tmp_path, capsys):
    text, written, jobs = _main(monkeypatch, tmp_path, capsys, "--timing", "measured", "--dead", "floor")
    assert jobs == [("nfl", "hindsight", {"timing.measured": True}, None, 19.0, "measured", "floor")]
    assert "timing measured: flip 2.2 s, clip load 19 s, reach capped at 175 s" in text and "dead floor:" in text
    out = json.loads(written)
    assert (out["timing"], out["dead"], out["prep_s"], out["sets"]) == ("measured", "floor", 19.0, {"timing.measured": True})


def test_a_worker_takes_the_knobs_from_its_job(monkeypatch):
    monkeypatch.setattr(va, "replay", lambda *a: (_ for _ in ()).throw(RuntimeError("stop here")))
    with pytest.raises(RuntimeError):
        va.run_one(("nba", "hindsight", {}, 1, 19.0, "measured", "floor"))
    assert (va.TIMING, va.DEAD, va.FLIP_S, va.PREP_S, va.REACH_CAP_S) == ("measured", "floor", 2.2, 19.0, 175.0)
