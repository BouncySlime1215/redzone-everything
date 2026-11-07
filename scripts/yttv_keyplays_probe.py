"""[YTTV key plays, item 8] Run yttv_keyplays on ONE hidden YouTube TV stick and print numbers/text only.

    # read the Key plays list, then deep link back to live (nothing selected)
    python scripts/yttv_keyplays_probe.py --host <hidden stick ip:port> --channel <yttv code> --read
    # play the row at index 0 (as read), let it roll 25 s, then deep link back to live
    python scripts/yttv_keyplays_probe.py --host ... --channel ... --play-index 0 --roll-s 25
    # spoiler mode (no rows): select 'Catch up via key plays', reel from 1 of N
    python scripts/yttv_keyplays_probe.py --host ... --channel ... --catch-up --roll-s 25
    # play the row matching a game clock, e.g. 4th quarter 2:31
    python scripts/yttv_keyplays_probe.py --host ... --channel ... --period 4 --clock 2:31 --roll-s 25

Hidden check: before every key the redzone app's http://localhost:8778/api/state must list exactly one stick
on screen and --host as standby; anything else refuses (no keys). --assume-hidden skips that check: use it only
when the TV input is on the other stick and the redzone app is not running.

Screen reads are screencap -> local OCR -> PNG deleted at once. Output: one JSON line (steps, rows, timings,
prompt answer, reel N of M). Nothing is written to disk; the flag is forced on only inside this process.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from redzone.tuners import yttv_keyplays as kp  # noqa: E402
from redzone.tuners.adb import Stick  # noqa: E402

STATE_URL = "http://localhost:8778/api/state"
# YouTube TV watch codes (same as docs/sync/yttv_tests/mechanics/mech_common.py); a raw code also works
CODES = {"espn": "ferg3lVdMOg", "espn2": "qHoTWZ9M9gw", "tnt": "lVQYDh9weHg", "tbs": "vv_r1X6N3sc",
         "nbatv": "ykTstVbuNpg", "fs1": "_2ONrjDR7S8", "usa": "ZgiS2N0QkbU", "trutv": "x2IdxPvLNC0",
         "fox": "njJRoIW1Lps", "cbssn": "XG1jwCkVgUE", "nflnetwork": "_pYg9qMKKIA", "espnu": "6x-T96Q-5eY",
         "btn": "1D2LKf_AW5w"}


class _Cfg(dict):
    def get(self, k, d=None):
        return super().get(k, d)


def hidden_by_app(host: str, url: str = STATE_URL) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=2.5) as r:
            d = json.load(r)
    except Exception as e:
        return False, f"state fetch failed: {e!r}"[:120]
    health = d.get("health") or {}
    on, standby = set(), set()
    for s in health.get("sticks") or []:
        role = str(s.get("role") or "").lower().replace("_", " ")
        h = s.get("host")
        if role in ("on screen", "onscreen", "visible", "active", "live"):
            on.add(h)
        elif role in ("standby", "hidden", "parked", "off screen", "offscreen"):
            standby.add(h)
    agent_on = (health.get("agent") or {}).get("on_screen")
    if agent_on:
        on.add(agent_on)
        standby.discard(agent_on)
    if len(on) != 1 or host in on or host not in standby:
        return False, f"not confirmed hidden (on={sorted(map(str, on))})"
    return True, "api/state"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", required=True, help="the HIDDEN stick, ip:port")
    ap.add_argument("--channel", required=True, help="channel it is on (espn, nbatv, tnt, ... or a raw YouTube TV watch code): the deep link back to live")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--read", action="store_true", help="read the list only, then back to live")
    g.add_argument("--play-index", type=int, help="play the row at this index (as read, top = 0)")
    g.add_argument("--clock", help="play the row with this game clock MM:SS (with --period)")
    g.add_argument("--catch-up", action="store_true", help="select 'Catch up via key plays' (reel from 1 of N; "
                                                            "the only pick in spoiler mode)")
    ap.add_argument("--period", type=int, default=None, help="quarter/half; OT = 5")
    ap.add_argument("--roll-s", type=float, default=25.0, help="let the reel play this long before going live")
    ap.add_argument("--assume-hidden", action="store_true", help="skip the app's hidden check (see above)")
    a = ap.parse_args()

    last = {"t": 0.0, "ok": False, "why": ""}

    def still_hidden() -> bool:
        if a.assume_hidden:
            return True
        if time.monotonic() - last["t"] > 1.0:
            last["ok"], last["why"] = hidden_by_app(a.host)
            last["t"] = time.monotonic()
        return last["ok"]

    async def run() -> dict:
        stick = Stick(host=a.host)
        if not await stick.connect():
            return {"ok": False, "reason": "adb connect failed"}
        await stick.identify()
        pkg = stick.packages.get("youtubetv")
        url = f"https://tv.youtube.com/watch/{CODES.get(a.channel.lower(), a.channel)}"

        async def relink() -> bool:
            if not still_hidden():
                return False
            r = await stick.view(url, pkg or "", fresh=False)
            return bool(r.ok)

        cfg = _Cfg({"yttv_keyplays.enabled": True})
        out: dict = {"host_role": "assumed hidden" if a.assume_hidden else hidden_by_app(a.host)[1]}
        if a.read:
            res = await kp.read_key_plays(stick, cfg=cfg, pkg=pkg, still_hidden=still_hidden, relink=relink)
            out["read"] = res.row()
            return out
        if a.catch_up:
            want = kp.Want(from_start=True)
        elif a.play_index is not None:
            want = kp.Want(index=a.play_index)
        else:
            want = kp.Want(period=a.period, clock_s=kp.parse_clock(a.clock)[1])
        res = await kp.catch_up(stick, want, cfg=cfg, pkg=pkg, still_hidden=still_hidden, relink=relink)
        out["catch_up"] = res.row()
        if res.ok:
            await asyncio.sleep(max(0.0, a.roll_s))
            after = await kp.ocr_panel(stick)
            out["after_roll"] = after.brief() if after is not None else None
            if still_hidden():
                ok, secs = await kp.return_to_live(stick, relink, cfg=cfg, pkg=pkg)
                out["return_to_live"] = {"ok": ok, "s": None if secs is None else round(secs, 2)}
            else:
                out["return_to_live"] = {"ok": False, "why": "stick no longer hidden: not touched"}
        return out

    out = asyncio.run(run())
    print(json.dumps(out, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
