"""The Mac slept, or moved networks: everything that held a connection is stale (hardening.mac).

How a sleep is seen: macOS's monotonic clock stops while the Mac sleeps and the wall clock does not,
so between two housekeeping ticks the wall clock runs ahead of the monotonic clock by the time asleep.
A tick that comes far later than scheduled on the monotonic clock (App Nap, a stalled event loop)
counts too. An NTP correction forward also looks like this; the cost is one harmless reconnect.

After a wake or a network change (NetWatch's home/away answer changed), at once instead of after each
part's own timeouts and backoffs:
  - the network is re-checked (home / away / tunnel), not up to CHECK_EVERY_S later;
  - every stick is reconnected on the next keepalive, and an open adb breaker probes now;
  - the TV's local socket is dropped (a socket that slept is dead) and a SmartThings breaker probes now;
  - which input the TV shows is re-read (someone may have used the remote meanwhile), and the standby
    is re-verified;
  - the FastCast socket is restarted, and HTTP backoffs left from the old network are cleared.
Nothing here presses a key or changes the TV's input.

Why: first added in 9cfabbd1 (2026-11-04) as "hardening.mac: reconnect everything at once after the Mac sleeps or changes network"; no separate decision is recorded for this module.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

log = logging.getLogger(__name__)

WAKE_GAP_S = 30.0     # wall clock ahead of monotonic (or a tick this late) by this much = it slept


class WakeWatch:
    def __init__(self, gap_s: float = WAKE_GAP_S, wall: Callable[[], float] = time.time,
                 mono: Callable[[], float] = time.monotonic, expect_s: float = 10.0):
        self.gap_s, self.wall, self.mono, self.expect_s = gap_s, wall, mono, expect_s
        self._last: tuple[float, float] | None = None

    def check(self, busy_s: float = 0.0) -> float | None:
        """Call once per housekeeping tick. Seconds the Mac was away (asleep or stalled), else None.

        busy_s: how long the previous tick's own work ran (the keepalive, a screen read). The loop sleeps
        expect_s AFTER that work, so the next check comes expect_s + busy_s later on a healthy Mac; without
        it a slow keepalive (a stick timing out) read as a stall and reset every connection."""
        w, m = self.wall(), self.mono()
        last, self._last = self._last, (w, m)
        if last is None:
            return None
        dw, dm = w - last[0], m - last[1]
        slept = dw - dm                       # wall ran ahead: asleep
        stalled = dm - self.expect_s - max(0.0, busy_s)   # the tick itself came late
        away = max(slept, stalled)
        return away if away >= self.gap_s else None


def recover(engine: Any, why: str) -> list[str]:
    """Make every connection re-prove itself now. Returns what it did (for the log and tests).
    Never raises: each step is independent."""
    did: list[str] = []
    ctl = getattr(engine, "control", None)

    def step(name: str, fn: Callable[[], Any]) -> None:
        try:
            fn()
            did.append(name)
        except Exception:
            log.exception("wake recovery step %s failed", name)

    if ctl is not None:
        def sticks():
            for s in getattr(ctl, "sticks", []) or []:
                s.last_ok = 0.0               # _reconnect runs on the next keepalive
                br = getattr(s, "breaker", None)
                if br is not None:
                    br.probe_now()
        step("sticks", sticks)
        tv = getattr(ctl, "tv", None)
        if tv is not None:
            def tv_reset():
                tv._drop_local()
                reset = getattr(tv, "reset_backoff", None)
                if reset is not None:
                    reset()                   # a backoff from before the sleep says nothing about the TV now
                br = getattr(tv, "st_breaker", None)
                if br is not None:
                    br.probe_now()
            step("tv", tv_reset)

        def resync():
            ctl._cec_synced_at = 0.0          # re-read which stick the TV shows
            ctl._verified_at = 0.0            # re-verify the standby
            ctl._tv_maintained_at = 0.0       # renew the SmartThings sign-in / socket now
        step("resync", resync)
    if getattr(engine, "fastcast", None) is not None and hasattr(engine, "_stop_fastcast"):
        step("fastcast", engine._stop_fastcast)   # the next sync (2 s) opens a fresh socket
    fetcher = getattr(engine, "fetcher", None)
    if fetcher is not None and getattr(fetcher, "_backoff", None):
        step("http_backoff", fetcher._backoff.clear)
    log.warning("Mac %s: reconnecting everything now (%s)", why, ", ".join(did) or "nothing to do")
    return did
