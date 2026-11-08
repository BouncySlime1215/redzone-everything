"""Park the HIDDEN YouTube TV stick just before a big play's snap (redzone/tuners/replay_park.py), then PLAY on Enter.

    python scripts/replay_park_try.py <stick ip:port> <play_ago_s> [--no-play]

<play_ago_s>: how many seconds ago the snap aired LIVE (wall clock; e.g. a play you saw at 1:02:10 and it is now
1:02:40 -> 30). Start with the stick PLAYING the game's channel at the live edge (tune another channel, then the
game: #391) and hidden from the TV. The script scrubs it (CENTER, RIGHT probe, k LEFTs, CENTER), reads the landing on
the overlay label, lets it play up to replay.park_lead_s (1.5 s) before the snap and pauses it. Then switch the TV to
that stick and press Enter: it sends MEDIA_PLAY, and the snap should come 1-2 s later. Prints JSON only; frames are
read for numbers, then deleted; the stick address is never printed. Nothing else on the Mac changes.
"""
import asyncio
import json
import sys
import time

from redzone.tuners import replay_park
from redzone.tuners.adb import Stick
from redzone.tuners.yttv_label import LabelProbe


async def main(host: str, play_ago_s: float, play: bool) -> None:
    stick = Stick(host=host)
    if not await stick.connect():
        print(json.dumps({"error": "stick not connected"}))
        return
    await stick.identify()                       # without it the stick has no app packages and the pause reads 'none'
    pkg = stick.packages.get("youtubetv")
    if not pkg:
        print(json.dumps({"error": "no YouTube TV package found on the stick"}))
        return
    back = play_ago_s + float(replay_park.PARK_DEFAULTS["back_lead_s"])
    print(json.dumps({"play_ago_s": play_ago_s, "back_s": back, "lefts": replay_park.lefts_for(back)}))
    r = await replay_park.park(stick, back, probe=LabelProbe(), pkg=pkg)
    row = {k: v for k, v in r.__dict__.items()}
    print(json.dumps(row, indent=1))
    if r.ok and play:
        t = time.time()
        await asyncio.to_thread(input, "Parked. Switch the TV to this stick, then press Enter to PLAY: ")
        sent = await stick.key("KEYCODE_MEDIA_PLAY")
        print(json.dumps({"play_sent": bool(sent and sent.ok), "parked_for_s": round(time.time() - t, 1),
                          "expect": "the snap about 1.5 s after PLAY"}))


if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if not x.startswith("--")]
    asyncio.run(main(a[0], float(a[1]), "--no-play" not in sys.argv))
