"""YTTV overlay time-label reader (scrub buffer item 2): numbers only, frame deleted at once, fail closed."""
import asyncio
import datetime as dt
from pathlib import Path
from types import SimpleNamespace

import pytest

from redzone.tuners import yttv_label as L

UTC = dt.timezone.utc
# 2026-11-07 02:30 UTC program window 2:30 AM - 3:00 AM (read in UTC for the test)
START = dt.datetime(2026, 11, 7, 2, 30, tzinfo=UTC).timestamp()


def o(text, x, y, conf=0.95, w=0.05, h=0.03):
    return L.Obs(text, conf, x, y, w, h)


def frame(label="23:36", window="2:30 AM - 3:00 AM", extra=()):
    rows = [o("Nov 7, 2:30 AM • SportsCenter", 0.07, 0.62, w=0.3), o("LIVE", 0.05, 0.62, w=0.03)]
    if window:
        rows.append(o(window, 0.81, 0.72, w=0.15))
    if label:
        rows.append(o(label, 0.60, 0.77))
    rows += [o("HD", 0.2, 0.89), o("Networks", 0.4, 0.89, w=0.08), *extra]
    return rows


def test_label_seconds_formats():
    assert L.label_seconds("23:36") == 1416
    assert L.label_seconds("1:07:12") == 4032
    assert L.label_seconds("0:05") == 5
    for bad in ("-1:07", "2:30 AM", "99:99", "LIVE", "12:3", ""):
        assert L.label_seconds(bad) is None


def test_ui_02_fixture_numbers():
    # ui_02 fixture: '23:36' in a 2:30-3:00 AM window -> playhead = start + 1416 s
    cap = START + 1416 + 30.0
    r = L.parse_label(frame(), cap, tz=UTC)
    assert r.ok and r.label == "23:36" and r.label_s == 1416
    assert r.prog_start == START and r.prog_end == START + 1800
    assert r.ui_delay_s == 30.0


def test_headline_alone_gives_start():
    r = L.parse_label(frame(window=None), START + 1416 + 25, tz=UTC)
    assert r.ok and r.prog_start == START and r.prog_end is None and r.ui_delay_s == 25.0


def test_window_crossing_midnight():
    mid = dt.datetime(2026, 11, 7, 23, 30, tzinfo=UTC).timestamp()
    rows = frame(label="45:00", window="11:30 PM - 12:30 AM")
    r = L.parse_label(rows, mid + 2700 + 20, tz=UTC)
    assert r.ok and r.prog_end - r.prog_start == 3600 and r.ui_delay_s == 20.0


def test_knob_offset_applies():
    r = L.parse_label(frame(label="23:21"), START + 1416 + 30, knob_offset_s=15, tz=UTC)
    assert r.label_s == 1416 and r.ui_delay_s == 30.0


@pytest.mark.parametrize("rows,status", [
    ([], "no_overlay"),
    (frame(label=None), "banner"),   # overlay/banner up, scrubber not focused
    (frame(extra=[o("24:10", 0.7, 0.78)]), "ambiguous"),
    (frame(label="59:00"), "out_of_window"),          # far past a 30-min window
])
def test_fails_closed(rows, status):
    r = L.parse_label(rows, START + 1416 + 30, tz=UTC)
    assert r.status == status and r.label_s is None and r.ui_delay_s is None


def test_low_confidence_and_future_playhead_fail():
    rows = frame(label=None) + [o("23:36", 0.6, 0.77, conf=0.5)]
    assert L.parse_label(rows, START + 1500, tz=UTC).status == "low_confidence"
    # playhead after the wall clock is impossible
    assert L.parse_label(frame(), START + 1300, tz=UTC).status == "out_of_window"


def test_clock_outside_label_band_is_ignored():
    # a top-right clock or the button row never counts as the scrub label
    rows = frame() + [o("9:41", 0.9, 0.03), o("10:02", 0.5, 0.9)]
    assert L.parse_label(rows, START + 1416 + 30, tz=UTC).label_s == 1416


def read(label_s, cap, start=START):
    return L.LabelRead("ok", cap, label="x", label_s=label_s, prog_start=start, prog_end=start + 1800,
                       ui_delay_s=cap - (start + label_s))


def test_behind_from_pair_matches_ui_02_table():
    live = read(1000, START + 1000 + 25)            # at the live head, 25 s ingest delay
    after = read(1000 + 78, START + 1000 + 25 + 145)  # 145 s later the label moved only 78 s
    assert L.behind_from_pair(live, after) == 67.0
    assert L.behind_from_pair(after, live) is None                    # wrong order
    assert L.behind_from_pair(live, read(10, START + 3000, start=START + 1800)) is None   # new program
    assert L.behind_from_pair(live, read(1000, START + 1000 + 25 + 900)) is None          # 900 s: misread


def test_edge_calibration():
    cal = L.EdgeCalibration()
    assert cal.behind("hdmi2", "tnt", read(1000, START + 1100)) is None   # no edge yet: fail closed
    assert cal.record_live("hdmi2", "tnt", read(1000, START + 1025))
    assert cal.behind("hdmi2", "tnt", read(1100, START + 1192)) == 67.0
    assert cal.behind("hdmi1", "tnt", read(1100, START + 1192)) is None   # per stick
    assert not cal.record_live("hdmi2", "tnt", L.LabelRead("no_label"))


class FakeOCR:
    def __init__(self, rows, status="ok"):
        self.rows, self.status, self.paths = rows, status, []

    def __call__(self, path):
        p = Path(path)
        assert p.exists() and p.read_bytes().startswith(b"\x89PNG")
        assert (p.stat().st_mode & 0o077) == 0 and (p.parent.stat().st_mode & 0o077) == 0
        self.paths.append(p)
        obs = [SimpleNamespace(text=r.text, confidence=r.confidence, x=r.x, y=r.y, width=r.width,
                               height=r.height) for r in self.rows]
        return SimpleNamespace(status=self.status, observations=obs, error="")


PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64


def run_read(ocr, png=PNG, enabled=True, offset=0.0):
    async def cap():
        return png
    return asyncio.run(L.read_label(None, enabled=enabled, recognize=ocr, screencap=cap, tz=UTC,
                                    knob_offset_s=offset))


def test_read_deletes_frame_and_returns_numbers(monkeypatch):
    import time
    monkeypatch.setattr(time, "time", lambda: START + 1416 + 30)
    ocr = FakeOCR(frame())
    r = run_read(ocr)
    assert r.ok and r.label_s == 1416 and r.ui_delay_s == 30.0 and r.frame_deleted
    assert ocr.paths and not ocr.paths[0].exists() and not ocr.paths[0].parent.exists()
    allowed = {"status", "cap_wall", "label", "label_s", "prog_start", "prog_end", "ui_delay_s",
               "confidence", "elapsed_s", "frame_deleted", "detail"}
    assert set(r.numbers()) == allowed     # no OCR text, no pixels, no paths


def test_frame_deleted_even_when_ocr_raises():
    seen = []

    def boom(path):
        seen.append(Path(path))
        raise RuntimeError("vision crashed")
    r = run_read(boom)
    assert r.status == "ocr_failed" and r.frame_deleted and not seen[0].exists()


def test_disabled_touches_nothing():
    called = []

    async def cap():
        called.append(1)
        return PNG
    r = asyncio.run(L.read_label(None, enabled=False, screencap=cap, recognize=FakeOCR([])))
    assert r.status == "disabled" and not called


def test_unsupported_and_empty_capture():
    assert run_read(FakeOCR([], status="unsupported")).status == "unsupported"
    assert run_read(FakeOCR(frame()), png=b"").status == "capture_failed"


def test_flag_ships_off():
    import yaml
    d = yaml.safe_load((Path(L.__file__).resolve().parents[1] / "default_config.yaml").read_text())
    assert d["timing"]["behind_live_s"]["label_reader"] is False
    assert L.enabled(SimpleNamespace(get=lambda k, default=None: d["timing"]["behind_live_s"]["label_reader"]
                                     if k == L.LABEL_READER_FLAG else default)) is False


def test_label_above_the_preview_card_reads():
    """Live, Nov 6: with the scrub preview up the label sits at y 0.444, above the card (the wall clock is at y 0.095)."""
    rows = [o("11:04 PM", 0.881, 0.095, w=0.068), o("34:05", 0.214, 0.444, w=0.044), o("88 Multiview", 0.462, 0.889, w=0.1)]
    r = L.parse_label(rows, 1794024000.0)
    assert r.ok and r.label_s == 2045


def test_post_tune_banner_is_its_own_status():
    """Live Nov 6 (#377): a LIVE/title banner with CC but no scrub label -> 'banner', no numbers."""
    rows = [o("LIVE", 0.05, 0.62, w=0.03), o("Kansas at MTSU", 0.07, 0.66, w=0.2), o("CC", 0.3, 0.89),
            o("11:04 PM", 0.881, 0.095, w=0.068)]
    r = L.parse_label(rows, 1794024000.0)
    assert r.status == "banner" and r.label_s is None
    assert L.parse_label([o("Kansas at MTSU", 0.07, 0.66, w=0.2)], 1794024000.0).status == "no_overlay"


def test_pair_with_no_program_window_and_left_probe():
    """No window on screen live: behind_from_pair still works; each read taken one LEFT back (offset 15)."""
    t0 = 1794024000.0
    live = L.parse_label([o("34:05", 0.214, 0.444)], t0, knob_offset_s=15)           # at the live head
    assert live.ok and live.prog_start is None and live.ui_delay_s is None and live.label_s == 2060
    # 4 LEFTs committed (60 s back), 100 s later the probe shows the playhead -15 s
    later = L.parse_label([o("34:50", 0.214, 0.444)], t0 + 100, knob_offset_s=15)
    assert L.behind_from_pair(live, later) == 55.0


def test_cli_pair_roundtrip(tmp_path, monkeypatch):
    t = {"now": 1794024000.0}
    rows = {"label": "34:05"}

    async def fake_read(stick, **kw):
        return L.parse_label([o(rows["label"], 0.214, 0.444)], t["now"], knob_offset_s=kw["knob_offset_s"])
    monkeypatch.setattr(L, "read_label", fake_read)
    import redzone.tuners.adb as adb
    monkeypatch.setattr(adb, "Stick", lambda host: None)
    saved = tmp_path / "live.json"
    assert L.main(["--stick", "x", "--knob-offset", "15", "--save", str(saved)]) == 0
    t["now"] += 100
    rows["label"] = "34:50"
    import io, contextlib, json
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert L.main(["--stick", "x", "--knob-offset", "15", "--since", str(saved)]) == 0
    assert json.loads(buf.getvalue())["behind_live_s"] == 55.0


# ------------------------------------------------------------------ LabelProbe: banner handling + one read
class FakeStick:
    """A hidden YTTV stick: CENTER toggles the overlay; a banner eats the first `banner` opens; LEFT/RIGHT move
    the knob only with the bar up; CENTER with a displaced knob commits it. The picture STANDS STILL while the
    overlay is up (scrub thread, Nov 7). Labels = seconds since program start."""

    def __init__(self, t0=1794024000.0, live_label=2000.0, banner=0, row_focus=False):
        self._t = t0
        self.live0, self.t0 = live_label, t0
        self.pos = live_label      # playhead label (seconds since program start)
        self.knob = 0              # knob offset in steps (negative = back)
        self.overlay = False
        self.bar = False
        self.banner = banner
        self.row_focus = row_focus
        self.host = "hidden"
        self.keys = []

    @property
    def t(self):
        return self._t

    @t.setter
    def t(self, v):
        if not self.overlay:
            self.pos += v - self._t     # plays at 1x only while the overlay is down
        self._t = v

    @property
    def behind(self):
        return self.live_label() - self.pos

    def live_label(self):
        return self.live0 + (self.t - self.t0)

    async def shell(self, cmd, timeout=4):
        code = cmd.split("KEYCODE_")[1]
        self.keys.append(code)
        self.t += 0.5
        if code == "DPAD_CENTER":
            if self.overlay:
                if self.bar and self.knob:
                    self.pos += self.knob * 15
                self.overlay, self.bar, self.knob = False, False, 0
            else:
                self.overlay = True
                if self.banner:
                    self.banner -= 1
                    self.overlay = "banner"
        elif code in ("DPAD_LEFT", "DPAD_RIGHT") and self.overlay is True and not self.row_focus:
            self.bar = True
            self.knob += -1 if code == "DPAD_LEFT" else 1
        return SimpleNamespace(ok=True)

    async def screencap(self):
        self.t += 1.0
        return PNG

    def rows(self):
        if not self.overlay:
            return []
        rows = [o("LIVE", 0.05, 0.62, w=0.03), o("CC", 0.3, 0.89)]
        if self.overlay is True and self.bar:
            lab = self.pos + self.knob * 15
            m, s_ = divmod(int(round(lab)), 60)
            rows.append(o(f"{m}:{s_:02d}", 0.214, 0.444))
        return rows


def probe_for(stick):
    def rec(path):
        obs = [SimpleNamespace(text=r.text, confidence=r.confidence, x=r.x, y=r.y, width=r.width, height=r.height)
               for r in stick.rows()]
        focus = [SimpleNamespace(text="CC", x=0.3, y=0.89, width=0.05, height=0.03)] if stick.row_focus else []
        return SimpleNamespace(status="ok" if obs else "empty", observations=obs, focus_boxes=focus, error="")

    async def nosleep(s):
        stick.t += s
    return L.LabelProbe(None, recognize=rec, sleep=nosleep)


def test_probe_measures_once_after_entry(monkeypatch):
    st = FakeStick()
    monkeypatch.setattr(L.time, "time", lambda: st.t)
    p = probe_for(st)

    async def go():
        p.arm(st)
        # enter_buffer's probe at the live edge, through the shared reader: CENTER, LEFT, read -> anchor
        await st.shell("input keyevent KEYCODE_DPAD_CENTER")
        await st.shell("input keyevent KEYCODE_DPAD_LEFT")
        ov = await p.overlay_reader(st)
        assert ov.bar_focused and "hidden" in p.anchors
        for _ in range(3):
            await st.shell("input keyevent KEYCODE_DPAD_LEFT")
        await st.shell("input keyevent KEYCODE_DPAD_CENTER")       # commit: 60 s back
        st.t += 30
        return await p.probe(st)
    r = asyncio.run(go())
    # measured at the screencap + the frozen seconds until the commit = where the stick really is now
    assert r.reason == "measured" and r.behind_live_s == pytest.approx(st.behind, abs=1.0)
    assert st.behind > 60.0 and st.keys[-2:] == ["DPAD_RIGHT", "DPAD_CENTER"]
    assert p.playhead(st) == r.behind_live_s


def test_probe_handles_the_post_tune_banner(monkeypatch):
    st = FakeStick()
    monkeypatch.setattr(L.time, "time", lambda: st.t)
    p = probe_for(st)
    p.anchors["hidden"] = L.parse_label([o("33:05", 0.214, 0.444)], st.t, knob_offset_s=15)   # 2000 s at live
    st.pos -= 60.0
    st.banner = 2                            # two opens land on the banner first
    r = asyncio.run(p.probe(st))
    assert r.reason == "measured" and r.behind_live_s == pytest.approx(st.behind, abs=1.0)
    assert st.behind > 60.0                  # every second with the overlay up added a second
    assert r.keys.count("DPAD_LEFT") >= 2    # probed, closed, reopened


def test_probe_fails_closed():
    st = FakeStick()
    p = probe_for(st)
    assert asyncio.run(p.probe(st)).reason.startswith("no at-live anchor")
    p.anchors["hidden"] = L.parse_label([o("33:05", 0.214, 0.444)], st.t, knob_offset_s=15)
    st.banner = 99
    r = asyncio.run(p.probe(st))
    assert r.behind_live_s is None and r.read.status == "banner" and "DPAD_RIGHT" not in r.keys
    st2 = FakeStick(row_focus=True)
    p2 = probe_for(st2)
    p2.anchors["hidden"] = p.anchors["hidden"]
    r2 = asyncio.run(p2.probe(st2))
    assert r2.behind_live_s is None and "row button" in r2.reason
    assert asyncio.run(p.probe(st, still_hidden=lambda: False)).keys == []


def test_arm_refuses_a_stick_marked_behind():
    """#391: a same-channel deep link does not reset a stick that is behind live; never anchor on it."""
    p = L.LabelProbe()
    st = SimpleNamespace(host="hidden", behind_on="youtubetv:espn")
    assert p.arm(st) is False and "hidden" not in p.armed
    st.behind_on = None
    assert p.arm(st) is True and p.armed["hidden"] == 0.0
