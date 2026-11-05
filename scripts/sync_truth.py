"""[RC7 req. 2, grader v10] data/sync.jsonl -> data/sync_truth/<night>.jsonl: one row per automatic anchor.

    python scripts/sync_truth.py data/sync.jsonl                 # writes data/sync_truth/<night>.jsonl
    python scripts/sync_truth.py data/sync.jsonl --out DIR

Each anchor row the sync filter logged (redzone/decider/sync.py measure: z = what the anchor measured,
err = z minus the filter's prediction just before it) becomes
    {"stick": "hdmi1"|"hdmi2", "night": "YYYY-MM-DD", "t": epoch, "est_behind_s": z - err,
     "true_behind_s": z, "source": "yttv"|"anchor140"|"gold", "kind": <filter kind>}
i.e. the app's belief at that moment against an independent measurement. Manual taps are never
written (no training or grading signal from Nick). Rows with no stick (the input wasn't known) or no
err are skipped. The night is the US/Eastern evening the row belongs to (before 6 AM counts as the
night before). Nothing here reads or writes the LAN: the stick is its HDMI input.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# filter kind -> grader source. clock: the YouTube TV playback clock; flip / score: self-calibration (#140)
# boundary and scorebug anchors; gold: the gold capture's own rows. Anything else (tap included) is dropped.
SOURCES = {"clock": "yttv", "position": "yttv", "flip": "anchor140", "score": "anchor140", "gold": "gold"}
ET = timezone(timedelta(hours=-5))   # US/Eastern standard time (November)


def night_of(t: float) -> str:
    d = datetime.fromtimestamp(t, ET)
    if d.hour < 6:
        d -= timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def rows_from(lines) -> list[dict]:
    out = []
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if not isinstance(r, dict):
            continue
        src = SOURCES.get(r.get("kind"))
        stick = str(r.get("stick") or "").lower().replace(" ", "")
        if src is None or r.get("z") is None or r.get("err") is None or r.get("t") is None:
            continue
        if stick not in ("hdmi1", "hdmi2"):
            continue
        z, err, t = float(r["z"]), float(r["err"]), float(r["t"])
        out.append({"stick": stick, "night": night_of(t), "t": round(t, 3), "est_behind_s": round(z - err, 3),
                    "true_behind_s": round(z, 3), "source": src, "kind": r["kind"]})
    return out


def write(rows: list[dict], out_dir: Path) -> dict[str, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r["night"], []).append(r)
    for night, rs in by.items():
        (out_dir / f"{night}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in sorted(rs, key=lambda r: r["t"])))
    return {n: len(rs) for n, rs in sorted(by.items())}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("logs", type=Path, nargs="+", help="data/sync.jsonl (and its rolled .jsonl.1)")
    ap.add_argument("--out", type=Path, default=ROOT / "data/sync_truth")
    a = ap.parse_args()
    rows = []
    for p in a.logs:
        rows += rows_from(p.read_text().splitlines())
    print(json.dumps(write(rows, a.out)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
