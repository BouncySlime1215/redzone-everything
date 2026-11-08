#!/usr/bin/env python3
"""[EXP-005] Score a candidate decider off-policy on logged nights (redzone/lab/offpolicy.py).

Serves: North Star "% of hindsight-oracle value achieved" (off-policy). Owner: lane 4. Grading only.

    python scripts/offpolicy_score.py data/decisions.jsonl --reward value --policy same

Each line of the log is a #186 decision record with "options" (decider.log_options on), a "night" and a
reward field written by the grader. --policy same replays the live propensities (a sanity check: it reads
the logged mean); the morning job passes the shadow candidate's probabilities instead (EXP-004).
Why: EXP-005: scores a candidate decider off-policy on logged nights (docstring).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from redzone.lab import offpolicy as O  # noqa: E402


def load(path: Path, reward: str) -> list[O.Logged]:
    rows = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if reward not in rec:
            continue
        row = O.from_decision_log(rec, rec[reward], str(rec.get("night", "")))
        if row is not None:
            rows.append(row)
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("log", type=Path)
    ap.add_argument("--reward", default="value", help="the record field holding the decision's reward")
    ap.add_argument("--policy", choices=["same"], default="same")
    a = ap.parse_args(argv)
    rows = load(a.log, a.reward)
    if not rows:
        print("no decisions with an option set and a reward", file=sys.stderr)
        return 1
    e = O.estimate(rows, lambda r: [o.get("p", 0.0) for o in r.options], lambda r, i: 0.0)
    print(json.dumps({"n": e.n, "nights": len(e.nights), "dr": e.dr, "lo": e.lo, "hi": e.hi, "ips": e.ips,
                      "dm": e.dm, "clipped": e.clipped}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
