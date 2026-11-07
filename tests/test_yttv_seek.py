"""[YTTV scrub buffer, item 1] redzone/tuners/yttv_seek.enter_buffer: focus guard, fail closed, knob check, commit,
and that media_session position is never used for behind-live. Offline: a fake stick + a fake overlay reader."""
import asyncio

import pytest

from redzone.tuners import yttv_seek as ys
from redzone.tuners.adb import RunResult

YT = "com.amazon.firetv.youtube.tv"


class Cfg(dict):
    def get(self, k, d=None):
        return super().get(k, d)


CFG = Cfg({"yttv_seek.key_gap_s": 0.0, "yttv_seek.overlay_wait_s": 0.0, "yttv_seek.poll_s": 0.0,
           "yttv_seek.settle_s": 0.05, "yttv_seek.reopen_wait_s": 0.0})


class FakeYTTV:
    """The overlay as measured Nov 6: CENTER opens it with the bar focused (label = playhead MM:SS), each LEFT moves
    the knob `step` s back, CENTER commits. Knobs: focus ('bar' | 'row' | 'none'), front app, state."""

    def __init__(self, focus="bar", front=YT, state="playing", step=15.0, label0=1800.0, after_commit="playing"):
        self.focus, self.front, self.state, self.step = focus, front, state, step
        self.label0, self.knob, self.after_commit = label0, label0, after_commit
        self.overlay, self.committed_back, self.keys = False, 0.0, []

    async def shell(self, cmd, timeout=8.0):
        code = cmd.split()[-1]
        self.keys.append(code)
        if code == "KEYCODE_DPAD_CENTER":
            if not self.overlay:
                self.overlay, self.knob = True, self.label0
            else:
                self.committed_back += self.label0 - self.knob
                self.overlay = False
                self.state = self.after_commit
        elif code == "KEYCODE_DPAD_LEFT" and self.overlay and self.focus == "bar":
            self.knob -= self.step
        return RunResult(True, "", 0.01)

    async def foreground(self):
        return f"{self.front}/.MainActivity"

    async def playback(self, package=None):
        # the position is deliberately nonsense after a seek: enter_buffer must not read it
        return {"state": self.state, "pkg": YT, "position": 12345, "updated": 1}


def reader(stick):
    async def read(_s):
        if not stick.overlay:
            return ys.OverlayRead()
        if stick.focus == "bar":
            return ys.parse_overlay([{"text": "9:00 PM - 11:00 PM", "x": .8, "y": .72},
                                     {"text": f"{int(stick.knob // 60)}:{int(stick.knob % 60):02d}", "x": .5, "y": .76},
                                     {"text": "cc", "x": .2, "y": .9}, {"text": "Networks", "x": .3, "y": .9}])
        if stick.focus == "row":
            return ys.parse_overlay([{"text": "Networks", "x": .3, "y": .9}],
                                    [{"text": "Networks", "x": .3, "y": .9}])
        return ys.parse_overlay([{"text": "Networks", "x": .3, "y": .9}])
    return read


def run(stick, seconds=60.0, **kw):
    kw.setdefault("read_overlay", reader(stick))
    return asyncio.run(ys.enter_buffer(stick, seconds, cfg=CFG, pkg=YT, **kw))


def relinker(log):
    async def relink():
        log.append("relink")
        return True
    return relink


def test_scrub_enters_the_buffer_with_four_lefts():
    s = FakeYTTV()
    r = run(s, 60.0, behind_before_s=25.0)
    assert r.ok and r.committed and r.presses == 4, r
    assert s.keys == ["KEYCODE_DPAD_CENTER"] + ["KEYCODE_DPAD_LEFT"] * 4 + ["KEYCODE_DPAD_CENTER"]
    assert s.committed_back == 60.0
    assert r.behind_live_s == 85.0 and not r.measured and r.behind_s is None   # estimate, never media_session


def test_label_reader_is_the_truth_when_given():
    s = FakeYTTV()

    async def label(_s):
        return 67.4
    r = run(s, 60.0, read_behind=label)
    assert r.ok and r.measured and r.behind_s == 67.4 and r.behind_live_s == 67.4


@pytest.mark.parametrize("kw,why", [({"front": "com.amazon.firebat"}, "not in front"),
                                    ({"state": "paused"}, "not playing")])
def test_preconditions_press_nothing(kw, why):
    s, log = FakeYTTV(**kw), []
    r = run(s, relink=relinker(log))
    assert not r.ok and why in r.reason and s.keys == [] and log == []


def test_not_hidden_presses_nothing():
    s = FakeYTTV()
    r = run(s, still_hidden=lambda: False)
    assert not r.ok and s.keys == []


@pytest.mark.parametrize("focus,why", [("row", "row button"), ("none", "no scrub label")])
def test_focus_guard_fails_closed_before_any_left(focus, why):
    """#356: LEFT with unknown focus seeked a stick 60 s. No LEFT unless the bar provably has focus."""
    s, log = FakeYTTV(focus=focus), []
    r = run(s, relink=relinker(log))
    assert not r.ok and why in r.reason, r
    assert not r.committed and s.committed_back == 0 and (focus == "none" or "KEYCODE_DPAD_LEFT" not in s.keys) and not r.committed
    assert log == ["relink"] and r.relinked is True        # the overlay was opened: re-link to live


def test_unreadable_overlay_fails_closed():
    s, log = FakeYTTV(), []

    async def blind(_s):
        return None
    r = run(s, read_overlay=blind, relink=relinker(log))
    assert not r.ok and "no scrub label" in r.reason and "KEYCODE_DPAD_LEFT" not in s.keys and log == ["relink"]


def test_knob_that_moved_the_wrong_amount_is_not_committed():
    s, log = FakeYTTV(step=10.0), []                      # an app that steps 10 s, not 15
    r = run(s, 60.0, relink=relinker(log))
    assert not r.ok and "knob moved 30s" in r.reason
    assert s.keys.count("KEYCODE_DPAD_CENTER") == 1 and not r.committed and log == ["relink"]


def test_visible_mid_scrub_is_never_pressed_or_relinked():
    s, log, calls = FakeYTTV(), [], []

    def hidden():
        calls.append(1)
        return len(calls) < 3          # visible right before the commit
    r = run(s, still_hidden=hidden, relink=relinker(log))
    assert not r.ok and "visible" in r.reason and not r.committed
    assert log == [] and r.relinked is False


def test_not_playing_after_commit_relinks():
    s, log = FakeYTTV(after_commit="buffering"), []
    r = run(s, relink=relinker(log))
    assert not r.ok and r.committed and "not playing" in r.reason and log == ["relink"]


def test_cap_and_zero():
    s = FakeYTTV(label0=3600.0)
    r = run(s, 600.0)
    assert r.ok and r.presses == 10                       # max_back_s 150 / 15
    s2 = FakeYTTV()
    r2 = run(s2, 5.0, behind_before_s=30.0)
    assert r2.ok and s2.keys == [] and r2.behind_live_s == 30.0


def test_never_raises():
    s = FakeYTTV()

    async def boom(*a, **k):
        raise RuntimeError("adb gone")
    s.foreground = boom
    r = run(s)
    assert not r.ok and "exception" in r.reason


def test_parse_overlay_rules():
    o = ys.parse_overlay([{"text": "9:00 PM - 11:00 PM", "y": .72}, {"text": "1:02:05", "y": .76}])
    assert o.bar_focused and o.scrub_s == 3725
    two = ys.parse_overlay([{"text": "12:00", "y": .7}, {"text": "13:00", "y": .75}])
    assert two.scrub_s is None and not two.bar_focused   # which one is the knob's? fail closed
    hd = ys.parse_overlay([{"text": "12:00", "y": .76}], [{"text": "HD", "y": .9}])
    assert hd.row_focused and not hd.bar_focused
    assert ys.mmss("12:75") is None and ys.mmss("1:05") == 65


def test_live_banner_after_a_tune_is_closed_before_the_scrub():
    """Live, Nov 6: after a tune a LIVE/title banner looks like the overlay but keys do nothing; no label = reopen."""
    s = FakeYTTV()
    base, seen = reader(s), []

    async def read(st):
        seen.append(1)
        if len(seen) <= 2:                                 # banner: open read + the probe read, no scrub label
            return ys.parse_overlay([{"text": "cc", "x": .2, "y": .9}, {"text": "Networks", "x": .3, "y": .9}])
        return await base(st)

    r = run(s, 60.0, read_overlay=read)
    assert r.ok and r.committed and r.presses == 4, r


def _misses_probe_once(s):
    base, seen = reader(s), []

    async def read(st):
        seen.append(1)
        return None if len(seen) == 2 else await base(st)   # the probe landed but OCR missed its label
    return read


def test_a_blind_probe_close_makes_the_delay_unknown():
    """The CENTER that closes an unread probe can commit that LEFT: the stick ends 75 s back, not the 60 s estimate."""
    s = FakeYTTV()
    r = run(s, 60.0, read_overlay=_misses_probe_once(s))
    assert s.committed_back == 75.0
    assert r.ok and r.committed and r.behind_live_s is None and r.behind_s is None and "unknown" in r.reason


def test_a_blind_probe_close_is_fine_with_a_label_read():
    s = FakeYTTV()

    async def label(_s):
        return 75.2
    r = run(s, 60.0, read_overlay=_misses_probe_once(s), read_behind=label)
    assert r.ok and r.measured and r.behind_s == 75.2
