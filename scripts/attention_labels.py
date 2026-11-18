#!/usr/bin/env python3
"""Data G: per-play human-attention labels for Watch Value (EXP-1704), plus the WP-only drama baseline.

Real-data sources (each is a per-team time series of z-scores; no source is ever a model INPUT):
  bsky          Bluesky mention counts per minute (G, attention-out/bsky), z vs the term's own day baseline
  wiki_edits    Wikipedia edit rate per 15 min (B, data-b-out facts `edit_rate_live_z`)
  wiki_views    Wikipedia pageviews per hour (B, `pageviews_live_z`)
  yt_velocity   highlight view velocity per play (C, media-out), when it lands
  mkt_volume    prediction-market volume spikes (D ticks; Nick 9:05 AM Nov 5 named market volume as a label
                source; prices themselves stay inputs only)

For play i at wall time t, each source's value is the max z of either team over the source's window
(t + lag, t + lag + width]; the play's attention is the mean over the sources that cover it, then ranked
within the game to [0, 1] (wv_aux_label.rank01). Coarse sources (hourly pageviews) cannot separate plays a
few minutes apart; that is why each source is graded on its own first (one source per run).

Derived baseline (novelty 0, arm A3): research drama math on our own WP.
  surprise_i   = |WP_i - WP_{i-1}|  (Ely, Frankel & Kamenica 2015: belief change)
  suspense_i   = expected |WP_{i+1} - WP_i| given (WP bucket, time-left bucket), fit on TRAIN nights only
  drama_i      = rank01(suspense) / 2 + rank01(surprise) / 2
A human-attention label must beat this arm, or it adds nothing we could compute from play-by-play.
"""
from __future__ import annotations

import bisect
import gzip
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Iterable

import numpy as np

try:
    from scripts.wv_aux_label import rank01
except ImportError:  # run as a file
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from wv_aux_label import rank01  # type: ignore

# (lag_s, width_s): attention trails the moment (feed and broadcast delay, people reacting).
WINDOWS = {
    "bsky": (0.0, 300.0),
    "wiki_edits": (0.0, 900.0),
    "wiki_views": (0.0, 3600.0),
    "yt_velocity": (0.0, 60.0),
    "mkt_volume": (0.0, 120.0),
}


class Series:
    """One source's per-team z series: team -> sorted (ts, z)."""

    def __init__(self, source: str) -> None:
        self.source = source
        self.ts: dict[str, list[float]] = defaultdict(list)
        self.z: dict[str, list[float]] = defaultdict(list)

    def add(self, team: str, ts: float, z: float) -> None:
        self.ts[team].append(float(ts))
        self.z[team].append(float(z))

    def finish(self) -> "Series":
        for t in list(self.ts):
            o = sorted(range(len(self.ts[t])), key=self.ts[t].__getitem__)
            self.ts[t] = [self.ts[t][k] for k in o]
            self.z[t] = [self.z[t][k] for k in o]
        return self

    def window_max(self, team: str, lo: float, hi: float) -> float:
        ts = self.ts.get(team)
        if not ts:
            return math.nan
        a, b = bisect.bisect_right(ts, lo), bisect.bisect_right(ts, hi)
        return max(self.z[team][a:b]) if b > a else math.nan


def play_attention(rows: list[dict], teams: tuple[str, ...], series: list[Series]) -> np.ndarray:
    """rows of ONE game (wall in epoch s) -> raw attention per play (mean of covering sources' z), NaN if none."""
    out = np.full(len(rows), np.nan)
    for i, r in enumerate(rows):
        t = r.get("wall")
        if t is None:
            continue
        vals = []
        for s in series:
            lag, width = WINDOWS[s.source]
            got = [v for v in (s.window_max(tm, t + lag, t + lag + width) for tm in teams) if not math.isnan(v)]
            if got:
                vals.append(max(got))
        if vals:
            out[i] = float(np.mean(vals))
    return out


def attention_label(rows: list[dict], teams: tuple[str, ...], series: list[Series]) -> np.ndarray:
    """The plug-in label for wv_aux_label: within-game rank of attention, NaN where no source covers."""
    raw = play_attention(rows, teams, series)
    ok = np.isfinite(raw)
    if ok.sum() and np.ptp(raw[ok]) == 0:   # flat over the game: no information, leave it uncovered
        return np.full(len(rows), np.nan)
    return rank01(raw)


# ---------------------------------------------------------------- loaders
def robust_z(x: np.ndarray) -> np.ndarray:
    med = np.median(x)
    mad = np.median(np.abs(x - med)) * 1.4826 or 1.0
    return (x - med) / mad


def load_bsky(paths: list[Path], term_to_teams: dict[str, list[str]]) -> Series:
    """Bluesky minute rows -> per-team z of log1p(term count / posts_total * 1e4), vs that term's day baseline.
    Normalizing by posts_total removes the platform's own daily traffic curve."""
    per_term: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for p in paths:
        with gzip.open(p, "rt") as f:
            rows = [json.loads(line) for line in f]
        for r in rows:
            ts = _minute_ts(r["minute_utc"])
            tot = max(1, r.get("posts_total") or 1)
            for term in term_to_teams:
                c = (r.get("terms") or {}).get(term, 0)
                per_term[term].append((ts, math.log1p(c / tot * 1e4)))
    s = Series("bsky")
    for term, pts in per_term.items():
        ts = np.array([p[0] for p in pts])
        z = robust_z(np.array([p[1] for p in pts]))
        for team in term_to_teams[term]:
            for a, b in zip(ts, z):
                s.add(team, a, b)
    return s.finish()


def load_b_facts(rows: Iterable[dict], field: str, source: str) -> Series:
    """B's fact shard rows (game_id, team, asof_utc, field, value) -> Series of that field."""
    s = Series(source)
    for r in rows:
        if r.get("field") == field and r.get("value") is not None:
            s.add(r["team"], _iso_ts(r["asof_utc"]), float(r["value"]))
    return s.finish()


def _minute_ts(m: str) -> float:
    from datetime import datetime, timezone
    return datetime.strptime(m, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).timestamp()


def _iso_ts(s: str) -> float:
    from datetime import datetime
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


# ---------------------------------------------------------------- drama math (derived, arm A3)
def _bucket(wp: float, left_frac: float) -> tuple[int, int]:
    return min(9, int(wp * 10)), min(4, int(max(0.0, left_frac) * 5))


def fit_suspense(train_games: list[list[dict]]) -> dict[tuple[int, int], float]:
    """Mean next-play |dWP| per (WP decile, time-left fifth). TRAIN nights only. Rows need wp_home, left_frac."""
    acc: dict[tuple[int, int], list[float]] = defaultdict(list)
    for rows in train_games:
        for a, b in zip(rows, rows[1:]):
            if a.get("wp_home") is None or b.get("wp_home") is None or a.get("left_frac") is None:
                continue
            acc[_bucket(a["wp_home"], a["left_frac"])].append(abs(b["wp_home"] - a["wp_home"]))
    return {k: float(np.mean(v)) for k, v in acc.items() if len(v) >= 20}


def drama_label(rows: list[dict], table: dict[tuple[int, int], float]) -> np.ndarray:
    n = len(rows)
    sus, sur = np.full(n, np.nan), np.full(n, np.nan)
    for i, r in enumerate(rows):
        if r.get("wp_home") is None:
            continue
        if r.get("left_frac") is not None:
            sus[i] = table.get(_bucket(r["wp_home"], r["left_frac"]), np.nan)
        if i and rows[i - 1].get("wp_home") is not None:
            sur[i] = abs(r["wp_home"] - rows[i - 1]["wp_home"])
    a, b = rank01(sus), rank01(sur)
    both = np.where(np.isfinite(a) & np.isfinite(b), (a + b) / 2, np.where(np.isfinite(a), a, b))
    return both

