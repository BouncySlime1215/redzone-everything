"""[Prime buffer] redzone/tuners/prime_pause.enter_buffer_pause: pause holds on Prime (overseer, Nov 7), the timecode is
the truth, fail closed. Offline: a fake Prime stick with a content clock and a fake wall clock."""
import asyncio

import pytest

from redzone.tuners import prime_pause as pp
from redzone.tuners.adb import RunResult

PRIME = "com.amazon.firebat"


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakePrime:
    """Content position advances with the wall clock while playing. snap_at: content jumps to live after that many
    wall seconds paused (a YTTV-like snap); overlay_after_play: whether the timecode is readable after PLAY."""

    def __init__(self, clock, front=PRIME, state="playing", t0=8454.0, snap_after=None, overlay_after_play=True,
                 resumes=True):
        self.clock, self.front, self.state = clock, front, state
        self.pos, self.mark = t0, clock()
        self.snap_after, self.overlay_after_play, self.resumes = snap_after, overlay_after_play, resumes
        self.keys, self.paused_at = [], None

    def position(self):
        if self.state == "playing":
            return self.pos + (self.clock() - self.mark)
        if self.snap_after is not None and self.clock() - self.paused_at >= self.snap_after:
            return self.pos + (self.clock() - self.mark)          # it went back to live behind our back
        return self.pos

    async def shell(self, cmd, timeout=8.0):
        code = cmd.split()[-1]
        self.keys.append(code)
        if code == "KEYCODE_MEDIA_PAUSE" and self.state == "playing":
            self.pos, self.mark, self.state, self.paused_at = self.position(), self.clock(), "paused", self.clock()
        elif code == "KEYCODE_MEDIA_PLAY" and self.state == "paused" and self.resumes:
            self.pos, self.mark, self.state = self.position(), self.clock(), "playing"
        return RunResult(True, "", 0.01)

    async def foreground(self):
        return f"{self.front}/.MainActivity"

    async def playback(self, package=None):
        return {"state": self.state, "pkg": PRIME, "position": 12345, "updated": 1}   # stale: never read


def run(stick, clock, seconds=60.0, monkeypatch=None, reader=None, **kw):
    async def fake_sleep(s):
        clock.t += max(0.0, float(s))
    monkeypatch.setattr(pp.asyncio, "sleep", fake_sleep)

    async def read(_s):
        if stick.state == "playing" and not stick.overlay_after_play:
            return None
        return float(int(stick.position()))                      # the overlay shows whole seconds
    return asyncio.run(pp.enter_buffer_pause(stick, seconds, read_timecode=reader or read, clock=clock, **kw))


def relinker(log):
    async def relink():
        log.append("relink")
        return True
    return relink


def test_pause_60_gains_60_on_the_timecode(monkeypatch):
    c = Clock()
    s = FakePrime(c)
    r = run(s, c, 60.0, monkeypatch, behind_before_s=6.6)
    assert r.ok and r.measured and r.committed, r
    assert s.keys == ["KEYCODE_MEDIA_PAUSE", "KEYCODE_MEDIA_PLAY"]
    assert abs(r.gained_s - 60.0) <= 1.5 and abs(r.behind_live_s - 66.6) <= 1.5
    assert "KEYCODE_MEDIA_REWIND" not in s.keys


def test_no_timecode_after_play_is_an_estimate(monkeypatch):
    c = Clock()
    s = FakePrime(c, overlay_after_play=False)
    r = run(s, c, 60.0, monkeypatch)
    assert r.ok and not r.measured and r.behind_s is None and abs(r.gained_s - 60.0) < 0.6


@pytest.mark.parametrize("kw,why", [({"front": "com.amazon.firetv.youtube.tv"}, "not in front"),
                                    ({"state": "paused"}, "not playing")])
def test_preconditions_press_nothing(monkeypatch, kw, why):
    c, log = Clock(), []
    s = FakePrime(c, **kw)
    r = run(s, c, 60.0, monkeypatch, relink=relinker(log))
    assert not r.ok and why in r.reason and s.keys == [] and log == []


def test_unreadable_timecode_unpauses_and_relinks(monkeypatch):
    c, log = Clock(), []
    s = FakePrime(c)

    async def blind(_s):
        return None
    r = run(s, c, 60.0, monkeypatch, reader=blind, relink=relinker(log))
    assert not r.ok and "unreadable" in r.reason and s.state == "playing" and log == ["relink"]
    assert not r.committed


def test_a_snap_during_the_hold_fails_closed(monkeypatch):
    c, log = Clock(), []
    s = FakePrime(c, snap_after=20.0)
    r = run(s, c, 60.0, monkeypatch, relink=relinker(log))
    assert not r.ok and "moved during the hold" in r.reason and log == ["relink"] and s.state == "playing"


def test_visible_mid_hold_unpauses_and_does_not_relink(monkeypatch):
    c, log, calls = Clock(), [], []
    s = FakePrime(c)

    def hidden():
        calls.append(1)
        return len(calls) < 5
    r = run(s, c, 60.0, monkeypatch, still_hidden=hidden, relink=relinker(log))
    assert not r.ok and "visible" in r.reason and s.state == "playing" and log == [] and r.relinked is False


def test_not_playing_after_play_relinks(monkeypatch):
    c, log = Clock(), []
    s = FakePrime(c, resumes=False)
    r = run(s, c, 60.0, monkeypatch, relink=relinker(log))
    assert not r.ok and "not playing" in r.reason and log == ["relink"]


def test_cap_and_zero(monkeypatch):
    c = Clock()
    s = FakePrime(c)
    r = run(s, c, 600.0, monkeypatch)
    assert r.ok and abs(r.gained_s - 120.0) <= 1.5                  # max_hold_s
    c2 = Clock()
    s2 = FakePrime(c2)
    r2 = run(s2, c2, 0.0, monkeypatch, behind_before_s=30.0)
    assert r2.ok and s2.keys == [] and r2.behind_live_s == 30.0


def test_never_raises(monkeypatch):
    c = Clock()
    s = FakePrime(c)

    async def boom(*a, **k):
        raise RuntimeError("adb gone")
    s.foreground = boom
    r = run(s, c, 60.0, monkeypatch)
    assert not r.ok and "exception" in r.reason


def test_timecode_parse_rules():
    assert pp.hms("2:20:54") == (False, 8454) and pp.hms("-0:01:05") == (True, 65) and pp.hms("4:32") is None
    assert pp.hms("1:75:00") is None
    obs = [{"text": "2:20:54", "y": .9}, {"text": "-0:00:12", "y": .9}, {"text": "4:32", "y": .1}]
    assert pp.parse_timecode(obs) == 8454
    assert pp.parse_timecode([{"text": "2:20:54", "y": .2}]) is None                      # outside the band
    assert pp.parse_timecode([{"text": "2:20:54", "y": .9}, {"text": "2:21:00", "y": .9}]) is None   # which one?
