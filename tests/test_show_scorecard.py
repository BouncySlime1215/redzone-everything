"""WP-20: the info-only show scorecard. Dev nights only, no frozen grader imported, one table, no verdict.

Why: the scorecard must never read a locked night (CLAUDE.md release gate) and must stay info-only, so the refusal,
the frozen-file rule and the counting are what is under test (build plan WP-20).
"""
import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from redzone.moments.detect import Moment
from scripts import show_scorecard as ss
from tests.test_coverage_audit import make_archive

ROOT = Path(__file__).resolve().parent.parent
P = ss.bjs.MR()


def item(gid, land, leave, shown=1):
    return SimpleNamespace(gid=gid, land=land, leave=leave, shown=shown)


def test_split_test_and_all_are_refused(arch_dir, capsys):
    assert ss.main(["--archive", str(arch_dir), "--split", "test"]) == 2
    assert ss.main(["--archive", str(arch_dir), "--split", "all"]) == 2
    assert "locked" in capsys.readouterr().err


@pytest.fixture
def arch_dir(tmp_path):
    make_archive(tmp_path, "nfl")
    return tmp_path


def test_locked_night_named_in_nights_is_refused_and_never_opened(arch_dir):
    with pytest.raises(ss.LockedNightError):
        ss.score_league("nfl", arch_dir, P, 7, 3, 0, ["20261108"])
    assert ss.main(["--archive", str(arch_dir), "--leagues", "nfl", "--nights", "20261107"]) == 2


def test_league_with_no_archive_is_refused_whole(tmp_path):
    assert ss.main(["--archive", str(tmp_path), "--leagues", "nba"]) == 2


def test_default_split_is_fit_and_locked_summaries_are_never_opened(arch_dir):
    for gid in ("nfl6", "nfl7"):      # the two locked nights' games: opening either would raise
        (arch_dir / "nfl" / f"summary_{gid}.json").write_text("{ locked file: opening this would raise")
    assert ss.score_league("nfl", arch_dir, P, 7, 3, 0) == {"league": "nfl", "slates": 0}
    assert ss.main(["--archive", str(arch_dir), "--leagues", "nfl"]) == 0   # no --split: fit, and it prints a table


def test_seen_counts_marks_moments_inside_aired_clips_only():
    ms = [Moment(100.0, "g", 1, "td"), Moment(500.0, "g", 1, "td"), Moment(110.0, "g", 3, "gain10"),
          Moment(120.0, "g", 2, "rz_trip"), Moment(130.0, "other", 1, "td"), Moment(9000.0, "g", 1, "td")]
    items = {"g": [item("g", 90, 140), item("g", 480, 520, shown=0)]}
    c = ss.seen_counts("nfl", ms, items, 0.0, 1000.0)
    # in window and a used game: td@100 (seen), td@500 (clip never aired), gain10@110, rz_trip@120. 'other' and 9000 drop out.
    assert c["must"] == [2, 3]            # td, td, gain10 (football must-show is wider than tier 1)
    assert c["setup"] == [1, 1]
    assert c["highlight"] == [1, 1]
    assert c["by_kind"]["td"] == [1, 2]


def test_football_must_show_is_wider_than_tier_one_and_hoops_is_not():
    ms = [Moment(10.0, "g", 3, "gain10"), Moment(10.0, "g", 3, "dunk")]
    items = {"g": [item("g", 0, 20)]}
    assert ss.seen_counts("nfl", ms[:1], items, 0, 100)["must"] == [1, 1]
    assert ss.seen_counts("nba", ms[1:], items, 0, 100)["must"] == [0, 0]


def test_band_follows_the_planner_table():
    assert ss.band("nfl", 4) == (6, 10) and ss.band("cfb", 12) == (10, 14) and ss.band("nba", 3) == (2, 4)
    assert ss.band("nba", 1) is None and ss.band("mlb", 4) is None


def test_capture_items_restores_the_sim():
    real = ss.bjs.mr_items
    with ss.capture_items():
        assert ss.bjs.mr_items is not real
    assert ss.bjs.mr_items is real


def test_render_says_it_is_a_replay_and_gives_no_verdict():
    row = {"league": "nfl", "slates": 1, "games_live": 4, "must_seen_pct": 50.0, "must_n": 2, "setup_seen_pct": None,
           "setup_n": 0, "highlight_seen_pct": 0.0, "highlight_n": 1, "cuts_per_10": 7.0, "band": (6, 10),
           "mid_play_joins": 0, "mid_play_leaves": 0, "ad_s_per_10": 0.0, "by_kind": {}}
    text = ss.render([row])
    assert "SIMULATED SHOW" in text and "not the real TV" in text and "not a release gate" in text
    assert "PASS" not in text and "FAIL" not in text
    assert "no slates" in ss.render([{"league": "nba", "slates": 0}])


def test_imports_no_frozen_grader_file_directly():
    frozen = {Path(f).with_suffix("").as_posix().replace("/", ".") for f in json.loads((ROOT / "docs/grader_freeze.json").read_text())["files"]}
    tree = ast.parse((ROOT / "scripts" / "show_scorecard.py").read_text())
    names = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            names |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            names.add(n.module)
            names |= {f"{n.module}.{a.name}" for a in n.names}
    assert not names & frozen, names & frozen
