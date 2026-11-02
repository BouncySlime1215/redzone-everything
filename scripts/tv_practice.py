"""TV practice: the replay demo picks the games, the real sticks do the switching.

    1. run the replay demo (it decides, on yesterday's games):
         python -m redzone.demo 20261101 --speed 4 --port 8790 --start 15:30
    2. stop the live service (both would drive the sticks), then:
         .venv/bin/python scripts/tv_practice.py --demo http://localhost:8790

Yesterday's games aren't on TV any more, so each game gets a stand-in live YouTube TV channel
(same game -> same channel all session). Every switch the demo makes goes through the real
Controller: hidden-stick tune, HDMI flip, playback confirm. Prints how long each one took.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from redzone.channels import ChannelMap  # noqa: E402
from redzone.config import Config  # noqa: E402
from redzone.control import Controller  # noqa: E402

STAND_INS = ["espn", "fs1", "espn2", "tnt", "nbatv", "trutv", "nflnetwork"]
REDZONE_STAND_IN = "nflnetwork"


def state(url: str) -> dict:
    with urllib.request.urlopen(f"{url}/api/state", timeout=3) as r:
        return json.load(r)


class StandIns:
    def __init__(self, codes: dict):
        self.pool = [c for c in STAND_INS if c in codes]
        self.by_game: dict[str, str] = {}

    def target(self, gid: str | None) -> str | None:
        if not gid:
            return None
        if gid == "redzone":
            return f"youtubetv:{REDZONE_STAND_IN}"
        if gid not in self.by_game:
            self.by_game[gid] = self.pool[len(self.by_game) % len(self.pool)]
        return f"youtubetv:{self.by_game[gid]}"


async def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--demo", default="http://localhost:8790")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    cfg = Config()
    cfg.data["mode"]["dry_run"] = False   # this run only; data/config.yaml is left as it is
    channels = ChannelMap()
    ctl = Controller(cfg, channels)
    await ctl.start()
    await ctl.keepalive()   # connects the sticks
    print(f"sticks: {[(s.host, s.state) for s in ctl.sticks]}  two_stick={ctl.two_stick}  live={ctl.live_control}")
    if not ctl.live_control:
        sys.exit("TV control is off (dry run?) - nothing would move")
    stand = StandIns(channels.codes)
    keep = asyncio.create_task(_keepalive(ctl))
    shown, preloaded = None, None
    try:
        while True:
            try:
                st = state(a.demo)
            except OSError:
                await asyncio.sleep(1)
                continue
            now = st.get("now") or {}
            gid = now.get("game_id")
            if gid and gid != shown:
                shown = gid
                tgt = stand.target(gid)
                t0 = time.monotonic()
                clock = (st.get("mode", {}).get("demo") or {}).get("clock", "")
                print(f"[{clock}] -> {now.get('matchup')}  ({tgt})  {now.get('reason', '')[:70]}")

                def done(out, t0=t0, tgt=tgt):
                    print(f"     {'OK ' if out.ok else 'FAIL'} {time.monotonic() - t0:4.1f}s  {tgt}  {out.detail}")
                ctl.submit(tgt, on_done=done)
            # The demo's controller never preloads, so pick the standby the way it ranks games:
            # the best live game that isn't on screen.
            live = [g for g in st.get("games") or [] if g.get("phase") == "live" and g.get("id") != shown]
            nxt = (st.get("next") or {}).get("game_id") or (
                max(live, key=lambda g: g.get("score") or 0)["id"] if live else None)
            if nxt and nxt != shown and nxt != preloaded:
                preloaded = nxt
                ctl.preload(stand.target(nxt))
            await asyncio.sleep(0.5)
    finally:
        keep.cancel()
        await ctl.close()


async def _keepalive(ctl: Controller) -> None:
    while True:
        try:
            await ctl.keepalive()
        except Exception:
            logging.exception("keepalive")
        await asyncio.sleep(10)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
