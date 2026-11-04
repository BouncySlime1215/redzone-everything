"""Noggin profiles: one named set of flags, scored TOGETHER (planner, Nov 4 2:45 AM).

Every part built on Nov 3-4 ships OFF and was scored alone, so nobody knew what the combined Noggin
scores. A profile is the whole candidate: the config overrides plus the time-shift buffer per league.
`scripts/northstar.py --profile candidate` scores it next to today's config on dev and locked nights,
and every flag decision is made on those combined numbers (dev only; locked is report-only).

    argv("candidate", "nba") -> ["--behind-live", "90", "--set", "noggin.priority.enabled=true", ...]
"""
from __future__ import annotations

PROFILES: dict[str, dict] = {
    "candidate": {
        "about": "Buffer B=90 NBA/NHL and 75 NFL, Priority on, drive-in-20 on, reels T1-T5 on, "
                 "Timing v1 NBA, NHL api-web (wsc) primary feed",
        "behind_live": {"nfl": 75, "nba": 90, "nhl": 90},
        "set": [
            "noggin.priority.enabled=true",
            "redzone_mode.drive_in_20=true",
            "replay.redzone_way=true",
            "noggin.timing.leagues=[nba]",
            "sources.nhl_primary=true",
        ],
    },
}


def names() -> list[str]:
    return sorted(PROFILES)


def argv(name: str, league: str) -> list[str]:
    """Extra scorecard arguments (scripts/eval.py / eval_sports.py) for this profile and league."""
    p = PROFILES[name]
    out = []
    b = (p.get("behind_live") or {}).get(league)
    if b is not None:
        out += ["--behind-live", str(b)]
    for kv in p.get("set") or []:
        out += ["--set", kv]
    return out
