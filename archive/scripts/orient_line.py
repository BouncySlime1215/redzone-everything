"""[EXP-038] Print the orientation-beat line: override rates on nights with phone.orient on vs off.

    python scripts/orient_line.py --db <mac repo>/data/redzone.db --on 2026-11-08 ... --off 2026-11-09 ...

Read-only on the database (switches table only). Nights are ET nights, 6 AM to 6 AM (phone_v2.night_window).
Why: EXP-038: prints the orientation-beat override line, phone.orient on vs off (docstring).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from redzone.lab.orient import compare, night_switches, overrides  # noqa: E402
from redzone.web.phone_v2 import night_window  # noqa: E402


def rates(db: Path, nights: list[str]) -> list[float]:
    out = []
    for n in nights:
        r = overrides(night_switches(db, *night_window(date.fromisoformat(n))))["rate"]
        if r is not None:
            out.append(r)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "redzone.db")
    ap.add_argument("--on", nargs="*", default=[], help="nights phone.orient was on (YYYY-MM-DD)")
    ap.add_argument("--off", nargs="*", default=[], help="nights it was off")
    a = ap.parse_args(argv)
    if not a.db.exists():
        print(json.dumps({"error": f"no database at {a.db}"}))
        return 1
    print(json.dumps(compare(rates(a.db, a.on), rates(a.db, a.off))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
