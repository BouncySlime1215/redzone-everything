"""Win-probability swing labels: time left, Brownian WP, surprise and suspense, no scipy."""
import json
import math
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")
pytest.importorskip("pyarrow")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from pbp import wp_swings as W  # noqa: E402


def test_normal_functions_match_math():
    z = np.array([-4.0, -1.0, 0.0, 0.7, 3.0])
    assert np.allclose(W._Phi(z), [0.5 * math.erfc(-x / math.sqrt(2)) for x in z], atol=2e-7)
    assert np.allclose(W._ppf(W._Phi(z)), z, atol=1e-4)


def test_time_left_and_overtime():
    assert W.nba_tau([1, 4, 5], [720.0, 30.0, 120.0]).tolist() == [2880.0, 30.0, 120.0]


def test_nba_labels_tight_game_late_has_big_swings(tmp_path):
    rows = []
    for g, late_lead in (("g1", 1), ("g2", 25)):
        for i, (p, c, h, a) in enumerate([(1, 720.0, 0, 0), (2, 400.0, 30, 28), (4, 60.0, 90 + late_lead, 90),
                                          (4, 5.0, 90 + late_lead, 92), (4, 0.0, 90 + late_lead, 92)]):
            rows.append({"game_id": g, "night": "20240101", "season": 2024, "seq": i, "period": p, "clock_s": c,
                         "home_score": h, "away_score": a})
    sh = tmp_path / "sh"
    sh.mkdir()
    pd.DataFrame(rows).to_parquet(sh / "2024.parquet")
    m = W.nba(sh, tmp_path / "out")
    t = pd.read_parquet(tmp_path / "out" / "nba" / "plays.parquet")
    g1, g2 = t[t.game_id == "g1"], t[t.game_id == "g2"]
    assert g1.surprise.iloc[3:].max() > 0.5 and g2.surprise.iloc[3:].max() < 0.01   # late lead flip vs blowout
    assert g1.suspense.iloc[2] > g2.suspense.iloc[2]
    assert g1.wp.iloc[-1] == 0.0 and g2.wp.iloc[-1] == 1.0              # final: the real winner
    assert m["plays"] == 10 and m["tag"] == W.PERSONAL and m["derived"]
    assert json.loads((tmp_path / "out" / "nba" / "meta.json").read_text())["games"] == 2
