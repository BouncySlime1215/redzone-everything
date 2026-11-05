"""[EXP-301] Grade the delay-and-react arms (lane 2's EXP-209 rows) per league and condition.

    python scripts/reactgrade.py ROWS.jsonl [...] --split fit [--window S] [--md out.md] [--json out.json]

The rows come from redzone.lab.reactgrade.night_row, one per (arm, night, league, seed). `--split test`
is the locked set: scored once, at the end, for the release decision only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path



def main(argv=None) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from redzone.lab import reactgrade as R
    ap = argparse.ArgumentParser()
    ap.add_argument("rows", nargs="+", type=Path)
    ap.add_argument("--split", default="fit")
    ap.add_argument("--window", type=float, default=None, help="seconds; default: each league's ratchet window")
    ap.add_argument("--md", type=Path)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args(argv)
    rows = [x for p in a.rows for x in R.load(p)]
    rep = R.report(rows, a.split, a.window)
    md = R.markdown(rep)
    print(md)
    if a.md:
        a.md.write_text(md + "\n")
    if a.json:
        a.json.write_text(json.dumps(rep, indent=1, default=str) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
