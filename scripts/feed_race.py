"""[feed race] Which free, public NFL / college football feed shows a score first? Shadow only.

    python scripts/feed_race.py nfl --minutes 180                 # a live NFL window, every 3 s
    python scripts/feed_race.py cfb --minutes 240 --ncaa-week 11  # Saturday: ESPN vs NCAA.com
    python scripts/feed_race.py --grade data/feed_race/2026-11-08-cfb.jsonl

Polls each candidate side by side and logs the first time each source shows each score of each game
(data/feed_race/<day>-<league>.jsonl). The grade is each candidate's lead over the app's own feed
(ESPN's site scoreboard) per score change: p50 / p10 / p90 and coverage, like the NHL source race (#58).
Nothing is merged and nothing on screen changes; it's a few small GETs every 3 s.

Candidates (all public, unauthenticated pages the sites' own web clients read, the same footing as the
ESPN polling the app already does; no logins, tokens or paid trials):
  espn_site     site.api.espn.com scoreboard (the baseline: what the app polls today)
  espn_cdn      cdn.espn.com core scoreboard (espn.com's own page data, a different cache)
  ncaa          data.ncaa.com casablanca scoreboard (college football only; needs the FBS week)
Left out on purpose: NFL.com / api.nfl.com (needs a client token pulled from the site), Fox bifrost
and CBS (keyed web APIs), Sportradar / paid feeds ($0 budget), anything behind a login.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ESPN_PATH = {"nfl": "football/nfl", "cfb": "football/college-football"}
CDN_PATH = {"nfl": "nfl", "cfb": "college-football"}
BASELINE = "espn_site"


def _norm(name: str | None) -> str:
    return re.sub(r"[^a-z]", "", (name or "").lower())


def _game(home: str, away: str) -> str:
    return f"{_norm(away)}@{_norm(home)}"


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def parse_espn(doc: dict) -> dict[str, tuple[int, int]]:
    """ESPN scoreboard (site API, or the cdn page's sbData): live games -> (away, home) score."""
    events = doc.get("events") or ((doc.get("content") or {}).get("sbData") or {}).get("events") or []
    out = {}
    for ev in events:
        comp = (ev.get("competitions") or [{}])[0]
        if ((comp.get("status") or ev.get("status") or {}).get("type") or {}).get("state") != "in":
            continue
        side = {c.get("homeAway"): c for c in comp.get("competitors") or []}
        h, a = side.get("home") or {}, side.get("away") or {}
        hs, as_ = _int(h.get("score")), _int(a.get("score"))
        if hs is None or as_ is None:
            continue
        team = lambda c: (c.get("team") or {}).get("location") or (c.get("team") or {}).get("shortDisplayName")  # noqa: E731
        out[_game(team(h), team(a))] = (as_, hs)
    return out


def parse_ncaa(doc: dict) -> dict[str, tuple[int, int]]:
    """NCAA.com casablanca scoreboard: live games -> (away, home) score."""
    out = {}
    for row in doc.get("games") or []:
        g = row.get("game") or {}
        if (g.get("gameState") or "").lower() != "live":
            continue
        h, a = g.get("home") or {}, g.get("away") or {}
        hs, as_ = _int(h.get("score")), _int(a.get("score"))
        if hs is None or as_ is None:
            continue
        out[_game((h.get("names") or {}).get("short"), (a.get("names") or {}).get("short"))] = (as_, hs)
    return out


class Race:
    """First time each source showed each (game, score)."""

    def __init__(self, log: Path | None = None):
        self.first: dict[tuple[str, str, tuple], float] = {}
        self.primed: set[tuple[str, str]] = set()
        self.log = log

    def saw(self, source: str, games: dict[str, tuple[int, int]], t: float) -> None:
        for game, score in games.items():
            k = (source, game, tuple(score))
            if k in self.first:
                continue
            # a game's first sighting is whatever it was when we started watching: no lag in it
            first = (source, game) not in self.primed
            self.primed.add((source, game))
            self.first[k] = float("nan") if first else t
            if self.log is not None and not first:
                with self.log.open("a") as f:
                    f.write(json.dumps({"t": round(t, 2), "src": source, "game": game, "score": list(score)}) + "\n")


def grade(first: dict[tuple[str, str, tuple], float], baseline: str = BASELINE) -> dict:
    """Each candidate's lead over the baseline (seconds, + = earlier) per score change both saw."""
    base = {(g, s): t for (src, g, s), t in first.items() if src == baseline and t == t}
    out = {}
    for src in sorted({k[0] for k in first} - {baseline}):
        mine = {(g, s): t for (x, g, s), t in first.items() if x == src and t == t}
        games = {g for g, _ in mine}
        leads = sorted(base[k] - mine[k] for k in base if k in mine)
        seen = [k for k in base if k[0] in games]
        n = len(leads)
        q = (lambda p: round(leads[min(n - 1, int(p * n))], 1)) if n else (lambda p: None)
        out[src] = {"paired": n, "coverage_pct": round(100 * n / len(seen), 1) if seen else None,
                    "lead_p50_s": round(statistics.median(leads), 1) if n else None,
                    "lead_p10_s": q(0.1), "lead_p90_s": q(0.9),
                    "behind_pct": round(100 * sum(x < -2 for x in leads) / n, 1) if n else None}
    return out


def grade_log(path: Path) -> dict:
    first = {}
    for line in path.read_text().splitlines():
        r = json.loads(line)
        first.setdefault((r["src"], r["game"], tuple(r["score"])), r["t"])
    return grade(first)


async def run(league: str, minutes: float, every: float, week: int | None, out: Path) -> dict:
    import httpx

    from redzone.sources.http import BROWSER_HEADERS
    urls = {"espn_site": (f"https://site.api.espn.com/apis/site/v2/sports/{ESPN_PATH[league]}/scoreboard"
                          + ("?groups=80&limit=300" if league == "cfb" else ""), parse_espn),
            "espn_cdn": (f"https://cdn.espn.com/core/{CDN_PATH[league]}/scoreboard?xhr=1"
                         + ("&groups=80" if league == "cfb" else ""), parse_espn)}
    if league == "cfb" and week is not None:
        urls["ncaa"] = (f"https://data.ncaa.com/casablanca/scoreboard/football/fbs/{datetime.now().year}/"
                        f"{week:02d}/scoreboard.json", parse_ncaa)
    race = Race(out)
    errors: dict[str, int] = {}
    end = time.time() + 60 * minutes
    async with httpx.AsyncClient(timeout=httpx.Timeout(6.0, connect=3.0), headers=BROWSER_HEADERS,
                                 follow_redirects=True) as http:
        async def one(src: str, url: str, parse) -> None:
            try:
                r = await http.get(url, headers={"Cache-Control": "no-cache"})
                if r.status_code == 200:
                    race.saw(src, parse(r.json()), time.time())
                    return
            except Exception:
                pass
            errors[src] = errors.get(src, 0) + 1
        while time.time() < end:
            t0 = time.time()
            await asyncio.gather(*(one(s, u, p) for s, (u, p) in urls.items()))
            await asyncio.sleep(max(0.0, every - (time.time() - t0)))
    v = grade(race.first)
    v["_errors"] = errors
    return v


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("league", nargs="?", choices=sorted(ESPN_PATH))
    ap.add_argument("--minutes", type=float, default=180)
    ap.add_argument("--every", type=float, default=3.0)
    ap.add_argument("--ncaa-week", type=int, default=None, help="FBS week for the NCAA.com scoreboard")
    ap.add_argument("--grade", type=Path, default=None, help="grade a log instead of polling")
    a = ap.parse_args(argv)
    if a.grade is not None:
        print(json.dumps(grade_log(a.grade), indent=1))
        return 0
    if a.league is None:
        ap.error("league or --grade")
    out = ROOT / "data" / "feed_race" / f"{datetime.now():%Y-%m-%d}-{a.league}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    print(json.dumps(asyncio.run(run(a.league, a.minutes, a.every, a.ncaa_week, out)), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
