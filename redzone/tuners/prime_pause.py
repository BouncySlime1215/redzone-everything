"""[Prime buffer] Put a HIDDEN Prime Video stick a set time behind live by pausing it.

Measured (overseer, Nov 7 ~12:25 AM ET, POR@LAL on the hidden stick, auto-switching paused):
  * MEDIA_PAUSE 60 s then MEDIA_PLAY: 67.7 s behind (from 6.6), and Prime's own timecode went 02:20:54 -> 02:24:44
    across exactly 230 s of wall time. It held for the 4 minutes checked, no snap back to live. On YouTube TV pause
    does NOT hold (yttv_seek scrubs instead); on Prime pause is the tool.
  * MEDIA_REWIND on Prime is continuous (x1 then CENTER ~2 s later landed 214 s back, x2 665 s): never used here.
    DPAD_LEFT alone leaves it paused.
  * media_session goes stale on Prime too: its position is never used. The overlay timecode (H:MM:SS, OCR-readable;
    the picture is readable in screencap on Prime) is the truth.

enter_buffer_pause(stick, seconds, ...):
  1. refuses unless the stick is still hidden, Prime is in front, and it is playing (nothing pressed);
  2. MEDIA_PAUSE, then reads the paused timecode tA (frame deleted at once). Unreadable: PLAY and fail closed;
  3. holds min(seconds, max_hold_s), checking hidden every poll_s. If the stick becomes visible it is un-paused with
     one MEDIA_PLAY (a frozen picture on Nick's screen is worse than a moving one) and not re-linked;
  4. reads again just before PLAY: still paused at tA (+- tol), or something moved it (snap/reset): fail closed;
  5. MEDIA_PLAY; the player must report playing (state only). Then one read tB: the gain is
     (wall since the pause) - (tB - tA), and it must match the hold (+- gain_tol_s) or fail closed. No tB read: the gain is
     the wall-clock hold, with `measured` False.
"Fail closed" = the caller's relink() puts the stick back at live; the result says why. Rule #1 is unchanged: only a
hidden stick is touched and a flip still waits for a dead ball. Flag prime_pause.enabled (off): the caller checks it.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from redzone.tuners.yttv_seek import BufferResult, _fail, _keys, _relink

log = logging.getLogger("redzone.prime_pause")

PRIME_PKG_HINTS = ("com.amazon.firebat", "com.amazon.avod", "com.amazon.amazonvideo.livingroom")
PAUSE_DEFAULTS = {"enabled": False, "max_hold_s": 120.0, "overlay_wait_s": 1.5, "read_tries": 2,
                  "poll_s": 0.5, "settle_s": 4.0, "hold_tol_s": 2.0, "gain_tol_s": 3.0, "band_y_min": 0.55}
_HMS = re.compile(r"^(-)?(\d{1,2}):(\d{2}):(\d{2})$")

Reader = Callable[[Any], Awaitable["float | None"]]


def _cfg(cfg, key: str):
    v = cfg.get(f"prime_pause.{key}") if cfg is not None and hasattr(cfg, "get") else None
    return PAUSE_DEFAULTS[key] if v is None else v


def hms(text: str) -> tuple[bool, float] | None:
    """'2:20:54' -> (False, 8454.0); '-0:01:05' -> (True, 65.0). Hours are required, so a scorebug game clock
    ('4:32') is never taken for the timecode."""
    m = _HMS.match(text.strip())
    if not m or int(m.group(3)) > 59 or int(m.group(4)) > 59:
        return None
    return bool(m.group(1)), int(m.group(2)) * 3600 + int(m.group(3)) * 60 + int(m.group(4))


def parse_timecode(obs: list[dict], band_y_min: float = PAUSE_DEFAULTS["band_y_min"]) -> float | None:
    """The overlay's elapsed timecode from OCR observations, or None. Only H:MM:SS in the lower band counts; a
    '-'-prefixed one (time left) is ignored; two different elapsed candidates = can't tell which: None."""
    found = set()
    for o in obs:
        if float(o.get("y", 1.0)) < band_y_min:
            continue
        got = hms(str(o.get("text", "")))
        if got and not got[0]:
            found.add(got[1])
    return found.pop() if len(found) == 1 else None


async def ocr_timecode(stick, timeout_s: float = 8.0, band_y_min: float = PAUSE_DEFAULTS["band_y_min"]) -> float | None:
    """Default reader: one screencap, local Vision OCR, the PNG deleted at once (frames read, then deleted)."""
    from redzone.screen_ocr import recognize
    d = tempfile.mkdtemp(prefix="rz-prime-")
    path = os.path.join(d, "s.png")
    try:
        try:
            if not await stick.screenshot(path):
                return None
        except asyncio.TimeoutError:
            return None
        res = await asyncio.to_thread(recognize, path, timeout=timeout_s)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
        try:
            os.rmdir(d)
        except OSError:
            pass
    if not res.read_ok:
        return None
    return parse_timecode([{"text": b.text, "x": b.x, "y": b.y} for b in res.observations], band_y_min)


@dataclass
class PauseResult(BufferResult):
    gained_s: float | None = None     # seconds this pause added behind live; `measured` = read on Prime's timecode.
    # behind_live_s = behind_before_s (the caller's number) + gained_s, so it is only as good as behind_before_s.


async def enter_buffer_pause(stick, seconds: float, *, cfg=None, pkg: str | None = None,
                             still_hidden: Callable[[], bool] | None = None,
                             read_timecode: Reader | None = None,
                             relink: Callable[[], Awaitable[bool]] | None = None,
                             behind_before_s: float = 0.0,
                             clock: Callable[[], float] | None = None) -> PauseResult:
    """Pause a hidden Prime stick `seconds` further behind live. See the module docstring. Never raises."""
    clock = clock or time.monotonic
    t0 = clock()
    res = PauseResult(ok=False, asked_s=round(float(seconds), 2))
    still_hidden = still_hidden or (lambda: True)
    if read_timecode is None:
        band = float(_cfg(cfg, "band_y_min"))

        async def read_timecode(st):
            return await ocr_timecode(st, band_y_min=band)
    try:
        await _enter(stick, float(seconds), cfg, pkg, still_hidden, read_timecode, relink,
                              float(behind_before_s), clock, res)
    except Exception as e:   # never raise into the switch path
        log.warning("enter_buffer_pause failed: %r", e)
        res.ok, res.reason = False, f"exception: {e!r}"[:160]
        if res.relinked is None and "PAUSE" in res.steps:
            await _relink(relink, still_hidden, res)
    res.took_s = clock() - t0
    return res


async def _read(stick, read_timecode, cfg) -> float | None:
    for i in range(int(_cfg(cfg, "read_tries"))):
        if i:
            await asyncio.sleep(float(_cfg(cfg, "overlay_wait_s")))
        got = await read_timecode(stick)
        if got is not None:
            return float(got)
    return None


async def _enter(stick, seconds, cfg, pkg, still_hidden, read_timecode, relink, behind_before, clock, res) -> bool:
    hold = min(max(seconds, 0.0), float(_cfg(cfg, "max_hold_s")))
    if hold < 1.0:
        res.ok, res.behind_live_s, res.gained_s, res.reason = True, behind_before, 0.0, "nothing to hold"
        return False
    # 1. preconditions: nothing pressed if any fails
    if not still_hidden():
        await _fail(res, "stick is not hidden", relink, still_hidden, pressed=False)
        return False
    front = await stick.foreground() or ""
    if not any(h in front for h in PRIME_PKG_HINTS) and not (pkg and pkg in front):
        await _fail(res, f"Prime is not in front ({front or 'unknown'})", relink, still_hidden, False)
        return False
    pb = await stick.playback(pkg)
    if pb.get("state") != "playing":
        await _fail(res, f"player is {pb.get('state')}, not playing", relink, still_hidden, False)
        return False
    # 2. pause, then the paused timecode
    if not await _keys(stick, "KEYCODE_MEDIA_PAUSE", 1, 0.0):
        await _fail(res, "PAUSE not sent", relink, still_hidden, pressed=True)
        return False
    w_pause = clock()
    res.steps.append("PAUSE")
    await asyncio.sleep(float(_cfg(cfg, "overlay_wait_s")))
    t_a = await _read(stick, read_timecode, cfg)
    if t_a is None:
        await _keys(stick, "KEYCODE_MEDIA_PLAY", 1, 0.0)
        await _fail(res, "timecode unreadable after the pause", relink, still_hidden, pressed=True)
        return True
    res.steps.append(f"paused at {t_a:.0f}s")
    # 3. hold, watching that the stick stays hidden
    poll = float(_cfg(cfg, "poll_s"))
    while clock() - w_pause < hold:
        if not still_hidden():
            await _keys(stick, "KEYCODE_MEDIA_PLAY", 1, 0.0)     # never leave a frozen picture on screen
            res.ok, res.reason = False, "stick became visible during the hold (un-paused, not re-linked)"
            res.steps.append("PLAY (visible)")
            res.relinked = False
            return True
        await asyncio.sleep(min(poll, max(0.0, hold - (clock() - w_pause))))
    # 4. still paused where we left it?
    t_p = await read_timecode(stick)
    if t_p is not None and abs(t_p - t_a) > float(_cfg(cfg, "hold_tol_s")):
        await _keys(stick, "KEYCODE_MEDIA_PLAY", 1, 0.0)
        await _fail(res, f"timecode moved during the hold ({t_a:.0f}s -> {t_p:.0f}s)", relink, still_hidden, True)
        return True
    if not still_hidden():
        await _keys(stick, "KEYCODE_MEDIA_PLAY", 1, 0.0)
        res.ok, res.reason, res.relinked = False, "stick became visible before PLAY (un-paused, not re-linked)", False
        return True
    # 5. play, then measure the gain on Prime's own timecode
    if not await _keys(stick, "KEYCODE_MEDIA_PLAY", 1, 0.0):
        await _fail(res, "PLAY not sent", relink, still_hidden, pressed=True)
        return True
    w_play = clock()
    res.committed = True
    res.steps.append(f"PLAY after {w_play - w_pause:.1f}s")
    end = clock() + float(_cfg(cfg, "settle_s"))
    playing = False
    while clock() < end:
        if (await stick.playback(pkg)).get("state") == "playing":
            playing = True
            break
        await asyncio.sleep(poll)
    if not playing:
        await _fail(res, "not playing after PLAY", relink, still_hidden, pressed=True)
        return True
    w_b = clock()                       # the screencap is taken at the start of a read (OCR then takes ~8 s)
    t_b = await read_timecode(stick)
    if t_b is None:
        res.gained_s = w_play - w_pause
        res.behind_live_s = behind_before + res.gained_s
        res.ok, res.reason = True, "estimated from the wall-clock hold (no timecode after PLAY)"
        return True
    gained = (w_b - w_pause) - (t_b - t_a)   # wall time since the pause minus content played since then
    if abs(gained - (w_play - w_pause)) > float(_cfg(cfg, "gain_tol_s")):
        await _fail(res, f"gained {gained:.1f}s, held {w_play - w_pause:.1f}s (snap?)", relink, still_hidden, True)
        return True
    res.gained_s, res.measured = gained, True
    res.behind_live_s = behind_before + gained
    res.steps.append(f"timecode {t_b:.0f}s: gained {gained:.1f}s")
    res.ok, res.reason = True, "gain measured on the Prime timecode"
    return True
