#!/usr/bin/env python3
"""Data H game-context corpus, phase 1: Wikipedia team-season and rivalry articles -> derived numbers.

    python scripts/gamectx/wiki_context.py pull --league nba --out out      # network (GitHub Actions only)
    python scripts/gamectx/wiki_context.py pull --league nfl --out out
    python scripts/gamectx/wiki_context.py coverage --out out

What it reads: the rendered HTML of every "<season> <team> season" article (from the category
"<season> NBA season by team" / "<year> National Football League season by team") and every rivalry article
("National Football League rivalries" / "National Basketball Association rivalries") through the public
MediaWiki API (one request at a time, maxlag, a descriptive User-Agent; no login).

What it keeps: NUMBERS ONLY. Article text is parsed in memory and dropped; no sentence, paragraph or title text
is written (the repo is public). Each row carries the page id + revision id so the read can be reproduced.
License: Wikipedia text is CC BY-SA 4.0; facts and counts derived from it carry the attribution line in the
ledger. Tag: personal-use, not for commercial release.

Outputs (out/<league>/):
  games.csv        one row per (team, game): date, opp, home, postseason, week/game no, result, record going IN,
                   streak going IN, and the article's write-up numbers for that game (*_postgame: written after the
                   game, so hindsight labels / features for later games only, never same-game inputs).
  team_week.csv    one row per (team, season, week) incl. week 0 = offseason: games so far, win pct going in,
                   streak, offseason roster-move counts, coach change, narrative counts to date.
  team_season.csv  one row per team-season: offseason counts, coach change, article size, narrative totals.
  rivalries.csv    one row per rivalry pair: article words, first meeting year, meetings, playoff meetings.
  ledger.json      articles processed, games, team-weeks, bytes read, license, tag.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date
from pathlib import Path

TAG = "personal-use, not for commercial release"
LICENSE = "Wikipedia CC BY-SA 4.0 (derived numbers; attribution: Wikipedia contributors)"
API = "https://en.wikipedia.org/w/api.php"
UA = "redzone-gamectx/0.1 (https://github.com/BouncySlime1215/redzone-everything; personal research, low rate)"

# ---- teams --------------------------------------------------------------------------------------------------
NFL = {"Cardinals": "ARI", "Falcons": "ATL", "Ravens": "BAL", "Bills": "BUF", "Panthers": "CAR", "Bears": "CHI",
       "Bengals": "CIN", "Browns": "CLE", "Cowboys": "DAL", "Broncos": "DEN", "Lions": "DET", "Packers": "GB",
       "Texans": "HOU", "Colts": "IND", "Jaguars": "JAX", "Chiefs": "KC", "Rams": "LA", "Chargers": "LAC",
       "Raiders": "LV", "Dolphins": "MIA", "Vikings": "MIN", "Patriots": "NE", "Saints": "NO", "Giants": "NYG",
       "Jets": "NYJ", "Eagles": "PHI", "Steelers": "PIT", "Seahawks": "SEA", "49ers": "SF", "Buccaneers": "TB",
       "Titans": "TEN", "Redskins": "WAS", "Football Team": "WAS", "Commanders": "WAS"}
NBA = {"Hawks": "ATL", "Celtics": "BOS", "Nets": "BKN", "Bobcats": "CHA", "Bulls": "CHI", "Cavaliers": "CLE",
       "Mavericks": "DAL", "Nuggets": "DEN", "Pistons": "DET", "Warriors": "GSW", "Rockets": "HOU", "Pacers": "IND",
       "Clippers": "LAC", "Lakers": "LAL", "Grizzlies": "MEM", "Heat": "MIA", "Bucks": "MIL", "Timberwolves": "MIN",
       "Pelicans": "NOP", "Knicks": "NYK", "Thunder": "OKC", "SuperSonics": "OKC", "Magic": "ORL", "76ers": "PHI",
       "Suns": "PHX", "Trail Blazers": "POR", "Kings": "SAC", "Spurs": "SAS", "Raptors": "TOR", "Jazz": "UTA",
       "Wizards": "WAS", "Hornets": "CHA"}


# NBA game logs name the opponent by city ("@ Boston", "L.A. Lakers")
NBA_CITY = {"Atlanta": "ATL", "Boston": "BOS", "Brooklyn": "BKN", "New Jersey": "BKN", "Charlotte": "CHA",
            "Chicago": "CHI", "Cleveland": "CLE", "Dallas": "DAL", "Denver": "DEN", "Detroit": "DET",
            "Golden State": "GSW", "Houston": "HOU", "Indiana": "IND", "L.A. Clippers": "LAC", "LA Clippers": "LAC",
            "Los Angeles Clippers": "LAC", "L.A. Lakers": "LAL", "LA Lakers": "LAL", "Los Angeles Lakers": "LAL",
            "Memphis": "MEM", "Vancouver": "MEM", "Miami": "MIA", "Milwaukee": "MIL", "Minnesota": "MIN",
            "New Orleans": "NOP", "New York": "NYK", "Oklahoma City": "OKC", "Seattle": "OKC", "Orlando": "ORL",
            "Philadelphia": "PHI", "Phoenix": "PHX", "Portland": "POR", "Sacramento": "SAC", "San Antonio": "SAS",
            "Toronto": "TOR", "Utah": "UTA", "Washington": "WAS"}
# NFL franchise -> article team name by season (titles are built directly; the "by team" categories are not reliable)
NFL_NAMES = {"ARI": "Arizona Cardinals", "ATL": "Atlanta Falcons", "BAL": "Baltimore Ravens", "BUF": "Buffalo Bills",
             "CAR": "Carolina Panthers", "CHI": "Chicago Bears", "CIN": "Cincinnati Bengals", "CLE": "Cleveland Browns",
             "DAL": "Dallas Cowboys", "DEN": "Denver Broncos", "DET": "Detroit Lions", "GB": "Green Bay Packers",
             "HOU": "Houston Texans", "IND": "Indianapolis Colts", "JAX": "Jacksonville Jaguars",
             "KC": "Kansas City Chiefs", "MIA": "Miami Dolphins", "MIN": "Minnesota Vikings",
             "NE": "New England Patriots", "NO": "New Orleans Saints", "NYG": "New York Giants", "NYJ": "New York Jets",
             "PHI": "Philadelphia Eagles", "PIT": "Pittsburgh Steelers", "SEA": "Seattle Seahawks",
             "SF": "San Francisco 49ers", "TB": "Tampa Bay Buccaneers", "TEN": "Tennessee Titans"}


def nfl_team_name(abbr: str, season: int) -> str | None:
    if abbr == "HOU" and season < 2002:
        return None
    if abbr == "LA":
        return "St. Louis Rams" if season < 2016 else "Los Angeles Rams"
    if abbr == "LAC":
        return "San Diego Chargers" if season < 2017 else "Los Angeles Chargers"
    if abbr == "LV":
        return "Oakland Raiders" if season < 2020 else "Las Vegas Raiders"
    if abbr == "WAS":
        return "Washington Redskins" if season < 2020 else ("Washington Football Team" if season < 2022
                                                            else "Washington Commanders")
    return NFL_NAMES.get(abbr)


def team_abbr(name: str, league: str) -> str | None:
    """'at Dallas Cowboys' / '@ New Orleans Hornets' / 'Washington Football Team' -> abbreviation (franchise)."""
    n = re.sub(r"\[.*?\]|\(.*?\)|[*†‡#^]", "", name or "").strip()
    n = re.sub(r"^(at|vs\.?|@)\s+", "", n, flags=re.I).strip()
    if league == "nba" and "Hornets" in n and ("New Orleans" in n or "Oklahoma City" in n):
        return "NOP"
    table = NFL if league == "nfl" else NBA
    for nick in sorted(table, key=len, reverse=True):
        if re.search(rf"\b{re.escape(nick)}\b", n):
            return table[nick]
    if league == "nba":
        for city in sorted(NBA_CITY, key=len, reverse=True):
            if n.startswith(city):
                return NBA_CITY[city]
    return None


def season_titles(league: str, first: int, last: int) -> list[tuple[int, str]]:
    """(season start year, category title) newest first is NOT wanted: oldest first so streaks chain."""
    out = []
    for y in range(first, last + 1):
        if league == "nfl":
            out.append((y, f"Category:{y} National Football League season by team"))
        else:
            out.append((y, f"Category:{y}–{str(y + 1)[-2:]} NBA season by team"))
    return out


def rivalry_titles(league: str) -> list[str]:
    table = NFL if league == "nfl" else NBA
    by_team = {}
    for nick, ab in table.items():
        by_team.setdefault(ab, []).append(nick)
    teams = sorted(by_team)
    out = []
    for i, a in enumerate(teams):
        for b in teams[i + 1:]:
            for na in by_team[a]:
                for nb in by_team[b]:
                    out += [f"{na}–{nb} rivalry", f"{nb}–{na} rivalry"]
    return out


RIVALRY_CATS = {"nfl": "Category:National Football League rivalries",
                "nba": "Category:National Basketball Association rivalries"}

# ---- narrative counts (stems, lower-case; counted per section, text dropped after) ---------------------------
TAGS = {
    "injury": r"injur|torn |sprain|concussion|out for the season|season-ending|surgery",
    "rivalry": r"rival",
    "streak": r"streak",
    "milestone": r"franchise record|career-high|career high|milestone|all-time|nba record|nfl record",
    "stakes": r"clinch|eliminat|playoff berth|division title|wild card|play-in|seed|must-win|postseason",
    "comeback": r"comeback|rallied|rally ",
    "overtime": r"overtime",
    "debut_return": r"debut|returned|return from|first game back",
    "national_tv": r"monday night|sunday night|thursday night|christmas|thanksgiving|primetime|prime time",
    "trade_sign": r"traded|acquired|signed|waived|released",
    "coach": r"head coach|fired|hired|interim",
}
TAG_RE = {k: re.compile(v, re.I) for k, v in TAGS.items()}


def tag_counts(text: str) -> dict:
    return {f"tag_{k}": len(r.findall(text)) for k, r in TAG_RE.items()}


OFFSEASON_H = re.compile(r"offseason|off-season|free agen|signing|departure|trade|draft|roster change|"
                         r"coaching change|staff change|transaction|additions|subtractions|re-sign|waiv", re.I)
PRESEASON_H = re.compile(r"preseason|pre-season|summer league|exhibition", re.I)
POST_H = re.compile(r"postseason|playoff", re.I)
SUMMARY_WEEK = re.compile(r"\bweek\s*(\d{1,2})\b", re.I)
POST_ROUND = {"wild card": 101, "first round": 101, "divisional": 102, "conference semifinal": 102,
              "conference championship": 103, "conference final": 103, "super bowl": 104, "nba finals": 104,
              "finals": 104}
MOVE_KIND = [("signings", re.compile(r"sign|addition|acquir|free agen", re.I)),
             ("departures", re.compile(r"depart|subtraction|releas|lost|waiv", re.I)),
             ("trades", re.compile(r"trade", re.I)),
             ("draft", re.compile(r"draft", re.I))]


# ---- the HTML walk -------------------------------------------------------------------------------------------
def _text(el) -> str:
    return " ".join(el.text_content().split())


def walk(html: str) -> dict:
    """One article's rendered HTML -> sections [{h2, h3, words, tags, tables:[DataFrame]}] + infobox dict.
    Nothing textual leaves this function except header labels used for routing."""
    import lxml.html
    import pandas as pd
    root = lxml.html.fromstring(html)
    infobox = {}
    for tb in root.xpath("//table[contains(@class,'infobox')]"):
        for tr in tb.xpath(".//tr"):
            th, td = tr.xpath("./th"), tr.xpath("./td")
            if th and td:
                infobox[_text(th[0]).lower()] = _text(td[0])
        tb.getparent().remove(tb)
    for junk in root.xpath("//*[contains(@class,'navbox') or contains(@class,'reflist') or contains(@class,"
                           "'references') or contains(@class,'mw-references-wrap') or self::style or self::script]"):
        junk.getparent().remove(junk)
    secs = [{"h2": "", "h3": "", "words": 0, "tags": defaultdict(int), "tables": []}]
    for el in root.iter():
        tag = el.tag if isinstance(el.tag, str) else ""
        if tag in ("h2", "h3", "h4"):
            h = _text(el).replace("[edit]", "").strip()
            cur = secs[-1]
            if tag == "h2":
                secs.append({"h2": h, "h3": "", "words": 0, "tags": defaultdict(int), "tables": []})
            else:
                secs.append({"h2": cur["h2"], "h3": h, "words": 0, "tags": defaultdict(int), "tables": []})
        elif tag in ("p", "li", "dd") and not el.xpath("ancestor::table"):
            t = _text(el)
            s = secs[-1]
            s["words"] += len(t.split())
            for k, v in tag_counts(t).items():
                s["tags"][k] += v
        elif tag == "table" and not el.xpath("ancestor::table"):
            # top-level tables only; read_html returns the nested ones too (NBA monthly game logs sit inside a
            # collapsible outer table). Duplicate game rows are dropped later by (date, opp).
            try:
                secs[-1]["tables"] += pd.read_html(io.StringIO(lxml.html.tostring(el, encoding="unicode")))
            except (ValueError, IndexError, ImportError):
                pass
    return {"sections": secs, "infobox": infobox}


def _flat(cols) -> list[str]:
    out = []
    for c in cols:
        if isinstance(c, tuple):
            c = " ".join(dict.fromkeys(str(x) for x in c if not str(x).startswith("Unnamed")))
        out.append(str(c).strip().lower())
    return out


def _col(cols: list[str], *names) -> int | None:
    for n in names:
        for i, c in enumerate(cols):
            if c == n or c.startswith(n):
                return i
    return None


MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], 1)}


def parse_date(s: str, season: int) -> str | None:
    m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+"
                  r"(\d{1,2})(?:,\s*(\d{4}))?", str(s), re.I)
    if not m:
        return None
    mo, d = MONTHS3[m.group(1).lower()], int(m.group(2))
    y = int(m.group(3)) if m.group(3) else (season + 1 if mo <= 7 else season)
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


MONTHS3 = {k[:3]: v for k, v in MONTHS.items()}
RESULT = re.compile(r"\b([WLT])\s*[\s,]*(\d{1,3})\s*[–\-−]\s*(\d{1,3})")


def schedule_rows(sec: dict, league: str, season: int) -> list[dict]:
    """Game rows from a section's tables (schedule / game log). Opponent, date, result, home/away."""
    rows = []
    post = bool(POST_H.search(sec["h2"] + " " + sec["h3"]))
    if PRESEASON_H.search(sec["h2"] + " " + sec["h3"]):
        return rows
    for df in sec["tables"]:
        cols = _flat(df.columns)
        ci_opp = _col(cols, "opponent", "team")
        ci_res = _col(cols, "result", "score")
        ci_date = _col(cols, "date")
        if ci_opp is None or ci_res is None or ci_date is None:
            continue
        ci_wk = _col(cols, "week", "game", "round")
        for r in df.itertuples(index=False):
            r = [str(x) for x in r]
            opp_raw, res = r[ci_opp], r[ci_res]
            m = RESULT.search(res)
            d = parse_date(r[ci_date], season)
            opp = team_abbr(opp_raw, league)
            if not (m and d and opp):
                continue
            wk = r[ci_wk] if ci_wk is not None else ""
            wk_n = int(re.sub(r"\D", "", wk)) if re.fullmatch(r"\s*\d{1,3}\s*", wk or "") else None
            if post and wk_n is None:
                wk_n = next((v for k, v in POST_ROUND.items() if k in wk.lower()), 100)
            away = bool(re.match(r"\s*(at|@)\s", opp_raw, re.I))
            rows.append({"date": d, "opp": opp, "home": int(not away), "postseason": int(post),
                         "week": wk_n, "result": m.group(1), "pf": int(m.group(2)), "pa": int(m.group(3))})
    return rows


def article_features(html: str, league: str, season: int) -> dict:
    w = walk(html)
    secs = w["sections"]
    games, summaries = [], {}
    off = {"off_words": 0, "off_tables_rows": 0, **{f"off_{k}": 0 for k, _ in MOVE_KIND},
           **{f"off_tag_{k}": 0 for k in TAGS}}
    total = {"words": 0, **{f"tag_{k}": 0 for k in TAGS}}
    for s in secs:
        head = f"{s['h2']} {s['h3']}"
        total["words"] += s["words"]
        for k, v in s["tags"].items():
            total[k] += v
        if OFFSEASON_H.search(head) and not POST_H.search(head):
            off["off_words"] += s["words"]
            for k, v in s["tags"].items():
                off["off_" + k] += v
            n = sum(len(df) for df in s["tables"])
            off["off_tables_rows"] += n
            for k, rx in MOVE_KIND:
                if rx.search(head):
                    off[f"off_{k}"] += n
                    break
        games += schedule_rows(s, league, season)
        if s["h3"]:   # a game write-up (NFL "Week 3: at Dallas Cowboys", playoff round headings)
            m = SUMMARY_WEEK.search(s["h3"])
            wk = int(m.group(1)) if m else next((v for k, v in POST_ROUND.items() if k in s["h3"].lower()), None)
            if wk is not None and s["words"]:
                d = summaries.setdefault(wk, {"words": 0, **{f"tag_{k}": 0 for k in TAGS}})
                d["words"] += s["words"]
                for k, v in s["tags"].items():
                    d[k] += v
    seen, uniq = set(), []
    for g in sorted(games, key=lambda g: (g["date"], g["opp"])):
        if (g["date"], g["opp"]) not in seen:
            seen.add((g["date"], g["opp"]))
            uniq.append(g)
    coach = next((v for k, v in w["infobox"].items() if k.startswith("head coach") or k == "coach"), "")
    return {"games": uniq, "summaries": summaries, "offseason": off, "total": total,
            "coach_key": hashlib.sha1(coach.strip().lower().encode()).hexdigest()[:10] if coach else None}


# ---- network (Actions only) ----------------------------------------------------------------------------------
class Wiki:
    def __init__(self, pause_s: float = 0.6):
        self.pause_s, self.bytes, self.calls = pause_s, 0, 0

    def get(self, **params) -> dict:
        params = {"format": "json", "formatversion": "2", "maxlag": "5", **params}
        url = API + "?" + urllib.parse.urlencode(params)
        for attempt in range(6):
            time.sleep(self.pause_s)
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "identity"})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    body = r.read()
                self.bytes += len(body)
                self.calls += 1
                js = json.loads(body)
                if js.get("error", {}).get("code") == "maxlag":
                    time.sleep(5 * (attempt + 1))
                    continue
                return js
            except Exception as e:  # noqa: BLE001 - network: back off and retry, then give up on this call
                print(f"  retry {attempt}: {type(e).__name__}", file=sys.stderr)
                time.sleep(2 ** attempt)
        return {}

    def members(self, cat: str) -> list[dict]:
        out, cont = [], {}
        while True:
            js = self.get(action="query", list="categorymembers", cmtitle=cat, cmlimit="500", cmnamespace="0",
                          **cont)
            out += js.get("query", {}).get("categorymembers", [])
            if "continue" not in js:
                return out
            cont = {"cmcontinue": js["continue"]["cmcontinue"]}

    def resolve(self, titles: list[str]) -> list[dict]:
        """Titles -> [{pageid, title}] for the ones that exist (redirects followed), 50 per call."""
        out = []
        for i in range(0, len(titles), 50):
            js = self.get(action="query", titles="|".join(titles[i:i + 50]), redirects="1")
            out += [{"pageid": p["pageid"], "title": p["title"]} for p in js.get("query", {}).get("pages", [])
                    if "pageid" in p and not p.get("missing")]
        return out

    def html(self, pageid: int) -> tuple[str, int]:
        js = self.get(action="parse", pageid=str(pageid), prop="text|revid", disablelimitreport="1",
                      disableeditsection="1")
        p = js.get("parse", {})
        return p.get("text", ""), int(p.get("revid") or 0)


# ---- build tables --------------------------------------------------------------------------------------------
def season_tables(league: str, season: int, team: str, feats: dict, pageid: int, revid: int,
                  prev_coach: dict) -> tuple[list[dict], list[dict], dict]:
    games_out, weeks = [], {}
    w = l = t = streak = 0
    off = feats["offseason"]
    cc = feats["coach_key"]
    coach_change = int(prev_coach.get(team) is not None and cc is not None and prev_coach[team] != cc)
    if cc is not None:
        prev_coach[team] = cc
    start = date.fromisoformat(feats["games"][0]["date"]) if feats["games"] else None
    narr = defaultdict(int)
    base = {"league": league, "season": season, "team": team, "pageid": pageid, "revid": revid}
    weeks[0] = {**base, "week": 0, "games_in": 0, "win_pct_in": None, "streak_in": 0, "coach_change": coach_change,
                **off}
    for i, g in enumerate(feats["games"]):
        n = w + l + t
        wk = g["week"] if (league == "nfl" and g["week"] is not None) else \
            ((date.fromisoformat(g["date"]) - start).days // 7 + 1 if start else None)
        if g["postseason"] and league == "nba":
            wk = 100 + (wk or 0)
        summ = feats["summaries"].get(g["week"]) if league == "nfl" else None
        row = {**base, "game_no": i + 1, **{k: g[k] for k in ("date", "opp", "home", "postseason", "result")},
               "week": wk, "margin_postgame": g["pf"] - g["pa"], "record_in_w": w, "record_in_l": l,
               "record_in_t": t, "win_pct_in": round((w + 0.5 * t) / n, 4) if n else None, "streak_in": streak,
               "writeup_words_postgame": summ["words"] if summ else None,
               **{f"{k}_postgame": (summ[k] if summ else None) for k in (f"tag_{x}" for x in TAGS)}}
        games_out.append(row)
        if wk is not None and wk not in weeks:
            weeks[wk] = {**base, "week": wk, "games_in": n, "win_pct_in": row["win_pct_in"], "streak_in": streak,
                         "coach_change": coach_change, **{f"narr_to_date_{k}": narr[k] for k in TAGS}}
        if summ:
            for k in TAGS:
                narr[k] += summ[f"tag_{k}"]
        if g["result"] == "W":
            w += 1
            streak = streak + 1 if streak > 0 else 1
        elif g["result"] == "L":
            l += 1
            streak = streak - 1 if streak < 0 else -1
        else:
            t += 1
            streak = 0
    team_season = {**base, "games": len(games_out), "coach_change": coach_change, "coach_key": cc or "", **off,
                   "article_words": feats["total"]["words"], **{k: v for k, v in feats["total"].items()
                                                                if k.startswith("tag_")}}
    return games_out, list(weeks.values()), team_season


def team_of_title(title: str, league: str) -> str | None:
    t = re.sub(r"^\d{4}(–\d{2,4})?\s+", "", title)
    t = re.sub(r"\s+season$", "", t)
    return team_abbr(t, league)


def rivalry_row(league: str, title: str, html: str, pageid: int, revid: int) -> dict | None:
    t = title.replace(" rivalry", "")
    parts = re.split(r"[–\-−]", t)
    teams = sorted({a for a in (team_abbr(p, league) for p in parts) if a})
    if len(teams) != 2:
        return None
    w = walk(html)
    ib = w["infobox"]

    def num(*keys):
        for k, v in ib.items():
            if any(k.startswith(x) for x in keys):
                m = re.search(r"\d[\d,]*", v)
                if m:
                    return int(m.group(0).replace(",", ""))
        return None

    first = next((v for k, v in ib.items() if k.startswith("first meeting")), "")
    fy = re.search(r"(18|19|20)\d{2}", first)
    return {"league": league, "team_a": teams[0], "team_b": teams[1], "pageid": pageid, "revid": revid,
            "article_words": sum(s["words"] for s in w["sections"]),
            "first_meeting_year": int(fy.group(0)) if fy else None,
            "meetings_total": num("meetings total", "total meetings", "meetings"),
            "postseason_meetings": num("postseason", "playoff"),
            "tag_rivalry": sum(s["tags"]["tag_rivalry"] for s in w["sections"])}


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with path.open("w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=keys)
        wr.writeheader()
        wr.writerows(rows)


def _read(path: Path) -> list[dict]:
    return list(csv.DictReader(path.open())) if path.exists() else []


def _coach_pass(seasons: list[dict], weeks: list[dict]) -> None:
    """coach_change = this season's head coach differs from the team's previous season (needs both articles)."""
    key = {(r["team"], int(r["season"])): r.get("coach_key") or "" for r in seasons}
    for rows in (seasons, weeks):
        for r in rows:
            a, b = key.get((r["team"], int(r["season"]))), key.get((r["team"], int(r["season"]) - 1))
            r["coach_change"] = int(a != b) if (a and b) else ""


def pull(league: str, first: int, last: int, out: Path, wiki: Wiki | None = None, limit: int = 0,
         rivalries: bool = True) -> dict:
    """Newest season first (Nick 12:10 PM: recent context weighs most). Each season is merged into the outputs
    already in out/<league>/ and written at once, so a slice is usable the moment it lands."""
    wiki = wiki or Wiki()
    d = out / league
    games, weeks, seasons, rivals = (_read(d / f) for f in ("games.csv", "team_week.csv", "team_season.csv",
                                                             "rivalries.csv"))
    old = json.loads((d / "ledger.json").read_text()) if (d / "ledger.json").exists() else {}
    articles = 0
    done_seasons = set(old.get("seasons_done", []))

    def flush():
        _coach_pass(seasons, weeks)
        for name, rows in (("games.csv", games), ("team_week.csv", weeks), ("team_season.csv", seasons),
                           ("rivalries.csv", rivals)):
            write_csv(d / name, rows)
        led = {"league": league, "seasons_done": sorted(done_seasons, reverse=True),
               "articles": old.get("articles", 0) + articles, "team_seasons": len(seasons), "games": len(games),
               "games_with_writeup": sum(1 for g in games if g.get("writeup_words_postgame") not in (None, "")),
               "team_weeks": len(weeks), "rivalries": len(rivals),
               "api_calls": old.get("api_calls", 0) + wiki.calls, "bytes_read": old.get("bytes_read", 0) + wiki.bytes,
               "license": LICENSE, "tag": TAG, "raw_text_stored": False,
               "built_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        (d / "ledger.json").write_text(json.dumps(led, indent=1))
        return led

    led = {}
    for season, cat in sorted(season_titles(league, first, last), reverse=True):
        if league == "nfl":
            names = [n for n in (nfl_team_name(a, season) for a in sorted(set(NFL_NAMES) | {"LA", "LAC", "LV", "WAS"}))
                     if n]
            mem = wiki.resolve([f"{season} {n} season" for n in names])
        else:
            mem = wiki.members(cat)
        print(f"{league} {season}: {len(mem)} articles", flush=True)
        g_s, w_s, t_s = [], [], []
        for m in mem:
            team = team_of_title(m["title"], league)
            if not team or not m["title"].endswith("season"):
                continue
            html, rev = wiki.html(m["pageid"])
            if not html:
                continue
            articles += 1
            g, wk, ts = season_tables(league, season, team, article_features(html, league, season), m["pageid"], rev,
                                      {})
            g_s += g
            w_s += wk
            t_s.append(ts)
            if limit and articles >= limit:
                break
        print(f"{league} {season}: {len(t_s)} team-seasons, {len(g_s)} games parsed", flush=True)
        keep = (lambda rows: [r for r in rows if int(r["season"]) != season])
        games, weeks, seasons = keep(games) + g_s, keep(weeks) + w_s, keep(seasons) + t_s
        done_seasons.add(season)
        led = flush()
        if limit and articles >= limit:
            break
    if rivalries:
        new = []
        mem = wiki.members(RIVALRY_CATS[league])
        if not mem:   # category title not found: try "<A>–<B> rivalry" for every franchise pair (redirects followed)
            mem = wiki.resolve(rivalry_titles(league))
        seen_ids = set()
        mem = [m for m in mem if not (m["pageid"] in seen_ids or seen_ids.add(m["pageid"]))]
        print(f"{league} rivalries: {len(mem)} articles", flush=True)
        for m in mem:
            if "rivalry" not in m["title"].lower():
                continue
            html, rev = wiki.html(m["pageid"])
            if html:
                articles += 1
                r = rivalry_row(league, m["title"], html, m["pageid"], rev)
                if r:
                    new.append(r)
                else:
                    print(f"  rivalry not mapped to 2 teams: pageid {m['pageid']}", flush=True)
        print(f"{league} rivalries: {len(new)} pairs", flush=True)
        rivals = new or rivals
        led = flush()
    return led


def coverage(out: Path) -> list[dict]:
    rows = []
    for p in sorted(out.glob("*/ledger.json")):
        rows.append(json.loads(p.read_text()))
    for r in rows:
        print(f"{r['league'].upper()} seasons {r.get('seasons_done')}: games {r['games']} (write-ups {r['games_with_writeup']}), team-weeks "
              f"{r['team_weeks']}, team-seasons {r['team_seasons']}, rivalries {r['rivalries']}, articles "
              f"{r['articles']}, {r['bytes_read'] / 1e6:.0f} MB read")
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["pull", "coverage"])
    ap.add_argument("--league", choices=["nfl", "nba"])
    ap.add_argument("--first", type=int)
    ap.add_argument("--last", type=int, default=2026)
    ap.add_argument("--limit", type=int, default=0, help="stop after N articles (smoke runs)")
    ap.add_argument("--out", type=Path, default=Path("out"))
    ap.add_argument("--no-rivalries", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "coverage":
        coverage(a.out)
        return 0
    first = a.first or (1999 if a.league == "nfl" else 2001)
    print(json.dumps(pull(a.league, first, a.last, a.out, limit=a.limit, rivalries=not a.no_rivalries), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
