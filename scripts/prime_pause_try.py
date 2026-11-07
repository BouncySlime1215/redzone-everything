"""Run prime_pause.enter_buffer_pause once on the HIDDEN Prime stick and print the result row.

    python scripts/prime_pause_try.py <stick ip:port> [seconds=60]

Only on the hidden stick, with a live Prime game playing: it presses MEDIA_PAUSE, holds, then MEDIA_PLAY, and reads
Prime's overlay timecode (frames deleted at once). No relink is wired here; on a failure, re-tune it by hand. Prints
JSON only; the stick address is not printed.
"""
import asyncio
import json
import sys

from redzone.tuners import prime_pause as pp
from redzone.tuners.adb import Stick


async def main(host: str, seconds: float) -> None:
    stick = Stick(host=host)
    await stick.connect()
    r = await pp.enter_buffer_pause(stick, seconds, still_hidden=lambda: True)
    print(json.dumps(r.row(), indent=1))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 60.0))
