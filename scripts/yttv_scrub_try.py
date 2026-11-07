"""Run yttv_seek.enter_buffer once on the HIDDEN YouTube TV stick and print the result row (item 1, #377).

    python scripts/yttv_scrub_try.py <stick ip:port> [seconds=60] [--miss-probe]

Only on the hidden stick: it presses CENTER/LEFT/CENTER. No relink is wired here; on a failure, re-tune it to live by
hand. --miss-probe makes the first probe read come back unreadable, to check the blind-close path on hardware (the
stick should end one step further back than asked, and the row should say behind-live unknown). Prints JSON only; no
frames are kept and the stick address is not printed.
"""
import asyncio
import json
import sys

from redzone.tuners import yttv_seek as ys
from redzone.tuners.adb import Stick


async def main(host: str, seconds: float, miss_probe: bool) -> None:
    stick = Stick(host=host)
    await stick.connect()
    reads = []

    async def read(st):
        reads.append(1)
        if miss_probe and len(reads) == 2:
            return None
        return await ys.ocr_overlay(st)

    r = await ys.enter_buffer(stick, seconds, read_overlay=read, still_hidden=lambda: True)
    print(json.dumps(r.row(), indent=1))


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    asyncio.run(main(args[0], float(args[1]) if len(args) > 1 else 60.0, "--miss-probe" in sys.argv))
