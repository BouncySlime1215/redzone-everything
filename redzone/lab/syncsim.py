"""[auto sync] Sync-error scorecard: how far the delay the app uses is from the screen's real delay.

Feed side is REAL: every play of a recorded night (data/recordings/<night>/plays.jsonl) with its
feed wallclock and the time our feed actually delivered it. Screen side is SIMULATED, because no
night has been recorded on screen yet (data/sync.jsonl starts that): each channel has a true delay
(spread like the channels measured on the sticks), a per-tune offset behind the live edge, a slow
drift and occasional stalls; the viewer tunes to a live game every few minutes; and the stick and
the broadcast give the signals the real code reads, with their real limits:

    YouTube TV   position reads every 5 s (stick clock pinned ~0.3 s); a quarter-hour show-title
                 flip only when a show changes (rare during games); the picture is DRM-blanked, so no
                 score bug
    Prime        no position; the score bug (a score's first frame, half a 2 s capture gap) and its
                 game clock while it runs. A bug change is usable only once the feed has that play:
                 with feed lags of 35-80 s that is often after the change aired (pending matching)

Graded every second while a game is on screen, after the first `settle_s` of each tune:
error of the delay used, the share under 2 s, how often the stated interval (+-2 sigma) misses
(a miss is what cuts plays mid-play), how long each tune took to sync, and the uncertain flags.

Why: first added in 84e73039 (2026-11-03) as "Per-channel delay predictor and automatic Kalman sync"; no separate decision is recorded for this module.
"""
from __future__ import annotations

import bisect
import json
import math
import random
import re
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from redzone.decider.screen import DelayBook

SCORING = re.compile(r"\bmakes\b|touchdown|field goal is good|\bgoal\b", re.I)
CLOCK_RUNS = {"nba": 0.55, "cbb": 0.55, "wcbb": 0.55, "nhl": 0.6, "nfl": 0.3}


@dataclass
class Game:
    gid: str
    league: str
    plays: list = field(default_factory=list)      # (wall, arrived, scoring)

    @property
    def start(self):
        return self.plays[0][0]

    @property
    def end(self):
        return self.plays[-1][0]


def load_night(path: Path) -> list[Game]:
    games: dict[str, Game] = {}
    for line in path.read_text().splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("wall") is None or r.get("t") is None:
            continue
        g = games.setdefault(str(r["game"]), Game(str(r["game"]), str(r.get("league", ""))))
        g.plays.append((float(r["wall"]), float(r["t"]),
                        bool(SCORING.search(f"{r.get('type', '')} {r.get('text', '')}"))
                        and "miss" not in (r.get("text") or "").lower()))
    out = []
    for g in games.values():
        g.plays.sort()
        if len(g.plays) >= 20:
            out.append(g)
    return out


@dataclass
class Channel:
    target: str
    app: str
    base: float          # true delay at the live edge (s)
    drift: float         # s per s
    origin: float        # stream origin (wall) for position reads
    t0: float

    def delay(self, t: float, tune_off: float, stall: float) -> float:
        return self.base + self.drift * (t - self.t0) + tune_off + stall


@dataclass
class Card:
    seconds: int = 0
    err_sum: float = 0.0
    under2: int = 0
    misses: int = 0                 # truth outside the stated +-2 sigma
    errs: list = field(default_factory=list)
    tunes: int = 0
    synced_tunes: int = 0           # tunes synced (filter says so AND truly within 2 s) by 120 s
    sync_times: list = field(default_factory=list)
    false_synced: int = 0           # seconds the filter's delay was in use and truth fell outside +-2 sigma
    uncertain: int = 0
    by_app: dict = field(default_factory=dict)

    def summary(self) -> dict:
        e = sorted(self.errs)
        q = lambda p: round(e[min(len(e) - 1, int(p * len(e)))], 2) if e else None
        st = sorted(self.sync_times)
        return {"seconds_graded": self.seconds,
                "mean_error_s": round(self.err_sum / max(1, self.seconds), 2),
                "median_error_s": q(0.5), "p95_error_s": q(0.95),
                "under_2s_pct": round(100 * self.under2 / max(1, self.seconds), 1),
                "interval_miss_pct": round(100 * self.misses / max(1, self.seconds), 2),
                "tunes": self.tunes,
                "synced_within_120s_pct": round(100 * self.synced_tunes / max(1, self.tunes), 1),
                "median_time_to_sync_s": st[len(st) // 2] if st else None,
                "false_synced_s": self.false_synced,
                "uncertain_pct": round(100 * self.uncertain / max(1, self.seconds), 1),
                "by_app": {a: {"under_2s_pct": round(100 * v[1] / max(1, v[0]), 1),
                               "mean_error_s": round(v[2] / max(1, v[0]), 2)} for a, v in sorted(self.by_app.items())}}


def simulate(games: list[Game], seed: int = 0, sync: bool = True, settle_s: float = 120.0,
             prime_share: float = 0.35, card: Card | None = None, defer: bool | None = None,
             preload: bool = False) -> Card:
    """One recorded night. sync=False: the signals the old code used go to the delay book the old way
    (show changes and score-bug samples, matched only at the change; no positions, no clock), for the
    before/after. defer (default: = sync): score-bug changes and clock reads wait for the feed."""
    defer = sync if defer is None else defer
    # preload: the hidden stick holds each YouTube TV game from before its start (the plan's
    # pre-game check, tonight.py), so it reads the pregame -> game title flip and a position there
    card = card or Card()
    if not games:
        return card
    # the world (channels, viewing, stalls) and the signals' noise draw from separate streams, so the
    # before/after see the very same night on screen
    rng = random.Random(seed * 1_000_003 + zlib.crc32(games[0].gid.encode()))
    noise = random.Random(seed * 7_000_003 + zlib.crc32(games[0].gid.encode()))
    clock = [0.0]
    book = DelayBook({"youtubetv": 45.0, "prime": 40.0}, 15.0, clock=lambda: clock[0])
    sb = book.attach_sync(min_sigma_s=4.0) if sync else None
    t_start = min(g.start for g in games)
    t_end = max(g.end for g in games)
    chans = {}
    for g in games:
        app = "prime" if rng.random() < prime_share else "youtubetv"
        chans[g.gid] = Channel(f"{app}:{g.gid}", app, min(75.0, max(15.0, rng.gauss(35.0, 8.0))),
                               rng.gauss(0.0, 0.5) / 3600.0, t_start - rng.uniform(1e4, 1e5), t_start)
    t = t_start
    cur, tune_off, stall, tuned_at, next_switch = None, 0.0, 0.0, t, t
    next_stall, synced_this_tune, flip_done = t + rng.expovariate(1 / 2700.0), False, set()
    pending: list[tuple] = []          # (usable from, target, true delay at onset, onset, kind)
    kicks = {g.gid: math.floor(g.start / 900.0) * 900.0 for g in games}
    while t <= t_end:
        clock[0] = t
        if preload and sb is not None:
            for g in games:
                hc = chans[g.gid]
                if hc.app != "youtubetv" or (cur is not None and g.gid == cur.gid):
                    continue
                hd = hc.delay(t, 0.0, 0.0)
                if abs(t - (kicks[g.gid] + hd)) < 0.5 and (hc.target, kicks[g.gid]) not in flip_done:
                    flip_done.add((hc.target, kicks[g.gid]))
                    sb.position(hc.target, t - hc.origin - hd + noise.gauss(0, 0.3), t=t, sd=0.4)
                    sb.measure(hc.target, hd + noise.gauss(0.8, 1.5), "flip", t=t, sd=3.0)
        if t >= next_switch or cur is None or t > cur.end + 60:
            live = [g for g in games if g.start - 60 <= t <= g.end]
            if live:
                nxt = rng.choice(live)
                if cur is None or nxt.gid != cur.gid:
                    cur = nxt
                    ch = chans[cur.gid]
                    tune_off = rng.uniform(0, 6) if ch.app == "youtubetv" else rng.uniform(0, 10)
                    stall, tuned_at, synced_this_tune = 0.0, t, False
                    pending = [p for p in pending if p[1] != ch.target]
                    book.tuned(ch.target)
                    if sb is None:
                        book.set_live(ch.target, None)
                    card.tunes += 1
            next_switch = t + rng.uniform(180, 720)
        if cur is None:
            t += 1.0
            continue
        ch = chans[cur.gid]
        if t >= next_stall:                                # a stall: the stream falls a few s behind
            stall += rng.uniform(2, 6)
            next_stall = t + rng.expovariate(1 / 2700.0)
        true = ch.delay(t, tune_off, stall)
        # ---- signals
        if ch.app == "youtubetv":
            if int(t) % 5 == 0 and sb is not None:
                pos = t - ch.origin - true + noise.gauss(0, 0.3)
                sb.position(ch.target, pos, t=t, sd=0.4)
            # A show change on the quarter hour: the pregame show -> the game (the quarter hour before
            # the first play) always; during a game, rarely (10%: a studio cut-in, a channel's own show).
            q = round(t / 900.0) * 900.0
            kick = math.floor(cur.start / 900.0) * 900.0
            if abs(t - (q + true)) < 0.5 and (ch.target, q) not in flip_done and (
                    q == kick or noise.random() < 0.1):
                flip_done.add((ch.target, q))
                z = true + noise.gauss(0.8, 1.5)
                if sb is not None:
                    sb.measure(ch.target, z, "flip", t=t, sd=3.0)
                book.boundary(ch.target, z, t)
        else:
            for w, arrived, scoring in cur.plays:
                if scoring and abs(w + true - t) < 0.5:     # the new score reaches the screen now
                    onset_err = noise.uniform(0, 2.0) + noise.gauss(0.5, 0.5) + noise.uniform(-0.5, 0.5)
                    # the old watcher matches only at the change (two frames, ~4 s): the feed must have it
                    if defer or arrived <= t + 4.0:
                        pending.append((max(arrived, t), ch.target, true + onset_err, t, "score"))
            if defer and int(t) % 10 == 0 and noise.random() < CLOCK_RUNS.get(cur.league, 0.4) and sb is not None:
                # the bug clock read now: matched once the feed has the next play after it (real arrival)
                walls = [w for w, _, _ in cur.plays]
                k = bisect.bisect_left(walls, t - true)
                if k < len(walls):
                    pending.append((cur.plays[k][1], ch.target, true + noise.gauss(0, 1.0), t, "clock"))
        for p in [p for p in pending if p[0] <= t]:
            pending.remove(p)
            _, target, z, onset, kind = p
            if t - onset > 120.0:
                continue                                   # too old to match
            if sb is not None:
                sb.measure(target, z, kind, t=onset)
            if kind == "score":
                book.screen(target, z, onset)
        # ---- grade
        d = book.get(ch.target)
        err = abs(d.mu - true)
        if not synced_this_tune and err < 2.0 and (sb is None or d.source == "sync"):
            synced_this_tune = True
            if t - tuned_at <= 120.0:
                card.synced_tunes += 1
            card.sync_times.append(round(t - tuned_at))
        if t - tuned_at >= settle_s:
            card.seconds += 1
            card.err_sum += err
            card.under2 += err < 2.0
            card.misses += err > 2 * d.current_sigma(t)
            if len(card.errs) < 400_000:
                card.errs.append(err)
            if sb is not None:
                f = sb.filters.get(ch.target)
                card.uncertain += bool(f and f.uncertain(t))
                card.false_synced += bool(d.source == "sync" and err > 2 * d.current_sigma(t))
            a = card.by_app.setdefault(ch.app, [0, 0, 0.0])
            a[0] += 1
            a[1] += err < 2.0
            a[2] += err
        t += 1.0
    return card


def nights(root: Path) -> list[tuple[str, list[Game]]]:
    return [(p.parent.name, load_night(p)) for p in sorted(root.glob("*/plays.jsonl"))]


def log_summary(path: Path) -> dict:
    """A real night's data/sync.jsonl: per channel, time to sync after each tune, share of rows synced
    and flagged, and the last estimate +- error. No ground truth: the error is the filter's own."""
    rows = []
    for line in path.read_text().splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    out: dict[str, dict] = {}
    tune_at: dict[str, float] = {}
    for r in rows:
        t, tg = r.get("t"), r.get("target")
        o = out.setdefault(tg, {"signals": {}, "tunes": 0, "sync_after_s": [], "rows": 0, "synced_rows": 0,
                                "uncertain_rows": 0, "last": None})
        o["rows"] += 1
        o["signals"][r.get("kind")] = o["signals"].get(r.get("kind"), 0) + 1
        o["synced_rows"] += bool(r.get("synced"))
        o["uncertain_rows"] += bool(r.get("uncertain"))
        if r.get("kind") == "tune":
            o["tunes"] += 1
            tune_at[tg] = t
        elif r.get("synced") and tg in tune_at:
            o["sync_after_s"].append(round(t - tune_at.pop(tg), 1))
        if r.get("mu") is not None:
            o["last"] = {"delay_s": r["mu"], "error_s": r.get("sd")}
    for o in out.values():
        s = sorted(o["sync_after_s"])
        o["median_sync_after_s"] = s[len(s) // 2] if s else None
        del o["sync_after_s"]
    return out

