"""playbook=BTN-06: the phone loses the app. The Mac keeps the last state and goes on switching; the
phone says it is offline after 10 s, a tap made meanwhile waits for the network and lands once with
its id, and the page reloads the app's state the moment it is back.

The server half runs everywhere. The browser half (tests/js/phone_offline.cjs: the real phone page in
Chromium against a fake app, a fake clock) needs node + Playwright and skips without them."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from test_app import client, engine  # noqa: F401  (fixtures)
from test_phone_control import _live

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.playbook("BTN-06")
def test_the_mac_keeps_the_state_while_the_phone_is_gone(client, engine):
    a = _live(engine)[0]
    tap = {"action": "hold", "game_id": a, "id": "phone-off-001"}
    first = client.post("/api/action", json=tap).json()
    assert first["ok"] and not first["duplicate"]
    # The phone drops. Nothing it held lapses because nobody is asking.
    engine.decider.user.manual_until = 0
    engine.decide()
    assert engine.decider.current == a and engine.hold["game_id"] == a
    # It comes back: its first look is GET /api/state (rz-api.js loadOnce), which shows the hold ...
    s = client.get("/api/state").json()
    assert s["mode"]["hold"]["game_id"] == a and s["mode"]["locked_game"] == a
    # ... and the tap it never heard back about is resent with the same id: answered, not rerun.
    again = client.post("/api/action", json=tap).json()
    assert again["duplicate"] and again["message"] == first["message"]
    assert engine.commands.runs == 1


def _browser() -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("no node: the browser half of BTN-06 needs node + Playwright")
    has = subprocess.run([node, "-e", "require('playwright')"], capture_output=True, cwd=ROOT)
    if has.returncode:
        pytest.skip("no Playwright for node")
    p = subprocess.run([node, str(ROOT / "tests/js/phone_offline.cjs")], capture_output=True, text=True,
                       timeout=180, cwd=ROOT)
    line = next((x for x in p.stdout.splitlines() if x.startswith("@@")), None)
    assert line, (p.stdout + p.stderr)[-1500:]
    got = json.loads(line[2:])
    if "Executable doesn't exist" in got.get("error", ""):
        pytest.skip("Playwright has no Chromium here")
    assert "error" not in got, got["error"]
    return got


@pytest.mark.playbook("BTN-06")
def test_the_phone_says_offline_then_resyncs_and_the_tap_lands_once():
    g = _browser()
    assert g["start"] == {"pill": "live", "banner": []}
    assert g["early"]["pill"] == "wait" and g["early"]["banner"] == []          # a 3 s blip: no banner
    off = g["offline"]
    assert off["pill"] == "wait" and len(off["banner"]) == 1 and "Phone offline" in off["banner"][0]
    assert off["posts"] == 0 and off["tapErr"] is None                         # waiting, not given up
    back = g["back"]
    assert back["pill"] == "live" and back["banner"] == [] and back["tapErr"] is None
    assert len(g["posts"]) == 1 and back["tap"]["id"] == g["posts"][0]["id"]   # once, with its id
    assert g["reloads"] >= 1 and back["paused"] is True                        # the app's state, reloaded
