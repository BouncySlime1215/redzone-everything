"""Download public ESPN play-by-play for backtesting, never for live TV control.

    .venv/bin/python scripts/download_recent.py nba 14
    .venv/bin/python scripts/download_recent.py nfl --date 20261101

Dates are event dates in America/New_York. Existing archive files are preserved.
Every requested date gets a manifest, including no games, unfinished games, and errors.
Why: Downloads public ESPN play-by-play for backtests, never for live TV control (docstring).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parent.parent
PATHS = {"nfl": "football/nfl", "nba": "basketball/nba", "nhl": "hockey/nhl", "mlb": "baseball/mlb"}
EASTERN = ZoneInfo("America/New_York")


def parse_date(value: str) -> date:
    try:
        if len(value) != 8 or not value.isdigit():
            raise ValueError
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must be YYYYMMDD") from exc


def event_date(event: dict) -> date | None:
    try:
        stamp = datetime.fromisoformat(event["date"].replace("Z", "+00:00"))
        return stamp.astimezone(EASTERN).date() if stamp.tzinfo else None
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def fetch_json(client: httpx.Client, url: str, params: dict) -> dict:
    response = client.get(url, params=params)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return data


def save_new(path: Path, data: dict) -> bool:
    """Atomic creation without replacing a file another download already saved."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False))
        try:
            os.link(tmp, path)
        except FileExistsError:
            return False
        return True
    finally:
        tmp.unlink(missing_ok=True)


def download_day(client: httpx.Client, league: str, day: date, arch: Path) -> dict:
    arch.mkdir(parents=True, exist_ok=True)
    key = day.strftime("%Y%m%d")
    base = f"https://site.api.espn.com/apis/site/v2/sports/{PATHS[league]}"
    manifest = {"league": league, "date": key, "timezone": "America/New_York",
                "checked_at": datetime.now(EASTERN).isoformat(), "status": "error",
                "scoreboard": "", "events": [], "excluded": [], "errors": []}
    path = arch / f"scoreboard_{key}.json"
    try:
        cached = path.exists()
        sb = json.loads(path.read_text()) if cached else fetch_json(client, f"{base}/scoreboard", {"dates": key})
        if not isinstance(sb, dict):
            raise ValueError("scoreboard is not a JSON object")
        events = sb.get("events")
        if not isinstance(events, list):
            raise ValueError("scoreboard has no events list")
        final = []
        same_day = []
        for event in events:
            if not isinstance(event, dict):
                raise ValueError("scoreboard contains a malformed event")
            local_day = event_date(event)
            state = event.get("status", {}).get("type", {}).get("state")
            if local_day != day:
                manifest["excluded"].append({"id": event.get("id"), "date": event.get("date"),
                                             "reason": "different Eastern date" if local_day else "unknown event date"})
                continue
            same_day.append(event)
            if state == "post":
                final.append(event)
            else:
                manifest["excluded"].append({"id": event.get("id"), "reason": "not final", "state": state})
        if not cached:
            # A canceled or unfinished game is not "no games".
            save_new(path, {**sb, "events": same_day})
        manifest["scoreboard"] = "preserved" if cached else "downloaded"
        manifest["exact_date_events"] = len(same_day)
        for event in final:
            eid = str(event.get("id", ""))
            row = {"id": eid, "name": event.get("name"), "date": event.get("date")}
            if not eid.isdigit():
                row["summary"] = "error"
                manifest["errors"].append(f"invalid event id: {eid!r}")
            else:
                out = arch / f"summary_{eid}.json"
                try:
                    if out.exists():
                        data = json.loads(out.read_text())
                        row["summary"] = "preserved"
                    else:
                        data = fetch_json(client, f"{base}/summary", {"event": eid})
                        if not data.get("header"):
                            raise ValueError("summary has no event header")
                        if str(data["header"].get("id", "")) != eid:
                            raise ValueError("summary event id does not match requested event")
                        save_new(out, data)
                        row["summary"] = "downloaded"
                        time.sleep(0.25)
                    if not isinstance(data, dict) or not data.get("header"):
                        raise ValueError("summary has no event header")
                    if str(data["header"].get("id", "")) != eid:
                        raise ValueError("summary event id does not match requested event")
                except (httpx.HTTPError, ValueError, OSError) as exc:
                    row["summary"] = "error"
                    manifest["errors"].append(f"event {eid}: {exc}")
            manifest["events"].append(row)
        manifest["status"] = ("partial" if manifest["errors"] else "complete" if final else
                              "no_final_events" if same_day else "no_events")
    except (httpx.HTTPError, ValueError, OSError, TypeError) as exc:
        manifest["errors"].append(str(exc))
    # Retain each attempt; a retry must not erase a failed manifest.
    save_new(arch / f"download_manifest_{key}_{time.time_ns()}.json", manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("league", choices=PATHS)
    parser.add_argument("days", type=int, nargs="?", default=None, help="previous N Eastern dates (default14)")
    parser.add_argument("--date", type=parse_date, dest="exact_date", help="one exact Eastern event date, YYYYMMDD")
    args = parser.parse_args(argv)
    if args.exact_date and args.days is not None:
        parser.error("choose --date or the days argument, not both")
    if args.days is not None and args.days < 1:
        parser.error("days must be positive")
    days = ([args.exact_date] if args.exact_date else
            [datetime.now(EASTERN).date() - timedelta(days=i) for i in range(1, (args.days or 14) + 1)])
    failed = False
    with httpx.Client(timeout=30, headers={"User-Agent": "RedZoneEverything/1.0 (public archive replay)"}) as client:
        for day in days:
            result = download_day(client, args.league, day, ROOT / "data/archive" / args.league)
            print(json.dumps(result, ensure_ascii=False), flush=True)
            failed |= bool(result["errors"])
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
