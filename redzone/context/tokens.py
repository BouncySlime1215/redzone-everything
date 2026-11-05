"""Context fact shards for Data Planner B, in lane 1's vocab v2 hand-off format.

One parquet per league-season. One row = one fact about a game (team null) or a team, as known at `asof_utc`
(strictly before it applies: kickoff for pregame, the minute for live). Columns:

    game_id, team, asof_utc, field, value (float), value_str (categorical, else null),
    bucket (0-15, quantile edges fit on TRAIN games only; null for categorical), kind (new | derived),
    source, license

`kind` "new" = an external fact we did not hold; "derived" = computed from data we already hold. The ledger counts
them apart and only "new" is new information (Nick 11:50 PM). B-plan rows carry the personal-use tag in `license`.
Spec: docs/datasources/context-tokens.md.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

PERSONAL_USE = "personal-use, not for commercial release"
COLUMNS = ["game_id", "team", "asof_utc", "field", "value", "value_str", "bucket", "kind", "source", "license"]
FACT_KINDS = ("new", "derived")
N_BUCKETS = 16


@dataclass(frozen=True)
class Fact:
    game_id: str                 # as in the play table, e.g. nflverse "2024_01_BAL_KC"
    team: str | None             # team abbr, or None for a game-level fact
    asof_utc: str                # ISO-8601 Z
    field: str                   # fixed names, listed in docs/datasources/context-tokens.md
    value: float | str | None    # numbers -> value; strings -> value_str
    kind: str                    # "new" | "derived"
    source: str                  # scout.json id
    license: str                 # license line; B rows must contain PERSONAL_USE

    def row(self) -> dict:
        if self.kind not in FACT_KINDS:
            raise ValueError(f"kind must be one of {FACT_KINDS}: {self.kind!r}")
        if not self.license:
            raise ValueError(f"missing license line for {self.source}")
        num, cat = _split(self.value)
        return {"game_id": self.game_id, "team": self.team, "asof_utc": self.asof_utc, "field": self.field,
                "value": num, "value_str": cat, "bucket": None, "kind": self.kind, "source": self.source,
                "license": self.license}


def _split(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return float("nan"), None
    if isinstance(v, bool):
        return float(v), None
    if isinstance(v, (int, float)):
        return float(v), None
    s = "_".join(str(v).strip().lower().split())
    if not s:
        return float("nan"), None
    try:
        return float(s), None
    except ValueError:
        return float("nan"), s[:48]


def to_frame(facts):
    import pandas as pd
    df = pd.DataFrame([f.row() for f in facts], columns=COLUMNS)
    df["value"] = df["value"].astype("float64")
    df["bucket"] = df["bucket"].astype("Int8")
    return df


def fit_edges(df, train_mask) -> dict[str, list[float]]:
    """Quantile edges per numeric field, fit on TRAIN rows only (never valid/test/locked)."""
    import numpy as np
    edges = {}
    tr = df[train_mask & df["value"].notna()]
    for field, g in tr.groupby("field"):
        q = np.unique(np.quantile(g["value"].to_numpy(), np.linspace(0, 1, N_BUCKETS + 1)[1:-1]))
        edges[field] = [float(x) for x in q]
    return edges


def apply_edges(df, edges: dict[str, list[float]]):
    import numpy as np
    out = df.copy()
    b = np.full(len(out), -1, dtype="int16")
    vals = out["value"].to_numpy()
    for field, e in edges.items():
        m = (out["field"].to_numpy() == field) & ~np.isnan(vals)
        if m.any():
            b[m] = np.searchsorted(np.asarray(e), vals[m], side="right")
    out["bucket"] = b
    out["bucket"] = out["bucket"].where(out["bucket"] >= 0).astype("Int8")
    return out


def ledger_counts(df) -> dict:
    by = df.groupby("kind").size().to_dict() if len(df) else {}
    return {"facts": int(len(df)), "new": int(by.get("new", 0)), "derived": int(by.get("derived", 0)),
            "games": int(df["game_id"].nunique()) if len(df) else 0,
            "fields": sorted(df["field"].unique().tolist()) if len(df) else [],
            "sources": sorted(df["source"].unique().tolist()) if len(df) else []}
