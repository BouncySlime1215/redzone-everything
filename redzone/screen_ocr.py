"""Read local PNG menu text with Apple Vision; never capture or control a device.

Coordinates are normalized with a top-left origin. Text/confidence describes OCR,
not focused controls, entitlement, playback, or the identity of the playing game.
Focus boxes are geometric UI candidates, not an accessibility focus assertion;
their confidence is edge/fill coverage, not a probability of correct selection.
Only one validated card outline or exact replay-action button is exposed. Callers
must separately verify the expected title/date and reject account/purchase flows.
No image or recognized text is uploaded. Empty/failed reads provide no evidence.

CLI: python -m redzone.screen_ocr /path/to/menu.png
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, replace
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import subprocess
import tempfile
import time


@dataclass(frozen=True)
class TextBox:
    text: str
    confidence: float
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class FocusBox:
    x: float
    y: float
    width: float
    height: float
    confidence: float
    text: str = ""
    source: str = "neutral_rectangle_border"


@dataclass(frozen=True)
class CaptionEvidence:
    status: str
    text: str = ""
    observations: tuple[TextBox, ...] = ()
    source: str = "below_focus_geometry"


@dataclass(frozen=True)
class OCRPreparation:
    status: str
    error: str = ""
    elapsed_s: float = 0.0
    helper_path: str = ""


@dataclass(frozen=True)
class OCRResult:
    status: str
    observations: tuple[TextBox, ...] = ()
    width: int = 0
    height: int = 0
    error: str = ""
    elapsed_s: float = 0.0
    source: str = "apple_vision_local"
    coordinate_system: str = "normalized_top_left"
    focus_boxes: tuple[FocusBox, ...] = ()
    focus_status: str = "none"

    @property
    def text(self) -> str:
        return "\n".join(row.text for row in self.observations)

    @property
    def read_ok(self) -> bool:
        return self.status == "ok" and bool(self.observations)


def focus_caption(result: OCRResult, focus: FocusBox | None = None, *,
                  expected_title: str | None = None) -> CaptionEvidence:
    """Associate a tight left-aligned caption immediately below one thumbnail.

    This is spatial evidence only. Relative recording ages stay relative, clipped
    names stay clipped, and neighboring captions are never used to complete them.
    A single line is allowed only with an explicit, exact expected_title. This is
    intended for a separately validated team-catalog tile, not playback approval.
    """
    if not result.read_ok or result.focus_status != "unique" or len(result.focus_boxes) != 1:
        return CaptionEvidence("ambiguous" if result.focus_status == "ambiguous" else "none")
    focus = focus or result.focus_boxes[0]
    if focus != result.focus_boxes[0] or focus.source != "neutral_rectangle_border":
        return CaptionEvidence("none")
    bottom = focus.y + focus.height
    left_slack = min(.015, focus.width * .08)
    right = focus.x + focus.width + .008
    candidates = sorted((row for row in result.observations
                         if bottom - .003 <= row.y < min(1., bottom + .18)
                         and abs(row.x - focus.x) <= left_slack), key=lambda row: (row.y, row.x))
    if not candidates or candidates[0].y - bottom > .055:
        return CaptionEvidence("none")
    selected: list[TextBox] = []
    previous_bottom = bottom
    for row in candidates:
        if selected and row.y - previous_bottom > .03:
            break  # A detached next row/section is not part of this card.
        if row.y + row.height > min(1., bottom + .18) or len(selected) >= 5:
            break
        if row.x + row.width > right or row.height > .07 or row.confidence < .75:
            return CaptionEvidence("ambiguous")
        if selected and row.y < previous_bottom - .008:
            return CaptionEvidence("ambiguous")  # Overlapping text/merged columns.
        selected.append(row)
        previous_bottom = row.y + row.height
    if len(selected) < 2:
        # An isolated section heading immediately below a carousel is not a card
        # caption. A caller may explicitly identify a nonplayback catalog title.
        normalize = lambda value: " ".join(value.casefold().split())
        if not (len(selected) == 1 and isinstance(expected_title, str)
                and expected_title.strip() and selected[0].confidence >= .9
                and normalize(selected[0].text) == normalize(expected_title)):
            return CaptionEvidence("none")
    return CaptionEvidence("matched", "\n".join(row.text for row in selected), tuple(selected))


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError("invalid numeric OCR value")
    return float(value)


def parse_result(raw: str) -> OCRResult:
    """Validate helper output strictly; malformed regions invalidate the read."""
    try:
        data = json.loads(raw)
        status = data["status"]
        if status in {"unreadable", "error"}:
            return OCRResult(status, error="image could not be decoded" if status == "unreadable"
                             else "Vision text recognition failed")
        if status not in {"ok", "empty"} or data["coordinate_system"] != "normalized_top_left":
            raise ValueError("invalid result status or coordinates")
        width, height = data["width"], data["height"]
        if type(width) is not int or type(height) is not int or not (0 < width <= 32768 and 0 < height <= 32768):
            raise ValueError("invalid image dimensions")
        observations = data["observations"]
        if not isinstance(observations, list) or len(observations) > 10000:
            raise ValueError("invalid observation collection")
        rows = []
        for row in observations:
            text = row["text"]
            if not isinstance(text, str) or not text.strip() or len(text) > 10000:
                raise ValueError("invalid recognized text")
            confidence = _number(row["confidence"])
            box = row["box"]
            x, y, w, h = (_number(box[key]) for key in ("x", "y", "width", "height"))
            if not (0 <= confidence <= 1 and 0 <= x <= 1 and 0 <= y <= 1
                    and 0 < w <= 1 and 0 < h <= 1 and x + w <= 1.000001 and y + h <= 1.000001):
                raise ValueError("out-of-bounds OCR region")
            rows.append(TextBox(text.strip(), confidence, x, y, w, h))
        if bool(rows) != (status == "ok"):
            raise ValueError("inconsistent empty status")
        rows.sort(key=lambda row: (row.y, row.x))
        candidates = data.get("focus_candidates", [])
        if not isinstance(candidates, list) or len(candidates) > 100:
            raise ValueError("invalid focus candidates")
        focus = []
        for box in candidates:
            x, y, w, h, confidence = (_number(box[key]) for key in
                                      ("x", "y", "width", "height", "confidence"))
            source = box["source"]
            if not (source in {"neutral_rectangle_border", "neutral_filled_button"}
                    and (0.9 if source == "neutral_rectangle_border" else 0.68) <= confidence <= 1
                    and 0 <= x <= 1 and 0 <= y <= 1 and 0 < w <= 1 and 0 < h <= 1
                    and x + w <= 1.000001 and y + h <= 1.000001):
                raise ValueError("invalid focus region")
            contained = [row.text for row in rows if row.x >= x and row.y >= y
                         and row.x + row.width <= x + w and row.y + row.height <= y + h]
            text = "\n".join(contained)
            if source == "neutral_filled_button":
                # Vision may read the triangular play icon as a standalone bullet.
                # Only discard isolated decorative glyphs, never words or labels.
                text = "\n".join(line for line in text.splitlines()
                                 if line.strip() not in {"•", "▶", "►", "▸", "●", ">"})
                text = re.sub(r"^[•▶►▸●>]\s+(?=Resume$|Start from beginning$|Watch Replay\b)", "", text,
                              flags=re.IGNORECASE)
                # These observed labels provide focus evidence, not permission to
                # activate them. The runner checks matchup/date and chooser context.
                action_pattern = (r"(?:Resume|Watch Replay\s+[\w .&'-]{1,70} Broadcast"
                                  r"|Watch key plays|Start from beginning"
                                  r"|Watch (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) "
                                  r"(?:[1-9]|[12][0-9]|3[01])\s*[•·]\s*[A-Z]{2,4} vs\.? [A-Z]{2,4})")
                if not re.fullmatch(action_pattern,
                                    text, flags=re.IGNORECASE):
                    continue
            focus.append(FocusBox(x, y, w, h, confidence, text, source))
        unique = len(focus) == 1 and bool(rows)
        return OCRResult(status, tuple(rows), width, height,
                         focus_boxes=tuple(focus) if unique else (),
                         focus_status="unique" if unique else "ambiguous" if len(focus) > 1 else "none")
    except (ValueError, TypeError, KeyError, OverflowError):
        return OCRResult("error", error="invalid OCR helper response")


def _helper(compile_timeout: float) -> Path:
    source = Path(__file__).resolve().parents[1] / "scripts" / "vision_ocr.swift"
    content = source.read_bytes()
    digest = hashlib.sha256(content + platform.machine().encode()).hexdigest()[:20]
    cache = Path(tempfile.gettempdir()) / f"redzone-vision-{os.getuid()}"
    cache.mkdir(mode=0o700, exist_ok=True)
    stat = cache.lstat()
    if cache.is_symlink() or stat.st_uid != os.getuid() or stat.st_mode & 0o077:
        raise OSError("OCR cache must be private to this user")
    binary = cache / f"vision-{digest}"
    if binary.is_file() and not binary.is_symlink() and os.access(binary, os.X_OK):
        return binary
    # Build separately, then atomically install; concurrent readers never see a partial binary.
    with tempfile.TemporaryDirectory(prefix="build-", dir=cache) as build:
        output = Path(build) / "vision"
        modules = Path(build) / "modules"
        result = subprocess.run(["/usr/bin/swiftc", "-O", "-module-cache-path", str(modules),
                                 str(source), "-o", str(output)], capture_output=True,
                                timeout=compile_timeout, check=False)
        if result.returncode != 0:
            raise RuntimeError("local Vision helper compilation failed")
        os.replace(output, binary)
    return binary


def prepare(*, compile_timeout: float = 45.0) -> OCRPreparation:
    """Warm the native helper cache BEFORE capturing a frame or changing a device.

    This only compiles local code. It neither reads images nor invokes Vision, so
    readiness does not promise OS permission for a later OCR request. Call via
    asyncio.to_thread from an async runner and retain normal frame freshness gates.
    """
    started = time.monotonic()
    try:
        if not 0 < compile_timeout <= 60:
            raise ValueError("compile timeout must be between zero and 60 seconds")
        if platform.system() != "Darwin":
            return OCRPreparation("unsupported", "local Vision OCR requires macOS")
        path = _helper(compile_timeout)
        return OCRPreparation("ready", elapsed_s=round(time.monotonic() - started, 3),
                              helper_path=str(path))
    except subprocess.TimeoutExpired:
        return OCRPreparation("timeout", "local OCR helper compilation timed out",
                              round(time.monotonic() - started, 3))
    except (OSError, RuntimeError, ValueError) as exc:
        return OCRPreparation("error", str(exc), round(time.monotonic() - started, 3))


def recognize(path: str | Path, *, timeout: float = 15.0, compile_timeout: float = 45.0) -> OCRResult:
    """Recognize one existing PNG. First use compiles locally; later calls reuse it.

    Run via asyncio.to_thread when used from an async engine. Timeouts stop the
    helper; failures carry no recognized text. No screenshots are created here.
    """
    started = time.monotonic()
    result = OCRResult("error", error="OCR did not run")
    try:
        if not (0 < timeout <= 60 and 0 < compile_timeout <= 60):
            raise ValueError("OCR timeouts must be between zero and 60 seconds")
        if platform.system() != "Darwin":
            result = OCRResult("unsupported", error="local Vision OCR requires macOS")
        else:
            image = Path(path).expanduser().resolve(strict=True)
            if not image.is_file() or image.stat().st_size > 32 * 1024 * 1024:
                raise ValueError("expected a local PNG file no larger than 32 MiB")
            with image.open("rb") as stream:
                if stream.read(8) != b"\x89PNG\r\n\x1a\n":
                    raise ValueError("expected a PNG screenshot")
            binary = _helper(compile_timeout)
            proc = subprocess.run([str(binary), str(image)], capture_output=True, text=True,
                                  timeout=timeout, check=False)
            result = parse_result(proc.stdout)
            if proc.returncode and result.status in {"ok", "empty"}:
                result = OCRResult("error", error="OCR helper exited unsuccessfully")
    except subprocess.TimeoutExpired:
        result = OCRResult("timeout", error="local OCR or helper compilation timed out")
    except (FileNotFoundError, PermissionError):
        result = OCRResult("unreadable", error="local PNG or Swift compiler is unavailable")
    except (OSError, RuntimeError, ValueError) as exc:
        result = OCRResult("error", error=str(exc))
    return replace(result, elapsed_s=round(time.monotonic() - started, 3))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()
    result = recognize(args.image, timeout=args.timeout)
    print(json.dumps({**asdict(result), "text": result.text}, ensure_ascii=False))
    raise SystemExit(0 if result.status in {"ok", "empty"} else 1)


if __name__ == "__main__":
    main()
