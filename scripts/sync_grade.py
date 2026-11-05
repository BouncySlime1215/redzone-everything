"""[EXP-800 / EXP-801] Grade one night's sticks on their own behind-live meter (redzone/lab/syncgrade.py).

    python scripts/sync_grade.py ROWS.jsonl [...] [--anchors anchors.json] [--json out.json]

ROWS: sync_probe truth rows in syncgrade's schema (t, stick, app, channel, uptime_ms, ms, tune_t,
pred_behind_s). anchors.json: {channel: measured absolute latency s}; channels without one read PENDING.
Prints JSON with sticks numbered, never addresses.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv=None) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from redzone.lab import syncgrade as S
    ap = argparse.ArgumentParser()
    ap.add_argument("rows", nargs="+", type=Path)
    ap.add_argument("--anchors", type=Path)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args(argv)
    rows = [json.loads(x) for p in a.rows for x in p.read_text().splitlines() if x.strip()]
    anchors = json.loads(a.anchors.read_text()) if a.anchors else None
    out = {"exp800": S.exp800(rows, anchors), "exp801": S.exp801(rows)}
    text = json.dumps(out, indent=1)
    print(text)
    if a.json:
        a.json.write_text(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
