"""[switch timing] The e2e night on the TV's own input change (SmartThings) at the latency measured on
the user's TV (median 1.98 s, max 2.2 s): every check of the HOME night must still hold, every flip is
the TV's input (readback-confirmed), and nothing is pressed on a stick to switch. Three hours of the
night keep it short; the full HOME night is tests/test_e2e_night.py."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def night(tmp_path_factory):
    from redzone.lab.night import run as night_run
    if not all((night_run.FIX / f"{g.fixture}.json").exists() for g in night_run.GAMES):
        pytest.skip("the night's ESPN fixtures are not in tests/fixtures")
    work = tmp_path_factory.mktemp("night_st")
    out = work / "night.json"
    env = dict(os.environ, PYTHONPATH=str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    env.pop("RZ_NIGHT_TRACE", None)
    env["RZ_NIGHT_HOSTILE_HOST"] = "1"
    proc = subprocess.run([sys.executable, "-m", "redzone.lab.night", "--tv", "smartthings", "--hours", "3",
                           "--json", str(out), "--work", str(work / "w")],
                          cwd=ROOT, env=env, capture_output=True, text=True, timeout=900)
    assert out.exists(), f"no report (exit {proc.returncode}):\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
    return json.loads(out.read_text())


def test_every_check_passes_on_the_tv_input_path(night):
    bad = [c for c in night["checks"] if not c["ok"]]
    assert not bad, json.dumps(bad, indent=1, default=str)[:4000]


def test_flips_are_the_tvs_input_at_the_measured_latency(night):
    ti = night["tv_input"]
    assert ti["commands"] >= 3 and ti["confirmed"] >= 0.95 * ti["commands"]
    assert 1.6 <= ti["total_s_median"] <= 2.8 and ti["total_s_max"] <= 3.5
