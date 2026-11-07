"""Run one WATCHED join (join -> read -> verify -> re-seek once -> fall back to live) on the HIDDEN stick, then
optionally watch it for drift. Prints JSON rows only; rows also go to data/joins.jsonl (kind join / watchdog / drift).
Frames are read for numbers, then deleted; the stick address is never printed or logged.

    python scripts/watchdog_try.py <stick ip:port> youtubetv <target_s> <channel> [--reset=<other channel>]
                                   [--drift-min=M] [--first-s=S]
    python scripts/watchdog_try.py <stick ip:port> prime <target_s> [label]

YouTube TV: start with the stick PLAYING <channel> at the live edge (tune another channel, then <channel>). The
LabelProbe is armed there, so the read after the commit is MEASURED. A re-seek / fallback relinks by deep link
through --reset (default: any other channel in data/channels.json) and back to <channel> (#391: a same-channel link
does not reset a stick that is behind).
--gap=S: wait S seconds between the reset tune and the tune back (tuners.relink_gap_s; Nick: 3 s fixed 1/10 fails).
--reprobe: on an unmeasured committed join, read the label once more before falling back (join.watchdog.
reprobe_unmeasured; adds ~7.5 s behind).
--drift-min=M: after a verified join, keep the drift monitor running M minutes (reads only when due: first after
--first-s, default 300, then x2; each read costs ~7.5 s and pushes the picture ~7.5 s back).
Prime: the timecode measures the pause; no relink is wired (on a fallback, re-tune it by hand).
"""
import asyncio
import json
import sys
import time

from redzone.tuners import join_watchdog as jw
from redzone.tuners.adb import Stick


async def main(host: str, app: str, target: float, channel: str, reset: str, drift_min: float, first_s: float,
               reprobe: bool = False, gap_s: float = 0.0):
    stick = Stick(host=host)
    if not await stick.connect():
        print(json.dumps({"error": "stick not connected", "state": stick.state}))
        return
    # [Nick's run, Nov 7 11:35Z] without identify() the stick has no app packages, so every relink tune failed at once
    # ("youtubetv app not found") and the fallback said live_failed with the stick still 79 s behind on NBA TV.
    await stick.identify()
    from redzone.tuners.join import _default_log
    print(json.dumps({"joins_log": str(_default_log().resolve()), "packages": sorted(stick.packages)}))
    kw: dict = {}
    probe = None

    async def no_relink() -> bool:
        return False
    relink = no_relink
    if app == "youtubetv":
        from redzone.channels import ChannelMap
        from redzone.tuners.tuners import TUNERS
        from redzone.tuners.yttv_label import LabelProbe
        cm = ChannelMap()
        tuner = TUNERS["youtubetv"]
        channel = channel.lower().replace(" ", "")          # "NBA TV" -> "nbatv", the channel table's key
        reset = reset.lower().replace(" ", "")
        if not cm.yttv_code(f"youtubetv:{channel}"):
            print(json.dumps({"error": f"no watch code for {channel!r}", "known": sorted(cm.codes)[:40]}))
            return
        other = reset or next((k for k in sorted(cm.codes) if k != channel), "")

        async def relink() -> bool:
            """Through another channel and back: a known live edge (#391). Prints each tune's steps."""
            for ch in (other, channel):
                code = cm.yttv_code(f"youtubetv:{ch}")
                if not ch or not code:
                    print(json.dumps({"relink": ch, "ok": False, "error": "no watch code"}))
                    return False
                r = await tuner.tune(stick, code, f"youtubetv:{ch}")
                print(json.dumps({"relink": ch, "ok": r.ok, "took_s": round(r.took, 1), "error": r.error,
                                  "steps": r.steps[-6:]}))
                if not r.ok:
                    return False
                if ch == other and gap_s > 0:
                    await asyncio.sleep(gap_s)    # tuners.relink_gap_s: back-to-back tunes failed 1/10 (Nick Nov 7)
            return bool(probe.arm(stick)) if probe else True

        probe = LabelProbe()
        if not probe.arm(stick):
            print(json.dumps({"label": "not armed (stick marked behind): the join will be unmeasured -> fallback"}))
        kw = {"read_overlay": probe.overlay_reader, "read_behind": probe}
    wcfg = {"join.watchdog.reprobe_unmeasured": True} if reprobe else None
    out = await jw.watched_join(stick, app, target, relink=relink, still_hidden=lambda: True, label="hidden", cfg=wcfg,
                                channel=channel, **kw)
    print(json.dumps({"state": out.state, "behind_s": out.behind_s, "attempts": out.attempts,
                      "fallback": out.fallback, "reprobes": out.reprobes, "verdicts": out.verdicts,
                      "took_s": out.took_s}, indent=1))
    if not (out.buffered and probe and drift_min > 0):
        return
    dm = jw.DriftMonitor({"join.drift.first_s": first_s})
    dm.anchor("hidden", app, target, out.behind_s)
    end = time.monotonic() + drift_min * 60
    while time.monotonic() < end:
        if dm.due("hidden", hidden=True):
            t0 = time.monotonic()
            pr = await probe.probe(stick)
            v = dm.record("hidden", pr.behind_live_s, cost_s=time.monotonic() - t0, channel=channel, label="hidden")
            print(json.dumps({"min": round((time.monotonic() - end) / 60 + drift_min, 1), "action": v.action,
                              "expected_s": v.expected_s, "measured_s": v.measured_s, "drift_s": v.drift_s,
                              "read_s": round(time.monotonic() - t0, 1), "reason": v.reason}))
            if v.action != "ok":
                return
        await asyncio.sleep(5)
    print(json.dumps({"drift_report": dm.report()}))


if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if not x.startswith("--")]
    opt = {x.split("=", 1)[0][2:]: x.split("=", 1)[1] for x in sys.argv[1:] if x.startswith("--") and "=" in x}
    asyncio.run(main(a[0], a[1], float(a[2]), a[3] if len(a) > 3 else "", opt.get("reset", ""),
                     float(opt.get("drift-min", 0)), float(opt.get("first-s", 300)), "--reprobe" in sys.argv,
                     float(opt.get("gap", 0))))
