"""[YTTV scrub buffer, item 2] Read YouTube TV's overlay time label -> numbers only.

Spec: docs/sync/2026-11-06-yttv-scrub-buffer-spec.md. After an overlay scrub, media_session position is
wrong, so the truth meter is the player overlay's time label (the 'MM:SS' / 'H:MM:SS' above the scrubber
knob = playhead seconds since the EPG program start) against the wall clock.

What this module does, and only this:
  1. One `screencap` of the stick (read only: no key is pressed here; the overlay must already be up with
     the scrubber focused, which is yttv_seek's job behind its focus guard).
  2. The PNG goes into a private temp dir (0700), local Apple Vision OCR reads it, and the file is
     deleted in a `finally` right after, success or not. Nothing is uploaded, nothing is kept.
     YouTube TV's video layer is DRM-black in a screencap; only the UI layer is read. No DRM bypass.
  3. Only numbers leave: label seconds, program window start/end (epoch), capture wall time, and the
     derived delays. No OCR text other than the matched time label string, never pixels.

Fail closed: anything ambiguous (no overlay, two time labels, label outside the program window, low
OCR confidence, OCR unavailable off macOS) returns a LabelRead with status != "ok" and no numbers.

Two ways to turn a label into behind-live seconds:
  * pair (no calibration): a read at the live head and a read after the seek, same program:
        behind = (wall_after - wall_live) - (label_after - label_live)
  * edge calibration: ui_delay = cap_wall - (prog_start + label) is the picture's delay vs YTTV's
    schedule clock; behind = ui_delay - ui_delay_at_live_head(stick, channel).

Flag: timing.behind_live_s.label_reader (default false). With it off, `read_label` returns
status "disabled" without touching the stick. The CLI below is a manual, read-only probe.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import re
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Awaitable, Callable, Iterable

LABEL_READER_FLAG = "timing.behind_live_s.label_reader"

# Overlay geometry (normalized, top-left; 1920x1080 screencaps Nov 5, ESPN, both sticks; ui_common.py):
# bar y ~0.815, x ~0.05..0.95; label sits just above the knob; window '2:30 AM - 3:00 AM' at (0.81, 0.72).
LABEL_Y = (0.60, 0.84)
LABEL_X = (0.03, 0.97)
MIN_CONF = 0.80
WINDOW_SLACK_S = 120          # a label may run a little past the listed end (overruns), never far
MAX_BEHIND_S = 600            # a pair/edge result outside [-5, this] is a misread, not a buffer

_WIN = re.compile(r"(\d{1,2}:\d{2})\s*([AaPp][Mm])\s*[-–—~]\s*(\d{1,2}:\d{2})\s*([AaPp][Mm])")
_HEAD = re.compile(r"\b([A-Z][a-z]{2})\s+(\d{1,2}),\s*(\d{1,2}:\d{2})\s*([AP]M)\b")
_LABEL = re.compile(r"^\s*(?:(\d{1,2}):)?([0-5]?\d):([0-5]\d)\s*$")


@dataclass(frozen=True)
class Obs:
    """One OCR line (normalized top-left box). Accepts screen_ocr.TextBox fields."""
    text: str
    confidence: float
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class LabelRead:
    status: str                       # ok | disabled | unsupported | capture_failed | ocr_failed |
                                      # no_overlay | no_label | ambiguous | low_confidence | out_of_window
    cap_wall: float = 0.0             # epoch seconds, midpoint of the screencap
    label: str = ""                   # the matched time label only, e.g. '1:07:12'
    label_s: float | None = None      # playhead seconds since program start (knob offset applied)
    prog_start: float | None = None   # epoch of the EPG window start (None if the window line was not read)
    prog_end: float | None = None
    ui_delay_s: float | None = None   # cap_wall - (prog_start + label_s): picture delay vs YTTV's schedule
    confidence: float = 0.0
    elapsed_s: float = 0.0
    frame_deleted: bool = True        # the screencap file is gone (always True unless deletion failed)
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok" and self.label_s is not None

    def numbers(self) -> dict:
        """What may be logged: numbers, the label string and the status. Nothing else."""
        return asdict(self)


def enabled(cfg) -> bool:
    try:
        return bool(cfg.get(LABEL_READER_FLAG, False))
    except Exception:
        return False


# ----------------------------------------------------------------------------------------- parsing
def label_seconds(text: str) -> float | None:
    m = _LABEL.match(text)
    if not m:
        return None
    h, mm, ss = m.groups()
    return (int(h) * 3600 if h else 0) + int(mm) * 60 + int(ss)


def _clock(hm: str, ampm: str, near: float, tz: dt.tzinfo | None) -> float:
    h, m = map(int, hm.split(":"))
    if not (1 <= h <= 12 and 0 <= m <= 59):
        raise ValueError("bad clock")
    h = h % 12 + (12 if ampm.upper() == "PM" else 0)
    base = dt.datetime.fromtimestamp(near, tz)
    cands = [(base + dt.timedelta(days=d)).replace(hour=h, minute=m, second=0, microsecond=0).timestamp()
             for d in (-1, 0, 1)]
    return min(cands, key=lambda e: abs(e - near))


def program_window(obs: Iterable[Obs], cap_wall: float, tz: dt.tzinfo | None = None
                   ) -> tuple[float | None, float | None]:
    """(start, end) epochs from the '2:30 AM - 3:00 AM' line; start only from the 'Nov 5, 2:30 AM' headline."""
    start = end = None
    head = None
    for o in obs:
        if o.y <= 0.2:
            continue
        m = _WIN.search(o.text)
        if m:
            try:
                a = _clock(m.group(1), m.group(2), cap_wall, tz)
                b = _clock(m.group(3), m.group(4), a + 3600, tz)
            except ValueError:
                continue
            if b <= a:
                b += 86400
            if start is not None and (start, end) != (a, b):
                return None, None          # two different windows on screen: do not guess
            start, end = a, b
            continue
        mh = _HEAD.search(o.text)
        if mh and o.y > 0.4 and head is None:
            try:
                head = _clock(mh.group(3), mh.group(4), cap_wall, tz)
            except ValueError:
                pass
    if start is None and head is not None:
        start = head
    return start, end


def parse_label(obs: Iterable[Obs], cap_wall: float, *, knob_offset_s: float = 0.0,
                tz: dt.tzinfo | None = None, min_conf: float = MIN_CONF) -> LabelRead:
    """Pure: OCR lines of one overlay frame -> LabelRead. knob_offset_s is added to the label when the
    knob is displaced from the playhead (e.g. +15 if the frame was taken after one un-committed LEFT)."""
    obs = list(obs)
    if not obs:
        return LabelRead("no_overlay", cap_wall)
    start, end = program_window(obs, cap_wall, tz)
    cands = []
    for o in obs:
        if not (LABEL_Y[0] <= o.y <= LABEL_Y[1] and LABEL_X[0] <= o.x and o.x + o.width <= LABEL_X[1]):
            continue
        if _WIN.search(o.text):
            continue
        s = label_seconds(o.text)
        if s is not None:
            cands.append((o, s))
    if not cands:
        return LabelRead("no_label" if start is not None else "no_overlay", cap_wall,
                         prog_start=start, prog_end=end)
    if len({s for _, s in cands}) > 1:
        return LabelRead("ambiguous", cap_wall, prog_start=start, prog_end=end,
                         detail=f"{len(cands)} time labels")
    o, s = cands[0]
    conf = min(c.confidence for c, _ in cands)
    if conf < min_conf:
        return LabelRead("low_confidence", cap_wall, prog_start=start, prog_end=end, confidence=conf)
    s = s + knob_offset_s
    if start is not None:
        span = (end - start) if end is not None else 6 * 3600
        if not (-1 <= s <= span + WINDOW_SLACK_S) or start + s > cap_wall + 5:
            return LabelRead("out_of_window", cap_wall, label=o.text.strip(), prog_start=start,
                             prog_end=end, confidence=conf)
    ui = round(cap_wall - (start + s), 2) if start is not None else None
    return LabelRead("ok", cap_wall, label=o.text.strip(), label_s=float(s), prog_start=start,
                     prog_end=end, ui_delay_s=ui, confidence=round(conf, 3))


# ------------------------------------------------------------------------------- behind-live math
def behind_from_pair(at_live: LabelRead, now: LabelRead, *, max_s: float = MAX_BEHIND_S) -> float | None:
    """Seconds `now` is behind the live head, from a read AT the live head and a later read.
    Needs no program start; fails closed (None) across a program boundary or on a nonsense result."""
    if not (at_live.ok and now.ok) or now.cap_wall <= at_live.cap_wall:
        return None
    if at_live.prog_start is not None and now.prog_start is not None \
            and abs(at_live.prog_start - now.prog_start) > 1:
        return None
    b = (now.cap_wall - at_live.cap_wall) - (now.label_s - at_live.label_s)
    return round(b, 2) if -5 <= b <= max_s else None


@dataclass
class EdgeCalibration:
    """ui_delay at the live head per (stick, channel). Seeded by a read taken while the stick is at
    live (before a seek); a later read's behind = ui_delay - edge. Held in memory; to_dict() to persist."""
    edges: dict[str, dict] = field(default_factory=dict)
    max_age_s: float = 4 * 3600

    @staticmethod
    def key(stick: str, channel: str) -> str:
        return f"{stick}|{channel}"

    def record_live(self, stick: str, channel: str, read: LabelRead) -> bool:
        if not read.ok or read.ui_delay_s is None:
            return False
        self.edges[self.key(stick, channel)] = {"ui_delay_s": read.ui_delay_s, "at": read.cap_wall}
        return True

    def behind(self, stick: str, channel: str, read: LabelRead, *, max_s: float = MAX_BEHIND_S
               ) -> float | None:
        e = self.edges.get(self.key(stick, channel))
        if not e or not read.ok or read.ui_delay_s is None or read.cap_wall - e["at"] > self.max_age_s:
            return None
        b = read.ui_delay_s - e["ui_delay_s"]
        return round(b, 2) if -5 <= b <= max_s else None

    def to_dict(self) -> dict:
        return {"edges": dict(self.edges)}


# ---------------------------------------------------------------------------------- frame handling
Recognizer = Callable[[str], object]   # screen_ocr.recognize-compatible: path -> OCRResult


def _private_dir() -> Path:
    d = Path(tempfile.mkdtemp(prefix="rz-label-"))
    os.chmod(d, 0o700)
    return d


def ocr_png_then_delete(png: bytes, recognize: Recognizer | None = None) -> tuple[list[Obs] | None, bool, str]:
    """Write the PNG to a private temp file, OCR it, delete it in `finally`. Returns
    (observations or None, frame_deleted, error). The bytes are not kept by this function."""
    if recognize is None:
        from redzone.screen_ocr import recognize as _rec
        recognize = lambda p: _rec(p, timeout=8.0)   # noqa: E731
    d = _private_dir()
    path = d / "f.png"
    obs: list[Obs] | None = None
    err = ""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(png)
        r = recognize(str(path))
        status = getattr(r, "status", "error")
        if status == "unsupported":
            err = "unsupported"
        elif status not in ("ok", "empty"):
            err = getattr(r, "error", "") or status
        else:
            obs = [Obs(o.text, o.confidence, o.x, o.y, o.width, o.height) for o in r.observations]
    except Exception as exc:   # OCR trouble is a failed read, never a crash in a seek
        err = type(exc).__name__
    finally:
        try:
            path.unlink(missing_ok=True)
        finally:
            shutil.rmtree(d, ignore_errors=True)
    deleted = not path.exists() and not d.exists()
    return obs, deleted, err


async def read_label(stick, *, enabled: bool, knob_offset_s: float = 0.0,
                     recognize: Recognizer | None = None, tz: dt.tzinfo | None = None,
                     screencap: Callable[[], Awaitable[bytes]] | None = None) -> LabelRead:
    """One read of the overlay time label on `stick` (redzone.tuners.adb.Stick). Read only: no keys.
    The caller must already have the overlay up with the scrubber focused (yttv_seek)."""
    t0 = time.monotonic()
    if not enabled:
        return LabelRead("disabled")
    cap = screencap or stick.screencap
    w0 = time.time()
    try:
        png = await cap()
    except (asyncio.TimeoutError, OSError) as exc:
        return LabelRead("capture_failed", detail=type(exc).__name__, elapsed_s=round(time.monotonic() - t0, 3))
    cap_wall = (w0 + time.time()) / 2
    if not png:
        return LabelRead("capture_failed", cap_wall, elapsed_s=round(time.monotonic() - t0, 3))
    obs, deleted, err = await asyncio.to_thread(ocr_png_then_delete, png, recognize)
    del png
    if obs is None:
        r = LabelRead("unsupported" if err == "unsupported" else "ocr_failed", cap_wall, detail=err)
    else:
        r = parse_label(obs, cap_wall, knob_offset_s=knob_offset_s, tz=tz)
    r = replace(r, elapsed_s=round(time.monotonic() - t0, 3), frame_deleted=deleted)
    if not deleted:   # never trust a read whose frame we could not remove; make it loud
        r = replace(r, status="ocr_failed", label_s=None, ui_delay_s=None, detail="frame not deleted")
    return r


# --------------------------------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Manual, read-only: one overlay label read (no keys pressed). "
                                             "Open the overlay with the scrubber focused first.")
    ap.add_argument("--stick", required=True, help="adb host:port of the HIDDEN stick")
    ap.add_argument("--knob-offset", type=float, default=0.0)
    args = ap.parse_args(argv)
    from redzone.tuners.adb import Stick
    stick = Stick(args.stick)
    r = asyncio.run(read_label(stick, enabled=True, knob_offset_s=args.knob_offset))
    print(json.dumps(r.numbers()))
    return 0 if r.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
