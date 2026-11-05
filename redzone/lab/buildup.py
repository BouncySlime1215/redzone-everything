"""[EXP-025] Seconds of build-up seen per big play, and the lab switch for the pre-big stage.

SECONDARY info line, computed here (not a frozen grader): for every graded big play (grading.is_big)
that was watched live start to finish, how many seconds of THAT game the screen had already shown,
continuously, before the play started airing (capped at BUILDUP_CAP, so one long stay doesn't dominate).
A big play not seen live counts 0 in the all-plays mean. Uses the archive's true wallclocks and the
true screen lag, like sportsgrade.

`prebig_runs()` swaps the decider the backtest builds for one with the pre-big stage
(redzone/noggin/prebig.py). The stage still does nothing until `noggin.prebig.enabled` is true.
"""
from __future__ import annotations

import contextlib
import statistics

from redzone.grading import PLAY_LEN, is_big, lag_of
from redzone.lab import backtest
from redzone.lab.backtest import offsets_of, watched_whole

BUILDUP_CAP = 180.0
ARRIVED_MIN = 20.0   # "arrived before it": at least this much of the build-up on screen


def visit_before(watching, offsets, gid: str, start: float, end: float, tune_s: float) -> float | None:
    """Seconds of game `gid` on screen before its play [start, end] (live-edge time) began airing,
    within the one visit that showed the whole play; None if no visit did."""
    best = None
    for i, (at, g) in enumerate(watching):
        if g != gid:
            continue
        o = offsets[i] if offsets and i < len(offsets) else 0.0
        nxt = watching[i + 1][0] if i + 1 < len(watching) else float("inf")
        up = at + tune_s
        if up <= start + o and end + o < nxt:
            best = start + o - up
    return best


def buildup(replays, switches, lag_true, tune_s: float, horizon: float) -> list[float | None]:
    """One entry per graded big play: seconds of build-up seen (None = not seen live)."""
    watching = [(s.t, f"replay:{s.to}" if s.kind == "replay" else s.to) for s in switches]
    offs = offsets_of(switches)
    out: list[float | None] = []
    for r in replays:
        lg = r.league
        prev = None
        for w, p in zip(r.wall, r.plays):
            if is_big(lg, p, prev):
                lag = lag_of(lag_true, r.gid)
                end = w + lag
                start = end - PLAY_LEN.get(lg, 0.0)
                if watched_whole(watching, r.gid, start, end, tune_s, horizon, offs):
                    v = visit_before(watching, offs, r.gid, start, end, tune_s)
                    out.append(min(BUILDUP_CAP, max(0.0, v if v is not None else 0.0)))
                else:
                    out.append(None)
            prev = p
    return out


def summarize(values: list[float | None]) -> dict:
    live = [v for v in values if v is not None]
    return {"big": len(values), "big_live": len(live),
            "buildup_median_s": round(statistics.median(live), 1) if live else None,
            "buildup_mean_all_s": round(sum(live) / len(values), 1) if values else None,
            "arrived_before": sum(v >= ARRIVED_MIN for v in live)}


@contextlib.contextmanager
def prebig_runs():
    """Inside this block, backtest.run builds the pre-big decider (still gated by its flag)."""
    from redzone.noggin.prebig import with_prebig
    orig = backtest.Decider
    backtest.Decider = with_prebig(orig)
    try:
        yield
    finally:
        backtest.Decider = orig
