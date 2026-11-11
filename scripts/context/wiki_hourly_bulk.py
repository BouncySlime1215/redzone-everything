#!/usr/bin/env python3
"""Bulk Wikipedia hourly pageviews for teams, team-season articles and players (crew B, scout id wikimedia-pageviews).

Attention labels for Watch Value (EXP-1704, Nick 11:30 AM Nov 5). Runs on a free GitHub runner, never in Claude.

    python scripts/context/wiki_hourly_bulk.py entities --leagues nba,nfl --out meta/
    python scripts/context/wiki_hourly_bulk.py run --league nba --season 2025 --entities meta/entities.csv \
        --out out/ --max-minutes 320

entities: teams from redzone/context/teams_seed.csv -> Wikidata qids (wbgetentities by enwiki title), then players
          per team from Wikidata SPARQL (P54 member of sports team, with start/end years when stated).
run:      for every UTC date with a game of that league-season (schedules/*.csv.gz), streams the Wikimedia
          "pageview_complete" daily user file (one file per day carries all 24 hourly counts per article):
              curl | lbzip2 -dc | grep -F "en.wikipedia <title> "  (tens of thousands of fixed patterns, fast)
          and keeps only our titles' hourly views. One parquet per day: out/hourly/<YYYY-MM-DD>.parquet with
          date, hour, kind (team | team_season | player), key (team abbr or player qid), views (all access
          methods, agent=user). Days already in out/ are skipped, so a re-run resumes.
Only derived counts leave the runner. Wikimedia pageviews are CC0. Spec: docs/datasources/context-buzz.md.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
SEED = ROOT_DIR / "redzone" / "context" / "teams_seed.csv"
SCHED = ROOT_DIR / "redzone" / "context" / "schedules"
UA = "redzone-data-b/1.0 (personal research; https://github.com/BouncySlime1215/redzone-everything)"
DUMP = "https://dumps.wikimedia.org/other/pageview_complete/{y}/{y}-{m}/pageviews-{y}{m}{d}-user.bz2"
HOUR_RE = re.compile(rb"([A-X])(\d+)")
ENT_COLS = ["league", "kind", "key", "team", "title", "start", "end"]
SEASON_FMT = {"nba": "{s}–{s2:02d}_{name}_season", "nhl": "{s}–{s2:02d}_{name}_season",
              "nfl": "{s}_{name}_season", "mlb": "{s}_{name}_season"}


def get_json(url: str, tries: int = 5):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001 (network; retried, then raised)
            if i == tries - 1:
                raise
            print(f"retry {i + 1}: {e}", file=sys.stderr)
            time.sleep(2 ** i * 10)


def seed_teams(leagues) -> list[dict]:
    with open(SEED, newline="") as f:
        return [r for r in csv.DictReader(f) if r["league"] in leagues]


def team_qids(teams) -> dict[str, str]:
    out = {}
    titles = [t["enwiki"] for t in teams]
    for i in range(0, len(titles), 50):
        q = urllib.parse.urlencode({"action": "wbgetentities", "sites": "enwiki", "titles": "|".join(titles[i:i + 50]),
                                    "props": "sitelinks", "sitefilter": "enwiki", "format": "json"})
        for qid, e in get_json(f"https://www.wikidata.org/w/api.php?{q}").get("entities", {}).items():
            t = e.get("sitelinks", {}).get("enwiki", {}).get("title")
            if t and qid.startswith("Q"):
                out[t.replace(" ", "_")] = qid
    return out


SPARQL = """SELECT ?p ?article (MIN(YEAR(?s)) AS ?start) (MAX(YEAR(?e)) AS ?end) WHERE {
  ?p p:P54 ?st . ?st ps:P54 wd:%s .
  OPTIONAL { ?st pq:P580 ?s } OPTIONAL { ?st pq:P582 ?e }
  ?article schema:about ?p ; schema:isPartOf <https://en.wikipedia.org/> .
} GROUP BY ?p ?article"""


def players(qid: str) -> list[dict]:
    q = urllib.parse.urlencode({"query": SPARQL % qid, "format": "json"})
    rows = get_json(f"https://query.wikidata.org/sparql?{q}")["results"]["bindings"]
    out = []
    for r in rows:
        title = urllib.parse.unquote(r["article"]["value"].rsplit("/wiki/", 1)[-1])
        out.append({"key": r["p"]["value"].rsplit("/", 1)[-1], "title": title,
                    "start": r.get("start", {}).get("value", ""), "end": r.get("end", {}).get("value", "")})
    return out


def cmd_entities(a):
    leagues = a.leagues.split(",")
    teams = seed_teams(leagues)
    qids = team_qids(teams)
    rows = []
    for t in teams:
        names = [t["enwiki"]] + [x for x in (t.get("former_titles") or "").split("|") if x]
        for ti in names:
            rows.append({"league": t["league"], "kind": "team", "key": t["abbr"], "team": t["abbr"], "title": ti,
                         "start": "", "end": ""})
        rows.append({"league": t["league"], "kind": "team_season", "key": t["abbr"], "team": t["abbr"],
                     "title": "{season}:" + t["name"].replace(" ", "_"), "start": "", "end": ""})
        q = qids.get(t["enwiki"])
        if not q:
            print(f"no qid for {t['enwiki']}", file=sys.stderr)
            continue
        ps = players(q)
        print(f"{t['league']} {t['abbr']} {q}: {len(ps)} players", file=sys.stderr)
        rows += [{"league": t["league"], "kind": "player", "team": t["abbr"], **p} for p in ps]
        time.sleep(1.0)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "entities.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ENT_COLS)
        w.writeheader()
        w.writerows(rows)
    print(json.dumps({"entities": len(rows), "players": sum(r["kind"] == "player" for r in rows)}))


def season_title(league: str, season: int, name: str) -> str:
    return SEASON_FMT[league].format(s=season, s2=(season + 1) % 100, name=name)


def _year(v) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def title_map(ent_path: Path, league: str, season: int) -> dict[str, tuple[str, str]]:
    """title -> (kind, key) for this league-season. Players count when their stint overlaps the season (stints
    with no dates on Wikidata count too)."""
    out = {}
    with open(ent_path, newline="") as f:
        for r in csv.DictReader(f):
            if r["league"] != league:
                continue
            t = r["title"]
            if r["kind"] == "team_season":
                t = season_title(league, season, t.split(":", 1)[1])
            elif r["kind"] == "player":
                s, e = _year(r["start"]), _year(r["end"])
                if (s and s > season + 1) or (e and e < season):
                    continue
            out[t.replace(" ", "_")] = (r["kind"], r["key"])
    return out


def parse_hits(lines, titles: dict[str, tuple[str, str]]) -> dict[tuple, int]:
    """pageview_complete lines b"en.wikipedia Title page_id access daily A3B1..." -> {(kind, key, hour): views}."""
    agg: dict[tuple, int] = defaultdict(int)
    for ln in lines:
        p = ln.rstrip(b"\n").split(b" ")
        if len(p) < 4 or p[0] != b"en.wikipedia":
            continue
        k = titles.get(p[1].decode("utf-8", "replace"))
        if not k:
            continue
        for h, v in HOUR_RE.findall(p[-1]):
            agg[(k[0], k[1], h[0] - 65)] += int(v)
    return agg


def game_days(league: str, season: int) -> list[str]:
    import pandas as pd
    g = pd.read_csv(SCHED / f"{league}.csv.gz")
    g = g[g["season"] == season]
    k = pd.to_datetime(g["start_utc"], utc=True)
    days = set(k.dt.strftime("%Y-%m-%d")) | set((k + pd.Timedelta(hours=4)).dt.strftime("%Y-%m-%d"))
    today = time.strftime("%Y-%m-%d", time.gmtime())   # the daily file appears the next day
    return sorted((d for d in days if d < today), reverse=True)   # newest first (Nick 12:10 PM)


def pull_day(day: str, titles, pat_file: Path) -> dict[tuple, int] | None:
    y, m, d = day.split("-")
    url = DUMP.format(y=y, m=m, d=d)
    cmd = (f"curl -fsSL --retry 4 --retry-delay 20 -A '{UA}' '{url}' | lbzip2 -dc "
           f"| LC_ALL=C grep -F -f '{pat_file}'")
    r = subprocess.run(["bash", "-o", "pipefail", "-c", cmd], capture_output=True)
    if r.returncode not in (0, 1):          # grep exits 1 on no match; anything else is a failed download
        print(f"{day}: pipeline failed rc={r.returncode} {r.stderr[-300:]!r}", file=sys.stderr)
        return None
    return parse_hits(r.stdout.splitlines(), titles)


def cmd_run(a):
    import pandas as pd
    titles = title_map(Path(a.entities), a.league, a.season)
    out = Path(a.out) / "hourly"
    out.mkdir(parents=True, exist_ok=True)
    pat = Path(a.out) / "patterns.txt"
    pat.write_bytes(b"".join(f"en.wikipedia {t} ".encode() + b"\n" for t in titles))
    days = [d for d in game_days(a.league, a.season) if not (out / f"{d}.parquet").exists()]
    print(f"{a.league} {a.season}: {len(titles)} titles, {len(days)} days to pull", file=sys.stderr)
    t0, done, failed, empty_streak = time.monotonic(), 0, 0, 0
    for day in days:
        if time.monotonic() - t0 > a.max_minutes * 60:
            print("time budget reached; re-run to resume", file=sys.stderr)
            break
        agg = pull_day(day, titles, pat)
        if agg is None:
            failed += 1
            continue
        empty_streak = empty_streak + 1 if not agg else 0
        if empty_streak >= 3:                 # format or title drift: stop early instead of burning the runner
            sys.exit(f"{day}: 3 days in a row with no matches; check the dump format")
        df = pd.DataFrame([(day, h, kd, k, v) for (kd, k, h), v in agg.items()],
                          columns=["date", "hour", "kind", "key", "views"])
        df = df.astype({"hour": "int8", "views": "int32"})
        df.to_parquet(out / f"{day}.parquet", index=False)
        done += 1
        print(f"{day}: {len(df)} rows, {int(df['views'].sum()) if len(df) else 0} views", file=sys.stderr)
    man = {"league": a.league, "season": a.season, "titles": len(titles), "days_pulled": done,
           "days_failed": failed, "days_total": len(game_days(a.league, a.season)),
           "days_held": len(list(out.glob("*.parquet")))}
    (Path(a.out) / "manifest.json").write_text(json.dumps(man, indent=1))
    print(json.dumps(man))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("entities")
    e.add_argument("--leagues", default="nba,nfl")
    e.add_argument("--out", required=True)
    r = sub.add_parser("run")
    r.add_argument("--league", required=True)
    r.add_argument("--season", type=int, required=True)
    r.add_argument("--entities", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--max-minutes", type=float, default=320)
    a = ap.parse_args(argv)
    (cmd_entities if a.cmd == "entities" else cmd_run)(a)


if __name__ == "__main__":
    main()
