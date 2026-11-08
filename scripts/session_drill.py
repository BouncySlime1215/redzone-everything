"""Session drill (sync hardening item 4): the real yttv_seek.enter_buffer against the Nov 6 post-tune banner, with and
without the session guard's banner wait, on virtual time. Offline: a fake stick, no adb, no TV.

With the guard, the scrub first looks at the screen (no key): video is DRM-black, so any UI text right after a tune
is the banner; it waits (polling every 2 s, at most 30 s) until the screen is clear, then scrubs.

The banner (Nov 6 live): after a tune a LIVE/title banner sits up 20-25+ s. It looks like the overlay (it has the CC
button) but keys do nothing there, so the scrub's LEFT probe finds no label, closes, reopens and tries again, and
fails closed after open_tries. Each scenario reports:
  ok        the scrub committed and was read
  took_s    seconds from the end of the tune to the end of the scrub (incl. any banner wait)
  keys      keys sent to the stick
  wasted    keys sent while the banner was up (they did nothing, and each is a chance to land on a half-drawn overlay)

    python scripts/session_drill.py [--json]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from redzone.tuners import session_guard as sg  # noqa: E402
from redzone.tuners import yttv_seek as ys  # noqa: E402
from redzone.tuners.adb import RunResult  # noqa: E402

YT = "com.amazon.firetv.youtube.tv"
TUNE_S = 5.0        # the tune itself (YouTube TV in-app tune median 4.7 s, Nov 2-3)
READ_S = 2.0        # one overlay screencap + OCR


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class BannerStick:
    def __init__(self, clock: Clock, banner_s: float, step=15.0):
        self.c, self.banner_s, self.step = clock, banner_s, step
        self.overlay = self.on_bar = False
        self.knob = self.label0 = 1800.0
        self.keys, self.wasted, self.state = [], 0, "playing"

    def banner(self) -> bool:
        return self.c() < self.banner_s

    async def shell(self, cmd, timeout=8.0):
        code = cmd.split()[-1]
        self.keys.append(code)
        self.c.t += 0.05
        if self.banner():
            self.wasted += 1
            self.overlay = not self.overlay   # the banner toggles like the overlay but takes no seek keys
            self.on_bar = False
            return RunResult(True, "", 0.05)
        if code == "KEYCODE_DPAD_CENTER":
            if not self.overlay:
                self.overlay, self.on_bar, self.knob = True, False, self.label0
            else:
                self.overlay, self.on_bar = False, False
        elif code == "KEYCODE_DPAD_LEFT" and self.overlay:
            self.on_bar = True
            self.knob -= self.step
        return RunResult(True, "", 0.05)

    async def foreground(self):
        return f"{YT}/.Main"

    async def playback(self, package=None):
        return {"state": self.state}


def reader(st: BannerStick):
    async def read(_s):
        st.c.t += READ_S
        if not st.overlay:
            return ys.OverlayRead()
        obs = [{"text": "cc", "x": .2, "y": .9}]
        if st.on_bar:
            obs.append({"text": f"{int(st.knob // 60)}:{int(st.knob % 60):02d}", "x": .5, "y": .76})
        return ys.parse_overlay(obs)
    return read


async def one(banner_s: float, guard: bool) -> dict:
    clock = Clock()
    clock.t = TUNE_S
    st = BannerStick(clock, banner_s)
    real_sleep = asyncio.sleep

    async def vsleep(s, *a, **k):
        clock.t += max(0.0, float(s))
        await real_sleep(0)
    asyncio.sleep = vsleep
    try:
        if guard:
            g = sg.SessionGuard(cfg=_Cfg({"session_guard.enabled": True}))

            async def look(_st, pkg, since_tune_s=None, read_screen=False, **kw):
                clock.t += READ_S if read_screen else 0.3      # one screencap + OCR, no key
                rows = [sg.Row("LIVE"), sg.Row("NBA Basketball"), sg.Row("CC")] if st.banner() else []
                return sg.Snapshot(host="b", wall=clock(), reachable=True, package=YT, want_pkg=YT, state="playing",
                                   since_tune_s=since_tune_s, rows=rows if read_screen else None)
            await sg.settle_banner(g, st, YT, clock, look=look)

        async def behind(_s):
            clock.t += 7.5     # one label read (Nick, Nov 7: +7.5 s per read)
            return 60.0
        res = await ys.enter_buffer(st, 60.0, pkg=YT, read_overlay=reader(st), read_behind=behind,
                                    relink=_relink, clock=clock)
    finally:
        asyncio.sleep = real_sleep
    return {"banner_s": banner_s, "guard": guard, "ok": res.ok, "took_s": round(clock() - TUNE_S, 1),
            "keys": len(st.keys), "wasted": st.wasted, "reason": res.reason}


async def _relink():
    return True


class _Cfg(dict):
    def get(self, k, d=None):
        return super().get(k, d)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    rows = [asyncio.run(one(b, g)) for b in (0.0, 10.0, 20.0, 25.0, 30.0, 35.0) for g in (False, True)]
    if a.json:
        print(json.dumps(rows, indent=2))
        return
    print(f"{'banner_s':>8} {'guard':>5} {'ok':>3} {'took_s':>7} {'keys':>4} {'wasted':>6}  reason")
    for r in rows:
        print(f"{r['banner_s']:>8.0f} {str(r['guard']):>5} {'Y' if r['ok'] else '-':>3} {r['took_s']:>7.1f} "
              f"{r['keys']:>4} {r['wasted']:>6}  {r['reason'][:60]}")


if __name__ == "__main__":
    main()
