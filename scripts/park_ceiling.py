"""PARK step 0: the supply ceiling. The highest jump pace each rule set allows, per sport, from the grader's own facts.

    nice -n 19 python scripts/park_ceiling.py --leagues nba nfl --cap 4 --json out.json --csv out.csv

No TV, no adb, no engine, no network. Dev (`fit`) nights only: a locked night is never opened. Numbers only on stdout.

What it does. For each recorded night it reads every game's dead-ball and live stretches from `grading.dead_at` (the grader's
fact, sampled each second) and then lets a GREEDY MAX-PACE viewer loose on N of the night's games with K sticks. The greedy
viewer wants nothing but jumps, so its pace is a ceiling for any brain: a real brain also wants the right game.

Two physics:
  live   today: both sticks at the live edge. A jump needs the screen game dead now AND the target dead when its picture
         comes up one flip later (two waits, by luck). The target is assumed already tuned on the hidden stick.
  park   the hidden stick sits behind live, parked `e` seconds before a play starts (R3) or `e` after a whistle (`--park-at start`,
         the first PARK idea). The landing is then always ready, so only the leave is waited for. A visit runs from the park
         point to the next whistle plus `e`. A dead ball is usable to land on when it is at least 2e + q long and to leave on
         when at least 2e long, so the position error can never put the cut inside a play (Rule #1 by construction: every
         counter here is zero because illegal points are never offered; the real counters come from park_sim.py with random
         errors). `q` is 0 when a pause holds (the stick can stop exactly) and 15 when only scrub positions hold (the stick
         keeps playing and can only be placed in 15 s steps, so it lands up to 15 s early).
Measured TV numbers (Nov 9-10): flip 2.05 s, tune 2.5 s, park / clip load 18.5-30 s by rewind depth, position right within
about 5 s, reach about 175 s, smallest rewind about 45 s behind live, 15 s skip steps at about 1.3 s a press.

Rule sets: R1 dead balls only. R2 (basketball) adds the 4 s after any shot as a legal point, usable only when 2e fits in it.
R3 one-play visits (park-at end). R4 banking: forward skips on (off = a hidden stick can only play or pause). R5 sticks 3 / 4.
R6 a minimum visit. `--pick short` lets the viewer choose the shortest ready visit instead of the next one in order.

Why: Nick's pace target is 5-10 jumps a minute ("7 is a good number"); before building anything we need to know what pace
each rule set can supply at all, with two sticks and the timings his TV really has.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import pickle
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if ROOT.as_posix() not in sys.path:
    sys.path.insert(0, ROOT.as_posix())

from redzone.grading import dead_at, raw_type  # noqa: E402
from redzone.lab import leverage, splits  # noqa: E402
from redzone.lab.backtest import Replay  # noqa: E402
from redzone.models import League  # noqa: E402
from redzone.moments import value2  # noqa: E402
from redzone.moments.detect import detect as detect_moments  # noqa: E402
from redzone.noggin import arbiter  # noqa: E402
from redzone.sources import espn  # noqa: E402

FLIP_S, TUNE_S = 2.05, 2.5          # measured medians (50 of 50, 30 of 30)
LOAD_1_S, PRESS_S = 19.3, 1.28      # park / clip load at one rewind press, and each further press (18.5-20.2 ... 28.8-30.2 at 9)
RESCRUB_1_S = LOAD_1_S - TUNE_S - 3.0   # a fresh scrub on the channel the stick is already on (load = tune + 3 s + rewind)
POS_ERR_S = 5.0                     # the rewind label is right within about 5 s
REACH_S, MIN_BEHIND_S = 175.0, 45.0  # deepest and shallowest a stick sits behind live
SKIP_STEP_S = 15.0
HIGH_PLAY_S = 7.0                   # veto_audit.PLAY_LEN_S: a high-leverage play covers this long before its stamp
SHOT_WINDOW_S = 4.0                 # R2: this long after any shot counts as a legal point (the grader gives a made basket 4 s)
WINDOWS_MIN = (5, 10, 20, 30, 45, 60)
SPREAD_S = 9000.0                   # the N games of one slate start within this long of each other (the tightest such group is used)
FAR = 1e12


def value_of(kind: str) -> float:
    """veto_audit.value_of: a score 5, a red-zone trip or turnover 3, any other must-see 1."""
    return 5.0 if kind in arbiter.SCORE_KINDS else 3.0 if kind in ("rz_trip", "turnover") else 1.0


def truth_of(r: Replay) -> dict:
    """One game's facts: dead stretches (1 s grid), shot times (basketball), must-see moments."""
    t0, t1 = r.wall[0], r.wall[-1]
    dead, brk, cur, ad = [], [], None, False
    for k in range(int(t1 - t0) + 1):
        st = t0 + k
        d, b, _ = dead_at(r.league, r.wall, r.plays, st)
        if d and cur is None:
            cur, ad = st, False
        elif not d and cur is not None:
            dead.append((cur, st))
            brk.append(ad)
            cur = None
        ad = ad or (d and b)
    if cur is not None:
        dead.append((cur, t1))
        brk.append(ad)
    lg, cfg2 = r.league.value, value2.config()
    hl = [(w - HIGH_PLAY_S, w) for w, p in zip(r.wall, r.plays) if leverage.high_leverage(r.league, p)]
    lev = []                                  # (time, value2 leverage of the score and clock): what a no-future brain can read
    for w, p in zip(r.wall, r.plays):
        hs, as_ = espn._int(p.get("homeScore")), espn._int(p.get("awayScore"))
        if hs is None or as_ is None:
            continue
        try:
            left, ot = value2.seconds_left(lg, espn._int(espn._get(p, "period", "number")) or 0, espn._clock_s(espn._get(p, "clock", "displayValue")), cfg2)
            lev.append((w, float(value2.leverage(lg, abs(hs - as_), left, ot, cfg2)), abs(hs - as_), left))
        except (KeyError, TypeError, ValueError):
            lev.append((w, 1.0, abs(hs - as_), -1.0))
    try:
        v2 = [(m.t, float(m.value), float(m.replay)) for m in value2.grade(lg, r.data, r.gid, cfg=cfg2)]
    except (KeyError, TypeError, ValueError):
        v2 = []
    shots = [w for w, p in zip(r.wall, r.plays) if p.get("shootingPlay") and "free throw" not in raw_type(p)] \
        if r.league.base == League.NBA else []
    big = [(m.t, value_of(m.kind)) for m in detect_moments(r.league.value, r.data, r.gid).moments
           if arbiter.trigger_rank(r.league.value, m.kind) != arbiter.NOT_FLOOR]
    return {"gid": r.gid, "t0": t0, "t1": t1, "dead": dead, "brk": brk, "shots": shots, "big": sorted(big), "hl": hl, "lev": lev, "v2": sorted(v2)}


def load_nights(league: str, cap: int, cache: Path | None) -> list[list[dict]]:
    """The `cap` dev nights with the most games, each a list of game truths. Cached outside the repo when asked."""
    key = cache / f"park_truth3_{league}_{cap}.pkl" if cache else None
    if key and key.exists():
        return pickle.loads(key.read_bytes())
    d = ROOT / "data/archive" / league
    keep = splits.nights_for(d, "fit") or set()
    games = splits.archive_nights(d)
    out = []
    for night in sorted(keep, key=lambda n: (-len(games.get(n, ())), n))[:cap]:
        rows = []
        for gid in games.get(night, ()):
            f = d / f"summary_{gid}.json"
            if not f.exists():
                continue
            try:
                r = Replay.load(League(league), f)
            except (OSError, ValueError):
                continue
            if r is not None and len(r.plays) >= 60:
                rows.append(truth_of(r))
        if len(rows) >= 2:
            out.append(rows)
    if key:
        key.parent.mkdir(parents=True, exist_ok=True)
        key.write_bytes(pickle.dumps(out))
    return out


def slate(night: list[dict], n: int) -> list[dict] | None:
    """The n games of this night that start closest together (None when the night has no such slate)."""
    gs = sorted(night, key=lambda g: g["t0"])
    best = None
    for i in range(len(gs) - n + 1):
        spread = gs[i + n - 1]["t0"] - gs[i]["t0"]
        if spread <= SPREAD_S and (best is None or spread < best[0]):
            best = (spread, gs[i:i + n])
    return best[1] if best else None


def span(games: list[dict]) -> tuple[float, float]:
    """While at least two of these games are in progress."""
    return sorted(g["t0"] for g in games)[1], sorted(g["t1"] for g in games)[-2]


class Units:
    """One game's visits under one rule set: where a stick may be parked, and where that visit may end."""

    def __init__(self, g: dict, *, e: float, q: float, shots: bool, min_visit: float, park_at: str):
        wins = list(g["dead"])
        if shots:
            wins = sorted(wins + [(s, s + SHOT_WINDOW_S) for s in g["shots"]])
            merged = []
            for s, x in wins:
                if merged and s <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], x))
                else:
                    merged.append((s, x))
            wins = merged
        wins.append((g["t1"], FAR))                      # after the final whistle a leave is always clean
        self.lives = [(a[1], b[0]) for a, b in zip(wins, wins[1:]) if b[0] > a[1]]   # live stretches between legal windows
        self.live_end = [x[1] for x in self.lives]
        self.live_start = [x[0] for x in self.lives]
        self.zs = [s + e for s, x in wins if x - s >= 2 * e]       # safe leave zones [zs, ze]
        self.ze = [x - e for s, x in wins if x - s >= 2 * e]
        self.p, self.leave = [], []
        for s, x in wins[:-1]:
            if x - s < 2 * e + q:
                continue
            p = (s + e) if park_at == "start" else (x - e - q / 2.0)
            j = bisect.bisect_right(self.zs, x)          # the first leave zone that starts after this window ends
            out = None
            while j < len(self.zs):
                c = max(self.zs[j], p + min_visit)
                if c <= self.ze[j]:
                    out = c
                    break
                j += 1
            if out is not None:
                self.p.append(p)
                self.leave.append(out)
        self.big_t = [b[0] for b in g["big"]]
        self.big_v = [b[1] for b in g["big"]]

    def next_leave(self, c: float) -> float:
        """The first content time at or after c that sits in a safe leave zone."""
        j = bisect.bisect_left(self.ze, c)
        return max(c, self.zs[j]) if j < len(self.zs) else FAR

    def whole_plays(self, a: float, b: float) -> int:
        return max(0, bisect.bisect_right(self.live_end, b) - bisect.bisect_left(self.live_start, a))

    def live_seconds(self, a: float, b: float) -> float:
        i, tot = bisect.bisect_right(self.live_end, a), 0.0
        while i < len(self.lives) and self.lives[i][0] < b:
            tot += max(0.0, min(b, self.lives[i][1]) - max(a, self.lives[i][0]))
            i += 1
        return tot

    def big(self, a: float, b: float) -> float:
        return sum(self.big_v[bisect.bisect_left(self.big_t, a):bisect.bisect_right(self.big_t, b)])


def skip_cost(delta: float, skip: bool) -> float:
    """Seconds a hidden stick needs to move its picture `delta` seconds forward: 15 s skips, then play the rest."""
    if delta <= 0:
        return 0.0
    if not skip:
        return delta
    return PRESS_S * math.floor(delta / SKIP_STEP_S) + (delta % SKIP_STEP_S)


def park_run(games: list[dict], *, sticks: int = 2, e: float = POS_ERR_S, q: float = 0.0, shots: bool = False,
             min_visit: float = 0.0, park_at: str = "end", skip: bool = True, pick: str = "next", flip: float = FLIP_S,
             reach: float = REACH_S, min_behind: float = MIN_BEHIND_S, repark: bool = True,
             start: float | None = None, stop: float | None = None) -> dict:
    """The greedy max-pace viewer on these games with parked sticks. Returns jump times and what each visit showed."""
    us = [Units(g, e=e, q=q, shots=shots, min_visit=min_visit, park_at=park_at) for g in games]
    T0, T1 = span(games)
    T0, T1 = (T0 if start is None else start), (T1 if stop is None else stop)
    k = min(sticks, len(games))
    # a stick: game index, content position it was left at, the time it went hidden. Every stick starts with a full bank:
    # as far behind live as the reach allows, already set up
    st = [{"g": i, "c": T0 - reach, "hid": T0 - 60.0} for i in range(k)]

    def plan(s: dict, target: float, held: set[int]) -> tuple | None:
        """(ready time, visit seconds, game, unit) for this hidden stick to be landed on at `target` or as soon after."""
        best = None
        cands = [s["g"]] + ([h for h in range(len(games)) if h not in held] if repark else [])
        for h in cands:
            u = us[h]
            same = h == s["g"]
            lo = s["c"] if same else s["hid"] + LOAD_1_S - reach
            i = bisect.bisect_left(u.p, lo)
            seen = 0
            while i < len(u.p) and seen < 40:
                p = u.p[i]
                if same:      # move forward by skips and play, or scrub the stick afresh (a load without the tune), whichever is sooner
                    behind = max(min_behind, s["hid"] + RESCRUB_1_S - p)
                    ready = s["hid"] + min(skip_cost(p - s["c"], skip or q > 0), RESCRUB_1_S + (SKIP_STEP_S / 2.0 if q == 0 else 0.0)
                                           + PRESS_S * max(0, math.ceil((behind - MIN_BEHIND_S) / SKIP_STEP_S)))
                else:
                    behind = max(min_behind, s["hid"] + LOAD_1_S - p)
                    ready = s["hid"] + LOAD_1_S + PRESS_S * max(0, math.ceil((behind - MIN_BEHIND_S) / SKIP_STEP_S)) \
                        + (SKIP_STEP_S / 2.0 if q == 0 else 0.0)
                ready = max(ready, p + min_behind - flip)
                go = max(ready, target)
                if go + flip - p > reach:        # parked too long ago: this stick has to move on to a later play
                    i += 1
                    continue
                key = (go, i if pick == "next" else u.leave[i] - p, 0 if same else 1)
                if best is None or key < best[0]:
                    best = (key, ready, u.leave[i] - p, h, i)
                if ready > target and (pick == "next" or seen > 12):
                    break
                seen += 1
                i += 1
        return best[1:] if best else None

    scr, land, unit = 0, T0, None
    # the first picture: the stick that is ready first
    first = [(plan(s, T0, {x["g"] for x in st}), n) for n, s in enumerate(st)]
    first = [(f, n) for f, n in first if f]
    if not first:
        return {"jumps": [], "visits": [], "span": (T0, T1)}
    f, scr = min(first)
    land, unit = max(f[0], T0), (f[2], f[3])
    st[scr]["g"] = unit[0]
    jumps, visits = [], []
    while land < T1:
        g, i = unit
        u = us[g]
        p, leave_c = u.p[i], u.leave[i]
        leave_t = land + (leave_c - p)
        held = {x["g"] for x in st}
        target, choice = leave_t, None
        for _ in range(4):
            opts = []
            for n, s in enumerate(st):
                if n == scr:
                    continue
                pl = plan(s, target, held)
                if pl:
                    opts.append((max(pl[0], target), pl[1], n, pl))
            if not opts:
                break
            go, _, n, pl = min(opts)
            tj = leave_t if go <= leave_t else land + (u.next_leave(p + (go - land)) - p)
            choice = (tj, n, pl)
            if tj <= target + 1e-6:
                break
            target = tj
        if choice is None or choice[0] >= T1:
            a, b = p, p + (min(T1, leave_t if choice is None else choice[0]) - land)
            visits.append((b - a, u.live_seconds(a, b), u.whole_plays(a, b), u.big(a, b)))
            break
        tj, n, pl = choice
        a, b = p, p + (tj - land)
        visits.append((b - a, u.live_seconds(a, b), u.whole_plays(a, b), u.big(a, b)))
        jumps.append(tj)
        st[scr].update(c=b, hid=tj)
        scr, land, unit = n, tj + flip, (pl[2], pl[3])
        st[scr]["g"] = unit[0]
    return {"jumps": jumps, "visits": visits, "span": (T0, T1)}


def live_run(games: list[dict], *, min_visit: float = 0.0, flip: float = FLIP_S, need_play: bool = True) -> dict:
    """Today's physics: both sticks at the live edge. Leave at a dead ball, land on a dead ball one flip later."""
    T0, T1 = span(games)
    n = int(T1 - T0)
    f = int(round(flip))
    dead = []
    for g in games:
        row = bytearray(n + f + 2)
        for s, x in g["dead"] + [(g["t1"], FAR), (-FAR, g["t0"])]:
            a, b = max(0, int(math.ceil(s - T0))), min(n + f + 2, int(x - T0))
            if b > a:
                row[a:b] = b"\x01" * (b - a)
        dead.append(row)
    jumps, visits = [], []
    cur, t, since = 0, 0, 0
    played = not need_play
    while t < n:
        if not dead[cur][t]:
            played = True
        if played and t - since >= min_visit and dead[cur][t]:
            tgt = next((h for h in range(len(games)) if h != cur and dead[h][t + f] and dead[h][t + f + 1]
                        and games[h]["t0"] - T0 <= t < games[h]["t1"] - T0), None)
            if tgt is not None:
                jumps.append(T0 + t)
                visits.append((float(t - since), float(sum(1 for z in range(since, t) if not dead[cur][z])), 1 if need_play else 0,
                               sum(v for bt, v in games[cur]["big"] if T0 + since <= bt <= T0 + t)))
                cur, t, since = tgt, t + f, t + f
                played = not need_play
                continue
        t += 1
    return {"jumps": jumps, "visits": visits, "span": (T0, T1)}


def pct(xs: list[float], q: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    return xs[min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))]


def summarize(runs: list[dict], big_total: float) -> dict:
    """Pace per minute and per window (median, 10th percentile, max over sliding windows), and what a visit holds."""
    out: dict = {"nights": len(runs)}
    mins = sum((r["span"][1] - r["span"][0]) / 60.0 for r in runs)
    nj = sum(len(r["jumps"]) for r in runs)
    out["minutes"] = round(mins, 1)
    out["jumps_per_min"] = round(nj / mins, 3) if mins else 0.0
    for w in (1,) + WINDOWS_MIN:
        counts = []
        for r in runs:
            a, b = r["span"]
            x = a
            while x + w * 60.0 <= b:
                counts.append(bisect.bisect_left(r["jumps"], x + w * 60.0) - bisect.bisect_left(r["jumps"], x))
                x += 30.0
        out[f"per_{w}m"] = [pct(counts, 0.5), pct(counts, 0.1), max(counts) if counts else float("nan")]
    for thr in (5, 7):          # the longest stretch, s, in which every trailing minute held at least this many jumps
        best = 0.0
        for r in runs:
            a, b = r["span"]
            x, run = a + 60.0, 0.0
            while x <= b:
                c = bisect.bisect_right(r["jumps"], x) - bisect.bisect_left(r["jumps"], x - 60.0)
                run = run + 10.0 if c >= thr else 0.0
                best = max(best, run + 60.0 if run else 0.0)
                x += 10.0
        out[f"longest_at_{thr}_s"] = best
    vs = [v for r in runs for v in r["visits"]]
    if vs:
        out["picture_s_median"] = round(statistics.median(v[0] for v in vs), 1)
        out["live_share"] = round(sum(v[1] for v in vs) / max(1e-9, sum(v[0] for v in vs)), 3)
        out["whole_play_share"] = round(sum(1 for v in vs if v[2] >= 1) / len(vs), 3)
        out["big_seen_share"] = round(sum(v[3] for v in vs) / big_total, 3) if big_total else float("nan")
    return out


def supply(nights: list[list[dict]]) -> dict:
    """Per game minute in progress: how many dead balls start, how many are long enough to park on, and the live share."""
    mins = dead_n = dead10 = dead25 = shots = 0.0
    live, dead_len = [], []
    for night in nights:
        for g in night:
            mins += (g["t1"] - g["t0"]) / 60.0
            dead_n += len(g["dead"])
            dead10 += sum(1 for s, x in g["dead"] if x - s >= 10)
            dead25 += sum(1 for s, x in g["dead"] if x - s >= 25)
            shots += len(g["shots"])
            dead_len += [x - s for s, x in g["dead"]]
            live += [b[0] - a[1] for a, b in zip(g["dead"], g["dead"][1:])]
    return {"game_minutes": round(mins), "dead_balls_per_game_min": round(dead_n / mins, 2), "dead_10s_plus_per_game_min": round(dead10 / mins, 2),
            "dead_25s_plus_per_game_min": round(dead25 / mins, 2), "shots_per_game_min": round(shots / mins, 2),
            "live_stretch_s": [pct(live, 0.25), pct(live, 0.5), pct(live, 0.75)], "dead_stretch_s": [pct(dead_len, 0.25), pct(dead_len, 0.5), pct(dead_len, 0.75)],
            "live_share": round(sum(live) / (mins * 60.0), 3)}


BURST_SETS = ("R3+R4 one-play, pause holds, skips", "R3+R4 pick shortest", "R3+R4 scrub only, pick shortest", "R2 any shot, error 2 s, shortest",
              "R5 four sticks, pick shortest", "what-if error 3 s, shortest", "what-if error 0, shortest",
              "what-if error 0, flip 0.5 s, 4 sticks, shortest", "what-if error 0, any shot, 4 sticks, flip 0.5")
BURST_MIN = (1, 2, 3, 5)


def burst(slates: list[list[dict]], kw: dict) -> dict:
    """From a full bank (every stick 175 s behind and set up): jumps in the first 1 / 2 / 3 / 5 minutes, over start times
    every 5 minutes through each night. [median, max]. The bank is the dead time a stick can skip before it reaches live."""
    got = {m: [] for m in BURST_MIN}
    for s in slates:
        a, b = span(s)
        t = a + 900.0
        while t + 300.0 <= b:
            j = park_run(s, start=t, stop=t + 300.0, **kw)["jumps"]
            for m in BURST_MIN:
                got[m].append(bisect.bisect_right(j, t + 60.0 * m))
            t += 300.0
    return {f"burst_{m}m": [pct(v, 0.5), pct(v, 0.9), max(v) if v else float("nan")] for m, v in got.items()}


# name -> (physics, keyword settings). The step 0 table: each rule set, then what each one thing buys.
RULE_SETS: dict[str, tuple[str, dict]] = {
    "R1 today, live edge, no park": ("live", {}),
    "R1 park at whistle, pause holds": ("park", {"park_at": "start", "skip": False}),
    "R3 one-play visits, pause holds, no skips": ("park", {"skip": False}),
    "R3+R4 one-play, pause holds, skips": ("park", {}),
    "R3+R4 pick shortest": ("park", {"pick": "short"}),
    "R3+R4 only scrub positions hold": ("park", {"q": 15.0}),
    "R3+R4 scrub only, pick shortest": ("park", {"q": 15.0, "pick": "short"}),
    "R2 any shot, error 5 s": ("park", {"shots": True}),
    "R2 any shot, error 2 s (what-if)": ("park", {"shots": True, "e": 2.0}),
    "R2 any shot, error 2 s, shortest": ("park", {"shots": True, "e": 2.0, "pick": "short"}),
    "R5 three sticks": ("park", {"sticks": 3}),
    "R5 four sticks": ("park", {"sticks": 4}),
    "R5 four sticks, pick shortest": ("park", {"sticks": 4, "pick": "short"}),
    "R6 min visit 12 s": ("park", {"min_visit": 12.0, "e": 0.0, "pick": "short"}),
    "R6 min visit 8 s (error 0, what-if)": ("park", {"min_visit": 8.0, "e": 0.0, "pick": "short"}),
    "R6 min visit 6 s (error 0, what-if)": ("park", {"min_visit": 6.0, "e": 0.0, "pick": "short"}),
    "R6 min visit 4 s (error 0, what-if)": ("park", {"min_visit": 4.0, "e": 0.0, "pick": "short"}),
    "what-if error 3 s, shortest": ("park", {"e": 3.0, "pick": "short"}),
    "what-if error 0, shortest": ("park", {"e": 0.0, "pick": "short"}),
    "what-if error 0, flip 0.5 s, 4 sticks, shortest": ("park", {"e": 0.0, "flip": 0.5, "sticks": 4, "pick": "short"}),
    "what-if error 0, any shot, 4 sticks, flip 0.5": ("park", {"e": 0.0, "flip": 0.5, "sticks": 4, "pick": "short", "shots": True}),
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--leagues", nargs="+", default=["nba", "nfl"])
    ap.add_argument("--games", nargs="+", type=int, default=[2, 3, 4, 6, 8])
    ap.add_argument("--cap", type=int, default=4, help="dev nights per league (those with the most games)")
    ap.add_argument("--only", nargs="*", default=None, help="rule-set names containing any of these")
    ap.add_argument("--cache", default=None, help="a folder OUTSIDE the repo for the per-game facts")
    ap.add_argument("--burst", action="store_true", help="also probe bursts from a full bank (slower)")
    ap.add_argument("--json", default=None)
    ap.add_argument("--csv", default=None)
    a = ap.parse_args()
    rows, facts = [], {}
    for lg in a.leagues:
        nights = load_nights(lg, a.cap, Path(a.cache) if a.cache else None)
        facts[lg] = supply(nights)
        print(lg, "supply", json.dumps(facts[lg]))
        for n in a.games:
            slates = [s for s in (slate(night, n) for night in nights) if s]
            if not slates:
                continue
            big_total = sum(v for s in slates for g in s for t, v in g["big"] if span(s)[0] <= t <= span(s)[1])
            for name, (physics, kw) in RULE_SETS.items():
                if a.only and not any(o.lower() in name.lower() for o in a.only):
                    continue
                if "shot" in name and League(lg).base != League.NBA:
                    continue
                runs = [live_run(s) if physics == "live" else park_run(s, **kw) for s in slates]
                row = {"league": lg, "games": n, "rule_set": name, **summarize(runs, big_total)}
                if a.burst and name in BURST_SETS:
                    row.update(burst(slates, kw))
                rows.append(row)
                print(f"{lg} N={n} {name:48s} {row['jumps_per_min']:6.2f}/min  5m {row['per_5m']}  60m {row['per_60m']}  "
                      f"pic {row.get('picture_s_median')}s  whole {row.get('whole_play_share')}  big {row.get('big_seen_share')}  "
                      f">=7 for {row['longest_at_7_s']:.0f}s", flush=True)
    if a.json:
        Path(a.json).write_text(json.dumps({"facts": facts, "rows": rows}, indent=1))
    if a.csv and rows:
        cols = ["league", "games", "rule_set", "nights", "minutes", "jumps_per_min"] + [f"per_{w}m_{s}" for w in (1,) + WINDOWS_MIN for s in ("med", "p10", "max")] \
            + ["longest_at_5_s", "longest_at_7_s", "picture_s_median", "live_share", "whole_play_share", "big_seen_share"] \
            + [f"burst_{m}m_{s}" for m in BURST_MIN for s in ("med", "p90", "max")]
        with open(a.csv, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(cols)
            for r in rows:
                flat = dict(r)
                for m in (1,) + WINDOWS_MIN:
                    flat[f"per_{m}m_med"], flat[f"per_{m}m_p10"], flat[f"per_{m}m_max"] = r[f"per_{m}m"]
                for m in BURST_MIN:
                    if f"burst_{m}m" in r:
                        flat[f"burst_{m}m_med"], flat[f"burst_{m}m_p90"], flat[f"burst_{m}m_max"] = r[f"burst_{m}m"]
                w.writerow([flat.get(c, "") for c in cols])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
