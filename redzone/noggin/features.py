"""Noggin Timing features and window labels: numpy + stdlib only (the live app has no pandas).

`context` is THE feature definition for a dead window, used by the data factory (training) and by the
live hook (redzone/noggin/live.py), so a feature means the same thing in both. `windows_for_game` labels
one game's windows with the live `cuts.DeadTracker`.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np

from redzone.decider import cuts
from redzone.models import League
from redzone.sources import espn

KINDS = tuple(cuts._RANK)
KIND_CODE = {k: i for i, k in enumerate(KINDS)}
MAX_LEN_S = 1500.0          # longer is a feed gap / rain delay, not a break
NBA_MARKS = (419.0, 179.0)  # 6:59 and 2:59: the mandatory-timeout marks (NBA Rule 5 §VI)

# Only what the live DeadTracker can compute for an open window (no score, no playoff flag yet: v2).
FEATURES = ("kind_code", "kind_median", "kind_floor", "period", "clock_left", "game_left", "season",
            "n_timeout_period", "timeout_index", "under_mark", "mark_owed", "n_stops_10m", "since_last_stop",
            "prev_kind_code", "prev_len", "merged")


REG = {League.NBA: (4, 720.0), League.CBB: (2, 1200.0), League.WCBB: (4, 600.0)}


def season_of(epoch: float) -> int:
    """Basketball season label = the calendar year it ends in (Oct 2025 -> 2026)."""
    t = datetime.fromtimestamp(epoch, tz=timezone.utc)
    return t.year + 1 if t.month >= 8 else t.year


def is_timeout(kind: str) -> bool:
    return kind.startswith("timeout") or kind == "media_timeout"


def context(league: League, history: list[tuple], cur: tuple) -> dict:
    """The model's features for window `cur` = (Window, period, clock_left), given the game's earlier
    windows in `history` (same tuples, oldest first). ONE definition for the factory and the live gate
    (redzone.noggin.live), so a feature means the same thing in training and at runtime."""
    win, per, clock = cur
    per = int(per or 0)
    clock = float(clock) if clock is not None else float("nan")
    kind = win.kind
    reg, plen = REG.get(league, (4, 720.0))
    to_clocks = [c for (w, p, c) in history if int(p or 0) == per and is_timeout(w.kind) and c is not None]
    is_to = is_timeout(kind)
    n_to = len(to_clocks) + int(is_to)
    under = sum(1 for m in NBA_MARKS if clock == clock and clock <= m) if league == League.NBA else 0
    owed = 0
    if league == League.NBA and under and per <= 4:
        mark = NBA_MARKS[under - 1]
        owed = int(not any(c <= mark for c in to_clocks))
    game_left = max(0.0, (reg - per) * plen + (clock if clock == clock else 0.0)) if per <= reg else 0.0
    prev = history[-1][0] if history else None
    recent = sum(1 for (w, _, _) in history if w.start >= win.start - 600)
    return dict(
        kind_code=KIND_CODE.get(kind, len(KINDS)),
        kind_median=cuts.MEDIAN_S.get(league.base, {}).get(kind, np.nan),
        kind_floor=cuts.FLOOR_S.get(league.base, {}).get(kind, np.nan),
        period=per, clock_left=clock, game_left=game_left, season=season_of(win.start),
        n_timeout_period=n_to, timeout_index=n_to if is_to else 0, under_mark=under, mark_owed=owed,
        n_stops_10m=recent, since_last_stop=(win.start - prev.start) if prev else np.nan,
        prev_kind_code=KIND_CODE.get(prev.kind, len(KINDS)) if prev else -1,
        prev_len=((prev.end - prev.start) if prev and prev.end is not None else np.nan),
        merged=int(prev is not None and prev.end is not None and win.start - prev.end < 5.0))


def vector(ctx: dict) -> np.ndarray:
    return np.array([ctx[f] for f in FEATURES], np.float32)


def windows_for_game(league: League, plays: list[dict]) -> list[dict]:
    """Every dead window in one game, with its context, using the live DeadTracker."""
    dt = cuts.DeadTracker(league)
    seen: dict[float, cuts.Window] = {}
    walls, rows = [], []
    for k, p in enumerate(plays):
        w = espn._ts(p.get("wallclock"))
        if w is None:
            continue
        pc = espn._play_clock(league, p)
        dt.add(str(p.get("id") or k), w, w, espn._kind(league, p), p.get("text") or "", pc["type_text"],
               pc["clock"], pc["period"])
        walls.append(w)
        rows.append((pc["period"], pc["clock"], _f(p.get("homeScore")), _f(p.get("awayScore")),
                     (pc["type_text"] or "").lower()))
        if len(dt.marks) >= 550:          # DeadTracker keeps only the recent past: harvest before it trims
            for win in dt.windows():
                seen[win.start] = win
    for win in dt.windows():
        seen[win.start] = win
    frozen = dt.frozen_until
    walls_a = np.asarray(walls)
    big = _big_marks(rows)
    out, history = [], []
    for win in sorted(seen.values(), key=lambda x: x.start):
        i = int(np.searchsorted(walls_a, win.start, side="right")) - 1
        if i < 0:
            continue
        per, clock, hs, as_, _ = rows[i]
        cur = (win, per, clock)
        ok = win.end is not None and win.start > frozen and math.isfinite(win.end - win.start) \
            and 0.0 <= win.end - win.start <= MAX_LEN_S
        if ok:
            row = context(league, history, cur)
            row.update(kind=win.kind, start=win.start, length_s=win.end - win.start, exact=bool(win.exact),
                       score_diff=abs(hs - as_), big_next60=int(_any_between(walls_a, big, win.end, win.end + 60.0)))
            out.append(row)
        history.append(cur)
    return out


def _f(x) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    return v if v == v else 0.0


def _big_marks(rows: list[tuple]) -> np.ndarray:
    """Big plays for the big-play-next label: a made three, a dunk, or a lead change (late ties count)."""
    out = np.zeros(len(rows), bool)
    ph = pa = 0.0
    for i, (_, _, h, a, tt) in enumerate(rows):
        made = (h + a) > (ph + pa)
        three = (h - ph == 3) or (a - pa == 3)
        swap = (ph - pa) * (h - a) < 0
        out[i] = made and (three or "dunk" in tt or swap)
        ph, pa = max(ph, h), max(pa, a)
    return out


def _any_between(walls: np.ndarray, mask: np.ndarray, lo: float, hi: float) -> bool:
    i, j = np.searchsorted(walls, lo, side="right"), np.searchsorted(walls, hi, side="right")
    return bool(mask[i:j].any())
