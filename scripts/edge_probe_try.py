"""[edge prior, Nick Nov 7] Measure YouTube TV's live-edge delay on the HIDDEN stick, Nick's way, and print JSON rows.

    python scripts/edge_probe_try.py <stick ip:port> <channel> [<channel> ...] [--reset=<other channel>]
                                     [--repeat=N] [--gap-s=S]

Per channel: tune --reset (default: any other channel with a watch code), then the channel (a fresh live edge,
#391), CENTER, RIGHT, one screencap -> OCR -> the PNG deleted, BACK. delay = (capture - EPG start) - label, the
start = the :00/:30 boundary (the EPG window when the overlay shows one). An empty label is skipped, not guessed.
--repeat runs each channel N times, --gap-s apart (default 0: the min_gap_s pacing is the app's, not this script's).
Prints one row per read, then a summary (n, mean, sd, min, max) that you can compare with your 34.9 +-0.4.
Rows also go to data/edge_probe.jsonl (numbers, channel key, status; never the label text or the stick address).
HIDDEN stick only: this tunes it twice and opens the overlay.
"""
import asyncio
import json
import statistics
import sys

from redzone.tuners.adb import Stick
from redzone.tuners.yttv_edge_probe import EdgeProbe


async def main(host: str, channels: list[str], reset: str, repeat: int, gap_s: float) -> None:
    from redzone.channels import ChannelMap
    from redzone.tuners.tuners import TUNERS
    stick = Stick(host=host)
    if not await stick.connect():
        print(json.dumps({"error": "stick not connected", "state": stick.state}))
        return
    await stick.identify()          # without it every tune fails "youtubetv app not found" (watchdog_try, Nov 7)
    cm = ChannelMap()
    tuner = TUNERS["youtubetv"]
    probe = EdgeProbe()
    delays: list[float] = []
    for raw in channels:
        ch = raw.lower().replace(" ", "")
        if not cm.yttv_code(f"youtubetv:{ch}"):
            print(json.dumps({"channel": ch, "error": "no watch code", "known": sorted(cm.codes)[:40]}))
            continue
        other = (reset.lower().replace(" ", "") or next((k for k in sorted(cm.codes) if k != ch), ""))

        async def relink(ch=ch, other=other) -> bool:
            for c in (other, ch):
                code = cm.yttv_code(f"youtubetv:{c}")
                if not c or not code:
                    return False
                r = await tuner.tune(stick, code, f"youtubetv:{c}")
                if not r.ok:
                    print(json.dumps({"relink": c, "ok": False, "error": r.error}))
                    return False
            return True
        for i in range(max(1, repeat)):
            r = await probe.measure(stick, relink=relink, still_hidden=lambda: True, channel=ch)
            print(json.dumps({"channel": ch, "status": r.status, "delay_s": r.delay_s, "label_s": r.label_s,
                              "start_from": r.start_from, "took_s": r.elapsed_s, "keys": r.keys, "detail": r.detail}))
            if r.ok:
                delays.append(float(r.delay_s))
            if gap_s and i < repeat - 1:
                await asyncio.sleep(gap_s)
    s = {"n": len(delays), "skipped": len(probe.reads) - len(delays)}
    if delays:
        s.update(mean=round(statistics.mean(delays), 2), sd=round(statistics.pstdev(delays), 2),
                 min=min(delays), max=max(delays))
    print(json.dumps({"summary": s}))


if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if not x.startswith("--")]
    opt = {x.split("=", 1)[0][2:]: x.split("=", 1)[1] for x in sys.argv[1:] if x.startswith("--") and "=" in x}
    if len(a) < 2:
        print(__doc__)
        raise SystemExit(2)
    asyncio.run(main(a[0], a[1:], opt.get("reset", ""), int(opt.get("repeat", 1)), float(opt.get("gap-s", 0))))
