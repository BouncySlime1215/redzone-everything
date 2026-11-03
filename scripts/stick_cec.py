"""HDMI-CEC on the Fire TV sticks: read it, or turn it off / on, over adb.

    .venv/bin/python scripts/stick_cec.py status
    .venv/bin/python scripts/stick_cec.py off       # the stick that loses the input never pauses
    .venv/bin/python scripts/stick_cec.py on        # back to HOME-only switching

Off is only right once the TV's own input change works (scripts/tv_setup.py), because HOME needs
CEC to take the TV. The app turns CEC back on for a HOME fallback onto a stick by itself, and off
again right after. If adb can't change it: Settings > Display & Sounds > HDMI CEC Device Control.
See docs/instant-switching.md.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone import tvsetup  # noqa: E402


async def status(home: tvsetup.Home, sticks: list[dict]) -> None:
    for s in sticks:
        ok, out = await home.shell(s["host"], tvsetup.CEC_GET)
        v = out.strip().splitlines()[-1].strip() if ok and out.strip() else "?"
        print(f"  {s.get('name') or s['host']}: HDMI-CEC {'on' if v == '1' else 'off' if v == '0' else 'unknown (' + v + ')'}")


def main() -> int:
    what = (sys.argv[1:] or ["status"])[0]
    if what not in ("status", "on", "off"):
        print(__doc__)
        return 2
    d = tvsetup.load()
    sticks = d.get("sticks") or []
    home = tvsetup.Home(sticks)
    if what == "status":
        asyncio.run(status(home, sticks))
        return 0
    rep = tvsetup.Report()
    ok = asyncio.run(tvsetup.set_cec(home, sticks, what == "on", rep))
    if ok:
        d.setdefault("tv", {})["sticks_cec_off"] = what == "off"
        tvsetup.save(d)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
