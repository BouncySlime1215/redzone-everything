"""Gym v2 slice 1 (task A-01): with the identity scenario the practice world decides byte for byte as park_replay.

The whole-night check on 3 dev nights is `scripts/gym2_parity.py` (about 10 minutes of CPU); here the same comparison
runs on the first 15 minutes of each of the 3 nights, stepping tick by tick and observing at every pause.

Why: everything Gym v2 adds is only trustworthy if the world under it is the show's own, exactly.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from redzone.lab.gym2 import env as gym

ROOT = Path(__file__).resolve().parent.parent
needs_archive = pytest.mark.skipif(not (ROOT / "data/archive/nba").exists(), reason="pinned eval data not fetched (scripts/fetch_eval_data.py)")


@needs_archive
@pytest.mark.parametrize("night", ["20261021", "20261023", "20261028"])
def test_identity_scenario_reproduces_park_replay_decisions_exactly(night):
    ref = gym.replay_decisions(night, 4, minutes=15)
    env = gym.GymV2(night, 4, minutes=15)
    obs = env.reset()
    assert obs["jumps"] == 0
    steps = 0
    while True:
        env.observe()                                    # reading the show between ticks must change nothing
        obs, reward, done, info = env.step()
        steps += 1
        if done:
            break
    got = env.decisions()
    assert steps > 100 and ref["records"], "the window must hold jumps for the check to mean anything"
    for k in ref:
        assert got[k] == ref[k], f"{night}: {k} differs"
    assert gym.digest(got) == gym.digest(ref)


@needs_archive
def test_run_is_the_same_episode_and_reset_starts_it_again():
    env = gym.GymV2("20261023", 4, minutes=6)
    env.reset()
    first = env.run().digest()
    env.reset()
    assert env.run().digest() == first


def test_slice_1_takes_no_action():
    env = gym.GymV2("20261023", 4)
    with pytest.raises(RuntimeError):
        env.step()
    env.loop = object()                                  # (as if reset) an action is refused before anything runs
    with pytest.raises(ValueError):
        env.step(action=1)
    env.loop = None


def test_the_show_never_imports_gym2():
    """Research only: no module of the app (redzone/ outside lab/gym2) and not the replay script imports it."""
    pat = re.compile(r"^\s*(from|import)\s+redzone\.lab(\.gym2|\s+import\s+.*\bgym2\b)", re.M)
    hits = [p.relative_to(ROOT).as_posix() for p in (ROOT / "redzone").rglob("*.py")
            if "gym2" not in p.parts and pat.search(p.read_text(encoding="utf-8"))]
    assert hits == []
    assert not re.search(r"^\s*(from|import)\s+\S*gym2", (ROOT / "scripts/park_replay.py").read_text(encoding="utf-8"), re.M)
