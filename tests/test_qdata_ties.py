"""Decisive-Q dataset arithmetic (A-02): the decisive and tie flags on hand-made Q tables, and the tie-share table by
padding, scenario and mix on hand-made rows (the A1 prerequisite), with eps recomputed from the stored Q values.
"""
from __future__ import annotations

from redzone.lab.gym2 import qdata as Q
from scripts import qdata_build


def test_decisive_is_best_minus_second_at_least_a_tenth():
    d = Q.decisive_of([1.0, 0.9, 0.2])
    assert d["best"] == 0 and d["gap"] == 0.1 and d["decisive"] and not d["tie"] and not d["single"]
    d = Q.decisive_of([0.3, 1.25, 1.16])
    assert d["best"] == 1 and d["second"] == 1.16 and d["gap"] == 0.09 and not d["decisive"] and not d["tie"]
    d = Q.decisive_of([2.0, 2.0, 0.0])
    assert d["best"] == 0 and d["gap"] == 0.0 and d["tie"] and not d["decisive"]
    d = Q.decisive_of([5.0, 5.0000004])
    assert d["best"] == 1 and d["tie"]                                  # within eps 1e-6
    assert Q.decisive_of([3.0])["single"] and Q.decisive_of([])["single"]
    assert Q.decisive_of([1.0, 0.95], threshold=0.05)["decisive"]


def _row(pad, scen, mix, qs):
    d = Q.decisive_of(qs)
    return {"pad": pad, "scenario": scen, "mix": mix, "options": [{"q": q} for q in qs],
            "tie": d["tie"], "decisive": d["decisive"], "single": d["single"]}


ROWS = [
    _row(8, "league", "nba:2", [1.0, 1.0]),          # tie
    _row(8, "league", "nba:2", [1.0, 0.5]),          # decisive
    _row(8, "espn", "nba:3", [0.0, 0.0, 0.0]),       # tie
    _row(49, "espn", "nba:2", [0.4, 0.35]),          # neither
    _row(49, "espn", "nba:2", [0.7]),                # single: left out
    _row(3, "league", "nba:1+nfl:1", [2.0, 1.95, 1.0]),
]


def test_the_tie_table_by_padding_scenario_and_mix():
    t = Q.tie_table(ROWS)
    assert t["points"] == 6 and t["single"] == 1
    a = t["by"]["all"]["all"]
    assert a["n"] == 5 and a["tie"] == 2 and a["decisive"] == 1 and a["tie_share"] == 0.4 and a["decisive_share"] == 0.2
    assert t["by"]["pad"]["8"] == {"n": 3, "tie": 2, "decisive": 1, "options": 7, "tie_share": 0.6667,
                                    "decisive_share": 0.3333, "options_mean": 2.333}
    assert t["by"]["pad"]["49"]["n"] == 1 and t["by"]["pad"]["49"]["tie"] == 0
    assert t["by"]["scenario"]["espn"]["tie"] == 1 and t["by"]["scenario"]["league"]["n"] == 3
    assert t["by"]["mix"]["nba:1+nfl:1"]["decisive_share"] == 0.0
    loose = Q.tie_table(ROWS, eps=0.06)                 # recomputed from Q: 0.05 gaps now tie
    assert loose["by"]["all"]["all"]["tie"] == 4 and loose["eps"] == 0.06
    text = Q.tie_table_text(t)
    assert "by pad:" in text and "tie=0.667" in text


def test_the_mix_label_is_the_census_spelling():
    assert Q.mix_label(["nfl", "nba", "nba"]) == "nba:2+nfl:1"


def test_the_ties_command_reads_shards(tmp_path, capsys):
    rows = [dict(r, shard=0, night="20250301", slate=i, t=i, leagues=["nba", "nba"]) for i, r in enumerate(ROWS)]
    for rel, data in Q.shard_files(rows).items():
        Q.qd_atomic_write(tmp_path / rel, data)
    assert qdata_build.main(["ties", str(tmp_path)]) == 0
    assert "n=5" in capsys.readouterr().out
