#!/usr/bin/env python3
"""The contract checker, the city map's "proof kernel" (Nick, Nov 4 3:40 PM). Runs in CI on every PR.

    python scripts/contract_check.py                    # fail on a violation that is not in the baseline
    python scripts/contract_check.py --update-baseline  # rewrite the baseline (only ever to drop fixed ones)
    python scripts/contract_check.py --json             # every violation as JSON

Rules (redzone/contracts/map.py, analysis in redzone/lab/citymap.py):
  timing-literal       a timing cost (flip / tune / switch time, its deadline) set as a number in code
                       instead of read from measured config (timing.* / calib.*)
  defined-twice        one seconds constant kept in two modules
  boundary             an outside system (adb, SmartThings, ESPN, the NHL api, HDMI-CEC) reached from
                       outside the modules its contract names
  second-scoring-path  a runtime module importing a game scorer outside the one scoring path
                       (Noggin is the single decision entry; scorers feed Watch Value)
  orphan / dead        a module nothing imports / only tests reach (no entry point)

Report mode first (Start Planner, Cycle 05): today's violations are the baseline in
docs/contract-baseline.json, each with its owner's lane, and each lane burns its own down. CI fails
on any violation not in the baseline, and on a baseline entry that would grow. North Star line:
contract_violations (gate 0), read from docs/citymap.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone.lab import citymap as C  # noqa: E402

BASELINE = ROOT / "docs" / "contract-baseline.json"


def load_baseline(p: Path = BASELINE) -> set[str]:
    try:
        return set(json.loads(p.read_text()).get("known", []))
    except (OSError, ValueError):
        return set()


def check(v: list[dict], known: set[str]) -> tuple[list[dict], list[str]]:
    """(new violations, baseline entries that are fixed now)."""
    keys = {x["key"] for x in v}
    return [x for x in v if x["key"] not in known], sorted(known - keys)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--update-baseline", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--baseline", type=Path, default=BASELINE)
    a = ap.parse_args(argv)
    v = C.violations(C.build(ROOT))
    if a.json:
        print(json.dumps(v, indent=1))
        return 0
    known = load_baseline(a.baseline)
    new, fixed = check(v, known)
    if a.update_baseline:
        if new and known:
            print("refusing: --update-baseline only drops fixed violations; these are new:")
            for x in new:
                print(f"  {x['rule']}: {x['where']}: {x['detail']}")
            print("fix them, or add a rule exception in redzone/contracts/map.py with a decisions.md line")
            return 1
        a.baseline.write_text(json.dumps({"about": "Known contract violations (scripts/contract_check.py). Only ever "
                                          "shrinks: fix one, then run --update-baseline.",
                                          "known": sorted(x["key"] for x in v)}, indent=1) + "\n")
        print(f"baseline: {len(v)} known ({len(fixed)} dropped)")
        return 0
    by = Counter(x["rule"] for x in v)
    print(f"contract violations = {len(v)} ({', '.join(f'{k} {n}' for k, n in sorted(by.items())) or 'none'}); "
          f"{len(new)} new, {len(fixed)} fixed since the baseline")
    for x in new:
        print(f"NEW {x['rule']}: {x['where']} ({x['owner']}, lane {x['lane']}): {x['detail']}")
    if fixed:
        print("fixed (run --update-baseline to drop them): " + ", ".join(fixed))
    return 1 if new else 0


if __name__ == "__main__":
    sys.exit(main())
