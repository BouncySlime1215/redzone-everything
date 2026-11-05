"""Stakes context (worker B3): point-in-time standings + playoff-odds swing, no leakage."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from redzone.context import standings as S
from redzone.context.tokens import to_frame

TOY = {"A": ("C", "D1"), "B": ("C", "D1"), "X": ("C", "D2"), "Y": ("C", "D2")}


def toy_cfg(**kw):
    return S.LeagueConfig("toy", lambda s: TOY, lambda s: (1, 1), "toy license; personal-use, not for commercial release",
                          sims=300, elo_k=30.0, elo_hfa=20.0, **kw)


def toy_games(flip_from: int | None = None) -> pd.DataFrame:
    pairs = [("A", "B"), ("X", "Y"), ("A", "X"), ("B", "Y"), ("A", "Y"), ("B", "X")] * 2
    rows = []
    t0 = pd.Timestamp("2024-09-01 17:00", tz="UTC")
    for i, (h, a) in enumerate(pairs):
        k = t0 + pd.Timedelta(days=7 * (i // 2))
        hs, as_ = (24, 10) if (i % 3) else (13, 20)
        if flip_from is not None and i >= flip_from:
            hs, as_ = as_ + 7, hs
        rows.append({"game_id": f"g{i:02d}", "season": 2024, "game_type": "REG", "kickoff_utc": k,
                     "final_utc": k + pd.Timedelta(hours=4), "home": h, "away": a, "home_score": hs, "away_score": as_,
                     "neutral": False})
    return pd.DataFrame(rows)


def facts_df(games, cfg=None):
    cfg = cfg or toy_cfg()
    elo = S.Elo(cfg)
    return to_frame(S.compute_season(games, cfg, elo, seed=7))


def test_no_result_from_game_or_later_leaks():
    base = facts_df(toy_games())
    for g in range(len(toy_games())):
        alt = facts_df(toy_games(flip_from=g))
        a = base[base.game_id == f"g{g:02d}"].reset_index(drop=True)
        b = alt[alt.game_id == f"g{g:02d}"].reset_index(drop=True)
        pd.testing.assert_frame_equal(a, b)


def test_asof_before_kickoff_and_uses_only_completed_games():
    games = toy_games()
    df = facts_df(games)
    kick = games.set_index("game_id")["kickoff_utc"]
    asof = pd.to_datetime(df["asof_utc"], utc=True)
    assert (asof < df["game_id"].map(kick)).all()
    # week-1 games: nobody has played, so 0 wins and NaN win_pct
    w1 = df[df.game_id.isin(["g00", "g01"]) & (df.field == "wins")]
    assert (w1.value == 0).all()
    # game 2 (week 2) sees exactly week-1 results: g00 home A lost (13-20), g01 X won
    w = df[(df.game_id == "g02") & (df.field == "wins")].set_index("team")["value"]
    assert w["A"] == 0 and w["X"] == 1


def test_swing_and_odds_in_unit_interval():
    df = facts_df(toy_games())
    for f in ("playoff_odds", "playoff_odds_if_win", "playoff_odds_if_loss", "playoff_odds_swing", "elo_win_prob"):
        v = df[df.field == f].value
        assert len(v) and v.between(0, 1).all(), f
    piv = df[df.team.notna()].pivot_table(index=["game_id", "team"], columns="field", values="value")
    assert (piv["playoff_odds_if_win"] + 1e-9 >= piv["playoff_odds_if_loss"]).all()   # winning never hurts here
    assert ((piv["playoff_odds_if_win"] - piv["playoff_odds_if_loss"]).abs() - piv["playoff_odds_swing"]).abs().max() < 1e-3
    assert set(df[df.field == "clinch_status"].value_str) <= {"alive", "eliminated", "clinched_div", "clinched_playoff"}
    assert (df.kind == "derived").all() and df.license.str.contains("personal-use").all()


def test_clinch_and_elimination_are_mathematical():
    st = S.Structure.build(toy_cfg(), 2024, list(TOY))
    # teams sorted A,B,X,Y. A 10 pts with 1 game left, B 2 pts: A clinched the division
    status = st.clinch_status(pts=np.array([10.0, 2.0, 5.0, 5.0]), rem=np.array([1, 1, 1, 1]))
    assert status[0] == "clinched_div"
    # B can reach 3 < A's 10 (div out) and A, X, Y are all > 3 = 3 bids: eliminated
    assert status[1] == "eliminated"
    assert status[2] == "alive"


def test_playoff_picker_counts():
    st = S.Structure.build(toy_cfg(), 2024, list(TOY))
    W = np.array([[5.0, 4.0, 3.0, 1.0], [1.0, 2.0, 3.0, 4.0]])
    m = st.made(W)
    assert m.sum(axis=1).tolist() == [3, 3]
    assert m[0].tolist() == [True, True, True, False]    # A div, X div, B wild card
    assert m[1].tolist() == [False, True, True, True]


@pytest.mark.parametrize("season,n,bids", [(1999, 31, 12), (2002, 32, 12), (2019, 32, 12), (2020, 32, 14), (2026, 32, 14)])
def test_nfl_config(season, n, bids):
    dmap = S.nfl_divisions(season)
    teams = list(dmap) if season <= 2001 else [t for t in dmap if t not in S._NFL_ALIAS]
    st = S.Structure.build(S.NFL, season, teams)
    per_div, wc = S.nfl_playoff(season)
    assert len(st.teams) == n
    assert per_div * len(st.div_cols) + wc * len(st.conf_cols) == bids


@pytest.mark.parametrize("season,bids", [(2005, 8), (2012, 10), (2020, 16), (2021, 10), (2022, 12)])
def test_mlb_config(season, bids):
    teams = sorted({t for v in S._MLB_BASE.values() for t in v} | {"HOU"})
    st = S.Structure.build(S.MLB, season, teams)
    per_div, wc = S.mlb_playoff(season)
    assert len(st.teams) == 30 and len(st.div_cols) == 6
    assert per_div * 6 + wc * 2 == bids
    assert S.mlb_divisions(season)["HOU"] == (("AL", "West") if season >= 2013 else ("NL", "Central"))


def test_pregame_rows_for_unplayed_games_use_now():
    games = toy_games()
    games.loc[games.index >= 6, ["home_score", "away_score"]] = float("nan")
    now = games.loc[6, "kickoff_utc"] - pd.Timedelta(hours=10)
    cfg = toy_cfg()
    df = to_frame(S.compute_season(games, cfg, S.Elo(cfg), now_utc=now, horizon=pd.Timedelta(days=3), seed=1))
    assert {"g06", "g07"} <= set(df.game_id) and "g08" not in set(df.game_id)
    assert (df[df.game_id == "g06"].asof_utc == now.strftime("%Y-%m-%dT%H:%M:%SZ")).all()
