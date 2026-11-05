#!/usr/bin/env python3
"""NFL Next Gen Stats weekly tables -> pregame team form facts (crew B, scout id nfl-ngs-weekly).

    python scripts/context/nfl_ngs.py --games games.csv --ngs-dir <dir with ngs_{passing,rushing,receiving}.parquet> \
        --out /mnt/project-files/data-b/shards

Source: nflverse release `nextgen_stats` (ngs_passing/rushing/receiving.parquet, 2016+), the nflverse copy of NFL
Next Gen Stats; upstream is the NFL, so personal-use. For each nflverse game and team, the facts are season-to-date
means over that team's NGS weeks strictly before the game's week (week 0 = season totals, never used), so nothing
from the game itself or later leaks in. asof = kickoff - 1 s. PFR-sourced nflverse tables are not used (red).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from redzone.context.schedules import load_nfl  # noqa: E402
from redzone.context.tokens import PERSONAL_USE, Fact, apply_edges, fit_edges, ledger_counts, to_frame  # noqa: E402

NGS_SOURCE = "nfl-ngs-weekly"
NGS_LICENSE = f"nflverse release (CC-BY-4.0, credit nflverse) of NFL Next Gen Stats; {PERSONAL_USE}"
NGS_TRAIN_CUTOFF = "2022-09-01"
# nflverse schedules keep historic abbreviations; NGS uses the franchise's current one.
NGS_TEAM = {"LA": "LAR", "STL": "LAR", "OAK": "LV", "SD": "LAC"}

# (table, field, value column, weight column)
NGS_FIELDS = [
    ("passing", "ngs_qb_cpoe", "completion_percentage_above_expectation", "attempts"),
    ("passing", "ngs_qb_time_to_throw", "avg_time_to_throw", "attempts"),
    ("passing", "ngs_qb_aggressiveness", "aggressiveness", "attempts"),
    ("passing", "ngs_qb_air_yards_to_sticks", "avg_air_yards_to_sticks", "attempts"),
    ("rushing", "ngs_rush_yoe_per_att", "rush_yards_over_expected_per_att", "rush_attempts"),
    ("rushing", "ngs_rush_box8_pct", "percent_attempts_gte_eight_defenders", "rush_attempts"),
    ("receiving", "ngs_rec_separation", "avg_separation", "targets"),
    ("receiving", "ngs_rec_yac_over_exp", "avg_yac_above_expectation", "receptions"),
]


def load_ngs(ngs_dir: Path) -> dict[str, pd.DataFrame]:
    out = {}
    for k in ("passing", "rushing", "receiving"):
        d = pd.read_parquet(ngs_dir / f"ngs_{k}.parquet")
        out[k] = d[(d["week"] > 0) & d["team_abbr"].notna()].copy()
    return out


def team_form(ngs: dict[str, pd.DataFrame], season: int, week: int, team: str) -> dict[str, float]:
    """Weighted season-to-date means over weeks strictly before `week` (same season)."""
    t = NGS_TEAM.get(team, team)
    vals = {}
    for table, field, col, wcol in NGS_FIELDS:
        d = ngs[table]
        if col not in d or wcol not in d:
            continue
        d = d[(d["season"] == season) & (d["week"] < week) & (d["team_abbr"] == t)]
        d = d[d[col].notna() & d[wcol].gt(0)]
        if len(d):
            vals[field] = round(float(np.average(d[col], weights=d[wcol])), 4)
    return vals


def build(games: pd.DataFrame, raw: pd.DataFrame, ngs: dict[str, pd.DataFrame]) -> list[Fact]:
    week = raw.set_index("game_id")["week"]
    facts = []
    for _, g in games[games["season"] >= 2016].iterrows():
        asof = (g["kickoff_utc"] - pd.Timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for team in (g["home"], g["away"]):
            for field, v in team_form(ngs, int(g["season"]), int(week[g["game_id"]]), team).items():
                facts.append(Fact(g["game_id"], team, asof, field, v, "new", NGS_SOURCE, NGS_LICENSE))
    return facts


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", required=True)
    ap.add_argument("--ngs-dir", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    games, raw = load_nfl(a.games), pd.read_csv(a.games, low_memory=False)
    df = to_frame(build(games, raw, load_ngs(Path(a.ngs_dir))))
    kick = games.set_index("game_id")["kickoff_utc"]
    train = df["game_id"].map(kick).lt(pd.Timestamp(NGS_TRAIN_CUTOFF, tz="UTC")).to_numpy()
    edges = fit_edges(df, train)
    df = apply_edges(df, edges)
    d = Path(a.out) / "nfl-ngs"
    d.mkdir(parents=True, exist_ok=True)
    (d / "edges.json").write_text(json.dumps({"train_cutoff_utc": NGS_TRAIN_CUTOFF, "edges": edges}, indent=1))
    seas = games.set_index("game_id")["season"]
    for s, part in df.groupby(df["game_id"].map(seas)):
        part.to_parquet(d / f"{int(s)}.parquet", index=False)
    c = ledger_counts(df)
    print(json.dumps(c))
    return c


if __name__ == "__main__":
    main()
