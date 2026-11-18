#!/usr/bin/env python3
"""Data H: pre-game report backfill for every NFL game since 1999 from nflverse (CC-BY 4.0 data, green).

    python scripts/gamectx/nflverse_backfill.py --games games.csv --draft draft_picks.csv --out out [--ctx wiki_out]

Inputs are the public nflverse release files (schedules/games.csv, draft_picks/draft_picks.csv). Same schema as
pregame.py (stakes, standings, streaks, rest, division game, revenge, national TV / prime slot, rivalry, offseason),
plus fields only nflverse has, all known before kickoff:
  interim_coach   the team's coach differs from its week-1 coach this season (a firing / hot-seat outcome)
  qb_change       the starting QB differs from the team's previous game
  rookie_qb       the starting QB was drafted this season (+ rookie_qb_round / rookie_qb_pick)
  draft_r1, draft_top100   picks the team made in this season's draft (offseason draft capital)
Lines, totals, weather and moneylines are left out here: crew D owns prices, crew B owns weather.
gid = the ESPN event id when nflverse has one (the key T8 and the archive use), else the nflverse game_id.
Personal-use, not for commercial release.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pregame as P  # noqa: E402

NY = ZoneInfo("America/New_York")
TO_ESPN = {"LA": "LAR", "STL": "LAR", "WAS": "WSH", "OAK": "LV", "SD": "LAC"}


def espn_abbr(t: str) -> str:
    return TO_ESPN.get(t, t)


def div_map(season: int) -> dict:
    """2002+ divisions; 1999-2001 conference only (old divisions differ), Seattle in the AFC then."""
    if season >= 2002:
        return P.NFL_DIV
    m = {t: (c, "") for t, (c, _) in P.NFL_DIV.items() if t != "HOU"}
    m["SEA"] = ("AFC", "")
    return m


def to_event(g: dict) -> dict:
    tm = g["gametime"] or "13:00"
    t = datetime.strptime(f"{g['gameday']} {tm}", "%Y-%m-%d %H:%M").replace(tzinfo=NY).astimezone(timezone.utc)
    hs = int(g["home_score"]) if g["home_score"] not in ("", "NA") else None
    as_ = int(g["away_score"]) if g["away_score"] not in ("", "NA") else None
    hh, mm = (int(x) for x in tm.split(":"))
    sunday_day = g["weekday"] == "Sunday" and hh < 19
    nets = [] if sunday_day else ["national-slot"]
    gid = g["espn"] if g.get("espn") not in (None, "", "NA") else g["game_id"]
    return {"gid": gid, "nflverse_id": g["game_id"], "start": t.strftime("%Y-%m-%dT%H:%MZ"),
            "done": hs is not None, "season_type": 2 if g["game_type"] == "REG" else 3,
            "home": espn_abbr(g["home_team"]), "away": espn_abbr(g["away_team"]), "hs": hs, "as": as_,
            "national": nets, "div_game": int(g["div_game"] or 0), "_g": g}


def backfill(games_csv: Path, draft_csv: Path, ctx: Path | None) -> list[dict]:
    games = list(csv.DictReader(games_csv.open()))
    draft = list(csv.DictReader(draft_csv.open()))
    rookies = {(r["gsis_id"], r["season"]): (r["round"], r["pick"]) for r in draft if r.get("gsis_id")}
    cap = defaultdict(lambda: [0, 0])
    for r in draft:
        k = (espn_abbr(r["team"]), r["season"])
        cap[k][0] += int(r["round"] == "1")
        cap[k][1] += int(int(r["pick"] or 999) <= 100)
    rows = []
    for season in sorted({int(g["season"]) for g in games}):
        if season < 1999:
            continue
        evs = [to_event(g) for g in games if int(g["season"]) == season]
        book = P.Book("nfl", season_games=16 if season < 2021 else 17, playoff_line=6 if season < 2020 else 7,
                      div=div_map(season))
        out = P.report("nfl", evs, evs, ctx, "nflverse", book=book)
        first_coach, last_qb = {}, {}
        by_gid = {e["gid"]: e for e in evs}
        for r in sorted(out, key=lambda r: r["start_utc"]):
            g = by_gid[r["gid"]]["_g"]
            r["nflverse_id"] = g["game_id"]
            r["season"] = season
            r["week"] = int(g["week"])
            r["national_nets"] = ""
            for side in ("home", "away"):
                team = r[side]
                coach, qb = g[f"{side}_coach"], g[f"{side}_qb_id"]
                first_coach.setdefault(team, coach)
                r[f"{side}_rest_days"] = int(g[f"{side}_rest"]) if g[f"{side}_rest"] not in ("", "NA") else ""
                r[f"{side}_b2b"] = 0
                r[f"{side}_interim_coach"] = int(bool(coach) and coach != first_coach[team])
                r[f"{side}_qb_change"] = int(team in last_qb and bool(qb) and qb != last_qb[team])
                rk = rookies.get((qb, str(season)))
                r[f"{side}_rookie_qb"] = int(rk is not None)
                r[f"{side}_rookie_qb_round"] = rk[0] if rk else ""
                r[f"{side}_rookie_qb_pick"] = rk[1] if rk else ""
                r[f"{side}_draft_r1"], r[f"{side}_draft_top100"] = cap[(team, str(season))]
                if qb:
                    last_qb[team] = qb
            rows.append(r)
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=Path, required=True)
    ap.add_argument("--draft", type=Path, required=True)
    ap.add_argument("--ctx", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=Path("out"))
    a = ap.parse_args(argv)
    rows = backfill(a.games, a.draft, a.ctx)
    P.write(a.out / "nfl" / "pregame_report_nflverse.csv", rows)
    done = sum(1 for r in rows if r["season_type"] in (2, 3))
    print(f"nfl: {len(rows)} games 1999-{max(r['season'] for r in rows)} ({done} rows), "
          f"team-weeks {2 * len(rows)}, seasons {len({r['season'] for r in rows})}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
