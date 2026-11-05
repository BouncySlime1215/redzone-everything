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
