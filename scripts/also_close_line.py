"""[EXP-039] Print the "Also close" line: manual-switch share on nights with phone.also_close on vs off.

    python scripts/also_close_line.py --db <mac repo>/data/redzone.db --on 2026-11-08 ... --off 2026-11-09 ...
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from redzone.lab.also_close import line, manual_share  # noqa: E402
from redzone.lab.orient import night_switches  # noqa: E402
from redzone.web.phone_v2 import night_window  # noqa: E402


def shares(db: Path, nights: list[str]) -> list[float]:
    out = []
    for n in nights:
        v = manual_share(night_switches(db, *night_window(date.fromisoformat(n))))
        if v is not None:
            out.append(v)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "redzone.db")
    ap.add_argument("--on", nargs="*", default=[])
    ap.add_argument("--off", nargs="*", default=[])
    a = ap.parse_args(argv)
    if not a.db.exists():
        print(json.dumps({"error": f"no database at {a.db}"}))
        return 1
    print(json.dumps(line(shares(a.db, a.on), shares(a.db, a.off))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
