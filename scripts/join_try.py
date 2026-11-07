"""Run one join (redzone/tuners/join.py) on the HIDDEN stick and print its row (also appended to data/joins.jsonl).

    python scripts/join_try.py <stick ip:port> youtubetv <target_s> [channel]
    python scripts/join_try.py <stick ip:port> prime <target_s> [channel]

YouTube TV: n = round((T - 10) / 15) LEFTs, predicted landing 15 n + 11.7 s; start from the live edge (tune another
channel, then this one). Prime: a pause of T s from wherever it plays (its timecode measures the gain). No relink is
wired here: on a failure, re-tune by hand. The stick address is never printed or logged.
"""
import asyncio
import json
import sys

from redzone.tuners import join
from redzone.tuners.adb import Stick


async def main(host: str, app: str, target: float, channel: str) -> None:
    stick = Stick(host=host)
    await stick.connect()
    r = await join.join(stick, app, target, still_hidden=lambda: True, label="hidden", channel=channel)
    print(json.dumps(r.row(), indent=1))


if __name__ == "__main__":
    a = sys.argv[1:]
    asyncio.run(main(a[0], a[1], float(a[2]), a[3] if len(a) > 3 else ""))
