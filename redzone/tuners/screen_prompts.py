"""Clear YouTube TV's own prompts from a screenshot: "Who's watching?" and "Still watching?".

Why: YouTube TV draws its UI itself and uiautomator reads nothing there, so its prompts are cleared from a screenshot with local OCR (docstring).

YouTube TV (Cobalt) draws its UI itself, so uiautomator reads nothing there, but `adb screencap`
shows it (only the video is DRM-black). So: one screenshot, the Mac's local Vision OCR for the
words and where they are, and the screenshot's pixels for which item is lit up. Focused items on
YouTube TV are drawn bright (white name, white button); the rest are dimmed.

What it will do, and nothing else:
- Profile picker: find the configured name (providers.profiles.youtubetv) in the row of names,
  find the ONE name that is clearly brighter than the others (the focused one), press DPAD
  left/right one step at a time, re-reading the screen after every step, and press CENTER only
  when the lit-up name IS the configured one. A profile marked "Signed out" / "Sign in" is never
  picked. Anything unclear (name missing or twice, no clear focus, focus didn't move, a payment
  screen) stops with ProfileSelectionRequired, without a guessed key.
- "Are you still watching?" / "Still there?": CENTER, but only when no "No"/"Exit"-style button is
  showing, or the pixels show the "yes"-style button is the lit-up one.
- Only while the YouTube TV app itself is in front: Fire OS has its own "Who's watching?"
  (Amazon profiles) after a wake, and that is not this app's picker.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import struct
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from redzone.discovery.providers import classify_screen, is_purchase_screen, normalize_text
from redzone.screen_ocr import OCRResult, TextBox
from redzone.tuners.profiles import ProfileSelectionRequired

log = logging.getLogger(__name__)

PICKER = ("who's watching", "who is watching", "choose an account", "choose a profile")
STILL = ("are you still watching", "still watching?", "still there")
SIGNED_OUT = ("signed out", "sign in", "signed-out", "sign back in", "session expired")
YES = ("yes", "continue watching", "keep watching", "i'm still here", "i'm here", "continue",
       "resume", "still watching", "yes, i'm here")
NO = ("no", "exit", "stop", "stop watching", "not now", "sign out", "back to home")
NOT_A_PROFILE = ("add account", "add profile", "manage", "edit", "guest", "settings")

MAX_STEPS = 8            # DPAD presses on one picker, at most (4 profiles + extras)
NAME_MIN_CONF = 0.6      # OCR confidence for a name we act on
LIT_MIN = 170.0          # the focused name's brightest ink (0-255)
LIT_MARGIN = 35.0        # ...must beat every other name by this much


@dataclass
class Frame:
    """One screenshot: the OCR result and its luminance (rows x cols, 0-255), if decodable."""
    ocr: OCRResult
    luma: object | None = None      # numpy array; None = pixels unavailable (focus unknown)
    package: str = ""               # foreground package when it was taken
    notes: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return normalize_text(" | ".join(r.text for r in self.ocr.observations))


Reader = Callable[[object], Awaitable[Frame]]


# --- pixels --------------------------------------------------------------------------

def bmp_luma(data: bytes):
    """Luminance of an uncompressed 24/32-bit BMP (what `sips -s format bmp` writes), or None."""
    import numpy as np
    if len(data) < 54 or data[:2] != b"BM":
        return None
    offset = struct.unpack_from("<I", data, 10)[0]
    width, height = struct.unpack_from("<ii", data, 18)
    bpp, compression = struct.unpack_from("<HI", data, 28)
    if bpp not in (24, 32) or compression not in (0, 3) or width <= 0 or height == 0:
        return None
    rows, step = abs(height), bpp // 8
    stride = (width * step + 3) & ~3
    if offset + stride * rows > len(data):
        return None
    px = np.frombuffer(data, np.uint8, stride * rows, offset).reshape(rows, stride)[:, :width * step]
    px = px.reshape(rows, width, step).astype(np.float32)
    luma = 0.0722 * px[..., 0] + 0.7152 * px[..., 1] + 0.2126 * px[..., 2]   # stored B, G, R
    return luma[::-1] if height > 0 else luma     # positive height = bottom-up rows


def png_luma(path: str):
    """Decode a PNG to luminance with macOS `sips` (no image library needed). None on failure."""
    try:
        with tempfile.TemporaryDirectory(prefix="rz-px-") as d:
            out = os.path.join(d, "screen.bmp")
            r = subprocess.run(["/usr/bin/sips", "-s", "format", "bmp", path, "--out", out],
                               capture_output=True, timeout=10, check=False)
            if r.returncode or not os.path.exists(out):
                return None
            with open(out, "rb") as f:
                return bmp_luma(f.read())
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def ink(luma, box: TextBox, *, pct: float = 95) -> float | None:
    """How bright a text box's brightest strokes are (the pct-th percentile luminance)."""
    import numpy as np
    if luma is None:
        return None
    h, w = luma.shape[:2]
    x0, x1 = int(box.x * w), int(min(1.0, box.x + box.width) * w + 0.999)
    y0, y1 = int(box.y * h), int(min(1.0, box.y + box.height) * h + 0.999)
    region = luma[max(0, y0):min(h, y1), max(0, x0):min(w, x1)]
    return float(np.percentile(region, pct)) if region.size else None


def lit(luma, boxes: list[TextBox], *, pct: float = 95) -> int | None:
    """Index of the ONE box drawn clearly brighter than every other, else None."""
    if luma is None or len(boxes) < 2:
        return None
    levels = [ink(luma, b, pct=pct) for b in boxes]
    if any(v is None for v in levels):
        return None
    order = sorted(range(len(boxes)), key=lambda i: levels[i], reverse=True)
    top, second = levels[order[0]], levels[order[1]]
    return order[0] if top >= LIT_MIN and top - second >= LIT_MARGIN else None


# --- capture -------------------------------------------------------------------------

async def read_frame(stick) -> Frame:
    """Screenshot the stick (read-only: screencap never changes the screen), OCR it, and keep its
    luminance. Errors give an empty frame: no evidence, so nothing is pressed."""
    from redzone.screen_ocr import recognize
    package = ""
    try:
        package = (await stick.foreground()).split("/")[0]
    except Exception:
        log.debug("foreground read failed", exc_info=True)
    with tempfile.TemporaryDirectory(prefix="rz-prompt-") as d:
        path = os.path.join(d, "screen.png")
        try:
            if not await stick.screenshot(path):
                return Frame(OCRResult("error", error="screencap failed"), package=package)
        except Exception:
            log.debug("screencap failed", exc_info=True)
            return Frame(OCRResult("error", error="screencap failed"), package=package)
        result = await asyncio.to_thread(recognize, path, timeout=8.0)
        luma = await asyncio.to_thread(png_luma, path) if result.read_ok else None
    return Frame(result, luma, package)


# --- reading a frame -----------------------------------------------------------------

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9' ]+", " ", normalize_text(s)).strip()


def _center(b: TextBox) -> tuple[float, float]:
    return b.x + b.width / 2, b.y + b.height / 2


def is_picker(frame: Frame) -> bool:
    return any(p in frame.text for p in PICKER)


def is_still_watching(frame: Frame) -> bool:
    return any(p in frame.text for p in STILL)


def _marks(rows: list[TextBox]) -> list[TextBox]:
    return [r for r in rows if any(m in normalize_text(r.text) for m in SIGNED_OUT)]


def profile_row(frame: Frame, wanted: str) -> tuple[list[TextBox], int, list[TextBox]]:
    """(names left to right, index of `wanted`, signed-out marks). Raises ValueError if unclear."""
    rows = list(frame.ocr.observations)
    heading = next((r for r in rows if any(p in normalize_text(r.text) for p in PICKER)), None)
    if heading is None:
        raise ValueError("a profile picker is no longer identifiable")
    marks = _marks(rows)
    below = [r for r in rows if r.y >= heading.y + heading.height and r not in marks
             and "@" not in r.text and len(r.text) <= 40]
    hits = [r for r in below if _norm(r.text) == wanted]
    if len(hits) != 1:
        raise ValueError("the configured profile is missing or appears more than once")
    target = hits[0]
    cy = _center(target)[1]
    tol = max(0.025, target.height)
    band = [r for r in below if abs(_center(r)[1] - cy) <= tol]
    # Two rows in one column (a name and its email/status line) can't both be tiles.
    band.sort(key=lambda r: (r.x, r.y))
    names: list[TextBox] = []
    for r in band:
        if names and r.x < names[-1].x + names[-1].width and r.x + r.width > names[-1].x:
            raise ValueError("profile names overlap")
        names.append(r)
    if any(r.confidence < NAME_MIN_CONF for r in names):
        raise ValueError("a profile name was read with low confidence")
    return names, names.index(target), marks


def signed_out(name: TextBox, marks: list[TextBox]) -> bool:
    """A "Signed out" / "Sign in" line directly under (or on) this name's tile."""
    cx = _center(name)[0]
    for m in marks:
        mx, my = _center(m)
        if abs(mx - cx) <= max(0.06, name.width) and -name.height <= my - (name.y + name.height) <= 0.09:
            return True
    return False


def _unmarked_text(frame: Frame, marks: list[TextBox]) -> str:
    return normalize_text(" | ".join(r.text for r in frame.ocr.observations if r not in marks))


# --- acting ----------------------------------------------------------------------------

async def select_profile_ocr(stick, configured: str, steps: list[str], before_key=None,
                             *, package: str = "", read: Reader | None = None,
                             frame: Frame | None = None) -> bool | None:
    """None: no picker on screen. True: CENTER sent on the configured, lit-up, signed-in profile.
    Raises ProfileSelectionRequired when the evidence isn't clear enough to press anything.
    `before_key` runs right before each key (the tuner's live-play guard)."""
    def stop(reason):
        steps.append(f"profile selection (screenshot) stopped: {reason}")
        raise ProfileSelectionRequired(reason, steps)

    read = read or read_frame
    frame = frame or await read(stick)
    if not is_picker(frame):
        return None
    wanted = _norm(configured)
    if not wanted:
        stop("no preferred profile configured")
    seen: dict[int, int] = {}
    for _ in range(MAX_STEPS + 1):
        if package and frame.package and frame.package != package:
            stop(f"the app in front is {frame.package}, not {package}")
        if not is_picker(frame):
            stop("a profile picker is no longer identifiable")
        try:
            names, want, marks = profile_row(frame, wanted)
        except ValueError as exc:
            stop(str(exc))
        rest = _unmarked_text(frame, marks)
        verdict = classify_screen(rest)
        if is_purchase_screen(rest) or (verdict and verdict.stops):
            stop("account, payment, or unavailable-content screen")
        if signed_out(names[want], marks):
            stop(f"'{configured}' is signed out on this stick: sign it in on the TV")
        if wanted in NOT_A_PROFILE:
            stop("the configured name is an account-management entry")
        focus = lit(frame.luma, names)
        if focus is None:
            stop("can't tell which profile is highlighted")
        if focus == want:
            key = "KEYCODE_DPAD_CENTER"
        else:
            seen[focus] = seen.get(focus, 0) + 1
            if seen[focus] > 1:
                stop("profile focus did not move")
            key = "KEYCODE_DPAD_RIGHT" if want > focus else "KEYCODE_DPAD_LEFT"
        if before_key is not None:
            before_key()
        try:
            sent = await stick.key(key)
        except Exception:
            stop("profile key delivery is uncertain")
        if not getattr(sent, "ok", False):
            stop("profile key delivery is unconfirmed")
        if key == "KEYCODE_DPAD_CENTER":
            steps.append(f"profile selection sent: {configured} (highlighted in a screenshot; "
                         "playback still unconfirmed)")
            return True
        steps.append(f"profile navigation (screenshot): {key.removeprefix('KEYCODE_')} "
                     f"from {names[focus].text} toward {names[want].text}")
        frame = await read(stick)
    stop("profile navigation did not reach the configured name")


def _buttons(frame: Frame, words: tuple[str, ...]) -> list[TextBox]:
    return [r for r in frame.ocr.observations if _norm(r.text) in words]


async def clear_still_watching(stick, steps: list[str], before_key=None, *, package: str = "",
                               read: Reader | None = None, frame: Frame | None = None) -> bool:
    """CENTER on an "Are you still watching?" / "Still there?" check when the yes-button is safe."""
    read = read or read_frame
    frame = frame or await read(stick)
    if not is_still_watching(frame):
        return False
    if package and frame.package and frame.package != package:
        steps.append(f"still-watching prompt left alone: {frame.package} is in front, not {package}")
        return False
    if is_purchase_screen(frame.text) or ((v := classify_screen(frame.text)) and v.stops):
        steps.append("still-watching prompt left alone: account, payment, or unavailable-content screen")
        return False
    yes, no = _buttons(frame, YES), _buttons(frame, NO)
    if no:
        if len(yes) != 1:
            steps.append("still-watching prompt left alone: no single 'yes' button to press")
            return False
        # A focused button is a bright fill: compare each button's typical (median) pixel.
        if lit(frame.luma, yes + no, pct=50) != 0:
            steps.append("still-watching prompt left alone: can't see that 'yes' is highlighted")
            return False
    if before_key is not None:
        before_key()
    sent = await stick.key("KEYCODE_DPAD_CENTER")
    if not getattr(sent, "ok", False):
        steps.append("still-watching: CENTER delivery unconfirmed")
        return False
    steps.append("cleared prompt (screenshot): still watching")
    return True


async def clear_prompt(stick, configured: str, steps: list[str], before_key=None, *,
                       package: str = "", read: Reader | None = None) -> str | None:
    """One screenshot; clear whichever YouTube TV prompt it shows. Returns "profile",
    "still_watching", or None (nothing to clear). Raises ProfileSelectionRequired like
    select_profile_ocr."""
    frame = await (read or read_frame)(stick)
    if not frame.ocr.read_ok:
        return None
    if is_picker(frame):
        return "profile" if await select_profile_ocr(stick, configured, steps, before_key,
                                                     package=package, read=read, frame=frame) else None
    if await clear_still_watching(stick, steps, before_key, package=package, read=read, frame=frame):
        return "still_watching"
    return None


# --- dry run on the hardware -------------------------------------------------------------

def describe(frame: Frame, configured: str) -> dict:
    """What clear_prompt would do with this frame, without pressing anything."""
    out: dict = {"ocr": frame.ocr.status, "package": frame.package, "pixels": frame.luma is not None,
                 "text": frame.text[:300]}
    if is_picker(frame):
        out["prompt"] = "profile"
        try:
            names, want, marks = profile_row(frame, _norm(configured))
        except ValueError as exc:
            out["stop"] = str(exc)
            return out
        focus = lit(frame.luma, names)
        out_signed = signed_out(names[want], marks)
        out.update(names=[n.text for n in names], want=names[want].text, signed_out=out_signed,
                   ink=[None if frame.luma is None else round(ink(frame.luma, n) or 0) for n in names],
                   focused=None if focus is None else names[focus].text,
                   would_press=None if focus is None or out_signed else
                   "CENTER" if focus == want else "RIGHT" if want > focus else "LEFT")
    elif is_still_watching(frame):
        out["prompt"] = "still_watching"
        out["buttons"] = {"yes": [b.text for b in _buttons(frame, YES)],
                          "no": [b.text for b in _buttons(frame, NO)]}
    return out


async def _probe(host: str, configured: str, png: str | None) -> dict:
    from redzone.screen_ocr import recognize
    from redzone.tuners.adb import Stick
    if png:
        result = await asyncio.to_thread(recognize, png, timeout=8.0)
        return describe(Frame(result, await asyncio.to_thread(png_luma, png)), configured)
    stick = Stick(host)
    try:
        return describe(await read_frame(stick), configured)
    finally:
        await stick.close()


def main() -> None:
    """Read-only: `python -m redzone.tuners.screen_prompts 192.168.x.x:5555 --profile Serge`
    screenshots the stick and prints what would be pressed. It never sends a key."""
    import argparse
    import json
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("host", nargs="?", default="")
    parser.add_argument("--profile", default="")
    parser.add_argument("--png", help="analyze a saved screencap instead of a live stick")
    args = parser.parse_args()
    if not (args.host or args.png):
        parser.error("give a stick host or --png")
    print(json.dumps(asyncio.run(_probe(args.host, args.profile, args.png)), indent=2))


if __name__ == "__main__":
    main()
