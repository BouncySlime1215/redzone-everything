"""Prime start probe for tuners.prime_wake_nav (~2 min, the HIDDEN stick only, no game on it that
anyone is watching; run with auto-switching paused or in a no-game window).

Nov 7 (POR@LAL): on a live game's detail page the first CENTER only wakes the nav; focus needs one
move and the second CENTER starts it ("Watch", not "Watch Live"). This runs the real PrimeTuner
with the fix ON, `--tries` times (HOME between tries, so every try opens the detail page fresh),
and reports whether each try started and which presses it took.

    .venv/bin/python scripts/prime_start_probe.py <hidden stick host:port> [--gti GTI] [--move down]

Without --gti it takes the latest-started live Prime game from data/providers.json.
Turn-on gate: every try starts with "pressed: watch (after the focus move)" or a first press that
started it, and no try ends paused or on another page. Then set tuners.prime_wake_nav: true (and
tuners.prime_wake_move_key if --move wasn't down). Screens are OCR'd in memory (temp files deleted);
the report keeps only the step labels. Writes data/reports/prime_start_probe_<time>.json.
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
from redzone.tuners.adb import Stick  # noqa: E402
from redzone.tuners.tuners import PrimeTuner  # noqa: E402

PKG = "com.amazon.firebat"
OUT = ROOT / "data" / "reports"


def live_prime_gti(now: float) -> tuple[str, str] | None:
    """(gti, title) of the latest-started Prime game that started in the last 5 h."""
    try:
        links = json.loads((ROOT / "data" / "providers.json").read_text()).get("links") or {}
    except (OSError, ValueError):
        return None
    best = None
    for game in links.values():
        start = float(game.get("start") or 0)
        for p in (game.get("providers") or {}).get("prime") or []:
            gti = p.get("gti") or p.get("ref")
            if gti and now - 5 * 3600 <= start <= now and p.get("geo_ok") is not False:
                if best is None or start > best[0]:
                    best = (start, gti, p.get("title") or "")
    return (best[1], best[2]) if best else None


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("host")
    ap.add_argument("--gti")
    ap.add_argument("--move", default="down", choices=["down", "right", "up", "left"])
    ap.add_argument("--tries", type=int, default=2)
    a = ap.parse_args()
    gti, title = a.gti, ""
    if not gti:
        found = live_prime_gti(time.time())
        if not found:
            sys.exit("no live Prime game in data/providers.json: pass --gti")
        gti, title = found
    st = Stick(a.host)
    if not await st.connect():
        sys.exit(f"can't reach {a.host}")
    st.packages = {"prime": PKG}
    tuner = PrimeTuner()
    tuner.wake_nav_fix, tuner.wake_move_key = True, f"KEYCODE_DPAD_{a.move.upper()}"
    print(f"game: {title or gti}  move: {a.move}  tries: {a.tries}")
    report = {"host_role": "hidden", "move": a.move, "started": time.time(), "tries": []}
    for i in range(a.tries):
        if i:
            await st.key("KEYCODE_HOME")   # leave the player so the next link opens the page fresh
            await asyncio.sleep(4.0)
        st.tuned = None
        t0 = time.monotonic()
        res = await asyncio.wait_for(tuner.tune(st, gti, f"prime:{gti}"), 120)
        pb = await st.playback(PKG)
        moved = "pressed: watch (after the focus move)" in res.steps
        row = {"ok": res.ok, "took_s": round(time.monotonic() - t0, 1), "moved": moved,
               "state_after": pb.get("state"), "error": res.error, "steps": res.steps}
        report["tries"].append(row)
        print(f"try {i + 1}: {'STARTED' if res.ok else 'FAILED'} in {row['took_s']} s"
              f"{' (focus move used)' if moved else ''}; state {row['state_after']}")
        for s in res.steps:
            print("    ", s)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"prime_start_probe_{time.strftime('%Y%m%d_%H%M')}.json"
    path.write_text(json.dumps(report, indent=1))
    ok = sum(t["ok"] for t in report["tries"])
    print(f"{ok}/{len(report['tries'])} started; wrote {path}")
    await st.close()


if __name__ == "__main__":
    asyncio.run(main())
