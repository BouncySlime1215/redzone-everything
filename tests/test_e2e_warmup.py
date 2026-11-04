"""[warmup.pregame] A night-lab evening that starts 20 min before the first game: with the warm-up on,
the night's first switch is a flip onto a hidden stick that was tuned before the start (path
"preloaded", about the pre-tuned press-to-TV), not a cold tune; every check of the night still holds."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _night(tmp, on: bool) -> tuple[dict, list[dict]]:
    from redzone.lab.night import run as night_run
    if not all((night_run.FIX / f"{g.fixture}.json").exists() for g in night_run.GAMES):
        pytest.skip("the night's ESPN fixtures are not in tests/fixtures")
    out, work = tmp / "night.json", tmp / "w"
    env = dict(os.environ, PYTHONPATH=str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    env.pop("RZ_NIGHT_TRACE", None)
    proc = subprocess.run([sys.executable, "-m", "redzone.lab.night", "--tv", "smartthings", "--hours", "1.3",
                           "--start", "15:40", "--no-default-faults", "--set", f"warmup.pregame={str(on).lower()}",
                           "--json", str(out), "--work", str(work)],
                          cwd=ROOT, env=env, capture_output=True, text=True, timeout=900)
    assert out.exists(), f"no report (exit {proc.returncode}):\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
    ev = [json.loads(x) for x in (work / "data" / "traces.jsonl").read_text().splitlines()]
    return json.loads(out.read_text()), ev


def _first_switch(ev):
    cmd = next(e for e in ev if e["stage"] == "command")
    pic = next(e for e in ev if e["switch_id"] == cmd["switch_id"] and e["stage"] == "picture")
    return cmd["detail"]["path"], pic["detail"]["to_picture_s"]


def test_the_first_switch_of_the_night_is_pre_tuned_with_the_warm_up(tmp_path):
    (tmp_path / "off").mkdir()
    (tmp_path / "on").mkdir()
    off, ev_off = _night(tmp_path / "off", False)
    on, ev_on = _night(tmp_path / "on", True)
    assert _first_switch(ev_off)[0] == "tuned"                   # today: the first switch is a cold tune
    path, took = _first_switch(ev_on)
    assert path == "preloaded" and took <= 3.0, (path, took)
    bad = [c for c in on["checks"] if not c["ok"]]
    assert not bad, json.dumps(bad, indent=1, default=str)[:3000]
