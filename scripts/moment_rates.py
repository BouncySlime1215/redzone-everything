"""Measured must-see rates per league from the archive, compared with the planner's table (read only).

Why: WP-07 exit check. The moment detectors (redzone/moments) must reproduce the planner's per-league rates
(docs/agents/planner/2026-11-09-product-per-league.md section 3) within 5%, on DEV nights only (train+valid of
redzone/lab/splits.py; the locked test nights are never read). A league outside 5% is reported, not tuned.

    python scripts/moment_rates.py --archive /path/to/data/archive [--split fit] [--leagues nfl,nba] [--json out.json]

Rates are per live game per 10 wall-clock minutes, over each game's first-to-last timed play, same definition as
the planner (a game needs 30+ timed plays spanning 1.2-7 h).
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone.lab import splits  # noqa: E402
from redzone.moments import detect, game_span, tiers  # noqa: E402

LEAGUES = ("nfl", "cfb", "nba", "cbb", "nhl", "mlb")
# Planner table: per live game, per 10 wall-clock minutes: (Tier 1, Tier 2, Tier 3).
PLANNER = {
    "nfl": (0.39, 0.77, 2.29), "cfb": (0.44, 0.68, 1.86), "nba": (0.17, 0.78, 4.30),
    "cbb": (0.03, 0.34, 2.19), "nhl": (0.42, 0.44, 3.27), "mlb": (0.35, 0.56, 0.54),
}
TOL = 0.05


def _header_night(f: Path) -> str:
    d = json.loads(f.read_text())
    t = dt.datetime.fromisoformat(d["header"]["competitions"][0]["date"].replace("Z", "+00:00"))
    return (t - dt.timedelta(hours=5)).strftime("%Y%m%d")


def measure(league: str, archive: Path, which: str) -> dict:
    ldir = archive / league
    by_night = splits.archive_nights(ldir)
    if by_night:
        nights = splits.nights_for(ldir, which)
        night_of = {g: n for n, gs in by_night.items() for g in gs}
    else:                                 # cfb/cbb keep no scoreboards: night = US Eastern date of the game start
        night_of = {f.stem[len("summary_"):]: _header_night(f) for f in ldir.glob("summary_*.json")}
        sp = splits.league_split(league, set(night_of.values()))
        nights = set({"train": sp.train, "valid": sp.valid, "fit": sp.train + sp.valid}[which])
    hours, games, skipped = 0.0, 0, 0
    tier_n = collections.Counter()
    kinds = collections.Counter()
    used_nights = set()
    for f in sorted(ldir.glob("summary_*.json")):
        gid = f.stem[len("summary_"):]
        n = night_of.get(gid)
        if nights is not None and n not in nights:
            continue                      # not a dev night (or no scoreboard): never read
        d = json.loads(f.read_text())
        res = detect(league, d, gid)
        span = game_span(res.times)
        if span is None:
            skipped += 1
            continue
        games += 1
        used_nights.add(n)
        hours += (span[1] - span[0]) / 3600
        for m in res.moments:
            if span[0] <= m.t <= span[1]:
                tier_n[m.tier] += 1
                kinds[m.kind] += 1
    per10 = [round(tier_n[t] / hours / 6, 3) if hours else None for t in (1, 2, 3)]
    return {"league": league, "split": which, "games": games, "nights": len(used_nights), "skipped": skipped,
            "game_hours": round(hours, 1), "per10": per10,
            "kinds_per_game": {k: round(v / games, 2) for k, v in kinds.most_common()} if games else {}}


def compare(row: dict) -> list[dict]:
    out = []
    for i, name in enumerate(("T1", "T2", "T3")):
        want, got = PLANNER[row["league"]][i], row["per10"][i]
        err = None if got is None else (got - want) / want
        out.append({"tier": name, "planner": want, "measured": got, "err_pct": None if err is None else round(100 * err, 1),
                    "within_5pct": err is not None and abs(err) <= TOL})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--archive", default=str(ROOT / "data" / "archive"))
    ap.add_argument("--split", default="fit", choices=("fit", "train", "valid"),
                    help="dev splits only; the locked test split is refused")
    ap.add_argument("--leagues", default=",".join(LEAGUES))
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    report = []
    print(f"{'league':<5}{'games':>6}{'nights':>7}{'hours':>8}  tier  planner  measured  err%  ok")
    for lg in a.leagues.split(","):
        row = measure(lg, Path(a.archive), a.split)
        row["compare"] = compare(row)
        report.append(row)
        for c in row["compare"]:
            print(f"{lg:<5}{row['games']:>6}{row['nights']:>7}{row['game_hours']:>8}  {c['tier']:<4}"
                  f"{c['planner']:>8}{c['measured']!s:>10}{c['err_pct']!s:>7}  {'yes' if c['within_5pct'] else 'NO'}")
    if a.json:
        Path(a.json).write_text(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
