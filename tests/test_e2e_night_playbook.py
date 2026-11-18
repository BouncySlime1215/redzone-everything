"""The e2e night with the playbook faults (redzone/lab/night/faults.py PLAYBOOK, `--faults
redzone.lab.night.faults:playbook`): a frozen feed, 429s, slow polls, weak Wi-Fi and a buffering stick
at seeded live times, next to the I4 faults. Graded: no mid-play cut while each ran or 60 s after,
and nothing crashed or got stuck. About 3 minutes; runs in CI's e2e step."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def pb_night(tmp_path_factory):
    from redzone.lab.night import run as night_run
    if not all((night_run.FIX / f"{g.fixture}.json").exists() for g in night_run.GAMES):
        pytest.skip("the night's ESPN fixtures are not in tests/fixtures")
    work = tmp_path_factory.mktemp("pbnight")
    out = work / "night.json"
    env = dict(os.environ, PYTHONPATH=str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    env.pop("RZ_NIGHT_TRACE", None)
    proc = subprocess.run([sys.executable, "-m", "redzone.lab.night", "--seed", "1", "--json", str(out),
                           "--work", str(work / "w"), "--faults", "redzone.lab.night.faults:playbook"],
                          cwd=ROOT, env=env, capture_output=True, text=True, timeout=900)
    assert out.exists(), f"no report (exit {proc.returncode}):\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
    return json.loads(out.read_text())


def _check(night, name):
    c = {x["name"]: x for x in night["checks"]}
    assert name in c, f"the night did not grade {name}"
    return c[name]


@pytest.mark.playbook("FEED-02", "FEED-06", "MAC-05", "SAFE-03", "NET-02")
def test_playbook_faults_never_cause_a_mid_play_cut(pb_night):
    c = _check(pb_night, "playbook_faults_no_midplay")
    assert c["ok"], json.dumps(c["detail"], indent=1)
    # the lane-4 faults are graded; #99 added more playbook faults to the same night (STK/TV), also graded
    assert {"feed_frozen", "rate_limit", "slow_poll", "wifi_degrade", "buffering"} <= {x["fault"] for x in c["detail"]}


@pytest.mark.playbook("FEED-07")
def test_a_game_missing_from_the_scoreboard_is_handled(pb_night):
    c = _check(pb_night, "net_feed_faults_handled")
    assert c["ok"], json.dumps(c["detail"], indent=1)
    got = {x["fault"]: x for x in c["detail"]}
    if got.get("game_missing", {}).get("game"):                # no switch into it, still known after
        assert got["game_missing"]["switched_into"] == 0 and got["game_missing"].get("kept", True)


def test_playbook_night_does_not_crash_or_stick(pb_night):
    for name in ("no_crash", "no_stuck_state", "zero_midplay_cuts", "tv_shows_games"):
        c = _check(pb_night, name)
        assert c["ok"], (name, json.dumps(c["detail"], default=str)[:2000])
