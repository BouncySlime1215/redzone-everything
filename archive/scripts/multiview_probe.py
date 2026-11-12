"""At home, ~5 minutes, before the games: measure YouTube TV multiview on a real stick.

    .venv/bin/python scripts/multiview_probe.py                 # the first stick with YouTube TV
    .venv/bin/python scripts/multiview_probe.py --stick 100.110.165.150:5555
    .venv/bin/python scripts/multiview_probe.py --yes           # no questions (stops if it can't find multiview)

It takes the TV for a few minutes (HOME on the stick makes the TV show it), so run it with no game
on, or after tapping Pause on the phone. It:

  1. opens a live channel in YouTube TV and waits for it to play
  2. looks for YouTube TV's "Multiview" entry on screen (screenshots read on the Mac, a few D-pad
     moves at most). If it can't find it, it asks you to open a multiview with the remote and
     press Enter (it times how long the multiview took to start playing)
  3. reads what the media session reports in the grid (does it name a channel?)
  4. for each tile: D-pad to it, CENTER, times until it plays full screen (and whether the media
     session names the channel), then BACK, and times the grid coming back

Everything goes to data/lab/multiview/<time>/ (probe.json plus screenshots). Send probe.json to
the coordinator. Its numbers become the multiview costs, and multiview.enabled goes on only if
expand is fast and verifiable (redzone/tuners/multiview.py).
Why: Measures YouTube TV multiview on a real stick, at home before games (docstring, multiview commit).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone.tuners.adb import Stick  # noqa: E402
from redzone.tuners.multiview import KEY, moves  # noqa: E402

DEVICES = ROOT / "data" / "devices.json"
API = "http://127.0.0.1:8778/api/state"
WORDS = ("multiview", "multi view", "watch in multiview", "build a multiview")
EXPLORE = ("KEYCODE_DPAD_DOWN", "KEYCODE_DPAD_DOWN", "KEYCODE_DPAD_RIGHT", "KEYCODE_DPAD_RIGHT",
           "KEYCODE_DPAD_UP", "KEYCODE_MENU")


def live_game() -> str:
    """Name of a live game the app is showing, or '' (no app running counts as nothing live)."""
    try:
        with urllib.request.urlopen(API, timeout=3) as r:
            st = json.loads(r.read())
    except Exception:
        return ""
    if (st.get("controls") or {}).get("paused") or (st.get("decider") or {}).get("paused"):
        return ""
    for g in st.get("games") or []:
        if g.get("phase") == "live":
            return g.get("matchup") or g.get("id") or "a game"
    return ""


class Probe:
    def __init__(self, stick: Stick, pkg: str, out: Path, ask: bool):
        self.stick, self.pkg, self.out, self.ask = stick, pkg, out, ask
        self.t0 = time.monotonic()
        self.log: list[dict] = []
        self.n = 0

    def note(self, step: str, **kw) -> None:
        row = {"t": round(time.monotonic() - self.t0, 2), "step": step, **kw}
        self.log.append(row)
        print(f"[{row['t']:6.1f}s] {step} " + " ".join(f"{k}={v}" for k, v in kw.items() if k != "ocr")[:200])

    async def shot(self, name: str) -> str:
        self.n += 1
        p = self.out / f"{self.n:02d}-{name}.png"
        ok = await self.stick.screenshot(str(p))
        text = ""
        if ok and sys.platform == "darwin":
            try:
                from redzone.screen_ocr import recognize
                text = recognize(p).text
            except Exception as e:
                text = f"(ocr failed: {e})"
        self.note(f"screenshot {p.name}", ok=ok, ocr=text[:600])
        return text

    async def pb(self) -> dict:
        r = await self.stick.playback(self.pkg)
        return {k: r.get(k) for k in ("state", "title", "subtitle", "position", "read_ok")}

    async def wait_playing(self, timeout: float, changed_from: str | None = None) -> tuple[float | None, dict]:
        t0 = time.monotonic()
        last = {}
        while time.monotonic() - t0 < timeout:
            last = await self.pb()
            if last.get("state") == "playing" and (changed_from is None or last.get("subtitle") != changed_from):
                return time.monotonic() - t0, last
            await asyncio.sleep(0.25)
        return None, last

    def confirm(self, question: str) -> bool:
        if not self.ask:
            return False
        return input(f"\n>>> {question} [Enter = yes, n = no] ").strip().lower() not in ("n", "no")


async def run(a) -> dict:
    d = json.loads(DEVICES.read_text()) if DEVICES.exists() else {}
    rows = [s for s in d.get("sticks", []) if (s.get("packages") or {}).get("youtubetv")]
    if a.stick:
        rows = [s for s in d.get("sticks", []) if s["host"] == a.stick] or [{"host": a.stick, "packages": {}}]
    if not rows:
        raise SystemExit("No stick with YouTube TV in data/devices.json (pass --stick HOST:5555).")
    row = rows[0]
    stick = Stick(row["host"], name=row.get("name", ""))
    stick.packages = row.get("packages") or {}
    pkg = stick.packages.get("youtubetv") or "com.amazon.firetv.youtube.tv"
    if not await stick.connect():
        raise SystemExit(f"Can't reach {stick.host} over adb (same Wi-Fi? Tailscale quit?).")

    out = ROOT / "data" / "lab" / "multiview" / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    p = Probe(stick, pkg, out, ask=not a.yes)
    result: dict = {"stick": stick.host, "pkg": pkg, "found_by": None, "open_s": None, "grid": {}, "tiles": [],
                    "out": str(out.relative_to(ROOT))}

    from redzone.channels import ChannelMap
    from redzone.tuners.tuners import YouTubeTVTuner
    cm = ChannelMap()
    chans = [c.strip() for c in a.channels.split(",") if c.strip()]
    code = next((cm.yttv_code(f"youtubetv:{c}") for c in chans if cm.yttv_code(f"youtubetv:{c}")), None)
    p.note("start", channels=chans, code=bool(code))

    # 1. A live channel playing, with the TV on this stick (HOME announces it over CEC).
    await stick.key("KEYCODE_WAKEUP", "KEYCODE_HOME")
    await asyncio.sleep(1.5)
    if code:
        await stick.view(YouTubeTVTuner().url(code), pkg, wait=True, fresh=True)
    else:
        await stick.shell(f"monkey -p {pkg} -c android.intent.category.LAUNCHER 1")
    took, now = await p.wait_playing(25)
    p.note("channel playing" if took else "channel NOT playing", took=took and round(took, 1), pb=now)
    await p.shot("channel")

    # 2. Find the multiview entry: a few D-pad moves, reading the screen after each.
    found = False
    for k in EXPLORE:
        await stick.key(k)
        await asyncio.sleep(1.2)
        text = (await p.shot(k.removeprefix("KEYCODE_").lower())).lower()
        if any(w in text for w in WORDS):
            p.note("multiview entry seen on screen", after=k)
            if p.confirm("The screen shows a Multiview option. Is it highlighted (focused)?") or a.yes:
                t0 = time.monotonic()
                await stick.key(KEY["ok"])
                took, now = await p.wait_playing(30)
                if took is not None:
                    found = True
                    result["found_by"] = f"D-pad ({k}) + CENTER"
                    result["open_s"] = round(time.monotonic() - t0, 2)
            break
    if not found:
        if not p.confirm("Couldn't open it by itself. With the Fire TV remote, open a YouTube TV multiview "
                         "(4 games), then press Enter here the moment you press its last button."):
            result["error"] = "multiview not found"
            return finish(p, result)
        t0 = time.monotonic()
        took, now = await p.wait_playing(40)
        result["found_by"] = "by hand"
        result["open_s"] = round(took, 2) if took is not None else None
    await asyncio.sleep(2)
    grid_pb = await p.pb()
    grid_text = await p.shot("grid")
    result["grid"] = {"pb": grid_pb, "ocr": grid_text[:800]}
    p.note("grid", pb=grid_pb)

    # 3. Each tile: move, expand, time it; collapse, time it.
    focus = 0
    for i in range(4):
        path = moves(focus, i)
        if path:
            await stick.key(*(KEY[x] for x in path))
            await asyncio.sleep(0.5)
        focus = i
        t0 = time.monotonic()
        await stick.key(KEY["ok"])
        took, full = await p.wait_playing(8, changed_from=grid_pb.get("subtitle"))
        expand_s = round(time.monotonic() - t0, 2)
        await p.shot(f"tile{i + 1}-full")
        t1 = time.monotonic()
        await stick.key(KEY["back"])
        back_pb = None
        for _ in range(24):
            back_pb = await p.pb()
            if back_pb.get("subtitle") == grid_pb.get("subtitle"):
                break
            await asyncio.sleep(0.25)
        collapse_s = round(time.monotonic() - t1, 2)
        await asyncio.sleep(1.0)
        await p.shot(f"tile{i + 1}-back")
        tile = {"tile": i + 1, "moves": path, "expand_s": expand_s if took is not None else None,
                "full_pb": full, "names_channel": bool((full or {}).get("subtitle")) and
                (full or {}).get("subtitle") != grid_pb.get("subtitle"),
                "collapse_s": collapse_s, "back_pb": back_pb}
        result["tiles"].append(tile)
        p.note(f"tile {i + 1}", expand_s=tile["expand_s"], collapse_s=collapse_s, names=tile["names_channel"])
        if a.yes is False and i == 0 and not p.confirm("Did tile 1 go full screen and come back to the grid?"):
            result["error"] = "expand/collapse not as expected (by eye)"
            break
    await stick.key(KEY["back"], "KEYCODE_HOME")
    return finish(p, result)


def finish(p: Probe, result: dict) -> dict:
    ex = [t["expand_s"] for t in result["tiles"] if t.get("expand_s") is not None]
    co = [t["collapse_s"] for t in result["tiles"] if t.get("collapse_s") is not None]
    result["summary"] = {
        "open_s": result.get("open_s"),
        "expand_median_s": sorted(ex)[len(ex) // 2] if ex else None,
        "collapse_median_s": sorted(co)[len(co) // 2] if co else None,
        "media_session_names_tile": all(t.get("names_channel") for t in result["tiles"]) if result["tiles"] else None,
    }
    result["log"] = p.log
    (p.out / "probe.json").write_text(json.dumps(result, indent=1, default=str))
    print("\nResult:", json.dumps(result["summary"]))
    print(f"Saved {p.out.relative_to(ROOT)}/probe.json; send it to the coordinator.")
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stick", help="HOST:5555 (default: the first stick with YouTube TV)")
    ap.add_argument("--channels", default="espn,fox,cbs,nbc,abc,tnt,fs1", help="channels to try for step 1")
    ap.add_argument("--yes", action="store_true", help="never ask; stop if multiview isn't found by itself")
    ap.add_argument("--force", action="store_true", help="run even while a game is live")
    a = ap.parse_args()
    busy = live_game()
    if busy and not a.force:
        print(f"{busy} is live and the app is controlling the TV. Tap Pause on the phone (or wait), "
              "then run this again (or add --force).")
        return 2
    r = asyncio.run(run(a))
    return 0 if not r.get("error") and r.get("tiles") else 1


if __name__ == "__main__":
    sys.exit(main())
