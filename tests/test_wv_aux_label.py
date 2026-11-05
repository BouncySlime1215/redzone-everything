"""Shared aux-label harness (EXP-1704 / EXP-1800) and Data G attention labels: offline checks."""
import gzip
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import attention_labels as AL  # noqa: E402
import wv_aux_label as H  # noqa: E402


def test_rank01_ties_nan_and_single():
    r = H.rank01(np.array([3.0, np.nan, 1.0, 3.0]))
    assert np.isnan(r[1]) and r[2] == 0.0 and r[0] == r[3] == 0.75
    assert H.rank01(np.array([np.nan, 2.0]))[1] == 0.5


def test_placebo_keeps_values_and_gaps_within_game():
    a = np.array([0.1, np.nan, 0.5, 0.9, np.nan])
    p = H.placebo(a, 3)
    assert np.isnan(p[1]) and np.isnan(p[4])
    assert sorted(p[np.isfinite(p)]) == sorted(a[np.isfinite(a)])


def test_mix_leaves_uncovered_plays_at_wv():
    y = H.mix(np.array([0.2, 0.4]), np.array([1.0, np.nan]), 0.3)
    assert np.allclose(y, [0.2 * 0.7 + 0.3, 0.4])


def test_arm_targets_shapes_and_range_guard():
    games = [([{"wall": i} for i in range(4)], np.array([0.0, 0.1, 0.2, 0.3]))]
    out = H.arm_targets(games, lambda rows: np.array([0.0, 1.0, np.nan, 0.5]), 0.3, seed=0,
                        derived_fn=lambda rows: np.full(len(rows), 0.5))
    assert set(out) == {"A0", "A1", "A2", "A3", "aux", "coverage"}
    assert out["coverage"].tolist() == [1, 1, 0, 1] and np.allclose(out["A0"], games[0][1])
    try:
        H.arm_targets(games, lambda rows: np.full(len(rows), 7.0), 0.3, 0)
        raise AssertionError("out-of-range aux must be refused")
    except ValueError:
        pass


def test_play_attention_windows_trail_the_play():
    s = AL.Series("bsky")
    for t, z in [(1000, 0.0), (1060, 5.0), (1600, 9.0)]:
        s.add("LAL", t, z)
    s.finish()
    rows = [{"wall": 1000.0}, {"wall": 1400.0}, {"wall": 5000.0}]
    a = AL.play_attention(rows, ("LAL", "SAC"), [s])
    assert a[0] == 5.0          # 1060 within (1000, 1300]; 1000 itself is the same moment, excluded
    assert a[1] == 9.0          # 1600 within (1400, 1700]
    assert np.isnan(a[2])       # nothing after: uncovered, not "nobody cared"


def test_load_bsky_normalizes_by_traffic(tmp_path):
    rows = [{"minute_utc": f"2025-11-05T00:{m:02d}Z", "posts_total": 1000, "terms": {"lakers": 1}} for m in range(10)]
    rows[5]["terms"]["lakers"] = 40
    p = tmp_path / "d.jsonl.gz"
    with gzip.open(p, "wt") as f:
        f.write("\n".join(json.dumps(r) for r in rows))
    s = AL.load_bsky([p], {"lakers": ["LAL"]})
    assert int(np.argmax(s.z["LAL"])) == 5 and max(s.z["LAL"]) > 3


def test_drama_uses_train_table_and_realized_surprise():
    g = [{"wp_home": 0.5 + 0.01 * (i % 3), "left_frac": 1 - i / 60} for i in range(60)]
    table = AL.fit_suspense([g])
    d = AL.drama_label(g, table)
    assert d.shape == (60,) and np.nanmax(d) <= 1 and np.nanmin(d) >= 0
