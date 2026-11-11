#!/usr/bin/env python3
"""Build data/context/teams.csv (+ rivalries.csv) from Wikipedia + Wikidata. Runs on GitHub Actions / Kaggle.

    python scripts/context/wikidata_teams.py [--out data/context] [--leagues nfl,cfb,nba,cbb,nhl,mlb]

Pro leagues (NFL/NBA/NHL/MLB): the hand-checked seed (redzone/context/teams_seed.csv) gives abbr + enwiki title;
this step resolves each title through the MediaWiki API (redirects=1, so a moved article is caught and the old
title is kept in former_titles), takes its Wikidata QID from pageprops, then one SPARQL query adds the current
home venue (P115 without an end-time qualifier) and its coordinates (P625).

College (CFB = NCAA Division I FBS football, CBB = Division I men's basketball): the program articles linked
from the Wikipedia lists "List of NCAA Division I FBS football programs" and "List of NCAA Division I men's
basketball programs" (title suffix " football" / " men's basketball"), then the same QID + venue steps.
abbr for college = the program name without the suffix ("North Carolina Tar Heels"); schedule strings like
"North Carolina" resolve by unique prefix (redzone/context/buzz.py TeamIndex.resolve).

Rivalries: Wikidata items that are an instance of a class labelled "sports rivalry" with participating teams
(P1923) or participants (P710) among our QIDs. If Wikidata has none, rivalries.csv is just a header.

Endpoints: en.wikipedia.org/w/api.php and query.wikidata.org/sparql, public, no key; UA set, <= 5 req/s.
Why: Builds data/context/teams.csv and rivalries.csv from Wikipedia and Wikidata (docstring, Data B commit).
"""
from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from redzone.context.buzz import SEED_CSV, TEAM_COLS, Http, title_key  # noqa: E402

log = logging.getLogger("wikidata_teams")
API = "https://en.wikipedia.org/w/api.php"
SPARQL = "https://query.wikidata.org/sparql"
COLLEGE_LISTS = {
    "cfb": ("List of NCAA Division I FBS football programs", " football"),
    "cbb": ("List of NCAA Division I men's basketball programs", " men's basketball"),
}

# links that end in " football" but are not one program (conference/bowl/season/history pages)
NOT_PROGRAM = re.compile(r"\b(Conference|Bowl|NCAA|College football|Division|season|seasons|history|Association|League)\b")

VENUE_Q = """
SELECT ?team ?venue ?coord WHERE {
  VALUES ?team { %s }
  ?team p:P115 ?st . ?st ps:P115 ?venue .
  FILTER NOT EXISTS { ?st pq:P582 ?end }
  OPTIONAL { ?venue wdt:P625 ?coord }
}"""

RIVAL_Q = """
SELECT DISTINCT ?riv ?rivLabel ?team WHERE {
  VALUES ?team { %s }
  ?riv wdt:P1923|wdt:P710 ?team .
  ?riv wdt:P31 ?cls . ?cls rdfs:label "sports rivalry"@en .
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}"""


def college_titles(http: Http, league: str) -> list[str]:
    page, suffix = COLLEGE_LISTS[league]
    data = http.get_json(API, {"action": "parse", "page": page, "prop": "links", "format": "json",
                               "formatversion": 2, "redirects": 1})
    links = (data or {}).get("parse", {}).get("links", [])
    return sorted({lk["title"] for lk in links if lk.get("ns") == 0 and lk.get("exists", True)
                   and lk["title"].endswith(suffix) and not NOT_PROGRAM.search(lk["title"])})


def resolve_titles(http: Http, titles: list[str]) -> dict[str, tuple[str, str]]:
    """title -> (canonical title after redirects, wikidata QID). 50 titles per request."""
    out = {}
    for i in range(0, len(titles), 50):
        batch = titles[i:i + 50]
        d = http.get_json(API, {"action": "query", "titles": "|".join(batch), "redirects": 1, "prop": "pageprops",
                                "ppprop": "wikibase_item", "format": "json", "formatversion": 2})
        q = (d or {}).get("query", {})
        hop = {r["from"]: r["to"] for r in q.get("normalized", []) + q.get("redirects", [])}
        pages = {p["title"]: p.get("pageprops", {}).get("wikibase_item", "") for p in q.get("pages", [])
                 if not p.get("missing")}
        for t in batch:
            c = t
            for _ in range(3):
                c = hop.get(c, c)
            if c in pages:
                out[t] = (c, pages[c])
    return out


def sparql(http: Http, query: str) -> list[dict]:
    d = http.get_json(SPARQL, {"query": query, "format": "json"}, cache=False)
    return (d or {}).get("results", {}).get("bindings", [])


def _qid(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


def venues(http: Http, qids: list[str]) -> dict[str, tuple[str, str, str]]:
    out = {}
    for i in range(0, len(qids), 150):
        vals = " ".join(f"wd:{q}" for q in qids[i:i + 150] if q)
        for b in sparql(http, VENUE_Q % vals):
            lat = lon = ""
            if "coord" in b:     # "Point(lon lat)"
                lon, lat = b["coord"]["value"].removeprefix("Point(").rstrip(")").split()[:2]
            out.setdefault(_qid(b["team"]["value"]), (_qid(b["venue"]["value"]), lat, lon))
    return out


def rivalries(http: Http, qids: list[str]) -> list[dict]:
    parts: dict[str, dict] = {}
    for i in range(0, len(qids), 150):
        vals = " ".join(f"wd:{q}" for q in qids[i:i + 150] if q)
        for b in sparql(http, RIVAL_Q % vals):
            r = parts.setdefault(_qid(b["riv"]["value"]), {"label": b.get("rivLabel", {}).get("value", ""),
                                                          "teams": set()})
            r["teams"].add(_qid(b["team"]["value"]))
    rows = []
    for rq, r in sorted(parts.items()):
        t = sorted(r["teams"])
        for i in range(len(t)):
            for j in range(i + 1, len(t)):
                rows.append({"rivalry_qid": rq, "label": r["label"], "team_a_qid": t[i], "team_b_qid": t[j]})
    return rows


def build(http: Http, leagues: list[str]) -> tuple[list[dict], list[dict]]:
    with open(SEED_CSV, newline="") as f:
        teams = [r for r in csv.DictReader(f) if r["league"] in leagues]
    for lg in ("cfb", "cbb"):
        if lg in leagues:
            suffix = COLLEGE_LISTS[lg][1]
            for t in college_titles(http, lg):
                name = t[: -len(suffix)]
                teams.append({c: "" for c in TEAM_COLS} | {"league": lg, "abbr": name, "name": name,
                                                           "enwiki": title_key(t), "active": "1"})
            log.info("%s: %d programs", lg, sum(1 for r in teams if r["league"] == lg))
    res = resolve_titles(http, sorted({r["enwiki"].replace("_", " ") for r in teams}))
    for r in teams:
        canon, qid = res.get(r["enwiki"].replace("_", " "), ("", ""))
        if not canon:
            log.warning("title not found on enwiki: %s (%s %s)", r["enwiki"], r["league"], r["abbr"])
            continue
        if title_key(canon) != title_key(r["enwiki"]):
            log.warning("moved: %s -> %s", r["enwiki"], canon)
            old = [x for x in str(r.get("former_titles") or "").split("|") if x]
            r["former_titles"] = "|".join(dict.fromkeys(old + [title_key(r["enwiki"])]))
            r["enwiki"] = title_key(canon)
        r["qid"] = qid
    qids = sorted({r["qid"] for r in teams if r.get("qid")})
    ven = venues(http, qids)
    for r in teams:
        v = ven.get(r.get("qid", ""))
        if v:
            r["venue_qid"], r["venue_lat"], r["venue_lon"] = v
    return teams, rivalries(http, qids)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=str(ROOT / "data/context"))
    ap.add_argument("--leagues", default="nfl,cfb,nba,cbb,nhl,mlb")
    ap.add_argument("--rps", type=float, default=5.0)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    teams, rivals = build(Http(rps=a.rps, cache_dir=out / "cache/wiki"), a.leagues.split(","))
    with open(out / "teams.csv", "w", newline="") as f:
        w = csv.DictWriter(f, TEAM_COLS, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(teams)
    with open(out / "rivalries.csv", "w", newline="") as f:
        w = csv.DictWriter(f, ["rivalry_qid", "label", "team_a_qid", "team_b_qid"], lineterminator="\n")
        w.writeheader()
        w.writerows(rivals)
    log.info("teams.csv: %d rows (%d with QID, %d with venue coords); rivalries.csv: %d pairs", len(teams),
             sum(1 for r in teams if r.get("qid")), sum(1 for r in teams if r.get("venue_lat")), len(rivals))


if __name__ == "__main__":
    main()
