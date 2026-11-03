"""How well each channel's stream delay is predicted, from the delay samples the app has stored.

    .venv/bin/python scripts/delay_report.py               # data/redzone.db
    .venv/bin/python scripts/delay_report.py --db other.db --json

For every channel with samples: hide that channel's own samples, predict it from everything else
(the way a channel never tuned before is predicted), and check the hidden centre falls inside the
predicted interval (mu +- 2 sigma). Too narrow an interval is what cuts plays mid-play, so the line
that matters is "outside". Prints both delay models (channel = per-channel predictor, legacy = one
figure per app). Read-only: the database is opened in read-only mode.
"""
import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone.config import Config  # noqa: E402
from redzone.decider.screen import TAP_HISTORY_S, DelayBook  # noqa: E402


def rows_from(db: Path, since: float) -> list[tuple]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return con.execute("SELECT ts, target, sample, COALESCE(kind, 'tap') FROM calibrations WHERE ts >= ? "
                           "ORDER BY ts", (since,)).fetchall()
    finally:
        con.close()


def report(rows: list[tuple], defaults: dict, sigma_default: float, now: float) -> dict:
    rows = [r for r in rows if r[3] in ("tap", "screen", "boundary")]
    out = {}
    for model in DelayBook.MODELS:
        lines, outside = [], 0
        for target in sorted({r[1] for r in rows}):
            full = DelayBook(defaults, sigma_default, clock=lambda: now, model=model)
            full.load(rows)
            truth = full.centres(DelayBook.app(target)).get(target)
            if truth is None:
                continue
            held = DelayBook(defaults, sigma_default, clock=lambda: now, model=model)
            held.load([r for r in rows if r[1] != target])
            d = held.get(target)
            miss = abs(truth.mu - d.mu) > 2 * d.sigma
            outside += miss
            lines.append({"target": target, "measured": round(truth.mu, 1), "predicted": round(d.mu, 1),
                          "sigma": round(d.sigma, 1), "source": d.source, "outside": miss})
        out[model] = {"channels": len(lines), "outside": outside, "lines": lines}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=ROOT / "data/redzone.db")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    cfg = Config(user_path=ROOT / "data/config.yaml")
    now = time.time()
    rows = rows_from(a.db, now - TAP_HISTORY_S)
    rep = report(rows, cfg.get("delay.default"), float(cfg.get("delay.sigma_default")), now)
    if a.json:
        print(json.dumps(rep, indent=1))
        return
    for model, r in rep.items():
        print(f"{model}: {r['outside']} of {r['channels']} channels outside the predicted interval")
        for x in r["lines"]:
            flag = "  OUTSIDE" if x["outside"] else ""
            print(f"  {x['target'][:40]:40s} measured {x['measured']:5.1f}  predicted {x['predicted']:5.1f} "
                  f"+-{2 * x['sigma']:4.1f} ({x['source']}){flag}")


if __name__ == "__main__":
    main()
