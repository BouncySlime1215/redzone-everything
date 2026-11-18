"""[STK-07 rig check] Hold the left game, then go back to it: does it play on from the dead ball we left?

Stop the live service first (both would drive the sticks). Uses the real Controller and config, with
buffer.hold_left on for this run only (data/config.yaml is left as it is):

    .venv/bin/python scripts/hold_left_probe.py --a espn --b fs1 --away 30

  1. switch to channel A (YouTube TV) and let it play
  2. switch to B: the stick we left must pause A where we left it (STK-07 `left_held`)
  3. wait --away seconds, reading the held stick (still paused at the same position?)
  4. switch back to A: path must be "left hold", played on from the pause, held ~away s behind live
Prints one JSON verdict. Never prints config values. Serves: STK-07. Owner: lane 3.
Why: STK-07 rig check on the real sticks, approved by Nick for a 10-minute test (docstring, commit).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from redzone.channels import ChannelMap  # noqa: E402
from redzone.config import Config  # noqa: E402
from redzone.control import Controller  # noqa: E402


async def run(a: argparse.Namespace) -> dict:
    cfg = Config()
    cfg.data["mode"]["dry_run"] = False
    cfg.data.setdefault("buffer", {})["hold_left"] = True
    ctl = Controller(cfg, ChannelMap())
    await ctl.start()
    await ctl.keepalive()
    if not (ctl.live_control and ctl.two_stick):
        return {"ok": False, "why": f"live={ctl.live_control} two_stick={ctl.two_stick}"}
    ta, tb = f"youtubetv:{a.a}", f"youtubetv:{a.b}"
    res: dict = {"a": ta, "b": tb}
    try:
        o1 = await ctl.execute(ta, may_cut=lambda: True)
        res["to_a"] = {"ok": o1.ok, "path": o1.path, "detail": o1.detail}
        await asyncio.sleep(a.settle)
        o2 = await ctl.execute(tb, may_cut=lambda: True)
        res["to_b"] = {"ok": o2.ok, "path": o2.path, "detail": o2.detail}
        if ctl._refresh_task:
            await asyncio.wait_for(asyncio.gather(ctl._refresh_task, return_exceptions=True), 20)
        lh = ctl._left
        res["held"] = bool(lh and lh.target == ta)
        reads = []
        end = time.time() + a.away
        while time.time() < end:
            hid = ctl.hidden
            pb = await hid.playback(ctl._package(hid, ta)) if hid else {}
            reads.append({"t": round(time.time() - (lh.at if lh else time.time()), 1), "state": pb.get("state"),
                          "position": pb.get("position")})
            await asyncio.sleep(5)
        res["while_away"] = reads
        res["stayed_paused"] = bool(reads) and all(r["state"] == "paused" for r in reads) \
            and len({r["position"] for r in reads}) == 1
        o3 = await ctl.execute(ta, may_cut=lambda: True)
        res["back_to_a"] = {"ok": o3.ok, "path": o3.path, "detail": o3.detail, "resume_s": o3.resume_s,
                            "held_behind_s": ctl.held.get(ta)}
        pb = await ctl.main.playback(ctl._package(ctl.main, ta))
        res["after"] = {"state": pb.get("state"), "position": pb.get("position")}
        res["ok"] = bool(res["held"] and res["stayed_paused"] and o3.ok and o3.path == "left hold"
                         and pb.get("state") == "playing")
    finally:
        await ctl.close()
    return res


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--a", default="espn", help="channel A (YouTube TV channel key)")
    p.add_argument("--b", default="fs1", help="channel B")
    p.add_argument("--away", type=float, default=30.0, help="seconds on B before going back")
    p.add_argument("--settle", type=float, default=8.0, help="seconds on A before leaving it")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    out = asyncio.run(run(a))
    print(json.dumps(out, indent=1, default=str))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
