#!/usr/bin/env python3
"""Noggin event tokens, vocab v2: rich tokens (~50-100 per play) on top of the v1 event group.

Serves: North Star "crunch live %" and "dead air %" through the world model (Saturday ~100M run); owner: lane 1.
Order: Nick 11:50 PM Nov 4 ("RICH TOKENS ... ledger counts DERIVED vs NEW-INFO tokens separately").
Plan: docs/plans/2026-11-07-world-model-100m.md. Personal use: shards built from restrictive sources carry
"license": "personal-use, not for commercial release" (plan B).

    python scripts/noggin_tokens_v2.py nfl RAW/nfl/2024.parquet 2024 OUT [--facts DATA_B/shards] [--train-before N]

One game = <BOS:league> <HOME> <AWAY> [<CTX> (FIELD VALUE)* </CTX>] then per event:
  the 6 v1 tokens (KIND DT CLOCK MARGIN PTS STOP), unchanged ids, so a v1 model's embeddings carry over,
  then (FIELD VALUE) pairs for every field the play has (missing fields are left out, not padded),
then <EOS>. An event therefore starts at its KIND token: windows cut there (is_event_start), not at multiples of 6.

Fields: FIELDS[league] maps a name to (source column, "num"|"cat", "new"|"derived"). Numeric fields are bucketed
by per-league quantile edges fit on TRAIN nights only (--train-before, the first valid night); categorical fields
(players, play type, park, referee) hash into CAT slots. "derived" = a model output or a function of other
columns (WP, EP, EPA, WPA, vegas WP); "new" = observed information the v1 tokens do not carry. The ledger keeps
v1 base tokens, new-info and derived tokens apart, for plays and for the pregame context separately.

Pregame context (crew B, PR #255): facts with asof strictly before the game's first play only (no leakage of
_postgame facts); FIELD hashes "home:<field>" / "away:<field>" so the side is in the token.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import zlib
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import noggin_tokens as V1  # noqa: E402

VOCAB_VERSION = 2
PERSONAL = "personal-use, not for commercial release"
FIELD_SLOTS, BUCKETS, CAT_SLOTS = 1024, 16, 8192

# id layout: v1 ids [0, V1.VOCAB_SIZE) | CTX open/close | FIELD | BUCKET | CAT
CTX_OPEN = V1.VOCAB_SIZE
CTX_CLOSE = CTX_OPEN + 1
FIELD0 = CTX_CLOSE + 1
BKT0 = FIELD0 + FIELD_SLOTS
CAT0 = BKT0 + BUCKETS
VOCAB_SIZE = CAT0 + CAT_SLOTS
assert VOCAB_SIZE < 65536

KIND_LO, KIND_HI = V1.KIND0, V1.DT0          # a KIND id starts every event

# name: (column, num|cat, new|derived). Pre-snap state, the play itself, and its outcome; nflverse columns.
FIELDS: dict[str, dict[str, tuple[str, str, str]]] = {
    "nfl": {
        "down": ("down", "cat", "new"), "togo": ("ydstogo", "num", "new"), "yardline": ("yardline_100", "num", "new"),
        "goal_to_go": ("goal_to_go", "cat", "new"), "pos_home": ("posteam_type", "cat", "new"),
        "pos_to": ("posteam_timeouts_remaining", "cat", "new"),
        "def_to": ("defteam_timeouts_remaining", "cat", "new"),
        "play_type": ("play_type", "cat", "new"), "shotgun": ("shotgun", "cat", "new"),
        "no_huddle": ("no_huddle", "cat", "new"), "dropback": ("qb_dropback", "cat", "new"),
        "pass_len": ("pass_length", "cat", "new"), "pass_loc": ("pass_location", "cat", "new"),
        "run_loc": ("run_location", "cat", "new"), "run_gap": ("run_gap", "cat", "new"),
        "air_yards": ("air_yards", "num", "new"), "yac": ("yards_after_catch", "num", "new"),
        "gained": ("yards_gained", "num", "new"), "first_down": ("first_down", "cat", "new"),
        "penalty": ("penalty", "cat", "new"), "sack": ("sack", "cat", "new"), "int": ("interception", "cat", "new"),
        "fumble_lost": ("fumble_lost", "cat", "new"), "kick_dist": ("kick_distance", "num", "new"),
        "fg_result": ("field_goal_result", "cat", "new"),
        "passer": ("passer_player_id", "cat", "new"), "rusher": ("rusher_player_id", "cat", "new"),
        "receiver": ("receiver_player_id", "cat", "new"),
        "wp": ("wp", "num", "derived"), "vegas_wp": ("vegas_wp", "num", "derived"), "ep": ("ep", "num", "derived"),
        "epa": ("epa", "num", "derived"), "wpa": ("wpa", "num", "derived"), "cp": ("cp", "num", "derived"),
        "xpass": ("xpass", "num", "derived"), "success": ("success", "cat", "derived"),
    },
}


def _slot(s: str, n: int) -> int:
    return zlib.crc32(s.encode()) % n


def field_token(name: str) -> int:
    return FIELD0 + _slot(name, FIELD_SLOTS)


def cat_token(name: str, value) -> int:
    return CAT0 + _slot(f"{name}={value}", CAT_SLOTS)


def bucket_token(x: float, edges) -> int:
    return BKT0 + int(np.searchsorted(np.asarray(edges, float), x, side="right"))


def is_event_start(t: np.ndarray) -> np.ndarray:
    t = np.asarray(t)
    return (t >= KIND_LO) & (t < KIND_HI)


def _missing(v) -> bool:
    import pandas as pd
    try:
        return v is None or (isinstance(v, str) and not v) or bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def fit_edges(df, fields: dict, train_mask=None) -> dict[str, list[float]]:
    """Quantile edges (BUCKETS - 1 cut points) per numeric field, fit on the train rows only."""
    import pandas as pd
    d = df if train_mask is None else df[train_mask]
    out = {}
    for name, (col, typ, _) in fields.items():
        if typ != "num" or col not in d:
            continue
        x = pd.to_numeric(d[col], errors="coerce").dropna().to_numpy(float)
        if len(x):
            out[name] = np.unique(np.quantile(x, np.linspace(0, 1, BUCKETS + 1)[1:-1])).tolist()
    return out


def extras(row: dict, fields: dict, edges: dict) -> tuple[list[int], int, int]:
    """(FIELD VALUE)* for one play row -> tokens, new count, derived count."""
    toks, new, der = [], 0, 0
    for name, (col, typ, cls) in fields.items():
        v = row.get(col)
        if _missing(v):
            continue
        if typ == "num":
            if name not in edges:
                continue
            try:
                val = bucket_token(float(v), edges[name])
            except (TypeError, ValueError):
                continue
        else:
            val = cat_token(name, int(v) if isinstance(v, float) and v.is_integer() else v)
        toks += [field_token(name), val]
        if cls == "derived":
            der += 2
        else:
            new += 2
    return toks, new, der


def context(facts, home: str, first_ts: float | None) -> tuple[list[int], int, int]:
    """Crew B pregame facts for one game -> <CTX> (FIELD VALUE)* </CTX>; only asof before the first play."""
    if facts is None or not len(facts):
        return [], 0, 0
    import pandas as pd
    f = facts
    if first_ts is not None:
        asof = pd.to_datetime(f.asof_utc, utc=True, errors="coerce")
        f = f[asof < pd.Timestamp(first_ts, unit="s", tz="UTC")]          # NaT compares False: dropped
    f = f[~f.field.str.endswith("_postgame")]
    toks, new, der = [CTX_OPEN], 0, 0
    for r in f.sort_values(["team", "field"], kind="stable").itertuples(index=False):
        side = "home" if r.team == home else "away" if isinstance(r.team, str) and r.team else "game"
        name = f"{side}:{r.field}"
        if isinstance(r.value_str, str) and r.value_str:
            val = cat_token(name, r.value_str)
        elif r.bucket is not None and not pd.isna(r.bucket):
            val = BKT0 + max(0, min(BUCKETS - 1, int(r.bucket)))
        else:
            continue
        toks += [field_token(name), val]
        if r.kind == "derived":
            der += 2
        else:
            new += 2
    if len(toks) == 1:
        return [], 0, 0
    toks.append(CTX_CLOSE)
    return toks, new + 2, der


def encode_game(league: str, plays: list[dict], home: str, away: str, rows: dict | None, fields: dict,
                edges: dict, facts=None) -> tuple[np.ndarray, dict]:
    """v1 tokens with extras after each event's 6 tokens. rows: play id -> source row dict."""
    from redzone.sources import espn
    base = V1.encode_game(league, plays, home, away)
    head, ev, eos = base[:3].tolist(), base[3:-1].reshape(-1, V1.TOKENS_PER_EVENT), int(base[-1])
    ts = [t for t in (espn._ts(p.get("wallclock")) for p in plays) if t is not None]
    ctx, cn, cd = context(facts, home, ts[0] if ts else None)
    out, cnt = head + ctx, {"base": len(head) + 1 + ev.size, "new": 0, "derived": 0, "ctx_new": cn, "ctx_derived": cd}
    for p, e in zip(plays, ev):
        out += e.tolist()
        r = (rows or {}).get(str(p.get("id", "")).rsplit(":", 1)[-1])
        if r is not None:
            x, n, d = extras(r, fields, edges)
            out += x
            cnt["new"] += n
            cnt["derived"] += d
    out.append(eos)
    return np.asarray(out, np.uint16), cnt


def nfl_rows(g) -> dict[str, dict]:
    """nflverse rows of one game keyed by play_id (the id suffix factory._plays_football writes)."""
    g = g.copy()
    if "first_down" not in g and {"first_down_rush", "first_down_pass", "first_down_penalty"} <= set(g):
        g["first_down"] = g[["first_down_rush", "first_down_pass", "first_down_penalty"]].max(axis=1)
    return {str(int(r["play_id"])): r for r in g.to_dict("records")}


def build(league: str, raw: Path, year: int, out: Path, facts_dir: Path | None = None,
          train_before: str | None = None, edges: dict | None = None) -> dict:
    """One season's v2 shard: <out>/<league>/<year>.npy + .json (index rows as v1, plus a ledger block)."""
    import pandas as pd
    import pyarrow.parquet as pq

    from redzone.noggin import factory as F
    if league not in FIELDS:
        raise SystemExit(f"{league}: no v2 fields yet (waiting on crew A's play table)")
    fields = FIELDS[league]
    d = Path(out) / league
    js = d / f"{year}.json"
    if js.exists() and json.loads(js.read_text()).get("complete"):
        return json.loads(js.read_text())
    d.mkdir(parents=True, exist_ok=True)
    have = set(pq.read_schema(raw).names)
    need = {c for c, *_ in fields.values()} | {"first_down_rush", "first_down_pass", "first_down_penalty"}
    df = pd.read_parquet(raw, columns=sorted((set(F.NFL_COLS) | {"home_team", "away_team", "game_date"} | need)
                                             & have))
    df = df[df.qtr.notna()].sort_values(["game_id", "play_id"], kind="stable")
    if edges is None:
        mask = None
        if train_before:
            mask = pd.to_datetime(df.game_date).dt.strftime("%Y%m%d") < train_before
        edges = fit_edges(df.assign(first_down=0), fields, mask)
    facts = None
    if facts_dir is not None and (Path(facts_dir) / league / f"{year}.parquet").exists():
        facts = pd.read_parquet(Path(facts_dir) / league / f"{year}.parquet")
    by_game = {k: v for k, v in facts.groupby("game_id")} if facts is not None else {}
    cut = F.EVAL_FIRST_NIGHT.get(league, "99999999")
    parts, games, off = [], [], 0
    led = {"base": 0, "new": 0, "derived": 0, "ctx_new": 0, "ctx_derived": 0}
    for gid, g in df.groupby("game_id", sort=False):
        walled = g.time_of_day.map(lambda v: isinstance(v, str)).any() if "time_of_day" in g else False
        plays, _ = F._plays_football(g, need_wall=bool(walled))
        night = V1._night(plays, g)
        if night is None or night >= cut or len(plays) < 10:
            continue
        home, away = str(g.home_team.iloc[0]), str(g.away_team.iloc[0])
        t, c = encode_game(league, plays, home, away, nfl_rows(g), fields, edges, by_game.get(gid))
        for k in led:
            led[k] += c[k]
        tg = V1.game_tags(league, V1.encode_game(league, plays, home, away))
        parts.append(t)
        games.append([str(gid), night, off, int(len(t)), int(tg["ot"]) | int(tg["comeback"]) << 1
                      | int(tg["blowout"]) << 2])
        off += len(t)
    arr = np.concatenate(parts) if parts else np.zeros(0, np.uint16)
    np.save(d / f"{year}.npy.tmp", arr)
    os.replace(d / f"{year}.npy.tmp.npy", d / f"{year}.npy")
    events = int(sum(is_event_start(arr)))
    info = {"league": league, "season": year, "tokens": int(len(arr)), "games": len(games), "events": events,
            "tokens_per_event": round(len(arr) / max(1, events), 1), "vocab_version": VOCAB_VERSION,
            "vocab_size": VOCAB_SIZE, "real": True, "source": V1.source_of(league, raw),
            "context_source": "data-b (PR #255)" if facts is not None else None,
            "license": PERSONAL if facts is not None else "open (nflverse CC-BY 4.0)",
            "ledger": led, "edges": edges, "train_before": train_before, "complete": True, "index": games}
    js.write_text(json.dumps(info))
    return info


def ledger_line(infos: list[dict]) -> str:
    """One ledger line: tokens/play and total, derived and new-information kept apart."""
    tot = sum(i["tokens"] for i in infos)
    ev = sum(i["events"] for i in infos)
    led = {k: sum(i["ledger"][k] for i in infos) for k in ("base", "new", "derived", "ctx_new", "ctx_derived")}
    return (f"vocab v2: {tot:,} tokens, {ev:,} plays, {tot / max(1, ev):.1f} tokens/play; base {led['base']:,}, "
            f"new-info {led['new'] + led['ctx_new']:,} (context {led['ctx_new']:,}), "
            f"derived {led['derived'] + led['ctx_derived']:,} (context {led['ctx_derived']:,})")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("league")
    ap.add_argument("raw", type=Path)
    ap.add_argument("year", type=int)
    ap.add_argument("out", type=Path)
    ap.add_argument("--facts", type=Path, default=None, help="crew B shards dir (<dir>/<league>/<season>.parquet)")
    ap.add_argument("--train-before", default=None, help="YYYYMMDD: fit bucket edges on nights before this only")
    ap.add_argument("--edges", type=Path, default=None,
                    help="shared edges JSON: loaded if it exists, else fit on this season's train rows and saved "
                         "(build the oldest season first so every later season reuses train-only edges)")
    a = ap.parse_args(argv)
    edges = json.loads(a.edges.read_text()) if a.edges and a.edges.exists() else None
    info = build(a.league, a.raw, a.year, a.out, a.facts, a.train_before, edges)
    if a.edges and edges is None:
        a.edges.write_text(json.dumps(info["edges"]))
    print(json.dumps({k: v for k, v in info.items() if k not in ("index", "edges")}))
    print(ledger_line([info]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
