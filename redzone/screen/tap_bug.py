"""[EXP-741 E, decider.entry.user_bug_gate] The Prime score bug's game clock as a tap's dead-ball signal.

A single tap onto a Prime game waits, on the hidden stick that already plays it, until the bug's game
clock reads the same on two frames at least MIN_GAP_S apart (stopped: in basketball the clock only
stops on a dead ball) and the newer frame is at most FRESH_S old. Two different readings = running.
No readable clock = unknown, and the tap falls back to the feed's entry plan. The screen has no feed
lag, which is what failed arms A-D on Prime (lane 3, EXP-741 grade). Numbers only: each frame becomes
(time, clock, period) and the PNG is dropped (Nick, 3:30 AM Nov 5).

Why: first added in e30ce4ac (2026-11-05) as "EXP-741 E/F: Prime tap score-bug clock gate (user_bug_gate, off) and taps never take an unknown l..."; no separate decision is recorded for this module.
"""
from __future__ import annotations

import json
import time
from collections import deque
from pathlib import Path

from redzone.screen.scorebug import clock_seconds

READS_KEPT = 8


class TapBug:
    def __init__(self, log_path: Path | None = None, fresh: float = 5.0, min_gap: float = 1.5):
        """fresh: the newer frame must be this recent (s; a band capture + OCR is ~1-3 s). min_gap: two
        equal readings at least this far apart (s) mean the clock is stopped, not between two ticks."""
        fresh_s, min_gap_s = fresh, min_gap
        self.log_path, self.fresh_s, self.min_gap_s = log_path, fresh_s, min_gap_s
        self.reads: dict[str, deque] = {}
        self._said: dict[str, str] = {}

    def add(self, target: str, t0: float, bug, visible: bool = False, why: str = "") -> None:
        clock = clock_seconds(getattr(bug, "clock", "") or "") if bug is not None else None
        period = (getattr(bug, "period", "") or "") if bug is not None else ""
        self.reads.setdefault(target, deque(maxlen=READS_KEPT)).append((t0, clock, period))
        self._log(dict(kind="read", t=round(t0, 2), target=target, clock=clock, period=period,
                       bug=bug is not None, visible=visible, why=why))

    def says(self, target: str, now: float | None = None) -> str:
        """stopped | running | unknown, as of now."""
        now = time.time() if now is None else now
        rs = [r for r in self.reads.get(target, ()) if r[1] is not None]
        if not rs or now - rs[-1][0] > self.fresh_s:
            return "unknown"
        t1, c1, p1 = rs[-1]
        prev = [r for r in rs[:-1] if r[0] <= t1 - self.min_gap_s]
        if not prev:
            return "unknown"
        t0, c0, p0 = prev[-1]
        if p0 != p1:
            return "unknown"
        return "stopped" if c0 == c1 else "running"

    def note(self, target: str, say: str, now: float, gate: str) -> None:
        """Log each change of the gate's answer for a waiting tap (shadow and on)."""
        if self._said.get(target) != say:
            self._said[target] = say
            self._log(dict(kind="gate", t=round(now, 2), target=target, says=say, mode=gate))

    def reset(self, target: str | None = None) -> None:
        if target is None:
            self.reads.clear()
            self._said.clear()
        else:
            self.reads.pop(target, None)
            self._said.pop(target, None)

    def _log(self, rec: dict) -> None:
        if self.log_path is None:
            return
        try:
            with self.log_path.open("a") as f:
                f.write(json.dumps(rec) + "\n")
        except OSError:
            pass
