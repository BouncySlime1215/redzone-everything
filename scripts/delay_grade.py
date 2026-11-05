"""[EXP-803] Grade delay-precision arms against the base arm (redzone/lab/delaygrade.py).

    python scripts/delay_grade.py RUNS_DIR [--base base] [--min-nights 23] [--md out.md] [--json out.json]

RUNS_DIR holds eval_sports.py outputs named <harness>-<seed>-<arm>.json (e.g. resync-1-tick5.json).
--min-nights is for reading a non-gating split only; the verdict's floor is EXP-803's (23 NBA dev nights).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv=None) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from redzone.lab import delaygrade as G
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", type=Path)
    ap.add_argument("--base", default="base")
    ap.add_argument("--min-nights", type=int, default=G.MIN_NIGHTS_803)
    ap.add_argument("--md", type=Path)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args(argv)
    rep = G.grade(G.load_dir(a.runs), a.base, a.min_nights)
    md = G.markdown(rep)
    print(md)
    if a.md:
        a.md.write_text(md + "\n")
    if a.json:
        a.json.write_text(json.dumps(rep, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
