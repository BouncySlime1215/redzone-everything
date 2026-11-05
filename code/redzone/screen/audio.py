"""Hear the TV to measure your stream delay automatically (optional, off by default).

The Mac's microphone listens for the crowd/announcer surge that follows a big play. The feed
says the touchdown happened at 8:01:10 (wallclock); the room gets loud at 8:01:52 -> your screen
is 42s behind. Each big play re-measures, so the delay stays right even when the stream drifts.

Privacy: audio never leaves memory and is never saved. Each quarter-second becomes one loudness
number (dB); only those numbers are kept, for 10 minutes.

Guardrails: a measurement only counts when exactly one clear surge lands in the plausible window
(your yelling at the wrong time, or two plays close together, is ignored), and every accepted
sample still goes through the delay book's median filter.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from collections import deque
from dataclasses import dataclass

log = logging.getLogger(__name__)

BLOCK_S = 0.25
RATE = 16000


@dataclass
class Surge:
    t: float        # onset time (epoch s)
    rise_db: float  # how far above the room's baseline
    length_s: float


def detect_surges(env: list[tuple[float, float]], baseline_s: float = 30.0, rise_db: float = 6.0,
                  min_len_s: float = 1.0) -> list[Surge]:
    """Onsets where loudness jumps rise_db above the trailing-median baseline and stays up."""
    out: list[Surge] = []
    if len(env) < int(baseline_s / BLOCK_S) + 4:
        return out
    win = int(baseline_s / BLOCK_S)
    i = win
    while i < len(env):
        base = sorted(x[1] for x in env[i - win:i])[win // 2]
        if env[i][1] - base >= rise_db:
            j = i
            while j < len(env) and env[j][1] - base >= rise_db - 2:
                j += 1
            length = (j - i) * BLOCK_S
            if length >= min_len_s:
                peak = max(x[1] for x in env[i:j]) - base
                out.append(Surge(env[i][0], round(peak, 1), round(length, 2)))
            i = j + int(5 / BLOCK_S)  # one surge per play: skip the tail
        else:
            i += 1
    return out


def match_delay(play_wall: float, surges: list[Surge], lo: float = 10.0, hi: float = 120.0,
                expected: float | None = None) -> float | None:
    """Delay for one big play, or None when the evidence is ambiguous."""
    hits = [s for s in surges if lo <= s.t - play_wall <= hi]
    if expected is not None:
        near = [s for s in hits if abs(s.t - play_wall - expected) <= 20]
        hits = near or hits
    strong = [s for s in hits if s.rise_db >= 8 or s.length_s >= 2]
    pool = strong or hits
    if len(pool) != 1:
        return None
    return round(pool[0].t - play_wall, 1)


class MicWatcher:
    """Background loudness meter. start() asks macOS for mic access the first time."""

    def __init__(self, keep_s: float = 600.0):
        self.env: deque[tuple[float, float]] = deque(maxlen=int(keep_s / BLOCK_S))
        self._stream = None
        self.error = ""
        self._lock = threading.Lock()

    def start(self) -> bool:
        try:
            import numpy as np
            import sounddevice as sd
        except ImportError as e:
            self.error = f"audio libraries missing: {e}"
            return False

        def cb(indata, frames, t_info, status):
            rms = float(np.sqrt(np.mean(np.square(indata[:, 0], dtype=np.float64))) + 1e-9)
            with self._lock:
                self.env.append((time.time(), 20 * math.log10(rms)))
        try:
            self._stream = sd.InputStream(samplerate=RATE, channels=1, blocksize=int(RATE * BLOCK_S),
                                          dtype="float32", callback=cb)
            self._stream.start()
            return True
        except Exception as e:  # no mic, permission denied, device busy
            self.error = f"microphone unavailable: {e}"
            self._stream = None
            return False

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None

    def surges(self, since: float) -> list[Surge]:
        with self._lock:
            env = [x for x in self.env if x[0] >= since - 35]
        return detect_surges(env)
