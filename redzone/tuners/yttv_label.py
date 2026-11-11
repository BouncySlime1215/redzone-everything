"""[YTTV scrub buffer, item 2] Read YouTube TV's overlay time label -> numbers only.

Why: after an overlay scrub media_session is wrong, so the overlay time label is the behind-live truth (decisions 2026-11-06 22:15 ET, flag off).

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
import logging
import os
import re
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Awaitable, Callable, Iterable

log = logging.getLogger("redzone.yttv_label")

LABEL_READER_FLAG = "timing.behind_live_s.label_reader"

# Overlay geometry (normalized, top-left; 1920x1080 screencaps Nov 5, ESPN, both sticks; ui_common.py):
# bar y ~0.815, x ~0.05..0.95; label sits just above the knob; window '2:30 AM - 3:00 AM' at (0.81, 0.72).
LABEL_Y = (0.38, 0.84)  # live Nov 6: with a preview card up the label box starts at y 0.444-0.450
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
                                      # no_overlay | banner | no_label | ambiguous | low_confidence |
                                      # out_of_window
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


_BANNER_WORDS = re.compile(r"^(?:LIVE|LIVI|LTVE|CC|HD|Multiview|\d*\s*Multiview|Networks|Episodes|More to watch)$",
                           re.IGNORECASE)


def is_banner(obs: Iterable[Obs]) -> bool:
    """The post-tune LIVE/title banner or a button row is up, but no scrub time label: the scrubber is not
    focused. Words only decide this; nothing is kept."""
    return any(_BANNER_WORDS.match(o.text.strip()) for o in obs)


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
        # Live Nov 6 (#377): after a tune a LIVE/title banner sits up 25+ s. It looks like the overlay (it even
        # has CC) but the scrubber is not focused, keys do nothing and there is no label. Say so, so the seek
        # path closes it, reopens and retries (LEFT is the probe), instead of treating it as "no overlay".
        status = "banner" if is_banner(obs) else "no_label" if start is not None else "no_overlay"
        return LabelRead(status, cap_wall, prog_start=start, prog_end=end)
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
    Needs no program start (live Nov 6: no program window is on screen with the scrub preview up), so this is
    the method to use. The label only shows once a LEFT has landed on the bar, so the "at live" read is taken
    with the knob one LEFT back: read it with knob_offset_s=15 (one uncommitted LEFT), and likewise for the
    later probe read. Fails closed (None) across a program boundary or on a nonsense result."""
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
    obs, _focus, deleted, err = _ocr_frame(png, recognize)
    return obs, deleted, err


def _ocr_frame(png: bytes, recognize: Recognizer | None = None
               ) -> tuple[list[Obs] | None, list[dict], bool, str]:
    """ocr_png_then_delete plus the OCR helper's focus boxes (for yttv_seek's focus guard)."""
    if recognize is None:
        from redzone.screen_ocr import recognize as _rec
        recognize = lambda p: _rec(p, timeout=8.0)   # noqa: E731
    d = _private_dir()
    path = d / "f.png"
    obs: list[Obs] | None = None
    focus: list[dict] = []
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
            focus = [{"text": f.text, "x": f.x, "y": f.y, "w": f.width, "h": f.height}
                     for f in getattr(r, "focus_boxes", ()) or ()]
    except Exception as exc:   # OCR trouble is a failed read, never a crash in a seek
        err = type(exc).__name__
    finally:
        try:
            path.unlink(missing_ok=True)
        finally:
            shutil.rmtree(d, ignore_errors=True)
    deleted = not path.exists() and not d.exists()
    return obs, focus, deleted, err


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


# ------------------------------------------------------------------------- one read after entry (#377 banner)
@dataclass
class ProbeResult:
    behind_live_s: float | None
    read: LabelRead
    keys: list[str] = field(default_factory=list)
    reason: str = ""


class LabelProbe:
    """The overlay label reader wired for the scrub entry (Nick, Nov 6 night: a read costs ~7.5 s, so read ONCE
    after entry, then trust the playhead).

    * `overlay_reader(stick)` is enter_buffer's read_overlay: ONE screencap feeds both yttv_seek's focus guard
      (OverlayRead) and this module's label numbers. After `arm(stick)` the first read with the bar focused (the
      LEFT probe at the live edge, knob one step back) is kept as the stick's at-live anchor.
    * `__call__(stick)` is enter_buffer's read_behind: the probe sequence with the post-tune banner handling from
      #377 (CENTER; no scrub label -> LEFT probe; still none -> CENTER close, reopen, retry; fail closed after
      open_tries), one label read with knob_offset_s = step, then RIGHT + CENTER so the knob goes back to where
      it was before it commits. behind = behind_from_pair(anchor, read) + (commit wall - screencap wall), since
      the picture stands still while the overlay is up.
    * `playhead(stick, now)`: after that, the stick's behind-live without another read: the playhead plays at 1x,
      so behind only changes when something holds the picture (a later read adds ~read_cost_s).

    Fails closed (None) on: no anchor, a row button focused, the banner never clearing, a label that is not
    one step behind the knob, or a pair result outside [-5, max]. Frames are read, then deleted (_ocr_frame).
    """

    def __init__(self, cfg=None, *, recognize: Recognizer | None = None, tz: dt.tzinfo | None = None,
                 sleep: Callable[[float], Awaitable[None]] | None = None):
        self.cfg = cfg
        self.recognize = recognize
        self.tz = tz
        self.sleep = sleep or asyncio.sleep
        self.anchors: dict[str, LabelRead] = {}
        self.armed: dict[str, float] = {}       # stick -> its behind_live_s when armed (0 = at the live edge)
        self.held: dict[str, tuple[float, float]] = {}   # stick -> (behind_s, wall) from the last measured read
        # [timing.label_stamp_at_pause] stick -> (the anchor's pause event, its behind-live then); stick -> the tune's
        # position counter on YouTube's clock from the last stamped read. Empty with the flag off.
        self.anchor_stamps: dict[str, tuple] = {}
        self.clocks: dict[str, object] = {}
        # (stick key, u_s, behind_s, sd_s, wall): this tune's lag behind YouTube's clock at its live edge, from one
        # stamped read, and how far the stick plays behind that edge now (engine: the per-tune offset; flag on only)
        self.on_tune_offset: Callable[[str, float, float, float, float], None] | None = None

    def _flag(self, name: str) -> bool:
        from redzone.tuners import yttv_timing
        return yttv_timing.on(self.cfg, name)

    async def _pause_stamp(self, stick):
        """[timing.label_stamp_at_pause] The media session's pause event (read with the YouTube TV package), or None."""
        from redzone.tuners import yttv_timing
        pkg = (getattr(stick, "packages", None) or {}).get("youtubetv")
        return yttv_timing.pause_stamp(await yttv_timing.read_session(stick, pkg))

    def _c(self, key: str, default):
        v = self.cfg.get(f"timing.behind_live_s.label_{key}") if self.cfg is not None and hasattr(self.cfg, "get") \
            else None
        return default if v is None else v

    @staticmethod
    def _key(stick) -> str:
        return str(getattr(stick, "host", stick))

    def arm(self, stick, behind_before_s: float = 0.0) -> bool:
        """The next bar-focused overlay read on this stick is its at-live anchor (call right before enter_buffer
        on a stick tuned or re-linked to the live edge). Refuses (False, nothing armed) while the stick is marked
        behind on a target (#391 Stick.behind_on: a same-channel deep link does NOT reset a stick that is behind
        live, so its "live edge" probe would anchor short: asked 90, read 23). The scrub then runs unmeasured and
        the caller fails closed."""
        k = self._key(stick)
        self.anchors.pop(k, None)
        self.armed.pop(k, None)
        self.anchor_stamps.pop(k, None)
        if getattr(stick, "behind_on", None):
            return False
        self.armed[k] = float(behind_before_s)
        return True

    async def _frame(self, stick, knob_offset_s: float):
        from redzone.tuners import yttv_seek
        w0 = time.time()
        try:
            png = await yttv_seek.band_png(stick, yttv_seek.overlay_band(self.cfg))
            if not png:
                w0 = time.time()      # cap_wall is the midpoint of the capture that worked
                png = await stick.screencap()
        except (asyncio.TimeoutError, OSError):
            return None, None
        wall = (w0 + time.time()) / 2
        if not png:
            return None, None
        obs, focus, deleted, _err = await asyncio.to_thread(_ocr_frame, png, self.recognize)
        del png
        if obs is None or not deleted:
            return None, None
        ov = yttv_seek.parse_overlay([{"text": o.text, "x": o.x, "y": o.y, "w": o.width, "h": o.height}
                                      for o in obs], focus)
        lab = parse_label(obs, wall, knob_offset_s=knob_offset_s, tz=self.tz)
        return ov, lab

    async def overlay_reader(self, stick):
        """enter_buffer's read_overlay. Records the at-live anchor on the first bar-focused read after arm()."""
        step = float(self._c("step_s", 15.0))
        ov, lab = await self._frame(stick, step)
        k = self._key(stick)
        if ov is not None and ov.bar_focused and lab is not None and lab.ok and k in self.armed:
            # knob is one LEFT behind the playhead; the playhead is `armed` s behind live
            armed = self.armed.pop(k)
            self.anchors[k] = replace(lab, label_s=lab.label_s + armed)
            if self._flag("label_stamp_at_pause"):
                # the picture froze at the CENTER that opened this overlay: the anchor's time is that pause event
                stamp = await self._pause_stamp(stick)
                if stamp is not None:
                    a = self.anchors[k]
                    self.anchors[k] = replace(a, cap_wall=stamp.wall, ui_delay_s=None if a.prog_start is None
                                              else round(stamp.wall - (a.prog_start + a.label_s), 2))
                    self.anchor_stamps[k] = (stamp, armed)
        return ov

    async def _key_press(self, stick, code: str) -> bool:
        r = await stick.shell(f"input keyevent KEYCODE_{code}", timeout=4)
        return bool(getattr(r, "ok", False))

    async def probe(self, stick, still_hidden: Callable[[], bool] | None = None,
                    behind_s: float | None = None) -> ProbeResult:
        """behind_s: where the caller believes the stick is (None = behind live, as after a scrub). Only read with
        timing.no_right_probe_at_live: at the live edge the RIGHT is dropped and the LEFT step booked."""
        still_hidden = still_hidden or (lambda: True)
        k = self._key(stick)
        anchor = self.anchors.get(k)
        step = float(self._c("step_s", 15.0))
        wait = float(self._c("overlay_wait_s", 1.3))
        tries = int(self._c("open_tries", 4))
        keys: list[str] = []
        stamp_on = self._flag("label_stamp_at_pause")
        left_at_live = self._flag("no_right_probe_at_live") and behind_s is not None and behind_s < step / 2
        stamp = None
        if anchor is None:
            return ProbeResult(None, LabelRead("no_label"), keys, "no at-live anchor: arm() before enter_buffer")
        if self._flag("stats_for_nerds") and getattr(stick, "nerds_panel_on", False):
            # the Stats for nerds panel covers the label (3 of 3 reads failed with it on): never read under it
            return ProbeResult(None, LabelRead("no_label"), keys, "stats for nerds panel is on")

        async def press(code):
            if not still_hidden():
                raise RuntimeError("stick became visible")
            keys.append(code)
            if not await self._key_press(stick, code):
                raise RuntimeError(f"{code} not sent")

        try:
            await press("DPAD_CENTER")
            await self.sleep(wait)
            got = None
            for _ in range(tries):
                ov, _lab = await self._frame(stick, step)
                if ov is not None and ov.row_focused:
                    return ProbeResult(None, LabelRead("ambiguous"), keys, "focus guard: a row button has focus")
                if ov is not None and ov.overlay:
                    await press("DPAD_LEFT")             # the probe: the label shows only once LEFT lands
                    await self.sleep(wait)
                    ov2, lab2 = await self._frame(stick, step)
                    if ov2 is not None and ov2.bar_focused and lab2 is not None and lab2.ok:
                        got = lab2
                        break
                    # banner (keys do nothing there) or a missed read: close, reopen. A close after a LEFT that
                    # did land may commit one step: the next read's pair counts it, so it is measured, not lost.
                    await press("DPAD_CENTER")
                    await self.sleep(wait)
                await press("DPAD_CENTER")
                await self.sleep(wait)
            if got is None:
                return ProbeResult(None, LabelRead("banner"), keys, "no scrub label after the probe (banner)")
            if stamp_on:
                # [timing.label_stamp_at_pause] the label shows the frame the CENTER froze: stamp it at the session's
                # pause event (4.5-6 s before this screencap on the TV), not at the screencap
                stamp = await self._pause_stamp(stick)
                if stamp is not None:
                    got = replace(got, cap_wall=stamp.wall, ui_delay_s=None if got.prog_start is None
                                  else round(stamp.wall - (got.prog_start + got.label_s), 2))
            if not left_at_live:
                await press("DPAD_RIGHT")                # knob back onto the playhead, then commit there
                await self.sleep(float(self._c("key_gap_s", 0.35)))
            await press("DPAD_CENTER")
            committed_at = time.time()
        except RuntimeError as e:
            return ProbeResult(None, LabelRead("no_label"), keys, str(e))
        b = behind_from_pair(anchor, got, max_s=float(self._c("max_behind_s", MAX_BEHIND_S)))
        if b is None:
            return ProbeResult(None, got, keys, "pair out of range or new program")
        if stamp is not None:
            after = await self._stamped_behind(stick, k, b, stamp, got)
            if after is not None:
                b2, wall, clock, pos_now = after
                if clock is not None:
                    self.clocks[k] = clock
                    if self.on_tune_offset is not None:
                        try:   # U = the picture's lag behind YouTube's clock now, less how far it plays behind the edge
                            self.on_tune_offset(k, round(clock.ui_delay(wall, pos_now) - b2, 2), b2, clock.sd_s, wall)
                        except Exception:   # the delay model's hand-off must never break a read
                            log.exception("label probe: tune offset hand-off failed")
                self.held[k] = (b2, wall)
                return ProbeResult(b2, got, keys, "measured (pause stamp, commit position)")
        # The picture stands still while the overlay is up (scrub thread, Nov 7): every second between this
        # screencap and the commit landing adds a second behind. Measured, not a constant.
        b = round(b + max(0.0, committed_at - got.cap_wall), 2)
        if left_at_live:
            from redzone.tuners import yttv_timing
            b = round(b + yttv_timing.LEFT_STEP_S, 2)   # the committed LEFT: exactly one step back (69 of 69)
        self.held[k] = (b, got.cap_wall)
        return ProbeResult(b, got, keys, "measured")

    async def _stamped_behind(self, stick, k: str, pair_b: float, stamp, read: LabelRead):
        """[timing.label_stamp_at_pause] Behind-live where the commit landed: the session's position after the commit
        against the pause event (both on the tune's own counter, 0.02 s). With the anchor's own pause stamp the
        label is not needed for this at all; without it, the pair (both reads stamped at their pause) plus the
        move since. Returns (behind_s, wall, TuneClock or None, position now), or None when the session read fails."""
        from redzone.tuners import yttv_timing
        pkg = (getattr(stick, "packages", None) or {}).get("youtubetv")
        end = time.monotonic() + float(self._c("settle_s", 4.0))
        now = None
        while True:
            now = await yttv_timing.read_session(stick, pkg)
            if now is not None and now.state == "playing" and now.pos_now_s is not None:
                break
            if time.monotonic() >= end:
                return None
            await self.sleep(0.25)
        a = self.anchor_stamps.get(k)
        if a is not None:
            a_stamp, armed = a
            b = armed + (now.wall - a_stamp.wall) - (now.pos_now_s - a_stamp.position_s)
        else:
            b = pair_b + (now.wall - stamp.wall) - (now.pos_now_s - stamp.position_s)
        max_s = float(self._c("max_behind_s", MAX_BEHIND_S))
        if not -5 <= b <= max_s:
            return None
        return round(b, 2), now.wall, self._clock_from(read, stamp), now.pos_now_s

    def _clock_from(self, read: LabelRead, stamp):
        from redzone.tuners import yttv_timing
        if read.prog_start is None or read.label_s is None:
            return None
        return yttv_timing.TuneClock.from_label(read.prog_start, read.label_s, stamp)

    async def __call__(self, stick) -> float | None:
        """enter_buffer's read_behind: seconds behind live, measured once after the commit, or None."""
        return (await self.probe(stick)).behind_live_s

    def playhead(self, stick, now: float | None = None) -> float | None:
        """Behind-live since the last measured read, trusting the playhead (it plays at 1x after a seek)."""
        h = self.held.get(self._key(stick))
        return None if h is None else h[0]


# --------------------------------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Manual, read-only: one overlay label read (no keys pressed). "
                                             "Open the overlay with the scrubber focused first.")
    ap.add_argument("--stick", required=True, help="adb host:port of the HIDDEN stick")
    ap.add_argument("--knob-offset", type=float, default=0.0,
                    help="seconds the knob sits before the playhead (15 = one uncommitted LEFT probe)")
    ap.add_argument("--save", type=Path, help="write this read (numbers only) here: the at-live read")
    ap.add_argument("--since", type=Path, help="an at-live read saved by --save: also print behind_live_s")
    args = ap.parse_args(argv)
    from redzone.tuners.adb import Stick
    stick = Stick(args.stick)
    r = asyncio.run(read_label(stick, enabled=True, knob_offset_s=args.knob_offset))
    out = r.numbers()
    if args.since:
        live = LabelRead(**json.loads(args.since.read_text()))
        out["behind_live_s"] = behind_from_pair(live, r)
    if args.save and r.ok:
        args.save.write_text(json.dumps(r.numbers()))
    print(json.dumps(out))
    return 0 if r.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
