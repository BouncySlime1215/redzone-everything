"""[YTTV scrub buffer, item 1] Put a YouTube TV stick a set time behind live by scrubbing the player overlay.

Why: Nick's Nov 6 10:15 PM order item 1: put a hidden YouTube TV stick behind live by overlay scrubbing (decisions 2026-11-07, flag off).

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
  2. CENTER (a LIVE/title banner sits up for ~20 s after a tune: close and reopen until the CC button shows), then reads the screen (screencap -> local OCR -> the PNG deleted at once; numbers and labels only) and
     goes on only if the overlay is up with the PROGRESS BAR focused: a scrub label MM:SS above the knob and no row
     button (HD, CC, Networks, ...) holding focus. Anything else: fail closed;
  2b. the probe that puts focus on the bar and shows the label: at the live edge a RIGHT (probe_key, default) or a
     LEFT; neither moves the knob there (Nick, Nov 7: RIGHT 8/8, LEFT 6/6, same landings), so the label it shows is
     the start point. Behind live only a LEFT probe is used, and it is assumed to move one step;
  2c. the bar starts at the PROGRAM start (FOX 50 just after kickoff: label 5 s): LEFTs needing more than the label
      fail closed before any is pressed (program_start_guard), with room_s = the label;
  3. LEFT x N, then reads again: the scrub label must have moved back N x step_s (+- step_tol_s). Exactly one step
     short = a lost press: one more LEFT (topup_max), which must move one step. Otherwise: fail closed. A fixed
     `presses` count (the join plan's n) sends n - 1 LEFTs after the probe, the motion its predictor is fit to;
  4. CENTER commits, then the player must report PLAYING again (its state only, never its position);
  5. behind_live_s: from the label reader if one is given (item 2, the overlay time label vs the wall clock), else
     the estimate behind_before + N x step_s + the seconds the overlay was up, with `measured` False.
  The picture stands still while the overlay is up (Nick's 90 s run, Nov 7: asked 90, 6 LEFTs, 37.6 s -> 130.8 s
  behind), so N is planned after the probe from what is left: seconds - the probe's step (0 at the live edge) - time
  in the overlay so far - one more read. N is capped by max_back_s / step_s.
"Fail closed" = no commit; the stick is re-linked to the live edge by the caller-supplied relink() (a deep link
lands at live +-0.5 s, but only from ANOTHER channel: #391 behind_on), and the result says why. The hidden check runs before every key batch: a stick that
became visible is never pressed again, and it is not re-linked either (that would change the screen).

Speed flags (build plan WP-03, both off; a read is the slowest part of the time the picture stands still):
  * yttv_seek.skip_open_read drops the first read of step 2, only when a RIGHT probe at the live edge comes next: a
    RIGHT moves nothing there and the read after it still checks focus. Until that read has shown the overlay, no LEFT
    and no commit is pressed. The knob read of step 3 stays: it is the one that catches a lost press before the commit.
  * yttv_seek.overlay_band_from captures the screen from that fraction down (Stick.screencap_band: no PNG encode on
    the stick). Only y <= OVERLAY_BAND_MAX is honoured, so every MM:SS candidate stays in view of the one-label rule.
BufferResult.reads holds one {"what", "s"} per screen read: open, probe, knob, topup, label.

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
from functools import partial
from typing import Any, Awaitable, Callable

log = logging.getLogger("redzone.yttv_seek")

YTTV_PKG_HINT = "youtube.tv"
SCRUB_DEFAULTS = {"enabled": False, "step_s": 15.0, "max_back_s": 150.0, "step_tol_s": 6.0,
                  "key_gap_s": 0.35, "open_tries": 4, "probe_key": "RIGHT", "topup_max": 1, "topup_tol_s": 2.0, "boundary_retries": 1, "reopen_wait_s": 1.5, "settle_s": 4.0, "poll_s": 0.25, "overlay_wait_s": 1.3,
                  "program_start_guard": True, "skip_open_read": False, "overlay_band_from": None}

# The player row under the bar (#356 ROW_BUTTON labels seen so far). Focus on any of these = NOT the bar.
ROW_BUTTONS = ("hd", "cc", "networks", "multiview", "episodes", "more to watch", "stats", "key plays", "scores",
               "live chat", "info")
_MMSS = re.compile(r"^(?:(\d{1,2}):)?(\d{1,2}):(\d{2})$")
_WIN = re.compile(r"(\d{1,2}:\d{2})\s*([AaPp][Mm])\s*[-–—~]\s*(\d{1,2}:\d{2})\s*([AaPp][Mm])")

# parse_overlay takes MM:SS labels from y > 0.3 and needs exactly one. A band starting lower could hide one of two
# candidates and make the other look like the knob's, so a later start is ignored (full frame).
OVERLAY_BAND_MAX = 0.30

Observation = dict          # {"text", "x", "y", "w", "h"} normalized top-left, as screen_ocr gives
Reader = Callable[[Any], Awaitable["OverlayRead | None"]]


def _cfg(cfg, key: str):
    v = cfg.get(f"yttv_seek.{key}") if cfg is not None and hasattr(cfg, "get") else None
    return SCRUB_DEFAULTS[key] if v is None else v


def overlay_band(cfg) -> float | None:
    """The configured band start as a screen fraction, or None for a full frame (unset, invalid, or past the limit)."""
    v = _cfg(cfg, "overlay_band_from")
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if 0.0 < f <= OVERLAY_BAND_MAX else None


async def band_png(stick, band_from: float | None) -> bytes:
    """The overlay band as a full-size PNG, or b'' when no band is asked for, the stick has no band capture, or it came
    back empty: the caller then takes a full frame. A timeout is not caught here: it stays a failed read."""
    grab = getattr(stick, "screencap_band", None)
    if band_from is None or grab is None:
        return b""
    return await grab(band_from) or b""


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


async def ocr_overlay(stick, timeout_s: float = 8.0, band_from: float | None = None) -> OverlayRead | None:
    """Default reader: one screencap, read by the Mac's local Vision OCR, the PNG deleted at once (Nick's rule:
    frames read, then deleted). None when it can't be read (off macOS, screencap failed, timeout)."""
    from redzone.screen_ocr import recognize
    d = tempfile.mkdtemp(prefix="rz-scrub-")
    path = os.path.join(d, "s.png")
    try:
        try:
            png = await band_png(stick, band_from)
            if png:
                with open(path, "wb") as f:
                    f.write(png)
            elif not await stick.screenshot(path):
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
    retries: int = 0                    # program-boundary retries from the live edge (boundary_retries)
    room_s: float | None = None         # the label where the bar was focused: how far back the bar can go (FOX, Nov 7)
    reads: list[dict] = field(default_factory=list)   # one {"what", "s"} per screen read, across boundary retries

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
                       clock: Callable[[], float] | None = None,
                       presses: int | None = None) -> BufferResult:
    """Scrub a hidden YouTube TV stick `seconds` behind where it plays now. See the module docstring. Never raises.
    presses: send exactly this many LEFTs (the probe included, capped by max_back_s) instead of planning them from the
    time in the overlay: the join API's measured n = round((T - 10) / 15) (redzone/tuners/join.py)."""
    clock = clock or time.monotonic
    t0 = clock()
    res = BufferResult(ok=False, asked_s=round(float(seconds), 2))
    if read_overlay is None:
        band = overlay_band(cfg)
        read_overlay = ocr_overlay if band is None else partial(ocr_overlay, band_from=band)
    still_hidden = still_hidden or (lambda: True)
    try:
        await _enter(stick, float(seconds), cfg, pkg, still_hidden, read_overlay, read_behind, relink,
                     float(behind_before_s), res, clock, presses)
        # [program boundary, Nick Nov 7 ~1:00 AM] the overlay label resets at a new program, so the knob check can read
        # "moved 39s, not 60s" with nothing wrong. That already failed closed; once the stick is re-linked to the live
        # edge (a known position, nothing committed), try the scrub again from the edge, at most boundary_retries times.
        tries = int(_cfg(cfg, "boundary_retries"))
        while (tries > 0 and not res.ok and not res.committed and res.relinked is True
               and res.reason.startswith("knob moved") and still_hidden()):
            tries -= 1
            res.retries += 1
            prior = list(res.steps)
            res.ok, res.reason, res.presses, res.relinked, res.steps = False, "", 0, None, []
            await _enter(stick, float(seconds), cfg, pkg, still_hidden, read_overlay, read_behind, relink,
                         0.0, res, clock, presses)
            res.steps = prior + ["retry from the live edge (program boundary?)"] + res.steps
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


def _timed(read, res: BufferResult):
    """`read` that also adds its duration to res.reads. perf_counter, not the injected clock: timing must not shift the
    time-in-overlay plan."""
    async def timed(stick, what: str):
        t0 = time.perf_counter()
        try:
            return await read(stick)
        finally:
            res.reads.append({"what": what, "s": round(time.perf_counter() - t0, 3)})
    return timed


async def _enter(stick, seconds, cfg, pkg, still_hidden, read_overlay, read_behind, relink, behind_before, res,
                 clock=time.monotonic, fixed_n=None):
    read = _timed(read_overlay, res)
    step = float(_cfg(cfg, "step_s"))
    gap = float(_cfg(cfg, "key_gap_s"))
    n = int(round(min(max(0.0, seconds), float(_cfg(cfg, "max_back_s"))) / step))
    if fixed_n is not None:
        n = max(0, min(int(fixed_n), int(float(_cfg(cfg, "max_back_s")) // step)))
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
    t_open = clock()   # [Nick's 90 s run, Nov 7] the picture stands still while the overlay is up: every second
    res.steps.append("CENTER")   # from here to the commit adds a second behind live (asked 90, took 37.6 -> 130.8)
    await asyncio.sleep(float(_cfg(cfg, "overlay_wait_s")))
    # Live finding (Nov 6): at the live edge the overlay shows NO scrub label until the first LEFT, so the first
    # press is the probe. It only moves an uncommitted knob, and it is pressed only with the overlay up and no row
    # button focused. The label it reveals must then be there, or we fail closed before any commit.
    # Live findings (Nov 6): after a tune a LIVE/title banner can sit up for 25+ s and looks like the overlay (it even
    # has the CC button), but keys do nothing there. The scrub label appears only after a LEFT lands on the bar, so
    # LEFT is the probe: no label means nothing moved, so close, reopen and probe again. Fail closed after open_tries.
    before = None
    # Steps the probe itself moved the knob. [Nick's 12f405ab runs, Nov 7: RIGHT 8/8 and LEFT 6/6 landed the same]
    # at the live edge NEITHER probe moves it (a LEFT there only reveals the label); behind live a LEFT probe is
    # still assumed to move one step (unmeasured).
    probe_moved = 1
    left_probe = True
    at_live = behind_before < step / 2
    blind_closes = 0      # CENTERs sent after a LEFT whose result we could not read: each may have committed a step
    right_probe = at_live and str(_cfg(cfg, "probe_key")).upper() == "RIGHT"
    skip_open = right_probe and bool(_cfg(cfg, "skip_open_read"))
    for attempt in range(int(_cfg(cfg, "open_tries"))):
        skip_read = skip_open and attempt == 0   # only the first try: a retry already found something off
        opened = None if skip_read else await read(stick, "open")
        if opened is not None and opened.row_focused:
            return await _fail(res, "focus guard: a row button has focus", relink, still_hidden, pressed=True)
        if not still_hidden():
            return await _fail(res, "stick became visible before the scrub", relink, still_hidden, pressed=True)
        if skip_read:
            res.steps.append("open read skipped: the RIGHT probe's read checks focus")
        if skip_read or (opened is not None and opened.overlay):
            # [lost press, Nick's canary Nov 7: 3 presses, landed one 15 s step short, ok:true] A LEFT probe's own
            # step can't be checked (no label before it). At the live edge a RIGHT probe lands on the bar without
            # moving the knob (it can't go past live), so the label it shows is the start point and EVERY LEFT is
            # checked. No label after it: the LEFT probe, as before.
            if right_probe:
                if not await _keys(stick, "KEYCODE_DPAD_RIGHT", 1, 0.0):
                    return await _fail(res, "RIGHT not sent", relink, still_hidden, pressed=True)
                await asyncio.sleep(float(_cfg(cfg, "overlay_wait_s")))
                r0 = clock()
                before = await read(stick, "probe")
                read_cost = clock() - r0
                if before is not None and before.bar_focused:
                    probe_moved, left_probe = 0, False
                    res.steps.append("RIGHT probe (knob at the live edge)")
                    break
                if before is not None and before.row_focused:
                    return await _fail(res, "focus guard: a row button has focus", relink, still_hidden, pressed=True)
                if skip_read:
                    # This read stands in for the open read: nothing below it is pressed on an overlay nobody saw.
                    if not still_hidden():
                        return await _fail(res, "stick became visible before the scrub", relink, still_hidden,
                                           pressed=True)
                    if before is None:   # focus was never seen: no LEFT, no closing CENTER (#356)
                        return await _fail(res, "focus guard: no read after the RIGHT probe", relink, still_hidden,
                                           pressed=True)
                    if not before.overlay:   # as an open read that finds no overlay: reopen and read first
                        before = None
                        await _keys(stick, "KEYCODE_DPAD_CENTER", 1, 0.0)
                        await asyncio.sleep(float(_cfg(cfg, "overlay_wait_s")))
                        continue
            if not await _keys(stick, "KEYCODE_DPAD_LEFT", 1, 0.0):
                return await _fail(res, "LEFT not sent", relink, still_hidden, pressed=True)
            res.presses = 1
            await asyncio.sleep(float(_cfg(cfg, "overlay_wait_s")))
            r0 = clock()
            before = await read(stick, "probe")
            read_cost = clock() - r0
            if before is not None and before.bar_focused:
                probe_moved, left_probe = (0 if at_live else 1), True
                break
            before = None
            res.presses = 0
            blind_closes += 1
            await _keys(stick, "KEYCODE_DPAD_CENTER", 1, 0.0)
            await asyncio.sleep(float(_cfg(cfg, "reopen_wait_s")))
        await _keys(stick, "KEYCODE_DPAD_CENTER", 1, 0.0)
        await asyncio.sleep(float(_cfg(cfg, "overlay_wait_s")))
    if before is None:
        return await _fail(res, "focus guard: no scrub label after the LEFT", relink, still_hidden, pressed=True)
    res.steps.append(f"bar focused at {before.scrub_s:.0f}s")
    # Plan the rest so that LEFTs + time in the overlay land on the target: the probe gave one step, the overlay
    # has been up (now - t_open), and the check read still to come costs about what the probe read did.
    left_s = seconds - probe_moved * step - (clock() - t_open) - read_cost - float(_cfg(cfg, "overlay_wait_s"))
    if fixed_n is None:
        n = probe_moved + max(0, min(n - probe_moved, int(round(left_s / (step + gap)))))
    res.steps.append(f"plan: {n} LEFT(s) ({clock() - t_open:.1f}s in the overlay so far)")
    # 3. the other LEFTs, then the label must have moved back that many steps
    if not still_hidden():
        return await _fail(res, "stick became visible before the scrub", relink, still_hidden, pressed=True)
    rest = n - probe_moved
    if fixed_n is not None:
        # A fixed count n is the join plan's presses = k + 1 (k = JoinPlan.lefts, fit on real LEFTs after the probe,
        # Nick's depth sweep Nov 7): (n - 1) LEFTs whichever probe ran (at live neither probe moves the knob).
        rest = max(0, n - 1)
    # [Nick's FOX 50 joins, Nov 7 ~12:05 ET, right after kickoff] the bar starts at the PROGRAM start: label 5 s at
    # the live edge, 'knob moved 5s, not 30s (5s -> 0s)'. The scrub can't go back further than the label, so when
    # the LEFTs need more than that, fail closed now (nothing seeks; the stick goes back to live) and say how much
    # room there was (room_s) so a caller can aim shallower or try later. Not a "knob moved" reason: a retry from
    # the edge a few seconds later has the same problem.
    res.room_s = round(float(before.scrub_s), 1)
    need = rest * step
    if bool(_cfg(cfg, "program_start_guard")) and rest > 0 and need > before.scrub_s + float(_cfg(cfg, "topup_tol_s")):
        return await _fail(res, f"program start: the bar has {before.scrub_s:.0f}s, the scrub needs {need:.0f}s",
                           relink, still_hidden, pressed=True)
    if rest > 0 and not await _keys(stick, "KEYCODE_DPAD_LEFT", rest, gap):
        res.presses = rest + int(left_probe)
        return await _fail(res, "LEFT not sent", relink, still_hidden, pressed=True)
    res.presses = rest + int(left_probe)
    res.steps.append(f"LEFT x{rest} after the {'LEFT' if left_probe else 'RIGHT'} probe")
    await asyncio.sleep(float(_cfg(cfg, "overlay_wait_s")))
    after = await read(stick, "knob")
    want = rest * step
    tol = float(_cfg(cfg, "step_tol_s"))
    if after is None or not after.bar_focused or after.scrub_s is None:
        return await _fail(res, "scrub label lost after the LEFTs", relink, still_hidden, pressed=True)
    moved = before.scrub_s - after.scrub_s
    # A LEFT that never landed leaves the knob whole steps short: top it up (one LEFT per missing step, at most
    # topup_max), read again, and only then commit. Anything else off: fail closed as before.
    tight = float(_cfg(cfg, "topup_tol_s"))
    for _ in range(int(_cfg(cfg, "topup_max"))):
        # only a knob exactly ONE step short is a lost press; anything else (a 10 s app, a label reset at a program
        # boundary, two lost presses) fails closed below, as before
        if abs((want - moved) - step) > tight:
            break
        was = moved
        if not still_hidden():
            return await _fail(res, "stick became visible before the top-up", relink, still_hidden, pressed=True)
        if not await _keys(stick, "KEYCODE_DPAD_LEFT", 1, 0.0):
            return await _fail(res, "LEFT not sent", relink, still_hidden, pressed=True)
        res.presses += 1
        res.steps.append(f"lost press: knob moved {moved:.0f}s of {want:.0f}s, one more LEFT")
        await asyncio.sleep(float(_cfg(cfg, "overlay_wait_s")))
        after = await read(stick, "topup")
        if after is None or not after.bar_focused or after.scrub_s is None:
            return await _fail(res, "scrub label lost after the top-up", relink, still_hidden, pressed=True)
        moved = before.scrub_s - after.scrub_s
        if abs((moved - was) - step) > tight:    # the extra LEFT itself must move exactly one step
            return await _fail(res, f"knob moved {moved:.0f}s, not {want:.0f}s, and the top-up {moved - was:.0f}s "
                               f"({before.scrub_s:.0f}s -> {after.scrub_s:.0f}s)", relink, still_hidden, pressed=True)
    if abs(moved - want) > tol:
        # the labels go in the reason for a trace (Nick's #403 mode A: "knob moved 0s, not 45s" 7 of 10)
        return await _fail(res, f"knob moved {moved:.0f}s, not {want:.0f}s ({before.scrub_s:.0f}s -> "
                           f"{after.scrub_s:.0f}s)", relink, still_hidden, pressed=True)
    res.steps.append(f"knob {after.scrub_s:.0f}s (moved {moved:.0f}s)")
    # 4. commit, then it must play again (state only: the position is wrong after a seek)
    if not still_hidden():
        return await _fail(res, "stick became visible before the commit", relink, still_hidden, pressed=True)
    if not await _keys(stick, "KEYCODE_DPAD_CENTER", 1, 0.0):
        return await _fail(res, "commit not sent", relink, still_hidden, pressed=True)
    res.committed = True
    frozen = clock() - t_open
    res.steps.append(f"CENTER (commit) after {frozen:.1f}s in the overlay")
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
    res.behind_live_s = behind_before + moved + probe_moved * step + frozen
    if read_behind is not None:
        try:
            got = await _timed(read_behind, res)(stick, "label")
        except Exception as e:
            log.warning("label read after the scrub failed: %r", e)
            got = None
        if got is not None:
            res.behind_live_s, res.measured = float(got), True
            res.steps.append(f"label: {got:.1f}s behind live")
    if blind_closes and not res.measured:
        # A probe LEFT may have landed on the bar with the label missed by OCR; the CENTER that closed it then
        # committed up to blind_closes x step_s we did not count. Without a label read the delay is unknown: the
        # caller must not flip on a guess (Rule #1), so report None rather than an estimate that may be short.
        res.behind_live_s = None
        res.steps.append(f"{blind_closes} blind close(s): up to {blind_closes * step:.0f}s uncounted")
        res.ok = True
        res.reason = "behind-live unknown: a probe close may have committed; needs a label read"
        return res
    res.ok = True
    res.reason = "measured by the label" if res.measured else "estimated from the knob (no label read)"
