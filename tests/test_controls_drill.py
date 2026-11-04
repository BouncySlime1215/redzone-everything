"""The controls drill as a test (scripts/controls_drill.py): one replayed 3 h night with scripted phone
presses, taps lost and duplicated on the way (RZ_FLAKY=1), the SmartThings TV path. About a minute.
Graded from what the TV showed (house.shown), not from what the app said."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _drill():
    spec = importlib.util.spec_from_file_location("controls_drill", ROOT / "scripts/controls_drill.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def drill():
    from redzone.lab.night import run as night_run
    if not all((night_run.FIX / f"{g.fixture}.json").exists() for g in night_run.GAMES):
        pytest.skip("the night's ESPN fixtures are not in tests/fixtures")
    was = os.environ.get("RZ_FLAKY")
    os.environ["RZ_FLAKY"] = "1"
    try:
        g = _drill().one(1, "smartthings", 3.0, False)
    finally:
        if was is None:
            os.environ.pop("RZ_FLAKY", None)
        else:
            os.environ["RZ_FLAKY"] = was
    assert "error" not in g, g.get("error")
    return g


@pytest.mark.playbook("BTN-01")
def test_lock_in_holds_until_released(drill):
    assert drill["n"].get("lock", 0) >= 1, "no LOCK IN press landed in the night"
    assert drill["lock_broken"] == [], drill["lock_broken"]


@pytest.mark.playbook("BTN-02")
def test_go_now_always_lands_on_the_tv(drill):
    assert drill["n"].get("go_now", 0) >= 1 and len(drill["go_now_s"]) == drill["n"]["go_now"]
    assert not [d for d in drill["dropped"] if d["kind"] in ("go_now", "open_go")], drill["dropped"]


@pytest.mark.playbook("BTN-03")
def test_lost_and_duplicated_taps_run_exactly_once(drill):
    assert drill["dropped"] == [] and drill["pause_broken"] == [] and drill["skip_stuck"] == []
    assert drill["not_once"] == [] and drill.get("taps_retried", 0) >= 1


@pytest.mark.playbook("BTN-04")
def test_a_released_lock_hands_back_without_a_mid_play_cut(drill):
    assert drill.get("released_at"), "no LOCK IN was released in the night"
    after = [m for m in drill.get("mid_play", []) if m.get("why") == "after_release"]
    assert after == [], after
