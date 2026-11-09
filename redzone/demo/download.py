"""Fetch one day's finished games (scoreboard + play-by-play) into data/demo/<day>/<league>/.

Same ESPN site API calls as scripts/download_recent.py, but into the demo folder (never the archive).

Why: first added in f469a653 (2026-11-02) as "Standby planner: preload the decider's own next pick; preload hit rate on the replay"; no separate decision is recorded for this module.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

PATHS = {"nfl": "football/nfl", "nba": "basketball/nba", "nhl": "hockey/nhl"}
ROOT = Path(__file__).resolve().parent.parent.parent
DEMO = ROOT / "data" / "demo"


def day_dir(day: str) -> Path:
    return DEMO / day


def download(day: str, leagues=("nfl", "nba", "nhl"), log=print) -> Path:
    import httpx
    out_root = day_dir(day)
    with httpx.Client(timeout=20, headers={"User-Agent": "Mozilla/5.0 RedZoneEverything/1.0"}) as c:
        for lg in leagues:
            base = f"https://site.api.espn.com/apis/site/v2/sports/{PATHS[lg]}"
            arch = out_root / lg
            arch.mkdir(parents=True, exist_ok=True)
            sb = c.get(f"{base}/scoreboard", params={"dates": day, "limit": 100}).json()
            evs = [e for e in sb.get("events", []) if e.get("status", {}).get("type", {}).get("state") == "post"]
            (arch / f"scoreboard_{day}.json").write_text(json.dumps({"events": evs}))
            got = 0
            for e in evs:
                out = arch / f"summary_{e['id']}.json"
                if not out.exists():
                    r = c.get(f"{base}/summary", params={"event": e["id"]})
                    if r.status_code != 200:
                        continue
                    out.write_text(r.text)
                    time.sleep(0.25)
                got += 1
            log(f"  {lg}: {got}/{len(evs)} finished games saved to {arch}")
    return out_root
