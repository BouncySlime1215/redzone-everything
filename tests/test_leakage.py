"""No brain model may have trained on a held-out night (Nick's no-overfitting rule 6).

Held-out = distill's time split of eval-data-v1 (valid, dev-test and locked nights; redzone/lab/
heldout.py). Every shipped model records what it trained on: v2 its public seasons, v3 the nights
of every source. These checks read the committed model files, so they run in CI without the data.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from redzone.lab import heldout as H
from redzone.scoring import brain as B
from redzone.scoring import brain3 as B3

ROOT = Path(__file__).resolve().parents[1]
V2 = sorted(p.stem for p in B.MODEL_DIR.glob("*.npz") if not p.stem.endswith(".v3"))
V3 = sorted(p.name.split(".")[0] for p in B.MODEL_DIR.glob("*.v3.npz"))


def test_held_out_rule_is_strict():
    assert H.allowed("nba", "20261026") and not H.allowed("nba", "20261027")
    assert not H.allowed("nhl", "20261029") and H.locked("nhl", "20261029")
    assert H.dev_test("nhl", "20261027") and not H.dev_test("nhl", "20261024")
    assert not H.season_allowed("nfl", 2025) and H.season_allowed("nfl", 2024)   # 2025 runs into Feb 2026
    assert H.season_allowed("nba", 2026) and not H.season_allowed("nba", 2027)
    for lg in H.FIRST_HELD:
        assert H.FIRST_HELD[lg] < H.LOCKED_FROM[lg] <= H.WINDOW_END


@pytest.mark.parametrize("league", V2)
def test_v2_models_never_saw_a_held_out_season(league):
    _, info = B.load(B.MODEL_DIR / f"{league}.npz")
    seasons = info.get("seasons")
    assert seasons, f"{league}.npz records no training seasons"
    bad = [s for s in seasons if not H.season_allowed(league, s)]
    assert not bad, f"{league}.npz trained on seasons {bad}, which reach the held-out nights ({H.cutoff(league)})"


@pytest.mark.parametrize("league", V3)
def test_v3_models_never_saw_a_held_out_night(league):
    f = B3.load(B.MODEL_DIR / f"{league}.v3.npz")
    tn = f.info.get("train_nights")
    assert tn, f"{league}.v3.npz records no training nights"
    lo, hi = tn["public"]
    assert hi < H.cutoff(league), f"{league}.v3 public data runs to {hi}, past the cutoff {H.cutoff(league)}"
    bad = [n for n in tn.get("espn", []) if not H.allowed(league, n)]
    assert not bad, f"{league}.v3 trained on held-out archive nights {bad}"


def test_cutoffs_match_distills_split_on_the_pinned_nights():
    """FIRST_HELD is plan() on eval-data-v1's nights; recompute it when both are available."""
    split = ROOT / "agent" / "distill" / "split.py"
    tables = ROOT / "data" / "training3"
    if not split.exists() or not all((tables / f"espn_{lg}.parquet").exists() for lg in H.FIRST_HELD):
        pytest.skip("needs agent/distill/split.py and the archive tables")
    pd = pytest.importorskip("pandas")
    spec = importlib.util.spec_from_file_location("distill_split", split)
    S = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(S)
    nights = {lg: sorted(pd.read_parquet(tables / f"espn_{lg}.parquet", columns=["night"]).night.astype(str).unique())
              for lg in H.FIRST_HELD}
    assert H.first_held_from(nights, S.plan) == H.FIRST_HELD


def test_no_training_table_game_is_a_held_out_game():
    """With the tables present: no public or archive training row's game is played on a held-out night."""
    pd = pytest.importorskip("pandas")
    tables = ROOT / "data" / "training3"
    if not tables.exists():
        pytest.skip("no training tables")
    for lg in H.FIRST_HELD:
        held = set()
        p = tables / f"espn_{lg}.parquet"
        if p.exists():
            a = pd.read_parquet(p, columns=["game_id", "night"])
            held = set(a[~a.night.astype(str).map(lambda n: H.allowed(lg, n))].game_id.astype(str))
        pub = tables / f"{lg}.parquet"
        if pub.exists() and held:
            ids = set(pd.read_parquet(pub, columns=["game_id"]).game_id.astype(str))
            assert not ids & held, f"{lg}: public training games also in held-out nights"
