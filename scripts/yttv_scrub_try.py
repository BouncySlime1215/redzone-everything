"""Run yttv_seek.enter_buffer once on the HIDDEN YouTube TV stick and print the result row (item 1, #377).

    python scripts/yttv_scrub_try.py <stick ip:port> [seconds=60] [--miss-probe] [--label] [--again=S]

Only on the hidden stick: it presses CENTER/LEFT/CENTER. No relink is wired here; on a failure, re-tune it to live by
hand. --miss-probe makes the first probe read come back unreadable, to check the blind-close path on hardware (the
stick should end one step further back than asked, and the row should say behind-live unknown). Prints JSON only; no
frames are kept and the stick address is not printed.
--label wires the overlay label reader (yttv_label.LabelProbe): the LEFT probe at the live edge is the anchor and
one read after the commit gives a MEASURED behind_live_s (the stick must be at the live edge when this starts).
--again=S then waits S seconds and reads once more (each read adds ~7.5 s), printing that second value.
Why: Runs yttv_seek.enter_buffer once on the hidden YouTube TV stick, item 1 (docstring).
"""
import asyncio
import json
import sys

from redzone.tuners import yttv_seek as ys
from redzone.tuners.adb import Stick


async def main(host: str, seconds: float, miss_probe: bool, label: bool = False, again: float = 0.0) -> None:
    stick = Stick(host=host)
    await stick.connect()
    reads = []
    probe = None
    if label:
        from redzone.tuners.yttv_label import LabelProbe
        probe = LabelProbe()
        if not probe.arm(stick):
            print(json.dumps({"label": "not armed: stick marked behind on a target (#391); run unmeasured"}))
            probe = None
    base = probe.overlay_reader if probe else ys.ocr_overlay

    async def read(st):
        reads.append(1)
        if miss_probe and len(reads) == 2:
            return None
        return await base(st)

    kw = {"read_behind": probe} if probe else {}
    r = await ys.enter_buffer(stick, seconds, read_overlay=read, still_hidden=lambda: True, **kw)
    print(json.dumps(r.row(), indent=1))
    if probe and again > 0 and r.ok:
        await asyncio.sleep(again)
        p2 = await probe.probe(stick)
        print(json.dumps({"again_after_s": again, "behind_live_s": p2.behind_live_s, "reason": p2.reason,
                          "status": p2.read.status, "keys": p2.keys}))


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    again = next((float(a.split("=", 1)[1]) for a in sys.argv[1:] if a.startswith("--again=")), 0.0)
    asyncio.run(main(args[0], float(args[1]) if len(args) > 1 else 60.0, "--miss-probe" in sys.argv,
                     "--label" in sys.argv, again))
