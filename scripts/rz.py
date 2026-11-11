"""Hands-on stick tools for testing (Phase 2 and the Screen Lab).

    .venv/bin/python scripts/rz.py tune espn          # tune YouTube TV to a channel you've linked
    .venv/bin/python scripts/rz.py link espn "<paste tv.youtube.com/watch/... link>"
    .venv/bin/python scripts/rz.py prime <GTI>        # open a Prime Video event
    .venv/bin/python scripts/rz.py now                # what the stick says is playing + on-screen text
    .venv/bin/python scripts/rz.py rewind 30          # rewind ~30s (measures how far each press goes)
    .venv/bin/python scripts/rz.py screencap          # does a screenshot capture video, or black? (DRM test)
    .venv/bin/python scripts/rz.py key KEYCODE_DPAD_CENTER
    .venv/bin/python scripts/rz.py bench              # how fast is the stick? (ends with a real tune)
    .venv/bin/python scripts/rz.py bench espn         # ...tuning to this linked channel
    RZ_ADB_SESSION=0 .venv/bin/python scripts/rz.py bench   # same, the old way (one adb process per command)
    .venv/bin/python scripts/rz.py flipcheck 3 espn   # two sticks: time real input flips, and check the
                                                      # stick you leave keeps playing (instant "go back")
Why: Hands-on stick tools for testing: Phase 2 and the Screen Lab (docstring, stick control commit).
"""
from __future__ import annotations

import asyncio
import json
import math
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone.channels import SETUP_CHANNELS, ChannelMap  # noqa: E402
from redzone.config import Config  # noqa: E402
from redzone.tuners.adb import ADB, PLAYBACK_CMD, Stick  # noqa: E402
from redzone.tuners.tuners import TUNERS  # noqa: E402


async def stick() -> Stick:
    d = json.loads((ROOT / "data/devices.json").read_text()) if (ROOT / "data/devices.json").exists() else {}
    if not d.get("sticks"):
        sys.exit("No sticks saved yet — run scripts/doctor.py first.")
    s0 = d["sticks"][0]
    st = Stick(s0["host"], name=s0.get("name", ""), os=s0.get("os", ""))
    st.packages = s0.get("packages", {})
    if not await st.connect():
        await st.close()
        sys.exit(f"Couldn't connect to {st.host} ({st.state}).")
    if not st.packages:
        await st.identify()
    return st


async def _timed(make, n: int) -> list[float]:
    """Run make() n times, one after another; milliseconds for each try that worked."""
    out = []
    for _ in range(n):
        t0 = time.perf_counter()
        r = await make()
        if getattr(r, "ok", True):
            out.append((time.perf_counter() - t0) * 1000)
    return out


def _p90(ms: list[float]) -> float:
    s = sorted(ms)
    return s[max(0, math.ceil(0.9 * len(s)) - 1)]


def _say(label: str, ms: list[float]) -> None:
    if not ms:
        print(f"  {label}:\n      didn't work — run scripts/doctor.py")
        return
    print(f"  {label}:\n      median {statistics.median(ms):.0f} ms, 9 in 10 under {_p90(ms):.0f} ms  "
          f"({len(ms)} tries)")


async def bench(st: Stick, ch: ChannelMap, args: list[str], samples: int = 20) -> None:
    """Time the stick the old way vs the always-open shell, then a full YouTube TV tune."""
    few = max(3, samples // 4)
    print(f"\nTiming {st.name or st.host}. Nothing on screen changes until the tune at the end.\n")
    old = await _timed(lambda: st.run("shell", "echo ok"), samples)
    await st.shell("echo ok")              # opens the always-open shell (not counted)
    new = await _timed(lambda: st.shell("echo ok"), samples)
    print(f"  Always-open shell: {st.session_status()}\n")
    _say("One stick command, old way (a new adb process each time)", old)
    _say("One stick command, always-open shell", new)
    if old and new:
        print(f"      -> about {statistics.median(old) - statistics.median(new):.0f} ms saved on every "
              "stick command (a tune sends 5-30)")
    _say('"What\'s playing?" check, old way (full dump, new adb process)',
         await _timed(lambda: st.run("shell", "dumpsys media_session"), few))
    _say('"What\'s playing?" check, now (just the lines we read, always-open shell)',
         await _timed(lambda: st.shell(PLAYBACK_CMD + "; true"), samples))
    _say("Is the stick awake? (now asked at most once a minute, not a key press every tune)",
         await _timed(lambda: st.shell("dumpsys power | grep -m1 'mWakefulness='"), few))
    _say("Android starting its key-press program (every remote press pays this; nothing is pressed)",
         await _timed(lambda: st.shell("input >/dev/null 2>&1; true"), few))
    print(f"\n  The stick says it's playing: {await st.playback()}")
    await _bench_tune(st, ch, args)


async def _bench_tune(st: Stick, ch: ChannelMap, args: list[str]) -> None:
    key = args[0] if args else next((k for k in SETUP_CHANNELS if ch.codes.get(k)), next(iter(ch.codes), None))
    code = (ch.codes.get(key) or key) if key else None
    if not code:
        print("\n  No YouTube TV channel linked yet, so no tune timing. Link one, then run bench again:\n"
              '      .venv/bin/python scripts/rz.py link espn "<paste tv.youtube.com/watch/... link>"\n')
        return
    target = f"youtubetv:{key}"
    print(f"\n  Tuning YouTube TV to {key} — this changes what's on the TV …")
    res = await TUNERS["youtubetv"].tune(st, code, target)
    print(f"  Full tune: {'playing' if res.ok else 'FAILED (' + res.error + ')'} in {res.took:.1f}s")
    print("\n".join("      " + s for s in res.steps))
    if not res.ok:
        return
    again = await TUNERS["youtubetv"].tune(st, code, target)
    print(f"  Same channel again: {again.took * 1000:.0f} ms — {again.steps[0] if again.steps else ''}")
    print(f"  (the stick's report when it confirmed: {st.tuned['report'] if st.tuned else 'none'})\n")
async def flipcheck(rounds: int, channel: str | None) -> None:
    """Two sticks: the things only the real hardware can answer for hot standby.
    1. Is either stick asleep? (Waking one makes it grab the TV input, so the app never does that mid-game.)
    2. How long does an input flip take? (The app adds two_stick.input_settle_s for the picture.)
    3. Does the stick you flip AWAY from keep playing? If yes, "go back" is a 1-2s flip."""
    from redzone.control import Controller
    c = Controller(Config(), ChannelMap())
    await c.start()
    u = c.usable[:2]
    if len(u) < 2 or not all(s.host in c.hdmi for s in u):
        sys.exit("Needs two connected sticks with their TV inputs saved — run scripts/doctor.py first.")
    for s in u:
        print(f"{s.name or s.host} ({c.hdmi[s.host]}): {'ASLEEP' if await c._sleeping(s) else 'awake'}")
    if channel:   # put both on a live channel, so "still playing?" means something
        for s in u:
            res = await c._tune(s, f"youtubetv:{channel}")
            print(f"  tuned {s.name or s.host}: {'playing' if res.ok else res.error} in {res.took:.1f}s")
    stopped = 0
    for _ in range(rounds):
        for s, other in ((u[1], u[0]), (u[0], u[1])):
            t0 = time.monotonic()
            ok = await c._flip_to(s)
            how = "TV remote (CEC didn't work)" if s.host in c._cec_bad else "CEC"
            print(f"flip to {c.hdmi[s.host]}: {'ok' if ok else 'FAILED: ' + c.tv.last_error} in "
                  f"{time.monotonic() - t0:.2f}s via {how} — stopwatch how long until the picture shows")
            await asyncio.sleep(6)
            state = (await other.playback()).get("state")
            stopped += state != "playing"
            print(f"   the stick you left ({other.name or other.host}) is {state}")
    med = c.latency.median("flip")
    print(f"\nflip command median: {med:.2f}s" if med is not None else "\nno successful flips")
    print("Set two_stick.input_settle_s (now "
          f"{c.cfg.get('two_stick.input_settle_s')}s) to how long the picture took after that.")
    print("The stick you leave stops playing: going back reloads it first (a few seconds slower)."
          if stopped else "The stick you leave keeps playing: going back is just a flip.")


async def main(argv: list[str]) -> None:
    if not argv:
        print(__doc__)
        return
    cmd, args = argv[0], argv[1:]
    ch = ChannelMap()
    if cmd == "link":
        code = ch.set_code_from_link(args[0], args[1])
        print(f"Saved {args[0]} = {code}")
        return
    if cmd == "flipcheck":
        await flipcheck(int(args[0]) if args else 2, args[1] if len(args) > 1 else None)
        return
    st = await stick()
    try:
        await run(st, ch, cmd, args)
    finally:
        await st.close()    # the always-open adb shell


async def run(st: Stick, ch: ChannelMap, cmd: str, args: list[str]) -> None:
    if cmd == "bench":
        await bench(st, ch, args)
    elif cmd == "tune":
        code = ch.codes.get(args[0]) or args[0]
        t0 = time.time()
        res = await TUNERS["youtubetv"].tune(st, code, f"youtubetv:{args[0]}")
        print(("✅ playing" if res.ok else "❌ " + res.error), f"in {time.time() - t0:.1f}s")
        print("\n".join("   " + s for s in res.steps))
    elif cmd == "prime":
        t0 = time.time()
        res = await TUNERS["prime"].tune(st, args[0], f"prime:{args[0]}")
        print(("✅ playing" if res.ok else "❌ " + res.error), f"in {time.time() - t0:.1f}s")
        print("\n".join("   " + s for s in res.steps))
    elif cmd == "now":
        print("foreground:", await st.foreground())
        print("playback:  ", await st.playback())
        print("screen text:", (await st.screen_text())[:600] or "(none)")
    elif cmd == "rewind":
        cfg = Config()
        want = float(args[0]) if args else 30
        before = await st.playback()
        key = cfg.get("replay.seek_key")
        presses = max(1, round(want / cfg.get("replay.seek_step_s")))
        await st.shell("; ".join([f"input keyevent {key}"] * presses))
        await asyncio.sleep(2)
        await st.key("KEYCODE_MEDIA_PLAY")
        after = await st.playback()
        print("before:", before, "\nafter: ", after)
        if before.get("position") and after.get("position"):
            moved = (before["position"] - after["position"]) / 1000
            print(f"moved back ≈ {moved:.0f}s with {presses} presses → {moved / presses:.1f}s per press "
                  f"(set replay.seek_step_s to this in Settings)")
    elif cmd == "screencap":
        out = ROOT / "data" / "screencap_test.png"
        with open(out, "wb") as f:
            subprocess.run([ADB, "-s", st.host, "exec-out", "screencap", "-p"], stdout=f, timeout=20)
        size = out.stat().st_size
        print(f"saved {out} ({size // 1024} KB). Open it: if the game picture is black, DRM blocks screenshots "
              "(expected) and we rely on the stick's playback reports + microphone instead. "
              "This file is for your test only; delete it after looking.")
    elif cmd == "key":
        await st.key(*args)
        print("sent", *args)
    else:
        print(__doc__)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
