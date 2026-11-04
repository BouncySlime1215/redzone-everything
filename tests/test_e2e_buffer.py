"""[buffer.watchdog] playbook STK-06 on a night-lab evening with the time-shift buffer on: the hidden
stick's hold falls 6 s further behind (buffer_lost mode drift), the watchdog sees it off its own
clock and rebuilds it within a few minutes; every check of the night still holds (0 mid-play)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.playbook("STK-06")
def test_a_drifting_hold_is_rebuilt_and_the_night_stays_clean(tmp_path):
    from redzone.lab.night import run as night_run
    if not all((night_run.FIX / f"{g.fixture}.json").exists() for g in night_run.GAMES):
        pytest.skip("the night's ESPN fixtures are not in tests/fixtures")
    out, work = tmp_path / "night.json", tmp_path / "w"
    env = dict(os.environ, PYTHONPATH=str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    env.pop("RZ_NIGHT_TRACE", None)
    proc = subprocess.run([sys.executable, "-m", "redzone.lab.night", "--tv", "smartthings", "--hours", "2",
                           "--seed", "1", "--no-default-faults", "--set", "timing.behind_live_s.enabled=true",
                           "--set", "buffer.watchdog=true",
                           "--fault", json.dumps({"name": "buffer_lost", "at": 4700, "dur": 0, "mode": "drift",
                                                  "seconds": 6}),
                           "--json", str(out), "--work", str(work)],
                          cwd=ROOT, env=env, capture_output=True, text=True, timeout=900)
    assert out.exists(), f"no report (exit {proc.returncode}):\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
    night = json.loads(out.read_text())
    hit = [f for f in night["faults"] if f["name"] == "buffer_lost" and f.get("hit")]
    assert hit, night["faults"]
    rows = [json.loads(x) for x in (work / "data" / "buffer.jsonl").read_text().splitlines()]
    fault_t = next(r["ts"] for r in rows if r["kind"] == "drift" and r["target"] == hit[0]["target"]
                   and abs(r["err_s"] - 6.0) < 1.0)
    rebuilt = [r for r in rows if r["kind"] == "recovered" and r["ts"] > fault_t]
    assert rebuilt and abs(rebuilt[0]["behind_s"] - 90.0) <= 2.0, rows
    bad = [c for c in night["checks"] if not c["ok"]]
    assert not bad, json.dumps(bad, indent=1, default=str)[:3000]
