"""At home on a Prime NBA night, ~10 minutes, before you watch: learn Prime Video's NBA multiview on
one stick and write data/prime_multiview_keys.json (what PrimeMultiviewTuner presses).

    .venv/bin/python scripts/prime_multiview_probe.py --gti GTI            # this Prime game first
    .venv/bin/python scripts/prime_multiview_probe.py --stick 100.110.165.181:5555 --gti GTI
    .venv/bin/python scripts/prime_multiview_probe.py --gti GTI --rounds 30 # more timed switches
    .venv/bin/python scripts/prime_multiview_probe.py --gti GTI --yes       # never ask (stops instead)
    .venv/bin/python scripts/prime_multiview_probe.py --fake --gap 0        # rehearsal, no stick (~10 s)

It takes that stick's screen for a few minutes, so run it with the app paused. It:

  1. opens the Prime game (a normal deep-link tune) and waits for it to play
  2. finds the multiview entry with a few D-pad moves, reading the accessibility tree
     (uiautomator dump) after each, because Prime's player screenshots are DRM-black. If it can't,
     it asks you to open multiview with the remote and type the keys you pressed
  3. opens the "add a game" picker, records every game it offers, and adds up to 2 of them
  4. learns the key path that makes each side tile main, and how the tile order changes after
     (swap, rotate or fixed), checking the big tile's label each time
  5. times --rounds tile switches, each verified, and prints the gate's first half: >= 95% right
     tile under 2 s (the second half, 0 mid-play entries, comes from the live night)

Output: data/prime_multiview_keys.json, and data/lab/prime_multiview/<time>/probe.json with every
step. Nothing turns on by itself: tuners.prime_multiview stays false until the live night passes.
Why: Learns Prime NBA multiview keys on one stick; the feature ships OFF (memory 2026-11-03).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone.tuners import prime_multiview as pmv  # noqa: E402

DEVICES = ROOT / "data" / "devices.json"
OPEN_WORDS = ("multiview", "multi-view", "multi view", "watch multiple", "watch more games")
ADD_WORDS = ("add game", "add a game", "add games", "add stream", "add")
# Moves tried from the player to reach the entries (each followed by a dump). Short on purpose.
EXPLORE = (["down"], ["down"], ["right"], ["right"], ["up"], ["up"], ["menu"])
GENERIC = {"live", "watch", "game", "games", "nba", "now", "the", "and", "at", "vs", "prime", "video",
           "on", "in", "quarter", "half", "final", "add", "tonight"}
PROMOTE_TRIES = {1: (["right", "ok"], ["right", "up", "ok"]),
                 2: (["right", "down", "ok"], ["right", "down", "down", "ok"], ["down", "right", "ok"])}


def words(label: str) -> list[str]:
    """A picker row's distinctive words, used as that tile's labels (team names come from Prime's own
    text, never from code)."""
    return [w for w in re.findall(r"[A-Za-z][A-Za-z.'-]{2,}", label) if w.lower() not in GENERIC]


def predict(model: str, tiles: list[str], main: int, slot: int) -> tuple[list[str], int]:
    s = pmv.Session("probe", list(tiles), main)
    s.promoted(slot, model)
    return s.tiles, s.main


class Probe:
    def __init__(self, stick, out: Path, ask: bool):
        self.stick, self.out, self.ask = stick, out, ask
        self.t0 = time.monotonic()
        self.log: list[dict] = []

    def note(self, step: str, **kw) -> None:
        row = {"t": round(time.monotonic() - self.t0, 2), "step": step, **kw}
        self.log.append(row)
        print(f"[{row['t']:6.1f}s] {step} " + " ".join(f"{k}={v}" for k, v in kw.items())[:220])

    async def nodes(self) -> list[pmv.Node] | None:
        r = await self.stick.run("shell", pmv.UI_DUMP, timeout=10)
        return pmv.parse_nodes(r.out if r.ok else "")

    async def press(self, path: list[str]) -> None:
        await self.stick.key(*pmv.codes(path))

    def confirm(self, q: str) -> bool:
        return self.ask and input(f"\n>>> {q} [Enter = yes, n = no] ").strip().lower() not in ("n", "no")

    def keys_by_hand(self, what: str) -> list[str]:
        if not self.ask:
            return []
        raw = input(f"\n>>> With the remote, {what}. Then type the keys you pressed, in order, comma "
                    "separated (up, down, left, right, ok, back, menu), or Enter if it didn't work: ")
        try:
            return [k.strip().lower() for k in raw.split(",") if k.strip()] if pmv.codes(
                [k.strip().lower() for k in raw.split(",") if k.strip()]) is not None else []
        except ValueError as e:
            print(f"   {e}")
            return []

    async def find(self, words_: tuple[str, ...]) -> list[str] | None:
        """D-pad around until a FOCUSED node names one of words_; the moves that got there."""
        path: list[str] = []
        for mv in EXPLORE:
            await self.press(mv)
            path += mv
            await asyncio.sleep(1.0)
            ns = await self.nodes()
            hit = next((n for n in ns or () if n.focused and any(w in n.text.lower() for w in words_)), None)
            self.note("explore", keys=path, readable=ns is not None, focused=hit.text if hit else None)
            if hit:
                return path
        return None

    async def main_is(self, labels: dict[str, list[str]], timeout: float = 4.0) -> tuple[str | None, float]:
        t0 = time.monotonic()
        seen = None
        while time.monotonic() - t0 < timeout:
            seen = pmv.main_tile(await self.nodes(), labels)
            if seen is not None:
                break
            await asyncio.sleep(0.3)
        return seen, time.monotonic() - t0


async def run(a) -> dict:
    if getattr(a, "fake", False):
        # Rehearsal: the simulated Prime player (redzone/lab/prime_mv_fake.py). Nothing touches a stick,
        # and the real key map is never overwritten.
        from redzone.lab.prime_mv_fake import FakePrimeStick
        stick = FakePrimeStick()
        out = ROOT / "data" / "lab" / "prime_multiview" / ("rehearsal-" + time.strftime("%Y%m%d-%H%M%S"))
        out.mkdir(parents=True, exist_ok=True)
        if not a.label:
            a.label = "Suns,Warriors"
        return await probe(stick, a, out, stick.tune, keys_file=None)
    from redzone.tuners.adb import Stick
    from redzone.tuners.tuners import PrimeTuner
    d = json.loads(DEVICES.read_text()) if DEVICES.exists() else {}
    rows = [s for s in d.get("sticks", []) if (s.get("packages") or {}).get("prime")]
    if a.stick:
        rows = [s for s in d.get("sticks", []) if s["host"] == a.stick] or [{"host": a.stick, "packages": {}}]
    if not rows:
        raise SystemExit("No stick with Prime Video in data/devices.json (pass --stick HOST:5555).")
    stick = Stick(rows[0]["host"], name=rows[0].get("name", ""))
    stick.packages = rows[0].get("packages") or {}
    stick.packages.setdefault("prime", "com.amazon.firebat")
    if not await stick.connect():
        raise SystemExit(f"Can't reach {stick.host} over adb (same Wi-Fi? Tailscale quit?).")
    out = ROOT / "data" / "lab" / "prime_multiview" / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    return await probe(stick, a, out, PrimeTuner().tune)


async def probe(stick, a, out: Path, tune, keys_file: Path | None = pmv.KEYS_FILE) -> dict:
    """Steps 1-5 on any stick-like object (`key`, `run`); `tune(stick, gti, target)` opens the first
    game. keys_file=None never writes the key map (rehearsal)."""
    p = Probe(stick, out, ask=not a.yes)
    p.keys_file = keys_file
    res: dict = {"stick": stick.host, "out": _rel(out), "error": None}
    km = pmv.KeyMap(stick=stick.host, measured_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"))

    # 1. The first game, by a normal tune.
    t = await tune(stick, a.gti, f"prime:{a.gti}")
    p.note("first game", ok=t.ok, took=round(t.took, 1), steps=t.steps[-3:])
    if not t.ok:
        res["error"] = f"couldn't open the Prime game: {t.error}"
        return finish(p, res, km)
    ns = await p.nodes()
    res["player_readable"] = ns is not None and any(n.text for n in ns)
    p.note("player dump", readable=res["player_readable"], texts=[n.text for n in ns or () if n.text][:12])

    # 2. Open multiview.
    t0 = time.monotonic()
    path = await p.find(OPEN_WORDS)
    if path is not None:
        await p.press(["ok"])
        km.open = path + ["ok"]
    else:
        km.open = p.keys_by_hand("open multiview from the playing game")
    if not km.open:
        res["error"] = "multiview entry not found"
        return finish(p, res, km)
    km.timings["open_s"] = [round(time.monotonic() - t0, 2)]

    # 3. The picker: what it offers, and add up to 2 games.
    tiles: list[str] = ["tile0"]
    labels: dict[str, list[str]] = {"tile0": [w for w in (a.label or "").split(",") if w]}
    for n in range(2):
        t0 = time.monotonic()
        add = km.add_open or await p.find(ADD_WORDS)
        if not km.add_open and add:
            km.add_open = add + ["ok"]
            await p.press(["ok"])
        elif km.add_open:
            await p.press(km.add_open)
        else:
            km.add_open = p.keys_by_hand("open the 'add a game' picker")
            if not km.add_open:
                break
        await asyncio.sleep(1.2)
        rows_, focus = pmv.picker_rows(await p.nodes())
        km.picker_readable = bool(rows_) and focus is not None
        km.picker_offered = km.picker_offered or rows_
        p.note("picker", readable=km.picker_readable, rows=rows_[:10], focus=focus)
        pick = next((i for i, r in enumerate(rows_) if focus is not None and i > focus - 1 and words(r)
                     and not any(set(words(r)) & set(labels[x]) for x in tiles)), None)
        if pick is None:
            await p.press(["back"])
            break
        await p.press([km.add_next] * (pick - focus) + [km.add_select])
        name = f"tile{len(tiles)}"
        tiles.append(name)
        labels[name] = words(rows_[pick])
        km.timings.setdefault("add_s", []).append(round(time.monotonic() - t0, 2))
        await asyncio.sleep(2.0)
        seen, _ = await p.main_is(labels)
        km.add_slot = "main" if seen == name else "append"
        p.note("added", tile=name, labels=labels[name], main_now=seen)
    res["tiles"] = {t_: labels[t_] for t_ in tiles}
    if len(tiles) < 2:
        res["error"] = "couldn't add a second game"
        return finish(p, res, km)
    if km.add_slot == "main":
        tiles = tiles[::-1]   # newest first: what main_is saw
    first_main, _ = await p.main_is(labels)
    if not labels["tile0"] and first_main is None:
        res["error"] = "can't tell the main tile from the accessibility tree (pass --label TEAM1,TEAM2?)"
        return finish(p, res, km)
    km.focus_check = "uiautomator"

    # 4. Promote paths from the starting layout (slot 0 main), then the order model. After each slot
    #    is learned, the same keys again must bring the first game back (true for swap and rotate).
    for slot in range(1, len(tiles)):
        for keys in PROMOTE_TRIES.get(slot, ()):
            t0 = time.monotonic()
            await p.press(keys)
            seen, _ = await p.main_is(labels)
            took = time.monotonic() - t0
            p.note("promote try", slot=slot, keys=keys, wanted=tiles[slot], seen=seen, took=round(took, 2))
            if seen == tiles[slot]:
                km.promote[f"0>{slot}"] = keys
                km.timings.setdefault("promote_s", {}).setdefault(f"0>{slot}", []).append(round(took, 2))
                if slot == 1 or len(tiles) == 2:
                    await p.press(keys)
                    back, _ = await p.main_is(labels)
                    p.note("promote back", seen=back)
                    if back != tiles[0]:
                        res["error"] = "the same keys didn't bring the first game back (fixed layout?)"
                        return finish(p, res, km)
                break
            if seen is not None and seen != tiles[0]:
                res["error"] = f"keys {keys} made the wrong tile main"
                return finish(p, res, km)
    if "0>1" not in km.promote:
        res["error"] = "no key path made the second tile main"
        return finish(p, res, km)
    model = "swap"
    if "0>2" in km.promote:
        # Now slot 2's game is main. swap -> [c, b, a]; rotate -> [c, a, b]. Promote slot 1 and look.
        await p.press(km.promote["0>1"])
        seen, _ = await p.main_is(labels)
        fits = [m for m in ("swap", "rotate")
                if (lambda o: o[0][o[1]])(predict(m, *predict(m, tiles, 0, 2), 1)) == seen]
        p.note("order model", seen=seen, fits=fits)
        if not fits:
            res["error"] = "tile order after a switch matches neither swap nor rotate"
            return finish(p, res, km)
        model = fits[0]
    km.after = model
    obs = [2, 1] if "0>2" in km.promote else []

    # 5. Timed, verified switches.
    s = pmv.Session("probe", list(tiles), 0)
    for slot in obs:
        s.promoted(slot, km.after)
    results = []
    for _ in range(a.rounds):
        options = [j for j in range(len(s.tiles)) if j != s.main and km.path(s.main, j) is not None]
        if not options:
            break
        j = random.choice(options)
        want = s.tiles[j]
        t0 = time.monotonic()
        await p.press(km.path(s.main, j))
        seen, _ = await p.main_is(labels)
        took = time.monotonic() - t0
        ok = seen == want
        results.append({"ok": ok, "took": round(took, 2), "slot": j})
        if ok:
            s.promoted(j, km.after)
        else:
            res["error"] = f"round {len(results)}: wanted {want}, main is {seen}; stopped"
            break
        await asyncio.sleep(a.gap)
    km.timings.setdefault("promote_s", {}).setdefault("rounds", []).extend(r["took"] for r in results if r["ok"])
    res["rounds"] = results
    res["gate_bench"] = pmv.gate(results, mid_play_entries=None)
    return finish(p, res, km)


def _rel(path: Path) -> str:
    try:
        return str(Path(path).relative_to(ROOT))
    except ValueError:
        return str(path)


def finish(p: Probe, res: dict, km: pmv.KeyMap) -> dict:
    res["keys"] = km.to_dict()
    res["can_switch"], res["can_build"] = km.can_switch, km.can_build
    res["log"] = p.log
    (p.out / "probe.json").write_text(json.dumps(res, indent=1, default=str))
    kf = getattr(p, "keys_file", pmv.KEYS_FILE)
    if km.can_switch and kf is not None:
        kf.parent.mkdir(parents=True, exist_ok=True)
        kf.write_text(json.dumps(km.to_dict(), indent=1))
        print(f"\nWrote {_rel(kf)}.")
    print("\nResult:", json.dumps({k: res.get(k) for k in ("error", "can_switch", "can_build", "gate_bench")}))
    print(f"Saved {_rel(p.out)}/probe.json; send it to the coordinator.")
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stick", help="HOST:5555 (default: the first stick with Prime Video)")
    ap.add_argument("--gti", help="the Prime game's gti (the part after prime: in its target)")
    ap.add_argument("--label", default="", help="words that name the first game on screen, comma separated")
    ap.add_argument("--rounds", type=int, default=20, help="timed tile switches (the gate needs >= 20)")
    ap.add_argument("--gap", type=float, default=2.0, help="seconds between timed switches")
    ap.add_argument("--yes", action="store_true", help="never ask; stop when something isn't found by itself")
    ap.add_argument("--fake", action="store_true", help="rehearse against a simulated Prime player (no stick, "
                    "never writes the key map); output goes to data/lab/prime_multiview/rehearsal-<time>/")
    a = ap.parse_args()
    if not a.fake and not a.gti:
        ap.error("--gti is required (or --fake to rehearse without a stick)")
    if a.fake:
        a.gti = a.gti or "amzn1.dv.gti.rehearsal"
        a.yes = True
    res = asyncio.run(run(a))
    return 0 if res.get("error") is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
