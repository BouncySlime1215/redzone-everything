#!/usr/bin/env python3
"""[time-shift buffer] Oracle side of the buffer sweep (Nick 9:10 AM Nov 5, parts B + C).

    python scripts/buffer_sweep.py --leagues nba,nfl,nhl --buffers 45,60,75,90 --out /mnt/project-files/lane1/oracle

For every archived night of the FIT split (train + valid; the locked nights are never read here), per league
and per buffer B (s behind the stream's live edge, so the screen runs STREAM_DELAY_S + B behind real life):

  full     oracle.solve: full hindsight, the ceiling of everything (the same at every B, up to the night's edges)
  window   oracle_window.solve_window: exact DP over only what the buffer reveals (B + stream delay - feed lag - tune),
           the past for anything beyond: the ceiling of a buffered app with a perfect in-window optimizer

Both graded by sportsgrade.grade, the app's own grader. Writes <out>/<B>/<league>.json, one row per night:
{league, big_total, big_live, big_from_start, crunch, crunch_live, entries, entries_mid_play, dead_s, watched_s,
switches} per oracle. big_from_start is null until lane 3's frozen grader line lands (Card.big_from_start).

Also the ADAPTIVE buffer (Nick 9:35 AM): B = feed-lag p90 + sync error + tune/seek + margin - stream delay,
floored at --floor (a play is always known before it is shown), reported as its own arm.
Owner: lane 1. Lane 3 grades the app side (eval_sports --behind-live) and divides by these rows.
Why: Oracle side of the time-shift buffer sweep, Nick's Nov 5 parts B and C (docstring).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from redzone.lab import oracle as O  # noqa: E402
from redzone.lab import oracle_window as OW  # noqa: E402
from redzone.lab.backtest import STREAM_DELAY_S  # noqa: E402
from redzone.models import League  # noqa: E402

TUNE_S = 5.0
SYNC_ERR_S = 2.0          # RC7 gate: sync p95 <= 2 s per stick
MARGIN_S = 5.0
MIN_NIGHTS = 20           # below this an arm's % oracle is PENDING (grading rule 4)
# Feed lag (s from a play to the app knowing it) of tonight's PRIMARY source, p50, per league: NBA = nba_cdn
# (data A feed race, ~11.5 s); NFL = ESPN (~37 s, race p50); NHL = ESPN until api-web is measured (assumed same).
PRIMARY_FEED_LAG = {"nba": 11.5, "nfl": 37.0, "nhl": 37.0, "cbb": 37.0}


def adaptive_buffer(league: str, feed_lag_p90: float | None = None, tune_s: float = TUNE_S,
                    sync_err_s: float = SYNC_ERR_S, margin_s: float = MARGIN_S, floor_s: float = 12.0) -> float:
    """Nick 9:35 AM: buffer = feed-lag p95 + sync error + tune/seek + margin, with a hard floor. The screen
    already runs STREAM_DELAY_S behind real life before any buffer, so B (behind the live edge) is that sum
    minus the stream delay. p90 stands in for p95 until the live feed-arrival logs give a p95 per league."""
    from redzone.noggin import pick as PK
    fl = feed_lag_p90 if feed_lag_p90 is not None else PK.FEED_LAG_P90.get(League(league), 60.0)
    return round(max(floor_s, fl + sync_err_s + tune_s + margin_s - STREAM_DELAY_S), 1)


def feed_lag_for(league: str, mode: str) -> float:
    """--feed-lag: 'primary' (p50 of tonight's primary source, the ceiling), 'p90' (PK.FEED_LAG_P90, the
    guarantee) or a number of seconds."""
    if mode == "primary":
        return PRIMARY_FEED_LAG.get(league, 37.0)
    if mode == "p90":
        from redzone.noggin import pick as PK
        return PK.FEED_LAG_P90.get(League(league), 60.0)
    return float(mode)


def bootstrap_pct(rs: dict, iters: int = 2000, seed: int = 0) -> list[float] | None:
    """95% night-bootstrap CI on window big_live / full big_live (ratio of sums, whole nights resampled)."""
    w = np.array([r["window"]["big_live"] for r in rs.values()], float)
    f = np.array([r["full"]["big_live"] for r in rs.values()], float)
    if len(w) < 2:
        return None
    idx = np.random.default_rng(seed).integers(0, len(w), (iters, len(w)))
    pct = 100 * w[idx].sum(1) / np.maximum(1, f[idx].sum(1))
    return [round(float(np.percentile(pct, 2.5)), 1), round(float(np.percentile(pct, 97.5)), 1)]


def row(card) -> dict:
    c = O.counts(card)
    return {"big_total": c["big"], "big_live": c["big_live"], "big_from_start": getattr(card, "big_from_start", None),
            "crunch": c["crunch"], "crunch_live": c["crunch_live"], "my_crunch": c["my_crunch"],
            "my_crunch_live": c["my_crunch_live"], "entries": c["entries"],
            "entries_mid_play": c["entries_mid_play"], "mid_play_cuts": c["mid_play_cuts"],
            "dead_s": c["dead_s"], "watched_s": c["watched_s"], "switches": c["switches"]}


def nights(league: League, split: str, end: str | None):
    import eval_sports as ES
    yield from ES.nights(league, 10_000, end, split)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--leagues", default="nba,nfl,nhl")
    ap.add_argument("--buffers", default="0,45,60,75,90")
    ap.add_argument("--adaptive", action="store_true", default=True)
    ap.add_argument("--floor", type=float, default=12.0)
    ap.add_argument("--feed-lag", default="p90",
                    help="'p90' (measured per league, PK.FEED_LAG_P90; the guarantee, default), "
                         "'primary' (p50 of tonight's primary feed, the ceiling), or seconds")
    ap.add_argument("--split", default="fit", choices=("fit", "train", "valid"), help="never the locked nights")
    ap.add_argument("--end", default=None)
    ap.add_argument("--max-nights", type=int, default=0)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    mine = {"WSH", "UNC"}
    summary = {}
    for lg in a.leagues.split(","):
        league = League(lg)
        fl = feed_lag_for(lg, a.feed_lag)
        arms = {str(int(b)): float(b) for b in a.buffers.split(",")}
        if a.adaptive:
            b = adaptive_buffer(lg, feed_lag_p90=fl, floor_s=a.floor)   # sized and solved with the same lag
            arms[f"adaptive_{b:g}"] = b
        rows: dict[str, dict] = {k: {} for k in arms}
        t_lg = time.time()
        for i, (day, reps, _targets) in enumerate(nights(league, a.split, a.end)):
            if a.max_nights and i >= a.max_nights:
                break
            t0 = min(r.wall[0] for r in reps) - 600.0
            t1 = max(r.wall[-1] for r in reps) + 600.0
            for key, b in arms.items():
                n = O.Night(reps, mine, STREAM_DELAY_S + b, TUNE_S, t0, t1)
                tab = O.tables(n, np.arange(n.t0, n.t1, O.STEP_S))
                full = row(O.grade(n, O.solve(n, "combined", tab)))     # solved and graded per arm
                win = row(O.grade(n, OW.solve_window(n, fl, "combined", tab)))
                rows[key][day] = {"league": lg, "buffer_s": b, "screen_lag_s": STREAM_DELAY_S + b,
                                  "feed_lag_s": fl, "feed_lag_mode": a.feed_lag,
                                  "window_s": OW.window_steps(STREAM_DELAY_S + b, fl, TUNE_S) * O.STEP_S,
                                  "full": full, "window": win}
            print(f"{lg} {day}: {len(reps)} games, {time.time() - t_lg:.0f} s", flush=True)
        for key, rs in rows.items():
            d = a.out / key
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{lg}.json").write_text(json.dumps(rs, indent=1))
            tot = {o: {k: sum(r[o][k] or 0 for r in rs.values()) for k in ("big_total", "big_live", "entries",
                                                                          "entries_mid_play", "switches",
                                                                          "dead_s", "watched_s")}
                   for o in ("full", "window")}
            pct = round(100 * tot["window"]["big_live"] / max(1, tot["full"]["big_live"]), 1)
            summary.setdefault(lg, {})[key] = {"nights": len(rs), "buffer_s": arms[key], "feed_lag_s": fl,
                                               **{f"{o}_{k}": v for o in tot for k, v in tot[o].items()},
                                               "window_pct_of_full_big_live": pct, "ci95": bootstrap_pct(rs),
                                               "status": "PENDING" if len(rs) < MIN_NIGHTS else "ok"}
    (a.out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
