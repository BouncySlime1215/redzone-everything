"""Virtual time for the end-to-end night: hours of a real night run in minutes.

install(t0) must run BEFORE anything from redzone is imported. Many classes take
`clock=time.time` as a default argument, which binds the function object when the module is
imported; replacing time.time first means every one of them (Merger, Scorer, Decider, DelayBook,
Supervisor, Planner, Reeler...) reads the virtual clock without a single change to their code.

The event loop (VirtualLoop) never sleeps: when nothing is ready it jumps the clock straight to
the next timer. Work handed to a thread (asyncio.to_thread: OCR, image decoding) is treated as
instantaneous: while a thread is out the clock is frozen and the loop waits for it in real time.

Why: first added in 71e46ec6 (2026-11-03) as "WIP: end-to-end replayed night test (fake sticks/TV, virtual clock, fault hook); not runnable yet"; no separate decision is recorded for this module.
"""
from __future__ import annotations

import asyncio
import selectors
import time as _time

_REAL = {"time": _time.time, "monotonic": _time.monotonic, "time_ns": _time.time_ns,
         "monotonic_ns": _time.monotonic_ns}


class VirtualClock:
    """Wall-clock seconds (time.time); monotonic runs from MONO0 in step with it."""
    MONO0 = 10_000.0

    def __init__(self, t0: float):
        self.t0 = float(t0)
        self.dt = 0.0            # seconds since t0, kept small so tiny steps don't vanish in a big float
        self.skew = 0.0          # a fault can skew the wall clock only (NTP jump); monotonic never jumps

    @property
    def t(self) -> float:
        return self.t0 + self.dt

    @t.setter
    def t(self, value: float) -> None:
        self.dt = float(value) - self.t0

    def reset(self, t0: float) -> None:
        """Start the night at t0 (only before anything has scheduled a timer)."""
        self.t0, self.dt = float(t0), 0.0

    def time(self) -> float:
        return self.t0 + self.dt + self.skew

    def monotonic(self) -> float:
        return self.MONO0 + self.dt

    def time_ns(self) -> int:
        return int(self.time() * 1e9)

    def monotonic_ns(self) -> int:
        return int(self.monotonic() * 1e9)

    def advance(self, dt: float) -> None:
        if dt > 0:
            # At least a microsecond: asyncio rounds a timer due "now" to a few ns of timeout.
            self.dt += max(dt, 1e-6)


CLOCK: VirtualClock | None = None


def install(t0: float) -> VirtualClock:
    """Replace time.time / monotonic (and their _ns twins) with the virtual clock, process-wide."""
    global CLOCK
    if CLOCK is not None:
        CLOCK.reset(t0)
        return CLOCK
    CLOCK = VirtualClock(t0)
    _time.time = CLOCK.time
    _time.monotonic = CLOCK.monotonic
    _time.time_ns = CLOCK.time_ns
    _time.monotonic_ns = CLOCK.monotonic_ns
    return CLOCK


class _Selector(selectors.BaseSelector):
    """The real selector, except that "wait `timeout` seconds" moves the virtual clock instead."""

    def __init__(self, clock: VirtualClock, loop_ref):
        self._real = selectors.DefaultSelector()
        self._clock = clock
        self._loop = loop_ref

    def register(self, fileobj, events, data=None):
        return self._real.register(fileobj, events, data)

    def unregister(self, fileobj):
        return self._real.unregister(fileobj)

    def modify(self, fileobj, events, data=None):
        return self._real.modify(fileobj, events, data)

    def get_key(self, fileobj):
        return self._real.get_key(fileobj)

    def get_map(self):
        return self._real.get_map()

    def close(self):
        self._real.close()

    def select(self, timeout=None):
        loop = self._loop()
        if loop is not None and loop.threads_out:
            # A thread is working: virtual time stands still until it reports back.
            return self._real.select(0.05)
        events = self._real.select(0)
        if events:
            return events
        if timeout is None:
            return self._real.select(0.05)   # nothing scheduled at all: let real I/O (if any) arrive
        self._clock.advance(timeout)
        return []


class VirtualLoop(asyncio.SelectorEventLoop):
    def __init__(self, clock: VirtualClock):
        import weakref
        self.threads_out = 0
        self.vclock = clock
        ref = None
        sel = _Selector(clock, lambda: ref() if ref else None)
        super().__init__(selector=sel)
        ref = weakref.ref(self)
        sel._loop = ref

    def time(self) -> float:
        return self.vclock.monotonic()

    def run_in_executor(self, executor, func, *args):
        fut = super().run_in_executor(executor, func, *args)
        self.threads_out += 1

        def done(_):
            self.threads_out -= 1
        fut.add_done_callback(done)
        return fut


def run(coro, clock: VirtualClock):
    """asyncio.run on the virtual loop."""
    loop = VirtualLoop(clock)
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        try:
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for t in pending:
                t.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.run_until_complete(loop.shutdown_default_executor())
        finally:
            asyncio.set_event_loop(None)
            loop.close()
