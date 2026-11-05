"""[burst labels] EXP-021 grade: MediaSession bursts as dead-ball labels, per stick, over live nights.

Serves: EXP-021. Owner: lane 3.

    python scripts/burst_labels.py --night <probe.jsonl> <plays.jsonl> <espn game id> <screen delay s> [--night ...]

Each --night is one game on one night: the app's sync_probe.jsonl (or the lab's adwatch.jsonl), the night's
recorded plays.jsonl, the game, and that stream's screen delay. Prints JSON (sticks numbered, no addresses).
The bar and how a label is judged: redzone/lab/burst_labels.py.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import burst_align as ba
from redzone.lab import burst_labels as bl


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--night", nargs=4, action="append", required=True,
                    metavar=("PROBE", "PLAYS", "GAME", "DELAY"))
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)
    nights = []
    for probe, plays, game, delay in a.night:
        ons = ba.onsets(ba.probe_lines(Path(probe), None))
        dead = [w for w, _, _ in ba.dead_balls(Path(plays), game)]
        nights.append(bl.grade_night(ons, dead, float(delay), seed=a.seed))
    print(json.dumps(bl.grade(nights, seed=a.seed), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
