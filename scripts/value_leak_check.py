#!/usr/bin/env python3
"""Leak check for GameValue shards: exit 1 if any row is from a locked or fresh night or reads past its decision.

    python scripts/value_leak_check.py <shards dir> [--archive data/archive] [--raw <dir>] [--allow-personal-nba] [--json]

Why: a model trained on one locked night or one future feature makes every later gate a lie, and the shard builder
cannot be trusted to grade itself (the-mind section 9, build plan WP-25). So this file does not import
redzone/noggin/value_shards.py: the column allow-list and the licence rules are its own, and a test keeps the two
in step. Night truth comes from redzone/noggin/factory.py (public cut) and scripts/coverage_audit.DevOnly (archive).

Fails closed: no shards, a missing sidecar, a missing splits file, or an archive row without an archive to check
it against are all FAIL. Archive files for locked games are never opened: only scoreboards are read, to know the split.

Features v4 get their own raw recomputation below (_raw_state): the drive state from nflverse's columns with this file's rules, and the
league-relative numbers from this file's own constants, so a slip in gamevalue.state_arrays or a planted future shows up as a difference.
Features v5 add kept wide columns (redzone/noggin/wide_features.py): the raw audit recomputes the sum-of-points, plays and scoring-plays ones from raw rows here (WIDE_RAW_RE),
and the prefix rebuild compares every kept column on a cut-off feed, the check that catches any column reading the future.

Stamps and the prefix rebuild only see what the shard builder and its adapter wrote, so a future fact an adapter put into a
per-play field passes them. `--raw` is the check that catches it: for a sample of rows per game it reads the head play's
fields straight from the raw source (not through the adapter) and recomputes the label from holdset's separate detector
counting only events strictly after decision_t. scripts/kaggle_gamevalue.py refuses to train unless this ran.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from redzone.moments.detect import clock_s  # noqa: E402
from redzone.noggin import factory  # noqa: E402
from scripts import holdset, holdset_public  # noqa: E402
from scripts.coverage_audit import DevOnly, LockedNightError  # noqa: E402

FEATURES = ("period", "clock_left", "game_left", "is_ot", "late", "abs_margin", "total_points", "pts_5m", "swing_5m",
            "lead_changes", "plays_5m", "since_head_s", "since_score_s", "since_start_s", "t1_5m", "t2_5m", "t3_5m",
            "t1_game", "value_10m", "down", "distance", "yards_to_goal", "playoff")
LABELS = ("value_next_3m", "p_tier1_60s")
FEATURES_VERSION = "v2"  # value_shards' default FEATURES_VERSION: older shards anchor the label on the feed head
HISTORY = ("pts_last3", "pts_last5", "swing_last5", "run_pts", "run_by_trailing", "to_5m", "fouls_5m", "plays_2m",
           "sec_per_play_10", "timeouts_period", "since_timeout_s", "timeout_recent")
DRIVE = ("drive_plays", "drive_yards", "drive_clock_s", "drive_start_ytg", "rz_reached", "next_down", "next_distance", "poss_flip",
         "half_left", "two_minute", "timeouts_half")
DERIVED = ("margin_class", "closeness", "crunch_eta_s", "league_id", "game_frac_left", "margin_scores", "margin_z", "pace_rel")
FEATURES_BY_VERSION = {"v2": FEATURES, "v3": FEATURES + HISTORY, "v4": FEATURES + HISTORY + DRIVE + DERIVED, "v5": FEATURES + HISTORY + DRIVE + DERIVED}
V4_LIKE = ("v4", "v5")   # versions built on the drive state and the feed_row filter
# v5 adds the kept columns of redzone/noggin/wide_features.py. The grammar is restated here so this file still does not import the builder; a test keeps the two in step.
WIDE_RE = re.compile(r"^gv5__[a-z0-9_]+?__(all|home|away|lead|trail)__(p[0-9]+|s[0-9]+|game)__(sum|mean|rate|nz|max|std|slope|cum|per|since_s|since_p|streak)__(raw|lg|log)__"
                     r"(none|close|late|crunch|last|margin|left|lead|down3|rz|short)$")
WIDE_RAW_RE = re.compile(r"^gv5__(pts|plays|scoring)__(all|home|away)__(p[0-9]+|s[0-9]+)__sum__raw__none$")     # the kept columns the raw audit recomputes itself
WIDE_RING = 256          # wide_features.RING: no window reaches further back than this many plays
# league -> (points in one score, final-margin sd, points per 5 min, regulation seconds, league id): restated, and a test keeps it in step.
SCALE = {"nfl": (7.0, 13.5, 3.75, 3600.0, 0.0), "nba": (2.5, 12.0, 24.0, 2880.0, 2.0), "cbb": (2.5, 11.0, 18.0, 2400.0, 3.0)}
PERIODS = {"nfl": (4, 900.0), "nba": (4, 720.0), "cbb": (2, 1200.0)}
META = ("league", "season", "game_id", "game_key", "night", "source_night_id", "source", "license", "plan",
        "decision_t", "feed_cut_t", "feed_head_t", "feature_t_max", "lag_s", "label_full_window", "label_first_t")
ALLOWED = frozenset(META + FEATURES + LABELS)


def allowed_for(version: str, wide: tuple = ()) -> frozenset:
    return frozenset(META + FEATURES_BY_VERSION[version] + tuple(wide) + LABELS)


PUBLIC_SOURCES = {"nflverse_pbp": "nfl"}
PERSONAL_SOURCES = {"hoopr_espn_nba": "nba", "hoopr_espn_cbb": "cbb", "espn_cfb": "cfb", "wehoop_espn_wnba": "wnba"}
PERSONAL_TAG = "personal-use, not for commercial release"
COPY_R = 0.995          # a feature this correlated with a label is the label
MIN_ROWS_COPY = 200
EPS = 1e-3              # seconds: float rounding on the stored times
HORIZON_S, TIER1_S = 180.0, 60.0
RAW_ROWS = 40           # rows per game re-read from the raw source
RAW_FIELDS = ("period", "clock_left", "down", "distance", "yards_to_goal", "abs_margin", "total_points")


class Report:
    def __init__(self):
        self.fails: list[str] = []
        self.checked = {"shards": 0, "rows": 0, "prefix_rows": 0, "raw_rows": 0}

    def fail(self, check: str, msg: str):
        self.fails.append(f"{check}: {msg}")


def _archive_gate(archive: Path | None, league: str, cache: dict):
    if league not in cache:
        try:
            if archive is None:
                raise LockedNightError("no --archive given")
            cache[league] = DevOnly(archive, league).game_ids()
        except LockedNightError as e:
            cache[league] = e
    return cache[league]


def check_times(df: pd.DataFrame, name: str, rep: Report):
    """Every feature must be older than its decision; the stored stamps have to say so and agree with each other."""
    late = int((df.feature_t_max > df.decision_t + EPS).sum())
    if late:
        rep.fail("future_feature", f"{name}: {late} rows have a feature newer than the decision")
    past_cut = int((df.feature_t_max > df.feed_cut_t + EPS).sum())
    if past_cut:
        rep.fail("future_feature", f"{name}: {past_cut} rows read past their feed cut")
    if int((np.abs(df.feature_t_max - df.feed_head_t) > EPS).sum()):
        rep.fail("future_feature", f"{name}: feature_t_max differs from the feed head")
    bad_lag = int(((df.lag_s < 0) | (np.abs(df.decision_t - df.lag_s - df.feed_cut_t) > EPS)).sum())
    if bad_lag:
        rep.fail("future_feature", f"{name}: {bad_lag} rows where decision - lag is not the feed cut")
    first, decision = df.label_first_t.to_numpy(float), df.decision_t.to_numpy(float)
    has = ~np.isnan(first)
    early = int((first[has] <= decision[has]).sum())
    if early:
        rep.fail("label_window", f"{name}: {early} rows count a label event at or before the decision")
    late_ev = int((first[has] > decision[has] + HORIZON_S + EPS).sum())
    if late_ev:
        rep.fail("label_window", f"{name}: {late_ev} rows count a label event past the 180 s window")
    if int((has != (df.value_next_3m.to_numpy(float) > 0)).sum()):
        rep.fail("label_window", f"{name}: a label and its first event disagree about whether anything happened")


def check_nights(df: pd.DataFrame, league: str, name: str, archive: Path | None, cache: dict, allow_nba: bool, rep: Report):
    """Locked and fresh nights, the night id on every row, and the licence of the source."""
    missing = int(df.source_night_id.isna().sum()) + int((df.source_night_id.astype(str).str.strip() == "").sum())
    if missing:
        rep.fail("source_night_id", f"{name}: {missing} rows have no source_night_id")
    wrong = int((df.source_night_id.astype(str) != league + ":" + df.night.astype(str)).sum()) - missing
    if wrong > 0:
        rep.fail("source_night_id", f"{name}: {wrong} rows whose source_night_id is not {league}:<night>")
    if (df.league.astype(str) != league).any():
        rep.fail("source_night_id", f"{name}: a row of another league in this folder")
    for source, part in df.groupby("source", dropna=False):
        if source == "archive":
            gate = _archive_gate(archive, league, cache)
            if isinstance(gate, Exception):
                rep.fail("locked_night", f"{name}: archive rows but no split to check them against ({gate})")
                continue
            games = part[["game_id", "night"]].drop_duplicates()
            bad = [(g, n) for g, n in zip(games.game_id.astype(str), games.night.astype(str)) if gate.get(g) != n]
            if bad:
                rep.fail("locked_night", f"{name}: {len(bad)} archive games on a locked, fresh or wrong night, e.g. {bad[:3]}")
            continue
        cut = factory.EVAL_FIRST_NIGHT.get(league)
        if cut is None:
            rep.fail("locked_night", f"{name}: no public cut known for {league}")
            continue
        late = sorted(part.night[part.night.astype(str) >= cut].astype(str).unique())
        if late:
            rep.fail("locked_night", f"{name}: public rows on or after the eval window {cut}: {late[:3]}")
        if PUBLIC_SOURCES.get(source) == league:
            pass
        elif allow_nba and PERSONAL_SOURCES.get(source) == league:
            if not part.license.astype(str).str.contains(PERSONAL_TAG, regex=False).all():
                rep.fail("licence", f"{name}: personal-use source without the personal-use tag")
        else:
            rep.fail("licence", f"{name}: source {source!r} is not cleared for {league}")
        first = part.groupby("game_id").agg(head=("feed_head_t", "min"), night=("night", "first"))
        wrong_night = [g for g, r in first.iterrows() if factory.night_of(float(r["head"])) != str(r["night"])]
        if wrong_night:
            rep.fail("locked_night", f"{name}: {len(wrong_night)} games whose night is not their wall-clock night, "
                                     f"e.g. {wrong_night[:3]}")


def _raw_clock(v) -> float:
    """The feature's own parser (redzone.moments.detect.clock_s), -1 when there is no clock: one parser, so the raw audit
    can only disagree when the value does. The comparison stays exact, so a clock one second off fails."""
    c = clock_s({"clock": {"displayValue": v if isinstance(v, str) else ""}})
    return -1.0 if c is None else float(c)


def _n(v, missing: float = 0.0) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return missing
    return missing if x != x else x


def _raw_plays(league: str, g: pd.DataFrame) -> dict[float, list[dict]]:
    """{wall: [what each raw play says]} read from the raw columns, not through holdset_public's adapter."""
    out: dict[float, list[dict]] = {}
    for r in g.itertuples(index=False):
        wall = holdset.ts(r.time_of_day if league == "nfl" else r.wallclock)
        if not wall:
            continue
        if league == "cfb":      # ESPN college football, canonical columns (holdset_public.CFB_MAP)
            home, away = _n(r.home_score), _n(r.away_score)
            play = {"period": _n(r.period_number, -1.0), "clock_left": _raw_clock(r.clock_display_value),
                    "clock_text": r.clock_display_value, "down": _n(r.start_down), "distance": _n(r.start_distance),
                    "yards_to_goal": _n(r.end_yards_to_endzone, -1.0)}
        elif league == "nfl":
            home, away = _n(r.total_home_score), _n(r.total_away_score)
            yl = _n(r.yardline_100, None)
            play = {"period": _n(r.qtr, -1.0), "clock_left": _raw_clock(r.time), "clock_text": r.time, "down": _n(r.down), "distance": _n(r.ydstogo),
                    "yards_to_goal": -1.0 if yl is None else max(0.0, yl - _n(r.yards_gained))}
        else:
            home, away = _n(r.home_score), _n(r.away_score)
            play = {"period": _n(r.period_number, -1.0), "clock_left": _raw_clock(r.clock_display_value),
                    "clock_text": r.clock_display_value, "down": -1.0,
                    "distance": -1.0, "yards_to_goal": -1.0}
        out.setdefault(wall, []).append({**play, "abs_margin": abs(home - away), "total_points": home + away})
    return out


def _raw_kind(league: str, type_text: str, text: str) -> str:
    """The raw play's kind: its own rule here, not gamevalue.play_kind, so a slip in either shows up as a difference."""
    t = type_text.lower()
    if "timeout" in t:
        return "timeout"
    if league in ("nfl", "cfb"):
        return "turnover" if ("interception" in t or "fumble recovery (opponent)" in t or "fumble return" in t) else "foul" if "penalty" in t else ""
    low = (text or "").lower()
    if "turnover" in t or "turnover" in low or any(v in t for v in ("traveling", "shot clock", "backcourt", "palming", "double dribble", "kicked ball", "8 second")):
        return "turnover"
    return "foul" if "foul" in t else ""


def _one_source_only(kind_text: str, text: str) -> bool:
    """v4: a football row only one feed has (official timeouts, quarter ends, the two-minute warning): this file's list of them."""
    k = kind_text.lower()
    return any(w in k for w in ("official timeout", "end period", "end of half", "end of game", "end game", "two-minute warning")) or ("timeout" in k and "official" in text.lower())


def _raw_history(league: str, g: pd.DataFrame, version: str = FEATURES_VERSION) -> dict[float, list[dict]]:
    """{wall: [v3 history features]} recomputed from the raw rows in wall order. The score, run and event logic is this file's own.
    NFL rows the shards never see (extra points and two-point tries fold into the touchdown; a row with no ESPN type) are skipped."""
    plays = []
    for r in g.itertuples(index=False):
        if league == "nfl":
            wall = holdset.ts(r.time_of_day) if isinstance(r.time_of_day, str) else None
            if r.play_type == "extra_point" or r.two_point_attempt == 1:
                continue
            kind_text = factory._nfl_type(r)
            if not kind_text or not wall or (version in V4_LIKE and _one_source_only(kind_text, r.desc if isinstance(r.desc, str) else "")):
                continue
            plays.append((wall, _n(r.total_home_score), _n(r.total_away_score), _raw_kind(league, kind_text, ""), r.qtr))
        elif league == "cfb":
            wall = holdset.ts(r.wallclock)
            if wall:
                plays.append((wall, _n(r.home_score), _n(r.away_score), _raw_kind(league, r.type_text if isinstance(r.type_text, str) else "", ""), r.period_number))
        else:
            wall = holdset.ts(r.wallclock)
            if wall:
                plays.append((wall, _n(r.home_score), _n(r.away_score), _raw_kind(league, r.type_text if isinstance(r.type_text, str) else "", r.text if isinstance(r.text, str) else ""), r.period_number))
    plays.sort(key=lambda x: x[0])
    out: dict[float, list[dict]] = {}
    home = away = run = 0.0
    team, period, in_period = 0, None, 0
    walls, totals, margins, kinds = [], [], [], []
    last_to = None

    def recent(i: int, span: float, k: str | None) -> float:
        n, j, end = 0, i, walls[i]
        while j >= 0 and walls[j] > end - span:
            n += k is None or kinds[j] == k
            j -= 1
        return float(n)

    for i, (wall, h, a, kind, per) in enumerate(plays):
        if h < home or a < away:
            run, team = 0.0, 0
        elif h > home or a > away:
            scorer = 1 if h - home >= a - away else -1
            run, team = (run + h - home + a - away, scorer) if scorer == team else (h - home + a - away, scorer)
        home, away = h, a
        if per != period:
            period, in_period = per, 0
        in_period += kind == "timeout"
        walls.append(wall); totals.append(home + away); margins.append(home - away); kinds.append(kind)
        back = lambda arr, n: arr[i - n] if i >= n else 0.0  # noqa: E731
        if kind == "timeout":
            last_to = wall
        out.setdefault(wall, []).append({
            "pts_last3": totals[i] - back(totals, 3), "pts_last5": totals[i] - back(totals, 5), "swing_last5": abs(margins[i] - back(margins, 5)),
            "run_pts": run, "run_by_trailing": float(team != 0 and team * (home - away) < 0), "to_5m": recent(i, 300.0, "turnover"), "fouls_5m": recent(i, 300.0, "foul"),
            "plays_2m": recent(i, 120.0, None), "sec_per_play_10": (wall - walls[i - 10]) / 10.0 if i >= 10 else -1.0,
            "timeouts_period": float(in_period), "since_timeout_s": wall - last_to if last_to is not None else -1.0,
            "timeout_recent": float("timeout" in kinds[max(i - 1, 0):i + 1])})
    return out


def _raw_nfl_state(g: pd.DataFrame) -> list[tuple[float, dict]]:
    """(wall, drive state after the play) from nflverse's columns, walked here with this file's own rules."""
    rows = []
    for r in g.itertuples(index=False):
        kind = factory._nfl_type(r).lower()
        wall = holdset.ts(r.time_of_day) if isinstance(r.time_of_day, str) else None
        desc = (r.desc if isinstance(r.desc, str) else "").lower()
        if r.play_type == "extra_point" or r.two_point_attempt == 1 or not kind or not wall or _one_source_only(kind, desc):
            continue
        rows.append((wall, kind, desc, r))
    rows.sort(key=lambda x: x[0])
    out, fresh, snaps, begin, begin_left, reached = [], True, 0, -1.0, -1.0, False
    ytg, nd, nx, half, tos = -1.0, -1.0, -1.0, None, 0.0
    for wall, kind, desc, r in rows:
        q, clock = _n(r.qtr, -1.0), _raw_clock(r.time)
        down, togo, yl, gained = _n(r.down), _n(r.ydstogo), _n(r.yardline_100, -1.0), _n(r.yards_gained)
        this_half = (q + 1) // 2 if 1 <= q <= 4 else q
        if this_half != half:
            half, tos = this_half, 0.0
        timeout = "timeout" in kind
        tos += timeout
        left = -1.0 if clock < 0 else (clock if q > 4 else (4 - q) * 900.0 + clock)
        half_left = -1.0 if clock < 0 else (clock if q > 4 else clock + (900.0 if q in (1, 3) else 0.0))
        gone = False
        if "kickoff" in kind:
            fresh, gone, ytg, nd, nx, snaps, begin, begin_left, reached = True, True, -1.0, -1.0, -1.0, 0, -1.0, -1.0, False
        elif not timeout:
            if fresh:
                fresh, snaps, begin, begin_left, reached = False, 0, -1.0, -1.0, False
            snaps += 1
            if begin < 0 and yl >= 0:
                begin, begin_left = yl, left
            dead_play = "no play" in desc
            if not dead_play and "punt" not in kind and down and 0 < yl <= 20:
                reached = True
            kicked = "punt" in kind or "field goal" in kind
            flag = "penalty" in kind
            scored = "touchdown" in kind or (r.sp == 1 and "touchdown" in desc and not dead_play)
            lost = not dead_play and any(w in kind for w in ("interception", "fumble recovery (opponent)", "fumble return", "blocked", "muffed", "opp fumble recovery")) or (not dead_play and kind == "safety")
            scrimmage = bool(down) and not kicked and not dead_play and not flag
            gone = kicked or scored or lost or (scrimmage and down == 4 and gained < togo)
            if gone:
                ytg, nd, nx, fresh = -1.0, -1.0, -1.0, True
            elif scrimmage:
                after = -1.0 if yl < 0 else max(0.0, yl - gained)
                ytg = after
                nd, nx = (1.0, min(10.0, after) if after > 0 else 10.0) if gained >= togo else (down + 1, togo - gained)
        out.append((wall, {"yards_to_goal": ytg, "drive_plays": float(snaps) if snaps else -1.0, "drive_start_ytg": begin,
                           "drive_yards": max(begin - ytg, 0.0) if begin >= 0 and ytg >= 0 else -1.0,
                           "drive_clock_s": max(begin_left - left, 0.0) if begin_left >= 0 and left >= 0 and snaps else -1.0,
                           "rz_reached": float(reached), "next_down": nd, "next_distance": nx, "poss_flip": float(gone), "half_left": half_left,
                           "two_minute": float(q in (2.0, 4.0) and 0 <= clock <= 120), "timeouts_half": tos}))
    return out


def _raw_state(league: str, g: pd.DataFrame) -> dict[float, list[dict]]:
    """{wall: [v4 features]} from the raw rows: football's drive state and every league's closeness and league-relative numbers."""
    unit, sd, rate, total, lid = SCALE[league]
    regs, length = PERIODS[league]
    timed = []
    for r in g.itertuples(index=False):
        wall = holdset.ts(r.time_of_day if league == "nfl" else r.wallclock) if isinstance(r.time_of_day if league == "nfl" else r.wallclock, str) else None
        if not wall:
            continue
        if league == "nfl":
            kind = factory._nfl_type(r)
            if r.play_type == "extra_point" or r.two_point_attempt == 1 or not kind or _one_source_only(kind, r.desc if isinstance(r.desc, str) else ""):
                continue
            timed.append((wall, _n(r.qtr, -1.0), _raw_clock(r.time), _n(r.total_home_score), _n(r.total_away_score)))
        else:
            timed.append((wall, _n(r.period_number, -1.0), _raw_clock(r.clock_display_value), _n(r.home_score), _n(r.away_score)))
    timed.sort(key=lambda x: x[0])
    drive_by: dict[float, list[dict]] = {}
    if league == "nfl":
        for w, d in _raw_nfl_state(g):
            drive_by.setdefault(w, []).append(d)
    walls = np.array([t[0] for t in timed], float)
    total_now = np.array([t[3] + t[4] for t in timed], float)
    out: dict[float, list[dict]] = {}
    seen: dict[float, int] = {}
    for i, (wall, per, clock, h, a) in enumerate(timed):
        margin = abs(h - a)
        before = int(np.searchsorted(walls, wall - 300.0, "right")) - 1
        pts = total_now[i] - (total_now[before] if before >= 0 else 0.0)
        left = -1.0 if clock < 0 else (clock if per > regs else (regs - per) * length + clock)
        since = wall - walls[0]
        cls = 0.0 if margin == 0 else 1.0 if margin <= 3 else 2.0 if margin <= 8 else 3.0 if margin <= 16 else 4.0 if margin <= 24 else 5.0
        frac = min(max(left, 0.0) / total, 1.0)
        row = {"margin_class": cls, "league_id": lid, "margin_scores": margin / unit, "pace_rel": pts / rate,
               "closeness": margin / max(left, 30.0) ** 0.5 if left >= 0 else -1.0,
               "crunch_eta_s": min(max(left - 300.0, 0.0) * since / max(total - left, 60.0), 21600.0) if left >= 0 else -1.0,
               "game_frac_left": frac if left >= 0 else -1.0,
               "margin_z": margin / (sd * max(frac, 0.02) ** 0.5) if left >= 0 else -1.0}
        if league == "nfl":
            nth = seen.get(wall, 0)
            seen[wall] = nth + 1
            row.update(drive_by[wall][nth])
        else:
            row.update({f: 0.0 if f in ("rz_reached", "poss_flip", "two_minute", "timeouts_half") else -1.0 for f in DRIVE})
        out.setdefault(wall, []).append(row)
    return out


def _raw_wide(league: str, g: pd.DataFrame, names: list[str]) -> dict[float, list[dict]]:
    """{wall: [{wide column: value}]} for the kept columns WIDE_RAW_RE covers, from the raw rows with this file's own walk: last-N-plays or last-T-seconds sums of
    points, plays and scoring plays, per side. The other kept columns are covered by the prefix rebuild, which compares every column on a cut-off feed."""
    plays = []
    for r in g.itertuples(index=False):
        if league == "nfl":
            wall = holdset.ts(r.time_of_day) if isinstance(r.time_of_day, str) else None
            if r.play_type == "extra_point" or r.two_point_attempt == 1:
                continue
            kind_text = factory._nfl_type(r)
            if not kind_text or not wall or _one_source_only(kind_text, r.desc if isinstance(r.desc, str) else ""):
                continue
            plays.append((wall, _n(r.total_home_score), _n(r.total_away_score)))
        else:
            wall = holdset.ts(r.wallclock)
            if wall and not (league == "cfb" and _one_source_only(r.type_text if isinstance(r.type_text, str) else "", r.text if isinstance(r.text, str) else "")):
                plays.append((wall, _n(r.home_score), _n(r.away_score)))
    plays.sort(key=lambda x: x[0])
    per_play = []
    home = away = 0.0
    for wall, h, a in plays:
        dh, da = max(h - home, 0.0), max(a - away, 0.0)
        home, away = h, a
        per_play.append({"pts": {"all": dh + da, "home": dh, "away": da}, "plays": {"all": 1.0}, "scoring": {"all": float(dh > 0) + float(da > 0), "home": float(dh > 0), "away": float(da > 0)}})
    out: dict[float, list[dict]] = {}
    for i, (wall, _, _) in enumerate(plays):
        row = {}
        for name in names:
            match = WIDE_RAW_RE.match(name)
            stat, side, window = match.group(1), match.group(2), match.group(3)
            span = int(window[1:])
            first = max(i - WIDE_RING + 1, 0)
            if window[0] == "p":
                first = max(first, i - span + 1)
            else:
                while first < i and plays[first][0] <= wall - span:
                    first += 1
            row[name] = sum(per_play[j][stat].get(side, 0.0) for j in range(first, i + 1))
        out.setdefault(wall, []).append(row)
    return out


def _same(a, b) -> bool:
    """Close enough for float32. A NaN in the shard is no number at all: the trainer drops that row."""
    if a != a:
        return True
    return bool(np.isclose(float(a), float(b), rtol=1e-5, atol=1e-3))


def _audit_game(league: str, gid: str, g: pd.DataFrame, rows: pd.DataFrame, name: str, rep: Report, version: str = FEATURES_VERSION, wide: tuple = ()):
    """A sample of one game's rows against the raw source: the head play's fields, then the label."""
    make = {"nfl": holdset_public.nfl_summary, "cfb": holdset_public.cfb_summary}.get(league, holdset_public.hoops_summary)
    built = holdset.game_row(league, gid, make(g))
    if built is None:
        rep.fail("raw_audit", f"{name}: game {gid} has shard rows but is not a whole game in the raw source")
        return
    plays, events = _raw_plays(league, g), built[4]
    history = _raw_history(league, g, version) if version != "v2" else {}
    state = _raw_state(league, g) if version in V4_LIKE else {}
    head_fields = tuple(f for f in RAW_FIELDS if not (version in V4_LIKE and league == "nfl" and f == "yards_to_goal"))
    wide_names = [n for n in wide if WIDE_RAW_RE.match(n)]
    wide_raw = _raw_wide(league, g, wide_names) if wide_names else {}
    state_fields = DRIVE + DERIVED + (("yards_to_goal",) if league == "nfl" else ())
    picks = np.unique(np.linspace(0, len(rows) - 1, min(RAW_ROWS, len(rows))).astype(int))
    for r in rows.iloc[picks].itertuples(index=False):
        rep.checked["raw_rows"] += 1
        candidates = plays.get(float(r.feed_head_t))
        if not candidates:
            rep.fail("raw_audit", f"{name}: game {gid} has a feed head at {r.feed_head_t} that no raw play has")
            continue
        if not any(all(_same(getattr(r, f), c[f]) for f in head_fields) for c in candidates):
            diff = [f"{f} {getattr(r, f)} vs raw {candidates[-1][f]}" for f in head_fields if not _same(getattr(r, f), candidates[-1][f])]
            rep.fail("raw_audit", f"{name}: game {gid} feature differs from the raw source: {', '.join(diff[:3])} "
                            f"(raw clock text {candidates[-1]['clock_text']!r})")
        if version != "v2":
            found = history.get(float(r.feed_head_t)) or []
            if not any(all(_same(getattr(r, f), c[f]) for f in HISTORY) for c in found):
                wrong = [f"{f} {getattr(r, f)} vs raw {found[-1][f]}" for f in HISTORY if found and not _same(getattr(r, f), found[-1][f])]
                rep.fail("raw_audit", f"{name}: game {gid} history differs from the raw source: {', '.join(wrong[:3]) or 'no raw play at the head'}")
        if wide_names:
            rep.checked["raw_wide_columns"] = max(rep.checked.get("raw_wide_columns", 0), len(wide_names))
            found = wide_raw.get(float(r.feed_head_t)) or []
            if not any(all(_same(getattr(r, f), c[f]) for f in wide_names) for c in found):
                wrong = [f"{f} {getattr(r, f)} vs raw {found[-1][f]}" for f in wide_names if found and not _same(getattr(r, f), found[-1][f])]
                rep.fail("raw_audit", f"{name}: game {gid} wide column differs from the raw source: {', '.join(wrong[:3]) or 'no raw play at the head'}")
        if version in V4_LIKE:
            found = state.get(float(r.feed_head_t)) or []
            if not any(all(_same(getattr(r, f), c[f]) for f in state_fields) for c in found):
                wrong = [f"{f} {getattr(r, f)} vs raw {found[-1][f]}" for f in state_fields if found and not _same(getattr(r, f), found[-1][f])]
                rep.fail("raw_audit", f"{name}: game {gid} v4 feature differs from the raw source: {', '.join(wrong[:3]) or 'no raw play at the head'}")
        window = [(w, tier) for w, tier in events if r.decision_t < w <= r.decision_t + HORIZON_S]
        value = sum(holdset.WEIGHTS[tier] for _, tier in window)
        tier1 = float(any(tier == 1 and w <= r.decision_t + TIER1_S for w, tier in window))
        if not (_same(r.value_next_3m, value) and _same(r.p_tier1_60s, tier1)):
            rep.fail("raw_audit", f"{name}: game {gid} label {r.value_next_3m} is not the events after its decision ({value})")


def audit_raw(df: pd.DataFrame, league: str, shard: str, name: str, raw: Path, rep: Report, version: str = FEATURES_VERSION, wide: tuple = ()):
    """Public rows of one shard against <raw>/<league>/pbp_<shard>.parquet."""
    path = Path(raw) / league / f"pbp_{shard}.parquet"
    if not path.exists():
        rep.fail("raw_audit", f"{name}: raw source {path} is missing")
        return
    frame = holdset_public.read_public(league, [path])
    if league == "nfl":
        frame = frame[frame.qtr.notna()].sort_values(["game_id", "play_id"], kind="stable") if len(frame) else frame
    elif league == "cfb":
        frame = holdset_public.cfb_frame(frame)
    elif len(frame):
        frame = frame[frame.type_text.fillna("").ne("Substitution") & frame.period_number.notna() & frame.wallclock.notna()].copy()
        frame["sq"] = pd.to_numeric(frame.sequence_number, errors="coerce")
        frame = frame.sort_values(["game_id", "sq"], kind="stable")
    games = {str(k): g for k, g in frame.groupby("game_id", sort=False)} if len(frame) else {}
    for gid, rows in df.groupby("game_id", sort=False):
        if str(gid) not in games:
            rep.fail("raw_audit", f"{name}: game {gid} is not in the raw source")
            continue
        _audit_game(league, str(gid), games[str(gid)], rows, name, rep, version, wide)


def check_label_copy(df: pd.DataFrame, name: str, rep: Report, features: tuple = FEATURES):
    if len(df) < MIN_ROWS_COPY:
        return
    for label in LABELS:
        y = df[label].to_numpy(float)
        if y.std() == 0:
            continue
        for f in features:
            x = df[f].to_numpy(float)
            if x.std() > 0 and abs(float(np.corrcoef(x, y)[0, 1])) >= COPY_R:
                rep.fail("label_copy", f"{name}: feature {f} is a copy of label {label}")


def check_splits(out: Path, seen: dict, rep: Report):
    """Whole nights, one split each, oldest first; every public night has a split; a game lives on one night."""
    path = out / "splits.json"
    public = {lg: ns for lg, ns in seen["public_nights"].items() if ns}
    if not path.exists():
        if public:
            rep.fail("splits", "no splits.json")
        return
    splits = json.loads(path.read_text())
    for lg, ns in public.items():
        s = splits.get(lg) or {}
        names = ("train", "calib", "test")
        where: dict[str, str] = {}
        for n in names:
            for night in s.get(n, []):
                if night in where:
                    rep.fail("splits", f"{lg}: night {night} in {where[night]} and {n}")
                where[night] = n
        order = [night for n in names for night in sorted(s.get(n, []))]
        if order != sorted(order):
            rep.fail("splits", f"{lg}: splits are not oldest first")
        missing = sorted(ns - set(where))
        if missing:
            rep.fail("splits", f"{lg}: nights with rows but no split: {missing[:3]}")
    for key, nights in seen["game_nights"].items():
        if len(nights) > 1:
            rep.fail("game_two_nights", f"{key} sits on nights {sorted(nights)[:3]}")
    for key, sources in seen["key_sources"].items():
        if len(sources) > 1:
            rep.fail("game_two_sources", f"{key} comes from {sorted(sources)}")


def check_dir(out: Path, archive: Path | None = None, allow_personal_nba: bool = False, raw: Path | None = None) -> Report:
    """Every check on every shard. raw: the directory of pbp_<season>.parquet files; without it the raw audit does not run
    (rep.checked["raw_rows"] stays 0), and the trainer refuses to go on."""
    rep, cache = Report(), {}
    out = Path(out)
    files = sorted(out.glob("*/*.parquet"))
    if not files:
        rep.fail("no_shards", f"{out}: nothing to check")
    seen = {"public_nights": {}, "game_nights": {}, "key_sources": {}}
    for f in files:
        name, league = f"{f.parent.name}/{f.stem}", f.parent.name
        side = f.with_suffix(".json")
        if not side.exists():
            rep.fail("sidecar", f"{name}: no sidecar")
            continue
        info = json.loads(side.read_text())
        if not info.get("complete"):
            rep.fail("sidecar", f"{name}: not marked complete")
        version = info.get("features_version")
        if version not in FEATURES_BY_VERSION:
            rep.fail("sidecar", f"{name}: features {version}, this check knows {sorted(FEATURES_BY_VERSION)}")
            version = FEATURES_VERSION
        wide = tuple(info.get("v5_columns") or ()) if version == "v5" else ()
        bad_names = [n for n in wide if not WIDE_RE.match(n)]
        if bad_names:
            rep.fail("columns", f"{name}: v5 columns that are not generated names: {bad_names[:5]}")
        allowed = allowed_for(version, wide)
        df = pd.read_parquet(f)
        rep.checked["shards"] += 1
        rep.checked["rows"] += len(df)
        unknown = sorted(set(df.columns) - allowed)
        absent = sorted(allowed - set(df.columns))
        if unknown:
            rep.fail("columns", f"{name}: columns not on the allow-list: {unknown[:5]}")
        if absent:
            rep.fail("columns", f"{name}: missing columns: {absent[:5]}")
            continue
        got = pd.util.hash_pandas_object(df, index=False).to_numpy().tobytes()
        if len(df) != info.get("rows") or hashlib.sha256(got).hexdigest() != info.get("rows_sha256"):
            rep.fail("sidecar", f"{name}: rows differ from what the builder recorded")
        if df.empty:
            continue
        if not info.get("prefix_checked"):
            rep.fail("prefix", f"{name}: the causal rebuild never ran")
        if info.get("prefix_failed"):
            rep.fail("future_feature", f"{name}: {info['prefix_failed']} features change when the feed is cut off, "
                                       f"e.g. {(info.get('prefix_examples') or ['?'])[0]}")
        rep.checked["prefix_rows"] += int(info.get("prefix_checked") or 0)
        check_times(df, name, rep)
        check_nights(df, league, name, archive, cache, allow_personal_nba, rep)
        check_label_copy(df, name, rep, FEATURES_BY_VERSION[version] + wide)
        if raw is not None and (df.source != "archive").any():
            audit_raw(df[df.source != "archive"], league, f.stem, name, raw, rep, version, wide)
        pub = df[df.source != "archive"]
        seen["public_nights"].setdefault(league, set()).update(pub.night.astype(str).unique())
        games = df[["game_id", "night", "game_key", "source"]].drop_duplicates()
        for g, n, k, s in zip(games.game_id.astype(str), games.night.astype(str), games.game_key.astype(str), games.source.astype(str)):
            seen["game_nights"].setdefault(f"{league}:{g}", set()).add(n)
            if k:
                seen["key_sources"].setdefault(k, set()).add(s)
    check_splits(out, seen, rep)
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("shards", type=Path)
    ap.add_argument("--archive", type=Path, default=None, help="data/archive, needed only for archive rows")
    ap.add_argument("--allow-personal-nba", action="store_true", help="accept hoopR NBA rows tagged personal-use")
    ap.add_argument("--raw", type=Path, default=None, help="dir of <league>/pbp_<season>.parquet: audit rows against the raw source")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    rep = check_dir(a.shards, a.archive, a.allow_personal_nba, a.raw)
    if a.json:
        print(json.dumps({"fails": rep.fails, **rep.checked}, indent=1))
    else:
        for line in rep.fails[:50]:
            print("FAIL", line)
        print(("FAIL" if rep.fails else "PASS"), f"{len(rep.fails)} problems |", rep.checked)
    return 1 if rep.fails else 0


if __name__ == "__main__":
    sys.exit(main())
