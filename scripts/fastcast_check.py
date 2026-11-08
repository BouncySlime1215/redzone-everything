"""[fastcast live check] Grade ESPN FastCast against polling from a night's flight recorder.

    python scripts/fastcast_check.py                    # today's data/recordings/<day>/plays.jsonl
    python scripts/fastcast_check.py --day 2026-11-03
    python scripts/fastcast_check.py --live             # the running service's own verdict (data/fastcast_check.json)

The service runs the check by itself while sources.fastcast is off (redzone/sources/fastcast_check.py):
FastCast in shadow on a few live games, never merged. This script is the same grade from the log
(timing, coverage, play type; the live check also compares scores). Exit code 0 = pass, 1 = fail,
2 = not decided yet. Read-only.
Why: Grades ESPN FastCast against polling: no fastcast rows appeared Nov 2-4 (lessons log).
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone.sources.fastcast_check import grade_log  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--data", type=Path, default=ROOT / "data")
    ap.add_argument("--live", action="store_true", help="print the service's own verdict instead")
    a = ap.parse_args(argv)
    if a.live:
        p = a.data / "fastcast_check.json"
        if not p.exists():
            print(f"no {p} yet: the check writes it once a live game is shadowed")
            return 2
        v = json.loads(p.read_text())
    else:
        p = a.data / "recordings" / a.day / "plays.jsonl"
        if not p.exists():
            print(f"no {p}")
            return 2
        v = grade_log(p)
    print(json.dumps(v, indent=1))
    if v["result"] == "pass":
        print('\nPASS. To turn FastCast on (no restart): curl -X POST localhost:8777/api/settings '
              '-H \'content-type: application/json\' -d \'{"changes": {"sources": {"fastcast": true}}}\'')
    return {"pass": 0, "fail": 1}.get(v["result"], 2)


if __name__ == "__main__":
    sys.exit(main())
