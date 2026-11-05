"""[SYNC A, EXP-800/801] data/sync_truth/edge/*.jsonl -> where each stick sat against its live edge, per app.

    python scripts/sync_edge_report.py                          # every night in data/sync_truth/edge
    python scripts/sync_edge_report.py data/sync_truth/edge/2026-11-05.jsonl --json

Per stick x app: rows, tunes, tune-to-playing (p50 / p95), landing positions per channel (median and
spread: a tune that lands off the channel's usual position did not land on the live edge), behind the
live edge (p95), the app's error (its belief minus the meter: p95 of |err|), and the rows where the stick
sat > force_live_s behind without a hold (would_force) and the re-links made. Lane 3's syncgrade (#279)
is the grader; this is the quick look.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def q(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(p * (len(xs) - 1) + 0.5))], 2)


def report(rows: list[dict]) -> dict:
    by: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        by.setdefault((str(r.get("stick")), str(r.get("app"))), []).append(r)
    out = {}
    for (stick, app), rs in sorted(by.items()):
        tunes = {(r.get("target"), r.get("tune_t")) for r in rs}
        ttp = [r["tune_to_playing_s"] for r in rs if r.get("tune_to_playing_s") is not None]
        lands: dict[str, list[float]] = {}
        for r in rs:
            if r.get("tune_to_playing_s") is not None or (r.get("land_pos_s") is not None and r.get("played_s") == 0):
                lands.setdefault(str(r.get("channel")), []).append(r["land_pos_s"])
        meas = [r for r in rs if r.get("behind_live_s") is not None]
        wf = [r for r in meas if r.get("would_force")]
        out[f"{stick}/{app}"] = {
            "rows": len(rs), "measured": len(meas), "tunes": len(tunes),
            "tune_to_playing_p50": q(ttp, 0.5), "tune_to_playing_p95": q(ttp, 0.95),
            "landing_by_channel": {ch: {"n": len(v), "median": round(statistics.median(v), 2),
                                        "spread": round(max(v) - min(v), 2)} for ch, v in sorted(lands.items())},
            "behind_p95": q([r["behind_live_s"] for r in meas], 0.95),
            "err_abs_p95": q([abs(r["err_s"]) for r in meas], 0.95),
            "would_force_pct": round(100.0 * len(wf) / len(meas), 1) if meas else None,
            "forced": sum(1 for r in rs if r.get("forced")),
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", type=Path, nargs="*")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    files = a.files or sorted((ROOT / "data" / "sync_truth" / "edge").glob("*.jsonl"))
    rows = []
    for f in files:
        for line in f.read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    rep = report(rows)
    if a.json:
        print(json.dumps(rep, indent=1))
        return 0
    for k, v in rep.items():
        print(f"{k:22s} rows {v['rows']:6d} tunes {v['tunes']:4d}  tune->playing p50/p95 {v['tune_to_playing_p50']}/"
              f"{v['tune_to_playing_p95']}  behind p95 {v['behind_p95']}  |err| p95 {v['err_abs_p95']}  "
              f"would_force {v['would_force_pct']}%  re-links {v['forced']}")
        for ch, lv in v["landing_by_channel"].items():
            print(f"    {ch:18s} landings {lv['n']:3d}  median {lv['median']}  spread {lv['spread']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
