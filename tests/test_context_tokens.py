import math

import pandas as pd
import pytest

from redzone.context.tokens import Fact, apply_edges, fit_edges, ledger_counts, to_frame

LIC = "test license"


def _f(gid, field, v, kind="new"):
    return Fact(gid, None, "2024-01-01T00:00:00Z", field, v, kind, "src", LIC)


def test_row_split_and_checks():
    df = to_frame([_f("g1", "temp_f", 41), _f("g1", "referee", "Bill Vinovich"), _f("g1", "temp_f", None)])
    assert df.loc[0, "value"] == 41.0 and pd.isna(df.loc[0, "value_str"])
    assert df.loc[1, "value_str"] == "bill_vinovich" and math.isnan(df.loc[1, "value"])
    with pytest.raises(ValueError):
        _f("g", "x", 1, kind="maybe").row()
    with pytest.raises(ValueError):
        Fact("g", None, "t", "x", 1, "new", "s", "").row()


def test_edges_fit_on_train_only():
    df = to_frame([_f(f"g{i}", "temp_f", float(i)) for i in range(100)])
    train = pd.Series([i < 50 for i in range(100)])
    edges = fit_edges(df, train)
    assert max(edges["temp_f"]) < 50  # nothing from the held-out half
    out = apply_edges(df, edges)
    assert out["bucket"].min() == 0 and out["bucket"].max() <= 15
    assert out.loc[99, "bucket"] == len(edges["temp_f"])


def test_ledger_counts_new_vs_derived():
    df = to_frame([_f("g1", "a", 1), _f("g2", "b", 2, "derived")])
    c = ledger_counts(df)
    assert (c["new"], c["derived"], c["games"]) == (1, 1, 2)
