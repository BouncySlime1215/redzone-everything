#!/usr/bin/env python3
"""Backfill the archive for eval-data-v2 (cycle 01 plan, task 4 / EVAL2): at least 8 LOCKED nights in
every league, and more your-team nights in the dev (train + valid) slice.

    python scripts/backfill_eval_v2.py --check            # print the split each league would get, no network
    python scripts/backfill_eval_v2.py                    # download, then print the split (exit 1 if < 8 locked)

Why it is needed: eval-data-v1 has 13 NBA and 14 NHL nights, so the newest 25 % (the locked set,
redzone/lab/splits.py) is only 4 nights each, and the release gate needs >= 8.

What it adds (data/archive/<league>/, the same files scripts/download_recent.py writes):
  1. NEW nights after v1's end, for every league (they are the newest, so they join the locked set).
  2. For NBA and NHL: OLDER nights from last season's regular season, only nights a your-team game
     (default_config.yaml teams, WSH) was played, newest first, until the league has enough nights
     that the newest 25 % is >= --min-locked. Those old nights land in TRAIN (time order), so nothing
     is ever tuned on a locked night, and the dev slice gains your-team games.

The locked set stays the newest nights of the current season. Runs on a free GitHub runner
(eval-data-v2.yml), never on the Mac, and never in a cloud session (ESPN is blocked there).
Why: Backfills eval-data-v2 to at least 8 locked nights per league (docstring, grader v21 commits).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from redzone.lab import splits  # noqa: E402

ARCH = ROOT / "data" / "archive"
LEAGUES = ("nfl", "nba", "nhl")
# Last season's regular-season stretch to draw your-team nights from (newest first). It ends BEFORE the
# Noggin's locked start (agent/distill/split.py BIG_CUTS: nba/nhl locked from 20260201), so an old night
# that lands in this pin's dev (tunable) slice is never one of the Noggin's locked nights. v2 drew from
# Jan 1 - Apr 16 2026: its Feb-Apr nights were Noggin-locked (tests/test_eval_data_v2.py guards this now).
# EXP-735: with --season, nights before this are dev only (the new pin carries it as dev_before).
SEASON_DEV_BEFORE = {"nba": "20261001", "nhl": "20261001"}
OLD_SEASON = {"nba": ("20251021", "20260131"), "nhl": ("20251007", "20260131")}
WINDOW_START = {"nfl": "20250905", "nba": "20251021", "nhl": "20251007"}   # eval-data-v2.yml --window starts


def _d(s: str) -> date:
    return datetime.strptime(s, "%Y%m%d").date()


def my_teams() -> dict[str, set[str]]:
    import yaml
    cfg = yaml.safe_load((ROOT / "redzone" / "default_config.yaml").read_text()) or {}
    return {lg: {str(t).upper() for t in v or []} for lg, v in (cfg.get("teams") or {}).items()}


def needed_nights(min_locked: int) -> int:
    """Smallest night count whose newest TEST_FRAC is >= min_locked (the rule in splits.split_nights)."""
    n = min_locked
    while math.ceil(n * splits.TEST_FRAC - 1e-9) < min_locked:
        n += 1
    return n


def max_nights_clean(locked_ok: int) -> int:
    """Largest night count whose newest TEST_FRAC is <= locked_ok nights, so the locked set never
    reaches back past the nights allowed to be locked."""
    n = locked_ok
    while math.ceil((n + 1) * splits.TEST_FRAC - 1e-9) <= locked_ok:
        n += 1
    return n


def default_locked(league: str) -> tuple[str, ...]:
    """The default pin's (eval-data.lock.json) locked nights for this league."""
    from redzone.lab import evaldata
    lock = evaldata.load_lock(ROOT / "eval-data.lock.json")
    w = (lock or {}).get("window", {}).get(league)
    if not w:
        return ()
    an = splits.archive_nights(ARCH / league)
    return splits.split_nights([n for n in an if w["start"] <= n <= w["end"]]).test


def previous_locked_start(league: str) -> str | None:
    """First LOCKED night of the default pin (eval-data.lock.json) for this league: everything older
    was that pin's dev (tunable) data and must never become locked in a new pin."""
    from redzone.lab import evaldata
    lock = evaldata.load_lock(ROOT / "eval-data.lock.json")
    w = (lock or {}).get("window", {}).get(league)
    if not w:
        return None
    an = splits.archive_nights(ARCH / league)
    s = splits.split_nights([n for n in an if w["start"] <= n <= w["end"]])
    return s.test[0] if s.test else None


def split_report(league: str, end: str | None = None, dev_before: str | None = None) -> dict:
    an = splits.archive_nights(ARCH / league, end)
    s = splits.split_nights(an, dev_before=dev_before) if dev_before else splits.league_split(league, an)
    mine = my_teams().get(league, set())

    def yt(nights):
        n = 0
        for night in nights:
            sb = json.loads((ARCH / league / f"scoreboard_{night}.json").read_text())
            n += sum(1 for e in sb.get("events", []) if mine & _abbrs(e))
        return n
    return {"nights": len(an), "train": len(s.train), "valid": len(s.valid), "locked": len(s.test),
            "locked_from": s.test[0] if s.test else None, "games": sum(len(v) for v in an.values()),
            "your_team_games_dev": yt(s.train + s.valid), "your_team_games_locked": yt(s.test),
            "leaks": splits.leaks(s, an), "clean_after": previous_locked_start(league),
            "locked_were_dev": sorted(n for n in s.test if previous_locked_start(league)
                                      and n < previous_locked_start(league))}


def _abbrs(event: dict) -> set[str]:
    out = set()
    for c in event.get("competitions") or []:
        for t in c.get("competitors") or []:
            a = ((t.get("team") or {}).get("abbreviation") or "").upper()
            if a:
                out.add(a)
    return out


def _final_your_team(client, league: str, day: date, mine: set[str]) -> bool:
    import download_recent as dr
    base = f"https://site.api.espn.com/apis/site/v2/sports/{dr.PATHS[league]}"
    sb = dr.fetch_json(client, f"{base}/scoreboard", {"dates": day.strftime("%Y%m%d")})
    return any(mine & _abbrs(e) and dr.event_date(e) == day
               and ((e.get("status") or {}).get("type") or {}).get("state") == "post"
               for e in sb.get("events") or [])


def season_all(client, lg: str, start: str, stop: str, log: list[dict]) -> None:
    """[EXP-735] Every final game of an old season stretch, all teams, as DEV nights (the pin's dev_before keeps
    them out of the locked block). Nights already archived are skipped, so a re-run resumes."""
    import download_recent as dr
    have = set(splits.archive_nights(ARCH / lg))
    day = _d(start)
    while day <= _d(stop):
        key = day.strftime("%Y%m%d")
        if key not in have:
            try:
                m = dr.download_day(client, lg, day, ARCH / lg)
                log.append({"league": lg, "date": key, "kind": "season", "status": m["status"],
                            "events": len(m["events"]), "errors": m["errors"][:3]})
            except Exception as e:   # one bad day never stops the backfill
                log.append({"league": lg, "date": key, "kind": "season", "status": "error", "errors": [str(e)]})
            time.sleep(0.25)
        day += timedelta(days=1)


def backfill(new_end: str, min_locked: int, extra: int, season: tuple[str, ...] = ()) -> list[dict]:
    import httpx
    import download_recent as dr
    log = []
    teams = my_teams()
    with httpx.Client(timeout=30, headers={"User-Agent": "RedZoneEverything/1.0 (public archive replay)"}) as client:
        for lg in LEAGUES:
            have = splits.archive_nights(ARCH / lg)
            last = max(have) if have else new_end
            day = _d(last) + timedelta(days=1)
            while day <= _d(new_end):                    # 1. newest nights after v1
                m = dr.download_day(client, lg, day, ARCH / lg)
                log.append({"league": lg, "date": m["date"], "kind": "new", "status": m["status"],
                            "events": len(m["events"]), "errors": m["errors"][:3]})
                day += timedelta(days=1)
            if lg in season:                              # EXP-735: the whole old stretch, as dev
                season_all(client, lg, *OLD_SEASON[lg], log)
                continue
            if lg not in OLD_SEASON:
                continue
            # Old nights only up to the count that keeps every locked night at or after the default
            # pin's locked start (its older nights were dev: tuned on). Short of fresh nights -> NOT READY.
            clean = previous_locked_start(lg) or "0"
            ok_locked = sum(1 for n in splits.archive_nights(ARCH / lg) if n >= clean)
            want = min(needed_nights(min_locked) + extra, max_nights_clean(ok_locked))
            start, stop = (_d(x) for x in OLD_SEASON[lg])
            day = stop
            while len(splits.archive_nights(ARCH / lg)) < want and day >= start:   # 2. old your-team nights
                key = day.strftime("%Y%m%d")
                try:
                    hit = _final_your_team(client, lg, day, teams.get(lg, set()))
                except Exception as e:   # one bad day never stops the backfill
                    log.append({"league": lg, "date": key, "kind": "old", "status": "error", "errors": [str(e)]})
                    hit = False
                if hit:
                    m = dr.download_day(client, lg, day, ARCH / lg)
                    log.append({"league": lg, "date": key, "kind": "old", "status": m["status"],
                                "events": len(m["events"]), "errors": m["errors"][:3]})
                time.sleep(0.25)
                day -= timedelta(days=1)
    return log


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--end", default="20261103", help="last night to add (YYYYMMDD, Eastern)")
    ap.add_argument("--min-locked", type=int, default=8)
    ap.add_argument("--extra", type=int, default=4, help="old nights beyond the minimum (margin for empty nights)")
    ap.add_argument("--season", nargs="*", default=[], choices=sorted(OLD_SEASON),
                    help="EXP-735: add the WHOLE old-season stretch (all games) as dev nights; needs the pin's "
                         "dev_before for that league (build_eval_data.py --dev-before)")
    ap.add_argument("--check", action="store_true", help="no network: only report the split")
    ap.add_argument("--json", type=Path, help="also write the report here")
    a = ap.parse_args(argv)
    log = [] if a.check else backfill(a.end, a.min_locked, a.extra, tuple(a.season))
    rep = {lg: split_report(lg, a.end, SEASON_DEV_BEFORE.get(lg) if lg in a.season else None)
           for lg in LEAGUES if (ARCH / lg).exists()}
    out = {"end": a.end, "min_locked": a.min_locked, "split": rep, "download_log": log}
    if a.json:
        a.json.write_text(json.dumps(out, indent=1))
    for lg, r in rep.items():
        print(f"{lg}: {r['nights']} nights = {r['train']} train / {r['valid']} valid / {r['locked']} locked "
              f"(from {r['locked_from']}), {r['games']} games; your-team games dev {r['your_team_games_dev']}, "
              f"locked {r['your_team_games_locked']}; leaks: {r['leaks'] or 'none'}; "
              f"locked nights that were dev in the default pin: {r['locked_were_dev'] or 'none'}")
    errors = [x for x in log if x.get("errors")]
    if errors:
        print(f"{len(errors)} day(s) with download errors (see --json)")
    # A --season league publishes once its locked set is no smaller than the default pin's (EXP-735: the point is a
    # bigger DEV set; locked grows only with fresh nights, and the release gate keeps its own >= 8-night floor, so
    # a thin locked set reads PENDING there, never PASS). Every other league keeps the >= --min-locked bar.
    floor = {lg: min(a.min_locked, len(default_locked(lg))) if lg in a.season else a.min_locked for lg in rep}
    short = [lg for lg, r in rep.items() if r["locked"] < floor[lg] or r["leaks"] or r["locked_were_dev"]]
    if short:
        print("NOT READY:", ", ".join(short))
    return 1 if short else 0


if __name__ == "__main__":
    sys.exit(main())
