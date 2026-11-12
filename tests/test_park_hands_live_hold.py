"""park_hands_live.hold_check: the hands test's pause check (a park held 3-5 minutes on the hidden stick, read back).

HELD only when the stick reads paused at every read, the frozen place moves at most 1 s, the resume starts from that
place (not the live edge) and it then plays at normal speed; a silence readback that is not "ok" stops it with
nothing more sent. Fake controller, fake stick reads and a fake clock: no device.
"""
from __future__ import annotations

import asyncio
import importlib.util
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def phl():
    spec = importlib.util.spec_from_file_location("park_hands_live_hold_under_test", ROOT / "scripts" / "park_hands_live.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@dataclass
class Step:
    step: str
    outcome: str = "ok"
    why: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome == "ok"


@dataclass
class Read:
    wall: float
    state: str
    position_s: float | None
    pos_now_s: float | None


@dataclass
class Place:
    ref_pos: float = 0.0
    ref_wall: float = 0.0


class World:
    """A hidden stick frozen at FROZEN; `drift(t)` moves the paused report, `resume_at` is where a resume lands."""
    FROZEN = 1000.0

    def __init__(self, drift=lambda t: 0.0, resume_offset=0.3, state_at=lambda t: "paused", sounds=None, speed=1.0):
        self.t, self.t0, self.drift, self.resume_offset, self.state_at = 0.0, None, drift, resume_offset, state_at
        self.sounds, self.speed, self.resumed, self.calls = list(sounds or []), speed, None, []
        self.place = Place()
        self.hidden = object()

    # clock
    def now(self) -> float:
        return self.t

    async def sleep(self, s: float) -> None:
        self.t += s

    # controller
    async def park_tune(self, target):
        self.calls.append("tune")
        return Step("tune")

    async def park_pause(self, target, at):
        self.calls.append("park")
        self.t0 = self.t
        return Step("park")

    async def park_resume(self, target):
        self.calls.append("resume")
        self.t += 0.5
        self.resumed = self.t
        self.place.ref_pos, self.place.ref_wall = self.FROZEN + self.resume_offset, self.t
        return Step("resume")

    def park_hands(self):
        return self

    def place_of(self, stick):
        return self.place

    async def _read(self, stick):
        if self.resumed is None:
            dt = self.t - self.t0
            return Read(self.t, self.state_at(dt), self.FROZEN + self.drift(dt), self.FROZEN + self.drift(dt))
        pos = self.place.ref_pos + self.speed * (self.t - self.resumed)
        return Read(self.t, "playing", pos, pos)

    # sound guard
    async def check(self, who):
        return self.sounds.pop(0) if self.sounds else "ok"


def hold(phl, w: World, hold_s: float = 240.0):
    return asyncio.run(phl.hold_check(w, w, "youtubetv:nbatv", hold_s, 30.0, sleep=w.sleep, now=w.now))


def test_a_held_park(phl):
    w = World()
    v = hold(phl, w)
    assert v["held"] and not v["stop"], v["why"]
    assert len(v["reads"]) == 9                               # before, every 30 s, at 240 s
    assert all(r["state"] == "paused" and r["sound"] == "ok" for r in v["reads"])
    assert w.calls == ["tune", "park", "resume"]


def test_a_resume_that_jumps_to_the_live_edge_is_not_held(phl):
    v = hold(phl, World(resume_offset=240.0))
    assert not v["held"] and "resume started" in v["why"]


def test_a_frozen_place_that_moves_is_not_held(phl):
    v = hold(phl, World(drift=lambda t: 5.0 if t >= 120 else 0.0))
    assert not v["held"] and "moved" in v["why"]


def test_a_stick_that_starts_playing_during_the_hold_is_not_held(phl):
    v = hold(phl, World(state_at=lambda t: "playing" if t >= 90 else "paused"))
    assert not v["held"] and "not paused" in v["why"]


def test_a_stream_that_stalls_after_the_resume_is_not_held(phl):
    v = hold(phl, World(speed=0.8))
    assert not v["held"] and "off the wall clock" in v["why"]


def test_silence_not_read_back_stops_with_nothing_more_sent(phl):
    w = World(sounds=["ok", "ok", "unreadable"])
    v = hold(phl, w)
    assert v["stop"] and not v["held"]
    assert "resume" not in w.calls                            # the hold ends; nothing more is sent to the stick
