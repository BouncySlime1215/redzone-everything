"""Your-team crunch is never left and always wins over another crunch (test writers, planner Nov 6).

Regression for the WV loop's v3 reads (/mnt/project-files/wv-loop/v3reads/your_team_crunch_misses.md), as shipped on
integration/next 1cd94c1 (eval_config pinned, 90 s buffer, 70 s feed lag):
  20251110 WSH @ DET (401810054)  82/105: a catch-up return left WSH's Q4 for LAL, then an ORL crunch; back at 0:01.
  20251212 CLE @ WSH (401836753)  12/31: the app stayed on a CHI crunch during WSH's own crunch.
Both are eval-data-v3 FIT nights (dev_before 20261001), never locked. The test needs the v3 archive
(RZ_EVAL_LOCK=eval-data-v3.lock.json python scripts/fetch_eval_data.py) and skips without it, so CI (v1) skips.
Grader: sportsgrade's my_crunch / my_crunch_live (frozen), via scripts/eval_sports.py one night at a time.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NIGHTS = [("20251110", "401810054"), ("20251212", "401836753")]


def _night(day: str) -> dict:
    env = {**os.environ, "RZ_EVAL_LOCK": "eval-data-v3.lock.json"}
    out = subprocess.run([sys.executable, "scripts/eval_sports.py", "nba", "--end", day, "--nights", "1",
                          "--behind-live", "90", "--feed-lag", "70"], cwd=ROOT, env=env, capture_output=True,
                         text=True, timeout=900, check=True).stdout
    return next(n for n in json.loads(out)["per_night"] if n["night"] == day)


@pytest.mark.parametrize("day,gid", NIGHTS)
def test_your_team_crunch_is_never_left(day, gid):
    if not (ROOT / "data/archive/nba" / f"summary_{gid}.json").exists() or not (ROOT / "eval-data-v3.lock.json").exists():
        pytest.skip("needs the eval-data-v3 archive (fit night)")
    n = _night(day)
    assert n["my_crunch"] > 0, f"{day}: no your-team crunch graded (wrong archive?)"
    assert n["my_crunch_live"] == n["my_crunch"], (
        f"{day}: your-team crunch live {n['my_crunch_live']}/{n['my_crunch']}: the app left WSH's crunch "
        f"or another crunch beat it")
