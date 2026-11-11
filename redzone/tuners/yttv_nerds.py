"""[POSITION-FIX item 5] YouTube TV's "Stats for nerds" panel on the hidden stick -> Live Latency as this tune's offset.

Why: a fresh YouTube TV tune does not land at a fixed lag (sd 1.2-2.0 s, range 5 s, Nov 10 TV); the panel's Live Latency
reads that lag with no position lost and no pause (FINDINGS 5c, 10-12 tunes). Flag timing.stats_for_nerds, OFF.

Measured on the hidden 2023 stick, espn and fs1 (numbers only):
  * DOWN, DOWN, LEFT x4, DOWN, RIGHT x6, CENTER (14 keys) in ONE adb command switch the panel on (1.2 s); the same 14
    keys switch it off; one BACK closes the overlay. Playback is not paused (21 toggles), the session line unchanged.
  * Live Latency (0.01 s) has a value about 11-23 s after a fresh tune (23 of 33 frames, at least one on 12 of 12
    tunes); 0.00 before it appears, once the buffer reaches about 20 s, and whenever the stick is behind live. So it is
    read only at the live edge, at about 11, 17 and 22 s after the landing; the first value that is not 0.00 is kept.
  * Against the overlay label on the same tune: Live Latency - label U = +0.34 s mean, sd 0.19.
  * The panel covers the time label (3 of 3 label reads failed with it on): it must be off before any label read and
    before a flip. The reader switches it off itself, in a `finally`, and marks the stick while it may still be on.
  * The panel shows a playback id line. That line is masked: never parsed, never kept, never logged.

Frames: one screencap per read, OCR'd locally, deleted at once (yttv_label._ocr_frame). Only numbers leave.
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Awaitable, Callable, Iterable

FLAG = "timing.stats_for_nerds"
TOGGLE_KEYS = ("DPAD_DOWN", "DPAD_DOWN", "DPAD_LEFT", "DPAD_LEFT", "DPAD_LEFT", "DPAD_LEFT", "DPAD_DOWN",
               "DPAD_RIGHT", "DPAD_RIGHT", "DPAD_RIGHT", "DPAD_RIGHT", "DPAD_RIGHT", "DPAD_RIGHT", "DPAD_CENTER")
SHOTS_AFTER_LANDING_S = (11.0, 17.0, 22.0)   # when Live Latency is readable after a fresh tune (11-23 s)
LATEST_S = 23.0                              # past this after the landing the value reads 0.00 again
LL_BIAS_S = 0.34                             # Live Latency - label U, mean (espn +0.29 n=6, fs1 +0.42 n=4)
LL_SD_S = 0.19                               # ... its spread: the per-tune offset is known to this after one read

# Lines that may carry an id (playback id, content id): dropped before any parsing. Matched case-insensitively.
_MASK = re.compile(r"cpn|video\s*id|docid|\bvid\b|\bid\s*[:=]", re.IGNORECASE)
_LL = re.compile(r"live\s*latency\W*?(\d+(?:\.\d+)?)\s*s", re.IGNORECASE)
_BUF = re.compile(r"buffer\s*health\W*?(\d+(?:\.\d+)?)\s*s", re.IGNORECASE)
_T = re.compile(r"(?:^|\s)t:\s*(\d+(?:\.\d+)?)")


@dataclass(frozen=True)
class NerdsRead:
    """One frame of the panel: numbers only."""
    status: str                       # ok | zero (panel up, Live Latency 0.00) | no_panel | capture_failed | ocr_failed
    live_latency_s: float | None = None
    position_s: float | None = None   # the 'Mystery Text' t: field: the media-session position
    buffer_s: float | None = None
    wall: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status == "ok" and self.live_latency_s is not None


def masked(lines: Iterable[str]) -> list[str]:
    """The panel's text without any line that may carry an id."""
    return [x for x in lines if not _MASK.search(x or "")]


def parse_nerds(texts: Iterable[str], wall: float = 0.0) -> NerdsRead:
    """Pure: the OCR'd text lines of one frame -> NerdsRead. The id lines are masked before anything is matched."""
    ll = pos = buf = None
    seen = False
    for line in masked(texts):
        m = _LL.search(line)
        if m:
            seen, ll = True, float(m.group(1))
        m = _BUF.search(line)
        if m:
            seen, buf = True, float(m.group(1))
        m = _T.search(line)
        if m:
            pos = float(m.group(1))
    if not seen:
        return NerdsRead("no_panel", wall=wall)
    if ll is None or ll <= 0.0:
        return NerdsRead("zero", None, pos, buf, wall)
    return NerdsRead("ok", ll, pos, buf, wall)


@dataclass
class LiveLatencyResult:
    """The per-tune offset from the panel: u_s is in the label's units (wall - (program start + label) at the live
    edge), so it drops into the same delay model as a label read."""
    u_s: float | None
    sd_s: float = LL_SD_S
    reads: list[dict] = field(default_factory=list)
    keys: list[str] = field(default_factory=list)
    panel_off: bool = True
    reason: str = ""

    def row(self) -> dict:
        return asdict(self)


def enabled(cfg) -> bool:
    try:
        return bool(cfg.get(FLAG, False)) if cfg is not None and hasattr(cfg, "get") else False
    except Exception:
        return False


def panel_on(stick) -> bool:
    """True while the panel may be up on this stick (a switch-off that did not go out)."""
    return bool(getattr(stick, "nerds_panel_on", False))


async def _send(stick, codes: Iterable[str]) -> bool:
    """All codes in ONE shell command (the 14-key toggle takes 1.2 s that way)."""
    cmd = "input keyevent " + " ".join(f"KEYCODE_{c}" for c in codes)
    r = await stick.shell(cmd, timeout=6)
    return bool(getattr(r, "ok", False))


async def _frame_texts(stick, recognize) -> list[str] | None:
    from redzone.tuners.yttv_label import _ocr_frame
    try:
        png = await stick.screencap()
    except (asyncio.TimeoutError, OSError):
        return None
    if not png:
        return None
    obs, _focus, deleted, _err = await asyncio.to_thread(_ocr_frame, png, recognize)
    del png
    if obs is None or not deleted:
        return None
    return [o.text for o in obs]


async def read_live_latency(stick, *, cfg, landed_wall: float, behind_s: float = 0.0,
                            still_hidden: Callable[[], bool] | None = None,
                            recognize=None, sleep: Callable[[float], Awaitable[None]] | None = None,
                            clock: Callable[[], float] | None = None,
                            shots_s: Iterable[float] = SHOTS_AFTER_LANDING_S) -> LiveLatencyResult:
    """Panel on, frames at about 11, 17 and 22 s after the landing (the first value not 0.00 is kept), panel off, BACK.
    Refuses (no key) when the flag is off, the stick is not hidden, it is behind live, or the window has passed.
    Never raises; the panel is switched off in a `finally` whenever it was switched on."""
    sleep = sleep or asyncio.sleep
    clock = clock or time.time
    still_hidden = still_hidden or (lambda: True)
    res = LiveLatencyResult(None)
    if not enabled(cfg):
        res.reason = "disabled"
        return res
    if behind_s >= 7.5:
        res.reason = "not at the live edge: Live Latency reads 0.00 behind live"
        return res
    if clock() - landed_wall > LATEST_S:
        res.reason = "too long after the tune: Live Latency reads 0.00 by then"
        return res
    if not still_hidden():
        res.reason = "stick is not hidden"
        return res
    switched_on = False
    try:
        res.keys.extend(TOGGLE_KEYS)
        if not await _send(stick, TOGGLE_KEYS):
            # some of the 14 may have landed: a second toggle could switch it ON, so press nothing more and keep the
            # stick marked (no label read, no flip onto it) until someone looks
            res.reason = "panel keys not sent"
            stick.nerds_panel_on = True
            res.panel_off = False
            return res
        switched_on = True
        stick.nerds_panel_on = True
        for at in shots_s:
            wait = landed_wall + float(at) - clock()
            if wait > 0:
                await sleep(wait)
            if not still_hidden():
                res.reason = "stick became visible"
                return res
            texts = await _frame_texts(stick, recognize)
            r = parse_nerds(texts, clock()) if texts is not None else NerdsRead("ocr_failed", wall=clock())
            res.reads.append({"status": r.status, "after_landing_s": round(r.wall - landed_wall, 1),
                              **({"live_latency_s": r.live_latency_s} if r.ok else {})})
            if r.ok:
                res.u_s = round(r.live_latency_s - LL_BIAS_S, 2)
                res.reason = "measured"
                break
        if res.u_s is None:
            res.reason = "no Live Latency value in the window"
        return res
    except Exception as e:   # a reader never breaks the hidden stick's path
        res.u_s, res.reason = None, f"exception: {type(e).__name__}"
        return res
    finally:
        if switched_on:
            if still_hidden():
                res.panel_off = await _switch_off(stick, res)
            else:   # never press a visible stick: it stays marked, so no label read and no flip trusts it
                res.panel_off = False
                stick.nerds_panel_on = True


async def _switch_off(stick, res: LiveLatencyResult) -> bool:
    """The same 14 keys, then one BACK. Only when both went out is the stick marked clear (label reads, flips)."""
    try:
        res.keys.extend(TOGGLE_KEYS)
        ok = await _send(stick, TOGGLE_KEYS)
        res.keys.append("BACK")
        ok = await _send(stick, ("BACK",)) and ok
    except Exception:
        ok = False
    stick.nerds_panel_on = not ok
    return ok
