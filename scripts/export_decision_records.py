#!/usr/bin/env python3
"""Export one night's decision records as JSONL for the Teacher's nightly review (schema in
redzone/lab/decision_records.py and docs/decision-records.md).

    python scripts/export_decision_records.py --night 2026-11-04                       # data/decision_records/
    python scripts/export_decision_records.py --db <mac repo>/data/redzone.db --data <mac repo>/data \
        --night 2026-11-04 --out /mnt/project-files/decision-records

Read-only on the database and logs, never uploads, and never reads the feedback table (Nick's taps:
the Noggin is self-taught). The output holds game ids and reasons only, but keep it out of the public
repo like every other night log. Light: one night is a few thousand rows, well under a second on the Mac.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone.lab import decision_records  # noqa: E402
from redzone.supervisor import night_of, night_window  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "redzone.db")
    ap.add_argument("--data", type=Path, default=ROOT / "data", help="folder with supervisor_*.jsonl")
    ap.add_argument("--night", help="YYYY-MM-DD (default: last night)")
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "decision_records")
    a = ap.parse_args(argv)
    try:
        night = date.fromisoformat(a.night) if a.night else night_of(time.time() - 86400)
    except ValueError:
        print(f"bad --night {a.night!r}: want YYYY-MM-DD", file=sys.stderr)
        return 2
    if not a.db.exists():
        print(f"no database at {a.db}", file=sys.stderr)
        return 2
    t0, t1 = night_window(night)
    recs = decision_records.from_db(a.db, night.isoformat(), t0, t1, a.data)
    path = decision_records.write(recs, a.out / f"{night.isoformat()}.jsonl")
    c = recs[0]["counts"]
    print(f"{path}: {c['decisions']} decisions, {c['switches']} switches, {c['moments']} big plays "
          f"({c['big_seen']} seen), {c['entries_mid_play']} mid-play entries of {c['landings_graded']} graded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
