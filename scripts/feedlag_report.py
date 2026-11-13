#!/usr/bin/env python3
"""Read the nightly feed-arrival logs: lag quantiles per source and row kind, and the replay-lab lag profile with real lags.

    python3 scripts/feedlag_report.py                          # every night in data/feedlag/
    python3 scripts/feedlag_report.py --nights 2026-11-12      # chosen nights
    python3 scripts/feedlag_report.py --json out.json          # the same numbers as JSON
    python3 scripts/feedlag_report.py --write-profile          # data/feedlag/replay_profile.json (a copy, see below)

The profile is the committed redzone/lab/feed_lag_profile.json with ESPN's NBA/college entries and the NBA liveData
entry replaced by the measured arrival lags (only where the logs hold enough rows). This script never writes the
committed file; replaying with the new one is `timing.LagSampler(timing.load_profile(Path(...)))`, and swapping it in
for good is a reviewed change. Method and caveats: docs/memory/runbooks/feedlag.md.

Why: C-02 (v2 WORKPLAN, tracks/C-picture-audio-timing.md C2): turns the nightly arrival log into the lag tables and the replay input the other timing tracks quote.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from redzone.lab import feedlag_report as fr  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Arrival-lag report from data/feedlag/*.jsonl")
    ap.add_argument("--dir", type=Path, default=ROOT / "data" / "feedlag")
    ap.add_argument("--nights", nargs="*", default=None, help="YYYY-MM-DD ... (default: every night found)")
    ap.add_argument("--max-gap", type=float, default=fr.WIDE_GAP, help="leave out rows whose window is wider (seconds)")
    ap.add_argument("--assumed", type=float, default=fr.ASSUMED_LAG, help="the lag the show assumes (seconds)")
    ap.add_argument("--json", type=Path, help="also write the report as JSON")
    ap.add_argument("--write-profile", nargs="?", const="", default=None, metavar="PATH",
                    help="write the rebuilt replay profile (default data/feedlag/replay_profile.json)")
    ap.add_argument("--basis", choices=("mid", "obs"), default="mid",
                    help="profile lag: mid = midpoint of the poll window (default), obs = first-seen (upper bound)")
    a = ap.parse_args(argv)

    files = fr.day_files(a.dir, a.nights)
    if not files:
        print(f"no day files under {a.dir} (run scripts/feedlag_logger.py first)")
        return 2
    rows = fr.load_arrivals(files)
    rep = fr.report(rows, a.max_gap, a.assumed)
    print(fr.format_report(rep))
    if a.json:
        a.json.write_text(json.dumps(rep, indent=1) + "\n")
        print(f"\nwrote {a.json}")
    if a.write_profile is not None:
        target = Path(a.write_profile) if a.write_profile else a.dir / "replay_profile.json"
        prof = fr.build_replay_profile(rows, basis=a.basis, max_gap=a.max_gap)
        try:
            fr.write_profile(prof, target)
        except ValueError as e:
            print(e)
            return 1
        replaced = prof["feedlag"]["replaced"]
        print(f"\nwrote {target}: replaced {', '.join(replaced) if replaced else 'nothing (not enough rows yet)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
