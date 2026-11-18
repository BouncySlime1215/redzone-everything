"""Director v1 off against on: the phase 2C scorecard on recorded dev nights, with fake sticks (no device).

    python scripts/park_director_ab.py                                  # the 3 NBA nights (4 games) and the NFL + Prime pinned case
    python scripts/park_director_ab.py --night 20261023 --games 4       # one night
    python scripts/park_director_ab.py --out results.json               # also the rows as JSON

Each case runs `park_replay.replay` twice: as shipped (park.seen and park.director_v1 off) and with Director v1 on
(redzone/noggin/director.py, its numbers and the league weights from the default config). One row per run:
    jumps a minute; big plays seen live (of the must-see plays in the span, on screen with their lead-in as they
    happened); the longest any game in progress went unseen (and how much of that was live play); the mean age of what
    the viewer knows of a game; the frozen grader's Rule #1 counts (cutgrade.grade: joins mid-play, the same without the made-basket window, leaves mid-play);
    warranted jumps; with the director on, the jumps by the goal that drove them (a big play, b freshness, c taste).
Judged where the pictures truly were (`park_replay.Judge`). Replayed with fake sticks: NOT measured on the TV.

Why: Director v1 is the baseline a later AI director must beat, so its gain and its Rule #1 counts are measured the same
way, flag off against flag on, on the nights every PARK change is judged on.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT.as_posix(), (ROOT / "scripts").as_posix()):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import park_replay as pr  # noqa: E402

NBA_NIGHTS = ("20261023", "20261028", "20261021")
PINNED = dict(nfl=0, prime=1, nfl_lag=44.0, deep=True, pin=True)     # tests/test_park_nfl_lag.py's pinned Prime football case


def cases(nights: list[str], games: int, football: bool) -> list[tuple[str, dict]]:
    out = [(f"NBA {n} ({games} games)", dict(night_id=n, games=games)) for n in nights]
    if football:
        out.append(("NFL + Prime pinned (20261023, 1 NBA + 1 NFL)", dict(night_id="20261023", games=1, **PINNED)))
    return out


def row(show) -> dict:
    """One run as one row of the scorecard."""
    res, sc = pr.result(show), pr.seen_card(show)
    return {"jumps_per_min": res["jumps_per_min"], "jumps": res["jumps"], "big_seen_live": sc["big_seen_live"],
            "big_total": sc["big_total"], "worst_unseen_min": round(sc["worst_unseen_s"] / 60, 1),
            "mean_unseen_min": round(sc["mean_unseen_s"] / 60, 2),
            "worst_unseen_live_min": round(sc["worst_unseen_live_s"] / 60, 1), "grader_joins_mid": res["grader_entries_mid_play"],
            "grader_joins_mid_strict": res["grader_entries_mid_play_strict"], "grader_leaves_mid": res["grader_leaves_mid_play"],
            "warranted": res["warranted"], "warrant_n": res["warrant_n"], "drivers": sc.get("drivers"),
            "value_per_min": res.get("value_per_min"), "live_share": res.get("live_share")}


def table(rows: list[dict]) -> str:
    head = ("| case | director | jumps/min | big plays seen live | worst unseen (min) | of it live play (min) | mean unseen (min) | grader joins mid / strict / "
            "leaves mid | warranted | drivers a/b/c |\n|---|---|---|---|---|---|---|---|---|---|")
    lines = [head]
    for r in rows:
        d = r["drivers"]
        lines.append(f"| {r['case']} | {'on' if r['director'] else 'off'} | {r['jumps_per_min']} | {r['big_seen_live']}/{r['big_total']} | "
                     f"{r['worst_unseen_min']} | {r['worst_unseen_live_min']} | {r['mean_unseen_min']} | {r['grader_joins_mid']} / {r['grader_joins_mid_strict']} / "
                     f"{r['grader_leaves_mid']} | {r['warranted']}/{r['warrant_n']} | {'/'.join(str(d[k]) for k in 'abc') if d else '-'} |")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Director v1 off against on, on recorded dev nights (fake sticks).")
    ap.add_argument("--night", action="append", default=[], help="a dev night id (repeat); default the 3 recorded NBA nights")
    ap.add_argument("--games", type=int, default=4)
    ap.add_argument("--no-football", action="store_true", help="leave out the NFL + Prime pinned case")
    ap.add_argument("--minutes", type=float, default=0.0)
    ap.add_argument("--out", default="", help="also write the rows as JSON here")
    a = ap.parse_args()
    rows = []
    for name, kw in cases(a.night or list(NBA_NIGHTS), a.games, not a.no_football):
        for on in (False, True):
            show = pr.replay(minutes=a.minutes, say=lambda line: None, seen=on, director=on, **kw)
            rows.append({"case": name, "director": on, **row(show)})
            print(f"{name}, director {'on' if on else 'off'}: {rows[-1]}", flush=True)
    print("\nREPLAYED with FAKE sticks on recorded dev nights (not measured on the TV)\n" + table(rows))
    if a.out:
        Path(a.out).write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
