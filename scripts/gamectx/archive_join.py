#!/usr/bin/env python3
"""Data H: join the game-context numbers to our archive's games -> one PREGAME row per (night, gid).

    python scripts/gamectx/archive_join.py --ctx out --archive data/archive --out out

For T8's stay/leave model and Game Importance. Key: gid = the ESPN event id in data/archive
(scoreboard_<night>.json / summary_<gid>.json), night = YYYYMMDD of the scoreboard file.
PREGAME ONLY: every value is known before tip / kick:
  - record, win pct and streak going IN (computed from earlier games of the team-season table);
  - narrative counts from write-ups of EARLIER games (narr_to_date_*);
  - offseason signings / draft counts and coach change (week 0). Trades are left out: the trades section of a
    season article also lists in-season deadline deals, which could postdate the game;
  - rivalry: exists + age in years (first meeting); meeting totals and article size are left out (the snapshot
    revision postdates the archive nights).
Nothing suffixed _postgame, no result, no margin. Output: out/<league>/pregame_archive.csv. Numbers only.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
from datetime import date, timedelta
from pathlib import Path

# ESPN abbreviations -> the franchise keys wiki_context.py writes
ESPN = {"nba": {"GS": "GSW", "NO": "NOP", "NY": "NYK", "SA": "SAS", "UTAH": "UTA", "WSH": "WAS", "PHO": "PHX"},
        "nfl": {"WSH": "WAS", "LAR": "LA", "JAC": "JAX"}}
TEAM_COLS = ["game_no", "week", "postseason", "record_in_w", "record_in_l", "record_in_t", "win_pct_in", "streak_in"]
OFF_COLS = ["coach_change", "off_signings", "off_departures", "off_draft", "off_tag_injury", "off_words"]


def _num(v):
    if v in (None, ""):
        return ""
    try:
        f = float(v)
        return int(f) if f.is_integer() else f
    except ValueError:
        return ""


def load(ctx: Path, league: str):
    d = ctx / league

    def rows(name):
        p = d / name
        return list(csv.DictReader(p.open())) if p.exists() else []
    games = {}
    for g in rows("games.csv"):
        games.setdefault((g["team"], g["opp"]), []).append(g)
    weeks = {}
    for w in rows("team_week.csv"):
        weeks[(w["team"], w["season"], w["week"])] = w
    riv = {}
    for r in rows("rivalries.csv"):
        riv[tuple(sorted((r["team_a"], r["team_b"])))] = r
    return games, weeks, riv


def find(games: dict, team: str, opp: str, night: str):
    d0 = date(int(night[:4]), int(night[4:6]), int(night[6:]))
    ok = {(d0 + timedelta(days=k)).isoformat() for k in (-1, 0, 1)}
    for g in games.get((team, opp), []):
        if g["date"] in ok:
            return g
    return None


def side(prefix: str, g: dict | None, weeks: dict) -> dict:
    out = {f"{prefix}_{c}": (_num(g[c]) if g else "") for c in TEAM_COLS}
    w0 = weeks.get((g["team"], g["season"], "0")) if g else None
    out.update({f"{prefix}_{c}": (_num(w0.get(c)) if w0 else "") for c in OFF_COLS})
    wk = weeks.get((g["team"], g["season"], g["week"])) if g and g.get("week") else None
    for k, v in (wk or {}).items():
        if k.startswith("narr_to_date_"):
            out[f"{prefix}_{k}"] = _num(v)
    return out


def join(ctx: Path, archive: Path, league: str) -> list[dict]:
    games, weeks, riv = load(ctx, league)
    m = ESPN[league]
    out = []
    for f in sorted(glob.glob(str(archive / league / "scoreboard_*.json"))):
        night = os.path.basename(f)[len("scoreboard_"):-5]
        for e in json.load(open(f)).get("events", []):
            comp = e["competitions"][0]["competitors"]
            ab = {c["homeAway"]: m.get(c["team"]["abbreviation"], c["team"]["abbreviation"]) for c in comp}
            if "home" not in ab or "away" not in ab:
                continue
            h, a = ab["home"], ab["away"]
            gh, ga = find(games, h, a, night), find(games, a, h, night)
            r = riv.get(tuple(sorted((h, a))))
            season = int((gh or ga or {}).get("season") or 0)
            fy = _num(r.get("first_meeting_year")) if r else ""
            row = {"league": league, "night": night, "gid": e["id"], "home": h, "away": a,
                   "ctx_found_home": int(gh is not None), "ctx_found_away": int(ga is not None),
                   "rivalry": int(r is not None), "rivalry_age_y": (season - fy) if (r and fy and season) else "",
                   **side("home", gh, weeks), **side("away", ga, weeks)}
            out.append(row)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ctx", type=Path, default=Path("out"))
    ap.add_argument("--archive", type=Path, default=Path("data/archive"))
    ap.add_argument("--out", type=Path, default=Path("out"))
    a = ap.parse_args(argv)
    for lg in ("nba", "nfl"):
        rows = join(a.ctx, a.archive, lg)
        if not rows:
            continue
        p = a.out / lg / "pregame_archive.csv"
        p.parent.mkdir(parents=True, exist_ok=True)
        keys = list(dict.fromkeys(k for r in rows for k in r))
        with p.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=keys)
            w.writeheader()
            w.writerows(rows)
        found = sum(1 for r in rows if r["ctx_found_home"] and r["ctx_found_away"])
        nights = sorted({r["night"] for r in rows})
        print(f"{lg}: {len(rows)} archive games, {found} with context on both sides, nights {nights[0]}..{nights[-1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
