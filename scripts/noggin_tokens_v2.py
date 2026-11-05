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
LEAGUE_SLOTS = 256                           # BOS for play-table leagues outside v1's 7 (soccer, MiLB, WNBA ...)
BOS2_0 = CAT0 + CAT_SLOTS
VOCAB_SIZE = BOS2_0 + LEAGUE_SLOTS
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
    # NBA.com cdn log (scripts/nba_cdn_plays.py keeps these as x_<col>); side is the acting team vs the home team
    "nba": {
        "side": ("side", "cat", "new"), "sub_type": ("x_subType", "cat", "new"), "actor": ("x_personId", "cat", "new"),
        "assist": ("x_assistPersonId", "cat", "new"), "shot_x": ("x_x", "num", "new"), "shot_y": ("x_y", "num", "new"),
        "shot_dist": ("x_shotDistance", "num", "new"), "shot_result": ("x_shotResult", "cat", "new"),
        "descriptor": ("x_descriptor", "cat", "new"), "area": ("x_area", "cat", "new"),
    },
    # Retrosheet event files (scripts/retrosheet_plays.py keeps these in each play's "x")
    "mlb": {
        "batter": ("batter", "cat", "new"), "pitcher": ("pitcher", "cat", "new"), "balls": ("balls", "cat", "new"),
        "strikes": ("strikes", "cat", "new"), "pitches": ("pitches", "num", "new"), "bat_home": ("bat_home", "cat", "new"),
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


def encode_game(league: str, plays: list[dict], home: str, away: str, rows: list | None, fields: dict,
                edges: dict, facts=None) -> tuple[np.ndarray, dict]:
    """v1 tokens with extras after each event's 6 tokens. rows: one source row dict (or None) per play."""
    from redzone.sources import espn
    base = V1.encode_game(league, plays, home, away)
    head, ev, eos = base[:3].tolist(), base[3:-1].reshape(-1, V1.TOKENS_PER_EVENT), int(base[-1])
    ts = [t for t in (espn._ts(p.get("wallclock")) for p in plays) if t is not None]
    ctx, cn, cd = context(facts, home, ts[0] if ts else None)
    out, cnt = head + ctx, {"base": len(head) + 1 + ev.size, "new": 0, "derived": 0, "ctx_new": cn, "ctx_derived": cd}
    for i, e in enumerate(ev):
        out += e.tolist()
        r = rows[i] if rows is not None and i < len(rows) else None
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


def season_games(league: str, raw: Path, fields: dict):
    """(gid, night, plays, home, away, rows) per game; rows line up with plays (None where a play has no extras)."""
    import pandas as pd
    import pyarrow.parquet as pq

    from redzone.noggin import factory as F
    raw = Path(raw)
    if league == "nfl":
        have = set(pq.read_schema(raw).names)
        need = {c for c, *_ in fields.values()} | {"first_down_rush", "first_down_pass", "first_down_penalty"}
        df = pd.read_parquet(raw, columns=sorted((set(F.NFL_COLS) | {"home_team", "away_team", "game_date"} | need)
                                                 & have))
        df = df[df.qtr.notna()].sort_values(["game_id", "play_id"], kind="stable")
        cut = F.EVAL_FIRST_NIGHT.get(league, "99999999")
        for gid, g in df.groupby("game_id", sort=False):
            walled = g.time_of_day.map(lambda v: isinstance(v, str)).any() if "time_of_day" in g else False
            plays, _ = F._plays_football(g, need_wall=bool(walled))
            night = V1._night(plays, g)
            if night is None or night >= cut:
                continue
            by = nfl_rows(g)
            rows = [by.get(str(p.get("id", "")).rsplit(":", 1)[-1]) for p in plays]
            yield str(gid), night, plays, str(g.home_team.iloc[0]), str(g.away_team.iloc[0]), rows
        return
    xs = None
    if str(raw).endswith(".parquet"):
        cols = [c for c in pq.read_schema(raw).names if c.startswith("x_")] + ["id"]
        if len(cols) > 1:
            x = pd.read_parquet(raw, columns=cols)
            xs = {str(r["id"]): r for r in x.to_dict("records")}
    for gid, night, plays, home, away in V1.games_of(league, raw):
        if xs is not None:
            rows = []
            for p in plays:
                r = xs.get(str(p.get("id", "")))
                if r is not None and isinstance(r.get("x_teamTricode"), str):
                    r = {**r, "side": "home" if r["x_teamTricode"] == home else "away"}
                rows.append(r)
        else:
            rows = [p.get("x") for p in plays]
        yield gid, night, plays, home, away, rows


def build(league: str, raw: Path, year: int, out: Path, facts_dir: Path | None = None,
          train_before: str | None = None, edges: dict | None = None) -> dict:
    """One season's v2 shard: <out>/<league>/<year>.npy + .json (index rows as v1, plus a ledger block)."""
    import pandas as pd
    if league not in FIELDS:
        raise SystemExit(f"{league}: no v2 fields yet")
    fields = FIELDS[league]
    d = Path(out) / league
    js = d / f"{year}.json"
    if js.exists() and json.loads(js.read_text()).get("complete"):
        return json.loads(js.read_text())
    d.mkdir(parents=True, exist_ok=True)
    games_all = [g for g in season_games(league, raw, fields) if len(g[2]) >= 10]
    if edges is None:                                   # train nights only
        rows = [r for g in games_all if not train_before or g[1] < train_before for r in g[5] if r is not None]
        edges = fit_edges(pd.DataFrame(rows), fields) if rows else {}
    facts = None
    if facts_dir is not None and (Path(facts_dir) / league / f"{year}.parquet").exists():
        facts = pd.read_parquet(Path(facts_dir) / league / f"{year}.parquet")
    by_game = {k: v for k, v in facts.groupby("game_id")} if facts is not None else {}
    parts, games, off = [], [], 0
    led = {"base": 0, "new": 0, "derived": 0, "ctx_new": 0, "ctx_derived": 0}
    for gid, night, plays, home, away, rows in games_all:
        t, c = encode_game(league, plays, home, away, rows, fields, edges, by_game.get(gid))
        for k in led:
            led[k] += c[k]
        tg = V1.game_tags(league, V1.encode_game(league, plays, home, away))
        parts.append(t)
        games.append([gid, night, off, int(len(t)), int(tg["ot"]) | int(tg["comeback"]) << 1
                      | int(tg["blowout"]) << 2])
        off += len(t)
    arr = np.concatenate(parts) if parts else np.zeros(0, np.uint16)
    np.save(d / f"{year}.npy.tmp", arr)
    os.replace(d / f"{year}.npy.tmp.npy", d / f"{year}.npy")
    events = int(sum(is_event_start(arr)))
    src = V1.source_of(league, raw)
    restrictive = facts is not None or any(k in src.lower() for k in ("espn", "hoopr", "api-web", "nba-cdn"))
    info = {"league": league, "season": year, "tokens": int(len(arr)), "games": len(games), "events": events,
            "tokens_per_event": round(len(arr) / max(1, events), 1), "vocab_version": VOCAB_VERSION,
            "vocab_size": VOCAB_SIZE, "real": True, "source": src,
            "context_source": "data-b (PR #255)" if facts is not None else None,
            "license": PERSONAL if restrictive else f"open ({src})",
            "ledger": led, "edges": edges, "train_before": train_before, "complete": True, "index": games}
    js.write_text(json.dumps(info))
    return info


# ---------------------------------------------------------------------------------------- crew A play table (#259)
TABLE_PERIOD_S = {"soccer": 2700.0, "basketball": 600.0, "hockey": 1200.0, "football": 900.0, "cricket": 0.0,
                  "baseball": 0.0}
PERIOD_BY_LEAGUE = {"nba": 720.0, "wnba": 600.0, "cbb": 1200.0, "wcbb": 600.0, "nfl": 900.0, "nhl": 1200.0}
TABLE_FIELDS = {  # play-table column -> (num|cat, new|derived): scripts/pbp/schema.py FIELD_META minus what v1 carries
    "side": ("cat", "new"), "possession": ("cat", "derived"), "sub_kind": ("cat", "new"), "actor_id": ("cat", "new"),
    "actor2_id": ("cat", "new"), "x": ("num", "new"), "y": ("num", "new"), "value": ("num", "new"),
    "outs": ("cat", "derived"), "balls": ("cat", "derived"), "strikes": ("cat", "derived"), "bases": ("cat", "derived"),
    # tracking joined at the event's game-clock second (crew A SportVU, --track): ball x/y, ball speed, live/dead,
    # shot-clock bucket. New information (the play table has none of it); counted under "new" in the ledger.
    "trk_x": ("num", "new"), "trk_y": ("num", "new"), "trk_speed": ("num", "new"), "trk_state": ("cat", "new"),
    "trk_shot_clock": ("cat", "new"),
}


def join_track(df, track_path: Path, tol_s: float = 1.5):
    """Each event row gets the tracking second nearest its game clock (same game and period, within tol_s): ball
    x/y, speed, live/dead and shot-clock bucket, plus the tracking wall clock where the event has none."""
    import pandas as pd
    t = pd.read_parquet(track_path, columns=["game_id", "period", "clock_s", "wall_ts", "kind", "sub_kind", "x", "y",
                                             "value"])
    t = t.rename(columns={"wall_ts": "trk_wall", "kind": "trk_state", "sub_kind": "trk_shot_clock", "x": "trk_x",
                          "y": "trk_y", "value": "trk_speed"})
    t["period"] = t.period.astype("int64")
    t["clock_s"] = t.clock_s.astype("float64")
    d = df.copy()
    d["_row"] = np.arange(len(d))
    d["period"] = d.period.astype("int64")
    d["clock_s"] = d.clock_s.astype("float64")
    m = pd.merge_asof(d.dropna(subset=["clock_s"]).sort_values("clock_s"), t.sort_values("clock_s"), on="clock_s",
                      by=["game_id", "period"], direction="nearest", tolerance=tol_s)
    m = m.sort_values("_row")
    out = df.copy()
    for c in ("trk_x", "trk_y", "trk_speed", "trk_state", "trk_shot_clock", "trk_wall"):
        out[c] = pd.Series(m[c].to_numpy(), index=df.index[m["_row"].to_numpy()]).reindex(df.index)
    if "wall_ts" in out:
        out["wall_ts"] = out.wall_ts.astype("float64").fillna(out.trk_wall.astype("float64"))
    else:
        out["wall_ts"] = out.trk_wall
    return out.drop(columns=["trk_wall"])


def table_game(g, sport: str, league: str, fields: dict, edges: dict) -> tuple[np.ndarray, dict]:
    """One game of crew A's play table -> v2 tokens. v1 group from kind/period/clock/scores/stop; DT from wall_ts,
    else from elapsed_s within a period (the game clock, as v1 does without wall clocks); a new BOS per league."""
    import pandas as pd
    nb = len(V1.BASES) * V1.KIND_SLOTS
    out = [BOS2_0 + _slot(f"{sport}:{league}", LEAGUE_SLOTS),
           V1.TEAM0 + _slot(f"{league}:{g.home.iloc[0]}", len(V1.BASES) * V1.TEAM_SLOTS),
           V1.TEAM0 + _slot(f"{league}:{g.away.iloc[0]}", len(V1.BASES) * V1.TEAM_SLOTS)]
    per_s = PERIOD_BY_LEAGUE.get(league) or TABLE_PERIOD_S.get(sport) or float(
        pd.to_numeric(g.elapsed_s, errors="coerce").max() or 0) or None
    cnt = {"base": 4, "new": 0, "derived": 0, "ctx_new": 0, "ctx_derived": 0}
    last_w = last_e = last_p = None
    ph = pa = 0
    for r in g.to_dict("records"):
        w = None if _missing(r.get("wall_ts")) else float(r["wall_ts"])
        per = 1 if _missing(r.get("period")) else int(r["period"])
        e = None if _missing(r.get("elapsed_s")) else float(r["elapsed_s"])
        if e is None and per_s and not _missing(r.get("clock_s")):     # countdown clock -> seconds into the period
            e = per_s - float(r["clock_s"])
        dt = (w - last_w) if w is not None and last_w is not None else (
            e - last_e if e is not None and last_e is not None and per == last_p else None)
        last_w, last_e, last_p = w if w is not None else last_w, e if e is not None else last_e, per
        frac = 0.0 if e is None or not per_s else max(0.0, min(0.999, e / per_s))
        hs = ph if _missing(r.get("home_score")) else int(r["home_score"])
        as_ = pa if _missing(r.get("away_score")) else int(r["away_score"])
        dh, da = max(0, hs - ph), max(0, as_ - pa)
        ph, pa = hs, as_
        stop = r.get("stop") or "unknown"
        out += [V1.KIND0 + _slot(f"{sport}:{r.get('kind') or '?'}", nb), V1.dt_token(dt),
                V1.CLOCK0 + (max(1, min(V1.MAX_PERIOD, per)) - 1) * V1.CLOCK_BUCKETS + int(frac * V1.CLOCK_BUCKETS),
                V1.MARGIN0 + V1.MARGIN_CAP + max(-V1.MARGIN_CAP, min(V1.MARGIN_CAP, hs - as_)),
                V1.PTS0 + (min(7, dh) if dh else 8 + min(7, da) if da else 0),
                V1.STOP0 + (V1.STOPS.index(stop) if stop in V1.STOPS else V1.STOPS.index("unknown"))]
        cnt["base"] += 6
        x, n, d = extras(r, fields, edges)
        out += x
        cnt["new"] += n
        cnt["derived"] += d
    out.append(V1.EOS)
    return np.asarray(out, np.uint16), cnt


def build_table(src: Path, out: Path, train_before: str | None = None, track: Path | None = None) -> dict:
    """One crew A shard (<root>/<source>/<league>/<season>.parquet) -> <out>/<league>/<season>.npy + .json.
    track: a tracking shard on the same game ids (SportVU), joined per event (join_track)."""
    import pandas as pd
    df = pd.read_parquet(src)
    season = src.stem
    league = str(df.league.iloc[0]) if "league" in df and len(df) and df.league.iloc[0] else src.parent.name
    if track is not None:
        df = join_track(df, track)
    d = Path(out) / league
    js = d / f"{season}.json"
    if js.exists() and json.loads(js.read_text()).get("complete"):
        return json.loads(js.read_text())
    d.mkdir(parents=True, exist_ok=True)
    fields = {c: (c, *TABLE_FIELDS[c]) for c in TABLE_FIELDS if c in df and df[c].notna().any()
              and not (df[c].dtype == object and (df[c] == "").all())}
    fields = {k: v for k, v in fields.items() if not (df[k].astype(str) == "").all()}
    if not train_before and len(df):        # default: the world model's own valid start (split(): 80% of nights)
        nights = sorted(df.night.unique())
        train_before = nights[int(len(nights) * 0.8)]
    tr = df[df.night < train_before]
    edges = fit_edges(tr, fields)
    df = df.sort_values(["game_id", "seq"], kind="stable")
    parts, games, off = [], [], 0
    led = {"base": 0, "new": 0, "derived": 0, "ctx_new": 0, "ctx_derived": 0}
    sport = str(df.sport.iloc[0]) if len(df) else "?"
    for gid, g in df.groupby("game_id", sort=False):
        if len(g) < 10:
            continue
        t, c = table_game(g, sport, league, fields, edges)
        for k in led:
            led[k] += c[k]
        parts.append(t)
        games.append([str(gid), str(g.night.iloc[0]), off, int(len(t)), 0])
        off += len(t)
    arr = np.concatenate(parts) if parts else np.zeros(0, np.uint16)
    np.save(d / f"{season}.npy.tmp", arr)
    os.replace(d / f"{season}.npy.tmp.npy", d / f"{season}.npy")
    lic = str(df.license.iloc[0]) if len(df) and "license" in df else ""
    events = int(sum(is_event_start(arr)))
    info = {"league": league, "sport": sport, "season": season, "tokens": int(len(arr)), "games": len(games),
            "events": events, "tokens_per_event": round(len(arr) / max(1, events), 1),
            "vocab_version": VOCAB_VERSION, "vocab_size": VOCAB_SIZE, "real": True,
            "source": str(df.source.iloc[0]) if len(df) else "?", "source_license": lic,
            "license": lic if lic.startswith("open") else PERSONAL, "ledger": led, "edges": edges,
            "train_before": train_before, "complete": True, "index": games}
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
    if (argv or sys.argv[1:])[:1] == ["table"]:                # crew A play-table shards
        tp = argparse.ArgumentParser()
        tp.add_argument("cmd")
        tp.add_argument("srcs", nargs="+", type=Path)
        tp.add_argument("--out", type=Path, required=True)
        tp.add_argument("--train-before", default=None)
        tp.add_argument("--track", type=Path, default=None, help="tracking shard joined per event (one src only)")
        b = tp.parse_args(argv)
        infos = [build_table(p, b.out, b.train_before, b.track) for p in b.srcs]
        print(ledger_line(infos))
        return 0
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
