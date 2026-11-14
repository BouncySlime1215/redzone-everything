#!/usr/bin/env python3
"""[Prime seek, real hardware] Can Prime Video's LIVE player be put behind live on purpose, and does it stay there?

Run it on the Mac with a live Prime stream already playing on the HIDDEN stick (not the one on screen), the
service's auto-switching paused, and nobody needing that stick. One method per run, so a failure is easy to read:

    .venv/bin/python scripts/prime_seek_probe.py <hidden host:port> rewind        # MEDIA_REWIND, then PLAY
    .venv/bin/python scripts/prime_seek_probe.py <hidden host:port> left --n 4    # bare DPAD_LEFT x N
    .venv/bin/python scripts/prime_seek_probe.py <hidden host:port> scrub --n 4   # CENTER, LEFT x N, CENTER
    .venv/bin/python scripts/prime_seek_probe.py <hidden host:port> skipback      # MEDIA_SKIP_BACKWARD
    .venv/bin/python scripts/prime_seek_probe.py <hidden host:port> pause --s 60  # PLAY_PAUSE, wait S, PLAY_PAUSE

Truth meter: Prime reports a wall-clock position, so behind live = now - position, extrapolated from the
session's `updated` stamp on the stick's own boot clock (read together with /proc/uptime). Every read logs the
update age: a stale session (age > 10 s) is marked and never counted as a hold (the Nov 6 "flat meter" trap).
Reads at 0, 5, 15, 30, 60, 120, 300 s after the keys (--watch S to stop earlier). PASS for a method = behind
live moves by >= 10 s and holds within 3 s of that for every fresh read.

Fails closed: no key is sent unless Prime (com.amazon.firebat) is the resumed activity AND its session is
playing with a wall-clock position. Keys go only to the host you name. No frames, no screenshots, no Peacock.
Writes data/reports/prime_seek_probe_<method>_<time>.json (numbers only). Afterwards re-tune the stick from
the app (or press HOME) to leave it at the live edge.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from redzone.tuners.adb import EDGE_READ, PRIME_PACKAGES, Stick, parse_playback  # noqa: E402

READS = (0, 5, 15, 30, 60, 120, 300)
STALE_MS = 10_000


def behind_of(text: str, pkg: str, now: float) -> dict:
    """{behind_s, age_s, state} from one `cat /proc/uptime; dumpsys media_session` read, or behind None."""
    lines = text.splitlines()
    try:
        up_ms = float(lines[0].split()[0]) * 1000.0
    except (IndexError, ValueError):
        return {"behind_s": None, "age_s": None, "state": "unreadable"}
    p = parse_playback("\n".join(lines[1:]), pkg)
    pos, upd, st = p.get("position"), p.get("updated"), p.get("state")
    if not isinstance(pos, (int, float)) or pos < 1e12 or upd is None:
        return {"behind_s": None, "age_s": None, "state": st}
    age = up_ms - upd
    speed = p.get("speed", 1.0) if st == "playing" else 0.0
    pos_now = pos + age * (speed or 0.0)
    return {"behind_s": round(now - pos_now / 1000.0, 1), "age_s": round(age / 1000.0, 1), "state": st,
            "stale": age > STALE_MS}


async def read(s: Stick, pkg: str) -> dict:
    r = await s.shell(EDGE_READ, timeout=6)
    return behind_of(r.out if r.ok else "", pkg, time.time())


async def keys(s: Stick, method: str, n: int, hold_s: float) -> list[str]:
    sent = []

    async def k(code: str) -> None:
        await s.shell(f"input keyevent {code}", timeout=4)
        sent.append(code)
        await asyncio.sleep(0.6)
    if method == "rewind":
        await k("KEYCODE_MEDIA_REWIND")
        await asyncio.sleep(5)
        await k("KEYCODE_MEDIA_PLAY")
    elif method == "left":
        for _ in range(n):
            await k("KEYCODE_DPAD_LEFT")
    elif method == "scrub":
        await k("KEYCODE_DPAD_CENTER")
        for _ in range(n):
            await k("KEYCODE_DPAD_LEFT")
        await k("KEYCODE_DPAD_CENTER")
    elif method == "skipback":
        await k("KEYCODE_MEDIA_SKIP_BACKWARD")
    elif method == "pause":
        await k("KEYCODE_MEDIA_PLAY_PAUSE")
        await asyncio.sleep(hold_s)
        await k("KEYCODE_MEDIA_PLAY_PAUSE")
    return sent


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("host")
    ap.add_argument("method", choices=("rewind", "left", "scrub", "skipback", "pause"))
    ap.add_argument("--n", type=int, default=4, help="LEFT presses for left/scrub")
    ap.add_argument("--s", type=float, default=60.0, help="pause length for pause")
    ap.add_argument("--watch", type=float, default=300.0, help="stop reading after this many seconds")
    a = ap.parse_args()
    s = Stick(a.host)
    if not await s.connect():
        print(f"NOT RUN: can't reach {a.host}", file=sys.stderr)
        return 2
    fg = await s.foreground()
    pkg = next((p for p in PRIME_PACKAGES if fg.startswith(p + "/")), None)
    before = await read(s, pkg or PRIME_PACKAGES[0])
    if pkg is None or before.get("state") != "playing" or before.get("behind_s") is None or before.get("stale"):
        print(f"NOT RUN (fail closed): foreground {fg or '?'}, session {before}. Put a live Prime stream on "
              f"this stick first.", file=sys.stderr)
        return 2
    t0 = time.time()
    sent = await keys(s, a.method, a.n, a.s)
    rows = []
    for at in READS:
        if at > a.watch:
            break
        await asyncio.sleep(max(0.0, t0 + at + (a.s if a.method == "pause" else 0) - time.time()))
        rows.append({"t": at, **await read(s, pkg)})
        print(json.dumps(rows[-1]))
    fresh = [r["behind_s"] for r in rows if r.get("behind_s") is not None and not r.get("stale")]
    moved = (fresh[0] - before["behind_s"]) if fresh else None
    held = bool(fresh) and moved is not None and moved >= 10 and max(fresh) - min(fresh) <= 3.0
    out = {"host": a.host, "method": a.method, "n": a.n, "pause_s": a.s if a.method == "pause" else None,
           "keys": sent, "before": before, "reads": rows, "moved_s": None if moved is None else round(moved, 1),
           "verdict": "PASS" if held else "FAIL", "when": time.strftime("%Y-%m-%d %H:%M:%S")}
    rep = ROOT / "data" / "reports"
    rep.mkdir(parents=True, exist_ok=True)
    path = rep / f"prime_seek_probe_{a.method}_{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(out, indent=1))
    print(f"{a.method}: {out['verdict']} (moved {out['moved_s']} s; fresh reads {fresh}) -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
