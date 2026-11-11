"""[timing.dead_v2] The dead-time-left table: sure seconds left in a stoppage, by break class and by the time already elapsed.

    python scripts/fetch_eval_data.py                 # once
    python scripts/measure_dead_left.py               # writes redzone/scoring/broadcast/dead_left_table.json (nfl nba nhl)
    python scripts/measure_dead_left.py nfl           # one league; the other leagues already in the file are kept

A row is a feed stoppage (espn.stoppage_for) to the real restart (cutgrade.resume_after): the definition of
scripts/measure_break_lengths.py, same length cap. Its class is dead_left.stoppage_class (the feed's own words on the play that
began it). For every league x kind x class x elapsed landmark t (dead_left.LANDMARKS) the table holds, among the stoppages that
outlasted t: how many there were (n), their largest remaining time, and for each risk level alpha (dead_left.ALPHAS) the
one-sided split-conformal lower bound on the remaining time: the floor(alpha * (n + 1))-th smallest. null: too few survivors
for that level. The class named after the kind is the pooled one (every stoppage of the kind).
Only TRAIN nights feed the table (redzone.lab.splits); a game belongs to the first night that lists it, so it is counted once;
valid nights grade it (research ledger L1); the locked nights are never read. Seconds are rounded down to 0.1 (never up).
License: derived numbers from ESPN game summaries (personal-use under data plan B, not for commercial release); no raw rows
and no game ids are published.
Why: the pooled quantile less the elapsed time allowed a clip in 0 of 891 NFL timeouts at the 99% level; the class and the
survivors' remaining time allowed 598 (research ledger L1, Nov 10, simulated on recorded nights).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import measure_break_lengths as mbl  # noqa: E402
from redzone.lab import cutgrade, splits  # noqa: E402
from redzone.lab.backtest import Replay  # noqa: E402
from redzone.models import League, Stoppage  # noqa: E402
from redzone.scoring import dead_left  # noqa: E402
from redzone.sources import espn  # noqa: E402


def stoppages(league: League, which: str = "train") -> tuple[list[tuple[str, str, float]], int]:
    """((kind, class, length in seconds) of every stoppage on the TRAIN nights (or `which`: "valid"), the number of nights).
    The locked nights cannot be asked for."""
    if which not in ("train", "valid"):
        raise ValueError(f"only dev nights (train, valid), not {which}")
    arch = ROOT / "data" / "archive" / league.value
    nights = splits.archive_nights(arch)
    chosen = getattr(splits.league_split(league.value, nights), which)
    return rows_for(league, arch, {n: nights[n] for n in chosen}), len(chosen)


def rows_for(league: League, arch: Path, nights: dict[str, list[str]]) -> list[tuple[str, str, float]]:
    """(kind, class, length in seconds) of every stoppage in these nights' games (night -> game ids)."""
    out = []
    for night in sorted(nights):
        for gid in nights[night]:
            f = arch / f"summary_{gid}.json"
            r = Replay.load(league, f) if f.exists() else None
            if not r:
                continue
            prev = Stoppage.NONE
            for i, p in enumerate(r.plays):
                k = espn.stoppage_for(league, p)
                if k not in (Stoppage.NONE, Stoppage.UNKNOWN) and k != prev:
                    res = cutgrade.resume_after(league, r.wall, r.plays, i)
                    if res != float("inf") and r.wall[i] is not None and 0 < res - r.wall[i] <= mbl.MAX_LEN_S:
                        cls = dead_left.stoppage_class(league, k, (p.get("type") or {}).get("text") or "", p.get("text") or "")
                        out.append((k.value, cls, float(res - r.wall[i])))
                prev = k
    return out


def _down(x: float) -> float:
    return math.floor(x * 10.0) / 10.0


def cells(rows: list[tuple[str, str, float]]) -> dict:
    """kind -> class -> landmark -> {n, max_s, left_s: {alpha: bound or None}} for one league."""
    by: dict = defaultdict(list)
    for kind, cls, ln in rows:
        by[(kind, kind)].append(ln)              # the pooled class
        if cls != kind:
            by[(kind, cls)].append(ln)
    out: dict = {}
    for (kind, cls), lens in sorted(by.items()):
        for t in dead_left.LANDMARKS:
            left = [x - t for x in lens if x > t]
            if not left:
                continue
            bounds = {f"{a:.2f}": dead_left.conformal_lower(left, a) for a in dead_left.ALPHAS}
            out.setdefault(kind, {}).setdefault(cls, {})[str(t)] = {
                "n": len(left), "max_s": _down(max(left)),
                "left_s": {a: None if b is None else _down(b) for a, b in bounds.items()}}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("leagues", nargs="*", default=["nfl", "nba", "nhl"])
    a = ap.parse_args()
    out = dead_left.TABLE_V2_FILE
    old = json.loads(out.read_text()) if out.exists() else {}
    table, nights = dict(old.get("table") or {}), dict(old.get("train_nights") or {})
    for lg in a.leagues:
        rows, n_nights = stoppages(League(lg))
        table[lg], nights[lg] = cells(rows), n_nights
        print(f"{lg}: {len(rows)} train stoppages on {n_nights} train nights")
        for kind, classes in table[lg].items():
            for cls, marks in classes.items():
                print(f"  {kind:20s} {cls:20s} " + "  ".join(
                    f"{t}s: n {c['n']}, 99% {c['left_s']['0.01']}" for t, c in marks.items() if t in ("0", "30", "60")))
    out.write_text(json.dumps({
        "source": "eval-data archive, train nights only (redzone.lab.splits); each game counted once",
        "license": "derived from ESPN summaries; personal-use (plan B), not for commercial release",
        "definition": "feed stoppage to real restart (cutgrade.resume_after). Per elapsed landmark t, among stoppages that "
                      "outlasted t: n, the largest remaining seconds (max_s) and, per risk level alpha, the one-sided "
                      "split-conformal lower bound on the remaining seconds (left_s: the floor(alpha * (n + 1))-th smallest; "
                      "null = too few survivors). The class named after the kind pools every stoppage of the kind",
        "landmarks": list(dead_left.LANDMARKS), "alphas": [f"{x:.2f}" for x in dead_left.ALPHAS],
        "train_nights": {k: nights[k] for k in sorted(nights)}, "table": {k: table[k] for k in sorted(table)}}, indent=1) + "\n")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
