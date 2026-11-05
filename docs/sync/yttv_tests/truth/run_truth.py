#!/usr/bin/env python3
"""Run every truth_NN test in --dry-run (default) or pass extra args through for a live night, e.g.
  python3 run_truth.py                       # all dry-runs, one PASS/FAIL/N/A line each
  python3 run_truth.py --live --game 4019xxxxx --minutes 20 --only 01,05,06
Live runs are sequential and read-only (HTTP feeds + read-only adb for 09/10/11)."""
import glob
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
args = sys.argv[1:]
live = "--live" in args
args = [x for x in args if x != "--live"]
only = None
if "--only" in args:
    i = args.index("--only")
    only = set(args[i + 1].split(","))
    del args[i:i + 2]
rc = 0
for f in sorted(glob.glob(os.path.join(HERE, "truth_[0-9][0-9]_*.py"))):
    nn = os.path.basename(f)[6:8]
    if only and nn not in only:
        continue
    cmd = [sys.executable, f] + ([] if live else ["--dry-run"]) + args
    p = subprocess.run(cmd, capture_output=True, text=True)
    line = next((x for x in p.stdout.splitlines() if x.startswith("[truth_") and ("PASS" in x or "FAIL" in x or "N/A" in x)), "")
    print(f"{os.path.basename(f):40s} exit={p.returncode} {line.split(']', 1)[-1].strip()[:90]}")
    if p.returncode:
        print(p.stderr[-800:])
        rc = 1
sys.exit(rc)
