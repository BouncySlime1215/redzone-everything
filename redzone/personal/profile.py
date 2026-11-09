"""Summarize the personal store into simple signals: hours by service, sports watch counts by team and league, active hours.

Why: decisions.md 2026-11-09 (personal data): Nick wants to see the data before the show uses it; nothing in scoring or the
decider imports this module yet, a later package connects it.

Team names are not in code (decisions 2026-11-03): league words are generic, team words come from an optional local file
data/personal/teams.json shaped {"league:ABBR": ["phrase", ...]}. Stdlib only.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from redzone.personal.store import read_all, store_path

# generic league words only; matched as lowercase substrings of title + tags
LEAGUE_WORDS = {
    "nfl": ("nfl", "redzone"), "cfb": ("college football", "ncaaf", "cfb"),
    "nba": ("nba",), "cbb": ("college basketball", "march madness", "ncaab"),
    "nhl": ("nhl", "stanley cup"), "mlb": ("mlb", "world series"),
}
DEFAULT_DUR_S = 180.0   # a play with no length (an app-open) counts as this many seconds


def load_teams(data_dir: Path) -> dict[str, list[str]]:
    try:
        raw = json.loads((Path(data_dir) / "personal" / "teams.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): [str(p).lower() for p in v if isinstance(p, str)] for k, v in raw.items() if isinstance(v, list)}


def summarize(records: list[dict], teams: dict[str, list[str]] | None = None, tz=None) -> dict:
    teams = teams or {}
    secs: Counter = Counter()
    hours: Counter = Counter()
    by_league: Counter = Counter()
    by_team: Counter = Counter()
    for r in records:
        dur = r.get("duration_s")
        secs[r["service"]] += dur if isinstance(dur, (int, float)) else DEFAULT_DUR_S
        try:
            d = datetime.fromisoformat(r["ts"].replace("Z", "+00:00"))
            hours[(d.astimezone(tz) if tz else d).hour] += 1
        except (ValueError, KeyError):
            pass
        blob = " ".join([r.get("title", ""), *r.get("tags", [])]).lower()
        for lg, words in LEAGUE_WORDS.items():
            if any(w in blob for w in words):
                by_league[lg] += 1
        for key, phrases in teams.items():
            if any(p and p in blob for p in phrases):
                by_team[key] += 1
    return {
        "events": len(records),
        "hours_by_service": {s: round(v / 3600, 2) for s, v in secs.most_common()},
        "sports_by_league": dict(by_league.most_common()),
        "sports_by_team": dict(by_team.most_common()),
        "active_hours": {h: hours[h] for h in sorted(hours)},
    }


def profile(data_dir: Path, tz=None) -> dict:
    return summarize(read_all(store_path(data_dir)), load_teams(data_dir), tz)
