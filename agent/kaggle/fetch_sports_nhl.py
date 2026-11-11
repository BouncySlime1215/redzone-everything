"""Fetch NHL play-by-play (api-web.nhle.com) or shift charts (api.nhle.com/stats) for every game, as parquet shards.

    python agent/kaggle/fetch_sports_nhl.py --kind plays|shifts [--first-season 2010] [--last-season 2025] [--min-interval 1.0]

Runs in a Kaggle notebook; no secret, no login. Season N means the season that starts in year N. One public JSON call per
game (~320 plays, ~750 shifts), flattened to rows with night, source_night_id (nhl:<night>) and the tag
"personal-use, not for commercial release"; games on or after the locked night are never requested. Polite: >= 1 s between
calls, robots.txt read first. Resumable per month. STOPS (exit 2) when the host's robots.txt carries Content-Signal
ai-train=no, which api-web.nhle.com did on 2026-11-09: see docs/agents/data/2026-11-09-sports-sizing.md. No flag overrides it.
Why: data job 14: NHL has no play-by-play archive and no shift data in the factory; shifts say who is on the ice at each dead puck.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent.kaggle import data_fetch as df  # noqa: E402
from agent.kaggle import sports_common as sc  # noqa: E402

WEB = "https://api-web.nhle.com/v1"
STATS = "https://api.nhle.com/stats/rest/en/shiftcharts?cayenneExp=gameId="
LICENSE = "Unofficial public NHL endpoints, no licence stated; personal/non-commercial use only (NHL terms)"
LICENSE_URL = "https://github.com/Zmalski/NHL-API-Reference"
DETAIL_KEYS = ("eventOwnerTeamId", "xCoord", "yCoord", "zoneCode", "shotType", "reason", "secondaryReason", "typeCode",
               "descKey", "duration", "committedByPlayerId", "drawnByPlayerId", "shootingPlayerId", "goalieInNetId",
               "scoringPlayerId", "assist1PlayerId", "assist2PlayerId", "hittingPlayerId", "hitteePlayerId",
               "winningPlayerId", "losingPlayerId", "blockingPlayerId", "playerId", "awayScore", "homeScore",
               "awaySOG", "homeSOG")
PLAYS_COLS = ["game_id", "season", "game_type", "start_time_utc", "night", "home_team_id", "away_team_id", "event_id",
              "sort_order", "period", "period_type", "time_in_period", "time_remaining", "situation_code",
              "home_defending_side", "type_code", "type_desc"] + ["d_" + k for k in DETAIL_KEYS] + ["details_json"]
SHIFT_FIELDS = ("id", "detailCode", "duration", "endTime", "eventDescription", "eventNumber", "firstName", "lastName",
                "period", "playerId", "shiftNumber", "startTime", "teamAbbrev", "teamId", "teamName", "typeCode")
SHIFTS_COLS = ["game_id", "night", "season"] + [f for f in SHIFT_FIELDS] + ["eventDetails_json"]


def flatten_plays(url: str, p) -> list[dict]:
    fp = sc.frag_params(url)
    out = []
    for e in (p or {}).get("plays", []):
        d = e.get("details") or {}
        row = {"game_id": p.get("id"), "season": p.get("season"), "game_type": p.get("gameType"),
               "start_time_utc": p.get("startTimeUTC"), "night": fp.get("night"),
               "home_team_id": (p.get("homeTeam") or {}).get("id"), "away_team_id": (p.get("awayTeam") or {}).get("id"),
               "event_id": e.get("eventId"), "sort_order": e.get("sortOrder"),
               "period": (e.get("periodDescriptor") or {}).get("number"),
               "period_type": (e.get("periodDescriptor") or {}).get("periodType"),
               "time_in_period": e.get("timeInPeriod"), "time_remaining": e.get("timeRemaining"),
               "situation_code": e.get("situationCode"), "home_defending_side": e.get("homeTeamDefendingSide"),
               "type_code": e.get("typeCode"), "type_desc": e.get("typeDescKey")}
        for k in DETAIL_KEYS:
            row["d_" + k] = d.get(k)
        rest = {k: v for k, v in d.items() if k not in DETAIL_KEYS}
        row["details_json"] = json.dumps(rest, sort_keys=True) if rest else ""
        out.append(row)
    return out


def flatten_shifts(url: str, p) -> list[dict]:
    fp = sc.frag_params(url)
    out = []
    for s in (p or {}).get("data", []):
        row = {"game_id": s.get("gameId"), "night": fp.get("night"), "season": fp.get("season")}
        row.update({f: s.get(f) for f in SHIFT_FIELDS})
        ed = s.get("eventDetails")
        row["eventDetails_json"] = json.dumps(ed, sort_keys=True) if ed else ""
        out.append(row)
    return out


def season_games(f, season: int) -> list[dict]:
    """Regular-season and playoff games of one season that are over and not on a locked night, from the weekly schedule."""
    games, seen, d = [], set(), f"{season}-09-20"
    while d and d <= f"{season + 1}-07-15":
        wk = f.get_json(f"{WEB}/schedule/{d}")
        for day in wk.get("gameWeek", []):
            for g in day.get("games", []):
                night = sc.night_of(g.get("startTimeUTC"))
                if (g.get("gameType") in (2, 3) and g.get("gameState") in ("OFF", "FINAL") and g["id"] not in seen
                        and not sc.is_locked("nhl", night)):
                    seen.add(g["id"])
                    games.append({"id": g["id"], "night": night})
        nxt = wk.get("nextStartDate")
        d = nxt if nxt and nxt > d else None
    return games


def make_spec(kind: str) -> df.SourceSpec:
    cols = PLAYS_COLS if kind == "plays" else SHIFTS_COLS

    def items(f, a) -> list[df.Item]:
        out = []
        for season in range(a.last_season, a.first_season - 1, -1):
            by_month: dict[str, list[str]] = {}
            for g in sorted(season_games(f, season), key=lambda g: g["id"]):
                base = f"{WEB}/gamecenter/{g['id']}/play-by-play" if kind == "plays" else f"{STATS}{g['id']}"
                by_month.setdefault(g["night"][:6], []).append(base + sc.fragment(night=g["night"], season=season))
            out += [df.Item(f"nhl_{kind}-{m}", urls) for m, urls in sorted(by_month.items(), reverse=True)]
        return out

    return df.SourceSpec(
        name=f"sports_nhl_{kind}", license_text=LICENSE, license_url=LICENSE_URL, all_strings=True,
        sampling_rule=f"none (every finished regular-season and playoff game); games on or after {sc.EVAL_FIRST_NIGHT['nhl']} never requested",
        list_items=items, row_filter=sc.lock_filter("nhl", "nhl_" + kind, LICENSE),
        add_args=lambda ap: (sc.season_args(ap, 2010, 2025), ap.add_argument("--kind", choices=("plays", "shifts"), default=kind)),
        manifest_extra={"tag": sc.PERSONAL_TAG, "plan": "B", "first_locked_night": sc.EVAL_FIRST_NIGHT["nhl"], "kind": kind})


def main(argv: list[str] | None = None) -> int:
    import argparse
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--kind", choices=("plays", "shifts"), default="plays")
    kind = pre.parse_known_args(argv)[0].kind
    cols = PLAYS_COLS if kind == "plays" else SHIFTS_COLS
    flat = flatten_plays if kind == "plays" else flatten_shifts
    return sc.main(make_spec(kind), lambda a: sc.JsonCsvFetcher(flat, cols, min_interval=a.min_interval), argv)


if __name__ == "__main__":
    raise SystemExit(main())
