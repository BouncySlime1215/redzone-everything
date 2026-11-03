"""Tonight's plan without the engine: read today's NFL/NBA/NHL/MLB slate and say where each game
will be watched, what's missing, and when the pre-game checks run.

    .venv/bin/python scripts/plan_tonight.py                 # today (Eastern), print + write data/plan_YYYYMMDD.json
    .venv/bin/python scripts/plan_tonight.py --date 2026-11-03 --json
    .venv/bin/python scripts/plan_tonight.py --no-links --no-write

Read-only except for the plan file: it fetches public scoreboards (and event links unless
--no-links), never touches a stick or the TV, and never saves channels.json/providers.json.
Check results already in today's plan file are kept. The running engine rebuilds the same plan
every minute and runs the checks itself.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone import plan as P  # noqa: E402
from redzone.channels import ChannelMap  # noqa: E402
from redzone.config import Config  # noqa: E402
from redzone.discovery.providers import ProviderBook  # noqa: E402
from redzone.models import League  # noqa: E402
from redzone.sources import espn  # noqa: E402
from redzone.sources.http import Fetcher  # noqa: E402


async def slate(fetcher: Fetcher, day: date, leagues: list[League]) -> list:
    async def one(lg):
        data = await fetcher.get_json(espn.scoreboard_url(lg, day.strftime("%Y%m%d")), min_interval=0)
        if not data:
            print(f"  ! {lg.value.upper()} scoreboard unavailable", file=sys.stderr)
            return []
        return espn.parse_scoreboard(lg, data)
    return [g for part in await asyncio.gather(*(one(lg) for lg in leagues)) for g in part]


async def links_for(fetcher: Fetcher, day: date, games: list, channels: ChannelMap, book: ProviderBook,
                    my_teams: set[str]) -> int:
    """Prime Video / ESPN+ event links, applied in memory only (nothing is saved)."""
    from redzone.sources import eventlinks
    try:
        links = await eventlinks.fetch_event_links(fetcher._client, day.isoformat())
    except Exception as e:  # links are a bonus: the plan still stands without them
        print(f"  ! event links unavailable: {e}", file=sys.stderr)
        return 0
    n = 0
    for g in games:
        found = eventlinks.match(links, g.league.value, {g.away.abbr, g.away.name}, {g.home.abbr, g.home.name},
                                 espn_event_id=g.game_id, expected_start=g.start_time)
        if found:
            channels.apply_links(g, found, my_teams)
            book.remember(g, found)
            n += 1
    return n


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--date", help="YYYY-MM-DD (default: today, Eastern)")
    ap.add_argument("--data", default=str(ROOT / "data"), help="data directory (default: data/)")
    ap.add_argument("--json", action="store_true", help="print the plan as JSON")
    ap.add_argument("--no-links", action="store_true", help="skip the Prime Video / ESPN+ link lookup")
    ap.add_argument("--no-write", action="store_true", help="don't write data/plan_YYYYMMDD.json")
    a = ap.parse_args(argv)
    data = Path(a.data)
    day = date.fromisoformat(a.date) if a.date else P.day_of(time.time())
    cfg = Config(user_path=data / "config.yaml", history_dir=data / "config_history")
    on = cfg.get("mode.leagues", {}) or {}
    leagues = [lg for lg in League if on.get(lg.value, True)]
    teams = cfg.get("teams") or {}
    channels = ChannelMap(data / "channels.json")
    channels.save = lambda: None   # read-only: never rewrite the engine's files
    book = ProviderBook(data / "providers.json")
    st = cfg.get("sunday_ticket", True)

    fetcher = Fetcher()
    try:
        games = await slate(fetcher, day, leagues)
        if not a.no_links:
            await links_for(fetcher, day, games, channels, book, {t for v in teams.values() for t in v})
    finally:
        await fetcher.close()

    def choose(g):
        return book.choose(g, channels.where(g, sunday_ticket=st), teams, channels, sunday_ticket=st)

    now = time.time()
    plan = P.build_plan(games, choose, my_teams=teams, day=day, now=now, leagues=leagues)
    plan = P.carry_checks(plan, P.load(day, data))
    P.expire(plan, now)
    if not a.no_write:
        path = P.save(plan, data)
        print(f"wrote {path}", file=sys.stderr)
    print(json.dumps(plan, indent=1) if a.json else P.text(plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
