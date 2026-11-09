"""[WP-15] The whole adversarial matrix on every fit night, for the exit check. Not collected by pytest.

    nice -n 15 python tests/adversarial/sweep.py out.jsonl     # one JSON line per cell, then the totals

Why: pytest runs the cells that guard a merge; this runs all of them, at most two workers (8 GB Mac).
"""
from __future__ import annotations

import json
import multiprocessing as mp
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / "tests"):
    sys.path.insert(0, str(p))

from tests.adversarial.harness import WorstBrain, score  # noqa: E402
from tests.brains import ATTACKS, AdversarialBrain, RandomBrain  # noqa: E402

KINDS = ("jump", "better", "crunch", "chase", "must_see", "start", "home")
EXEMPT = ("resume", "skip")   # the replay stack's own kinds: reported apart, see test_adversarial_replay.py
LEAGUES = ("nba", "nhl", "nfl")
WORKERS = 2


def matrix() -> list[tuple[str, str, str, bool, str]]:
    out = []
    for lg in LEAGUES:
        out += [(lg, "worst", k, False, "") for k in (*KINDS, *EXEMPT)]
        out += [(lg, f"random:{s}", k, False, "") for s in (1, 2) for k in ("jump", "better", "crunch")]
        out += [(lg, f"adv:{a}", "better", raw, "") for a in ATTACKS for raw in (False, True)]
    return out


def maker(name: str):
    if name == "worst":
        return WorstBrain
    kind, arg = name.split(":")
    return (lambda t: RandomBrain(int(arg))) if kind == "random" else (lambda t: AdversarialBrain(arg))


def cell(args):
    lg, name, kind, raw, _ = args
    r = score(lg, maker(name), kind=kind, raw=raw)
    r.update(brain=name, raw=raw)
    return r


def main(out: Path) -> dict:
    tot = {"cells": 0, "entries_mid_play": 0, "cuts_high": 0, "proposed": 0, "exempt_entries_mid_play": 0}
    with mp.get_context("spawn").Pool(WORKERS) as pool, out.open("w") as f:
        for r in pool.imap_unordered(cell, matrix()):
            f.write(json.dumps(r) + "\n")
            f.flush()
            exempt = r["kind"] in EXEMPT
            tot["cells"] += 1
            tot["proposed"] += r["proposed"]
            tot["exempt_entries_mid_play" if exempt else "entries_mid_play"] += r["entries_mid_play"]
            tot["cuts_high"] += r["cuts_high"]
    return tot


if __name__ == "__main__":
    print(json.dumps(main(Path(sys.argv[1])), indent=1))
