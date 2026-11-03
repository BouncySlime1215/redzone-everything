"""Training lab: search decider settings against real past slates.

Objective (higher is better), with a hard rule that mid-play cuts must be zero:
    big-play % seen + 0.3 x big-play % live - 0.7 x modeled commercial time %

Switch frequency is reported, not capped or penalized. A safe opportunity can be taken
as soon as the controller and screen evidence permit it.

Random search, then a local polish around the best. Writes the winner and a
leaderboard to data/lab/. Nothing changes in your live settings until you apply it.
"""
from __future__ import annotations

import copy
import json
import random
import time
from pathlib import Path

from redzone.config import Config, _deep_merge, without_retired
from redzone.lab.backtest import Replay, run
from redzone.models import League

ROOT = Path(__file__).resolve().parent.parent.parent
SPACE = {
    "decider.switch_margin": [6, 8, 10, 12, 15],
    "decider.chase_max_deficit": [10, 15, 25, 40],
    "decider.break_destination_min": [10, 20, 25, 35],
    "decider.return_tolerance": [0, 5, 10],
    "decider.locked_override_lead": [15, 20, 30],
    "replay.clip_s": [12, 15, 20],
    "replay.max_age_s": [900, 1800, 2700],
}


def load_slates(arch: Path = ROOT / "data/archive/nfl", end: str | None = None) -> list[list[Replay]]:
    """Every 1 pm / 4 pm ET NFL slate with 2+ games, oldest first; only nights <= `end` (YYYYMMDD)."""
    slates = []
    for sb_path in sorted(arch.glob("scoreboard_*.json")):
        if end and sb_path.stem.split("_")[-1] > str(end):
            continue
        sb = json.loads(sb_path.read_text())
        for hh in ("18:", "21:"):
            ids = [e["id"] for e in sb.get("events", []) if hh in e.get("date", "")]
            reps = [r for r in (Replay.load(League.NFL, arch / f"summary_{i}.json") for i in ids
                                if (arch / f"summary_{i}.json").exists()) if r]
            if len(reps) >= 2:
                slates.append(reps)
    return slates


class _Overlay(Config):
    """A Config with extra overrides, never saved to disk."""

    def __init__(self, base: Config, params: dict):
        self.defaults = base.defaults
        self.overrides = {}
        nested: dict = {}
        for k, v in params.items():
            node = nested
            parts = k.split(".")
            for p in parts[:-1]:
                node = node.setdefault(p, {})
            node[parts[-1]] = v
        self.data = without_retired(_deep_merge(base.data, nested))


def evaluate(base: Config, params: dict, slates, my_teams: set[str]) -> dict:
    cfg = _Overlay(base, params)
    tot_big = tot_live = tot_seen = cuts = 0
    dead = watched = switches = hours = 0.0
    for reps in slates:
        for r in reps:
            r._cache.clear() if len(r._cache) > 5000 else None
        rep = run(reps, cfg, my_teams)
        tot_big += rep.big_plays
        tot_live += rep.big_plays_live
        tot_seen += rep.big_plays_caught
        cuts += len(rep.mid_play_cuts)
        dead += rep.dead_air_s
        watched += rep.watched_s
        switches += len(rep.switches)
        hours += rep.hours
    live_pct = 100 * tot_live / max(1, tot_big)
    dead_pct = 100 * dead / max(1, watched)
    sph = switches / max(0.1, hours)
    seen_pct = 100 * tot_seen / max(1, tot_big)
    score = seen_pct + 0.3 * live_pct - 0.7 * dead_pct - cuts * 1000
    return {"score": round(score, 2), "big_seen_pct": round(seen_pct, 1), "big_live_pct": round(live_pct, 1), "dead_air_pct": round(dead_pct, 1),
            "switches_per_hour": round(sph, 1), "mid_play_cuts": cuts, "params": params}


def search(n_random: int = 60, n_polish: int = 30, seed: int = 7, my_teams: set[str] | None = None,
           max_slates: int = 14) -> dict:
    rng = random.Random(seed)
    base = Config(user_path=ROOT / "data/config.yaml")
    my_teams = my_teams or set(base.get("teams.nfl"))
    slates = load_slates()
    if len(slates) > max_slates:  # spread evenly across the season
        step = len(slates) / max_slates
        slates = [slates[int(i * step)] for i in range(max_slates)]
    current = {k: base.get(k) for k in SPACE}
    results = [evaluate(base, current, slates, my_teams) | {"label": "current"}]
    t0 = time.time()
    for _ in range(n_random):
        p = {k: rng.choice(v) for k, v in SPACE.items()}
        results.append(evaluate(base, p, slates, my_teams))
    best = max(results, key=lambda r: r["score"])
    for _ in range(n_polish):
        p = copy.deepcopy(best["params"])
        k = rng.choice(list(SPACE))
        p[k] = rng.choice(SPACE[k])
        r = evaluate(base, p, slates, my_teams)
        results.append(r)
        if r["score"] > best["score"]:
            best = r
    out = ROOT / "data/lab"
    out.mkdir(parents=True, exist_ok=True)
    board = sorted(results, key=lambda r: -r["score"])[:15]
    report = {"ran_at": time.time(), "seconds": round(time.time() - t0), "slates": len(slates),
              "current": results[0], "best": best, "leaderboard": board}
    (out / "optimize.json").write_text(json.dumps(report, indent=1))
    return report


if __name__ == "__main__":
    rep = search()
    print(json.dumps({"current": rep["current"], "best": rep["best"]}, indent=1))
