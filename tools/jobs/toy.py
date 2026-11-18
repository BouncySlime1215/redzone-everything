"""Deterministic toy entry command for the job runner's smoke and resume tests (runs in milliseconds per unit).

Why: WORKPLAN v2 F-03 acceptance "kill-and-resume test equals uninterrupted run": the proof needs an entry that
follows the worker's unit protocol (tools/jobs/worker.py) and whose outputs depend on every step, on the carried
state and on the per-unit seeded RNG, so any resume mistake changes the final hash.

    python -m tools.jobs.toy          # only under the worker: reads RZ_JOB_STEP_START/END, RZ_JOB_STATE, RZ_JOB_NEXT

RZ_TOY_SLEEP_S (seconds per step) slows it down so a test can kill it mid-run.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import sys
import time


def main() -> int:
    start, end = int(os.environ["RZ_JOB_STEP_START"]), int(os.environ["RZ_JOB_STEP_END"])
    state_dir, nxt = os.environ.get("RZ_JOB_STATE", ""), os.environ["RZ_JOB_NEXT"]
    acc, step = "0" * 64, 0
    if state_dir:
        with open(os.path.join(state_dir, "state.json"), encoding="utf-8") as f:
            st = json.load(f)
        acc, step = st["acc"], st["step"]
    elif start:
        print("toy: no state for a unit that does not start at 0", file=sys.stderr)
        return 3
    if step != (start if state_dir else 0):
        print(f"toy: state is at step {step}, unit starts at {start}", file=sys.stderr)
        return 3
    pause = float(os.environ.get("RZ_TOY_SLEEP_S", "0"))
    for s in range(start, end):
        acc = hashlib.sha256(f"{acc}:{s}:{random.random()!r}".encode()).hexdigest()
        if pause:
            time.sleep(pause)
    with open(os.path.join(nxt, "state.json"), "w", encoding="utf-8") as f:
        json.dump({"acc": acc, "step": end}, f, sort_keys=True)
    with open(os.path.join(nxt, "model.bin"), "wb") as f:
        f.write(bytes.fromhex(acc) * 4)
    with open(os.path.join(nxt, "metrics.json"), "w", encoding="utf-8") as f:
        json.dump({"acc_prefix": acc[:16], "step": end}, f, sort_keys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
