"""[NBA-YES, audit] The veto audit's --landings tables: what a refused landing would really have shown, and the wait to the next legal one.

Why: basketball barely stops, so before any landing rule is loosened we need the floor Rule #1 itself sets: how long, from the moment
the brain first wants a game, until that game's picture may come up at all (a dead ball, or 8 s after a made basket). No product behaviour.
"""
from types import SimpleNamespace as NS

from redzone.lab.backtest import Replay
from redzone.models import League
from scripts import veto_audit as va
from tests.test_cut_grade import _p
from tests.test_yes_audit import _main, _row

LAG, TUNE = 30.0, 5.0


def _truth():
    """One NBA game. A made shot at 1000, a missed one at 1040, a foul at 1060 with two free throws (1075, 1085), a timeout at 1120,
    then play from 1200. The clock runs between the shots; it stands still from the foul to the second free throw and in the timeout."""
    rows = [(900.0, _p("Substitution", "5:05")),
            (1000.0, _p("Jump Shot", "5:00", shootingPlay=True, scoringPlay=True)),
            (1040.0, _p("Jump Shot", "4:30", shootingPlay=True)),
            (1060.0, _p("Shooting Foul", "4:20")),
            (1075.0, _p("Free Throw - 1 of 2", "4:20", shootingPlay=True, scoringPlay=True)),
            (1085.0, _p("Free Throw - 2 of 2", "4:20", shootingPlay=True, scoringPlay=True)),
            (1110.0, _p("Jump Shot", "4:00", shootingPlay=True)),
            (1120.0, _p("Full Timeout", "3:55")),
            (1200.0, _p("Jump Shot", "3:45", shootingPlay=True)),
            (1300.0, _p("Jump Shot", "3:00", shootingPlay=True))]
    t = va.Truth()
    t.bind([Replay(League.NBA, "n1", {}, [p for _, p in rows], [w for w, _ in rows], "AAA @ BBB")], LAG, TUNE)
    return t


def _now(st):
    """The tick whose cut comes up at screen time st."""
    return st - TUNE + LAG


def test_the_picture_is_named_by_the_graders_facts():
    t = _truth()
    said = {st: t.doing("n1", _now(st)) for st in (1002.0, 1006.0, 1010.0, 1050.0, 1065.0, 1080.0, 1087.0, 1095.0, 1130.0, 1250.0)}
    assert said == {1002.0: "made basket, up to 4 s after", 1006.0: "made basket, 4 to 8 s after (clean since grader v49)",
                    1010.0: "live possession", 1050.0: "live possession", 1065.0: "whistle (foul, violation, out of bounds)",
                    1080.0: "free throws", 1087.0: "last free throw, up to 4 s after", 1095.0: "live possession",
                    1130.0: "timeout", 1250.0: "live possession"}
    assert t.doing("n1", _now(800.0)) == t.doing("n1", _now(1400.0)) == "not started or ended" and t.doing("zz", 0.0) == "unknown game"


def test_a_landing_is_legal_on_a_dead_ball_or_in_the_made_basket_window():
    t = _truth()
    assert t.landing_ok("n1", 1002.0) == (True, True) and t.landing_ok("n1", 1006.0) == (False, True)       # 4 s dead, 8 s clean
    assert t.landing_ok("n1", 1010.0) == t.landing_ok("n1", 1045.0) == (False, False)                       # a miss opens nothing
    assert t.landing_ok("n1", 1065.0) == (True, True) and t.landing_ok("zz", 1065.0) == (False, False)


def test_the_wait_runs_to_the_next_legal_landing_under_each_test():
    t = _truth()
    assert t.landing_wait("n1", _now(1002.0)) == t.landing_wait("n1", _now(1002.0), bucket=False) == 0.0
    assert t.landing_wait("n1", _now(1006.0)) == 0.0 and t.landing_wait("n1", _now(1006.0), bucket=False) == 54.0   # the foul at 1060
    assert t.landing_wait("n1", _now(1010.0)) == 50.0 and t.landing_wait("n1", _now(1095.0)) == 25.0               # the timeout at 1120
    assert t.landing_wait("zz", 0.0) == va.LANDING_CAP_S


def test_the_wait_columns():
    assert [va.wait_column(w) for w in (0.0, 0.5, 5.0, 5.5, 15.0, 30.0, 60.0, 60.5, va.LANDING_CAP_S)] == \
        ["now", "<=5", "<=5", "<=15", "<=15", "<=30", "<=60", "later", "later"]


def test_a_want_is_new_unless_the_tick_before_wanted_the_same_cut():
    a = va.AuditDecider.__new__(va.AuditDecider)
    a.tally = va.Tally()
    cands = {"b": NS(scored=NS(high_leverage=False, crunch=True, crunch_now=False)), "c": NS(scored=NS(high_leverage=False, crunch=False, crunch_now=False))}
    for tick, pick in ((1, "b"), (2, "b"), (3, "c"), (5, "c"), (6, "c")):
        a.tally.ticks = tick
        a._want(pick, cands, 100.0 + tick)
    assert a.tally.wanted == 5 and a.tally.wants == [(101.0, "b"), (102.0, "b"), (103.0, "c"), (105.0, "c"), (106.0, "c")]
    assert a.tally.want_info == [(True, True), (False, True), (True, False), (True, False), (False, False)]    # tick 4 wanted nothing


def test_the_night_files_refusals_and_new_wants_by_the_truth():
    t = _truth()
    tally = va.Tally(wants=[(_now(1010.0), "n1"), (_now(1020.0), "n1"), (_now(1006.0), "n1")], want_info=[(True, False), (False, False), (True, True)],
                     log={"n1": [(_now(1010.0), "legal:no_landing", True, True, ""), (_now(1065.0), "legal:no_landing", True, False, ""),
                                 (_now(1050.0), "gate:leave", True, False, "")]})
    row = va.new_row()
    va.landing_rows(tally, t, row)
    assert row["landings"]["refused"] == {"live possession": {"<=60": 1}, "whistle (foul, violation, out of bounds)": {"now": 1}}
    assert row["landings"]["first"] == [["live possession", False, 50.0, 50.0],
                                        ["made basket, 4 to 8 s after (clean since grader v49)", True, 54.0, 0.0]]


def test_the_tables_print_only_when_asked_and_split_crunch_targets():
    assert va.landing_lines([_row()]) == [] and "next legal landing" not in va.table([_row()])
    row = _row()
    row["landings"] = {"refused": {"live possession": {"<=15": 3, "later": 1}, "timeout": {"now": 2}},
                       "first": [["live possession", False, 10.0, 10.0], ["live possession", True, 40.0, 2.0], ["timeout", True, 0.0, 0.0]]}
    lines = va.landing_lines([row])
    assert lines[3].split() == ["nfl/hindsight", "live", "possession", "4", "0", "0", "3", "0", "0", "1"]
    assert lines[4].split() == ["nfl/hindsight", "timeout", "2", "2", "0", "0", "0", "0", "0"]
    by = {" ".join(ln.split()[1:-10]): ln.split()[-10:] for ln in lines[7:]}
    assert by["dead ball only all"] == ["3", "10.0", "40.0", "40.0", "33%", "33%", "67%", "67%", "100%", "100%"]
    assert by["dead ball or made basket crunch"] == ["2", "0.0", "2.0", "2.0", "50%", "100%", "100%", "100%", "100%", "100%"]
    assert by["dead ball only not crunch"][:4] == ["1", "10.0", "10.0", "10.0"] and va.table([row]).splitlines()[-len(lines):] == lines


def test_the_option_rides_on_the_job_and_changes_no_default(monkeypatch, tmp_path, capsys):
    text, out, jobs = _main(monkeypatch, tmp_path, capsys, lambda job: _row(), "--landings", "--set", "gate.x=true")
    assert [j[2] for j in jobs] == [{"gate.x": True, va.LANDINGS_KEY: True}] and out["sets"] == {"gate.x": True}
    plain = _main(monkeypatch, tmp_path, capsys, lambda job: _row())
    assert plain[2] == [("nfl", "hindsight", {}, None, 15.0)] and plain[0] == va.table([_row()]) + "\n"
