"""Tune the LOCAL stations by YouTube TV search on the HIDDEN stick, through the app's own routing and
tuner as if tuners.yttv_local_search were on (ChannelMap.local_search -> search_ref ->
YouTubeTVTuner.tune). Reads data/channels.json, writes nothing (no channels.json or config change).

    .venv/bin/python scripts/yttv_search_tune_try.py <hidden stick ip[:port]> abc,cbs,fox,nbc

Per station: search (WRAL / ABC 11 / CBS 17 / FOX 50) -> channel page -> Select at the starting focus,
else reopen + DOWN + Select; accepted only on a media-session subtitle naming the station AND a live
playhead, else BACK. Run it only on the stick that is NOT on screen. The stick address is never printed.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from redzone.channels import LOCAL_SEARCH_QUERY, ChannelMap  # noqa: E402
from redzone.tuners.adb import Stick  # noqa: E402
from redzone.tuners.tuners import YouTubeTVTuner  # noqa: E402


async def main(host: str, keys: list[str]) -> int:
    bad = [k for k in keys if k not in LOCAL_SEARCH_QUERY]
    if bad:
        print(f"not a local search station: {', '.join(bad)} (use {', '.join(LOCAL_SEARCH_QUERY)})")
        return 2
    cm = ChannelMap()
    cm.local_search = True
    stick = Stick(host=host if ":" in host else f"{host}:5555")
    if not await stick.connect():
        print("couldn't reach the stick over adb")
        return 2
    fails = 0
    try:
        if not stick.packages.get("youtubetv"):
            await stick.identify()
        for key in keys:
            target = f"youtubetv:{key}"
            code = cm.yttv_code(target)
            if code:
                print(f"{key}: routes by its watch code, not search (channels.json codes.{key} isn't a "
                      f"Sunday Ticket code, or today is Sunday): skipped")
                continue
            ref = cm.search_ref(cm.yttv_search_key(target) or key)
            r = await YouTubeTVTuner().tune(stick, ref, target)
            fails += not r.ok
            print(f"{key} ({LOCAL_SEARCH_QUERY[key]}): {'OK' if r.ok else 'FAILED'} in {r.took:.1f} s  {r.error}")
            for step in r.steps:
                print("   ", step)
        return 1 if fails else 0
    finally:
        await stick.close()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    sys.exit(asyncio.run(main(sys.argv[1], [k.strip() for k in sys.argv[2].split(",") if k.strip()])))
