"""Stick assignment backtest (overseer item b, lane 3): what the two sticks' different tune speeds
cost the hot standby on a replayed day, and what the levers buy.

    python -m redzone.lab.stick_assign 20261101 [--json out.json]

Live lab, Nov 4 (docs/livelab-2026-11-04.md): pre-tune to PLAYING p50 2.33 s on the 4K Plus (.150)
vs 3.74 s on the 4K 2023 (.181). Every switch flips the TV to the hidden stick, so the roles swap
each time and the slow stick is the hidden one every other switch. The demo's modeled hidden stick
(standby.ShadowStick) gets one tune multiplier per stick (p50 / mean of the two p50s).

Variants, each the whole day on the real engine (dry run, nothing touched):
  one_speed     today's model: both sticks the same speed (the baseline)
  plus_first    per-stick, the 4K Plus starts hidden
  s2023_first   per-stick, the 4K 2023 starts hidden
The two per-stick runs are the "first assignment" lever. Nothing here changes the live app.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRETUNE_P50_S = {"4k_plus": 2.33, "4k_2023": 3.74}   # live lab, Nov 4


def multipliers(p50: dict[str, float] = PRETUNE_P50_S) -> dict[str, float]:
    mean = sum(p50.values()) / len(p50)
    return {k: round(v / mean, 3) for k, v in p50.items()}


VARIANTS = {
    "one_speed": (None, None),
    "plus_first": (multipliers(), "4k_plus"),
    "s2023_first": (multipliers(), "4k_2023"),
}


def one(day: str, sticks: dict | None, first: str | None) -> dict:
    """One replayed day with the given hidden-stick model (call in its own process)."""
    from redzone import standby
    from redzone.demo.__main__ import build, scorecard_config
    e = build(day, 30.0, 2.0, scorecard_config())
    old = e.shadow
    e.shadow = standby.ShadowStick(app_s=old.app_s, sticks=sticks, first_hidden=first)
    e.advance(e.t1)
    card = e.report()
    return {"preload": card.get("preload"), "mid_play_cuts": card.get("mid_play_cuts"),
            "switches": card.get("switches")}


def run(day: str) -> dict:
    procs = {}
    for name, (sticks, first) in VARIANTS.items():
        code = (f"import json,sys; sys.path.insert(0,{str(ROOT)!r}); from redzone.lab.stick_assign import one; "
                f"print('@@'+json.dumps(one({day!r}, {sticks!r}, {first!r})))")
        procs[name] = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE,
                                       stderr=subprocess.DEVNULL, text=True, cwd=ROOT)
    out = {}
    for name, p in procs.items():
        so, _ = p.communicate(timeout=3600)
        line = next((x for x in so.splitlines() if x.startswith("@@")), None)
        out[name] = json.loads(line[2:]) if line else {"error": "no result"}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("day")
    ap.add_argument("--json", type=Path)
    a = ap.parse_args(argv)
    res = {"day": a.day, "multipliers": multipliers(), "variants": run(a.day)}
    for name, r in res["variants"].items():
        p = r.get("preload") or {}
        yt = (p.get("seconds_to_picture") or {}).get("youtubetv", {})
        print(f"{name:12s} hits {p.get('hits')}/{p.get('switches')} ({p.get('hit_rate')}%)  loading {p.get('still_loading')}"
              f"  yttv mean {yt.get('mean_s')} s  mid-play {r.get('mid_play_cuts')}  by_stick {p.get('by_stick')}")
    if a.json:
        a.json.write_text(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
