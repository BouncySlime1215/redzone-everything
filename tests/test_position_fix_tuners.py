"""[POSITION-FIX] The Nov 10 timing findings in the stick path, each behind its own flag (off = today's keys and numbers).

Measured cases are fixtures here as numbers only (research/position FINDINGS 5a-5f, 9): the label is the whole-second
floor of the frozen frame, one LEFT commits exactly 15.000 s back, RIGHT at the live edge does not undo a LEFT, the
media session read without the package returns another app's session, the per-stick key delays.
"""
import asyncio
from types import SimpleNamespace

import pytest

from redzone.tuners import yttv_label as L
from redzone.tuners import yttv_nerds as N
from redzone.tuners import yttv_seek as ys
from redzone.tuners import yttv_timing as T
from redzone.tuners.adb import parse_playback
from tests.test_yttv_label import PNG, FakeStick, o, probe_for
from tests.test_yttv_seek import CFG, LEFT_PROBE, YT, Cfg, FakeYTTV, reader, run

OTHER = "com.other.app"


def cfg_with(**flags):
    return Cfg({**CFG, **{f"timing.{k}": v for k, v in flags.items()}})


def session_text(uptime_s, blocks):
    """EDGE_READ output: /proc/uptime, then one media session block per (pkg, state code, position ms, updated ms)."""
    lines = [f"{uptime_s:.3f} 1.000"]
    for pkg, state, pos_ms, upd_ms in blocks:
        lines += [f"  package={pkg}", "    active=true",
                  f"    state=PlaybackState {{state={state}, position={pos_ms}, buffered position=0, speed=1.0, "
                  f"updated={upd_ms}, actions=0}}"]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ item 2: timing.playback_with_package
def test_a_paused_yttv_session_is_invisible_without_the_package():
    """FINDINGS 2.1 / 5a: another app's idle session wins when YouTube TV is paused and no package is passed."""
    text = session_text(1000.0, [(YT, 2, 46_853_896, 990_000), (OTHER, 3, 5_000, 500_000)])
    assert parse_playback(text)["pkg"] == OTHER                      # today: the wrong session
    assert parse_playback(text, YT)["state"] == "paused"            # with the package: the right one


def test_package_helper_is_off_by_default_and_yttv_only():
    st = SimpleNamespace(packages={"youtubetv": YT, "prime": "prime.pkg"})
    assert T.yttv_package(st, "youtubetv:espn", CFG) is None
    on = cfg_with(playback_with_package=True)
    assert T.yttv_package(st, "youtubetv:espn", on) == YT
    assert T.yttv_package(st, None, on) == YT                       # the YouTube TV path's own reads
    assert T.yttv_package(st, "prime:tnf", on) is None              # other apps: as before
    assert T.yttv_package(st, "", on) is None                       # no target known: as before
    assert T.yttv_package(st, "youtubetv:espn", on, "given.pkg") == "given.pkg"


class PkgYTTV(FakeYTTV):
    """enter_buffer's own playback reads, recording the package each passes."""
    def __init__(self, **kw):
        super().__init__(**kw)
        self.packages, self.asked = {"youtubetv": YT}, []

    async def playback(self, package=None):
        self.asked.append(package)
        return await super().playback(package)


def test_enter_buffer_passes_the_package_only_with_the_flag():
    s = PkgYTTV(label0=3600.0)
    asyncio.run(ys.enter_buffer(s, 60.0, cfg=CFG, pkg=None, read_overlay=reader(s)))
    assert s.asked and set(s.asked) == {None}
    s2 = PkgYTTV(label0=3600.0)
    asyncio.run(ys.enter_buffer(s2, 60.0, cfg=cfg_with(playback_with_package=True), pkg=None, read_overlay=reader(s2)))
    assert s2.asked and set(s2.asked) == {YT}
    assert s.keys == s2.keys                                         # the same keys either way


def test_sync_probe_reads_the_yttv_session_with_the_flag(cfg):
    from redzone import sync_probe as sp
    text = session_text(1000.0, [(YT, 2, 46_853_896, 990_000), (OTHER, 3, 5_000, 500_000)])

    class St:
        host, packages = "h1", {"youtubetv": YT}

        async def run(self, *a, timeout=6.0):
            return SimpleNamespace(ok=True, out=text)

    st = St()
    control = SimpleNamespace(live_control=True, _busy=lambda: False, _flipping=False, input_unknown=False,
                              main=st, on_target="youtubetv:espn", two_stick=False)
    cfg.update({"mode": {"dry_run": False}})
    rows_off = asyncio.run(sp.SyncProbe(cfg, control).tick(now=1.0))
    cfg.update({"timing": {"playback_with_package": True}})
    rows_on = asyncio.run(sp.SyncProbe(cfg, control).tick(now=1.0))
    assert rows_off[0]["ms"]["pkg"] == OTHER and rows_on[0]["ms"]["pkg"] == YT


# ------------------------------------------------------------------ item 1: timing.label_stamp_at_pause
def test_session_read_stamps_the_pause_event():
    """The stick stamps each state change on its boot clock: event wall = read wall - (uptime - updated)."""
    text = session_text(1000.0, [(YT, 2, 46_853_896, 995_300)])
    r = T.session_from_text(text, wall=5000.0, pkg=YT)
    assert r.state == "paused" and r.position_s == pytest.approx(46853.896)
    assert r.event_wall == pytest.approx(5000.0 - 4.7) and r.pos_now_s == pytest.approx(46853.896)
    st = T.pause_stamp(r)
    assert st == T.PauseStamp(pytest.approx(4995.3), pytest.approx(46853.896))
    playing = T.session_from_text(session_text(1000.0, [(YT, 3, 46_800_000, 990_000)]), wall=5000.0, pkg=YT)
    assert T.pause_stamp(playing) is None and playing.pos_now_s == pytest.approx(46810.0)


def test_tune_clock_holds_through_a_pause_and_a_seek():
    """FINDINGS 5b: one tune, the origin pinned; after a 30 s pause the label moved by the pause inside the band."""
    clock = T.TuneClock.from_label(prog_start=1_000_000.0, label_s=2000.0, stamp=T.PauseStamp(1_002_025.0, 46_800.0))
    assert clock.ui_delay(1_002_025.0, 46_800.0) == pytest.approx(25.0)
    assert clock.ui_delay(1_002_055.0, 46_800.0) == pytest.approx(55.0)      # paused 30 s: 30 s more behind
    assert clock.ui_delay(1_002_100.0, 46_830.0) == pytest.approx(70.0)      # then a 15 s rewind


class SessionStick(FakeStick):
    """FakeStick (overlay up = picture frozen) with a media session: position = the playhead on the tune's counter,
    stamped on the stick's boot clock at each pause/play. Capture times can be uneven, as on the TV."""
    POS0 = 46_000.0

    def __init__(self, caps=(), right_at_live_jumps=False, window=False, **kw):
        if window:
            kw.setdefault("live_label", 2040.0)    # program start (t0 - 2040) on a whole minute
        super().__init__(**kw)
        self.window = None
        if window:
            import datetime as dt
            a = dt.datetime.fromtimestamp(self.t0 - self.live0)
            b = a + dt.timedelta(hours=2)
            self.window = f"{a.strftime('%I:%M %p').lstrip('0')} - {b.strftime('%I:%M %p').lstrip('0')}"
        self.boot = self.t0 - 10_000.0
        self.ev_t, self.ev_pos = self.t, self.pos
        self.packages = {"youtubetv": YT}
        self.caps = list(caps)
        self.right_at_live_jumps = right_at_live_jumps
        self.jump = False

    async def shell(self, cmd, timeout=4):
        if cmd == T.EDGE_READ:
            st = 2 if self.overlay else 3
            return SimpleNamespace(ok=True, out=session_text(self.t - self.boot, [
                (OTHER, 1, 5_000, 1_000), (YT, st, int(round((self.ev_pos + self.POS0) * 1000)),
                                          int(round((self.ev_t - self.boot) * 1000)))]))
        was = bool(self.overlay)
        code = cmd.split("KEYCODE_")[1]
        at_live = self.behind < 7.5
        if code == "DPAD_RIGHT" and self.right_at_live_jumps and at_live and self.overlay is True:
            self.keys.append(code)
            self.t += 0.5
            self.jump = True                  # FINDINGS 5a: RIGHT does not undo the LEFT; the commit goes to live
            return SimpleNamespace(ok=True)
        if code == "DPAD_CENTER" and self.overlay is True and self.jump:
            self.keys.append(code)
            self.t += 0.5
            self.overlay, self.bar, self.knob, self.jump = False, False, 0, False
            self.pos = self.live_label()
            self.ev_t, self.ev_pos = self.t, self.pos
            return SimpleNamespace(ok=True)
        r = await super().shell(cmd, timeout)
        if bool(self.overlay) != was:
            self.ev_t, self.ev_pos = self.t, self.pos
        return r

    async def screencap(self):
        self.t += self.caps.pop(0) if self.caps else 1.0
        return PNG

    def rows(self):
        rows = super().rows()
        if rows and self.window:
            rows.append(o(self.window, 0.81, 0.72, w=0.15))
        return rows


def stamped_probe(st, **flags):
    p = probe_for(st)
    p.cfg = Cfg({f"timing.{k}": v for k, v in flags.items()})
    return p


def anchor_then_scrub(st, p, lefts=3):
    async def go():
        p.arm(st)
        await st.shell("input keyevent KEYCODE_DPAD_CENTER")
        await st.shell("input keyevent KEYCODE_DPAD_LEFT")
        ov = await p.overlay_reader(st)
        assert ov.bar_focused
        for _ in range(lefts):
            await st.shell("input keyevent KEYCODE_DPAD_LEFT")
        await st.shell("input keyevent KEYCODE_DPAD_CENTER")
        st.t += 30
        return await p.probe(st)
    return asyncio.run(go())


def test_flag_off_probe_is_unchanged_and_off_by_the_screenshot_stamp(monkeypatch):
    """A slow anchor screencap (5.5 s, as on the TV) puts today's pair off by seconds; the keys are today's."""
    st = SessionStick(caps=[5.5])
    monkeypatch.setattr(L.time, "time", lambda: st.t)
    p = stamped_probe(st)
    r = anchor_then_scrub(st, p)
    assert r.reason == "measured" and r.keys[-2:] == ["DPAD_RIGHT", "DPAD_CENTER"]
    assert abs(r.behind_live_s - st.behind) > 2.0                    # the screenshot stamp: wrong by a few seconds
    assert p.anchor_stamps == {} and p.clocks == {}


def test_label_stamped_at_the_pause_is_exact(monkeypatch):
    st = SessionStick(caps=[5.5], window=True)
    monkeypatch.setattr(L.time, "time", lambda: st.t)
    p = stamped_probe(st, label_stamp_at_pause=True)
    offsets = []
    p.on_tune_offset = lambda k, u, b, sd, wall: offsets.append((k, u, b, sd))
    r = anchor_then_scrub(st, p)
    assert r.reason.startswith("measured (pause stamp")
    assert r.behind_live_s == pytest.approx(st.behind, abs=0.05)     # the session meter: 0.02 s on the TV
    assert r.keys[-2:] == ["DPAD_RIGHT", "DPAD_CENTER"]              # the same keys as flag off
    assert p.clocks["hidden"] and offsets and offsets[0][0] == "hidden" and offsets[0][3] == T.LABEL_SD_S
    assert offsets[0][2] == pytest.approx(st.behind, abs=0.05)
    # U: this tune's lag behind (program start + label) at its live edge. The fake lands on the live edge (0 s);
    # the label is a whole-second step, so U is known to that step
    assert abs(offsets[0][1]) <= 1.0


def test_pause_stamp_fails_soft_to_todays_math_without_a_session(monkeypatch):
    class NoSession(SessionStick):
        async def shell(self, cmd, timeout=4):
            if cmd == T.EDGE_READ:
                return SimpleNamespace(ok=False, out="")
            return await super().shell(cmd, timeout)
    st = NoSession()
    monkeypatch.setattr(L.time, "time", lambda: st.t)
    r = anchor_then_scrub(st, stamped_probe(st, label_stamp_at_pause=True))
    assert r.reason == "measured" and r.behind_live_s == pytest.approx(st.behind, abs=1.0)


# ------------------------------------------------------------------ item 3: timing.no_right_probe_at_live
def at_live_probe(st, **flags):
    p = stamped_probe(st, **flags)
    p.anchors["hidden"] = L.parse_label([o("33:05", 0.214, 0.444)], st.t, knob_offset_s=15)   # 2000 s at live
    return p


def test_right_at_the_live_edge_jumps_to_live_and_today_books_it_wrong(monkeypatch):
    st = SessionStick(right_at_live_jumps=True)
    monkeypatch.setattr(L.time, "time", lambda: st.t)
    r = asyncio.run(at_live_probe(st).probe(st, behind_s=0.0))
    assert "DPAD_RIGHT" in r.keys and st.behind < 1.0                # the commit went to about live
    assert r.behind_live_s - st.behind > 3.0                         # booked as if the RIGHT had undone the LEFT


def test_no_right_at_live_commits_the_left_and_books_15_000(monkeypatch):
    st = SessionStick(right_at_live_jumps=True)
    monkeypatch.setattr(L.time, "time", lambda: st.t)
    r = asyncio.run(at_live_probe(st, no_right_probe_at_live=True).probe(st, behind_s=0.0))
    assert "DPAD_RIGHT" not in r.keys and r.keys[-1] == "DPAD_CENTER"
    assert st.behind > 15.0 and r.behind_live_s == pytest.approx(st.behind, abs=1.0)
    # behind live (no behind_s, as after a scrub): the RIGHT stays, as today
    st2 = SessionStick()
    monkeypatch.setattr(L.time, "time", lambda: st2.t)
    p2 = at_live_probe(st2, no_right_probe_at_live=True)
    st2.pos -= 60.0
    assert "DPAD_RIGHT" in asyncio.run(p2.probe(st2)).keys


def test_no_right_and_pause_stamp_together_are_exact(monkeypatch):
    st = SessionStick(right_at_live_jumps=True)
    monkeypatch.setattr(L.time, "time", lambda: st.t)
    p = at_live_probe(st, no_right_probe_at_live=True, label_stamp_at_pause=True)
    r = asyncio.run(p.probe(st, behind_s=0.0))
    # no anchor stamp here (a hand-set anchor): the pair is used, good to the label's whole-second step
    assert r.behind_live_s == pytest.approx(st.behind, abs=1.0) and "pause stamp" in r.reason


def test_enter_buffer_left_probe_at_live_is_booked_as_one_step():
    """Nov 10: at the live edge one LEFT moves the knob a full step (69 of 69). Flag off, the LEFT probe books 0 s for
    it (the Nov 7 reading) and the estimate is a step short; on, the LEFT is the probe and is booked as 15.000 s."""
    s = FakeYTTV(label0=3600.0)
    s.reveal_only = False
    r = run(s, 60.0, cfg=LEFT_PROBE)
    assert r.ok and r.behind_live_s == pytest.approx(s.committed_back - 15.0, abs=0.5)
    s2 = FakeYTTV(label0=3600.0)
    s2.reveal_only = False
    r2 = run(s2, 60.0, cfg=cfg_with(no_right_probe_at_live=True))   # the default RIGHT probe is dropped at live
    assert r2.ok and "KEYCODE_DPAD_RIGHT" not in s2.keys
    assert r2.behind_live_s == pytest.approx(s2.committed_back, abs=0.5)
    assert any("booked as 15.000s" in x for x in r2.steps)


def test_enter_buffer_flag_off_keys_are_todays():
    s, s2 = FakeYTTV(label0=3600.0), FakeYTTV(label0=3600.0)
    run(s, 60.0, presses=3)
    run(s2, 60.0, presses=3, cfg=cfg_with(no_right_probe_at_live=False, playback_with_package=False))
    assert s.keys == s2.keys and s.keys[1] == "KEYCODE_DPAD_RIGHT"
    s3 = FakeYTTV(label0=3600.0)                                     # behind live: unchanged with the flag on
    s3.reveal_only = False
    s4 = FakeYTTV(label0=3600.0)
    s4.reveal_only = False
    a = run(s3, 60.0, behind_before_s=25.0)
    b = run(s4, 60.0, behind_before_s=25.0, cfg=cfg_with(no_right_probe_at_live=True))
    assert s3.keys == s4.keys and a.behind_live_s == pytest.approx(b.behind_live_s, abs=0.01)


# ------------------------------------------------------------------ item 6: timing.key_constants
def test_key_constants_per_stick():
    st23 = SimpleNamespace(name="Fire TV Stick 4K (2023)", model="AFTKM")
    plus = SimpleNamespace(name="Fire TV Stick 4K Plus", model="AFTMA08C15")
    assert T.key_constants(CFG, st23) is None                        # off
    on = cfg_with(key_constants=True)
    assert T.key_constants(on, st23) == T.KeyConstants(0.70, 0.69, 0.13)
    assert T.key_constants(on, plus) == T.KeyConstants(0.60, 0.53, 0.08)
    assert T.key_constants(on, SimpleNamespace(name="Some stick", model="X")) is None
    table = cfg_with(key_constants=True, key_constants_by_stick={"AFTKM": {"pause_s": 0.5, "resume_s": 0.4}})
    assert T.key_constants(table, st23).pause_s == 0.5                  # the config wins, by model code too
    # FINDINGS 5e: keys 30 s apart on the 2023 stick: frozen 30 - 0.70 + 0.69 s
    assert T.paused_span(100.0, 130.0, T.key_constants(on, st23)) == pytest.approx(29.99)


def test_hold_fallback_uses_the_key_constants(monkeypatch):
    from redzone import control as ctl
    c = ctl.Controller.__new__(ctl.Controller)
    c.cfg = Cfg({"timing.behind_live_s.hold_overreport_s": 0.0})
    st = SimpleNamespace(name="Fire TV Stick 4K Plus", model="", tuned=None)
    assert c._held_for(st, "youtubetv:fox", {"state": "playing"}, 61.0, keys=(100.0, 160.0)) == 61.0   # off: the span
    c.cfg = Cfg({"timing.behind_live_s.hold_overreport_s": 0.0, "timing.key_constants": True})
    assert c._held_for(st, "youtubetv:fox", {"state": "playing"}, 61.0, keys=(100.0, 160.0)) == 59.9
    assert c._held_for(st, "youtubetv:fox", {"state": "playing"}, 61.0) == 61.0   # no key times: the span


# ------------------------------------------------------------------ item 5: timing.stats_for_nerds
PANEL = ["Video ID / sCPN  FAKEID000000 / FAKE", "Viewport / Frames 1920x1080 / 0 dropped of 900",
         "Buffer Health 19.87 s", "Live Latency 24.31 s", "Live Mode seq 9312", "Mystery Text t:46853.90 b:19.87",
         "Date Tue Nov 10 2026"]


def test_parse_nerds_masks_the_id_line_and_keeps_numbers():
    r = N.parse_nerds(PANEL, wall=5.0)
    assert r.ok and r.live_latency_s == 24.31 and r.buffer_s == 19.87 and r.position_s == 46853.90
    assert "FAKEID" not in repr(r) and all("CPN" not in x and "Video ID" not in x for x in N.masked(PANEL))
    assert N.parse_nerds([x.replace("24.31", "0.00") for x in PANEL]).status == "zero"
    assert N.parse_nerds(["CC", "HD"]).status == "no_panel"


class NerdStick:
    """The panel: the 14-key toggle switches it; Live Latency shows only from 11 s after the landing."""
    def __init__(self, landed, ll_from=11.0, ll=24.31):
        self.now, self.landed, self.ll_from, self.ll = landed, landed, ll_from, ll
        self.panel, self.cmds, self.ok = False, [], True

    async def shell(self, cmd, timeout=6):
        self.cmds.append(cmd)
        self.now += 1.2
        if cmd == "input keyevent " + " ".join(f"KEYCODE_{c}" for c in N.TOGGLE_KEYS):
            self.panel = not self.panel
        return SimpleNamespace(ok=self.ok)

    async def screencap(self):
        self.now += 1.4
        return b"png"

    def rec(self, path):
        lat = self.ll if self.now - self.landed >= self.ll_from else 0.0
        rows = [x.replace("24.31", f"{lat:.2f}") for x in PANEL] if self.panel else ["CC"]
        obs = [SimpleNamespace(text=t, confidence=0.9, x=0.1, y=0.1, width=0.1, height=0.02) for t in rows]
        return SimpleNamespace(status="ok", observations=obs, focus_boxes=[], error="")


def nerds(st, cfg, **kw):
    async def sl(s):
        st.now += s
    return asyncio.run(N.read_live_latency(st, cfg=cfg, landed_wall=st.landed, recognize=st.rec, sleep=sl,
                                           clock=lambda: st.now, **kw))


def test_live_latency_is_read_once_and_the_panel_is_switched_off():
    st = NerdStick(landed=1000.0)
    r = nerds(st, Cfg({N.FLAG: True}))
    assert r.reason == "measured" and r.u_s == round(24.31 - N.LL_BIAS_S, 2) and r.sd_s == 0.19
    assert len(st.cmds) == 3 and st.cmds[0] == st.cmds[1] and st.cmds[2].endswith("KEYCODE_BACK")
    assert len(N.TOGGLE_KEYS) == 14 and not st.panel and r.panel_off and not N.panel_on(st)


def test_live_latency_refusals_press_nothing():
    st = NerdStick(landed=1000.0)
    assert nerds(st, Cfg({})).reason == "disabled" and st.cmds == []
    assert nerds(st, Cfg({N.FLAG: True}), behind_s=60.0).reason.startswith("not at the live edge") and st.cmds == []
    st.now = 1030.0
    assert nerds(st, Cfg({N.FLAG: True})).reason.startswith("too long after") and st.cmds == []
    st.now = 1000.0
    assert nerds(st, Cfg({N.FLAG: True}), still_hidden=lambda: False).reason == "stick is not hidden" and st.cmds == []


def test_no_value_in_the_window_still_switches_off():
    st = NerdStick(landed=1000.0, ll_from=99.0)
    r = nerds(st, Cfg({N.FLAG: True}))
    assert r.u_s is None and r.reason.startswith("no Live Latency") and len(r.reads) == 3 and not st.panel


def test_visible_stick_is_never_pressed_to_switch_off_and_blocks_the_label():
    st = NerdStick(landed=1000.0)
    seen = []

    def hidden():
        seen.append(1)
        return len(seen) < 2
    r = nerds(st, Cfg({N.FLAG: True}), still_hidden=hidden)
    assert r.reason == "stick became visible" and len(st.cmds) == 1 and N.panel_on(st)
    # with the panel marked, the label probe refuses before any key (the panel covers the label: 3 of 3 failed)
    fs = SessionStick()
    fs.nerds_panel_on = True
    p = stamped_probe(fs, stats_for_nerds=True)
    p.anchors["hidden"] = L.parse_label([o("33:05", 0.214, 0.444)], fs.t, knob_offset_s=15)
    pr = asyncio.run(p.probe(fs))
    assert pr.keys == [] and "stats for nerds" in pr.reason


def test_flip_refuses_a_hidden_stick_with_the_panel_up():
    from redzone import control as ctl

    class C(ctl.Controller):
        hidden = None
        two_stick = True

        def __init__(self, cfg):
            self.cfg = cfg

        def _live_on_hidden(self, target):
            return True
    c = C(Cfg({}))
    c.hidden = SimpleNamespace(nerds_panel_on=True)
    assert c.standby_ready("youtubetv:espn")                         # flag off: as today
    c.cfg = Cfg({"timing.stats_for_nerds": True})
    assert not c.standby_ready("youtubetv:espn")
    c.hidden.nerds_panel_on = False
    assert c.standby_ready("youtubetv:espn")
