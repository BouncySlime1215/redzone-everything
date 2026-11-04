"""Time the TV's local input keys against SmartThings, on Nick's TV (~1 minute, Nick at the TV).

    .venv/bin/python scripts/tv_local_probe.py [--rounds 3] [--out data/tv_local_probe.json]

The fast path already exists (redzone/tv.py "local": Samsung's remote socket on port 8002,
KEY_HDMI1/KEY_HDMI2, a kept-open socket ~50 ms a key). It is used only once scripts/tv_setup.py has
watched it switch THIS TV (devices.json tv.local_input_verified); some Samsung models ignore the
keys. This probe answers the open question without changing anything: does a local key switch this
TV, and how much faster than SmartThings (median 1.98 s, Nov 3) is it to a confirmed input?

Each round flips HDMI1 <-> HDMI2 twice: once with a local key, once through SmartThings, and times
each from the command to the TV's own readback (SmartThings input status) showing the new input.
It changes no setting. If the TV was never paired it shows "Allow" once; the pairing token goes to
data/tv_token.txt as tv_setup.py does, and this script never prints or logs it.

DISRUPTIVE: it switches the TV's input. Only with Nick at the TV and no game he is watching.
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

from redzone import tv as tvmod  # noqa: E402

READ_EVERY_S = 0.25
CONFIRM_WITHIN_S = 8.0


async def _confirmed(tv, want: str) -> float | None:
    """Seconds until the TV reports `want` (None = not within CONFIRM_WITHIN_S)."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < CONFIRM_WITHIN_S:
        if await tv.input_status() == want:
            return time.monotonic() - t0
        await asyncio.sleep(READ_EVERY_S)
    return None


async def _one(tv, method: str, want: str) -> dict:
    t0 = time.monotonic()
    sent = await (tv.key(f"KEY_{want}") if method == "local" else tv.st_input(want))
    cmd_s = time.monotonic() - t0
    seen = await _confirmed(tv, want) if sent else None
    return {"method": method, "to": want, "sent": bool(sent), "cmd_s": round(cmd_s, 3),
            "confirmed_s": round(cmd_s + seen, 3) if seen is not None else None,
            "error": "" if sent else tv.last_error[:160]}


def _median(xs: list[float]) -> float | None:
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else None


async def main_async(rounds: int, out: Path) -> int:
    tv = tvmod.SamsungTV.from_devices()
    if not tv.host or not tv.st_device:
        print("needs the TV's address and its SmartThings device in data/devices.json (scripts/tv_setup.py)")
        return 2
    start = await tv.input_status()
    if start not in ("HDMI1", "HDMI2"):
        print(f"the TV reports input {start!r}; put it on one of the sticks first")
        return 2
    rows, cur = [], start
    for _ in range(rounds):
        for method in ("local", "smartthings"):
            want = "HDMI2" if cur == "HDMI1" else "HDMI1"
            r = await _one(tv, method, want)
            rows.append(r)
            print(f"{method:11s} -> {want}: sent={r['sent']} confirmed={r['confirmed_s']} s {r['error']}")
            now = await tv.input_status()
            cur = now if now in ("HDMI1", "HDMI2") else cur
            await asyncio.sleep(2.0)
    tv._drop_local()
    summary = {m: {"n": sum(r["method"] == m for r in rows),
                   "confirmed": sum(r["method"] == m and r["confirmed_s"] is not None for r in rows),
                   "median_s": _median([r["confirmed_s"] for r in rows if r["method"] == m and r["confirmed_s"]])}
               for m in ("local", "smartthings")}
    report = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "rounds": rows, "summary": summary,
              "local_trusted_before": tv.local_trusted}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1))
    print(json.dumps(summary, indent=1))
    loc = summary["local"]
    if loc["n"] and loc["confirmed"] == loc["n"]:
        print("Local keys switched this TV every time. To use them: scripts/tv_setup.py (it records the proof).")
    else:
        print("Local keys did not switch this TV every time: SmartThings stays the path.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--out", type=Path, default=ROOT / "data/tv_local_probe.json")
    a = ap.parse_args(argv)
    return asyncio.run(main_async(a.rounds, a.out))


if __name__ == "__main__":
    sys.exit(main())
