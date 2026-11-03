"""Build the live game reasoner's training rows from data/archive (not in git, see README).

    python scripts/build_reasoner_data.py nfl nba nhl

Writes data/training/reasoner_<league>.jsonl.gz: one row per archived play (brief facts, brief
text, labels from what happened next, night). See redzone/lab/reasoner_data.py.
"""
import argparse
import gzip
import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone.config import Config  # noqa: E402
from redzone.lab import reasoner_data  # noqa: E402
from redzone.models import League  # noqa: E402


def _clean(x):
    if isinstance(x, float) and not math.isfinite(x):
        return None
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    return x


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("leagues", nargs="+")
    ap.add_argument("--every", type=int, default=1)
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    pinned = ROOT / "data/eval_config.yaml"
    cfg = Config(user_path=pinned if pinned.exists() else ROOT / "data/config.yaml")
    out_dir = ROOT / "data/training"
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in a.leagues:
        lg = League(name)
        mine = set(cfg.get(f"teams.{lg.value}") or ["WSH"])
        t0 = time.time()
        rows = reasoner_data.league_rows(lg, ROOT / "data/archive" / lg.value, cfg, mine, a.every, a.limit)
        path = out_dir / f"reasoner_{lg.value}.jsonl.gz"
        with gzip.open(path, "wt") as fh:
            for r in rows:
                fh.write(json.dumps(_clean(r.as_dict())) + "\n")
        pos = sum(r.y for r in rows)
        print(f"{lg.value}: {len(rows)} rows, {len({r.gid for r in rows})} games, "
              f"{len({r.night for r in rows})} nights, y={pos / max(1, len(rows)):.3f}, "
              f"{time.time() - t0:.0f}s -> {path}")


if __name__ == "__main__":
    main()
