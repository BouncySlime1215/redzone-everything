"""Gym v2 parity gate (task A-01): the practice world with the identity scenario decides byte for byte as the replay.

    python scripts/gym2_parity.py                                   # 3 NBA dev nights, whole nights, 4 games, seed 0
    python scripts/gym2_parity.py --nights 20261023 --minutes 30    # a shorter check

For each night: `park_replay.replay` (the script's own run) and `GymV2` stepped tick by tick (observing the show at
every pause) each produce the decision list (every event, line, jump record, key and command sent, the result row).
It prints both sha256 digests and the counts, and exits 1 on any difference. About 100 s of CPU per whole night per
side on the Mac (Nov 12); run it at background priority. Dev nights only (the replay refuses others).

Why: everything Gym v2 adds (scenarios, mixes, rewards, the mask) is only trustworthy if the world under it is the
show's own, exactly.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if ROOT.as_posix() not in sys.path:
    sys.path.insert(0, ROOT.as_posix())

from redzone.lab.gym2 import env as gym  # noqa: E402

NIGHTS = ("20261021", "20261023", "20261028")     # the 3 NBA dev nights of docs/park.md's fast-steps replay


def check(night: str, games: int, seed: int, minutes: float) -> tuple[bool, str]:
    t0 = time.process_time()
    ref = gym.replay_decisions(night, games, seed=seed, minutes=minutes)
    t1 = time.process_time()
    env = gym.GymV2(night, games, seed=seed, minutes=minutes)
    env.reset()
    steps = 0
    while not env.done:
        env.observe()
        env.step()
        steps += 1
    got = env.decisions()
    t2 = time.process_time()
    a, b = gym.digest(ref), gym.digest(got)
    bad = [k for k in ref if ref[k] != got.get(k)]
    line = (f"{night}: replay {a[:16]} gym {b[:16]} {'IDENTICAL' if a == b else 'DIFFERENT ' + ','.join(bad)}; "
            f"jumps {len(ref['records'])}, events {len(ref['events'])}, lines {len(ref['says'])}, gym steps {steps}; "
            f"cpu {t1 - t0:.0f} s + {t2 - t1:.0f} s")
    return a == b, line


def main() -> int:
    ap = argparse.ArgumentParser(description="Gym v2 identity scenario vs park_replay, decision list byte for byte.")
    ap.add_argument("--nights", nargs="+", default=list(NIGHTS))
    ap.add_argument("--games", type=int, default=4)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--minutes", type=float, default=0.0, help="0 = the whole night")
    a = ap.parse_args()
    ok = True
    for n in a.nights:
        same, line = check(n, a.games, a.seed, a.minutes)
        ok &= same
        print(line, flush=True)
    print("PARITY " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
