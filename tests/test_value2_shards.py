"""The value2_next_3m shard label: off by default with byte-identical default shards, and leak-checked when on.

Why: docs/plans/2026-11-09-value-v2.md build step 2. Every existing shard, model and grade is on the v1 label, so
turning the builder's code path on must not move one default byte, and the new label must never be readable as a feature.
"""
import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from redzone.moments import value2
from redzone.noggin import value_shards as V
from scripts import value_leak_check as LC
from tests.test_gamevalue import nba_summary
from tests.test_value_shards import NIGHTS, _fails, _has, _shard, _weekly, nfl_summary

# Canonical hashes of the default rows, computed at e5562138 (the commit before value v2 existed).
BASE_NFL = "6ea9e22757e9a74e25a322222ae35d86c058bebfa5caf7d115fb0ea0e0a2e5de"
BASE_NBA = "f1a2a01f91467588bd6060f58ba4d0643c5f2cd10947ec7c41e452fa5c01462f"


def canon(df: pd.DataFrame) -> str:
    """A hash of the column list and every value to 4 decimals: the same on any platform for the same rows."""
    body = {c: ["" if (isinstance(x, float) and x != x) else (f"{x:.4f}" if isinstance(x, (float, np.floating)) else str(x))
                for x in df[c].tolist()] for c in df.columns}
    return hashlib.sha256(json.dumps([list(df.columns), body], sort_keys=True).encode()).hexdigest()


def _nfl(**kw):
    return V.build_rows("nfl", "g1", nfl_summary(), night="20231008", season=2023, source="nflverse_pbp", license_line="CC-BY", plan="A", **kw)


def _nba():
    return V.build_rows("nba", "h1", nba_summary(0.0, 3), night="20231008", season=2023, source="hoopr_espn_nba", license_line="x", plan="B")


@pytest.fixture
def v2_label():
    V.select_label("v2")
    yield
    V.select_label("v1")


def test_default_is_v1_and_the_default_rows_are_the_bytes_they_were_before_value_v2():
    assert V.LABEL_VERSION == "v1" and V.VALUE_LABELS == ("value_next_3m", "p_tier1_60s") == LC.LABELS
    assert V.VALUE2_LABEL not in V.VALUE_COLUMNS
    assert canon(_nfl()) == BASE_NFL and canon(_nba()) == BASE_NBA


def test_default_sidecar_and_manifest_say_nothing_about_a_label_version(tmp_path):
    meta = _shard(tmp_path, "2023", _nfl())
    assert "label_version" not in meta and "label_version" not in V.finalize(tmp_path)
    assert V.shard_done(tmp_path, "nfl", "2023")


def test_turning_the_label_on_adds_one_column_and_moves_nothing_else(v2_label):
    off_nfl, off_nba = BASE_NFL, BASE_NBA
    for rows, base in ((_nfl(), off_nfl), (_nba(), off_nba)):
        assert list(rows.columns)[-1] == V.VALUE2_LABEL == LC.LABELS_V2[-1] and V.VALUE_LABELS == LC.LABELS_V2
        assert V.VALUE2_LABEL not in V.VALUE_FEATURES
        assert canon(rows.drop(columns=[V.VALUE2_LABEL])) == base


def test_the_label_is_the_v2_value_strictly_after_the_decision(v2_label):
    s = nfl_summary()
    rows = _nfl()
    graded = value2.grade("nfl", V.strip_published_wp(s), "g1", fixed_stakes=1.0)
    assert graded and rows[V.VALUE2_LABEL].abs().sum() > 0
    for r in rows.iloc[::37].itertuples():
        want = sum(g.value for g in graded if r.decision_t < g.t <= r.decision_t + V.HORIZON_S)
        assert getattr(r, V.VALUE2_LABEL) == pytest.approx(want, abs=1e-4)
    # the fixture's touchdowns come at 7-3, 14-3, 21-3 and 28-3: same v1 points, falling v2 value
    tds = [g for g in graded if g.kind == "td_off"]
    assert [g.base for g in tds] == [5.0] * len(tds) and tds[0].value > tds[-1].value


def test_playoff_is_the_only_stake_in_the_label(v2_label):
    s = {**nfl_summary(), "pickcenter": [{"spread": -9.5, "homeTeamOdds": {"favorite": False}, "awayTeamOdds": {"favorite": True}}]}
    plain = V.build_rows("nfl", "g1", s, night="20231008", season=2023, source="archive", license_line="l", plan="A")
    assert plain[V.VALUE2_LABEL].tolist() == _nfl()[V.VALUE2_LABEL].tolist()          # a spread in the summary changes nothing
    post = _nfl(playoff=1)
    assert post[V.VALUE2_LABEL].to_numpy() == pytest.approx(_nfl()[V.VALUE2_LABEL].to_numpy() * 1.5, abs=1e-4)


def test_published_win_probability_never_reaches_the_v2_label(v2_label):
    s = nfl_summary()
    assert _nfl()[V.VALUE2_LABEL].tolist() == V.build_rows(
        "nfl", "g1", {k: v for k, v in s.items() if k != "winprobability"}, night="20231008", season=2023, source="nflverse_pbp",
        license_line="CC-BY", plan="A")[V.VALUE2_LABEL].tolist()


def test_prefix_check_still_passes_with_the_label_on(v2_label):
    for league, gid, s, rows in (("nfl", "g1", nfl_summary(), _nfl()), ("nba", "h1", nba_summary(0.0, 3), _nba())):
        n, bad = V.prefix_check(league, gid, s, rows)
        assert n == V.PREFIX_ROWS and bad == []


def test_a_v2_shard_is_not_a_finished_default_shard_and_the_other_way_round(tmp_path):
    _shard(tmp_path / "a", "2023", _nfl())
    V.select_label("v2")
    try:
        assert not V.shard_done(tmp_path / "a", "nfl", "2023")
        meta = _shard(tmp_path / "b", "2023", _nfl())
        assert meta["label_version"] == "v2" and V.shard_done(tmp_path / "b", "nfl", "2023")
    finally:
        V.select_label("v1")
    assert not V.shard_done(tmp_path / "b", "nfl", "2023")


def test_leak_check_passes_v2_shards_and_refuses_the_column_without_its_sidecar_flag(tmp_path, v2_label):
    out = tmp_path / "shards"
    for i in range(len(NIGHTS)):
        _shard(out, f"w{i}", _weekly(i))
    V.finalize(out)
    assert _fails(out) == []
    side = V.shard_paths(out, "nfl", "w0")[1]
    info = json.loads(side.read_text())
    del info["label_version"]                               # a v2 column smuggled into a shard that claims to be default
    side.write_text(json.dumps(info))
    assert _has(_fails(out), "columns")
    info["label_version"] = "v9"
    side.write_text(json.dumps(info))
    assert _has(_fails(out), "sidecar")


def test_leak_check_catches_a_feature_that_copies_the_v2_label(v2_label):
    rows = _nfl()
    assert len(rows) >= LC.MIN_ROWS_COPY and rows[V.VALUE2_LABEL].std() > 0
    rows["pts_5m"] = rows[V.VALUE2_LABEL] * 2.0 + 1.0
    seen = LC.Report()
    LC.check_label_copy(rows, "nfl/x", seen, LC.FEATURES, LC.LABELS_V2)
    assert any(f.startswith("label_copy:") and "pts_5m is a copy of label value2_next_3m" in f for f in seen.fails)
    blind = LC.Report()
    LC.check_label_copy(rows, "nfl/x", blind, LC.FEATURES, LC.LABELS)      # the v1 list alone would not have looked
    assert not any("value2_next_3m" in f for f in blind.fails)


def test_an_unknown_label_version_stops_the_import():
    with pytest.raises(ValueError, match="RZ_GV_LABEL"):
        V.select_label("v3")
    assert V.LABEL_VERSION == "v1"


def test_a_league_value_v2_does_not_grade_cannot_build_the_label(v2_label):
    with pytest.raises(KeyError):
        V.value2_label("nhl", {"plays": []}, "g", np.array([0.0]))
