"""A tiny synthetic archive for the decisive-Q dataset tests: the repo's two NBA fixture games, moved onto made-up nights.

CI has no real training nights, so every qdata test builds this folder in tmp_path: each night holds the two fixture
games (tests/fixtures/espn_nba_summary_401909860.json and _401909906.json) with every wallclock moved so they tip 20
minutes apart on that night, under new game ids, plus a scoreboard naming them. Training nights are in March 2025; the
fenced and after-cutoff nights (locked Dec 2025, tuning Oct 2026, Nov 2025) are there so the fence has something to
refuse. Shared by tests/test_qdata_*.py.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

FIX = Path(__file__).resolve().parent / "fixtures"
GAMES = ("401909860", "401909906")
TRAIN_NIGHTS = ("20250301", "20250302", "20250303", "20250304", "20250305", "20250306", "20250307", "20250308")
FENCED_NIGHTS = ("20251215", "20261020")          # locked, tuning
LATE_NIGHTS = ("20251101",)                       # after the training cutoff, in no fence


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ts(s: str) -> float:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def write_archive(root: Path, nights=TRAIN_NIGHTS + FENCED_NIGHTS + LATE_NIGHTS) -> Path:
    """root/nba/scoreboard_<night>.json and summary_<id>.json for each night; returns root."""
    d = root / "nba"
    d.mkdir(parents=True, exist_ok=True)
    src = {g: json.loads((FIX / f"espn_nba_summary_{g}.json").read_text()) for g in GAMES}
    for n, night in enumerate(nights):
        day = datetime.strptime(night, "%Y%m%d").replace(tzinfo=timezone.utc)
        events = []
        for k, g in enumerate(GAMES):
            data = json.loads(json.dumps(src[g]))
            first = next(_ts(p["wallclock"]) for p in data["plays"] if p.get("wallclock"))
            start = (day + timedelta(hours=23, minutes=30 + 20 * k)).timestamp()
            dt = start - first
            for p in data["plays"]:
                if p.get("wallclock"):
                    p["wallclock"] = _iso(_ts(p["wallclock"]) + dt)
            gid = f"9{n:03d}{k:02d}{g[-4:]}"
            (d / f"summary_{gid}.json").write_text(json.dumps(data))
            events.append({"id": gid, "date": _iso(start)[:16] + "Z"})
        (d / f"scoreboard_{night}.json").write_text(json.dumps({"events": events}))
    return root
