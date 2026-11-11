"""Data Division A (play-by-play): the one play table every source converter writes.

Serves: North Star "% of hindsight-oracle value achieved" via world-model pretraining data scale (EXP-097,
EXP-1000). Owner: Data Planner A. Lane 1's tokenizer v2 reads this table, so converters land data without waiting
on the vocab: a source is "landed" when its play table shard is in the private Kaggle dataset and its ledger row
is written.

One row = one event (a play, a pitch, a ball, a soccer event), in game order. Columns (all optional ones may be
null; a converter never invents a value it does not have):

    source      str   converter id, e.g. "mlb_statsapi", "wehoop", "statsbomb", "cricsheet"
    sport       str   SPORTS
    league      str   e.g. "milb_aaa", "mlb", "wnba", "wcbb", "epl", "ipl" (lowercase, no spaces)
    level       str   LEVELS
    season      int   the season's label year (the year it ends in for winter sports)
    game_id     str   unique within source
    night       str   YYYYMMDD local game date (US: ET minus 5 h, as factory.night_of; else the local date)
    seq         int   0.. in game order
    period      int   quarter / half / inning (top and bottom = 2 x inning - 1 / 2 x inning) / over-innings
    clock_s     float seconds LEFT in the period (count-down sports), or null
    elapsed_s   float seconds since the period started (soccer, cricket ball index x 30 s is NOT allowed: null)
    wall_ts     float unix seconds of the event (real wall clock only, never synthesised)
    home, away  str   team ids/abbreviations as the source gives them
    home_score, away_score  int  score AFTER the event
    side        str   "home" | "away" | "" : the acting team
    possession  str   "home" | "away" | "" : the team with the ball (batting team in baseball/cricket) after the event
    kind        str   the source's event type, lowercased (fixed vocab of the source, never free text)
    sub_kind    str   a finer type / outcome when the source has one ("" otherwise)
    actor_id, actor2_id  str  player ids as the source gives them ("" if none)
    x, y        float location normalised to 0..1 of the field (attacking left to right when the source says so)
    value       float points / runs / goals on this event (0 if none)
    outs, balls, strikes  int  baseball count before the pitch; cricket: outs = wickets down
    bases       int   baseball runners bitmask (1 = first, 2 = second, 4 = third) before the event
    stop        str   what the game does right after: STOPS (tokenizer v1 names) or ""
    license     str   the source's license line (the same text as its scout.json row)
    tag         str   PERSONAL for every plan-B source, "" for green/yellow sources used under their own licence

No free text column: play descriptions are mapped to kind/sub_kind and numbers only (tokenizer-v2-sources.md).
Locked/scorecard nights of our six product leagues are never written (EVAL_FIRST_NIGHT); other leagues have no
locked nights, but every shard keeps `night`, so time splits stay whole-night.
Why: Defines the one play table every source converter writes (docstring, Data A commit).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

PERSONAL = "personal-use, not for commercial release"
SPORTS = ("baseball", "basketball", "football", "hockey", "soccer", "cricket", "tennis")
LEVELS = ("pro", "minor", "college", "intl", "youth")
STOPS = ("none", "timeout", "two_minute_warning", "end_period", "halftime", "intermission", "review", "injury",
         "pitching_change", "score_break", "whistle", "unknown")
# Product leagues' scorecard/locked windows (redzone/noggin/factory.EVAL_FIRST_NIGHT + MLB 2025+ held out).
EVAL_FIRST_NIGHT = {"nba": "20261020", "cbb": "20261020", "wcbb": "20261020", "nfl": "20250905",
                    "nhl": "20261019", "mlb": "20250301"}

STR = ("source", "sport", "league", "level", "game_id", "night", "home", "away", "side", "possession", "kind", "sub_kind",
       "actor_id", "actor2_id", "stop", "license", "tag")
INT = ("season", "seq", "period", "home_score", "away_score", "outs", "balls", "strikes", "bases")
FLOAT = ("clock_s", "elapsed_s", "wall_ts", "x", "y", "value")
COLUMNS = STR + INT + FLOAT
REQUIRED = ("source", "sport", "league", "level", "season", "game_id", "night", "seq", "kind", "license")

# For tokenizer v2 (lane 1): each field as (num | cat | key | meta, new | derived). Keys: (source, game_id) identify a
# game, seq is the play order. "new" = observed in the source; "derived" = computed by the converter from other rows
# (counts, running state). Edges for num fields are fit by lane 1 on train nights only.
FIELD_META = {
    "source": ("key", "new"), "game_id": ("key", "new"), "seq": ("key", "new"),
    "sport": ("cat", "new"), "league": ("cat", "new"), "level": ("cat", "new"), "season": ("meta", "new"),
    "night": ("meta", "new"), "home": ("cat", "new"), "away": ("cat", "new"),
    "period": ("num", "new"), "clock_s": ("num", "new"), "elapsed_s": ("num", "new"), "wall_ts": ("num", "new"),
    "home_score": ("num", "new"), "away_score": ("num", "new"), "side": ("cat", "new"),
    "possession": ("cat", "derived"), "kind": ("cat", "new"), "sub_kind": ("cat", "new"),
    "actor_id": ("cat", "new"), "actor2_id": ("cat", "new"), "x": ("num", "new"), "y": ("num", "new"),
    "value": ("num", "new"), "outs": ("num", "derived"), "balls": ("num", "derived"),
    "strikes": ("num", "derived"), "bases": ("cat", "derived"), "stop": ("cat", "derived"),
    "license": ("meta", "new"), "tag": ("meta", "new"),
}
assert set(FIELD_META) == set(COLUMNS)

# Token estimates (honest split, Nick 11:50 PM): events x 6 is vocab v1 (noggin_tokens). Tokenizer v2's rich
# fields are counted by lane 1; here only the event count and v1 tokens are claimed as NEW information.
V1_TOKENS_PER_EVENT = 6


def row(**kw) -> dict:
    """One play-table row with every column present (missing -> null / "")."""
    out = {}
    for c in STR:
        v = kw.get(c)
        out[c] = "" if v is None else str(v)
    for c in INT:
        v = kw.get(c)
        out[c] = None if v is None or (isinstance(v, float) and math.isnan(v)) else int(v)
    for c in FLOAT:
        v = kw.get(c)
        out[c] = None if v is None else float(v)
    extra = set(kw) - set(COLUMNS)
    if extra:
        raise KeyError(f"unknown play-table columns: {sorted(extra)}")
    return out


def validate(rows: list[dict]) -> list[str]:
    """Problems with a converted game (empty list = OK)."""
    errs = []
    if not rows:
        return ["no rows"]
    for i, r in enumerate(rows):
        for c in REQUIRED:
            if r.get(c) in (None, ""):
                errs.append(f"row {i}: {c} missing")
        if r.get("sport") not in SPORTS:
            errs.append(f"row {i}: sport {r.get('sport')!r}")
        if r.get("level") not in LEVELS:
            errs.append(f"row {i}: level {r.get('level')!r}")
        if r.get("seq") != i:
            errs.append(f"row {i}: seq {r.get('seq')} out of order")
        if r.get("stop") and r["stop"] not in STOPS:
            errs.append(f"row {i}: stop {r['stop']!r}")
        n = r.get("night") or ""
        if len(n) != 8 or not n.isdigit():
            errs.append(f"row {i}: night {n!r}")
        cut = EVAL_FIRST_NIGHT.get(r.get("league") or "")
        if cut and n >= cut:
            errs.append(f"row {i}: night {n} is in {r['league']}'s scorecard/locked window")
        if not r.get("license"):
            errs.append(f"row {i}: no license line")
        if errs and len(errs) > 20:
            break
    return errs


def to_frame(rows: list[dict]):
    import pandas as pd
    df = pd.DataFrame(rows, columns=list(COLUMNS))
    for c in INT:
        df[c] = df[c].astype("Int32")
    for c in FLOAT:     # wall_ts needs float64: float32 rounds unix seconds to ~128 s steps
        df[c] = df[c].astype("float64" if c == "wall_ts" else "float32")
    return df


def write_shard(rows: list[dict], path: Path, meta: dict) -> dict:
    """Write one shard (parquet, zstd) + <path>.json sidecar: events, games, v1 token estimate, licence, tag."""
    path.parent.mkdir(parents=True, exist_ok=True)
    df = to_frame(rows)
    df.to_parquet(path, compression="zstd", index=False)
    side = dict(meta)
    side.update({"events": int(len(df)), "games": int(df.game_id.nunique()) if len(df) else 0,
                 "v1_tokens": int(len(df) * V1_TOKENS_PER_EVENT + 4 * (df.game_id.nunique() if len(df) else 0)),
                 "bytes": path.stat().st_size, "license": meta.get("license", ""), "tag": meta.get("tag", ""),
                 "schema": "pbp-play-table-1"})
    Path(str(path) + ".json").write_text(json.dumps(side, indent=1, sort_keys=True))
    return side
