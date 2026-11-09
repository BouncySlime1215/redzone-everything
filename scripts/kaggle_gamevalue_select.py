#!/usr/bin/env python3
"""Pick the GameValue v5 columns on Kaggle: generate every candidate, filter, boost, check forward, write the kept list the runtime loads.

    RZ_GV_FEATURES=v4 python scripts/kaggle_gamevalue_select.py --shards <v4 dir> --raw <raw dir> --out /kaggle/working/v5_kept.json --leagues nfl,nba,cbb --personal-nba --budget-hours 10

Why: Nick asked for far more grading variables (decisions.md, 2026-11-09). 100,000+ candidates on the data we have would overfit if they all shipped, so this
keeps only what raises the capture on nights it was not picked on, and writes down how many it generated and how many it kept. Those are two different numbers; the model uses only the second.

Only training nights are read: the train nights of <shards>/splits.json, whole nights, never calib or test, never a night on or after the scorecard window, and locked nights never reach this script at all
(kaggle_value_shards drops them before a summary exists). kaggle_gamevalue.py refuses a kept list whose selection nights overlap its held nights.

Order, per league. The train nights split by time: the filter reads only the oldest 60%, the forward check only the newest 40%, so no night the filter read can score a column.
  1. Cheap filter, oldest 60%. Every candidate is computed game by game and thrown away; only its running variance and correlation with the two labels stay (a few doubles per column), so 300,000 columns fit in a few MB. At most
     --filter-rows rows (default 1,000,000), kept by whole 180 s decision buckets so ranking is not starved. Constants, near-empty columns and label copies (|r| at least COPY_R) go. The top --keep-filter by best |r| go on.
  2. Gradient-boosted importance, newest 40%, up to --max-rows rows, again by whole decision buckets, beside the v4 features; columns identical up to order (raw, scaled and log clones) go, because a tree cannot tell them apart.
  3. Forward check, nested inside the newest 40%: for each forward fold the column order comes from a booster on that fold's older nights only, and each size in --sizes is scored on the fold's newer nights by the K=4 capture of 180 s decisions, the capture
     proxy (scripts/kaggle_gamevalue.py's rank_queries groups the rows the same way). Noise is measured: three seeded v4-only fits per fold. A size wins only if its mean gain over those is more than 2 standard errors, at least 80% of folds are positive and every planned fold ran.
     Otherwise the league keeps nothing. A run stopped by --budget-hours records folds_done and marks the file complete: false, which the runtime refuses to load.

Each league reads and validates only its own kept columns: the file's union is one schema, and a league reads 0 for the columns of other leagues.

The capture here is a proxy on training rows, used only to choose columns. The fair score is kaggle_gamevalue.py's shared-clock capture on the test nights and then the real Planner on the same nights
(kaggle_gamevalue_holdset.py); a kept list that wins here and loses there ships nothing. scripts/gamevalue_v5_report.py is the paired v5 vs v4 report. Nick launches this; it never runs on the Mac.

--probe-games N times the filter, the materialize step and one fit on N games and prints the extrapolated hours; it writes nothing.
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
KEEP_FILTER, MAX_ROWS, MAX_NIGHTS, FILTER_ROWS = 3000, 400_000, 400, 1_000_000
FILTER_FRAC = 0.6                                   # oldest share of the train nights the filter may read; the forward check gets the rest
SIZES = (25, 50, 100, 200)                          # columns kept from the importance order; 0 (v4 alone) is always scored
NOISE_SEEDS = (7, 11, 13)                           # v4-only fits per fold, the measured noise of one fit
FOLDS, K_DECISION, MIN_POSITIVE_FRAC, MIN_SE = 5, 4, 0.8, 2.0     # at least 80% of folds positive; mean gain above MIN_SE standard errors
BUCKET_S = 180.0                                    # holdset's decision spacing: thinning keeps or drops a whole decision
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


def split_nights(nights: list[str]) -> tuple[list[str], list[str]]:
    """(oldest FILTER_FRAC, the rest), so the forward check never scores a night the filter read."""
    ordered = sorted(nights)
    cut = max(1, int(np.ceil(len(ordered) * FILTER_FRAC)))
    return ordered[:cut], ordered[cut:]


def night_index(shards: Path, league: str, nights: set[str]) -> dict[str, tuple[int, int]]:
    """{night: (rows, games)} of the shards, read from two id columns only, so a thinning step and a probe's estimate cost nothing."""
    out: dict[str, tuple[int, int]] = {}
    for f in sorted((Path(shards) / league).glob("*.parquet")):
        t = pd.read_parquet(f, columns=["night", "game_id"])
        t = t[t.night.astype(str).isin(nights)]
        for night, g in t.groupby("night"):
            rows, games = out.get(str(night), (0, 0))
            out[str(night)] = (rows + len(g), games + int(g.game_id.nunique()))
    return out


def thin_step(index: dict[str, tuple[int, int]] | None, nights: list[str], target: int) -> int:
    """Every Nth decision bucket keeps the rows of these nights near `target`. 1 when the shard index is not known."""
    if not index:
        return 1
    rows = sum(index.get(n, (0, 0))[0] for n in nights)
    return max(1, int(np.ceil(rows / max(target, 1))))


def bucket_keep(decision_t: np.ndarray, step: int) -> np.ndarray:
    """Keep whole 180 s decisions: floor(t / 180) % step == 0, so the games of one decision are kept together and rank_queries is not starved."""
    return np.floor(np.asarray(decision_t, float) / BUCKET_S).astype(np.int64) % step == 0


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


def game_rows(league: str, gid: str, summary: dict, night: str, season: int, playoff: int, step: int = 1) -> GameRows | None:
    """Labels and heads from the shard builder itself under v4, so selection sees exactly the rows a model would train on. `step` thins by decision bucket."""
    got: dict = {}

    def capture(view, head, decision):
        got["head"] = head
        return V.features(view, head, decision)

    rows = V.build_rows(league, gid, summary, night=night, season=season, source="select", license_line="select", plan="select", playoff=playoff, feature_fn=capture)
    if rows.empty:
        return None
    full = (rows.label_full_window.to_numpy() == 1) & bucket_keep(rows.decision_t.to_numpy(float), step)
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


def filter_candidates(league: str, games, candidates: tuple[W.Block, ...] | None = None, log=print, deadline: float | None = None) -> tuple[list[str], dict]:
    """Step 1. `games` yields GameRows. Returns (column names to go on, counts for the report). A deadline reached mid-stream is reported, not guessed around: filter_stopped_early."""
    block_list = candidates if candidates is not None else W.blocks(league)
    sizes = [len(b.sides) * len(b.aggs) * len(b.norms) * len(b.crosses) for b in block_list]
    offset = np.cumsum([0] + sizes)
    filt = CorrFilter(int(offset[-1]))
    seen = 0
    rows = 0
    stopped = False
    for g in games:
        if deadline is not None and time.monotonic() > deadline:
            stopped = True
            break
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
        if stopped:
            return [], {"candidates": int(filt.m), "filter_rows": 0, "filter_games": 0, "filter_stopped_early": True}
        raise Refused(f"{league}: no training row to select on")
    r, var = filt.correlations()
    nz_ok = filt.nz / filt.n >= MIN_NZ
    copies = int((r >= COPY_R).sum())
    usable = (var > MIN_VAR) & nz_ok & (r < COPY_R)
    order = np.argsort(-r[usable], kind="stable")
    names = [name for b in block_list for name in W.block_names(b)]
    chosen = np.flatnonzero(usable)[order]
    return [names[i] for i in chosen], {"candidates": int(filt.m), "constant_or_near_empty": int(filt.m - usable.sum() - copies), "label_copies": copies,
                                         "usable": int(usable.sum()), "filter_rows": int(filt.n), "filter_games": seen, "filter_stopped_early": stopped}


def materialize(league: str, games, names: list[str], log=print, deadline: float | None = None) -> tuple[pd.DataFrame, np.ndarray, np.ndarray, bool]:
    """Step 2's matrix: (labels and night per row, v4 features float32, the named columns float32, stopped early?). Rows were thinned by decision bucket as each game was built."""
    plan = W.compile_plan(league, tuple(names))
    frames, base, wide = [], [], []
    base_cols = list(G.GV_FEATURE_SETS["v4"])
    stopped = False
    for g in games:
        if deadline is not None and time.monotonic() > deadline:
            stopped = True
            break
        if g is None:
            continue
        frames.append(g.frame[["night", "game_id", "decision_t", "value_next_3m", "p_tier1_60s"]])
        base.append(g.frame[base_cols].to_numpy(np.float32))
        wide.append(W.run_plan(g.series, g.head, plan))
    if not frames:
        if stopped:
            return pd.DataFrame(), np.empty((0, 0)), np.empty((0, 0)), True
        raise Refused(f"{league}: no rows reached the boosting step")
    return pd.concat(frames, ignore_index=True), np.concatenate(base), np.concatenate(wide), stopped


def drop_duplicates(X: np.ndarray) -> list[int]:
    """Indices of the first of each set of columns with the same order of values: importance would otherwise split between raw, scaled and log clones, which a tree cannot tell apart."""
    sample = X[:: max(1, len(X) // 20_000)]
    seen, keep = set(), []
    for j in range(X.shape[1]):
        ranks = np.unique(sample[:, j] + 0.0, return_inverse=True)[1]              # +0.0 folds -0.0 into 0.0
        key = hashlib.sha256(np.asarray(ranks, np.int32).tobytes()).digest()
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

    def fit(self, X: np.ndarray, y: np.ndarray, names: list[str], seed: int = 7):
        import lightgbm as lgb
        return lgb.train(dict(GBM_PARAMS, seed=seed, bagging_seed=seed, feature_fraction_seed=seed, num_threads=os.cpu_count() or 4), lgb.Dataset(X, y, feature_name=names), self.rounds)

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
    filter_nights: list[str] = field(default_factory=list)
    forward_nights: list[str] = field(default_factory=list)
    folds_done: int = 0
    folds_planned: int = 0
    complete: bool = False                # every game read and every planned fold ran; a partial league keeps nothing

    def as_json(self) -> dict:
        return {"selection_nights": self.nights, "filter_nights": self.filter_nights, "forward_nights": self.forward_nights, **self.counts, "kept": len(self.kept), "kept_columns": self.kept,
                "folds": self.folds, "folds_done": self.folds_done, "folds_planned": self.folds_planned, "complete": self.complete, "verdict": self.verdict}


def _rank_by_gain(fitter, frame: pd.DataFrame, base: np.ndarray, wide: np.ndarray, base_names: list[str], wide_names: list[str]) -> np.ndarray:
    """Wide column indices, best first, by gain summed over a booster fit on `frame` alone. Ties break by name order."""
    X = np.hstack((base, wide))
    model = fitter.fit(X, frame.value_next_3m.to_numpy(float), base_names + wide_names, seed=NOISE_SEEDS[0])
    gains = fitter.gain(model)[len(base_names):]
    return np.lexsort((np.arange(len(wide_names)), -gains))


@dataclass
class ForwardResult:
    """Step 3's table: capture per fold for each size, and the noise of the v4-only fits."""
    rows: list[dict]
    scores: dict[int, list[float]]
    noise: list[float]                    # per fold: sd of the v4-only fits over the noise seeds
    done: int
    planned: int


def forward_check(fitter, frame: pd.DataFrame, base: np.ndarray, wide: np.ndarray, base_names: list[str], wide_names: list[str], nights: list[str], folds: int,
                  sizes: tuple[int, ...], deadline: float | None) -> ForwardResult:
    """Step 3, nested. Column order and every fit see only the fold's older nights. Size 0 is v4 alone, scored as the mean of the noise seeds."""
    scores: dict[int, list[float]] = {s: [] for s in (0, *sizes)}
    rows, noise, done = [], [], 0
    night_of = frame.night.astype(str).to_numpy()
    plan = K.forward_folds(nights, folds)
    for fit_nights, val_nights in plan:
        if deadline is not None and time.monotonic() > deadline:
            break
        fit = np.isin(night_of, fit_nights)
        val = np.isin(night_of, val_nights)
        if not fit.any() or not val.any():
            continue
        y = frame.value_next_3m.to_numpy(float)[fit]
        baseline = [fitter.fit(base[fit], y, base_names, seed=seed) for seed in NOISE_SEEDS]
        runs = [capture_pct(frame[val], fitter.predict(m, base[val])) for m in baseline]
        if any(r is None for r in runs):
            continue
        scores[0].append(float(np.mean(runs)))
        noise.append(float(np.std(runs, ddof=1)))
        order = _rank_by_gain(fitter, frame[fit], base[fit], wide[fit], base_names, wide_names)
        row = {"fit_last": max(fit_nights), "val_first": min(val_nights), "scores": {"0": scores[0][-1]}, "noise": noise[-1]}
        for size in sizes:
            cols = order[:size]
            names = base_names + [wide_names[j] for j in cols]
            model = fitter.fit(np.hstack((base[fit], wide[fit][:, cols])), y, names, seed=NOISE_SEEDS[0])
            score = capture_pct(frame[val], fitter.predict(model, np.hstack((base[val], wide[val][:, cols]))))
            row["scores"][str(size)] = score
            if score is not None:
                scores[size].append(score)
        rows.append(row)
        done += 1
    return ForwardResult(rows, scores, noise, done, len(plan))


def pick_size(res: ForwardResult, sizes: tuple[int, ...]) -> tuple[int, str]:
    """(kept size, why). 0 means v4 alone won, or the check did not finish. Only a size scored on every fold may win, and only above the measured noise."""
    if res.done == 0 or res.done < res.planned:
        return 0, f"refused: {res.done} of {res.planned} forward folds ran"
    base = res.scores[0]
    best_size, best_gain, why = 0, 0.0, "no size beat v4 alone by more than 2 standard errors on at least 80% of folds"
    for size in sizes:
        got = res.scores[size]
        if len(got) != len(base) or len(got) != res.done:
            continue
        deltas = np.array([a - b for a, b in zip(got, base)], float)
        gain = float(deltas.mean())
        seed_se = float(np.sqrt(np.mean(np.square(res.noise)) * (1.0 + 1.0 / len(NOISE_SEEDS)) / len(deltas)))      # one seeded fit against the mean of the v4 seeds
        fold_se = float(deltas.std(ddof=1) / np.sqrt(len(deltas))) if len(deltas) > 1 else float("inf")
        se = max(seed_se, fold_se)
        positive = int((deltas > 0).sum())
        need = int(np.ceil(MIN_POSITIVE_FRAC * len(deltas)))
        if gain > best_gain and gain > MIN_SE * se and positive >= need:
            best_size, best_gain = size, gain
            why = f"size {size}: mean gain {gain:.3f} capture points, SE {se:.3f}, {positive}/{len(deltas)} folds positive (proxy on training nights)"
    return best_size, why


def select_league(league: str, raw: Path, nights: list[str], fitter, *, keep_filter: int = KEEP_FILTER, sizes: tuple[int, ...] = SIZES, folds: int = FOLDS, max_rows: int = MAX_ROWS,
                  filter_rows: int = FILTER_ROWS, blocks: tuple[W.Block, ...] | None = None, deadline: float | None = None, log=print,
                  games_factory: Callable | None = None, index: dict | None = None) -> Selection:
    """Steps 1-3 for one league. The filter reads only the oldest 60% of `nights`, the forward check only the rest, so each game is built once. `index` is night_index, for the thinning steps."""
    filter_nights, forward_nights = split_nights(nights)
    done = Selection(league, sorted(nights), {}, filter_nights=filter_nights, forward_nights=forward_nights)

    def stream(wanted_list: list[str], step: int):
        wanted = set(wanted_list)
        source = games_factory(wanted) if games_factory else games_from_raw(league, raw, wanted)
        for gid, summary, night, season, playoff in source:
            if night not in wanted:
                raise Refused(f"{league}: selection reached night {night}, not a training night of this step")
            yield game_rows(league, gid, summary, night, season, playoff, step)

    start = time.monotonic()
    filter_deadline = None if deadline is None else start + 0.5 * max(deadline - start, 0.0)    # half the league's budget for the filter
    names, counts = filter_candidates(league, stream(filter_nights, thin_step(index, filter_nights, filter_rows)), blocks, log, filter_deadline)
    counts["candidates_generated"] = counts.pop("candidates")
    counts["filter_step"] = thin_step(index, filter_nights, filter_rows)
    done.counts = counts
    if counts.get("filter_stopped_early"):
        done.verdict = f"out of time in the filter after {counts.get('filter_games', 0)} games: kept nothing"
        return done
    if not names:
        done.verdict, done.complete = "no candidate survived the filter", True
        return done
    keep = names[:keep_filter]
    counts["forward_step"] = thin_step(index, forward_nights, max_rows)
    frame, base, wide, stopped = materialize(league, stream(forward_nights, counts["forward_step"]), keep, log, deadline)
    if stopped or not len(frame):
        done.verdict = "out of time building the forward rows: kept nothing"
        return done
    columns = drop_duplicates(wide)
    keep = [keep[j] for j in columns]
    wide = wide[:, columns]
    counts["boosted_columns"] = len(keep)
    night_list = sorted(frame.night.astype(str).unique())
    base_names = list(G.GV_FEATURE_SETS["v4"])
    res = forward_check(fitter, frame, base, wide, base_names, keep, night_list, folds, sizes, deadline)
    done.folds, done.folds_done, done.folds_planned = res.rows, res.done, res.planned
    size, why = pick_size(res, sizes)
    done.verdict = why
    done.complete = res.done == res.planned and res.done > 0
    if size:
        order = _rank_by_gain(fitter, frame, base, wide, base_names, keep)         # the final order, on the forward nights only
        done.kept = [keep[j] for j in order[:size]]
    return done


def probe_league(league: str, raw: Path, nights: list[str], fitter, games: int, index: dict | None, *, filter_rows: int = FILTER_ROWS, keep_filter: int = KEEP_FILTER, max_rows: int = MAX_ROWS,
                 sizes: tuple[int, ...] = SIZES, folds: int = FOLDS, blocks: tuple[W.Block, ...] | None = None, games_factory: Callable | None = None, log=print) -> dict:
    """Time the three steps on `games` real games and extrapolate: filter per game, materialize per game, then every forward-fold fit, scaled by row count. Writes nothing."""
    filter_nights, forward_nights = split_nights(nights)
    step_f, step_m = thin_step(index, filter_nights, filter_rows), thin_step(index, forward_nights, max_rows)
    wanted = set(filter_nights)
    source = games_factory(wanted) if games_factory else games_from_raw(league, raw, wanted)
    picked = []
    for gid, summary, night, season, playoff in source:
        if night not in wanted:
            raise Refused(f"{league}: probe reached night {night}, not a filter night")
        got = game_rows(league, gid, summary, night, season, playoff, step_f)
        if got is not None:
            picked.append(got)
        if len(picked) >= games:
            break
    if not picked:
        raise Refused(f"{league}: the probe read no game")
    t0 = time.monotonic()
    names, _ = filter_candidates(league, iter(picked), blocks, log)
    t_filter = (time.monotonic() - t0) / len(picked)
    keep = names[:keep_filter]
    rows_probe = sum(len(g.head) for g in picked)
    t0 = time.monotonic()
    frame, base, wide, _ = materialize(league, iter(picked), keep, log)
    t_mat = (time.monotonic() - t0) / len(picked)
    t0 = time.monotonic()
    fitter.fit(np.hstack((base, wide)), frame.value_next_3m.to_numpy(float), [f"f{j}" for j in range(base.shape[1] + wide.shape[1])], seed=NOISE_SEEDS[0])
    t_fit = time.monotonic() - t0
    total = lambda ns: sum(index.get(n, (0, 0))[1] for n in ns) if index else len(picked) * len(ns)  # noqa: E731
    rows_all = sum(index.get(n, (0, 0))[0] for n in forward_nights) / step_m if index else rows_probe * max(len(forward_nights), 1)
    fits = folds * (len(NOISE_SEEDS) + 1 + len(sizes)) + 1
    hours = (t_filter * total(filter_nights) + t_mat * total(forward_nights) + fits * t_fit * rows_all / max(rows_probe, 1)) / 3600.0
    out = {"league": league, "probe_games": len(picked), "filter_s_per_game": round(t_filter, 3), "materialize_s_per_game": round(t_mat, 3), "fit_s_on_probe": round(t_fit, 3),
           "filter_games_estimated": int(total(filter_nights)), "forward_games_estimated": int(total(forward_nights)), "fits": fits, "extrapolated_hours": round(hours, 2)}
    log(json.dumps(out), flush=True)
    return out


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
    ap.add_argument("--filter-rows", type=int, default=FILTER_ROWS, help="most rows the filter reads, thinned by whole decision buckets")
    ap.add_argument("--probe-games", type=int, default=None, help="time every step on this many games, print extrapolated hours, write nothing")
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
    started = time.monotonic()
    deadline = started + a.budget_hours * 3600.0 if a.budget_hours else None
    sizes = tuple(int(x) for x in a.sizes.split(",") if x)
    splits = json.loads((a.shards / "splits.json").read_text())
    report: dict[str, Selection] = {}
    shard_id = V.shards_id(a.shards)
    try:
        for i, league in enumerate(leagues):
            status, why = V.public_status(league, a.personal_nba)
            if status != "ok":
                raise Refused(f"{league}: {why}")
            nights = thin_nights(selection_nights(splits, league), a.max_nights)
            index = night_index(a.shards, league, set(nights))
            if a.probe_games:
                got = probe_league(league, a.raw, nights, fitter or LGBFitter(), a.probe_games, index, filter_rows=a.filter_rows, keep_filter=a.keep_filter, max_rows=a.max_rows, sizes=sizes, folds=a.folds)
                print(f"PROBE {league}: {got['extrapolated_hours']} h extrapolated from {got['probe_games']} games", flush=True)
                continue
            now = time.monotonic()
            share = None if deadline is None else now + max(deadline - now, 0.0) / (len(leagues) - i)     # the budget left, shared by the leagues left
            result = select_league(league, a.raw, nights, fitter or LGBFitter(), keep_filter=a.keep_filter, sizes=sizes, folds=a.folds, max_rows=a.max_rows, filter_rows=a.filter_rows, deadline=share, index=index)
            report[league] = result
            W.write_kept(a.out, {lg: r.as_json() for lg, r in report.items()}, complete=False, shard_id=shard_id)         # partial output after every league
            print(json.dumps({"league": league, "candidates_generated": result.counts.get("candidates_generated"), "kept": len(result.kept), "folds_done": result.folds_done, "complete": result.complete, "verdict": result.verdict}), flush=True)
    except (Refused, ValueError) as e:
        print(f"REFUSED: {e}", flush=True)
        return EXIT_REFUSED
    if a.probe_games:
        return 0
    finished = all(r.complete for r in report.values()) and set(report) == set(leagues)
    W.write_kept(a.out, {lg: r.as_json() for lg, r in report.items()}, complete=finished, shard_id=shard_id)
    print("HONEST COUNTS: generated", {lg: r.counts.get("candidates_generated") for lg, r in report.items()}, "kept per league", {lg: len(r.kept) for lg, r in report.items()},
          "complete" if finished else "INCOMPLETE: the runtime will not load this file", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
