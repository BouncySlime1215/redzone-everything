"""[edge prior, Nick Nov 7] Measure YouTube TV's live-edge delay on the HIDDEN stick, numbers only.

Nick's method (hidden stick, Nov 7 ~10:55 ET, 17 reads on 7 channels, 34.3-35.5 s, mean 34.9, sd ~0.4):
  1. a fresh live edge: tune another channel, then back (a same-channel deep link does not reset a stick that is
     behind, #391), so the playhead is AT the live edge;
  2. CENTER (overlay up), RIGHT (at the live edge the knob can't move, it only lands focus on the bar so the time
     label shows; Nick, Nov 7: RIGHT 8/8 left the knob where it was);
  3. one screencap -> local OCR -> the PNG deleted at once (yttv_label.read_label); only the label's seconds leave;
  4. BACK closes the overlay (BACK does not commit).
  delay = (capture wall - EPG program start) - label, with the start = the :00/:30 boundary (the EPG window when
  the overlay shows it). A read with no label is skipped, never guessed (Nick's 3 empty reads).

Every app channel shares one edge delay (Nick's 7 channels agreed within ~1 s), so one read re-prices them all
(DelayBook.add_edge_read). The delay is against YouTube TV's schedule clock: any delay the network itself adds
before air is not in it. The stated doubt keeps the 4 s floor for that reason.

Rule #1 unchanged: only the HIDDEN stick, only at the live edge (never on a buffered / scrubbed stream), at most
once per delay.edge_prior.refresh.min_gap_s (30 min). Flags delay.edge_prior.enabled and .refresh.enabled, OFF.
YouTube TV video is DRM-black in a screencap; only the overlay UI is read. Rows in data/edge_probe.jsonl: numbers,
the channel key and the status; never the label text, pixels or the stick's address.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from redzone.tuners import yttv_label

log = logging.getLogger("redzone.yttv_edge_probe")

BOUNDARY_S = 1800.0          # EPG programs start on the :00 / :30


@dataclass
class EdgeRead:
    status: str                       # ok | not_hidden | tune_failed | keys_failed | no_label (+ read statuses:
                                      # banner, no_overlay, ambiguous, ...) | no_boundary
    app: str = "youtubetv"
    channel: str = ""
    delay_s: float | None = None      # (cap_wall - prog_start) - label_s
    label_s: float | None = None      # playhead seconds since the program start, at the live edge
    prog_start: float | None = None   # epoch of the program start used (EPG window, else the :00/:30 boundary)
    start_from: str = ""              # epg | boundary
    cap_wall: float = 0.0
    elapsed_s: float = 0.0            # the whole probe, tune included
    keys: list[str] = field(default_factory=list)
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok" and self.delay_s is not None

    def row(self) -> dict:
        """What may be logged: numbers, the channel key, the status. No label text, no pixels, no address."""
        return {k: v for k, v in asdict(self).items()}


def edge_delay(cap_wall: float, label_s: float, *, prog_start: float | None = None,
               max_gap_s: float = 120.0, boundary_s: float = BOUNDARY_S) -> tuple[float | None, float | None, str]:
    """(delay_s, program start, 'epg' | 'boundary') from one live-edge label read.

    delay = (cap_wall - start) - label_s. With the EPG window read (prog_start) that start is used; otherwise the
    :00/:30 boundary just before cap_wall - label_s (Nick's rule). A result outside [0, max_gap_s] is not a live
    edge on a program that started on the boundary (a 7:10 tip, a misread): (None, start, ...) and it is skipped."""
    if not (math.isfinite(cap_wall) and math.isfinite(label_s)) or label_s < 0:
        return None, None, ""
    if prog_start is not None:
        d = cap_wall - prog_start - label_s
        return (round(d, 2) if 0.0 <= d <= max_gap_s else None), prog_start, "epg"
    x = cap_wall - label_s
    start = math.floor(x / boundary_s) * boundary_s
    d = x - start
    return (round(d, 2) if d <= max_gap_s else None), start, "boundary"


def _default_log() -> Path:
    from redzone.config import DATA
    return DATA / "edge_probe.jsonl"


class EdgeProbe:
    """One live-edge read every delay.edge_prior.refresh.min_gap_s at most (tried or not).

    measure(stick, relink=..., still_hidden=...) does the whole probe; the caller (control._edge_probe_tick or
    scripts/edge_probe_try.py) supplies relink() (tune another channel, then this one: a known live edge) and
    still_hidden() (checked before every key: a stick that became visible is never pressed)."""

    def __init__(self, cfg=None, *, clock: Callable[[], float] = time.time,
                 recognize: yttv_label.Recognizer | None = None, tz: dt.tzinfo | None = None,
                 sleep: Callable[[float], Awaitable[None]] | None = None, log_path: Path | str | None = ""):
        self.cfg = cfg
        self.clock = clock
        self.recognize = recognize
        self.tz = tz
        self.sleep = sleep or asyncio.sleep
        self.log_path = _default_log() if log_path == "" else (Path(log_path) if log_path else None)
        self.last_try: float | None = None
        self.reads: list[EdgeRead] = []

    def _c(self, key: str, default):
        v = None
        if self.cfg is not None and hasattr(self.cfg, "get"):
            try:
                v = self.cfg.get(f"delay.edge_prior.refresh.{key}")
            except Exception:
                v = None
        if v is None:
            from redzone import config
            v = config.default(f"delay.edge_prior.refresh.{key}")
        return default if v is None else v

    def due(self, now: float | None = None) -> bool:
        now = self.clock() if now is None else now
        return self.last_try is None or now - self.last_try >= float(self._c("min_gap_s", 1800))

    async def _press(self, stick, code: str, still_hidden: Callable[[], bool], keys: list[str]) -> None:
        if not still_hidden():
            raise RuntimeError("stick became visible")
        keys.append(code)
        r = await stick.shell(f"input keyevent KEYCODE_{code}", timeout=4)
        if not getattr(r, "ok", False):
            raise RuntimeError(f"{code} not sent")

    async def measure(self, stick, *, relink: Callable[[], Awaitable[bool]] | None,
                      still_hidden: Callable[[], bool], channel: str = "", app: str = "youtubetv") -> EdgeRead:
        t0 = time.monotonic()
        self.last_try = self.clock()
        out = await self._measure(stick, relink, still_hidden, channel, app)
        out.elapsed_s = round(time.monotonic() - t0, 2)
        self.reads.append(out)
        del self.reads[:-50]
        self._log(out)
        return out

    async def _measure(self, stick, relink, still_hidden, channel: str, app: str) -> EdgeRead:
        if not still_hidden():
            return EdgeRead("not_hidden", app, channel, detail="nothing pressed")
        if relink is not None:
            try:
                ok = await relink()
            except Exception as e:   # a tuner bug is a skipped read, never a crash in keepalive
                ok, why = False, type(e).__name__
            else:
                why = ""
            if not ok:
                return EdgeRead("tune_failed", app, channel, detail=why or "fresh live edge not reached")
        await self.sleep(float(self._c("after_tune_s", 2.0)))
        wait = float(self._c("overlay_wait_s", 1.3))
        keys: list[str] = []
        read = None
        try:
            await self._press(stick, "DPAD_CENTER", still_hidden, keys)
            await self.sleep(wait)
            await self._press(stick, "DPAD_RIGHT", still_hidden, keys)   # focus onto the bar; the knob can't move at live
            await self.sleep(wait)
            read = await yttv_label.read_label(stick, enabled=True, knob_offset_s=0.0, recognize=self.recognize,
                                               tz=self.tz)
        except RuntimeError as e:
            return EdgeRead("keys_failed", app, channel, keys=keys, detail=str(e))
        finally:
            if keys and still_hidden():
                try:
                    await self._press(stick, "BACK", still_hidden, keys)   # close the overlay; BACK never commits
                except RuntimeError:
                    pass
        if read is None or not read.ok:
            st = read.status if read is not None else "no_label"
            return EdgeRead(st if st not in ("ok", "") else "no_label", app, channel, cap_wall=read.cap_wall if read
                            else 0.0, keys=keys, detail="no label read: skipped")
        delay, start, src = edge_delay(read.cap_wall, float(read.label_s), prog_start=read.prog_start,
                                       max_gap_s=float(self._c("max_label_gap_s", 120)))
        if delay is None:
            return EdgeRead("no_boundary", app, channel, label_s=read.label_s, prog_start=start, start_from=src,
                            cap_wall=read.cap_wall, keys=keys,
                            detail="program did not start on the :00/:30 boundary (or a misread): skipped")
        return EdgeRead("ok", app, channel, delay_s=delay, label_s=read.label_s, prog_start=start, start_from=src,
                        cap_wall=read.cap_wall, keys=keys)

    def _log(self, r: EdgeRead) -> None:
        if self.log_path is None:
            return
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a") as f:
                f.write(json.dumps({"ts": round(self.clock(), 2), **r.row()}) + "\n")
        except OSError:
            log.exception("edge probe: log")
