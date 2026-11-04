#!/usr/bin/env python3
"""Write the one-brain break table (redzone/scoring/break_table.v1.json) from the archive's non-locked nights.

    python scripts/measure_break_table.py [--out PATH]

Owner: lane 1. Read by redzone/contracts/breaks.load_table behind ``breaks.one_model`` (redzone/lab/breaktable.py).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from redzone.contracts.breaks import BREAK_TABLE_PATH  # noqa: E402
from redzone.lab import breaktable  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(BREAK_TABLE_PATH))
    a = ap.parse_args(argv)
    t = breaktable.measure()
    Path(a.out).write_text(json.dumps(t, indent=1) + "\n")
    print(json.dumps({"provenance": t["provenance"]["leagues"], "rules": t["rules"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
