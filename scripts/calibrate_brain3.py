"""Calibrate Brain v3's cautious restart bound per break type (redzone/lab/brain3cal.py).

    python scripts/calibrate_brain3.py            # writes brain_models/entry_cal_v3.json + docs/brain3/entry_cal.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from redzone.lab import brain3cal as C  # noqa: E402

# NBA: its TV-timeout labels only count dead spells of at least 140 s (brain3data.NBA_TV_TIMEOUT_S), so
# p10 can't overrun them and a scale fitted there means nothing. NHL: one calibration night. Both keep p10.
SHIP = ("nfl",)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--alpha", type=float, default=C.ALPHA)
    ap.add_argument("--leagues", nargs="*", default=["nfl", "nba", "nhl"])
    a = ap.parse_args()
    res = C.run(tuple(a.leagues), a.alpha)
    shipped = {**res, "scales": {k: v for k, v in res["scales"].items() if k in SHIP}}
    C.save(shipped)
    out = Path(__file__).resolve().parents[1] / "docs" / "brain3" / "entry_cal.json"
    out.write_text(json.dumps(res, indent=1))
    print(json.dumps(shipped["scales"], indent=1))


if __name__ == "__main__":
    main()
