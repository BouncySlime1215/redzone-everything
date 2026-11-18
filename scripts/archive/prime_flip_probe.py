"""Prime flip probe (~15 min, needs you home with a live Prime game and the service PAUSED).

Why Prime switches take ~30 s: the app flips the TV input by pressing HOME on the stick it wants on
screen (these Fire OS builds have no `cmd hdmi_control onetouchplay`). The stick that LOSES the
input pauses Prime (older stick) or drops its session (detail page, "none"). Coming back to it then
needs the resume ladder or a ~30-50 s deep link. This probe measures, per stick, the facts the fix
depends on:

  1. Hidden play: with Prime playing on stick A, flip the TV to stick B. Every 2 s for 60 s, read
     A's Prime state: does it keep playing, pause, or lose the session? When?
  2. Come back: flip to A and time each resume method, one per round, in a fixed safe order:
       wait (does it play again by itself?), MEDIA_PLAY, MEDIA_PLAY_PAUSE, press-through
       (Watch -> Watch Live, only on the detail page), fresh deep link.
     A CENTER is never sent while Prime is playing or loading (it would pause it).
  3. Behind live: Prime reports a wall-clock position, so each resume also says how far behind live
     it came back (a paused game resuming 60 s behind is a free "hold for the dead ball").

    .venv/bin/python scripts/prime_flip_probe.py <stickA host:port> <stickB host:port> [rounds]

Writes data/reports/prime_flip_probe_<time>.json; the numbers go into the Prime resume order.
Never run it while someone is watching: it flips the TV input about 3x per round.
Why: Probes Prime flip timing, which is slow; needs a live Prime game with the service paused (docstring).
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from redzone.tuners.adb import Stick  # noqa: E402

PKG = "com.amazon.firebat"
OUT = Path(__file__).resolve().parent.parent / "data" / "reports"
METHODS = ["wait", "media_play", "play_pause", "press_through"]


async def state(s: Stick) -> dict:
    p = await s.playback(PKG)
    pos = p.get("position")
    behind = round(time.time() - pos / 1000, 1) if isinstance(pos, (int, float)) and pos > 1e12 else None
    return {"t": round(time.time(), 2), "state": p.get("state"), "behind_live": behind}


async def flip_to(s: Stick) -> float:
    """The app's own flip (control._home_flip_prime): HOME makes this stick the TV's active source,
    then Prime's last screen is brought back to the front (reorder, never a new link)."""
    r = await s.shell("dumpsys activity activities | grep -m1 mResumedActivity", timeout=5)
    import re
    m = re.search(r"u0 (com\.amazon\.firebat/[\w.$]+)", r.out if r.ok else "")
    t0 = time.monotonic()
    await s.shell("input keyevent KEYCODE_HOME", timeout=8)
    await asyncio.sleep(2.5)
    if m:
        await s.shell(f"am start -f 0x00020000 -n {m.group(1)} 2>&1", timeout=8)
    return round(time.monotonic() - t0, 2)


async def try_method(s: Stick, method: str, limit: float = 20.0) -> dict:
    t0 = time.monotonic()
    before = await state(s)
    if method == "media_play" and before["state"] == "paused":
        await s.key("KEYCODE_MEDIA_PLAY")
    elif method == "play_pause" and before["state"] == "paused":
        await s.key("KEYCODE_MEDIA_PLAY_PAUSE")
    elif method == "press_through" and before["state"] in ("none", "stopped", None):
        from redzone.tuners.tuners import TUNERS
        await TUNERS["prime"].resume_from_page(s, PKG, [])
    trace = [before]
    while time.monotonic() - t0 < limit:
        st = await state(s)
        trace.append(st)
        if st["state"] == "playing":
            return {"method": method, "ok": True, "seconds": round(time.monotonic() - t0, 1),
                    "from": before["state"], "behind_live": st["behind_live"], "trace": trace}
        await asyncio.sleep(1.0)
    return {"method": method, "ok": False, "seconds": limit, "from": before["state"], "trace": trace}


async def main() -> None:
    a, b = Stick(sys.argv[1]), Stick(sys.argv[2])
    rounds = int(sys.argv[3]) if len(sys.argv) > 3 else len(METHODS)
    for s in (a, b):
        if not await s.connect():
            sys.exit(f"can't reach {s.host}")
    if (await state(a))["state"] != "playing":
        sys.exit(f"Prime isn't playing on {a.host}: start a live game there first")
    report = {"a": a.host, "b": b.host, "started": time.time(), "rounds": []}
    for r in range(rounds):
        method = METHODS[r % len(METHODS)]
        print(f"round {r + 1}: flip to B, watch A hidden for 60 s, come back with '{method}'")
        flip_s = await flip_to(b)
        hidden = []
        for _ in range(30):
            hidden.append(await state(a))
            await asyncio.sleep(2.0)
        back_s = await flip_to(a)
        res = await try_method(a, method)
        if not res["ok"]:
            print("  didn't come back: re-linking so the next round starts playing")
            from redzone.tuners.tuners import TUNERS
            ref = (a.tuned or {}).get("target", "").split(":", 1)[-1]
            if ref:
                t0 = time.monotonic()
                ok = await asyncio.wait_for(TUNERS["prime"].tune(a, ref, f"prime:{ref}"), 60)
                res["relink_s"] = round(time.monotonic() - t0, 1)
                res["relink_ok"] = bool(getattr(ok, "ok", ok))
        first_change = next((h for h in hidden if h["state"] != "playing"), None)
        report["rounds"].append({"method": method, "flip_away_s": flip_s, "flip_back_s": back_s,
                                 "hidden_states": hidden, "hidden_stopped_after_s":
                                 round(first_change["t"] - hidden[0]["t"], 1) if first_change else None,
                                 "resume": res})
        print(f"  hidden: {'kept playing' if not first_change else first_change['state'] + ' after ' + str(report['rounds'][-1]['hidden_stopped_after_s']) + ' s'}"
              f"; back with {method}: {'%.1f s' % res['seconds'] if res['ok'] else 'FAILED'}"
              f"{'' if not res.get('behind_live') else ', %s s behind live' % res['behind_live']}")
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"prime_flip_probe_{time.strftime('%Y%m%d_%H%M')}.json"
    path.write_text(json.dumps(report, indent=1))
    print("wrote", path)
    for s in (a, b):
        await s.close()


if __name__ == "__main__":
    asyncio.run(main())
