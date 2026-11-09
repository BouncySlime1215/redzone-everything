#!/usr/bin/env python3
"""Leak check for GameValue shards: exit 1 if any row is from a locked or fresh night or reads past its decision.

    python scripts/value_leak_check.py <shards dir> [--archive data/archive] [--allow-personal-nba] [--json]

Why: a model trained on one locked night or one future feature makes every later gate a lie, and the shard builder
cannot be trusted to grade itself (the-mind section 9, build plan WP-25). So this file does not import
redzone/noggin/value_shards.py: the column allow-list and the licence rules are its own, and a test keeps the two
in step. Night truth comes from redzone/noggin/factory.py (public cut) and scripts/coverage_audit.DevOnly (archive).

Fails closed: no shards, a missing sidecar, a missing splits file, or an archive row without an archive to check
it against are all FAIL. Archive files for locked games are never opened: only scoreboards are read, to know the split.
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

from redzone.noggin import factory  # noqa: E402
from scripts.coverage_audit import DevOnly, LockedNightError  # noqa: E402

FEATURES = ("period", "clock_left", "game_left", "is_ot", "late", "abs_margin", "total_points", "pts_5m", "swing_5m",
            "lead_changes", "plays_5m", "since_head_s", "since_score_s", "since_start_s", "t1_5m", "t2_5m", "t3_5m",
            "t1_game", "value_10m", "down", "distance", "yards_to_goal", "playoff")
LABELS = ("value_next_3m", "p_tier1_60s")
META = ("league", "season", "game_id", "game_key", "night", "source_night_id", "source", "license", "plan",
        "decision_t", "feed_cut_t", "feed_head_t", "feature_t_max", "lag_s", "label_full_window")
ALLOWED = frozenset(META + FEATURES + LABELS)
PUBLIC_SOURCES = {"nflverse_pbp": "nfl"}
PERSONAL_SOURCES = {"hoopr_espn_nba": "nba"}
PERSONAL_TAG = "personal-use, not for commercial release"
COPY_R = 0.995          # a feature this correlated with a label is the label
MIN_ROWS_COPY = 200
EPS = 1e-3              # seconds: float rounding on the stored times


class Report:
    def __init__(self):
        self.fails: list[str] = []
        self.checked = {"shards": 0, "rows": 0, "prefix_rows": 0}

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


def check_label_copy(df: pd.DataFrame, name: str, rep: Report):
    if len(df) < MIN_ROWS_COPY:
        return
    for label in LABELS:
        y = df[label].to_numpy(float)
        if y.std() == 0:
            continue
        for f in FEATURES:
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


def check_dir(out: Path, archive: Path | None = None, allow_personal_nba: bool = False) -> Report:
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
        df = pd.read_parquet(f)
        rep.checked["shards"] += 1
        rep.checked["rows"] += len(df)
        unknown = sorted(set(df.columns) - ALLOWED)
        absent = sorted(ALLOWED - set(df.columns))
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
        check_label_copy(df, name, rep)
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
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    rep = check_dir(a.shards, a.archive, a.allow_personal_nba)
    if a.json:
        print(json.dumps({"fails": rep.fails, **rep.checked}, indent=1))
    else:
        for line in rep.fails[:50]:
            print("FAIL", line)
        print(("FAIL" if rep.fails else "PASS"), f"{len(rep.fails)} problems |", rep.checked)
    return 1 if rep.fails else 0


if __name__ == "__main__":
    sys.exit(main())
