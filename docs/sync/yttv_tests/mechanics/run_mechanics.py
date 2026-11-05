#!/usr/bin/env python3
"""Run the mechanics tests in sequence. Default: every test with --dry-run (safe anywhere).
Live: python run_mechanics.py --live 05,12,08   (read-only ones first; action tests refuse unless the app
reports a hidden stick). Extra args after -- are passed to every test, e.g. -- --channel tnt."""
import argparse
import glob
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("--live", default=None, help="comma list of test numbers to run LIVE (e.g. 05,12)")
ap.add_argument("rest", nargs=argparse.REMAINDER)
a = ap.parse_args()
rest = [x for x in a.rest if x != "--"]
files = sorted(glob.glob(os.path.join(HERE, "mechanics_[0-9][0-9]_*.py")))
if a.live:
    want = {n.zfill(2) for n in a.live.split(",")}
    files = [f for f in files if os.path.basename(f)[10:12] in want]
bad = 0
for f in files:
    cmd = [sys.executable, f] + ([] if a.live else ["--dry-run"]) + rest
    r = subprocess.run(cmd, capture_output=True, text=True)
    verdict = [ln for ln in r.stdout.splitlines() if '"kind": "verdict"' in ln]
    print(f"{os.path.basename(f):42s} exit={r.returncode}  {verdict[-1][-200:] if verdict else r.stderr[-200:]}")
    bad += r.returncode != 0
sys.exit(1 if bad else 0)
