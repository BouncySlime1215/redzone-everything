"""Vocab v2 (rich tokens): v1 ids kept, extras after each event, train-only bucket edges, pregame context only
before the first play, derived vs new-info counted apart, and the world model cuts windows on KIND tokens."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

pd = pytest.importorskip("pandas")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import noggin_tokens as V1  # noqa: E402
import noggin_tokens_v2 as V2  # noqa: E402
import world_model_pilot as W  # noqa: E402

FIELDS = {"down": ("down", "cat", "new"), "togo": ("ydstogo", "num", "new"), "wp": ("wp", "num", "derived")}


def test_ids_extend_v1_and_fit_uint16():
    assert V2.CTX_OPEN == V1.VOCAB_SIZE and V2.VOCAB_SIZE < 65536
    assert V2.field_token("down") >= V2.FIELD0 and V2.cat_token("down", 3) >= V2.CAT0


def test_edges_fit_on_train_rows_only():
    df = pd.DataFrame({"ydstogo": [1, 2, 3, 4, 100, 200], "wp": [.1, .2, .3, .4, .5, .6]})
    e = V2.fit_edges(df, FIELDS, pd.Series([True] * 4 + [False] * 2))
    assert max(e["togo"]) <= 4 and "down" not in e


def test_extras_skip_missing_and_count_derived_apart():
    e = {"togo": [2.0, 5.0], "wp": [0.5]}
    toks, new, der = V2.extras({"down": 3.0, "ydstogo": 7, "wp": float("nan")}, FIELDS, e)
    assert toks == [V2.field_token("down"), V2.cat_token("down", 3), V2.field_token("togo"), V2.BKT0 + 2]
    assert (new, der) == (4, 0)
    _, new, der = V2.extras({"wp": 0.7}, FIELDS, e)
    assert (new, der) == (0, 2)


def test_context_drops_facts_after_the_first_play_and_postgame():
    facts = pd.DataFrame({"team": ["KC", "DET", "KC"], "asof_utc": ["2023-09-07T00:00:00Z"] * 2 + ["2023-09-08T00:00:00Z"],
                          "field": ["qb_out", "attendance_postgame", "players_out"], "value": [0.0, 1.0, 1.0],
                          "value_str": [None, None, None], "bucket": pd.array([1, 3, 2], dtype="Int8"),
                          "kind": ["new", "new", "new"]})
    first = pd.Timestamp("2023-09-07T12:00:00Z").timestamp()
    toks, new, der = V2.context(facts, "KC", first)
    assert toks == [V2.CTX_OPEN, V2.field_token("home:qb_out"), V2.BKT0 + 1, V2.CTX_CLOSE]
    assert V2.context(facts.iloc[1:2], "KC", first)[0] == []


def test_world_model_windows_tile_on_kind_tokens():
    k = V1.KIND0
    ev = lambda n: [k, V1.DT0, V1.CLOCK0, V1.MARGIN0, V1.PTS0, V1.STOP0] + [V2.FIELD0, V2.BKT0] * n  # noqa: E731
    t = np.array([V1.BOS0, V1.TEAM0, V1.TEAM0] + ev(3) + ev(0) + ev(5) + ev(2) + [V1.EOS], np.int64)
    cs = W.cuts(t, 20)
    assert cs[0] == 0 and all(t[c] == k for c in cs[1:]) and all(b - a <= 20 for a, b in zip(cs, cs[1:]))
    kpos, dpos, spos = W.positions(t)
    assert len(kpos) == 4 and (t[dpos] == V1.DT0).all() and (t[spos] == V1.STOP0).all()


def test_retrosheet_plays_keep_the_v2_extras():
    import retrosheet_plays as R
    txt = "\n".join(["id,ANA202404050", "info,visteam,BAL", "info,hometeam,ANA", "start,p1,P One,1,0,1",
                     "start,p2,P Two,0,0,1", "play,1,0,bat01,32,BCFBS,K"])
    p = R.parse(txt)[0]["plays"][0]
    assert p["x"] == {"batter": "bat01", "pitcher": "p1", "balls": 3, "strikes": 2, "pitches": 5, "bat_home": False}
    toks, new, der = V2.extras(p["x"], V2.FIELDS["mlb"], {"pitches": [3.0, 6.0]})
    assert len(toks) == 12 and (new, der) == (12, 0)


def test_play_table_shard_tokenizes_with_train_only_edges(tmp_path):
    rows = []
    for gi in range(10):
        for i in range(12):
            rows.append({"source": "statsbomb", "sport": "soccer", "league": "x_league", "game_id": f"g{gi}",
                         "night": f"202401{gi + 1:02d}", "seq": i, "home": "A", "away": "B", "period": 1 + i // 6,
                         "elapsed_s": float(i * 60), "wall_ts": None, "home_score": i // 10, "away_score": 0,
                         "kind": "pass" if i % 2 else "shot", "stop": "none", "side": "home", "possession": "",
                         "actor_id": f"p{i}", "x": i / 12 + gi, "y": 0.5, "license": "StatsBomb terms"})
    src = tmp_path / "statsbomb" / "x_league" / "2024.parquet"
    src.parent.mkdir(parents=True)
    pd.DataFrame(rows).to_parquet(src)
    info = V2.build_table(src, tmp_path / "out")
    assert info["games"] == 10 and info["events"] == 120 and info["train_before"] == "20240109"
    assert max(info["edges"]["x"]) < 8 and "possession" not in info["edges"]          # fit on nights before the cut
    assert info["license"] == V2.PERSONAL and info["ledger"]["base"] == 10 * (4 + 72)
    arr = np.load(tmp_path / "out" / "x_league" / "2024.npy")
    assert arr.max() < V2.VOCAB_SIZE and V2.BOS2_0 <= arr[0] < V2.VOCAB_SIZE


def test_tracking_joins_on_game_period_and_nearest_clock(tmp_path):
    ev = pd.DataFrame({"game_id": ["g1", "g1", "g1"], "period": [1, 1, 2], "clock_s": [700.0, 650.2, 700.0],
                       "wall_ts": [None, None, None]})
    trk = pd.DataFrame({"game_id": ["g1"] * 3, "period": [1, 1, 1], "clock_s": [700.1, 650.0, 600.0],
                        "wall_ts": [1000.0, 1050.0, 1100.0], "kind": ["track_live"] * 3, "sub_kind": ["sc18-24"] * 3,
                        "x": [0.1, 0.2, 0.3], "y": [0.5] * 3, "value": [3.0, 9.0, 1.0]})
    p = tmp_path / "trk.parquet"
    trk.to_parquet(p)
    out = V2.join_track(ev, p)
    assert list(out.trk_x[:2]) == [0.1, 0.2] and pd.isna(out.trk_x[2])          # period 2 has no tracking
    assert list(out.wall_ts[:2]) == [1000.0, 1050.0]


def test_play_table_mirror_swaps_sides_on_train_nights_only(tmp_path):
    rows = []
    for gi in range(10):
        for i in range(12):
            rows.append({"source": "nba_stats_pbp", "sport": "basketball", "league": "nba", "game_id": f"g{gi}",
                         "night": f"201601{gi + 1:02d}", "seq": i, "home": "ATL", "away": "DET", "period": 1 + i // 6,
                         "clock_s": float(700 - i * 50), "wall_ts": None, "home_score": 2 * (i // 3),
                         "away_score": i // 4, "kind": "made_shot" if i % 3 == 2 else "rebound", "stop": "none",
                         "side": "home" if i % 2 else "away", "possession": "away" if i % 2 else "home",
                         "actor_id": f"p{i}", "x": 0.3, "y": 0.2, "license": "NBA.com ToS"})
    src = tmp_path / "nba_stats_pbp" / "nba" / "2016.parquet"
    src.parent.mkdir(parents=True)
    df = pd.DataFrame(rows)
    df.to_parquet(src)
    real = V2.build_table(src, tmp_path / "out")
    syn = V2.build_table(src, tmp_path / "out", mirror=True)
    assert syn["synthetic"] and not syn["real"] and syn["tags"] == ["synthetic", "mirror"]
    assert syn["license"] == V2.PERSONAL and syn["edges"] == real["edges"]
    assert syn["games"] == 8 and all(g[1] < real["train_before"] and g[0].endswith("~mirror") for g in syn["index"])
    m = V2.mirror_table(df)
    assert (m.home == "DET").all() and (m.home_score == df.away_score).all() and (m.x == df.x).all()
    assert (m.side == df.side.map({"home": "away", "away": "home"})).all() and (m.actor_id == df.actor_id).all()
    a = np.load(tmp_path / "out" / "nba" / "2016.npy")
    b = np.load(tmp_path / "out" / "nba" / "2016.mirror.npy")
    ga, gb = real["index"][0], syn["index"][0]
    ta, tb = a[ga[2]:ga[2] + ga[3]], b[gb[2]:gb[2] + gb[3]]
    assert (ta[1], ta[2]) == (tb[2], tb[1])                                    # HOME / AWAY team tokens swap
    ev = V2.is_event_start(ta)
    c = V1.MARGIN0 + V1.MARGIN_CAP
    assert np.array_equal(tb[np.nonzero(ev)[0] + 3].astype(int) - c, -(ta[np.nonzero(ev)[0] + 3].astype(int) - c))
    assert (tb.max() < V2.VOCAB_SIZE) and len(ta) == len(tb)
