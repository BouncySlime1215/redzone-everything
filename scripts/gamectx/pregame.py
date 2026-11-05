#!/usr/bin/env python3
"""Data H: the PRE-GAME REPORT, one row per game, same schema for history and for today's slate.

    python scripts/gamectx/pregame.py history --league nba --archive data/archive --ctx out --out out
    python scripts/gamectx/pregame.py today --league nba --ctx out --out out [--date YYYYMMDD]   # network
    python scripts/gamectx/pregame.py loop --ctx out --out out --hours 5.5                       # network

Input is ESPN scoreboard JSON: the archive files for history (data/archive/<league>/scoreboard_<night>.json) and the
public scoreboard endpoint for the season to date + today (Actions only, one call per range, cached). Every value is
known BEFORE the game: it is computed from games that finished earlier in the same season.

Fields (v1): record / win pct / streak / last-10 going in, home-away split, rest days and back-to-back, division and
conference game, division rank and games back, conference seed and the gap to the playoff line (NBA: 6 = playoff,
10 = play-in; NFL: 7), games left, clinched / eliminated flags (approximate: wins only, no tiebreakers), revenge
(lost the last meeting this season), national-TV broadcast and prime slot, rivalry + age and the offseason numbers
from the Wikipedia pass (wiki_context.py). Lines and public betting % come from crew D, injuries and pageviews from
crew B, and are joined downstream by (night, gid). Not yet: milestones in reach, star matchups, rookies (NBA), fan
sentiment (Reddit hook off until a terms check passes; see docs/datasources/web-text-h.md).
Numbers and codes only; no text. Personal-use, not for commercial release.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import archive_join as AJ  # noqa: E402

TAG = "personal-use, not for commercial release"
ET = timezone(timedelta(hours=-5))   # report clock; DST error of 1 h only moves the prime-slot flag edge

# ESPN abbreviations -> (conference, division)
NBA_DIV = {}
for conf, divs in {"E": {"ATL": "SE", "CHA": "SE", "MIA": "SE", "ORL": "SE", "WSH": "SE",
                         "BOS": "AT", "BKN": "AT", "NY": "AT", "PHI": "AT", "TOR": "AT",
                         "CHI": "CE", "CLE": "CE", "DET": "CE", "IND": "CE", "MIL": "CE"},
                   "W": {"DEN": "NW", "MIN": "NW", "OKC": "NW", "POR": "NW", "UTAH": "NW",
                         "GS": "PA", "LAC": "PA", "LAL": "PA", "PHX": "PA", "SAC": "PA",
                         "DAL": "SW", "HOU": "SW", "MEM": "SW", "NO": "SW", "SA": "SW"}}.items():
    for t, d in divs.items():
        NBA_DIV[t] = (conf, conf + d)
NFL_DIV = {}
for div, teams in {"AFC-E": "BUF MIA NE NYJ", "AFC-N": "BAL CIN CLE PIT", "AFC-S": "HOU IND JAX TEN",
                   "AFC-W": "DEN KC LV LAC", "NFC-E": "DAL NYG PHI WSH", "NFC-N": "CHI DET GB MIN",
                   "NFC-S": "ATL CAR NO TB", "NFC-W": "ARI LAR SF SEA"}.items():
    for t in teams.split():
        NFL_DIV[t] = (div[:3], div)
DIV = {"nba": NBA_DIV, "nfl": NFL_DIV}
SEASON_GAMES = {"nba": 82, "nfl": 17}
PLAYOFF_LINE = {"nba": 6, "nfl": 7}
PLAYIN_LINE = {"nba": 10, "nfl": 7}
# NFL Sunday-afternoon CBS / FOX games are regional even when ESPN lists them as "national": they never count.
NFL_REGIONAL = {"CBS", "FOX", "Fox", "Paramount+", "Fox One"}
SPORT = {"nba": "basketball/nba", "nfl": "football/nfl"}


def events_of(sb: dict) -> list[dict]:
    out = []
    for e in sb.get("events", []):
        c = e["competitions"][0]
        teams = {x["homeAway"]: x for x in c["competitors"]}
        if "home" not in teams or "away" not in teams:
            continue
        st = (c.get("status") or e.get("status") or {}).get("type", {})
        nets = set()
        for b in c.get("broadcasts", []) or []:
            if (b.get("market") or "national") == "national":
                nets.update(b.get("names", []))
        for b in c.get("geoBroadcasts", []) or []:
            if (b.get("market", {}) or {}).get("type") == "National":
                nets.add((b.get("media") or {}).get("shortName", ""))

        def score(x):
            try:
                return int(x.get("score"))
            except (TypeError, ValueError):
                return None
        out.append({"gid": str(e["id"]), "start": e.get("date"), "done": bool(st.get("completed")),
                    "season_type": (e.get("season") or {}).get("type", 2),
                    "home": teams["home"]["team"]["abbreviation"], "away": teams["away"]["team"]["abbreviation"],
                    "hs": score(teams["home"]), "as": score(teams["away"]), "national": sorted(n for n in nets if n)})
    return out


def _start(e):
    return datetime.fromisoformat(e["start"].replace("Z", "+00:00"))


class Book:
    """Season-to-date state per team, advanced game by game in start order."""

    def __init__(self, league: str, season_games: int | None = None, playoff_line: int | None = None,
                 div: dict | None = None):
        self.lg = league
        self.n_games = season_games or SEASON_GAMES[league]
        self.line = playoff_line or PLAYOFF_LINE[league]
        self.div = div or DIV[league]
        self.res = defaultdict(list)          # team -> [(start, 'W'/'L'/'T', home, opp)]
        self.last_meet = {}                   # (team, opp) -> 'W'/'L'

    def add(self, e):
        if not e["done"] or e["hs"] is None or e["as"] is None or e["season_type"] != 2:
            return
        h, a, t = e["home"], e["away"], _start(e)
        rh = "W" if e["hs"] > e["as"] else ("L" if e["hs"] < e["as"] else "T")
        ra = {"W": "L", "L": "W", "T": "T"}[rh]
        self.res[h].append((t, rh, 1, a))
        self.res[a].append((t, ra, 0, h))
        self.last_meet[(h, a)], self.last_meet[(a, h)] = rh, ra

    def wl(self, team):
        r = self.res[team]
        w, l_, t = (sum(1 for x in r if x[1] == k) for k in "WLT")
        return w, l_, t

    def pct(self, team):
        w, l_, t = self.wl(team)
        n = w + l_ + t
        return (w + 0.5 * t) / n if n else 0.5

    def team_side(self, team, opp, when, home):
        r = self.res[team]
        w, l_, t = self.wl(team)
        n = w + l_ + t
        streak = 0
        for x in reversed(r):
            if x[1] == "T":
                break
            s = 1 if x[1] == "W" else -1
            if streak == 0 or (streak > 0) == (s > 0):
                streak += s
            else:
                break
        last10 = sum(1 for x in r[-10:] if x[1] == "W")
        split = [x for x in r if x[2] == home]
        rest = (when - r[-1][0]).total_seconds() / 86400 if r else None
        conf, div = self.div.get(team, ("", ""))
        teams = list(self.div)
        div_teams = sorted((k for k in teams if div and self.div[k][1] == div), key=self.pct, reverse=True)
        conf_teams = sorted((k for k in teams if self.div[k][0] == conf), key=self.pct, reverse=True)

        def wins(k):
            ww, _, tt = self.wl(k)
            return ww + 0.5 * tt

        def gb(k, ref):
            (w1, l1, _), (w2, l2, _) = self.wl(ref), self.wl(k)
            return ((w1 - w2) + (l2 - l1)) / 2
        seed = conf_teams.index(team) + 1 if team in conf_teams else None
        left = self.n_games - n
        pl = self.line
        line_team = conf_teams[pl - 1] if len(conf_teams) >= pl else None
        first_out = conf_teams[pl] if len(conf_teams) > pl else None
        max_w = wins(team) + left
        elim = int(line_team is not None and team not in conf_teams[:pl] and max_w < wins(line_team))
        clinch = int(first_out is not None and wins(team) > wins(first_out) + (self.n_games - sum(self.wl(first_out))))
        return {"w": w, "l": l_, "t": t, "win_pct": round(self.pct(team), 4) if n else "", "streak": streak,
                "last10_w": last10 if n else "", "split_pct": round(sum(1 for x in split if x[1] == "W") / len(split), 4)
                if split else "", "rest_days": round(rest, 2) if rest is not None else "",
                "b2b": int(rest is not None and rest < 1.6), "div_rank": div_teams.index(team) + 1 if team in div_teams else "",
                "div_gb": gb(team, div_teams[0]) if div_teams else "", "conf_seed": seed or "",
                "gap_playoff_line": gb(team, line_team) if line_team else "",
                "gap_playin_line": gb(team, conf_teams[max(pl, PLAYIN_LINE[self.lg]) - 1])
                if len(conf_teams) >= max(pl, PLAYIN_LINE[self.lg]) else "", "games_left": left, "clinched_approx": clinch,
                "eliminated_approx": elim, "revenge": int(self.last_meet.get((team, opp)) == "L")}


def national(league: str, nets: list[str]) -> list[str]:
    return [n for n in nets if not (league == "nfl" and n in NFL_REGIONAL)]


def report(league: str, season_events: list[dict], targets: list[dict], ctx: Path | None, built: str,
           book: Book | None = None) -> list[dict]:
    """season_events: every event of the season so far (any order); targets: the games to report on."""
    book = book or Book(league)
    evs = sorted(season_events, key=_start)
    tg = sorted(targets, key=_start)
    games, weeks, riv = AJ.load(ctx, league) if ctx else ({}, {}, {})
    m = AJ.ESPN[league]
    rows, i = [], 0
    for e in tg:
        t = _start(e)
        while i < len(evs) and _start(evs[i]) < t - timedelta(hours=3):
            book.add(evs[i])
            i += 1
        h, a = e["home"], e["away"]
        loc = t.astimezone(ET)
        night = loc.strftime("%Y%m%d")
        hc, ac = DIV[league].get(h, ("", "")), DIV[league].get(a, ("", ""))
        r = riv.get(tuple(sorted((m.get(h, h), m.get(a, a)))))
        nets = national(league, e["national"])
        hc, ac = book.div.get(h, ("", "")), book.div.get(a, ("", ""))
        dg = e.get("div_game")
        row = {"league": league, "night": night, "gid": e["gid"], "home": h, "away": a, "start_utc": e["start"],
               "built_utc": built, "season_type": e["season_type"],
               "div_game": int(dg) if dg is not None else int(bool(hc[1]) and hc[1] == ac[1]),
               "conf_game": int(bool(hc[0]) and hc[0] == ac[0]), "national_tv": int(bool(nets)),
               "national_nets": "|".join(nets), "prime_slot": int(bool(nets) and
                                                                         loc.hour * 60 + loc.minute >= 19 * 60 + 30),
               "rivalry": int(r is not None)}
        for side, team, opp, home in (("home", h, a, 1), ("away", a, h, 0)):
            for k, v in book.team_side(team, opp, t, home).items():
                row[f"{side}_{k}"] = v
            g = AJ.find(games, m.get(team, team), m.get(opp, opp), night) if games else None
            for k, v in AJ.side(side, g, weeks).items():
                if any(k.endswith(c) for c in AJ.OFF_COLS):
                    row[k] = v
        rows.append(row)
    return rows


def write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def now_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _season_of(league: str, e) -> int:
    t = _start(e).astimezone(ET)
    return t.year if t.month >= (10 if league == "nba" else 8) else t.year - 1


def history(league: str, archive: Path, ctx: Path | None, out: Path, full: bool = False) -> int:
    """Targets = every archive game. Season state = the archive alone, or (full=True, network) the whole season
    to date from the public scoreboard, which the archive's sparse nights can't give (NFL 2026 has a few nights)."""
    evs, seen = [], set()
    for f in sorted(glob.glob(str(archive / league / "scoreboard_*.json"))):
        for e in events_of(json.load(open(f))):
            if e["gid"] not in seen:
                seen.add(e["gid"])
                evs.append(e)
    by_season = defaultdict(list)
    for e in evs:
        by_season[_season_of(league, e)].append(e)
    rows = []
    for season, tg in sorted(by_season.items()):
        state = tg
        if full:
            last = max(_start(e) for e in tg).astimezone(ET)
            state = fetch_range(league, datetime.strptime(season_start(league, last), "%Y%m%d"), last)
        rows += report(league, state, tg, ctx, "history-full" if full else "history-archive")
    write(out / league / "pregame_report_history.csv", rows)
    print(f"{league}: {len(rows)} history rows ({'full season state' if full else 'archive-only state'})")
    return len(rows)


# ---- network (Actions only) ----------------------------------------------------------------------------------
def fetch(league: str, d0: str, d1: str) -> dict:
    url = (f"https://site.api.espn.com/apis/site/v2/sports/{SPORT[league]}/scoreboard?dates={d0}-{d1}&limit=1000")
    for k in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "redzone-gamectx/0.1"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except Exception as e:  # noqa: BLE001
            print(f"  retry {k}: {type(e).__name__}", file=sys.stderr)
            time.sleep(2 ** k * 5)
    return {}


def fetch_range(league: str, d0: datetime, d1: datetime) -> list[dict]:
    """Season events in <= 28-day windows (the endpoint caps a call at 1000 events), de-duplicated."""
    out, seen, a = [], set(), d0
    while a <= d1:
        b = min(a + timedelta(days=27), d1)
        for e in events_of(fetch(league, a.strftime("%Y%m%d"), b.strftime("%Y%m%d"))):
            if e["gid"] not in seen:
                seen.add(e["gid"])
                out.append(e)
        a = b + timedelta(days=1)
        time.sleep(1)
    return out


def season_start(league: str, day: datetime) -> str:
    y = day.year if day.month >= (10 if league == "nba" else 8) else day.year - 1
    return f"{y}1001" if league == "nba" else f"{y}0901"


def today(league: str, ctx: Path | None, out: Path, day: str | None = None) -> list[dict]:
    d = datetime.strptime(day, "%Y%m%d") if day else datetime.now(ET)
    ds = d.strftime("%Y%m%d")
    evs = fetch_range(league, datetime.strptime(season_start(league, d), "%Y%m%d"), d)
    tg = [e for e in evs if _start(e).astimezone(ET).strftime("%Y%m%d") == ds]
    rows = report(league, evs, tg, ctx, now_utc())
    if rows:
        write(out / league / "daily" / f"{ds}.csv", rows)
        write(out / league / "latest.csv", rows)
    print(f"{league} {ds}: {len(rows)} games")
    return rows


def loop(ctx: Path | None, out: Path, hours: float, push_cmd: str | None) -> None:
    """Build both leagues now, then rebuild a league whenever one of its games is 45-75 min from start."""
    end = time.time() + hours * 3600
    done = set()
    starts = {}
    for lg in ("nba", "nfl"):
        rows = today(lg, ctx, out)
        starts[lg] = [(r["gid"], datetime.fromisoformat(r["start_utc"].replace("Z", "+00:00"))) for r in rows]
    if push_cmd:
        os.system(push_cmd)
    while time.time() < end:
        now = datetime.now(timezone.utc)
        due = {lg for lg, ss in starts.items() for gid, t in ss
               if gid not in done and timedelta(minutes=45) <= t - now <= timedelta(minutes=75)}
        for lg in sorted(due):
            today(lg, ctx, out)
            done.update(gid for gid, t in starts[lg] if timedelta(minutes=45) <= t - now <= timedelta(minutes=75))
        if due and push_cmd:
            os.system(push_cmd)
        if all(t - now < timedelta(minutes=45) for ss in starts.values() for _, t in ss):
            break
        time.sleep(300)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["history", "today", "loop"])
    ap.add_argument("--league", choices=["nba", "nfl"])
    ap.add_argument("--archive", type=Path, default=Path("data/archive"))
    ap.add_argument("--ctx", type=Path, default=None, help="wiki_context.py output dir (optional)")
    ap.add_argument("--out", type=Path, default=Path("out"))
    ap.add_argument("--date")
    ap.add_argument("--hours", type=float, default=5.5)
    ap.add_argument("--push-cmd", default=None)
    ap.add_argument("--full", action="store_true", help="history: season state from the public scoreboard (network)")
    a = ap.parse_args(argv)
    leagues = [a.league] if a.league else ["nba", "nfl"]
    if a.cmd == "history":
        for lg in leagues:
            history(lg, a.archive, a.ctx, a.out, a.full)
    elif a.cmd == "today":
        for lg in leagues:
            today(lg, a.ctx, a.out, a.date)
    else:
        loop(a.ctx, a.out, a.hours, a.push_cmd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
