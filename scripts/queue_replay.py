"""Replay queue decisions from a scenario file, offline, and time them for a 32-game slate.

Why: WP-02 (P-QUEUE Q3, Q4, Q10) needs a way to read what the pure queue would do with no TV and no engine,
and contract rule K5 needs a caller until the engine wires it in (WP-12).

    python scripts/queue_replay.py                  # the built-in scenario
    python scripts/queue_replay.py --scenario f.json
    python scripts/queue_replay.py --bench          # median and p99 of one full tick plus view, 32 games

A scenario is {"games": {id: <Entry fields>}, "ticks": [...]}. A tick has `now`, `on_screen`, `hidden`, an
optional `stick` (<Stick fields>), optional `set` ({id: fields to change}) and optional `fail`
({"game", "failure", "attempt"}). Routes are lists of {"app", "ref", ...}. Pure: reads only the scenario.
"""
from __future__ import annotations

import argparse
import dataclasses as dc
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from redzone.noggin import gamequeue as gq  # noqa: E402

YT, PRIME = "youtubetv", "prime"
BUILTIN = {
    "games": {
        "WSH": {"routes": [{"app": PRIME, "ref": "w", "klass": gq.LONG_LEAD}], "level": 1, "brain_rank": 1, "value": 80},
        "DAL": {"routes": [{"app": YT, "ref": "d"}], "level": 4, "value": 40},
        "GB": {"routes": [{"app": YT, "ref": "g"}, {"app": "espn", "ref": "g2", "klass": gq.LIVE_EDGE}], "level": 2,
               "brain_rank": 2, "value": 55},
        "KC": {"routes": [{"app": YT, "ref": "k"}], "level": 2, "planner_rank": 1, "value": 30},
    },
    "ticks": [
        {"now": 0, "on_screen": "DAL", "hidden": None},
        {"now": 10, "on_screen": "DAL", "hidden": "GB", "set": {"GB": {"level": 4}}},
        {"now": 20, "on_screen": "DAL", "hidden": "GB",
         "set": {"KC": {"tier": 1, "join_in_s": -5, "lookahead_s": 75, "value": 30}}},
        {"now": 30, "on_screen": "DAL", "hidden": "GB", "fail": {"game": "KC", "failure": "never_started", "attempt": 1}},
        {"now": 40, "on_screen": "DAL", "hidden": "GB", "set": {"WSH": {"user": "lock"}}},
        {"now": 50, "on_screen": "DAL", "hidden": "WSH", "fail": {"game": "WSH", "failure": "walled"}},
        {"now": 60, "on_screen": "DAL", "hidden": None},
    ],
}


def entry(game: str, raw: dict) -> gq.Entry:
    fields = dict(raw)
    fields["routes"] = tuple(gq.Route(**r) for r in fields.get("routes", ()))
    return gq.Entry(game=game, **fields)


def stick(raw: dict | None) -> gq.Stick:
    return gq.Stick(**(raw or {}))


def replay(scenario: dict) -> list[dict]:
    """One line per tick. A failure changes the game for the ticks after it."""
    games = {g: entry(g, raw) for g, raw in scenario["games"].items()}
    out = []
    for t in scenario["ticks"]:
        for g, change in t.get("set", {}).items():
            games[g] = dc.replace(games[g], **change)
        line = {"now": t["now"]}
        if "fail" in t:
            f = t["fail"]
            move = gq.on_failure(games[f["game"]], f["failure"], f.get("attempt", 0))
            games[f["game"]] = move.entry
            line["failure"] = {"game": f["game"], "kind": move.failure.value, "step": move.step.value,
                               "budget_s": move.budget_s, "backoff_s": move.backoff_s, "phone": move.phone}
        plan = gq.tick(list(games.values()), t.get("on_screen"), t.get("hidden"), stick(t.get("stick")), float(t["now"]))
        line["queue"] = gq.view(plan)
        out.append(line)
    return out


def slate(n: int = 32) -> list[gq.Entry]:
    """A hard slate for the timing check: every rung present, several routes per game."""
    rows = []
    for i in range(n):
        routes = (gq.Route(YT, f"y{i}", search=i % 5 == 0), gq.Route(PRIME, f"p{i}", klass=gq.LONG_LEAD))
        rows.append(gq.Entry(
            f"G{i:02d}", routes[::1 if i % 3 else -1], level=1 + i % 5, user=("", "pin", "lock", "")[i % 4] if i % 7 == 0 else "",
            tier=(None, 1, 2)[i % 3], join_in_s=-(i % 30.0) if i % 3 else None, lookahead_s=75.0,
            lead_s=float(i * 8), brain_rank=float(i) if i % 2 else None, planner_rank=float(i), value=float(i * 3)))
    return rows


def bench(runs: int = 2000) -> dict:
    games, s, took = slate(), gq.Stick(busy_until=105.0, job_game="G03", job_to=3), []
    for _ in range(runs):
        t0 = time.perf_counter()
        gq.view(gq.tick(games, "G00", "G01", s, 100.0))
        took.append((time.perf_counter() - t0) * 1000)
    took.sort()
    return {"games": len(games), "runs": runs, "median_ms": round(statistics.median(took), 4),
            "p99_ms": round(took[int(runs * .99)], 4), "max_ms": round(took[-1], 4)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scenario", type=Path)
    ap.add_argument("--bench", action="store_true")
    a = ap.parse_args()
    if a.bench:
        print(json.dumps(bench()))
        return 0
    scenario = json.loads(a.scenario.read_text(encoding="utf-8")) if a.scenario else BUILTIN
    for line in replay(scenario):
        print(json.dumps(line, allow_nan=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
