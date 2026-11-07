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
    (frame(label=None), "no_label"),
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
