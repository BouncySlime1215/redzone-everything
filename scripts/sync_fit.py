"""[sync fit] EXP-084: one live night's sync calibration and delay error, from the app's own logs.

Serves: EXP-084 (docs/experiments.md), North Star "Per-stick delay error p95 <= 2 s". Owner: lane 3.

    python scripts/sync_fit.py --data <night's data dir> [--since EPOCH] [--until EPOCH] [--out fit.json]

Reads (local files only, whatever the night has):
- sync.jsonl (+ .1): every anchor the filter saw. The delay error of an anchor is how far its measured delay
  (z) sat from the filter's estimate just BEFORE it (the previous row's mu for that channel), so the anchor
  never grades itself. It is an upper bound on the filter's error (it also holds the anchor's own noise).
- seeks.jsonl: YouTube TV's seek step / wake / stall (calibration.seek_fit, #140's telemetry).
- sync_probe.jsonl: MediaSession burst onsets per stick (sync_probe.burst_watch, #146).
And with `behind` logged on sync rows (#140), each channel's fresh-tune delay (calibration.delay_fit).

Grade: PASS / FAIL against 2 s once there are at least MIN_ANCHORS graded anchors, else PENDING. Prints JSON,
never addresses (sticks are numbered).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from redzone import calibration as cal
from redzone.sync_probe import burst_watch

BAR = 2.0          # the North Star line's bar (p95, seconds)
MIN_ANCHORS = 20   # fewer graded anchors than this: PENDING
SKIP = ("tune", "seek", "shift", "position")


def _pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(q * (len(s) - 1) + 0.5))], 2)


def delay_errors(rows: list[dict]) -> dict:
    """|z - mu before it| for every anchor with a measured delay, by stick (or channel when no stick)."""
    last_mu: dict[str, float] = {}
    by: dict[str, list[float]] = {}
    sticks: dict[str, str] = {}
    for r in sorted(rows, key=lambda r: r.get("t") or 0.0):
        tg = r.get("target")
        if r.get("kind") == "tune":
            last_mu.pop(tg, None)   # a fresh tune: the filter starts over from its prior
        if r.get("kind") not in SKIP and r.get("z") is not None and tg in last_mu:
            raw = str(r.get("stick") or f"channel {tg}")
            key = sticks.setdefault(raw, raw if raw.startswith("channel") else f"stick {len(sticks) + 1}")
            by.setdefault(key, []).append(abs(float(r["z"]) - last_mu[tg]))
        if r.get("mu") is not None:
            last_mu[tg] = float(r["mu"])
    every = [e for v in by.values() for e in v]
    per = {k: {"n": len(v), "p50": _pct(v, 0.5), "p95": _pct(v, 0.95)} for k, v in sorted(by.items())}
    p95 = _pct(every, 0.95)
    grade = "PENDING" if len(every) < MIN_ANCHORS else ("PASS" if p95 <= BAR else "FAIL")
    return {"n": len(every), "p95": p95, "grade": grade, "bar_s": BAR, "per": per}


def bursts(path: Path) -> dict[str, int]:
    out: dict[str, list[str]] = {}
    for p in (path.with_suffix(".jsonl.1"), path):
        if not p.exists():
            continue
        for raw in p.read_text().splitlines():
            try:
                r = json.loads(raw)
            except ValueError:
                continue
            out.setdefault(str(r.get("host") or "?"), []).extend(r.get("log") or [])
    return {f"stick {i + 1}": len(burst_watch().feed(lines)) for i, (_, lines) in enumerate(sorted(out.items()))}


def fit(data: Path, lo: float | None, hi: float | None) -> dict:
    sync_rows = cal.night_rows(data / "sync.jsonl", lo, hi)
    seeks = cal.night_rows(data / "seeks.jsonl", lo, hi)
    has_behind = any("behind" in r for r in sync_rows)
    return {
        "rows": {"sync": len(sync_rows), "seeks": len(seeks), "behind_logged": has_behind},
        "delay_error": delay_errors(sync_rows),
        "seek": {k: v[:2] for k, v in cal.seek_fit(seeks).items()} if seeks else None,
        "delays": ({k: {"s": round(v[0], 2), "n": v[1], "sd": round(v[3], 2)}
                    for k, v in cal.delay_fit(sync_rows).items()} if has_behind else None),
        "bursts": bursts(data / "sync_probe.jsonl"),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data", type=Path, required=True, help="the night's data dir (sync.jsonl, seeks.jsonl, ...)")
    ap.add_argument("--since", type=float, default=None)
    ap.add_argument("--until", type=float, default=None)
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args()
    out = fit(a.data, a.since, a.until)
    text = json.dumps(out, indent=1)
    if a.out:
        a.out.write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
