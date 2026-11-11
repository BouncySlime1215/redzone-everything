"""PARK-HANDS acceptance on the real sticks and TV: the park loop through the APP's controller (unit 7).

    python scripts/park_hands_live.py --channels espn,fs1,espn2,tnt --moves 40 --look 16

One move = Controller.park_tune (the hidden stick to the next channel, with its read), park_pause (frozen about 1 s
later), `--look` seconds on the picture, then park_jump (resume, flip, pause the stick left). The flags are set in
memory only for this run (park.enabled, sound_guard.enabled, sound_guard.must_be_silent; data/config.yaml is not
written). Silence is read back before the first command and after every flip; any sound stop ends the run with
nothing more sent. At the end every paused stick is resumed. One row per step goes to --out (words and numbers
only: no address, device id or frame). Compare with ledger L19: tune 2.7 s median, pause 0.77 s, jump 2.25 s, 0
failures (with the read, a tune takes about 13-19 s).

Agents never run this (CLAUDE.md: no stick or TV scripts); the coordinator does, with the TV muted.
Why: unit 7's acceptance is the same 40-move park loop the coordinator ran by hand on Nov 10, driven by the app's code.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone import control as ctl  # noqa: E402
from redzone.channels import ChannelMap  # noqa: E402
from redzone.config import Config, _deep_merge  # noqa: E402
from redzone.platform.sound_guard import MARKER, SoundGuard  # noqa: E402

FLAGS = {"mode": {"dry_run": False}, "park": {"enabled": True},
         "sound_guard": {"enabled": True, "must_be_silent": True}}


def app_running() -> bool:
    try:
        out = subprocess.run(["pgrep", "-f", "[Pp]ython[^ ]* (.* )?-m redzone( |$)"], capture_output=True, text=True,
                             timeout=5).stdout.split()
        return bool(out)
    except Exception:
        return True


async def run(args) -> int:
    if MARKER.exists():
        print("Refused: a sound incident is open (data/sound_guard/incident.json). Nothing was sent.")
        return 4
    if app_running():
        print("Refused: the app is running (or that cannot be ruled out). Nothing was sent.")
        return 2
    cfg = Config()
    cfg.data = _deep_merge(cfg.data, FLAGS)                  # in memory only
    c = ctl.Controller(cfg, ChannelMap(ROOT / "data" / "channels.json"))
    c.load_devices()
    c.tv.sound_settings = lambda k, fb=None: cfg.get(k, fb)
    c.tv.sound_guard = g = SoundGuard(c.tv)
    s = await g.silence()
    if await g.check("park_hands_live start") != "ok":
        print(f"Silence not read back before the start ({ {k: s.get(k) for k in ('mute', 'volume')} }). Nothing else sent.")
        return 4
    for st in c.sticks:
        if not await st.connect():
            print(f"{c._stick_label(st.host)} did not answer. Nothing was sent to a stick.")
            return 2
    inp = (g.last or {}).get("input")
    on = next((i for i, st in enumerate(c.sticks) if c.hdmi.get(st.host) == inp), None)
    if on is None:
        print("Cannot tell which stick is on screen. Nothing was sent to a stick.")
        return 2
    c.active = on
    why = c.park_why()
    if why:
        print(f"PARK refuses: {why}. Nothing was sent to a stick.")
        return 2
    chans = [x.strip() for x in args.channels.split(",") if x.strip()]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows, rc = [], 0
    with out.open("a") as f:
        for i in range(args.moves):
            target = f"youtubetv:{chans[i % len(chans)]}"
            steps = [await c.park_tune(target)]
            if steps[-1].ok:
                steps.append(await c.park_pause(target, time.time() + 1.0))
            if steps[-1].ok:
                await asyncio.sleep(args.look)
                steps.append(await c.park_jump(target))
            for st in steps:
                row = {"move": i + 1, **st.view()}
                rows.append(row)
                f.write(json.dumps(row) + "\n")
                print(f"{i + 1:>3} {st.step:<5} {st.outcome:<7} {st.took_s:5.1f} s  {st.target:<16} {st.way}  {st.why}")
            if any(st.outcome == "stop" for st in steps):
                print("SOUND STOP: nothing more is sent. Clear the incident by hand after finding its cause.")
                rc = 4
                break
            if steps[-1].ok:
                await asyncio.sleep(args.look)
        fin = await c.park_finish()
        f.write(json.dumps({"move": 0, **fin.view()}) + "\n")
    summary(rows, fin)
    return rc


def summary(rows: list, fin) -> None:
    def med(step, key=None):
        xs = [(r["numbers"].get(key) if key else r["took_s"]) for r in rows if r["step"] == step and r["outcome"] == "ok"]
        xs = [x for x in xs if isinstance(x, (int, float))]
        return f"{statistics.median(xs):.2f} s (n={len(xs)})" if xs else "none"
    bad = {}
    for r in rows:
        if r["outcome"] != "ok":
            bad[f"{r['step']} {r['outcome']}"] = bad.get(f"{r['step']} {r['outcome']}", 0) + 1
    reads = [r["numbers"].get("read") for r in rows if r["step"] == "tune" and r["outcome"] == "ok"]
    print(f"tune {med('tune')}, pause {med('park')}, jump {med('jump')}, flip {med('jump', 'flip_s')}; "
          f"places read {sum(1 for x in reads if x)} of {len(reads)}; not ok: {bad or 'none'}; finish: {fin.outcome}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--channels", default="espn,fs1,espn2,tnt", help="YouTube TV short names, in turn")
    ap.add_argument("--moves", type=int, default=40)
    ap.add_argument("--look", type=float, default=16.0, help="seconds on each picture before the next jump")
    ap.add_argument("--out", default=str(ROOT / "data" / "park_hands" / "run.jsonl"))
    return asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
