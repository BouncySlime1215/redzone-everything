"""[replay on the scrub, Nick Nov 7 1:02 PM ET] Park a HIDDEN YouTube TV stick just before a big play's snap.

Why: Nick, Nov 7 1:02 PM ET: he could see the rewind, so the seek happens on the hidden stick, parked just before the big play's snap (docstring).

Nick: 'that rewind worked rly good / got right to the spot of the big play / but i saw the rewind / it should be auto
on the play right b4 the snap when it switches'. So the seek happens on the hidden stick and the TV flips onto it:
  1. k LEFTs so the knob lands at or before the park point. The frozen-overlay time cancels out (it puts the picture
     behind live AND holds it still), so only the knob's 15 s steps and a little playing time count:
     k = ceil((back_s - back_lead_s + park_lead_s + scrub_slack_s) / 15), at least 1;
  2. yttv_seek.enter_buffer with that fixed count (knob-checked, fails closed), its landing READ by the label probe
     (behind-live B; never media_session, which crawls after a seek);
  3. the picture plays toward the snap: wait until it shows (snap - park_lead_s), then MEDIA_PAUSE. The flip
     un-pauses it, so the TV lands park_lead_s before the snap.
`back_s` is how far before NOW the replay starts (the decider's Missed.back_s: back_lead_s before the snap).
Fails closed (ok False) on: no package (pkg None), no label probe, no read, a landing past the park point, a wait over park_wait_max_s, the
stick no longer hidden. Never raises. Frames are read for numbers, then deleted (yttv_label / yttv_seek).
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Callable

log = logging.getLogger("redzone.replay_park")

PARK_DEFAULTS = {"back_lead_s": 3.0, "park_lead_s": 1.5, "scrub_slack_s": 4.0, "park_wait_max_s": 40.0, "pause_settle_s": 3.0, "pause_poll_s": 0.4,
                 "yttv_max_lefts": 8}


def _cfg(cfg, key: str):
    v = cfg.get(f"replay.{key}") if cfg is not None and hasattr(cfg, "get") else None
    return PARK_DEFAULTS[key] if v is None else v


def lefts_for(back_s: float, cfg=None) -> int:
    step = (cfg.get("yttv_seek.step_s") if cfg is not None and hasattr(cfg, "get") else None) or 15.0
    need = float(back_s) - float(_cfg(cfg, "back_lead_s")) + float(_cfg(cfg, "park_lead_s")) + float(
        _cfg(cfg, "scrub_slack_s"))
    return max(1, math.ceil(need / float(step)))


@dataclass
class ParkResult:
    ok: bool
    lefts: int = 0
    behind_s: float | None = None     # label-read behind-live after the scrub
    waited_s: float = 0.0             # played toward the snap before the pause
    to_snap_s: float | None = None    # at the pause: how far the picture is BEFORE the snap (PLAY -> snap after this)
    paused: bool = False              # the player reported paused after MEDIA_PAUSE
    moved: bool = False               # keys may have moved the stick (caller marks it behind, #391)
    reason: str = ""
    took_s: float = 0.0
    steps: list[str] = field(default_factory=list)


async def park(stick, back_s: float, *, probe, cfg=None, pkg: str | None = None,
               still_hidden: Callable[[], bool] | None = None, clock: Callable[[], float] | None = None,
               sleep=None) -> ParkResult:
    clock = clock or time.time
    sleep = sleep or asyncio.sleep
    still_hidden = still_hidden or (lambda: True)
    t0 = clock()
    res = ParkResult(ok=False)
    try:
        await _park(stick, float(back_s), probe, cfg, pkg, still_hidden, clock, sleep, t0, res)
    except asyncio.CancelledError:
        raise
    except Exception as e:   # never raise into the switch path
        log.warning("replay park failed: %r", e)
        res.ok, res.reason, res.moved = False, f"exception: {e!r}"[:160], True
    res.took_s = round(clock() - t0, 2)
    return res


async def _park(stick, back_s, probe, cfg, pkg, still_hidden, clock, sleep, t0, res) -> None:
    park_lead = float(_cfg(cfg, "park_lead_s"))
    t_snap = t0 - (back_s - float(_cfg(cfg, "back_lead_s")))     # wall time the snap aired live
    k = res.lefts = lefts_for(back_s, cfg)
    if k > int(_cfg(cfg, "yttv_max_lefts")):
        res.reason = f"{back_s:.0f}s back needs {k} LEFTs (> yttv_max_lefts)"
        return
    if not pkg:
        # [Nick, Nov 8 89656de9] playback(None) can't pick the YouTube TV session among several (Prime, Alexa,
        # vizzini) and reads 'none': never press keys on a park whose pause can't be confirmed
        res.reason = "no YouTube TV package: the pause could not be read (identify() the stick first)"
        return
    if probe is None or not probe.arm(stick):
        res.reason = "no label probe armed: a replay is never placed blind"
        return
    from redzone.tuners import yttv_seek
    r = await yttv_seek.enter_buffer(stick, back_s, cfg=cfg, pkg=pkg, still_hidden=still_hidden,
                                     read_overlay=probe.overlay_reader, read_behind=probe, presses=k + 1)
    res.steps = list(getattr(r, "steps", []) or [])
    res.moved = bool(getattr(r, "presses", 1) or getattr(r, "committed", True))
    behind = getattr(r, "behind_s", None) if getattr(r, "ok", False) else None
    if behind is None:
        res.reason = str(getattr(r, "reason", "") or "no label read")[:160]
        return
    res.behind_s = round(float(behind), 2)
    t_pause = t_snap - park_lead + float(behind)               # when the picture shows (snap - park_lead_s)
    wait = t_pause - clock()
    if wait < -1.0:
        res.reason = f"landed {res.behind_s:.1f}s behind live, {-wait:.1f}s past the park point: the snap is gone"
        return
    if wait > float(_cfg(cfg, "park_wait_max_s")):
        res.reason = f"landed {wait:.1f}s before the park point (> park_wait_max_s)"
        return
    t_w = clock()
    while wait > 0:
        if not still_hidden():
            res.reason = "stick became visible while waiting for the park point"
            return
        await sleep(min(0.25, wait))
        wait = t_pause - clock()
    res.waited_s = round(clock() - t_w, 2)
    if not still_hidden():
        res.reason = "stick became visible before the pause"
        return
    # behind-live B is NOT the distance to the snap: live moved on during the scrub (overlay up, label read), so the
    # snap is (back_s - back_lead_s) + the scrub's wall time behind live, and a 63 s replay needs B ~110 s
    res.to_snap_s = round(t_snap - (clock() - float(behind)), 2)
    sent = await stick.key("KEYCODE_MEDIA_PAUSE")
    if not sent or not getattr(sent, "ok", False):
        res.reason = "PAUSE not sent"
        return
    # [Nick, Nov 8 fd1ba542] the first read right after PAUSE can be a transient 'none' (the post-tune family, #356)
    # while the player is paused (state 2 at +0, +10, +20 s): re-read for pause_settle_s before calling it lost
    end = clock() + float(_cfg(cfg, "pause_settle_s"))
    while True:
        pb = await stick.playback(pkg)
        if pb.get("state") == "paused" or clock() >= end:
            break
        await sleep(float(_cfg(cfg, "pause_poll_s")))
    if pb.get("state") not in ("playing", "paused"):
        res.reason = f"player is {pb.get('state')} after PAUSE"
        return
    res.paused = pb.get("state") == "paused"
    res.steps.append(f"parked {park_lead:.1f}s before the snap ({k} LEFTs, {res.behind_s:.1f}s behind live, "
                     f"waited {res.waited_s:.1f}s)")
    res.ok, res.reason = True, "parked before the snap"
