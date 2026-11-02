"""Download a past NFL regular season (scoreboards + play-by-play) for the training lab.

    .venv/bin/python scripts/download_season.py 2025
Polite: one request at a time, skips files it already has. About 140 MB per season.
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
season = sys.argv[1] if len(sys.argv) > 1 else "2025"
arch = ROOT / "data/archive/nfl"
arch.mkdir(parents=True, exist_ok=True)
base = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
with httpx.Client(timeout=20, headers={"User-Agent": "Mozilla/5.0 RedZoneEverything/1.0"}) as c:
    for week in range(1, 19):
        sb = c.get(f"{base}/scoreboard", params={"seasontype": 2, "week": week, "dates": season}).json()
        by_day: dict[str, list] = {}
        for ev in sb.get("events", []):
            day = datetime.fromisoformat(ev["date"].replace("Z", "+00:00")).strftime("%Y%m%d")
            by_day.setdefault(day, []).append(ev)
        for day, evs in by_day.items():
            p = arch / f"scoreboard_{day}.json"
            if not p.exists():
                p.write_text(json.dumps({"events": evs}))
        for ev in sb.get("events", []):
            out = arch / f"summary_{ev['id']}.json"
            if out.exists():
                continue
            r = c.get(f"{base}/summary", params={"event": ev["id"]})
            if r.status_code == 200:
                out.write_text(r.text)
            elif r.status_code in (403, 429):
                time.sleep(30)
            time.sleep(0.3)
        print("week", week, "done", flush=True)
