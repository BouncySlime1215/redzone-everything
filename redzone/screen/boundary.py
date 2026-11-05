"""Stream delay measured automatically from show changes (no taps, no mic, no camera).

YouTube TV's media session names the show on screen ('Monday Night Kickoff, ESPN, null'), and that
title is STREAM-timed: it flips when the new show reaches your screen, not when the schedule says.
Shows change on the quarter hour, so a title that flips at 19:00:22 on a playing stream means the
screen runs ~22 s behind real life. That is one 'boundary' sample:

    delay = flip_wall - nearest scheduled quarter hour   (error: half the time between two reads)

The stream position (ms on a per-channel timeline that advances in real time) also gives each
channel an origin: origin = boundary_wall - position_at_flip. Afterwards any stick on that channel
knows its CURRENT delay from one read, including the per-tune live-edge offset:

    delay_now = wall_now - (origin + position_now)

Real cases measured on the sticks (Nov 2 2026, ET): truTV flipped at 19:00:22.081 -> 22 s; ESPN,
flip read at 20:17:56.017 with position 47160235 for the 20:15 boundary, earlier paused at
20:14:01.768 with position 47075571 -> 26.4 s behind live at the pause.

A boundary sample is weaker than a tap ("I just saw it"): networks don't switch the title to the
second, and a show can start late. The delay book gives it a wider sigma and needs two agreeing
samples before it narrows anything (see DelayBook.boundary).
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Callable

log = logging.getLogger("redzone.boundary")

QUARTER_S = 900.0
WINDOW_S = 180.0          # read fast only this close to :00/:15/:30/:45 (plus the delay, see in_window)
READ_EVERY_S = 0.5        # inside a window: the error of a sample is about half of this
MIN_SAMPLE_S = 5.0
MAX_SAMPLE_S = 120.0
NEAR_BOUNDARY_S = 150.0   # a flip further than this from a quarter hour (after the delay) isn't a boundary
MAX_GAP_S = 3.0           # two reads further apart than this time a flip too loosely
CLOCK_TIGHT_MS = 2000.0   # the stick clock must be pinned this tightly to place a position in wall time
YTTV = "youtubetv"


def nearest_boundary(t: float) -> float:
    return round(t / QUARTER_S) * QUARTER_S


def in_window(now: float, delay_s: float = 0.0, window_s: float = WINDOW_S) -> bool:
    """Close enough to a quarter hour (as it reaches the screen, `delay_s` later) to read fast."""
    return abs(now - delay_s - nearest_boundary(now - delay_s)) <= window_s


def show_title(report: dict) -> str:
    t = re.sub(r"\s+", " ", str(report.get("title") or "")).strip()
    return "" if t.lower() in ("null", "none") else t


def is_show_change(a: str, b: str) -> bool:
    """Two different, real show names (not empty, not 'null', not the same show re-posted)."""
    return bool(a) and bool(b) and a.casefold() != b.casefold()


def flip_sample(flip_wall: float, estimate_s: float) -> tuple[float, float] | None:
    """(delay sample, boundary) for a title flip seen on screen at `flip_wall`, or None when it isn't
    a believable quarter-hour boundary. `estimate_s` is the current delay estimate."""
    boundary = nearest_boundary(flip_wall - estimate_s)
    if abs(flip_wall - estimate_s - boundary) > NEAR_BOUNDARY_S:
        return None
    sample = flip_wall - boundary
    if not MIN_SAMPLE_S <= sample <= MAX_SAMPLE_S:
        return None
    return sample, boundary


def channel_origin(boundary_wall: float, position_at_boundary_s: float) -> float:
    """Wall time of position 0 on this channel's stream timeline, as seen on screen with zero delay."""
    return boundary_wall - position_at_boundary_s


def delay_from_origin(origin: float, wall: float, position_s: float) -> float:
    """How far behind real life the frame at `position_s`, on screen at `wall`, is."""
    return wall - (origin + position_s)


@dataclass
class StickClock:
    """Pins the stick's clock (elapsedRealtime ms, the media session's `updated`) to wall time,
    from reads alone: `updated` can't be later than a read that shows it, and a new `updated`
    can't be earlier than the read before it. off = stick_ms - wall_ms within [lo, hi]."""
    lo: float = float("-inf")
    hi: float = float("inf")

    def note(self, prev: "Read | None", cur: "Read") -> None:
        u = cur.updated
        if u is None:
            return
        self.lo = max(self.lo, u - cur.wall_hi * 1000.0)
        if prev is not None and prev.updated is not None and u != prev.updated:
            self.hi = min(self.hi, u - prev.wall_lo * 1000.0)
        if self.lo > self.hi + 500.0:      # the stick rebooted or its clock jumped: start over
            self.lo, self.hi = u - cur.wall_hi * 1000.0, float("inf")

    def offset_ms(self) -> float | None:
        if self.hi - self.lo > CLOCK_TIGHT_MS:
            return None
        return (self.lo + self.hi) / 2.0


@dataclass
class Read:
    wall_lo: float           # the dumpsys snapshot was taken between these two wall times
    wall_hi: float
    target: str
    tune: object             # identity of the stick's confirmed tune: a new tune is a new object
    title: str
    channel: str
    playing: bool
    position: int | None     # ms
    updated: int | None      # stick ms
    speed: float

    def position_at(self, wall: float, off_ms: float | None) -> float | None:
        """Stream position (s) at `wall`, extrapolated from the report with the stick's clock."""
        if self.position is None or self.updated is None or off_ms is None:
            return None
        stick_ms = wall * 1000.0 + off_ms
        return (self.position + self.speed * (stick_ms - self.updated)) / 1000.0


def _continuous(a: Read, b: Read, slack_ms: float = 3000.0) -> bool:
    """b follows a on the same stream with no pause, seek or new tune in between."""
    if not (a.playing and b.playing) or a.target != b.target or a.tune is not b.tune:
        return False
    if a.channel and b.channel and a.channel.casefold() != b.channel.casefold():
        return False
    if None in (a.position, a.updated, b.position, b.updated):
        return False
    if (a.position, a.updated) == (b.position, b.updated):
        return True
    return abs(b.position - (a.position + a.speed * (b.updated - a.updated))) <= slack_ms


@dataclass
class Sample:
    target: str
    sample: float            # delay, s
    boundary: float          # the quarter hour, wall
    flip_wall: float
    error_s: float           # half the read gap
    title_from: str
    title_to: str
    origin: float | None = None


@dataclass
class BoundaryWatcher:
    """Turns successive YouTube TV playback reads of each stick into delay samples and channel origins.

    estimate(target) -> current (mu, sigma); on_sample(Sample) is called for each accepted sample;
    on_live(target, delay_s | None) for each read that gives (or loses) a per-tune current delay."""
    estimate: Callable[[str], tuple[float, float]]
    on_sample: Callable[[Sample], None] = lambda s: None
    on_live: Callable[[str, float | None], None] = lambda t, d: None
    # [auto sync] every playing read with the stick clock pinned: (target, wall, stream position s, sd);
    # and a new tune or a seek on a stick: (target, wall). Both feed the per-channel filter (sync.py).
    on_position: Callable[[str, float, float, float], None] = lambda t, w, p, sd: None
    on_tune: Callable[..., None] = lambda t, w, new=True: None
    clock: Callable[[], float] = time.time
    mono: Callable[[], float] = time.monotonic
    last: dict = field(default_factory=dict)        # stick key -> last Read
    clocks: dict = field(default_factory=dict)      # stick key -> StickClock
    origins: dict = field(default_factory=dict)     # target -> origin (wall s)
    samples: list = field(default_factory=list)     # accepted Samples, newest last (this run)
    rejected: int = 0

    def observe_stick(self, stick, report: dict) -> Sample | None:
        tuned = getattr(stick, "tuned", None) or {}
        return self.observe(getattr(stick, "host", str(id(stick))), tuned.get("target"), tuned, report)

    def observe(self, key: str, target: str | None, tune: object, report: dict) -> Sample | None:
        if not target or not str(target).startswith(YTTV + ":") or report.get("read_ok") is False:
            self.last.pop(key, None)
            return None
        cur = self._read(target, tune, report)
        prev = self.last.get(key)
        clk = self.clocks.setdefault(key, StickClock())
        clk.note(prev, cur)
        self.last[key] = cur
        self._sync(prev, cur, clk)
        out = None
        if prev is not None and is_show_change(prev.title, cur.title):
            out = self._flip(prev, cur, clk)
        if out is None and cur.playing:
            self._live(cur, clk, new_tune=prev is None or prev.tune is not cur.tune)
        return out

    def _read(self, target: str, tune: object, r: dict) -> Read:
        now, mono = self.clock(), self.mono()
        lo = r.get("read_started")
        hi = r.get("read_finished")
        wall_hi = now - (mono - hi) if isinstance(hi, (int, float)) else now
        wall_lo = now - (mono - lo) if isinstance(lo, (int, float)) else wall_hi
        speed = r.get("speed")
        try:
            speed = 1.0 if speed is None else float(speed)
        except (TypeError, ValueError):
            speed = 0.0
        playing = r.get("state") == "playing" and speed > 0
        return Read(wall_lo, wall_hi, str(target), tune, show_title(r),
                    re.sub(r"\s+", " ", str(r.get("subtitle") or "")).strip(), playing,
                    r.get("position"), r.get("updated"), speed)

    def _flip(self, a: Read, b: Read, clk: StickClock) -> Sample | None:
        gap = b.wall_hi - a.wall_lo
        if not _continuous(a, b) or gap > MAX_GAP_S:
            self.rejected += 1
            log.info("show change on %s (%r -> %r) not timed: %s", b.target, a.title, b.title,
                     "read gap %.1fs" % gap if gap > MAX_GAP_S else "not one continuous playback")
            return None
        flip = (a.wall_lo + b.wall_hi) / 2.0
        mu, _ = self.estimate(b.target)
        got = flip_sample(flip, mu)
        if got is None:
            self.rejected += 1
            log.info("show change on %s (%r -> %r) at %s isn't a quarter-hour boundary %.0fs behind; ignored",
                     b.target, a.title, b.title, time.strftime("%H:%M:%S", time.localtime(flip)), mu)
            return None
        sample, boundary = got
        pos = b.position_at(flip, clk.offset_ms())
        s = Sample(b.target, sample, boundary, flip, gap / 2.0, a.title, b.title,
                   None if pos is None else channel_origin(boundary, pos))
        if s.origin is not None:
            self.origins[b.target] = s.origin
        self.samples.append(s)
        log.info("delay %.1fs (±%.1f) on %s from the show change %r -> %r", sample, s.error_s, b.target,
                 a.title, b.title)
        self.on_sample(s)
        return s

    def _sync(self, prev: "Read | None", cur: Read, clk: StickClock) -> None:
        """[auto sync] Tell the filter about a new tune / a seek, and hand it this read's position."""
        if not cur.playing:
            return
        try:
            new = prev is None or prev.tune is not cur.tune or prev.target != cur.target
            if new or (prev.playing and not _continuous(prev, cur)):
                self.on_tune(cur.target, cur.wall_hi, new)     # new = a new tune: its position clock is new
            off = clk.offset_ms()
            pos = cur.position_at(cur.wall_hi, off)
            if pos is None:
                return
            # doubt: the clock pin's half width plus half the read's own duration
            sd = max(0.05, (clk.hi - clk.lo) / 2000.0 + (cur.wall_hi - cur.wall_lo) / 2.0)
            self.on_position(cur.target, cur.wall_hi, pos, sd)
        except Exception:
            log.exception("sync hand-off failed for %s", cur.target)

    def _live(self, cur: Read, clk: StickClock, new_tune: bool) -> None:
        origin = self.origins.get(cur.target)
        if origin is None:
            if new_tune:
                self.on_live(cur.target, None)    # the last tune's offset doesn't carry over
            return
        wall = cur.wall_hi
        pos = cur.position_at(wall, clk.offset_ms())
        if pos is None:
            if new_tune:
                self.on_live(cur.target, None)
            return
        d = delay_from_origin(origin, wall, pos)
        mu, sigma = self.estimate(cur.target)
        if not MIN_SAMPLE_S <= d <= MAX_SAMPLE_S or abs(d - mu) > 2 * sigma:
            log.info("%s: origin says %.1fs behind, the estimate %.0f±%.0f; dropping the origin "
                     "(the stream restarted?)", cur.target, d, mu, 2 * sigma)
            self.origins.pop(cur.target, None)
            self.on_live(cur.target, None)
            return
        self.on_live(cur.target, d)

    def status(self) -> dict:
        return {"samples": len(self.samples), "rejected": self.rejected, "origins": len(self.origins),
                "last": None if not self.samples else {
                    "target": self.samples[-1].target, "delay_s": round(self.samples[-1].sample, 1),
                    "at": self.samples[-1].flip_wall, "show": self.samples[-1].title_to}}
