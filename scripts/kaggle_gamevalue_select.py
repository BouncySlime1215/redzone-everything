#!/usr/bin/env python3
"""Pick the GameValue v5 columns on Kaggle: generate every candidate, filter, boost, check forward, write the kept list the runtime loads.

    RZ_GV_FEATURES=v4 python scripts/kaggle_gamevalue_select.py --shards <v4 dir> --raw <raw dir> --out /kaggle/working/v5_kept.json --leagues nfl,nba,cbb --personal-nba --budget-hours 10

Why: Nick asked for far more grading variables (decisions.md, 2026-11-09). 100,000+ candidates on the data we have would overfit if they all shipped, so this
keeps only what raises the capture on nights it was not picked on, and writes down how many it generated and how many it kept. Those are two different numbers; the model uses only the second.

Only training nights are read: the train nights of <shards>/splits.json, whole nights, never calib or test, never a night on or after the scorecard window, and locked nights never reach
this script at all (kaggle_value_shards drops them before a summary exists). kaggle_gamevalue.py refuses a kept list whose selection nights overlap its held nights.

Order, per league:
  1. Cheap filter. Every candidate is computed game by game and thrown away; only its running variance and correlation with the two labels stay (a few doubles per column), so 300,000 columns fit
     in a few MB. Constants, near-empty columns and label copies (|r| at least COPY_R) go. The top --keep-filter by best |r| go on.
  2. Gradient-boosted importance. Those columns are computed for real, up to --max-rows rows over whole nights, beside the v4 features; exact duplicates go.
  3. Forward check, nested: for each forward fold the column order comes from a booster on that fold's older nights only, and each size in --sizes is scored on the fold's newer nights by the K=4 capture
     of 180 s decisions, the capture proxy (scripts/kaggle_gamevalue.py's rank_queries groups the rows the same way). The size with the best mean wins only if it beats v4 alone by MIN_GAIN points on average and no fold loses more than WORST_FOLD. Otherwise the league keeps nothing.

The capture here is a proxy on training rows, used only to choose columns. The fair score is kaggle_gamevalue.py's shared-clock capture on the test nights and then the real Planner on the same nights
(kaggle_gamevalue_holdset.py); a kept list that wins here and loses there ships nothing. Nick launches this; it never runs on the Mac.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from redzone.noggin import factory as F  # noqa: E402
from redzone.noggin import gamevalue as G  # noqa: E402
from redzone.noggin import value_shards as V  # noqa: E402
from redzone.noggin import wide_features as W  # noqa: E402
from scripts import kaggle_gamevalue as K  # noqa: E402
from scripts import kaggle_value_shards as KS  # noqa: E402

EXIT_REFUSED, EXIT_MAC = 2, 3
COPY_R, MIN_VAR, MIN_NZ = 0.995, 1e-9, 0.001        # the leak check's copy bar; a column nearly always zero teaches nothing
KEEP_FILTER, MAX_ROWS, MAX_NIGHTS = 3000, 400_000, 400
SIZES = (25, 50, 100, 200)                          # columns kept from the importance order; 0 (v4 alone) is always scored
MIN_GAIN, WORST_FOLD = 0.1, -0.5                    # capture points: smaller gains are the noise of one fold
FOLDS, K_DECISION = 5, 4
GBM_PARAMS = dict(num_leaves=31, max_depth=6, min_data_in_leaf=200, learning_rate=0.05, feature_fraction=0.7, bagging_fraction=0.7,
                  bagging_freq=1, lambda_l2=1.0, seed=7, deterministic=True, force_col_wise=True, verbose=-1, objective="tweedie", tweedie_variance_power=1.5)
ROUNDS = 150


class Refused(RuntimeError):
    """Selection must not go on: the reason is the message."""


def selection_nights(splits: dict, league: str) -> list[str]:
    """The nights this league's selection may read. Train only, and a train night that is also held out or inside the eval window refuses, not skips."""
    s = splits.get(league) or {}
    train, held = sorted(s.get("train") or []), set(s.get("calib") or []) | set(s.get("test") or [])
    if not train:
        raise Refused(f"{league}: splits.json has no train nights")
    overlap = sorted(set(train) & held)
    if overlap:
        raise Refused(f"{league}: train nights also held out, e.g. {overlap[:3]}")
    cut = F.EVAL_FIRST_NIGHT.get(league)
    if cut is not None:
        late = [n for n in train if n >= cut]
        if late:
            raise Refused(f"{league}: train nights inside the eval window starting {cut}, e.g. {late[:3]}")
    return train


def thin_nights(nights: list[str], limit: int) -> list[str]:
    """Evenly spaced whole nights, oldest first, so a long history fits the time budget without touching any night's games."""
    if len(nights) <= limit:
        return list(nights)
    picks = np.unique(np.linspace(0, len(nights) - 1, limit).astype(int))
    return [nights[int(i)] for i in picks]


def games_from_raw(league: str, raw: Path, nights: set[str]):
    """(game id, summary, night, season, playoff) of the wanted nights only: every other game's features are never built."""
    stats: dict = {}
    for path in sorted((raw / league).glob("pbp_*.parquet")):
        digits = re.sub(r"\D", "", path.stem)
        season = int(digits) if digits else 0
        if league == "nfl":
            gen = KS._nfl_games(path, season, None, stats)
        elif league == "cfb":
            gen = KS._cfb_games(path, season, None, stats)
        else:
            gen = KS._hoops_games(path, season, None, stats, league)
        for gid, summary, night, season_read, playoff, _key in gen:
            if night in nights:
                yield str(gid), summary, night, season_read, playoff


@dataclass
class GameRows:
    """One game's decision rows with a full label window, ready to score: labels, v4 features, and the play series."""
    frame: pd.DataFrame
    series: W.Series
    head: np.ndarray


def game_rows(league: str, gid: str, summary: dict, night: str, season: int, playoff: int) -> GameRows | None:
    """Labels and heads from the shard builder itself under v4, so selection sees exactly the rows a model would train on."""
    got: dict = {}

    def capture(view, head, decision):
        got["head"] = head
        return V.features(view, head, decision)

    rows = V.build_rows(league, gid, summary, night=night, season=season, source="select", license_line="select", plan="select", playoff=playoff, feature_fn=capture)
    if rows.empty:
        return None
    full = rows.label_full_window.to_numpy() == 1
    if not full.any():
        return None
    clean = V.wall_ordered(league, V.strip_published_wp(summary))
    plays = [x for x in V.flat_plays(league, clean) if G.feed_row(league, x[1])]
    return GameRows(rows[full].reset_index(drop=True), W.track(league, plays), got["head"][full])


class CorrFilter:
    """Running correlation of every candidate with both labels. Holds a few doubles per column, not the column."""

    def __init__(self, m: int):
        self.n = 0
        self.m = m
        self.sum, self.sq, self.xy1, self.xy2, self.nz = np.zeros(m), np.zeros(m), np.zeros(m), np.zeros(m), np.zeros(m)
        self.y = [0.0, 0.0, 0.0, 0.0]        # sum and square-sum of each label

    def add_labels(self, y1: np.ndarray, y2: np.ndarray) -> None:
        self.n += len(y1)
        self.y = [self.y[0] + float(y1.sum()), self.y[1] + float((y1 * y1).sum()), self.y[2] + float(y2.sum()), self.y[3] + float((y2 * y2).sum())]

    def update(self, start: int, x: np.ndarray, y1: np.ndarray, y2: np.ndarray) -> None:
        x64 = np.asarray(x, np.float64)
        stop = start + x64.shape[1]
        self.sum[start:stop] += x64.sum(axis=0)
        self.sq[start:stop] += (x64 * x64).sum(axis=0)
        self.xy1[start:stop] += x64.T.dot(y1)
        self.xy2[start:stop] += x64.T.dot(y2)
        self.nz[start:stop] += (x64 != 0.0).sum(axis=0)

    def correlations(self) -> tuple[np.ndarray, np.ndarray]:
        """(best |r| over the two labels, variance): r is NaN-free, 0 where a column has no variance."""
        n = max(self.n, 1)
        var = np.maximum(self.sq / n - (self.sum / n) ** 2, 0.0)
        r = np.zeros(self.m)
        y_sum, y_sq, y2_sum, y2_sq = self.y
        for xy, ys, yy in ((self.xy1, y_sum, y_sq), (self.xy2, y2_sum, y2_sq)):
            cov = xy / n - (self.sum / n) * (ys / n)
            vy = max(yy / n - (ys / n) ** 2, 0.0)
            with np.errstate(divide="ignore", invalid="ignore"):
                here = np.where((var > 0) & (vy > 0), np.abs(cov) / np.sqrt(var * vy), 0.0)
            r = np.maximum(r, np.nan_to_num(here))
        return r, var


def filter_candidates(league: str, games, candidates: tuple[W.Block, ...] | None = None, log=print) -> tuple[list[str], dict]:
    """Step 1. `games` yields GameRows. Returns (column names to go on, counts for the report)."""
    block_list = candidates if candidates is not None else W.blocks(league)
    sizes = [len(b.sides) * len(b.aggs) * len(b.norms) * len(b.crosses) for b in block_list]
    offset = np.cumsum([0] + sizes)
    filt = CorrFilter(int(offset[-1]))
    seen = 0
    rows = 0
    for g in games:
        if g is None:
            continue
        y1, y2 = g.frame.value_next_3m.to_numpy(float), g.frame.p_tier1_60s.to_numpy(float)
        filt.add_labels(y1, y2)
        for b, start in zip(block_list, offset[:-1]):
            filt.update(int(start), W.block_values(g.series, g.head, b), y1, y2)
        seen += 1
        rows += len(y1)
        if seen % 200 == 0:
            log(json.dumps({"league": league, "filter_games": seen, "rows": rows}), flush=True)
    if not filt.n:
        raise Refused(f"{league}: no training row to select on")
    r, var = filt.correlations()
    nz_ok = filt.nz / filt.n >= MIN_NZ
    copies = int((r >= COPY_R).sum())
    usable = (var > MIN_VAR) & nz_ok & (r < COPY_R)
    order = np.argsort(-r[usable], kind="stable")
    names = [name for b in block_list for name in W.block_names(b)]
    chosen = np.flatnonzero(usable)[order]
    return [names[i] for i in chosen], {"candidates": int(filt.m), "constant_or_near_empty": int(filt.m - usable.sum() - copies), "label_copies": copies,
                                         "usable": int(usable.sum()), "filter_rows": int(filt.n), "filter_games": seen}


def materialize(league: str, games, names: list[str], max_rows: int, est_rows: int, log=print) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    """Step 2's matrix: (labels and night per row, v4 features float32, the named columns float32), whole nights, every Nth row when the rows would not fit."""
    step = max(1, int(np.ceil(est_rows / max(max_rows, 1))))
    plan = W.compile_plan(league, tuple(names))
    frames, base, wide = [], [], []
    base_cols = list(G.GV_FEATURE_SETS["v4"])
    for g in games:
        if g is None:
            continue
        idx = np.arange(0, len(g.head), step)
        frames.append(g.frame.iloc[idx][["night", "game_id", "decision_t", "value_next_3m", "p_tier1_60s"]])
        base.append(g.frame[base_cols].to_numpy(np.float32)[idx])
        wide.append(W.run_plan(g.series, g.head[idx], plan))
    if not frames:
        raise Refused(f"{league}: no rows reached the boosting step")
    return pd.concat(frames, ignore_index=True), np.concatenate(base), np.concatenate(wide)


def drop_duplicates(X: np.ndarray) -> list[int]:
    """Indices of the first of each set of identical columns: importance would otherwise split between copies."""
    sample = X[:: max(1, len(X) // 20_000)]
    seen, keep = set(), []
    for j in range(X.shape[1]):
        key = hashlib.sha256(np.round(sample[:, j], 4).tobytes()).digest()
        if key not in seen:
            seen.add(key)
            keep.append(j)
    return keep


def capture_pct(frame: pd.DataFrame, pred: np.ndarray, k: int = K_DECISION) -> float | None:
    """Value captured by picking the top K games at each 180 s decision, as a share of the best K possible. Uses K.rank_queries' grouping. None with no decision."""
    q = K.rank_queries(frame)
    if not len(q.idx):
        return None
    y = frame.value_next_3m.to_numpy(float)[q.idx]
    p = np.asarray(pred, float)[q.idx]
    got, best, at = 0.0, 0.0, 0
    for size in q.sizes:
        yy, pp = y[at:at + size], p[at:at + size]
        order = np.argsort(-pp, kind="stable")[:k]
        got += float(yy[order].sum())
        best += float(np.sort(yy)[::-1][:k].sum())
        at += size
    return 100.0 * got / best if best > 0 else None


class LGBFitter:
    """LightGBM, imported only when a fit runs. Anything with fit/gain/predict can stand in."""

    def __init__(self, rounds: int = ROUNDS):
        self.rounds = rounds

    def fit(self, X: np.ndarray, y: np.ndarray, names: list[str]):
        import lightgbm as lgb
        return lgb.train(dict(GBM_PARAMS, num_threads=os.cpu_count() or 4), lgb.Dataset(X, y, feature_name=names), self.rounds)

    @staticmethod
    def gain(model) -> np.ndarray:
        return np.asarray(model.feature_importance("gain"), float)

    @staticmethod
    def predict(model, X: np.ndarray) -> np.ndarray:
        return np.asarray(model.predict(X), float)


@dataclass
class Selection:
    """The result of one league."""
    league: str
    nights: list[str]
    counts: dict
    kept: list[str] = field(default_factory=list)
    folds: list[dict] = field(default_factory=list)
    verdict: str = ""

    def as_json(self) -> dict:
        return {"selection_nights": self.nights, **self.counts, "kept": len(self.kept), "kept_columns": self.kept, "folds": self.folds, "verdict": self.verdict}


def _rank_by_gain(fitter, frame: pd.DataFrame, base: np.ndarray, wide: np.ndarray, base_names: list[str], wide_names: list[str]) -> np.ndarray:
    """Wide column indices, best first, by gain summed over a booster fit on `frame` alone. Ties break by name order."""
    X = np.hstack((base, wide))
    model = fitter.fit(X, frame.value_next_3m.to_numpy(float), base_names + wide_names)
    gains = fitter.gain(model)[len(base_names):]
    return np.lexsort((np.arange(len(wide_names)), -gains))


def forward_check(fitter, frame: pd.DataFrame, base: np.ndarray, wide: np.ndarray, base_names: list[str], wide_names: list[str], nights: list[str], folds: int,
                  sizes: tuple[int, ...], deadline: float | None) -> tuple[list[dict], dict[int, list[float]], int]:
    """Step 3, nested. Returns (fold rows, {size: capture per fold}, folds done). Column order and fits see only the fold's older nights."""
    scores: dict[int, list[float]] = {s: [] for s in (0, *sizes)}
    rows, done = [], 0
    night_of = frame.night.astype(str).to_numpy()
    for fit_nights, val_nights in K.forward_folds(nights, folds):
        if deadline is not None and time.monotonic() > deadline:
            break
        fit = np.isin(night_of, fit_nights)
        val = np.isin(night_of, val_nights)
        if not fit.any() or not val.any():
            continue
        order = _rank_by_gain(fitter, frame[fit], base[fit], wide[fit], base_names, wide_names)
        y = frame.value_next_3m.to_numpy(float)[fit]
        row = {"fit_last": max(fit_nights), "val_first": min(val_nights), "scores": {}}
        for size in (0, *sizes):
            cols = order[:size]
            names = base_names + [wide_names[j] for j in cols]
            X_fit = np.hstack((base[fit], wide[fit][:, cols]))
            X_val = np.hstack((base[val], wide[val][:, cols]))
            model = fitter.fit(X_fit, y, names)
            score = capture_pct(frame[val], fitter.predict(model, X_val))
            row["scores"][str(size)] = score
            if score is not None:
                scores[size].append(score)
        rows.append(row)
        done += 1
    return rows, scores, done


def pick_size(scores: dict[int, list[float]], done: int, sizes: tuple[int, ...]) -> tuple[int, str]:
    """(kept size, why). 0 means v4 alone won. Only a size scored on every finished fold may win."""
    if done == 0:
        return 0, "no forward fold finished"
    base = scores[0]
    if len(base) < done:
        return 0, "v4 baseline did not score every fold"
    best_size, best_gain, why = 0, 0.0, f"no size beat v4 alone by {MIN_GAIN} capture points"
    for size in sizes:
        got = scores[size]
        if len(got) != len(base):
            continue
        deltas = [a - b for a, b in zip(got, base)]
        gain, worst = float(np.mean(deltas)), float(np.min(deltas))
        if gain > best_gain and gain >= MIN_GAIN and worst >= WORST_FOLD:
            best_size, best_gain = size, gain
            why = f"size {size}: mean gain {gain:.2f} capture points over v4 alone, worst fold {worst:+.2f} (proxy on training nights)"
    return best_size, why


def select_league(league: str, raw: Path, nights: list[str], fitter, *, keep_filter: int = KEEP_FILTER, sizes: tuple[int, ...] = SIZES, folds: int = FOLDS, max_rows: int = MAX_ROWS,
                  blocks: tuple[W.Block, ...] | None = None, deadline: float | None = None, log=print,
                  games_factory: Callable | None = None) -> Selection:
    """Steps 1-3 for one league. Reads only `nights`."""
    wanted = set(nights)

    def stream():
        for gid, summary, night, season, playoff in (games_factory() if games_factory else games_from_raw(league, raw, wanted)):
            if night not in wanted:
                raise Refused(f"{league}: selection reached night {night}, not a training night")
            yield game_rows(league, gid, summary, night, season, playoff)

    names, counts = filter_candidates(league, stream(), blocks, log)
    counts["candidates_generated"] = counts.pop("candidates")
    if not names:
        return Selection(league, sorted(wanted), counts, verdict="no candidate survived the filter")
    keep = names[:keep_filter]
    frame, base, wide = materialize(league, stream(), keep, max_rows, counts["filter_rows"], log)
    columns = drop_duplicates(wide)
    keep = [keep[j] for j in columns]
    wide = wide[:, columns]
    counts["boosted_columns"] = len(keep)
    night_list = sorted(frame.night.astype(str).unique())
    base_names = list(G.GV_FEATURE_SETS["v4"])
    fold_rows, scores, done = forward_check(fitter, frame, base, wide, base_names, keep, night_list, folds, sizes, deadline)
    size, why = pick_size(scores, done, sizes)
    kept: list[str] = []
    if size:
        order = _rank_by_gain(fitter, frame, base, wide, base_names, keep)         # the final order, now on every selection night
        kept = [keep[j] for j in order[:size]]
    return Selection(league, sorted(wanted), counts, kept, fold_rows, why)


def main(argv=None, fitter=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--shards", type=Path, required=True, help="value_shards dir with splits.json: the train nights are the only nights read")
    ap.add_argument("--raw", type=Path, required=True, help="dir of <league>/pbp_<season>.parquet")
    ap.add_argument("--out", type=Path, required=True, help="the kept-column list to write")
    ap.add_argument("--leagues", default="nfl")
    ap.add_argument("--personal-nba", "--personal-hoopr", dest="personal_nba", action="store_true", help="accept the personal-use hoopR/wehoop rows (off by default)")
    ap.add_argument("--max-nights", type=int, default=MAX_NIGHTS)
    ap.add_argument("--keep-filter", type=int, default=KEEP_FILTER)
    ap.add_argument("--max-rows", type=int, default=MAX_ROWS)
    ap.add_argument("--sizes", default=",".join(map(str, SIZES)))
    ap.add_argument("--folds", type=int, default=FOLDS)
    ap.add_argument("--budget-hours", type=float, default=None, help="stop starting forward folds past this many hours from launch")
    a = ap.parse_args(argv)
    if G.GV_FEATURES_VERSION != "v4":
        print(f"REFUSED: selection reads the v4 base columns; run it under {G.GV_FEATURES_ENV}=v4 (got {G.GV_FEATURES_VERSION})", flush=True)
        return EXIT_REFUSED
    if fitter is None and sys.platform == "darwin":
        print("REFUSED: selection never runs on the Mac. Run this on Kaggle CPU.", flush=True)
        return EXIT_MAC
    leagues = [x for x in a.leagues.split(",") if x]
    deadline = time.monotonic() + a.budget_hours * 3600.0 if a.budget_hours else None
    sizes = tuple(int(x) for x in a.sizes.split(",") if x)
    splits = json.loads((a.shards / "splits.json").read_text())
    report: dict = {}
    try:
        for league in leagues:
            status, why = V.public_status(league, a.personal_nba)
            if status != "ok":
                raise Refused(f"{league}: {why}")
            nights = thin_nights(selection_nights(splits, league), a.max_nights)
            result = select_league(league, a.raw, nights, fitter or LGBFitter(), keep_filter=a.keep_filter, sizes=sizes, folds=a.folds, max_rows=a.max_rows, deadline=deadline)
            report[league] = result
            print(json.dumps({"league": league, "candidates_generated": result.counts["candidates_generated"], "kept": len(result.kept), "verdict": result.verdict}), flush=True)
    except (Refused, ValueError) as e:
        print(f"REFUSED: {e}", flush=True)
        return EXIT_REFUSED
    columns = sorted({c for r in report.values() for c in r.kept})
    W.write_kept(a.out, {lg: r.as_json() for lg, r in report.items()}, columns)
    print("HONEST COUNTS: generated", {lg: r.counts["candidates_generated"] for lg, r in report.items()}, "kept", {lg: len(r.kept) for lg, r in report.items()},
          "union the model reads", len(columns), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
