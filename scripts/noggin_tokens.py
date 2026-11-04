#!/usr/bin/env python3
"""Noggin Foundation Model (NFM) event tokens: every play of every game as a short token group, per season.

Serves: North Star "% of hindsight-oracle value achieved" (the NFM is the Noggin's main track, Nick Nov 4 4:45 PM);
owner: lane 1 (Noggin training). Design: docs/noggin-foundation-model.md.

    python scripts/noggin_tokens.py nba RAW/nba/pbp_2025.parquet 2025 OUT

One game = <BOS:league> <HOME:team> <AWAY:team> then per event 6 tokens, in this order:
  KIND    the play's kind (espn._kind), hashed into 2048 slots per base league (stable across shards)
  DT      wall seconds since the previous event, log-bucketed (32 buckets; game-clock delta if no wall)
  CLOCK   period (1..10) x 16 buckets of the period's game clock
  MARGIN  home minus away after the play, clipped to -20..+20
  PTS     points on this play and which side scored (0..7 home / away)
  STOP    what the game is doing right after (models.Stoppage: live, timeout, end of period, review ...)
then <EOS>. Ids fit uint16 (VOCAB_SIZE < 65536). Nights on or after the league's scorecard window are never
tokenized (factory.EVAL_FIRST_NIGHT): the locked nights stay unseen. Shards: <out>/<league>/<year>.npy (uint16,
games back to back) + <year>.json (games: [gid, night, offset, length], tokens, events, vocab version).
"""
from __future__ import annotations

import json
import math
import os
import sys
import zlib
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

VOCAB_VERSION = 1
BASES = ("nfl", "nba", "nhl", "mlb")
LEAGUES = ("nfl", "cfb", "nba", "cbb", "wcbb", "nhl", "mlb")
KIND_SLOTS, TEAM_SLOTS, DT_BUCKETS, CLOCK_BUCKETS, MAX_PERIOD, MARGIN_CAP = 2048, 512, 32, 16, 10, 20
STOPS = ("none", "timeout", "two_minute_warning", "end_period", "halftime", "intermission", "review", "injury",
         "pitching_change", "score_break", "whistle", "unknown")

# id layout: specials | BOS per league | EOS | team | kind | dt | clock | margin | pts | stop
PAD, EOS, UNK_DT = 0, 1, 2
BOS0 = 3
TEAM0 = BOS0 + len(LEAGUES)
KIND0 = TEAM0 + len(BASES) * TEAM_SLOTS
DT0 = KIND0 + len(BASES) * KIND_SLOTS
CLOCK0 = DT0 + DT_BUCKETS
MARGIN0 = CLOCK0 + MAX_PERIOD * CLOCK_BUCKETS
PTS0 = MARGIN0 + 2 * MARGIN_CAP + 1
STOP0 = PTS0 + 16
VOCAB_SIZE = STOP0 + len(STOPS)
TOKENS_PER_EVENT = 6


def _slot(s: str, n: int) -> int:
    return zlib.crc32(s.encode()) % n


def team_token(base: str, abbr: str) -> int:
    return TEAM0 + BASES.index(base) * TEAM_SLOTS + _slot(abbr or "?", TEAM_SLOTS)


def dt_token(dt: float | None) -> int:
    if dt is None or not math.isfinite(dt) or dt < 0:
        return UNK_DT
    return DT0 + min(DT_BUCKETS - 1, int(math.log2(1.0 + dt) * 2.6))     # 0 s .. ~2 h


def encode_game(league: str, plays: list[dict], home: str, away: str) -> np.ndarray:
    """ESPN-format play dicts (the live parser's shape) -> uint16 tokens."""
    from redzone.models import PERIOD_S, League
    from redzone.sources import espn
    lg = League(league)
    base = lg.base.value
    per_s = float(PERIOD_S.get(lg) or 0) or None
    out = [BOS0 + LEAGUES.index(league), team_token(base, home), team_token(base, away)]
    last_w = last_c = None
    prev_h = prev_a = 0
    for p in plays:
        w = espn._ts(p.get("wallclock"))
        per = max(1, min(MAX_PERIOD, espn._int(espn._get(p, "period", "number")) or 1))
        c = espn._clock_s(espn._get(p, "clock", "displayValue"))
        dt = (w - last_w) if w is not None and last_w is not None else (
            abs(last_c - c) if c is not None and last_c is not None else None)
        last_w = w if w is not None else last_w
        last_c = c if c is not None else last_c
        frac = 0.0 if c is None or not per_s else max(0.0, min(0.999, 1.0 - c / per_s))
        hs, as_ = espn._int(p.get("homeScore")), espn._int(p.get("awayScore"))
        hs = prev_h if hs is None else hs
        as_ = prev_a if as_ is None else as_
        dh, da = max(0, hs - prev_h), max(0, as_ - prev_a)
        prev_h, prev_a = hs, as_
        pts = PTS0 + (min(7, dh) if dh else 8 + min(7, da) if da else 0)
        stop = espn.stoppage_for(lg, p).value
        out += [KIND0 + BASES.index(base) * KIND_SLOTS + _slot(espn._kind(lg, p), KIND_SLOTS), dt_token(dt),
                CLOCK0 + (per - 1) * CLOCK_BUCKETS + int(frac * CLOCK_BUCKETS),
                MARGIN0 + MARGIN_CAP + max(-MARGIN_CAP, min(MARGIN_CAP, hs - as_)),
                pts, STOP0 + (STOPS.index(stop) if stop in STOPS else STOPS.index("unknown"))]
    out.append(EOS)
    return np.asarray(out, np.uint16)


CLOSE = {"nba": 6, "nfl": 8, "nhl": 1, "mlb": 2}


def game_tags(league: str, t: np.ndarray) -> dict:
    """Coverage tags for one tokenized game (the data ledger balances on these, Nick 5:00 PM): crunch events (last
    regulation period, last quarter of its clock, within the league's close margin, or OT), OT, a comeback (a lead
    of 10+ (NFL 14, NHL 2, MLB 4) erased), a blowout (final margin 20+ (NHL 4, MLB 6); MARGIN clips at 20, so
    20 is the most a token can show)."""
    from redzone.models import REGULATION, League
    lg = League(league)
    base = lg.base.value
    reg = REGULATION.get(lg, 4)
    ev = t[3:-1][: (len(t) - 4) // TOKENS_PER_EVENT * TOKENS_PER_EVENT].reshape(-1, TOKENS_PER_EVENT)
    if not len(ev):
        return {"events": 0, "crunch_events": 0, "ot": False, "comeback": False, "blowout": False}
    clock = ev[:, 2].astype(int) - CLOCK0
    per, frac = clock // CLOCK_BUCKETS + 1, (clock % CLOCK_BUCKETS) / CLOCK_BUCKETS
    margin = ev[:, 3].astype(int) - MARGIN0 - MARGIN_CAP
    close = CLOSE.get(base, 3)
    crunch = (per > reg) | ((per == reg) & (frac >= 0.75) & (np.abs(margin) <= close))
    big_lead = {"nfl": 14, "nhl": 2, "mlb": 4}.get(base, 10)
    blow = {"nhl": 4, "mlb": 6}.get(base, MARGIN_CAP)
    led_home, led_away = (margin >= big_lead).any(), (margin <= -big_lead).any()
    final = int(margin[-1])
    comeback = bool((led_home and final <= 0) or (led_away and final >= 0))
    return {"events": int(len(ev)), "crunch_events": int(crunch.sum()), "ot": bool((per > reg).any()),
            "comeback": comeback, "blowout": abs(final) >= blow}


def _night(plays: list[dict], g) -> str | None:
    """The game's night: from the first wallclock, else (older seasons with no wall clocks) the game_date column.
    A game with neither is dropped, so no game can slip past the scorecard cut undated."""
    from redzone.noggin import factory as F
    from redzone.sources import espn
    ts = [t for t in (espn._ts(p.get("wallclock")) for p in plays) if t is not None]
    if ts:
        return F.night_of(ts[0])
    import pandas as pd
    d = g.game_date.iloc[0] if "game_date" in g else None
    try:
        return None if d is None or pd.isna(d) else pd.Timestamp(d).strftime("%Y%m%d")
    except (TypeError, ValueError):
        return None


def games_of(league: str, raw: Path):
    """(gid, night, plays, home, away) for every game of a raw season file, the scorecard window dropped."""
    import pandas as pd

    from redzone.noggin import factory as F
    from redzone.sources import espn
    cut = F.EVAL_FIRST_NIGHT.get(league, "99999999")
    raw = Path(raw)
    if str(raw).endswith(".jsonl.gz"):
        import gzip
        with gzip.open(raw, "rt") as f:
            for line in f:
                g = json.loads(line)
                ts = [t for t in (espn._ts(p.get("wallclock")) for p in g["plays"]) if t is not None]
                if not ts:
                    continue
                night = F.night_of(ts[0])
                if night < cut:
                    away, _, home = (g.get("matchup") or "? @ ?").partition(" @ ")
                    yield str(g["id"]), night, g["plays"], home, away
        return
    import pyarrow.parquet as pq
    have = set(pq.read_schema(raw).names)
    if "time_of_day" in have:                       # nflfastR (football)
        df = pd.read_parquet(raw, columns=[c for c in F.NFL_COLS + ["home_team", "away_team", "game_date"]
                                           if c in have])
        df = df[df.qtr.notna()].sort_values(["game_id", "play_id"], kind="stable")
        for gid, g in df.groupby("game_id", sort=False):
            walled = g.time_of_day.map(lambda v: isinstance(v, str)).any()
            plays, _ = F._plays_football(g, need_wall=bool(walled))
            night = _night(plays, g)
            if night is None or night >= cut:
                continue
            home = str(g.home_team.iloc[0]) if "home_team" in g else "?"
            away = str(g.away_team.iloc[0]) if "away_team" in g else "?"
            yield str(gid), night, plays, home, away
        return
    cols = ["id", "sequence_number", "type_text", "text", "period_number", "clock_display_value", "wallclock",
            "scoring_play", "score_value", "home_score", "away_score", "game_id", "home_team_abbrev", "away_team_abbrev", "game_date"]
    df = pd.read_parquet(raw, columns=[c for c in cols if c in have])
    df["sq"] = pd.to_numeric(df.sequence_number, errors="coerce")
    for gid, g in df.sort_values(["game_id", "sq"], kind="stable").groupby("game_id", sort=False):
        plays = F._plays_basketball(g)
        night = _night(plays, g)
        if night is None or night >= cut:
            continue
        yield (str(gid), night, plays, str(g.home_team_abbrev.iloc[0]) if "home_team_abbrev" in g else "?",
               str(g.away_team_abbrev.iloc[0]) if "away_team_abbrev" in g else "?")


def source_of(league: str, raw: Path) -> str:
    """Which open source a raw season came from (the data ledger's 'source' column)."""
    n = Path(raw).name
    if n.endswith(".jsonl.gz"):
        return "espn-site-api"
    return {"nfl": "nflverse", "cfb": "nflverse"}.get(league, "sportsdataverse-hoopR" if league in ("nba", "cbb")
                                                      else "sportsdataverse-wehoop" if league == "wcbb" else "pbp")


def build(league: str, raw: Path, year: int, out: Path) -> dict:
    """One season's token shard; an existing complete shard is reused (resume)."""
    d = Path(out) / league
    js = d / f"{year}.json"
    if js.exists():
        info = json.loads(js.read_text())
        if info.get("complete"):
            return info
    d.mkdir(parents=True, exist_ok=True)
    parts, games, off = [], [], 0
    cov = {"crunch_events": 0, "ot_games": 0, "comeback_games": 0, "blowout_games": 0, "no_wall_games": 0}
    for gid, night, plays, home, away in games_of(league, raw):
        if len(plays) < 10:
            continue
        t = encode_game(league, plays, home, away)
        tg = game_tags(league, t)
        cov["crunch_events"] += tg["crunch_events"]
        cov["ot_games"] += tg["ot"]
        cov["comeback_games"] += tg["comeback"]
        cov["blowout_games"] += tg["blowout"]
        cov["no_wall_games"] += not any(p.get("wallclock") for p in plays)   # DT from the game clock (risk §8)
        parts.append(t)
        games.append([gid, night, off, int(len(t)), int(tg["ot"]) | int(tg["comeback"]) << 1 | int(tg["blowout"]) << 2])
        off += len(t)
    arr = np.concatenate(parts) if parts else np.zeros(0, np.uint16)
    np.save(d / f"{year}.npy.tmp", arr)
    os.replace(d / f"{year}.npy.tmp.npy", d / f"{year}.npy")
    info = {"league": league, "season": year, "tokens": int(len(arr)), "games": len(games),
            "events": int(sum((g[3] - 4) // TOKENS_PER_EVENT for g in games)), "vocab_version": VOCAB_VERSION,
            "real": True,
            "vocab_size": VOCAB_SIZE, "source": source_of(league, raw), "coverage": cov, "complete": True,
            "index": games}     # index rows: [gid, night, offset, length, tags bits: 1 OT, 2 comeback, 4 blowout]
    js.write_text(json.dumps(info))
    return info


def main(argv=None) -> int:
    a = argv or sys.argv[1:]
    info = build(a[0], Path(a[1]), int(a[2]), Path(a[3]))
    print(json.dumps({k: v for k, v in info.items() if k != "index"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
