"""[YTTV scrub buffer, item 1] Put a YouTube TV stick a set time behind live by scrubbing the player overlay.

Measured (overseer, Nov 6 9:40 PM; docs/sync/2026-11-06-yttv-scrub-buffer-spec.md on codex/resume-redzone):
  * DPAD_CENTER opens the overlay with the progress bar focused; each DPAD_LEFT moves the knob 15 s back; DPAD_CENTER
    commits. N=4 lands about 60 s behind; 7 of 7 repeats held (ESPNU, ACC Network, TNT, both sticks), one 20 min.
  * Pause does NOT hold (back at live 20-30 s after resume); MEDIA_REWIND / SKIP_BACKWARD / bare DPAD_LEFT don't
    seek; MEDIA_PLAY and BACK don't commit (MEDIA_PLAY_PAUSE also commits).
  * media_session position is WRONG after a seek (it crawls ~+0.3 s per 20 s): never used here for behind-live.
  * Keys are only safe when focus is known (#356: LEFT x9 with unknown focus seeked the stick 60 s), and keys on the
    wrong app land in Prime.

enter_buffer(stick, seconds, ...) therefore:
  1. refuses unless the stick is still hidden, YouTube TV is in front, and it is playing (nothing pressed);
  2. CENTER, then reads the screen (screencap -> local OCR -> the PNG deleted at once; numbers and labels only) and
     goes on only if the overlay is up with the PROGRESS BAR focused: a scrub label MM:SS above the knob and no row
     button (HD, CC, Networks, ...) holding focus. Anything else: fail closed;
  3. LEFT x N (N = round(seconds / step_s), capped by max_back_s), then reads again: the scrub label must have moved
     back N x step_s (+- step_tol_s). Otherwise: fail closed;
  4. CENTER commits, then the player must report PLAYING again (its state only, never its position);
  5. behind_live_s: from the label reader if one is given (item 2, the overlay time label vs the wall clock), else
     the estimate behind_before + N x step_s, with `measured` False.
"Fail closed" = no commit; the stick is re-linked to the live edge by the caller-supplied relink() (a deep link
lands at live +-0.5 s), and the result says why. The hidden check runs before every key batch: a stick that
became visible is never pressed again, and it is not re-linked either (that would change the screen).

Rule #1 is unchanged: this only ever moves a HIDDEN stick, and a flip still waits for a dead ball. Flag
yttv_seek.enabled (off). YouTube TV video stays DRM-black in screencaps; only the overlay UI is read.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable

log = logging.getLogger("redzone.yttv_seek")

YTTV_PKG_HINT = "youtube.tv"
SCRUB_DEFAULTS = {"enabled": False, "step_s": 15.0, "max_back_s": 150.0, "step_tol_s": 4.0,
                  "key_gap_s": 0.35, "settle_s": 4.0, "poll_s": 0.25, "overlay_wait_s": 0.8}

# The player row under the bar (#356 ROW_BUTTON labels seen so far). Focus on any of these = NOT the bar.
ROW_BUTTONS = ("hd", "cc", "networks", "multiview", "episodes", "more to watch", "stats", "key plays", "scores",
               "live chat", "info")
_MMSS = re.compile(r"^(?:(\d{1,2}):)?(\d{1,2}):(\d{2})$")
_WIN = re.compile(r"(\d{1,2}:\d{2})\s*([AaPp][Mm])\s*[-–—~]\s*(\d{1,2}:\d{2})\s*([AaPp][Mm])")

Observation = dict          # {"text", "x", "y", "w", "h"} normalized top-left, as screen_ocr gives
Reader = Callable[[Any], Awaitable["OverlayRead | None"]]


def _cfg(cfg, key: str):
    v = cfg.get(f"yttv_seek.{key}") if cfg is not None and hasattr(cfg, "get") else None
    return SCRUB_DEFAULTS[key] if v is None else v


def mmss(text: str) -> float | None:
    m = _MMSS.match((text or "").strip())
    if not m:
        return None
    hh, mm, ss = m.groups()
    if int(ss) > 59 or (hh and int(mm) > 59):
        return None
    return (int(hh) * 3600 if hh else 0) + int(mm) * 60 + int(ss)


@dataclass
class OverlayRead:
    """What one screen read says about the YouTube TV overlay (numbers and labels only)."""
    overlay: bool = False
    scrub_s: float | None = None        # the MM:SS label above the knob: shown only while the bar has focus
    focus_text: str = ""                # text inside the OCR helper's focus box, if any
    row_focused: bool = False           # a row button (HD, CC, ...) holds focus: the bar does NOT
    buttons: list[str] = field(default_factory=list)

    @property
    def bar_focused(self) -> bool:
        return self.overlay and self.scrub_s is not None and not self.row_focused


def parse_overlay(obs: list[Observation], focus: list[Observation] | None = None) -> OverlayRead:
    """The overlay from OCR rows (normalized top-left). The scrub label is a bare MM:SS / H:MM:SS above the bar
    (y < 0.8; the bar sits at y ~0.81); the program window '9:00 PM - 11:00 PM' is not one."""
    r = OverlayRead()
    labels = []
    for t in obs or []:
        s = str(t.get("text", "")).strip()
        y = float(t.get("y", 0.0))
        if _WIN.search(s):
            r.overlay = True
            continue
        sec = mmss(s)
        if sec is not None and 0.3 < y < 0.8:
            labels.append((y, sec))
            r.overlay = True
        if y > 0.85:
            low = s.lower()
            for k in ROW_BUTTONS:
                if (k in ("hd", "cc") and low == k) or (k not in ("hd", "cc") and k in low):
                    r.buttons.append(k)
                    r.overlay = True
    if len(labels) == 1:
        r.scrub_s = labels[0][1]
    # Several MM:SS candidates = we can't tell which is the knob's: leave scrub_s None (fail closed).
    for f in focus or []:
        text = str(f.get("text", "")).strip().lower()
        r.focus_text = text
        if any((k in ("hd", "cc") and text == k) or (k not in ("hd", "cc") and k in text) for k in ROW_BUTTONS):
            r.row_focused = True
        elif float(f.get("y", 0.0)) > 0.85:
            r.row_focused = True     # a focus box down in the row, unreadable: still not the bar
    return r


async def ocr_overlay(stick, timeout_s: float = 8.0) -> OverlayRead | None:
    """Default reader: one screencap, read by the Mac's local Vision OCR, the PNG deleted at once (Nick's rule:
    frames read, then deleted). None when it can't be read (off macOS, screencap failed, timeout)."""
    from redzone.screen_ocr import recognize
    d = tempfile.mkdtemp(prefix="rz-scrub-")
    path = os.path.join(d, "s.png")
    try:
        try:
            if not await stick.screenshot(path):
                return None
        except asyncio.TimeoutError:
            return None
        res = await asyncio.to_thread(recognize, path, timeout=timeout_s)
    finally:
        for p in (path,):
            try:
                os.remove(p)
            except OSError:
                pass
        try:
            os.rmdir(d)
        except OSError:
            pass
    if not res.read_ok:
        return None
    obs = [{"text": b.text, "x": b.x, "y": b.y, "w": b.width, "h": b.height} for b in res.observations]
    focus = [{"text": f.text, "x": f.x, "y": f.y, "w": f.width, "h": f.height} for f in res.focus_boxes]
    return parse_overlay(obs, focus)


@dataclass
class BufferResult:
    ok: bool
    asked_s: float
    presses: int = 0                    # LEFT presses sent
    behind_live_s: float | None = None  # how far behind live the stick now is (None when not known)
    measured: bool = False              # behind_live_s came from the label reader (truth), not the estimate
    committed: bool = False             # the scrub was committed (CENTER after the LEFTs)
    relinked: bool | None = None        # fail closed: re-linked to live (True), tried and failed (False), not needed
    reason: str = ""
    steps: list[str] = field(default_factory=list)
    took_s: float = 0.0

    @property
    def behind_s(self) -> float | None:
        """Label-measured seconds behind live after the commit; None when no label read (the item 4 seam)."""
        return self.behind_live_s if self.measured else None

    def row(self) -> dict:
        d = asdict(self)
        for k in ("behind_live_s", "took_s"):
            if d[k] is not None:
                d[k] = round(float(d[k]), 2)
        return d


async def enter_buffer(stick, seconds: float, *, cfg=None, pkg: str | None = None,
                       still_hidden: Callable[[], bool] | None = None,
                       read_overlay: Reader | None = None,
                       read_behind: Callable[[Any], Awaitable[float | None]] | None = None,
                       relink: Callable[[], Awaitable[bool]] | None = None,
                       behind_before_s: float = 0.0,
                       clock: Callable[[], float] | None = None) -> BufferResult:
    """Scrub a hidden YouTube TV stick `seconds` behind where it plays now. See the module docstring. Never raises."""
    clock = clock or time.monotonic
    t0 = clock()
    res = BufferResult(ok=False, asked_s=round(float(seconds), 2))
    read_overlay = read_overlay or ocr_overlay
    still_hidden = still_hidden or (lambda: True)
    try:
        await _enter(stick, float(seconds), cfg, pkg, still_hidden, read_overlay, read_behind, relink,
                     float(behind_before_s), res)
    except Exception as e:   # never raise into the switch path: report, and leave the stick re-linked if we can
        log.warning("enter_buffer failed: %r", e)
        res.ok, res.reason = False, f"exception: {e!r}"[:160]
        if res.presses and not res.committed and res.relinked is None:
            await _relink(relink, still_hidden, res)
    res.took_s = clock() - t0
    return res


async def _relink(relink, still_hidden, res: BufferResult) -> None:
    if relink is None or not still_hidden():
        res.relinked = False
        return
    try:
        res.relinked = bool(await relink())
    except Exception as e:
        log.warning("relink after a failed scrub failed: %r", e)
        res.relinked = False


async def _fail(res: BufferResult, why: str, relink, still_hidden, pressed: bool) -> None:
    res.ok, res.reason = False, why
    res.steps.append(f"fail: {why}")
    if pressed:
        await _relink(relink, still_hidden, res)


async def _keys(stick, code: str, n: int, gap: float) -> bool:
    for _ in range(n):
        r = await stick.shell(f"input keyevent {code}", timeout=4)
        if not r.ok:
            return False
        if gap > 0:
            await asyncio.sleep(gap)
    return True


async def _enter(stick, seconds, cfg, pkg, still_hidden, read_overlay, read_behind, relink, behind_before, res):
    step = float(_cfg(cfg, "step_s"))
    gap = float(_cfg(cfg, "key_gap_s"))
    n = int(round(min(max(0.0, seconds), float(_cfg(cfg, "max_back_s"))) / step))
    if n <= 0:
        res.ok, res.reason, res.behind_live_s = True, "nothing to move", behind_before
        return
    # 1. preconditions: nothing pressed if any fails
    if not still_hidden():
        return await _fail(res, "stick is not hidden", relink, still_hidden, pressed=False)
    front = await stick.foreground()
    if YTTV_PKG_HINT not in (front or "") and not (pkg and pkg in (front or "")):
        return await _fail(res, f"YouTube TV is not in front ({front or 'unknown'})", relink, still_hidden, False)
    pb = await stick.playback(pkg)
    if pb.get("state") != "playing":
        return await _fail(res, f"player is {pb.get('state')}, not playing", relink, still_hidden, False)
    # 2. open the overlay; the bar must hold focus
    if not await _keys(stick, "KEYCODE_DPAD_CENTER", 1, 0.0):
        return await _fail(res, "CENTER not sent", relink, still_hidden, pressed=True)
    res.steps.append("CENTER")
    await asyncio.sleep(float(_cfg(cfg, "overlay_wait_s")))
    before = await read_overlay(stick)
    if before is None or not before.bar_focused:
        why = "overlay unreadable" if before is None else (
            "a row button has focus" if before.row_focused else
            "no scrub label: the bar does not have focus" if before.overlay else "overlay not up")
        return await _fail(res, f"focus guard: {why}", relink, still_hidden, pressed=True)
    res.steps.append(f"bar focused at {before.scrub_s:.0f}s")
    # 3. LEFT x n, then the label must have moved back n steps
    if not still_hidden():
        return await _fail(res, "stick became visible before the scrub", relink, still_hidden, pressed=True)
    if not await _keys(stick, "KEYCODE_DPAD_LEFT", n, gap):
        res.presses = n
        return await _fail(res, "LEFT not sent", relink, still_hidden, pressed=True)
    res.presses = n
    res.steps.append(f"LEFT x{n}")
    after = await read_overlay(stick)
    want = n * step
    if after is None or not after.bar_focused or after.scrub_s is None:
        return await _fail(res, "scrub label lost after the LEFTs", relink, still_hidden, pressed=True)
    moved = before.scrub_s - after.scrub_s
    if abs(moved - want) > float(_cfg(cfg, "step_tol_s")):
        return await _fail(res, f"knob moved {moved:.0f}s, not {want:.0f}s", relink, still_hidden, pressed=True)
    res.steps.append(f"knob {after.scrub_s:.0f}s (moved {moved:.0f}s)")
    # 4. commit, then it must play again (state only: the position is wrong after a seek)
    if not still_hidden():
        return await _fail(res, "stick became visible before the commit", relink, still_hidden, pressed=True)
    if not await _keys(stick, "KEYCODE_DPAD_CENTER", 1, 0.0):
        return await _fail(res, "commit not sent", relink, still_hidden, pressed=True)
    res.committed = True
    res.steps.append("CENTER (commit)")
    end = time.monotonic() + float(_cfg(cfg, "settle_s"))
    playing = False
    while time.monotonic() < end:
        p = await stick.playback(pkg)
        if p.get("state") == "playing":
            playing = True
            break
        await asyncio.sleep(float(_cfg(cfg, "poll_s")))
    if not playing:
        return await _fail(res, "not playing after the commit", relink, still_hidden, pressed=True)
    # 5. how far behind now
    res.behind_live_s = behind_before + moved
    if read_behind is not None:
        try:
            got = await read_behind(stick)
        except Exception as e:
            log.warning("label read after the scrub failed: %r", e)
            got = None
        if got is not None:
            res.behind_live_s, res.measured = float(got), True
            res.steps.append(f"label: {got:.1f}s behind live")
    res.ok = True
    res.reason = "measured by the label" if res.measured else "estimated from the knob (no label read)"
