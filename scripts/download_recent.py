"""Download recent NBA/NHL (or any league) games' play-by-play for backtesting.

    .venv/bin/python scripts/download_recent.py nba 14     # last 14 days of NBA
"""
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
PATHS = {"nfl": "football/nfl", "nba": "basketball/nba", "nhl": "hockey/nhl", "mlb": "baseball/mlb"}
league, days = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 14
arch = ROOT / "data/archive" / league
arch.mkdir(parents=True, exist_ok=True)
base = f"https://site.api.espn.com/apis/site/v2/sports/{PATHS[league]}"
with httpx.Client(timeout=20, headers={"User-Agent": "Mozilla/5.0 RedZoneEverything/1.0"}) as c:
    for i in range(1, days + 1):
        d = (date.today() - timedelta(days=i)).strftime("%Y%m%d")
        sb = c.get(f"{base}/scoreboard", params={"dates": d}).json()
        evs = [e for e in sb.get("events", []) if e.get("status", {}).get("type", {}).get("state") == "post"]
        if not evs:
            continue
        (arch / f"scoreboard_{d}.json").write_text(json.dumps({"events": evs}))
        for e in evs:
            out = arch / f"summary_{e['id']}.json"
            if not out.exists():
                r = c.get(f"{base}/summary", params={"event": e["id"]})
                if r.status_code == 200:
                    out.write_text(r.text)
                time.sleep(0.25)
        print(league, d, len(evs), flush=True)
