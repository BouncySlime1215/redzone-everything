"""Screen Lab: learn which signals tell us what's really on the TV.

Run it during a live game while you tap Game / Commercial / Loading / Error / Replay
in the phone's Lab tab whenever the screen changes (and "I just saw it" right as a play ends):

    .venv/bin/python scripts/screen_lab.py            # records until Ctrl-C
    .venv/bin/python scripts/screen_lab.py --report   # after: how well each signal matched your taps

Every 2s it records, from the stick: playback state + position (media session), the foreground
app screen, and every 10s the on-screen text. Nothing is captured from the video itself.
Output: data/lab/screen_<date>.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone.tuners.adb import Stick  # noqa: E402

OUT = ROOT / "data" / "lab" / f"screen_{datetime.now():%Y-%m-%d}.jsonl"


async def record() -> None:
    d = json.loads((ROOT / "data/devices.json").read_text())
    s0 = d["sticks"][0]
    st = Stick(s0["host"], name=s0.get("name", ""))
    st.packages = s0.get("packages", {})
    if not await st.connect():
        sys.exit(f"can't reach {st.host} ({st.state}) — run scripts/doctor.py")
    try:
        await _record(st)
    finally:
        await st.close()   # its always-open adb shell


async def _record(st: Stick) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    print(f"Recording to {OUT}. Tap labels on the phone's Lab tab. Ctrl-C to stop.")
    n, last_text = 0, 0.0
    with open(OUT, "a") as f:
        while True:
            t = time.time()
            pb = await st.playback()
            row = {"t": round(t, 2), "state": pb["state"], "pkg": pb["pkg"], "pos": pb["position"],
                   "fg": await st.foreground()}
            if t - last_text > 10:
                row["text"] = (await st.screen_text())[:400]
                last_text = t
            f.write(json.dumps(row) + "\n")
            f.flush()
            n += 1
            if n % 15 == 0:
                print(f"  {datetime.now():%H:%M:%S} {row['state']:<10} pos={row['pos']} {row['fg'][-40:]}")
            await asyncio.sleep(max(0.2, 2 - (time.time() - t)))


def report() -> None:
    rows = [json.loads(x) for x in OUT.read_text().splitlines()] if OUT.exists() else []
    db = sqlite3.connect(ROOT / "data" / "redzone.db")
    labels = db.execute("SELECT ts, label FROM labels ORDER BY ts").fetchall()
    cal = db.execute("SELECT ts, target, sample, mu FROM calibrations ORDER BY ts").fetchall()
    if not rows or not labels:
        print("Need both a recording and some phone labels.")
        return
    # What label was active at each recorded moment?
    def label_at(t):
        cur = None
        for ts, lab in labels:
            if ts <= t:
                cur = lab
        return cur
    table: dict[tuple, int] = {}
    pos_moves: dict[str, list[float]] = {}
    prev = None
    for r in rows:
        lab = label_at(r["t"])
        if lab is None:
            continue
        table[(lab, r["state"])] = table.get((lab, r["state"]), 0) + 1
        if prev and r.get("pos") is not None and prev.get("pos") is not None:
            pos_moves.setdefault(lab, []).append((r["pos"] - prev["pos"]) / 1000 / max(0.1, r["t"] - prev["t"]))
        prev = r
    print("\nWhat the stick reported vs what you saw (counts):")
    for (lab, state), n in sorted(table.items()):
        print(f"  you saw {lab:<11} stick said {state:<11} x{n}")
    print("\nPlayback position speed by label (1.0 = playing normally):")
    for lab, v in pos_moves.items():
        v.sort()
        print(f"  {lab:<11} median {v[len(v) // 2]:.2f}x  (n={len(v)})")
    if cal:
        print("\nDelay calibrations (I just saw it):")
        for ts, target, sample, mu in cal[-10:]:
            print(f"  {datetime.fromtimestamp(ts):%H:%M:%S} {target}: sample {sample:.1f}s → estimate {mu:.1f}s")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    if a.report:
        report()
    else:
        try:
            asyncio.run(record())
        except KeyboardInterrupt:
            print("\nstopped")
