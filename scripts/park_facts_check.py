"""Check the live PARK facts builder against the grader's whole-game facts, row by row, on recorded dev nights.

    nice -n 19 python scripts/park_facts_check.py --nights 2          # the two NBA dev nights with the most games
    nice -n 19 python scripts/park_facts_check.py --night 20261023

For every game of each night it feeds `redzone.noggin.park_facts.GameFacts` the rows one at a time, in feed order,
and after each row compares every dead stretch the builder calls final (ended before `settled`) with
`scripts/park_ceiling.truth_of` (the grader's `dead_at` read with the whole game in hand). It also counts:
- the open-stoppage hole (ledger L25): seconds just before the newest row that the grader, reading only the rows so
  far, calls "still stopped" though the whole game says play ran there and the clock had already moved by that row;
  the builder's view must call each of them play;
- any moment the builder calls a stoppage open after a later row of its period showed the clock moved (must be 0);
- whether, once the game is over, the builder's planner view equals truth_of exactly (every key).

Dev (`fit`) nights only; a locked night is never opened. No TV, no adb, no network. Numbers only on stdout.

Why: the planner was proven on hindsight facts; the app gets rows one at a time, and this is the proof that what the
builder calls final is exactly the grader's answer, and that it never calls a stoppage open once the clock has moved.
"""
from __future__ import annotations

import argparse
import bisect
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT.as_posix(), (ROOT / "scripts").as_posix()):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import park_ceiling as pc  # noqa: E402
from redzone.grading import dead_at, play_clock  # noqa: E402
from redzone.lab import splits  # noqa: E402
from redzone.lab.backtest import Replay  # noqa: E402
from redzone.models import League  # noqa: E402
from redzone.noggin.park_facts import GameFacts  # noqa: E402
from redzone.sources import espn  # noqa: E402


def dev_nights(league: str = "nba", count: int = 2) -> list[str]:
    """The `count` dev nights with the most games (park_ceiling.load_nights' order)."""
    d = ROOT / "data/archive" / league
    keep = splits.nights_for(d, "fit") or set()
    games = splits.archive_nights(d)
    return sorted(keep, key=lambda n: (-len(games.get(n, ())), n))[:count]


def replays(night: str, league: str = "nba") -> list:
    """The night's games as the grader loads them (60 rows or more, as park_ceiling.load_nights keeps them)."""
    d = ROOT / "data/archive" / league
    if night not in (splits.nights_for(d, "fit") or set()):
        raise SystemExit(f"{night} is not a dev night")
    out = []
    for gid in splits.archive_nights(d).get(night, ()):
        f = d / f"summary_{gid}.json"
        r = Replay.load(League(league), f) if f.exists() else None
        if r is not None and len(r.plays) >= 60:
            out.append(r)
    return out


def _moved_after(gf: GameFacts, row_t: float) -> bool:
    """Did any row after the row stamped row_t (the last such), in its period, show the game clock moved?"""
    i = bisect.bisect_right(gf.wall, row_t) - 1
    lg, p = gf.league, gf.plays[i]
    per, c0 = espn._int(espn._get(p, "period", "number")), play_clock(lg, p)
    for q in gf.plays[i + 1:]:
        if espn._int(espn._get(q, "period", "number")) != per:
            return False
        c = play_clock(lg, q)
        if c is not None and c0 is not None and c < c0 - 0.05:
            return True
    return False


def check_game(r) -> dict:
    """Feed one recorded game row by row; count agreements and every failure."""
    truth = pc.truth_of(r)
    ends = [b for _, b in truth["dead"]]
    gf = GameFacts(r.league, r.gid)
    out = {"rows": 0, "compared": 0, "stretches": 0, "confirmed": 0, "mismatch": 0, "opens": 0, "open_after_move": 0,
           "hole": 0, "hole_closed": 0, "final_equal": False, "first_mismatch": None}
    last = float("-inf")
    for k, p in enumerate(r.plays):
        gf.add(p)
        fx = gf.facts()
        if fx.settled < last:
            out["mismatch"] += 1                              # the frontier never moves back
        last = fx.settled
        m = bisect.bisect_left(ends, fx.settled)              # the truth's stretches that end before `settled`
        want = list(zip(truth["dead"][:m], truth["brk"][:m]))
        got = list(zip(fx.dead, fx.brk))
        out["compared"] += 1
        out["stretches"] += len(want)
        out["confirmed"] = max(out["confirmed"], len(want))
        if want != got:
            out["mismatch"] += 1
            out["first_mismatch"] = out["first_mismatch"] or k
        if fx.open is not None:
            out["opens"] += 1
            if _moved_after(gf, fx.open.row):
                out["open_after_move"] += 1
        # the hole: the second before this row; the grader on these rows says stopped, the whole game says play
        st = r.wall[k] - 1.0
        if k + 1 < len(r.plays) and r.wall[k + 1] > r.wall[k] and st >= r.wall[0]:
            i = bisect.bisect_right(r.wall, st) - 1
            so_far = dead_at(r.league, r.wall[:k + 1], r.plays[:k + 1], st)[0]
            if so_far and not dead_at(r.league, r.wall, r.plays, st)[0] and gf._restart(i)[0] is not None:
                out["hole"] += 1
                view, _ = gf.planner_view(r.wall[-1] + 5000, with_value=False)
                if not any(a <= st < b for a, b in view["dead"]):
                    out["hole_closed"] += 1
    gf.data = r.data
    gf.set_final()
    view, unsure = gf.planner_view(r.wall[-1] + 5000)
    out["final_equal"] = view == truth and unsure is None
    out["rows"] = len(r.plays)
    return out


def check_night(night: str, league: str = "nba") -> dict:
    tot = {"night": night, "games": 0, "rows": 0, "compared": 0, "stretches": 0, "confirmed": 0, "mismatch": 0, "opens": 0,
           "open_after_move": 0, "hole": 0, "hole_closed": 0, "final_equal": 0}
    for r in replays(night, league):
        g = check_game(r)
        tot["games"] += 1
        for key in ("rows", "compared", "stretches", "confirmed", "mismatch", "opens", "open_after_move", "hole", "hole_closed"):
            tot[key] += g[key]
        tot["final_equal"] += int(g["final_equal"])
    return tot


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--nights", type=int, default=2, help="this many NBA dev nights, most games first")
    ap.add_argument("--night", action="append", help="a dev night by date (repeatable)")
    a = ap.parse_args()
    bad = 0
    for night in a.night or dev_nights("nba", a.nights):
        t = check_night(night)
        print(json.dumps(t))
        bad += t["mismatch"] + t["open_after_move"] + (t["hole"] - t["hole_closed"]) + (t["games"] - t["final_equal"])
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
