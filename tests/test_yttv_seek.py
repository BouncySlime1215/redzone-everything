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
        self.left_n, self.lose, self.revealed, self.reveal_only = 0, set(), False, True

    async def shell(self, cmd, timeout=8.0):
        code = cmd.split()[-1]
        self.keys.append(code)
        if code == "KEYCODE_DPAD_CENTER":
            if not self.overlay:
                self.overlay, self.knob, self.revealed = True, self.label0, False
            else:
                self.committed_back += self.label0 - self.knob
                self.overlay = False
                self.state = self.after_commit
        elif code == "KEYCODE_DPAD_LEFT" and self.overlay and self.focus == "bar":
            if not self.revealed and self.knob == self.label0 and self.reveal_only:
                self.revealed = True                         # at the live edge the first LEFT only shows the label
                return RunResult(True, "", 0.01)
            self.left_n += 1
            if self.left_n not in self.lose:                  # a lost press: sent, never landed
                self.knob = max(0.0, self.knob - self.step)   # the bar starts at the program start
        elif code == "KEYCODE_DPAD_RIGHT" and self.overlay and self.focus == "bar":
            self.knob = min(self.knob + self.step, self.label0)   # pinned at the live edge
            self.revealed = True
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
    kw.setdefault("cfg", CFG)
    return asyncio.run(ys.enter_buffer(stick, seconds, pkg=YT, **kw))


def relinker(log):
    async def relink():
        log.append("relink")
        return True
    return relink


def test_scrub_enters_the_buffer_with_four_lefts():
    s = FakeYTTV()
    s.reveal_only = False                                 # behind live: the LEFT probe moves a step
    r = run(s, 60.0, behind_before_s=25.0)
    assert r.ok and r.committed and r.presses == 4, r
    assert s.keys == ["KEYCODE_DPAD_CENTER"] + ["KEYCODE_DPAD_LEFT"] * 4 + ["KEYCODE_DPAD_CENTER"]
    assert s.committed_back == 60.0
    assert abs(r.behind_live_s - 85.0) < 0.5 and not r.measured and r.behind_s is None   # estimate, never media_session


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
    r = run(s, 60.0, relink=relinker(log), cfg=Cfg({**CFG, "yttv_seek.boundary_retries": 0}))
    assert not r.ok and "knob moved" in r.reason
    assert s.keys.count("KEYCODE_DPAD_CENTER") == 1 and not r.committed and log == ["relink"]


class Boundary(FakeYTTV):
    """A new program starts mid-scrub on the first try: the label resets, so the knob looks like it moved 39 s
    (Nick's stick, Nov 7 ~1:00 AM). The re-link closes the overlay at the live edge; the second try is normal."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.lefts = 0

    async def shell(self, cmd, timeout=8.0):
        out = await super().shell(cmd, timeout)
        if cmd.split()[-1] == "KEYCODE_DPAD_LEFT":
            self.lefts += 1
            if self.lefts == 2:          # the second LEFT of the first try: the program boundary
                self.knob = self.label0 - 15.0 - 39.0 + 15.0
        return out


def test_program_boundary_retries_once_from_the_live_edge():
    s, log = Boundary(), []

    async def relink():
        log.append("relink")
        s.overlay, s.knob = False, s.label0      # back at the live edge, nothing committed
        return True
    r = asyncio.run(ys.enter_buffer(s, 60.0, cfg=CFG, pkg=YT, read_overlay=reader(s), relink=relink))
    assert r.ok and r.committed and r.retries == 1 and log == ["relink"]
    assert any("retry from the live edge" in x for x in r.steps)


def test_boundary_retry_needs_a_relink():
    s = Boundary()
    r = run(s, 60.0)                               # no relink: position unknown, never retried
    assert not r.ok and "knob moved" in r.reason and r.retries == 0 and not r.committed


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
    assert r.ok and r.committed and len(seen) >= 4, r      # closed, reopened, then scrubbed


LEFT_PROBE = Cfg({**CFG, "yttv_seek.probe_key": "LEFT"})


def _misses_probe_once(s):
    base, seen = reader(s), []

    async def read(st):
        seen.append(1)
        return None if len(seen) == 2 else await base(st)   # the probe landed but OCR missed its label
    return read


def test_a_blind_probe_close_makes_the_delay_unknown():
    """The CENTER that closes an unread probe can commit that LEFT: the stick ends 75 s back, not the 60 s estimate."""
    s = FakeYTTV()
    s.reveal_only = False                                 # a probe that did land a step
    r = run(s, 60.0, read_overlay=_misses_probe_once(s), cfg=LEFT_PROBE)
    assert s.committed_back > 60.0                        # the blind close committed a step the estimate can't see
    assert r.ok and r.committed and r.behind_live_s is None and r.behind_s is None and "unknown" in r.reason


def test_a_blind_probe_close_is_fine_with_a_label_read():
    s = FakeYTTV()

    async def label(_s):
        return 75.2
    r = run(s, 60.0, read_overlay=_misses_probe_once(s), read_behind=label, cfg=LEFT_PROBE)
    assert r.ok and r.measured and r.behind_s == 75.2


class Wall:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_time_in_the_overlay_is_planned_and_counted():
    """Nick's 90 s run (Nov 7): the picture stands still while the overlay is up, so asked 90 with 6 LEFTs and 37.6 s
    in the overlay ended 130.8 s behind. Each OCR read here costs 8 s; the stick ends near the ask, not ask + took."""
    wall, s = Wall(), FakeYTTV(label0=3600.0)
    base = reader(s)

    async def slow_read(st):
        wall.t += 8.0
        return await base(st)
    r = asyncio.run(ys.enter_buffer(s, 90.0, cfg=CFG, pkg=YT, read_overlay=slow_read, clock=wall))
    frozen = 24.0                                         # three reads while the overlay was up
    assert r.ok and r.presses == 4, r                     # not 6: 4 x 15 + 24 s in the overlay = 84, nearest to 90
    assert abs((s.committed_back + frozen) - 90.0) <= 7.5
    assert abs(r.behind_live_s - (s.committed_back + frozen)) < 0.01


RIGHT_PROBE = Cfg({**CFG, "yttv_seek.probe_key": "RIGHT"})


def test_right_is_the_default_probe_and_the_left_probe_moves_the_same():
    """Nick's 12f405ab runs (Nov 7): RIGHT 8/8 (63.0 mean) and LEFT 6/6 (62.1) landed the same for target 60: at the
    live edge the LEFT probe only reveals the label too. A fixed n = 3 moves the knob 30 s either way."""
    s = FakeYTTV(label0=3600.0)
    r = run(s, 60.0, presses=3, cfg=Cfg({k: v for k, v in CFG.items()}))
    assert r.ok and s.keys[1] == "KEYCODE_DPAD_RIGHT" and s.committed_back == 30.0
    s2 = FakeYTTV(label0=3600.0)
    r2 = run(s2, 60.0, presses=3, cfg=LEFT_PROBE)
    assert r2.ok and "KEYCODE_DPAD_RIGHT" not in s2.keys and s2.keys.count("KEYCODE_DPAD_LEFT") == 3
    assert s2.committed_back == 30.0 and r2.presses == 3


def test_right_probe_moves_like_the_left_probe_for_a_fixed_count():
    """Nick's #403 run (Nov 7), mode B: with the RIGHT probe all 3 LEFTs moved and it landed one step deeper (78 vs
    64.2). A fixed count now sends n-1 LEFTs after a RIGHT probe: the same motion as the LEFT probe at the live edge
    (which only reveals the label), so the shared join predictor holds for both. Every LEFT is still checked."""
    s = FakeYTTV(label0=3600.0)
    r = run(s, 60.0, presses=3, cfg=RIGHT_PROBE)
    assert r.ok and s.keys[:2] == ["KEYCODE_DPAD_CENTER", "KEYCODE_DPAD_RIGHT"]
    assert s.keys.count("KEYCODE_DPAD_LEFT") == 2 and s.committed_back == 30.0 and r.presses == 2


@pytest.mark.parametrize("lost", [1, 2])
def test_lost_press_is_topped_up_before_the_commit(lost):
    """Nick's canary run 1 (Nov 7, 5f80eb94): espn target 60, 3 presses, landed one 15 s step short, and the row
    said ok. With the RIGHT probe every LEFT is checked: a lost one is seen, one more LEFT, re-read, then commit."""
    s = FakeYTTV(label0=3600.0)
    s.lose = {lost}
    r = run(s, 60.0, presses=3, cfg=RIGHT_PROBE)
    assert r.ok and r.committed and s.committed_back == 30.0, r
    assert s.keys.count("KEYCODE_DPAD_LEFT") == 3 and r.presses == 3
    assert any("lost press" in x for x in r.steps)


def test_a_lost_press_after_the_left_probe_is_topped_up_too():
    s = FakeYTTV(label0=3600.0)
    s.lose = {2}                                         # the probe reveals, one LEFT lands, the next doesn't
    r = run(s, 60.0, presses=3, cfg=LEFT_PROBE)
    assert r.ok and s.committed_back == 30.0 and any("lost press" in x for x in r.steps), r


def test_two_lost_presses_fail_closed_and_say_where_the_knob_was():
    s, log = FakeYTTV(label0=3600.0), []
    s.lose = {1, 2}
    r = run(s, 60.0, presses=3, relink=relinker(log),
            cfg=Cfg({**RIGHT_PROBE, "yttv_seek.boundary_retries": 0}))
    assert not r.ok and not r.committed and "knob moved 0s, not 30s (3600s -> 3600s)" in r.reason
    assert log == ["relink"] and s.committed_back == 0.0


def test_not_at_live_keeps_the_left_probe():
    """Behind live, a RIGHT would move the knob forward: the LEFT probe is used even when RIGHT is configured."""
    s = FakeYTTV(label0=3600.0)
    s.reveal_only = False                                 # behind live the LEFT probe is assumed to move a step
    r = run(s, 60.0, presses=3, behind_before_s=30.0, cfg=RIGHT_PROBE)
    assert r.ok and "KEYCODE_DPAD_RIGHT" not in s.keys and s.committed_back == 45.0


# ------------------------------------------- Nick's FOX 50 joins right after kickoff (Nov 7 ~12:05 ET): program start
def test_a_scrub_deeper_than_the_program_fails_closed_before_any_left():
    """Label 5 s at the live edge, a 60 s join (2 LEFTs = 30 s): used to press both and fail 'knob moved 5s, not 30s
    (5s -> 0s)'. Now: no LEFT, fail closed, relinked, and the room is reported."""
    s = FakeYTTV(label0=5.0)
    rl = []

    async def relink():
        rl.append(1)
        return True
    r = asyncio.run(ys.enter_buffer(s, 60, cfg=CFG, pkg=YT, read_overlay=reader(s), relink=relink, presses=3))
    assert not r.ok and r.reason.startswith("program start: the bar has 5s, the scrub needs 30s")
    assert s.keys.count("KEYCODE_DPAD_LEFT") == 0 and r.room_s == 5.0 and r.relinked is True and rl
    assert r.retries == 0                     # not a "knob moved" reason: no boundary retry from the edge


def test_a_program_with_enough_room_scrubs_as_before():
    s = FakeYTTV(label0=52.0)                 # Nick's third FOX run: label 52 s, 2 LEFTs fit
    r = asyncio.run(ys.enter_buffer(s, 60, cfg=CFG, pkg=YT, read_overlay=reader(s), presses=3))
    assert r.ok and s.committed_back == 30.0 and r.room_s == 52.0


def test_the_program_start_guard_can_be_turned_off():
    s = FakeYTTV(label0=5.0)
    cfg = Cfg({**CFG, "yttv_seek.program_start_guard": False, "yttv_seek.boundary_retries": 0})
    r = asyncio.run(ys.enter_buffer(s, 60, cfg=cfg, pkg=YT, read_overlay=reader(s), presses=3))
    assert not r.ok and r.reason.startswith("knob moved 5s, not 30s")


# ------------------------------------------- [build plan WP-03] one read fewer, band capture, per-read timing
SKIP = Cfg({**CFG, "yttv_seek.skip_open_read": True})
CENTER, RIGHT, LEFT = "KEYCODE_DPAD_CENTER", "KEYCODE_DPAD_RIGHT", "KEYCODE_DPAD_LEFT"


async def _label_75(_s):
    return 75.0


def _whats(r):
    return [x["what"] for x in r.reads]


def test_skip_open_read_is_three_reads_not_four_and_the_same_keys():
    """Exit check: the open read goes; the probe read, the knob read and the label read after the commit stay."""
    off, on = FakeYTTV(label0=3600.0), FakeYTTV(label0=3600.0)
    r_off = run(off, 60.0, presses=3, read_behind=_label_75)
    r_on = run(on, 60.0, presses=3, read_behind=_label_75, cfg=SKIP)
    assert _whats(r_off) == ["open", "probe", "knob", "label"]
    assert _whats(r_on) == ["probe", "knob", "label"]
    assert r_on.ok and r_on.committed and on.keys == off.keys == [CENTER, RIGHT, LEFT, LEFT, CENTER]
    assert on.committed_back == off.committed_back == 30.0


def test_flag_off_is_the_default_and_keeps_every_read():
    assert ys.SCRUB_DEFAULTS["skip_open_read"] is False and ys.SCRUB_DEFAULTS["overlay_band_from"] is None
    s, seen = FakeYTTV(label0=3600.0), []
    base = reader(s)

    async def read(st):
        seen.append(list(st.keys))                       # which keys were already sent at each read
        return await base(st)
    r = run(s, 60.0, presses=3, read_overlay=read)
    assert r.ok and seen == [[CENTER], [CENTER, RIGHT], [CENTER, RIGHT, LEFT, LEFT]]


@pytest.mark.parametrize("kw,cfg", [({"behind_before_s": 30.0}, SKIP),
                                    ({}, Cfg({**SKIP, "yttv_seek.probe_key": "LEFT"}))])
def test_skip_needs_a_right_probe_at_the_live_edge(kw, cfg):
    """Behind live or with a LEFT probe, the LEFT comes first and only the open read guards it: the read stays."""
    s = FakeYTTV(label0=3600.0)
    r = run(s, 60.0, presses=3, cfg=cfg, **kw)
    assert r.ok and _whats(r)[0] == "open" and RIGHT not in s.keys


def test_skip_row_focus_fails_closed_at_the_probe_read():
    s, log = FakeYTTV(focus="row"), []
    r = run(s, relink=relinker(log), cfg=SKIP)
    assert not r.ok and "row button" in r.reason and not r.committed
    assert s.keys == [CENTER, RIGHT] and log == ["relink"] and s.committed_back == 0


def test_skip_no_label_still_fails_closed():
    s, log = FakeYTTV(focus="none"), []
    r = run(s, relink=relinker(log), cfg=SKIP)
    assert not r.ok and "no scrub label" in r.reason and not r.committed and s.committed_back == 0
    assert log == ["relink"] and _whats(r).count("open") == 3     # later attempts read before any key


def test_skip_unreadable_probe_read_presses_nothing_more():
    """Without the open read, a failed read after the RIGHT means focus was never seen: no LEFT, no CENTER (#356)."""
    s, log = FakeYTTV(), []

    async def blind(_s):
        return None
    r = run(s, read_overlay=blind, relink=relinker(log), cfg=SKIP)
    assert not r.ok and r.reason.startswith("focus guard") and s.keys == [CENTER, RIGHT] and log == ["relink"]


def test_skip_no_overlay_at_the_probe_read_reopens_and_reads_first():
    s = FakeYTTV(label0=3600.0)
    base, seen = reader(s), []

    async def read(st):
        seen.append(1)
        return ys.OverlayRead() if len(seen) == 1 else await base(st)   # the overlay had not come up yet
    r = run(s, 60.0, presses=3, read_overlay=read, cfg=SKIP)
    assert s.keys[:3] == [CENTER, RIGHT, CENTER]          # no LEFT on an overlay nobody has seen
    assert r.ok and _whats(r)[:2] == ["probe", "open"] and s.committed_back == 30.0, r


def test_skip_banner_takes_the_left_probe_like_flag_off():
    """A read that shows the overlay without a label proves what the open read would have: LEFT probe as today."""
    s = FakeYTTV(label0=3600.0)
    base, seen = reader(s), []

    async def read(st):
        seen.append(1)
        if len(seen) == 1:
            return ys.parse_overlay([{"text": "cc", "x": .2, "y": .9}])
        return await base(st)
    r = run(s, 60.0, presses=3, read_overlay=read, cfg=SKIP)
    assert s.keys[:3] == [CENTER, RIGHT, LEFT] and r.ok and r.committed, r


def test_skip_checks_hidden_before_the_right():
    s, log, calls = FakeYTTV(), [], []

    def hidden():
        calls.append(1)
        return len(calls) < 2                             # visible right after the overlay opened
    r = run(s, still_hidden=hidden, relink=relinker(log), cfg=SKIP)
    assert not r.ok and "visible" in r.reason and s.keys == [CENTER] and log == []


def test_skip_checks_hidden_before_a_fallback_left():
    s, log, calls = FakeYTTV(), [], []

    def hidden():
        calls.append(1)
        return len(calls) < 3                             # visible after the RIGHT, before a LEFT probe
    base = reader(s)

    async def banner(st):
        return ys.parse_overlay([{"text": "cc", "x": .2, "y": .9}]) if RIGHT in st.keys else await base(st)
    r = run(s, still_hidden=hidden, read_overlay=banner, relink=relinker(log), cfg=SKIP)
    assert not r.ok and "visible" in r.reason and s.keys == [CENTER, RIGHT] and log == []


@pytest.mark.parametrize("lost", [1, 2])
def test_skip_keeps_the_knob_read_that_catches_a_lost_press(lost):
    s = FakeYTTV(label0=3600.0)
    s.lose = {lost}
    r = run(s, 60.0, presses=3, cfg=SKIP)
    assert r.ok and r.committed and s.committed_back == 30.0 and s.keys.count(LEFT) == 3, r
    assert _whats(r) == ["probe", "knob", "topup"] and any("lost press" in x for x in r.steps)


def test_skip_two_lost_presses_never_commit():
    s, log = FakeYTTV(label0=3600.0), []
    s.lose = {1, 2}
    r = run(s, 60.0, presses=3, relink=relinker(log), cfg=Cfg({**SKIP, "yttv_seek.boundary_retries": 0}))
    assert not r.ok and not r.committed and "knob moved 0s, not 30s" in r.reason and log == ["relink"]
    assert s.committed_back == 0.0


def test_each_read_is_timed_and_goes_in_the_row():
    s = FakeYTTV(label0=3600.0)
    base = reader(s)

    async def slow(st):
        await asyncio.sleep(0.02)
        return await base(st)

    async def label(_s):
        await asyncio.sleep(0.03)
        return 75.0
    r = run(s, 60.0, presses=3, read_overlay=slow, read_behind=label)
    assert [x["what"] for x in r.row()["reads"]] == ["open", "probe", "knob", "label"]
    assert all(x["s"] >= 0.015 for x in r.reads) and r.reads[-1]["s"] >= 0.025


@pytest.mark.parametrize("v,want", [(None, None), (0.3, 0.3), (0.25, 0.25), (0.31, None), (0.75, None), (0, None),
                                    (-0.1, None), (True, None), ("x", None)])
def test_a_band_that_could_cut_a_label_candidate_means_full_frame(v, want):
    """parse_overlay's one-label rule needs every MM:SS at y > 0.3 in view: cut one off and the other looks unique."""
    assert ys.overlay_band(Cfg({"yttv_seek.overlay_band_from": v})) == want
    assert ys.overlay_band(None) is None


class BandStick(FakeYTTV):
    """FakeYTTV that also captures. `band` is what screencap_band returns, or an exception to raise."""

    def __init__(self, band=b"BAND", **kw):
        super().__init__(**kw)
        self.band, self.grabs = band, []

    async def screencap_band(self, from_frac, timeout=8.0):
        self.grabs.append(("band", from_frac))
        if isinstance(self.band, Exception):
            raise self.band
        return self.band

    async def screencap(self, timeout=8.0):
        self.grabs.append(("full", None))
        return b"FULL"

    async def screenshot(self, path):
        self.grabs.append(("full", None))
        with open(path, "wb") as f:
            f.write(b"FULL")
        return True


def _ocr(stick, seen):
    """A recognize() stand-in that OCRs the fake stick: records the file's bytes, and its path to check deletion."""
    from types import SimpleNamespace as NS

    def box(text, y):
        return NS(text=text, confidence=1.0, x=.5, y=y, width=.05, height=.03)

    def recognize(path, timeout=8.0):
        with open(path, "rb") as f:
            seen.append((path, f.read()))
        focus = [] if stick.focus != "row" else [NS(text="Networks", x=.3, y=.9, width=.1, height=.05)]
        rows = [box("Networks", .9)]
        if stick.overlay and stick.focus == "bar":
            rows.append(box(f"{int(stick.knob // 60)}:{int(stick.knob % 60):02d}", .76))
        rows = rows if stick.overlay else []
        return NS(read_ok=True, status="ok", observations=rows, focus_boxes=focus)
    return recognize


BAND = Cfg({**CFG, "yttv_seek.overlay_band_from": 0.3})


def test_default_reader_uses_the_band_and_deletes_it(monkeypatch):
    import os
    s, seen = BandStick(label0=3600.0), []
    monkeypatch.setattr("redzone.screen_ocr.recognize", _ocr(s, seen))
    r = run(s, 60.0, presses=3, read_overlay=None, cfg=BAND)
    assert r.ok and r.committed and s.grabs and set(s.grabs) == {("band", 0.3)}, r
    assert {b for _, b in seen} == {b"BAND"} and not any(os.path.exists(p) for p, _ in seen)


def test_flag_off_never_touches_the_band(monkeypatch):
    s, seen = BandStick(label0=3600.0), []
    monkeypatch.setattr("redzone.screen_ocr.recognize", _ocr(s, seen))
    r = run(s, 60.0, presses=3, read_overlay=None)
    assert r.ok and set(s.grabs) == {("full", None)} and {b for _, b in seen} == {b"FULL"}


def test_an_empty_band_falls_back_to_the_full_frame(monkeypatch):
    s, seen = BandStick(band=b"", label0=3600.0), []
    monkeypatch.setattr("redzone.screen_ocr.recognize", _ocr(s, seen))
    r = run(s, 60.0, presses=3, read_overlay=None, cfg=BAND)
    assert r.ok and ("band", 0.3) in s.grabs and ("full", None) in s.grabs and {b for _, b in seen} == {b"FULL"}


def test_a_band_timeout_is_a_failed_read_not_a_second_capture(monkeypatch):
    """The picture stands still while the overlay is up: a second 8 s capture would add to the delay."""
    s, seen = BandStick(band=asyncio.TimeoutError(), label0=3600.0), []
    monkeypatch.setattr("redzone.screen_ocr.recognize", _ocr(s, seen))
    assert asyncio.run(ys.ocr_overlay(s, band_from=0.3)) is None
    assert s.grabs == [("band", 0.3)] and seen == []


def test_skip_and_band_together_keep_the_row_guard(monkeypatch):
    s, seen, log = BandStick(focus="row"), [], []
    monkeypatch.setattr("redzone.screen_ocr.recognize", _ocr(s, seen))
    cfg = Cfg({**SKIP, "yttv_seek.overlay_band_from": 0.3})
    r = run(s, read_overlay=None, relink=relinker(log), cfg=cfg)
    assert not r.ok and "row button" in r.reason and s.keys == [CENTER, RIGHT] and log == ["relink"]


@pytest.mark.parametrize("band,cfg,want", [(b"BAND", BAND, [("band", 0.3)]),
                                           (b"", BAND, [("band", 0.3), ("full", None)]),
                                           (b"BAND", CFG, [("full", None)])])
def test_label_probe_frames_follow_the_band_flag(band, cfg, want):
    from redzone.tuners.yttv_label import LabelProbe
    s, seen = BandStick(band=band, label0=3600.0), []
    s.overlay = True
    ov = asyncio.run(LabelProbe(cfg, recognize=_ocr(s, seen)).overlay_reader(s))
    assert ov is not None and ov.bar_focused and s.grabs == want
    assert {b for _, b in seen} == {b"BAND" if want[-1][0] == "band" else b"FULL"}
