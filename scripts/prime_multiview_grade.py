"""Grade a live Prime NBA multiview night against the turn-on gate (redzone/tuners/prime_multiview.py).

    .venv/bin/python scripts/prime_multiview_grade.py                 # tonight (since 4 PM local)
    .venv/bin/python scripts/prime_multiview_grade.py --since-h 8 --json out.json

Reads the switches the app logged with path "prime_tile" (data/redzone.db) and the supervisor's
graded cuts (data/supervisor_cuts.jsonl, written ~90 s after each cut). Gate: >= 95% of tile
switches confirmed on the right tile in under 2 s (>= 20 switches) and 0 mid-play entries on them.
A switch whose cut isn't graded yet counts as unknown, and the gate refuses to pass on unknowns.
Why: Grades a live Prime NBA multiview night against the turn-on gate (docstring, Prime multiview commit).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone.tuners import prime_multiview as pmv  # noqa: E402

TILE = "prime_tile"


def tile_switches(db: Path, since: float) -> list[dict]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT ts, target, ok, dry_run, detail FROM switches WHERE ts >= ?", (since,)).fetchall()
    finally:
        con.close()
    out = []
    for ts, target, ok, dry, detail in rows:
        try:
            d = json.loads(detail or "{}")
        except ValueError:
            continue
        if d.get("path") != TILE or dry:
            continue
        tr = d.get("trace") or {}
        out.append({"ts": ts, "target": target, "ok": bool(ok), "took": float(d.get("took") or 1e9),
                    "id": tr.get("id")})
    return out


def graded(cuts: Path, since: float) -> dict[str, dict]:
    out = {}
    if not cuts.exists():
        return out
    for line in cuts.read_text().splitlines():
        try:
            g = json.loads(line)
        except ValueError:
            continue
        if g.get("path") == TILE and float(g.get("ts") or 0) >= since and g.get("id"):
            out[g["id"]] = g
    return out


def grade(switches: list[dict], cuts: dict[str, dict]) -> dict:
    mid = sum(1 for g in cuts.values() if g.get("enter") == "play")
    ungraded = sum(1 for s in switches if s.get("id") not in cuts)
    g = pmv.gate(switches, mid_play_entries=mid if not ungraded else None)
    g.update(ungraded=ungraded, left_mid_play=sum(1 for c in cuts.values() if c.get("leave") == "play"))
    if g["left_mid_play"]:
        g["pass"] = False   # rule #1 holds for tile switches too
    return g


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--since-h", type=float, default=None, help="hours back (default: since 4 PM local today)")
    ap.add_argument("--json", help="also write the result here")
    a = ap.parse_args()
    if a.since_h is not None:
        since = time.time() - a.since_h * 3600
    else:
        lt = time.localtime()
        since = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 16, 0, 0, 0, 0, -1))
        if since > time.time():
            since -= 86400
    data = Path(a.data)
    res = grade(tile_switches(data / "redzone.db", since), graded(data / "supervisor_cuts.jsonl", since))
    print(json.dumps(res, indent=1))
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=1))
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
