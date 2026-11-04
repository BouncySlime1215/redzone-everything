#!/usr/bin/env python3
"""Data ledger: fold a factory manifest into docs/data-ledger.json and print the cycle plan's table.

Serves: the cycle plans' data ledger (cycle 06 standing section) and the phone's "Data + NFM" sheet.
Owner: lane 1 writes (after each factory run), lane 5 reads. Light: reads two small JSON files.

    python scripts/data_ledger.py                                   # print the table from the ledger
    python scripts/data_ledger.py --manifest out/manifest.json      # fold a factory manifest in, write, print
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from redzone.lab import data_ledger as DL  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ledger", type=Path, default=DL.LEDGER)
    ap.add_argument("--manifest", type=Path, help="a factory manifest.json to fold in")
    a = ap.parse_args(argv)
    led = DL.read(a.ledger)
    if a.manifest:
        try:
            man = json.loads(a.manifest.read_text())
        except (OSError, ValueError) as e:
            print(f"can't read {a.manifest}: {e}", file=sys.stderr)
            return 2
        led = DL.fold_manifest(led, man, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"))
        a.ledger.write_text(json.dumps(led, indent=1) + "\n")
    print(DL.table(DL.view(led)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
