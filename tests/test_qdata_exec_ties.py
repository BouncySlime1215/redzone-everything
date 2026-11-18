"""Executed-option accounting (A-02b) on hand-made rows: an option whose forced jump was not made never enters the
best / second / tie / decisive computation; a point with fewer than two executed options is no_choice and left out of
the tie-share denominator; the ties command prints both definitions side by side; A-02 rows are read through `done`.
"""
from __future__ import annotations

from redzone.lab.gym2 import qdata as Q
from scripts import qdata_build


def _o(k, q, made=True):
    o = {"k": k, "q": q}
    if k != "stay":
        o.update(made=made, exec=made, not_made_reason=None if made else "core_landing_long_wait")
    return o


def test_a_jump_not_made_never_enters_best_second_or_tie():
    legal = [_o("stay", 1.0), _o([1, 0, 5, 7], 1.0, made=False), _o([1, 3, 5, 9], 0.5)]
    f = Q.exec_flags(legal)
    assert f["n_exec"] == 2 and not f["no_choice"]
    assert f["exec_best"] == 0 and f["exec_gap"] == 0.5 and f["exec_decisive"] and not f["exec_tie"]
    assert Q.decisive_of([o["q"] for o in legal])["tie"]            # the A-02 definition sees a tie here
    legal = [_o("stay", 0.2), _o([1, 0, 5, 7], 9.0, made=False), _o([1, 3, 5, 9], 0.25)]
    f = Q.exec_flags(legal)
    assert f["exec_best"] == 2 and legal[f["exec_best"]]["exec"]    # the unmade best is never chosen


def test_fewer_than_two_executed_options_is_no_choice():
    f = Q.exec_flags([_o("stay", 1.0), _o([1, 0, 5, 7], 2.0, made=False)])
    assert f["no_choice"] and f["n_exec"] == 1 and f["exec_best"] == 0 and not f["exec_tie"] and not f["exec_decisive"]
    assert Q.exec_flags([])["no_choice"]


def _row(pad, opts):
    d = Q.decisive_of([o["q"] for o in opts])
    return {"pad": pad, "scenario": "league", "mix": "nba:2", "options": opts, "tie": d["tie"], "decisive": d["decisive"],
            "single": d["single"]}


ROWS = [
    _row(8, [_o("stay", 1.0), _o([1, 0, 5, 7], 1.0, made=False), _o([1, 3, 5, 9], 0.5)]),   # old tie, exec decisive
    _row(8, [_o("stay", 1.0), _o([1, 0, 5, 7], 1.0, made=False)]),                           # old tie, no_choice
    _row(8, [_o("stay", 1.0), _o([1, 0, 5, 7], 1.0)]),                                       # tie both ways
    _row(49, [_o("stay", 1.0)]),                                                             # single: no_choice
]


def test_the_executed_table_leaves_no_choice_out_of_the_denominator():
    t = Q.exec_tie_table(ROWS)
    a = t["by"]["all"]["all"]
    assert a["points"] == 4 and a["no_choice"] == 2 and a["no_choice_share"] == 0.5
    assert a["n"] == 2 and a["tie"] == 1 and a["decisive"] == 1 and a["tie_share"] == 0.5 and a["decisive_share"] == 0.5
    assert a["old_n"] == 3 and a["old_tie"] == 3 and a["old_tie_share"] == 1.0 and a["old_decisive_share"] == 0.0
    assert a["jumps"] == 4 and a["jumps_made"] == 2 and a["not_made_share"] == 0.5
    assert t["by"]["pad"]["49"]["n"] == 0 and t["by"]["pad"]["49"]["tie_share"] is None
    text = Q.exec_tie_table_text(t)
    assert "no_choice=0.500" in text and "old n=3" in text


def test_a02_rows_are_read_through_done():
    rows = [_row(8, [{"k": "stay", "q": 1.0}, {"k": [1, 0, 5, 7], "q": 1.0, "done": False},
                     {"k": [1, 3, 5, 9], "q": 0.5, "done": True}])]
    a = Q.exec_tie_table(rows)["by"]["all"]["all"]
    assert a["n"] == 1 and a["decisive"] == 1 and a["old_tie"] == 1


def test_the_ties_command_prints_both_definitions(tmp_path, capsys):
    rows = [dict(r, shard=0, night="20250301", slate=i, t=i, leagues=["nba", "nba"]) for i, r in enumerate(ROWS)]
    for rel, data in Q.shard_files(rows).items():
        Q.qd_atomic_write(tmp_path / rel, data)
    assert qdata_build.main(["ties", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "EXECUTED options only" in out and "no_choice=0.500" in out and "by pad:" in out
    assert qdata_build.main(["ties", str(tmp_path), "--json"]) == 0
    assert '"executed"' in capsys.readouterr().out
