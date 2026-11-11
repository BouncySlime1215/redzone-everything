"""[timing.dead_v2] Dead time left by break class and elapsed time (redzone/scoring/dead_left.py v2), on the real table.

Why: the pooled quantile less the elapsed seconds allowed a clip in 0 of 891 NFL timeouts at the 99% level; the class and the
survivors' remaining time allowed 598 (research ledger L1, simulated on recorded nights). These pin the table's key cells, the
shift between landmarks, every fallback, and that no promise exceeds what training ever saw.
"""
import json
import random

import pytest

from redzone.models import League, Stoppage
from redzone.scoring import dead_left as D
from redzone.scoring.dead_left import cell_v2, conformal_lower, guaranteed_left_v2, guaranteed_left_v2_within, stoppage_class

FILE = json.loads(D.TABLE_V2_FILE.read_text())
TABLE = FILE["table"]


# ----- the class, from the feed's own words -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("type_text,text,cls", [
    ("Official Timeout", "Official Timeout", "official"),
    ("Official Timeout", "", "official"),
    ("Timeout", "Official Timeout at 02:00.", "official"),
    ("Timeout", "TV Timeout", "official"),
    ("Timeout", "Media timeout", "official"),
    ("Timeout", "Timeout #2 by WSH at 01:58.", "team"),
    ("Timeout", "Timeout by DAL.", "team"),
    ("Timeout", "Timeout at 07:12.", "other"),
    ("Timeout", "", "other"),
])
def test_a_football_timeout_is_official_team_or_other(type_text, text, cls):
    assert stoppage_class("nfl", "timeout", type_text, text) == cls
    assert stoppage_class(League.CFB, Stoppage.TIMEOUT, type_text, text) == cls


def test_every_other_kind_and_league_is_its_own_class():
    assert stoppage_class("nba", "timeout", "Full Timeout", "Lakers Full timeout") == "timeout"
    assert stoppage_class("nhl", "timeout", "Stoppage", "TV timeout") == "timeout"
    assert stoppage_class("nfl", "review", "Timeout", "Official Timeout") == "review"
    assert stoppage_class("nfl", Stoppage.TWO_MINUTE_WARNING, "Two-minute warning", "Two-Minute Warning") == "two_minute_warning"
    assert stoppage_class("nfl", "timeout") == "timeout"                       # no timeout type on the play: nothing to read
    assert stoppage_class("nfl", "timeout", "Pass Reception", "Official ruling stands") == "timeout"


# ----- the conformal rank ----------------------------------------------------------------------------------------------------------

def test_the_bound_is_the_floor_alpha_n_plus_1_th_smallest():
    v = list(range(1, 200))                                                      # n = 199
    assert conformal_lower(v, 0.01) == 2 and conformal_lower(v, 0.10) == 20      # floor(0.01 x 200) = 2, floor(0.10 x 200) = 20
    assert conformal_lower(v[:99], 0.01) == 1 and conformal_lower(v[:98], 0.01) is None
    assert conformal_lower(v[:9], 0.10) == 1 and conformal_lower(v[:8], 0.10) is None
    assert conformal_lower([5.0, 1.0, 3.0] * 10, 0.10) == 1.0 and conformal_lower([], 0.10) is None


# ----- real table values -----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("cls,at0,at30,at60", [("official", 29.0, 13.0, 11.0), ("team", 12.0, 1.0, 1.0), (None, 16.0, 2.0, 3.0)])
def test_nfl_timeouts_at_the_99_percent_level(cls, at0, at30, at60):
    assert [guaranteed_left_v2("nfl", "timeout", t, 0.01, cls) for t in (0, 30, 60)] == [at0, at30, at60]


def test_the_classes_are_the_ledgers_two_kinds_of_timeout():
    off, team = TABLE["nfl"]["timeout"]["official"]["0"], TABLE["nfl"]["timeout"]["team"]["0"]
    assert (off["n"], off["left_s"]["0.10"]) == (2274, 110.0) and (team["n"], team["left_s"]["0.10"]) == (1200, 26.0)
    assert guaranteed_left_v2("nfl", "timeout", 0, 0.10, "official") == 110.0
    assert D.guaranteed_left("nfl", "timeout", 0.0) == 35.0                     # v1, the pooled q10, is untouched


def test_an_nba_timeout_has_no_room_at_the_start_and_room_30_s_in():
    assert guaranteed_left_v2("nba", "timeout", 0, 0.01) == 3.0
    assert guaranteed_left_v2(League.NBA, Stoppage.TIMEOUT, 30, 0.01) == 54.0
    assert TABLE["nba"]["timeout"]["timeout"]["30"]["n"] == 549


# ----- between landmarks -----------------------------------------------------------------------------------------------------------

def test_between_landmarks_the_time_since_the_landmark_comes_off():
    assert guaranteed_left_v2("nfl", "timeout", 35, 0.01, "official") == pytest.approx(13.0 - 5.0)
    assert guaranteed_left_v2("nba", "timeout", 36, 0.01) == pytest.approx(54.0 - 6.0)
    assert guaranteed_left_v2("nba", "timeout", 29.9, 0.01) == pytest.approx(29.0 - 9.9)     # still the 20 s landmark
    assert guaranteed_left_v2("nfl", "timeout", 25, 0.01, "team") == 0.0                      # 2 - 5: never below 0
    assert guaranteed_left_v2("nfl", "timeout", 150, 0.01, "official") == 0.0                 # past the last landmark: 2 - 30
    assert guaranteed_left_v2("nhl", "timeout", 130, 0.01) == pytest.approx(11.0 - 10.0)
    assert guaranteed_left_v2("nba", "timeout", -5, 0.01) == 3.0                              # a negative elapsed reads as 0


def test_over_a_window_of_elapsed_times_the_least_bound_counts():
    assert guaranteed_left_v2_within("nba", "timeout", 30, 30, 0.01) == 54.0
    assert guaranteed_left_v2_within("nba", "timeout", 30, 42, 0.01) == pytest.approx(54.0 - 12.0)
    assert guaranteed_left_v2_within("nba", "timeout", 24, 36, 0.01) == pytest.approx(29.0 - 10.0)   # it may still be under 30 s
    assert guaranteed_left_v2_within("nba", "timeout", 0, 60, 0.01) == 0.0
    assert guaranteed_left_v2_within("nba", "timeout", 40, 30, 0.01) == 54.0                         # an empty window is its end
    rnd = random.Random(3)
    for _ in range(300):
        lo = rnd.uniform(0, 140)
        hi = lo + rnd.uniform(0, 40)
        least = guaranteed_left_v2_within("nfl", "timeout", lo, hi, 0.05, "official")
        grid = [lo + (hi - lo) * i / 400 for i in range(401)]
        assert least <= min(guaranteed_left_v2("nfl", "timeout", e, 0.05, "official") for e in grid) + 1e-9


# ----- fallbacks -------------------------------------------------------------------------------------------------------------------

def test_a_class_without_enough_survivors_reads_the_pooled_class():
    assert TABLE["nfl"]["timeout"]["other"]["0"]["n"] == 1                       # one train timeout was neither kind
    assert guaranteed_left_v2("nfl", "timeout", 0, 0.01, "other") == 16.0 == guaranteed_left_v2("nfl", "timeout", 0, 0.01)
    assert TABLE["nfl"]["timeout"]["team"]["90"]["left_s"] == {"0.10": 4.0, "0.05": 2.0, "0.02": 1.0, "0.01": None}   # n = 93
    assert cell_v2("nfl", "timeout", 90, 0.01, "team")[0] == "timeout" and cell_v2("nfl", "timeout", 90, 0.02, "team")[0] == "team"
    assert TABLE["nfl"]["timeout"]["timeout"]["90"]["left_s"]["0.01"] == 5.0     # ...so the 99% level reads the pooled 90 s cell
    assert guaranteed_left_v2("nfl", "timeout", 90, 0.02, "team") == 1.0         # ...and the 98% level still reads its own
    # [DEAD-V2] the pooled 5 s at 99% would promise more than the team class's own 1 s at 98%: a stricter level never promises
    # more, so the 99% answer is capped by every looser level's (anchor-mode returned 5.0 here)
    assert guaranteed_left_v2("nfl", "timeout", 90, 0.01, "team") == 1.0
    assert guaranteed_left_v2("nfl", "timeout", 0, 0.01, "no such class") == 16.0


def test_nothing_that_qualifies_guarantees_nothing():
    assert TABLE["nhl"]["end_period"]["end_period"]["0"]["n"] == 19
    assert guaranteed_left_v2("nhl", "end_period", 0, 0.01) == 0.0               # 19 survivors cannot carry a 99% promise
    assert guaranteed_left_v2("nhl", "end_period", 0, 0.05) > 0.0                # ...but they carry a 95% one (floor(0.05 x 20) = 1)
    assert guaranteed_left_v2("nfl", "none", 0, 0.01) == 0.0 and guaranteed_left_v2("mlb", "timeout", 0, 0.10) == 0.0
    assert guaranteed_left_v2("nfl", "timeout", 0, 0.005, "official") == 0.0     # stricter than every stored level
    assert cell_v2("nfl", "timeout", 0, 0.005, "official") is None and cell_v2("mlb", "timeout", 0, 0.10) is None


def test_a_level_between_two_stored_ones_reads_the_stricter_and_a_college_league_its_pro_table():
    assert guaranteed_left_v2("nfl", "timeout", 0, 0.03, "official") == 41.0     # the 0.02 cell, never the looser 0.05 one (70)
    assert guaranteed_left_v2("nfl", "timeout", 0, 0.5, "official") == 110.0
    assert guaranteed_left_v2("cfb", "timeout", 0, 0.01, "official") == 29.0 and guaranteed_left_v2(League.CBB, "timeout", 30, 0.01) == 54.0


@pytest.mark.parametrize("alpha", [0.0, 1.0, -0.1, 1.5])
def test_a_risk_level_is_strictly_between_0_and_1(alpha):
    with pytest.raises(ValueError):
        guaranteed_left_v2("nfl", "timeout", 0.0, alpha)


# ----- the property: never more than training ever saw -----------------------------------------------------------------------------

def _cells():
    return [(lg, kind, cls) for lg, kinds in TABLE.items() for kind, classes in kinds.items() for cls in classes]


def test_no_promise_exceeds_the_largest_remaining_time_seen_in_training():
    rnd = random.Random(7)
    checked = 0
    for lg, kind, cls in _cells():
        for alpha in (*D.ALPHAS, 0.03, 0.07, 0.25, 0.004):
            for e in (*D.LANDMARKS, *(rnd.uniform(0, 200) for _ in range(40))):
                got = guaranteed_left_v2(lg, kind, e, alpha, cls)
                mark = max(t for t in D.LANDMARKS if t <= e)
                read = cell_v2(lg, kind, mark, alpha, cls)
                assert got >= 0.0
                if read is None:
                    assert got == 0.0
                    continue
                assert read[0] in (cls, kind)
                assert got <= read[1]["max_s"] - (e - mark) + 1e-9 or got == 0.0   # the cell's largest remaining time, carried to e
                assert got <= read[1]["max_s"]
                assert got <= guaranteed_left_v2(lg, kind, mark, alpha, cls)
                checked += 1
    assert checked > 5000


# ----- the file holds derived numbers only -----------------------------------------------------------------------------------------

def test_the_table_file_holds_only_counts_and_seconds():
    assert set(FILE) == {"source", "license", "definition", "landmarks", "alphas", "train_nights", "table"}
    assert tuple(FILE["landmarks"]) == D.LANDMARKS and FILE["alphas"] == [f"{a:.2f}" for a in D.ALPHAS]
    assert "train nights only" in FILE["source"] and set(TABLE) == {"nfl", "nba", "nhl"} == set(FILE["train_nights"])
    for lg, kind, cls in _cells():
        marks = TABLE[lg][kind][cls]
        assert set(marks) <= {str(t) for t in D.LANDMARKS}
        ns = [marks[str(t)]["n"] for t in D.LANDMARKS if str(t) in marks]
        assert ns == sorted(ns, reverse=True)                                    # fewer stoppages outlast a later landmark
        for cell in marks.values():
            assert set(cell) == {"n", "max_s", "left_s"} and set(cell["left_s"]) == set(FILE["alphas"])
            bounds = [cell["left_s"][a] for a in FILE["alphas"]]                 # loosest level first
            known = [b for b in bounds if b is not None]
            assert known == sorted(known, reverse=True) and all(0.0 < b <= cell["max_s"] for b in known)
            assert [b is None for b in bounds] == sorted(b is None for b in bounds)   # a stricter level needs more survivors
    assert set(TABLE["nfl"]["timeout"]) == {"timeout", "official", "team", "other"}
    assert all(set(classes) == {kind} for lg in ("nba", "nhl") for kind, classes in TABLE[lg].items())


# ----- [DEAD-V2] the clean reader (dead_left_lite.guaranteed_left) ------------------------------------------------------------------

def test_the_lite_reader_is_the_same_answer_by_class_alone():
    from redzone.scoring import dead_left_lite as L
    assert L.guaranteed_left("nba", "timeout", 30.0, 0.01) == 54.0
    assert L.guaranteed_left("nfl", "official", 0.0, 0.01) == 29.0 == guaranteed_left_v2("nfl", "timeout", 0, 0.01, "official")
    assert L.guaranteed_left(League.NFL, "team", 0.0, 0.01) == 12.0 and L.kind_of("team") == "timeout"
    assert L.guaranteed_left("nfl", "no such class", 0.0, 0.01) == 0.0           # no cell, no kind to pool on: 0
    assert L.guaranteed_left("nfl", "no such class", 0.0, 0.01, kind="timeout") == 16.0
    assert L.guaranteed_left("mlb", "timeout", 0.0, 0.10) == 0.0 and L.guaranteed_left("nhl", "end_period", 0.0, 0.01) == 0.0
    with pytest.raises(ValueError):
        L.guaranteed_left("nba", "timeout", 0.0, 1.0)


def test_the_lite_reader_imports_nothing_from_the_app():
    """tools/park_live loads it by file path beside its own redzone copy: it must stand alone (standard library only)."""
    import ast
    from redzone.scoring import dead_left_lite as L
    tree = ast.parse(open(L.__file__).read())
    mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    mods |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert mods <= {"__future__", "json", "functools", "pathlib"}


def test_never_more_at_a_stricter_level_and_never_more_than_the_cell_read():
    from redzone.scoring import dead_left_lite as L
    rnd = random.Random(11)
    alphas = sorted({*D.ALPHAS, 0.004, 0.015, 0.03, 0.07, 0.2, 0.5})
    checked = 0
    for lg, kind, cls in [*_cells(), ("cfb", "timeout", "official"), ("cbb", "timeout", "timeout")]:
        for e in (*D.LANDMARKS, *(rnd.uniform(0, 200) for _ in range(25))):
            got = [L.guaranteed_left(lg, cls, e, a, kind=kind) for a in alphas]
            assert got == sorted(got), (lg, kind, cls, e, got)                 # monotone in alpha
            mark = max(t for t in D.LANDMARKS if t <= e)
            for a, g in zip(alphas, got):
                lvl = L.level(a)
                read = None if lvl is None else L.cell(lg, cls, mark, lvl, kind)
                cap = 0.0 if read is None else max(0.0, read[1]["left_s"][lvl] - (e - mark))
                assert 0.0 <= g <= cap + 1e-9                                  # never more than the table supports
                checked += 1
    assert checked > 3000
