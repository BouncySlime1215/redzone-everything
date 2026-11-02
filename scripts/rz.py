"""Hands-on stick tools for testing (Phase 2 and the Screen Lab).

    .venv/bin/python scripts/rz.py tune espn          # tune YouTube TV to a channel you've linked
    .venv/bin/python scripts/rz.py link espn "<paste tv.youtube.com/watch/... link>"
    .venv/bin/python scripts/rz.py prime <GTI>        # open a Prime Video event
    .venv/bin/python scripts/rz.py now                # what the stick says is playing + on-screen text
    .venv/bin/python scripts/rz.py rewind 30          # rewind ~30s (measures how far each press goes)
    .venv/bin/python scripts/rz.py screencap          # does a screenshot capture video, or black? (DRM test)
    .venv/bin/python scripts/rz.py key KEYCODE_DPAD_CENTER
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone.channels import ChannelMap  # noqa: E402
from redzone.config import Config  # noqa: E402
from redzone.tuners.adb import ADB, Stick  # noqa: E402
from redzone.tuners.tuners import TUNERS  # noqa: E402


async def stick() -> Stick:
    d = json.loads((ROOT / "data/devices.json").read_text()) if (ROOT / "data/devices.json").exists() else {}
    if not d.get("sticks"):
        sys.exit("No sticks saved yet — run scripts/doctor.py first.")
    s0 = d["sticks"][0]
    st = Stick(s0["host"], name=s0.get("name", ""), os=s0.get("os", ""))
    st.packages = s0.get("packages", {})
    if not await st.connect():
        sys.exit(f"Couldn't connect to {st.host} ({st.state}).")
    if not st.packages:
        await st.identify()
    return st


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
    st = await stick()
    if cmd == "tune":
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
