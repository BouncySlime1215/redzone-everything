"""Gym v2 scenarios: what one practice episode draws, from a seed, and the log that lets any episode be run again (A-01 slice 2).

    sc = sample(seed=7, n_games=4)                 # every draw comes from this seed only
    env = GymV2("20261023", 4, scenario=sc, draw_log="draws.jsonl")
    Scenario.from_dict(json.loads(line)["scenario"])   # the logged draw, run again exactly

Per episode (track A-director section 4), each number from a measurement (or marked ESTIMATE):
  padding rung   R0 no K (about 48 s, the replay's "nok" case), R1 10 or 8 s, R2 2 s, R3 1 or 2 s; equal weights.
                 A rung scales every error part of the show's timing model so a read stream pads at exactly that many
                 seconds (the way park_replay.prime_timing scales a Prime game); belief and truth move together.
  feed lag       per game, which feed the show reads ("league": NBA CDN, median 10.7 s; "espn": the summary, median
                 about 36 s; L111) and that game's own median (game medians spread 10.2-11.7 s on the CDN: sd 0.5 s;
                 ESPN's spread is an ESTIMATE, 2 s). On top, a heavy tail: a row now and then arrives late, and the feed
                 holds every later row back until it is in (rows come in order). The tail is tuned so the per-row lag
                 has the measured p90 (CDN about 60 s, ESPN about 67 s); L111 says that tail is partly late-STAMPED rows,
                 so as a delivery delay it is the pessimistic side. The show keeps believing its own 8 s.
  position error a fresh unread tune lands with sd U(1.2, 2.0) s (L18: 1.24 s espn, 1.98 s fs1), on the fake stick and
                 in the judge's truth.
  stick failures a place read fails 6 of 20 (L112), a park fails 1 of 19 (L112): independent per step.
  step times     the app's steps with park.fast_steps off or on (park_replay STEP_COSTS, priced from L112 / L116: tune
                 16.0 s, park 4.2 s, jump 6.9 s off and 2.7 s on), half each.
Not modelled yet (named, not hidden): late-edited stamps injected into the feed (L79; the recorded nights carry their
own), BACK undoing a pause (L74, one case, no rate), flip failures (0 of 19 on the TV), K error beyond the rung's model.

Why: EXP1b failed because the old practice world had too little variation (89% ties); every episode must draw its
world, and every draw must be reproducible from the log.
"""
from __future__ import annotations

import bisect
import json
import math
import random
from dataclasses import asdict, dataclass, replace
from pathlib import Path

SAMPLER = "gym2-scenario-v1"

# The measured numbers the sampler draws from: (value, source). Tests check the draws against these.
MEASURED = {
    "feed_median_s": {"league": (10.7, "L111: NBA CDN, 632 rows, 7 games"), "espn": (36.0, "L111 / study 03: ESPN summary")},
    "feed_p90_s": {"league": (60.0, "L111"), "espn": (67.0, "study 03 (L111 builder: 73.5)")},
    "feed_game_sd_s": {"league": (0.5, "L111: game medians 10.2-11.7 s"), "espn": (2.0, "ESTIMATE")},
    "tune_sd_s": ((1.2, 2.0), "L18: 1.24 s espn n=21, 1.98 s fs1 n=10"),
    "p_read_fail": (6 / 20, "L112: places read 14 of 20"),
    "p_park_fail": (1 / 19, "L112: 1 park of 19 failed"),
    "steps_s": ({"tune": 16.0, "park": 4.2, "jump_off": 6.9, "jump_on": 2.7}, "L112 (n=20), L116 bench"),
}
RUNGS = {"R0": (None,), "R1": (10, 8), "R2": (2,), "R3": (1, 2)}     # None = no K (the "nok" case, about 48 s)
RUNG_W = {"R0": 1.0, "R1": 1.0, "R2": 1.0, "R3": 1.0}
FEED_W = {"league": 0.5, "espn": 0.5}
FAST_W = 0.5
NOMINAL_FEED_LAG = 8.0          # what the replay (and the show) assume: park_replay's leads are built on it
# The late-row tail: a row is late with probability TAIL_P, by a lognormal extra (median TAIL_MED[src] s, sigma TAIL_SIG).
# Tuned with `tail_quantiles` on rows every 1-25 s (the NBA archive's spacing) so the per-row lag after hold-back has the
# measured p90; tests/test_gym2_scenario.py checks the realised quantiles.
TAIL_P = 0.03
TAIL_SIG = 0.6
TAIL_MED = {"league": 60.0, "espn": 48.0}


@dataclass(frozen=True)
class Scenario:
    """One episode's world. All defaults = the identity: the replay exactly as park_replay runs it."""
    name: str = "identity"
    seed: int | None = None
    rung: str = ""                    # "" = the replay's own timing
    pad_s: int | None = None          # the rung's padding for a read stream (None with R0 = no K)
    feed_src: tuple = ()              # per game: "league" | "espn"; () = the replay's nominal feed
    feed_lag_s: tuple = ()            # per game: that game's median lag
    tail_p: float = 0.0               # per row: probability it arrives late
    tail_med_s: tuple = ()            # per game: the late extra's median
    tail_sig: float = 0.0
    tune_sd: float | None = None      # a fresh unread tune's landing sd (None = the replay's)
    p_read_fail: float = 0.0
    p_park_fail: float = 0.0
    steps: str = "model"              # park_replay STEP_COSTS: model (as before) | off | on

    @property
    def identity(self) -> bool:
        return replace(self, name="identity") == IDENTITY

    def to_dict(self) -> dict:
        return {k: list(v) if isinstance(v, tuple) else v for k, v in asdict(self).items()}

    @classmethod
    def from_dict(cls, d: dict) -> "Scenario":
        return cls(**{k: tuple(v) if isinstance(v, list) else v for k, v in d.items()})

    # ---- what it changes -----------------------------------------------------------------------------------------
    def build_kwargs(self) -> dict:
        """Keyword arguments for park_replay.build (empty for the identity)."""
        kw: dict = {}
        if self.rung == "R0":
            kw["case"] = "nok"
        if self.pad_s is not None:
            kw["timing"] = lambda tm, p=int(self.pad_s): rung_timing(tm, p)
        if self.steps != "model":
            kw["step_costs"] = self.steps
        return kw

    def patch(self, b, pr) -> None:
        """Applied to a built replay (park_replay.Built) before it runs. Draws use their own RNGs (from `seed`), so the
        replay's own random streams are untouched."""
        show = b.show
        rig, judge = show.rig, show.judge
        base = 0 if self.seed is None else int(self.seed)
        if self.tune_sd is not None:
            rig.tune_sd = float(self.tune_sd)
            if judge is not None:
                judge.tm = truth_timing(judge.tm, float(self.tune_sd))
        if self.p_read_fail > 0 or self.p_park_fail > 0:
            frng, probs, orig = random.Random(base * 7919 + 17), {"read": self.p_read_fail, "pause": self.p_park_fail}, rig._fails

            def fails(what: str, _orig=orig, _p=probs, _r=frng) -> bool:
                if _orig(what):
                    return True
                p = _p.get(what, 0.0)
                return p > 0 and _r.random() < p
            rig._fails = fails
        if self.feed_src:
            n = len(b.rows.rs)
            lags = list(self.feed_lag_s) + [NOMINAL_FEED_LAG] * (n - len(self.feed_lag_s))
            meds = list(self.tail_med_s) + [0.0] * (n - len(self.tail_med_s))
            b.rows = LateRowFeed(pr, b.rows, lags, self.tail_p, meds, self.tail_sig, base * 104729 + 3)


IDENTITY = Scenario()


# ---------------------------------------------------------------------------------------------- the draws
def sample(seed: int, n_games: int, *, rung_w: dict | None = None, feed_w: dict | None = None, fast_w: float = FAST_W) -> Scenario:
    """One episode's world from `seed` alone."""
    rng = random.Random(f"{SAMPLER}:{int(seed)}")
    rung = _pick(rng, rung_w or RUNG_W)
    pad = rng.choice(RUNGS[rung])
    srcs = tuple(_pick(rng, feed_w or FEED_W) for _ in range(n_games))
    lags = tuple(round(max(1.0, rng.gauss(MEASURED["feed_median_s"][s][0], MEASURED["feed_game_sd_s"][s][0])), 3) for s in srcs)
    lo, hi = MEASURED["tune_sd_s"][0]
    return Scenario(name=f"draw-{int(seed)}", seed=int(seed), rung=rung, pad_s=pad, feed_src=srcs, feed_lag_s=lags,
                    tail_p=TAIL_P, tail_med_s=tuple(TAIL_MED[s] for s in srcs), tail_sig=TAIL_SIG,
                    tune_sd=round(rng.uniform(lo, hi), 4), p_read_fail=MEASURED["p_read_fail"][0],
                    p_park_fail=MEASURED["p_park_fail"][0], steps="on" if rng.random() < fast_w else "off")


def _pick(rng: random.Random, w: dict) -> str:
    keys = sorted(w)
    tot = sum(w[k] for k in keys)
    x, acc = rng.random() * tot, 0.0
    for k in keys:
        acc += w[k]
        if x < acc:
            return k
    return keys[-1]


def log_draw(path, *, night: str, games: int, episode_seed: int, scenario: Scenario, extra: dict | None = None) -> dict:
    """Append one draw to a JSONL log: enough to run the episode again (`Scenario.from_dict`; `extra`: e.g. the mix)."""
    row = {"sampler": SAMPLER, "night": str(night), "games": int(games), "episode_seed": int(episode_seed),
           "scenario": scenario.to_dict(), **(extra or {})}
    if path is not None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")
    return row


# ---------------------------------------------------------------------------------------------- the timing models
def rung_timing(tm, pad_s: int):
    """The show's timing with every game that has a K cut at exactly `pad_s` on a read stream: all four error parts
    scaled by one factor (as park_replay.prime_timing does). Games with no K keep their wide prior."""
    from redzone.noggin.park_show import ShowTiming
    from redzone.noggin.park_timing import GameTiming
    games = []
    for g in tm.g:
        if not g.known:
            games.append(g)
            continue
        p = g.params
        f = (float(pad_s) - 1e-6) / (p.pad_k * g.errors(True).total)
        q = replace(p, read_sd=p.read_sd * f, tune_sd=p.tune_sd * f, k_sd_min=p.k_sd_min * f, stamp_sd_nba=p.stamp_sd_nba * f,
                    stamp_sd_college=p.stamp_sd_college * f, stamp_sd_football=p.stamp_sd_football * f, key_sd=p.key_sd * f)
        games.append(GameTiming(g.league, replace(g.k, spread=g.k.spread * f), q))
    return ShowTiming(games, delay=tm.delay, read=tm.read, eyes=tm.eyes, params=tm.p)


def truth_timing(tm, tune_sd: float):
    """The judge's truth: as the show's model, except a stream whose tune was not read lands with sd `tune_sd`."""
    from redzone.noggin.park_show import ShowTiming
    from redzone.noggin.park_timing import GameTiming
    games = [GameTiming(g.league, g.k, replace(g.params, tune_sd=float(tune_sd))) for g in tm.g]
    return ShowTiming(games, delay=tm.delay, read=tm.read, eyes=tm.eyes, params=replace(tm.p, tune_sd=float(tune_sd)))


# ---------------------------------------------------------------------------------------------- the feed
def arrivals(wall: list, lag: float, tail_p: float, tail_med: float, tail_sig: float, rng: random.Random) -> list:
    """Wall-clock arrival of each row: stamp + the game's lag + (now and then) a late extra; rows come in order, so a late
    row holds back every row after it until it is in."""
    out, top = [], -math.inf
    mu = math.log(tail_med) if tail_med > 0 else 0.0
    for w in wall:
        extra = rng.lognormvariate(mu, tail_sig) if (tail_p > 0 and tail_med > 0 and rng.random() < tail_p) else 0.0
        top = max(top, w + lag + extra)
        out.append(top)
    return out


def tail_quantiles(wall: list, lag: float, tail_p: float, tail_med: float, tail_sig: float, seed: int = 0) -> tuple:
    """(median, p90) of the per-row lag (arrival less stamp) on one game's stamps: the sampler's calibration check."""
    arr = arrivals(wall, lag, tail_p, tail_med, tail_sig, random.Random(seed))
    lags = sorted(a - w for a, w in zip(arr, wall))
    return lags[len(lags) // 2], lags[int(0.9 * (len(lags) - 1))]


class LateRowFeed:
    """park_replay.RowFeed with each game's feed `lags[g]` s behind its stamps (instead of the nominal 8 s) and late rows
    (`arrivals`). Same polling, same hand-over (`FactsFeed.offer_rows`); only when each row comes in changes."""

    def __init__(self, pr, rows, lags: list, tail_p: float, tail_meds: list, tail_sig: float, seed: int):
        self.rs, self.feed, self.base, self.poll = rows.rs, rows.feed, rows.base, rows.poll
        self.leads, self.k, self.first = list(rows.leads), [0] * len(rows.rs), True
        rng = random.Random(seed)
        # a row stamped s is in once the show's edge t is past s - lead (park_replay.RowFeed); a lag L instead of the
        # nominal one moves that by L - nominal
        self.arr = [[a - (float(lags[g]) - NOMINAL_FEED_LAG) for a in
                     arrivals(r.wall, 0.0, tail_p, float(tail_meds[g]), tail_sig, rng)] for g, r in enumerate(self.rs)]

    def step(self, t: int) -> None:
        if t % self.poll and not self.first:
            return
        self.first = False
        for g, r in enumerate(self.rs):
            k = bisect.bisect_right(self.arr[g], self.base + t + self.leads[g])
            if k != self.k[g]:
                self.feed.offer_rows(g, r.plays[self.k[g]:k], t, final=k == len(r.plays), data=r.data)
                self.k[g] = k
            else:
                self.feed.at[g] = t
