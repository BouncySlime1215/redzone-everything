#!/usr/bin/env python3
"""NHL api-web play-by-play + shift charts -> the factory's compact ESPN-shaped game records (NFM tokens).

Serves: North Star "% of hindsight-oracle value achieved" via data scale (Nick Nov 4 5:00 PM); owner: lane 1.
Why: ESPN's NHL summaries carry wall clocks only from 2019-20, so NHL 2011-2019 had no tokens. The NHL's own
feed (api-web.nhle.com, public, unauthenticated, no published terms; docs/data-sources.md) has every event
of those seasons, without wall clocks: DT then comes from the game clock and the night from the game date.
Shifts (api.nhle.com shiftcharts) are kept beside each game (on-ice changes, for vocab v2), never tokenized
in v1. Runs on Kaggle only (unreachable from the cloud container); ≤ 2 requests/s, every response cached in
the season file, nights on or after factory.EVAL_FIRST_NIGHT["nhl"] never fetched.
"""
from __future__ import annotations

import gzip
import json
import time
from datetime import date, timedelta
from pathlib import Path

SCHEDULE = "https://api-web.nhle.com/v1/schedule/{d}"
PBP = "https://api-web.nhle.com/v1/gamecenter/{g}/play-by-play"
SHIFTS = "https://api.nhle.com/stats/rest/en/shiftcharts?cayenneExp=gameId={g}"
MIN_GAP_S = 0.5            # politeness: at most 2 requests per second from this job

# api-web typeDescKey -> the ESPN NHL type text the live parser reads (espn._kind / espn.stoppage_for), so a
# 2015 faceoff and a 2024 ESPN faceoff are the same KIND token.
TYPE = {"faceoff": "Faceoff", "shot-on-goal": "Shot", "missed-shot": "Missed Shot", "blocked-shot": "Blocked Shot",
        "goal": "Goal", "hit": "Hit", "giveaway": "Giveaway", "takeaway": "Takeaway", "stoppage": "Stoppage",
        "period-start": "Period Start", "period-end": "Period End", "game-end": "Game End",
        "delayed-penalty": "Delayed Penalty", "shootout-complete": "Shootout Complete", "failed-shot-attempt": "Shot"}


HYPHENATED = ("cross-checking", "high-sticking")       # ESPN keeps the hyphen in these penalty names


def _title(s: str) -> str:
    s = (s or "").lower()
    return s.title() if s in HYPHENATED else " ".join(w.capitalize() for w in s.replace("-", " ").split())


def convert(pbp: dict) -> dict | None:
    """One api-web play-by-play payload -> {id, date, season_type, matchup, plays} (ESPN play dict shape)."""
    plays, hs, as_ = [], 0, 0
    home = ((pbp.get("homeTeam") or {}).get("abbrev")) or "?"
    away = ((pbp.get("awayTeam") or {}).get("abbrev")) or "?"
    for k, p in enumerate(pbp.get("plays") or []):
        key = p.get("typeDescKey") or ""
        d = p.get("details") or {}
        if key == "penalty":
            t = _title(d.get("descKey") or "penalty")          # ESPN names the penalty ("Hooking")
        else:
            t = TYPE.get(key, _title(key))
        text = (d.get("reason") or d.get("secondaryReason") or "").replace("-", " ")
        if key == "goal":
            hs, as_ = int(d.get("homeScore", hs) or 0), int(d.get("awayScore", as_) or 0)
        plays.append({"id": str(p.get("eventId", k)), "sequenceNumber": str(p.get("sortOrder", k)),
                      "type": {"text": t}, "text": text,
                      "period": {"number": (p.get("periodDescriptor") or {}).get("number")},
                      "clock": {"displayValue": p.get("timeRemaining")}, "wallclock": None,
                      "scoringPlay": key == "goal", "homeScore": hs, "awayScore": as_})
    if len(plays) < 10:
        return None
    return {"id": str(pbp.get("id", "")), "date": str(pbp.get("gameDate", ""))[:10],
            "season_type": pbp.get("gameType"), "matchup": f"{away} @ {home}", "plays": plays}


def shifts_of(js: dict | None) -> list[list]:
    """Shift chart -> compact [team, player, period, start 'MM:SS', end 'MM:SS'] rows (vocab v2 input)."""
    return [[s.get("teamAbbrev"), s.get("playerId"), s.get("period"), s.get("startTime"), s.get("endTime")]
            for s in ((js or {}).get("data") or []) if s.get("typeCode", 517) == 517]


def season_ids(get, year: int, cut: str) -> list[tuple[str, str]]:
    """(game id, YYYYMMDD) for regular season + playoff games of season `year` (Sep year-1 .. Jul year)."""
    out, d, end = [], date(year - 1, 9, 1), date(year, 7, 15)
    while d < end:
        js = get(SCHEDULE.format(d=d.isoformat())) or {}
        for day in js.get("gameWeek") or []:
            night = (day.get("date") or "").replace("-", "")
            for g in day.get("games") or []:
                if g.get("gameType") in (2, 3) and night and night < cut and (str(g["id"]), night) not in out:
                    out.append((str(g["id"]), night))
        d += timedelta(days=7)
    return out


def fetch_season(year: int, dest: Path, max_games: int | None = None, session=None) -> bool:
    """Every game of season `year` to dest (jsonl.gz of convert() records, each with its shifts). Resumable."""
    from redzone.noggin import factory as F
    if dest.exists() and dest.stat().st_size > 0:
        return True
    if session is None:
        import requests
        session = requests.Session()
        session.headers["User-Agent"] = "redzone-noggin-factory (personal, non-commercial)"
    last = [0.0]

    def get(url):
        for k in range(4):
            time.sleep(max(0.0, MIN_GAP_S - (time.time() - last[0])))
            last[0] = time.time()
            try:
                r = session.get(url, timeout=30)
                if r.status_code == 404:
                    return None
                r.raise_for_status()
                return r.json()
            except Exception:  # noqa: BLE001 - retried with backoff, then skipped
                time.sleep(2 ** k)
        return None
    ids = season_ids(get, year, F.EVAL_FIRST_NIGHT.get("nhl", "99999999"))
    ids = ids[:max_games] if max_games else ids
    dest.parent.mkdir(parents=True, exist_ok=True)
    part, kept = dest.with_suffix(".part"), 0
    with gzip.open(part, "wt") as f:
        for gid, night in ids:
            g = convert(get(PBP.format(g=gid)) or {})
            if g is None:
                continue
            g["date"] = g["date"] or f"{night[:4]}-{night[4:6]}-{night[6:]}"
            g["shifts"] = shifts_of(get(SHIFTS.format(g=gid)))
            f.write(json.dumps(g) + "\n")
            kept += 1
    part.rename(dest)
    print(f"  nhl {year} api-web: {kept} of {len(ids)} games", flush=True)
    return kept > 0
