"""Replay dev nights for the thread ledger: how many threads open, how many the show saw the answer to, and what it cost.

    nice -n 15 python scripts/thread_replay.py --leagues nba nhl --cap 3 --json off.json
    nice -n 15 python scripts/thread_replay.py --leagues nba nhl --cap 3 --set threads.enabled=true --set threads.closure_first=true --json on.json

Why: D-ADDICT M1 (closure-first hold and cut) needs its measure: `closures seen %`, weighed against entries_mid_play,
high-leverage leaves, dead air, must-show seen and cuts per hour, flag off against flag on.

The threads are found from the night's whole play-by-play (real time), so the same list of questions grades both arms:
the flag only changes where the screen was. A closure is seen when the game was on screen for the whole answering play on
its delayed picture (`seen` in scripts/veto_audit.py, which also supplies must-see moments and their value). `close_run`
is counted but never in the core number: it is ledger only. Dev nights only (`--split` takes fit and nothing else).
Prints a decision hash per league: on the same tree, flag off against flag off it must match, and a run with the flag
off must match the base commit's hash. Info only: not a gate, nothing frozen is imported. No TV, no adb, no engine.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if ROOT.as_posix() not in sys.path:
    sys.path.insert(0, ROOT.as_posix())

from redzone.grading import lag_of  # noqa: E402
from redzone.lab import backtest, splits, sportsgrade  # noqa: E402
from redzone.moments import threads as T  # noqa: E402
from redzone.noggin.show import ShowDecider  # noqa: E402
from scripts.veto_audit import SCRIPTS, Air, card, must_see, parse_sets, seen  # noqa: E402


def night_threads(replays, params: T.Params) -> list[T.Thread]:
    """Every thread of the night, opened and closed on real time. A game that ends with one open closes it there."""
    out: list[T.Thread] = []
    for r in replays:
        led = T.Ledger()
        last = None
        for i, w in enumerate(r.wall):
            g = r.state_at(i)
            if g is not None:
                led.tick({r.gid: g}, None, w, params, 0.0)
                last = w
        for gid, t in list(led.open.items()):
            t.closes_on, t.closed_at, t.aired_at = "game_end", last, last
            led.closed.append(t)
            del led.open[gid]
        out.extend(led.closed)
    return out


def tally_threads(row: dict, threads: list[T.Thread], lag, screen: list, play_len: float) -> None:
    """One night's threads into the league's row. Seen = the answering play on screen; asked = on screen when it opened."""
    for t in threads:
        row["opened"][t.kind] += 1
        if t.kind not in T.HOLDABLE:
            continue
        at = float(t.closed_at) + lag_of(lag, t.game_id)
        answer = seen(Air(at, t.game_id, t.kind, 1.0), screen)
        asked = seen(Air(float(t.opened_at) + lag_of(lag, t.game_id) + play_len, t.game_id, t.kind, 1.0), screen)
        row["closed"] += 1
        row["seen"] += int(answer)
        row["asked"] += int(asked)
        row["asked_seen"] += int(asked and answer)
        row["by_kind"][t.kind][0] += 1
        row["by_kind"][t.kind][1] += int(answer)


def tally_must(row: dict, replays, lag, screen: list) -> None:
    """The must-see value on screen: the arbiter's floor, valued as scripts/veto_audit.py does."""
    for ms in must_see(replays, lag).values():
        for m in ms:
            row["must_total"] += m.value
            row["must_seen"] += m.value if seen(m, screen) else 0.0
            row["must_n"] += 1
            row["must_seen_n"] += int(seen(m, screen))


def new_row(league: str) -> dict:
    return {"league": league, "nights": 0, "opened": Counter(), "closed": 0, "seen": 0, "asked": 0, "asked_seen": 0,
            "by_kind": {k: [0, 0] for k in sorted(T.HOLDABLE)}, "must_total": 0.0, "must_seen": 0.0, "must_n": 0, "must_seen_n": 0,
            "cuts": 0, "hours": 0.0, "holds": Counter(), "decisions": 0, "hash": hashlib.sha256()}


def run_league(league: str, sets: dict, cap: int | None) -> dict:
    row, high, deciders = new_row(league), [], []
    real_run, real_nights, real_grade = backtest.run, splits.nights_for, sportsgrade.grade
    params = T.Params.from_config(lambda k, d=None: sets.get(k, d))

    class Recording(ShowDecider):
        def __init__(self, cfg, delays, clock=None):
            super().__init__(cfg, delays, clock=clock)
            deciders.append(self)

    def run(replays, cfg, my_teams, **kw):
        before = len(deciders)
        rep = real_run(replays, cfg, my_teams, **kw)
        tune_s = kw.get("tune_s", 5.0)
        screen = sorted((s.t + tune_s + s.arrive_dt, None if s.kind == "replay" else s.to) for s in rep.switches)
        row["nights"] += 1
        tally_threads(row, night_threads(replays, params), rep.lag_true, screen, backtest.PLAY_LEN_S if hasattr(backtest, "PLAY_LEN_S") else 7.0)
        tally_must(row, replays, rep.lag_true, screen)
        row["cuts"] += sum(1 for s in rep.switches if s.kind != "replay")
        row["hours"] += rep.hours
        for dec in deciders[before:]:
            row["holds"].update(dec.thread_holds)
        for d in rep.decisions:
            row["decisions"] += 1
            row["hash"].update(repr((round(d.at, 3), d.action, d.target, d.reason, d.state, d.gate, d.kind, d.want, round(d.rewind_s, 3), d.owner)).encode())
        return rep

    def nights_for(league_dir, which, end=None):
        if which != "fit":
            raise AssertionError(f"thread replay asked for split {which!r}: dev nights only")
        keep = real_nights(league_dir, which, end)
        if cap is None:
            return keep
        games = splits.archive_nights(league_dir, end)
        return set(sorted(keep, key=lambda n: (-len(games.get(n, ())), n))[:cap])

    def grade(*a, **kw):
        c = real_grade(*a, **kw)
        high.extend(c.cuts_high)
        return c

    backtest.Decider, backtest.run, splits.nights_for, sportsgrade.grade = Recording, run, nights_for, grade
    try:
        script, args = SCRIPTS[league]
        c = card(script, *args, "--split", "fit", *[x for k, v in sets.items() for x in ("--set", f"{k}={json.dumps(v)}")])
    finally:
        backtest.Decider, backtest.run, splits.nights_for, sportsgrade.grade = ShowDecider, real_run, real_nights, real_grade
        backtest._CALS.clear()
    nights = c.get("per_night", [])
    row["entries"] = sum(n["entries"] for n in nights)
    row["entries_mid_play"] = sum(n["entries_mid_play"] for n in nights)
    row["high_leverage_leaves"] = len(high)
    row["plays_missed_entry"] = c.get("plays_missed_entry", sum(n.get("plays_missed_entry", 0) for n in nights))
    row["dead_air_pct"] = c.get("dead_air_pct")
    row["hash"] = row["hash"].hexdigest()[:16]
    return json.loads(json.dumps(row))


def line(r: dict) -> str:
    n, c = max(r["nights"], 1), max(r["closed"], 1)
    opened = sum(r["opened"].values())
    return (f"{r['league']:<4} nights {r['nights']:<3} threads/night {opened / n:6.1f}  closures seen {100 * r['seen'] / c:5.1f}%  "
            f"asked-then-answered {100 * r['asked_seen'] / max(r['asked'], 1):5.1f}% (of {r['asked']})  "
            f"cuts/h {r['cuts'] / max(r['hours'], 1e-9):5.1f}  dead air {r['dead_air_pct']}  "
            f"mid-play {r['entries_mid_play']}/{r['entries']}  high-lev leaves {r['high_leverage_leaves']}  missed {r['plays_missed_entry']}  "
            f"must-show {100 * r['must_seen'] / max(r['must_total'], 1):5.1f}% ({r['must_seen_n']}/{r['must_n']})  "
            f"holds {sum(r['holds'].values())}  hash {r['hash']} ({r['decisions']})")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--leagues", nargs="+", default=list(SCRIPTS), choices=list(SCRIPTS))
    ap.add_argument("--cap", type=int, default=None, help="busiest N fit nights per league (default every fit night)")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="override one config value for this run")
    ap.add_argument("--json", type=Path, default=None)
    a = ap.parse_args()
    sets = parse_sets(a.set)
    rows = [run_league(lg, sets, a.cap) for lg in a.leagues]
    for r in rows:
        print(line(r))
    if a.json:
        a.json.write_text(json.dumps({"sets": sets, "rows": rows}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
