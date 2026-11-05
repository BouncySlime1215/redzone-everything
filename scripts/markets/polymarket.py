#!/usr/bin/env python3
"""Polymarket public price history for sports game markets -> market tick rows (Data Division D, worker D1).

Contract: docs/datasources/markets/contract.md. Source notes + license: docs/datasources/markets/polymarket.md.
Documented, unauthenticated read endpoints only (no account, wallet or key; never the HTML site). Wallet,
user and profile fields are never stored. Hidden calibration/eval check only: never a feature, label or
anything shown. Runs on Kaggle (the dev container's allowlist blocks polymarket hosts).

    python scripts/markets/polymarket.py --league nba --start 2024-10-01 --end 2025-06-30 --out out/markets \
        [--max-markets N] [--tag-id 745]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
UA = "redzone-research/0.1"
SOURCE = "polymarket"
TAG = "personal-use, not for commercial release"
LICENSE = ("yellow-B: documented public read APIs, ToS §4.2 bars site scraping and data resale to capital-market "
           "firms, §5.1 personal license; personal-use, not for commercial release")
MIN_INTERVAL_S = 0.5          # ~2 req/s, far below the documented 300-1000 req/10 s
HISTORY_CHUNK_S = 12 * 3600   # 1-minute history fetched in 12 h windows
PRE_S, POST_S = 24 * 3600, 6 * 3600   # window around game start
EASTERN = ZoneInfo("America/New_York")
# Verified on live listings (NBA): from the 2024-25 season, US game markets list away first ("Rockets vs.
# Thunder" = HOU@OKC, 2025-10-21; "Knicks vs. Celtics" = NYK@BOS, 2024-10-22), but April 2024 playoff
# markets listed the HOME team first and often lack gameStartTime. Older markets are skipped, not guessed.
LEGACY_BEFORE = datetime(2024, 7, 1, tzinfo=timezone.utc).timestamp()
DROP_KEYS = {"proxy_wallet", "proxyWallet", "user", "maker", "taker", "owner", "name", "pseudonym", "bio",
             "profile_image", "profile_image_optimized", "profileImage", "transaction_hash", "transactionHash"}

# Our league slug -> Polymarket /sports `sport` code.
PM_SPORT = {"cbb": "ncaab"}
# Leagues whose game markets are 3-way (draw possible): only the home-win Yes market gives p_home.
THREE_WAY = {"epl", "lal", "bun", "sea", "fl1", "ucl", "uel", "mls", "soccer", "elc"}
# US leagues list the away team first (Polymarket /sports `ordering`, typically "away"); soccer lists home first.
DEFAULT_ORDERING = "away"
HOME_FIRST = THREE_WAY

# Nickname (lowercase, as Polymarket outcomes name them) -> league abbreviation, for the six product leagues.
_NBA = ("hawks ATL celtics BOS nets BKN hornets CHA bulls CHI cavaliers CLE mavericks DAL nuggets DEN pistons DET "
        "warriors GSW rockets HOU pacers IND clippers LAC lakers LAL grizzlies MEM heat MIA bucks MIL "
        "timberwolves MIN pelicans NOP knicks NYK thunder OKC magic ORL 76ers PHI suns PHX "
        "trail_blazers POR blazers POR kings SAC spurs SAS raptors TOR jazz UTA wizards WAS")
_NFL = ("cardinals ARI falcons ATL ravens BAL bills BUF panthers CAR bears CHI bengals CIN browns CLE cowboys DAL "
        "broncos DEN lions DET packers GB texans HOU colts IND jaguars JAX chiefs KC raiders LV chargers LAC "
        "rams LAR dolphins MIA vikings MIN patriots NE saints NO giants NYG jets NYJ eagles PHI steelers PIT "
        "49ers SF seahawks SEA buccaneers TB titans TEN commanders WSH")
_NHL = ("ducks ANA bruins BOS sabres BUF flames CGY hurricanes CAR blackhawks CHI avalanche COL blue_jackets CBJ "
        "stars DAL red_wings DET oilers EDM panthers FLA kings LAK wild MIN canadiens MTL predators NSH "
        "devils NJD islanders NYI rangers NYR senators OTT flyers PHI penguins PIT sharks SJS kraken SEA "
        "blues STL lightning TBL maple_leafs TOR utah UTA mammoth UTA hockey_club UTA canucks VAN "
        "golden_knights VGK capitals WSH jets WPG coyotes ARI")
_MLB = ("diamondbacks ARI d-backs ARI braves ATL orioles BAL red_sox BOS cubs CHC white_sox CWS reds CIN "
        "guardians CLE rockies COL tigers DET astros HOU royals KC angels LAA dodgers LAD marlins MIA "
        "brewers MIL twins MIN mets NYM yankees NYY athletics ATH a's ATH phillies PHI pirates PIT padres SD "
        "giants SF mariners SEA cardinals STL rays TB rangers TEX blue_jays TOR nationals WSH")


def _pairs(s: str) -> dict[str, str]:
    w = s.split()
    return {w[i].replace("_", " "): w[i + 1] for i in range(0, len(w), 2)}


TEAMS: dict[str, dict[str, str]] = {"nba": _pairs(_NBA), "nfl": _pairs(_NFL), "nhl": _pairs(_NHL),
                                    "mlb": _pairs(_MLB)}
# Polymarket slug code -> league abbreviation where they differ (slugs are lowercase short codes).
SLUG_ALIAS = {"nba": {"gs": "GSW", "ny": "NYK", "no": "NOP", "sa": "SAS", "bkn": "BKN", "bro": "BKN",
                      "uth": "UTA", "pho": "PHX", "was": "WAS", "wsh": "WAS", "cha": "CHA", "nop": "NOP"},
              "nhl": {"la": "LAK", "nj": "NJD", "sj": "SJS", "tb": "TBL", "mon": "MTL", "utah": "UTA",
                      "las": "VGK", "veg": "VGK", "was": "WSH"},
              "nfl": {"was": "WSH", "jac": "JAX", "la": "LAR", "lv": "LV", "oak": "LV"},
              "mlb": {"az": "ARI", "chw": "CWS", "oak": "ATH", "sd": "SD", "sf": "SF", "kc": "KC", "tb": "TB",
                      "was": "WSH", "wsn": "WSH"}}


# ---------------------------------------------------------------- pure parsing

def _jl(v) -> list:
    """Gamma returns outcomes/outcomePrices/clobTokenIds as JSON-encoded strings."""
    if v is None:
        return []
    if isinstance(v, list):
        return v
    try:
        out = json.loads(v)
        return out if isinstance(out, list) else []
    except (TypeError, ValueError):
        return []


def parse_ts(s: str | None) -> float | None:
    """ISO date-time ('2025-01-02T00:30:00Z', '2025-01-02 00:30:00+00') -> UTC epoch seconds."""
    if not s:
        return None
    s = s.strip().replace(" ", "T").replace("Z", "+00:00")
    if re.search(r"[+-]\d\d$", s):
        s += ":00"
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


def team_abbr(league: str, name: str) -> str:
    """Outcome / slug team name -> normalized abbreviation. Product leagues must hit the map (else '');
    other leagues (cfb, cbb, pretraining) keep the upper-cased short code when it looks like one."""
    n = (name or "").strip().lower()
    if not n:
        return ""
    if league in TEAMS:
        m = TEAMS[league]
        if n in SLUG_ALIAS.get(league, {}):
            return SLUG_ALIAS[league][n]
        if n in m:
            return m[n]
        if n.upper() in set(m.values()):
            return n.upper()
        for nick, ab in sorted(m.items(), key=lambda kv: -len(kv[0])):   # "Los Angeles Lakers" -> lakers
            if n.endswith(" " + nick):
                return ab
        return ""
    if re.fullmatch(r"[a-z0-9]{2,6}", n):
        return n.upper()
    return ""


def _slug_parts(slug: str) -> tuple[str, str, str] | None:
    """'nba-lal-bos-2025-01-15[-...]' -> ('lal', 'bos', '20250115')."""
    m = re.match(r"^[a-z0-9]+-([a-z0-9]+)-([a-z0-9]+)-(\d{4})-(\d\d)-(\d\d)", slug or "")
    return (m.group(1), m.group(2), m.group(3) + m.group(4) + m.group(5)) if m else None


def local_date(ts: float | None) -> str:
    return datetime.fromtimestamp(ts, EASTERN).strftime("%Y%m%d") if ts is not None else ""


_WILL = re.compile(r"^will (?:the )?(.+?) (?:win|beat|defeat)\b", re.I)


def parse_market(m: dict, league: str, ordering: str | None = None) -> dict | None:
    """Gamma Market -> {market_id, token_id, invert, game_key, start_ts, end_ts, kind} or None if not a game
    moneyline we can turn into p_home. `ordering`: Polymarket /sports `ordering` ('away' = away team first)."""
    ordering = ordering or ("home" if league in HOME_FIRST else DEFAULT_ORDERING)
    smt = (m.get("sportsMarketType") or "").lower()
    if smt and smt != "moneyline":
        return None
    outcomes = [str(o) for o in _jl(m.get("outcomes"))]
    tokens = [str(t) for t in _jl(m.get("clobTokenIds"))]
    if len(outcomes) != 2 or len(tokens) != 2:
        return None
    slug = m.get("slug") or ""
    ev = (m.get("events") or [{}])[0] or {}
    sp = _slug_parts(slug) or _slug_parts(ev.get("slug") or "")
    start_ts = parse_ts(m.get("gameStartTime")) or parse_ts(ev.get("startTime"))
    if start_ts is None or start_ts < LEGACY_BEFORE:   # pre-2024-07 listings used other orderings: skip
        return None
    q = m.get("question") or ""

    # Team order from the event title "A vs. B"; first-listed is away unless the sport lists home first.
    raw = q if re.search(r"\s(?:vs\.?|@|at)\s", q, re.I) else (ev.get("title") or q)
    title = re.sub(r"\s*(?:\(.*?\)|\d{4}-\d\d-\d\d)\s*$", "", raw.split(":")[-1].strip())
    names = [re.sub(r"[?.!]$", "", x).strip() for x in re.split(r"\s+(?:vs\.?|@|at)\s+", title, flags=re.I)]
    names = names if len(names) == 2 else ["", ""]
    abbrs = [team_abbr(league, x) for x in names]
    if not all(abbrs) and sp:                       # slug codes, e.g. nba-lal-bos-2025-01-15
        abbrs = [team_abbr(league, sp[0]), team_abbr(league, sp[1])]
    i_home = 0 if ordering == "home" else 1
    home, away = abbrs[i_home], abbrs[1 - i_home]

    def side(name: str) -> int | None:              # which listed team (0/1) a name refers to
        n = name.strip().lower()
        ab = team_abbr(league, name)
        for i in (0, 1):
            if n and names[i] and (n == names[i].lower() or names[i].lower().startswith(n)
                                   or n.startswith(names[i].lower())):
                return i
            if ab and ab == abbrs[i] and league in TEAMS:
                return i
        return None

    low = [o.lower() for o in outcomes]
    if low == ["yes", "no"]:                        # "Will X win?" -> p(yes) is p(X)
        w = _WILL.match(q.strip())
        i = side(w.group(1)) if w else None
        if i is None:
            return None
        if i != i_home and league in THREE_WAY:     # p(away win) != 1 - p(home win) when draws exist
            return None
        token, invert = tokens[0], i != i_home
    else:                                           # two-team moneyline: outcomes are the teams
        if league in THREE_WAY:
            return None
        sides = [side(o) for o in outcomes]
        if i_home not in sides:
            return None
        token, invert = tokens[sides.index(i_home)], False

    day = sp[2] if sp else local_date(start_ts)
    game_key = f"{league}:{day}:{away}@{home}" if (home and away and day) else ""
    return {"market_id": token, "condition_id": m.get("conditionId") or "", "gamma_id": str(m.get("id") or ""),
            "invert": invert, "game_key": game_key, "start_ts": start_ts, "day": day,
            "end_ts": parse_ts(m.get("closedTime")) or parse_ts(m.get("endDate")), "kind": "moneyline"}


def history_to_ticks(history: dict, pm: dict, league: str) -> list[dict]:
    """CLOB /prices-history response {'history': [{'t', 'p'}]} -> contract tick rows (1-minute bars)."""
    seen: set[int] = set()
    out = []
    for pt in sorted(history.get("history") or [], key=lambda x: x.get("t", 0)):
        t, p = pt.get("t"), pt.get("p")
        if t is None or p is None or int(t) in seen:
            continue
        seen.add(int(t))
        p = float(p)
        ph = 1.0 - p if pm["invert"] else p
        start = pm.get("start_ts")
        phase = "pregame" if start is None or t < start else "live"
        out.append({"source": SOURCE, "league": league, "game_key": pm["game_key"], "market_id": pm["market_id"],
                    "market_kind": pm["kind"], "phase": phase, "ts": float(t), "p_home": round(ph, 4),
                    "line": None, "volume": None, "n_trades": None, "interval_s": 60,
                    "license": LICENSE, "tag": TAG})
    return out


def scrub(row: dict) -> dict:
    """Drop wallet / user / profile fields (recursively) before anything is written."""
    return {k: (scrub(v) if isinstance(v, dict) else v) for k, v in row.items() if k not in DROP_KEYS}


def history_windows(pm: dict) -> list[tuple[int, int]]:
    s = pm.get("start_ts")
    if s is None:
        return []
    e = pm.get("end_ts")
    lo, hi = int(s - PRE_S), int(min(s + POST_S, e) if e and e > s + 3600 else s + POST_S)
    return [(a, min(a + HISTORY_CHUNK_S, hi)) for a in range(lo, hi, HISTORY_CHUNK_S)]


def pick_tag(sports: list[dict], league: str) -> int | None:
    """/sports row for the league -> its most specific tag id: the one shared by the fewest sports
    (drops 1 = Sports, 100639 = Games and sport-wide tags such as 100350 = soccer, 28 = basketball)."""
    code = PM_SPORT.get(league, league)
    split = [(str(r.get("sport", "")).lower(), [t.strip() for t in str(r.get("tags", "")).split(",")
                                                 if t.strip().isdigit()]) for r in sports or []]
    freq: dict[str, int] = defaultdict(int)
    for _, tags in split:
        for t in set(tags):
            freq[t] += 1
    for sport, tags in split:
        if sport == code and tags:
            spec = [t for t in tags if t not in ("1", "100639")] or tags
            return int(min(reversed(spec), key=lambda t: freq[t]))
    return None


def sport_ordering(sports: list[dict], league: str) -> str | None:
    code = PM_SPORT.get(league, league)
    for row in sports or []:
        if str(row.get("sport", "")).lower() == code:
            return (row.get("ordering") or "").lower() or None
    return None


# ---------------------------------------------------------------- network

class Client:
    def __init__(self, min_interval: float = MIN_INTERVAL_S, max_retries: int = 6):
        import requests
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        self.min_interval, self.max_retries, self._last, self.n = min_interval, max_retries, 0.0, 0

    def get(self, url: str, params: dict | None = None):
        for attempt in range(self.max_retries):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            self.n += 1
            r = self.s.get(url, params=params, timeout=30)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 502, 503, 504):
                ra = r.headers.get("Retry-After")
                try:
                    ra = float(ra) if ra else (r.json() or {}).get("retry_after_seconds")
                except ValueError:
                    ra = None
                time.sleep(float(ra) if ra else min(60.0, 2.0 ** attempt))
                continue
            print(f"  {r.status_code} {url} {params}: {r.text[:200]}", file=sys.stderr)
            return None
        return None


def list_game_markets(c: Client, tag_id: int, start: str, end: str, max_markets: int | None = None):
    """Closed moneyline markets under a league tag whose end date falls in [start, end + 2 d]."""
    end2 = (datetime.fromisoformat(end) + timedelta(days=2)).strftime("%Y-%m-%d")
    params = {"tag_id": tag_id, "closed": "true", "limit": 100, "sports_market_types": "moneyline",
              "end_date_min": f"{start}T00:00:00Z", "end_date_max": f"{end2}T23:59:59Z"}
    n = 0
    while True:
        page = c.get(f"{GAMMA}/markets/keyset", params)
        if not page:
            return
        for m in page.get("markets") or []:
            yield m
            n += 1
            if max_markets and n >= max_markets:
                return
        cur = page.get("next_cursor")
        if not cur:
            return
        params = {**params, "after_cursor": cur}


def fetch_history(c: Client, pm: dict) -> dict:
    hist: list[dict] = []
    for a, b in history_windows(pm):
        r = c.get(f"{CLOB}/prices-history", {"market": pm["market_id"], "startTs": a, "endTs": b, "fidelity": 1})
        hist.extend((r or {}).get("history") or [])
    return {"history": hist}


def run(league: str, start: str, end: str, out: Path, max_markets: int | None, tag_id: int | None) -> dict:
    c = Client()
    sports = c.get(f"{GAMMA}/sports") or []
    tag_id = tag_id or pick_tag(sports, league)
    if tag_id is None:
        sys.exit(f"no Polymarket sports tag for league {league!r}; pass --tag-id")
    ordering = sport_ordering(sports, league)
    lo, hi = parse_ts(f"{start}T00:00:00Z"), parse_ts(f"{end}T23:59:59Z") + 86400
    by_day: dict[str, list[dict]] = defaultdict(list)
    n_seen = n_markets = n_matched = 0
    for m in list_game_markets(c, tag_id, start, end, max_markets):
        n_seen += 1
        pm = parse_market(m, league, ordering)
        if not pm or pm["start_ts"] is None or not (lo <= pm["start_ts"] <= hi):
            continue
        ticks = history_to_ticks(fetch_history(c, pm), pm, league)
        if not ticks:
            continue
        n_markets += 1
        n_matched += bool(pm["game_key"])
        by_day[pm["day"] or local_date(pm["start_ts"])].extend(scrub(t) for t in ticks)
    d = out / SOURCE / league
    d.mkdir(parents=True, exist_ok=True)
    n_ticks = 0
    for day, rows in sorted(by_day.items()):
        rows.sort(key=lambda r: (r["game_key"], r["market_id"], r["ts"]))
        with open(d / f"{day}.jsonl", "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        n_ticks += len(rows)
    stats = {"league": league, "start": start, "end": end, "tag_id": tag_id, "markets_listed": n_seen,
             "markets": n_markets, "ticks": n_ticks, "days": len(by_day),
             "matched_share": round(n_matched / n_markets, 4) if n_markets else 0.0, "requests": c.n,
             "license": LICENSE, "tag": TAG, "generated_utc": datetime.now(timezone.utc).isoformat()}
    mpath = out / SOURCE / "manifest.json"
    try:
        man = json.loads(mpath.read_text())
    except (OSError, ValueError):
        man = {"source": SOURCE, "license": LICENSE, "tag": TAG, "runs": {}}
    man.setdefault("runs", {})[f"{league}:{start}:{end}"] = stats
    mpath.write_text(json.dumps(man, indent=2))
    return stats


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--league", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", default="out/markets", type=Path)
    ap.add_argument("--max-markets", type=int)
    ap.add_argument("--tag-id", type=int, help="override the tag picked from /sports")
    a = ap.parse_args(argv)
    print(json.dumps(run(a.league.lower(), a.start, a.end, a.out, a.max_markets, a.tag_id), indent=2))


if __name__ == "__main__":
    main()
