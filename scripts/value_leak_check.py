#!/usr/bin/env python3
"""Leak check for GameValue shards: exit 1 if any row is from a locked or fresh night or reads past its decision.

    python scripts/value_leak_check.py <shards dir> [--archive data/archive] [--raw <dir>] [--allow-personal-nba] [--json]

Why: a model trained on one locked night or one future feature makes every later gate a lie, and the shard builder
cannot be trusted to grade itself (the-mind section 9, build plan WP-25). So this file does not import
redzone/noggin/value_shards.py: the column allow-list and the licence rules are its own, and a test keeps the two
in step. Night truth comes from redzone/noggin/factory.py (public cut) and scripts/coverage_audit.DevOnly (archive).

Fails closed: no shards, a missing sidecar, a missing splits file, or an archive row without an archive to check
it against are all FAIL. Archive files for locked games are never opened: only scoreboards are read, to know the split.

Stamps and the prefix rebuild only see what the shard builder and its adapter wrote, so a future fact an adapter put into a
per-play field passes them. `--raw` is the check that catches it: for a sample of rows per game it reads the head play's
fields straight from the raw source (not through the adapter) and recomputes the label from holdset's separate detector
counting only events strictly after decision_t. scripts/kaggle_gamevalue.py refuses to train unless this ran.
"""
from __future__ import annotations

import argparse
import hashlib
import json
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
FEATURES_BY_VERSION = {"v2": FEATURES, "v3": FEATURES + HISTORY}
META = ("league", "season", "game_id", "game_key", "night", "source_night_id", "source", "license", "plan",
        "decision_t", "feed_cut_t", "feed_head_t", "feature_t_max", "lag_s", "label_full_window", "label_first_t")
ALLOWED = frozenset(META + FEATURES + LABELS)


def allowed_for(version: str) -> frozenset:
    return frozenset(META + FEATURES_BY_VERSION[version] + LABELS)


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


def _raw_history(league: str, g: pd.DataFrame) -> dict[float, list[dict]]:
    """{wall: [v3 history features]} recomputed from the raw rows in wall order. The score, run and event logic is this file's own.
    NFL rows the shards never see (extra points and two-point tries fold into the touchdown; a row with no ESPN type) are skipped."""
    plays = []
    for r in g.itertuples(index=False):
        if league == "nfl":
            wall = holdset.ts(r.time_of_day) if isinstance(r.time_of_day, str) else None
            if r.play_type == "extra_point" or r.two_point_attempt == 1:
                continue
            kind_text = factory._nfl_type(r)
            if not kind_text or not wall:
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


def _same(a, b) -> bool:
    """Close enough for float32. A NaN in the shard is no number at all: the trainer drops that row."""
    if a != a:
        return True
    return bool(np.isclose(float(a), float(b), rtol=1e-5, atol=1e-3))


def _audit_game(league: str, gid: str, g: pd.DataFrame, rows: pd.DataFrame, name: str, rep: Report, version: str = FEATURES_VERSION):
    """A sample of one game's rows against the raw source: the head play's fields, then the label."""
    make = {"nfl": holdset_public.nfl_summary, "cfb": holdset_public.cfb_summary}.get(league, holdset_public.hoops_summary)
    built = holdset.game_row(league, gid, make(g))
    if built is None:
        rep.fail("raw_audit", f"{name}: game {gid} has shard rows but is not a whole game in the raw source")
        return
    plays, events = _raw_plays(league, g), built[4]
    history = _raw_history(league, g) if version != "v2" else {}
    picks = np.unique(np.linspace(0, len(rows) - 1, min(RAW_ROWS, len(rows))).astype(int))
    for r in rows.iloc[picks].itertuples(index=False):
        rep.checked["raw_rows"] += 1
        candidates = plays.get(float(r.feed_head_t))
        if not candidates:
            rep.fail("raw_audit", f"{name}: game {gid} has a feed head at {r.feed_head_t} that no raw play has")
            continue
        if not any(all(_same(getattr(r, f), c[f]) for f in RAW_FIELDS) for c in candidates):
            diff = [f"{f} {getattr(r, f)} vs raw {candidates[-1][f]}" for f in RAW_FIELDS if not _same(getattr(r, f), candidates[-1][f])]
            rep.fail("raw_audit", f"{name}: game {gid} feature differs from the raw source: {', '.join(diff[:3])} "
                            f"(raw clock text {candidates[-1]['clock_text']!r})")
        if version != "v2":
            found = history.get(float(r.feed_head_t)) or []
            if not any(all(_same(getattr(r, f), c[f]) for f in HISTORY) for c in found):
                wrong = [f"{f} {getattr(r, f)} vs raw {found[-1][f]}" for f in HISTORY if found and not _same(getattr(r, f), found[-1][f])]
                rep.fail("raw_audit", f"{name}: game {gid} history differs from the raw source: {', '.join(wrong[:3]) or 'no raw play at the head'}")
        window = [(w, tier) for w, tier in events if r.decision_t < w <= r.decision_t + HORIZON_S]
        value = sum(holdset.WEIGHTS[tier] for _, tier in window)
        tier1 = float(any(tier == 1 and w <= r.decision_t + TIER1_S for w, tier in window))
        if not (_same(r.value_next_3m, value) and _same(r.p_tier1_60s, tier1)):
            rep.fail("raw_audit", f"{name}: game {gid} label {r.value_next_3m} is not the events after its decision ({value})")


def audit_raw(df: pd.DataFrame, league: str, shard: str, name: str, raw: Path, rep: Report, version: str = FEATURES_VERSION):
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
        _audit_game(league, str(gid), games[str(gid)], rows, name, rep, version)


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
        allowed = allowed_for(version)
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
        check_label_copy(df, name, rep, FEATURES_BY_VERSION[version])
        if raw is not None and (df.source != "archive").any():
            audit_raw(df[df.source != "archive"], league, f.stem, name, raw, rep, version)
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
