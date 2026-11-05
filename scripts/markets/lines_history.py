#!/usr/bin/env python3
"""Free pregame line history -> markets tick rows (worker D3, markets division).

Converts three free sources into contract tick rows (docs/datasources/markets/contract.md):

  * nflverse games.csv (nflverse/nfldata `games.csv`, same columns as nflreadr `load_schedules`):
    closing spread, total and moneylines only -> phase `close`.
  * SBRO (Sportsbook Reviews Online) historical xlsx, fetched from the Wayback Machine only: two rows per
    game (V row, then H row) with Open/Close columns -> phase `open` and `close` for spread, total and
    moneyline (NBA/NFL/NCAAF/NCAAB: Open/Close hold spread-or-total, ML is the close moneyline;
    NHL/MLB: Open/Close are moneylines, OpenOU/CloseOU are totals, PuckLine/RunLine are spreads).
  * The Odds API forward snapshots (free tier, key held by Nick): one response of /v4/sports/{sport}/odds
    -> consensus (median across bookmakers) vig-free p_home, home spread and total, phase `pregame`/`live`.

Times: historical files have no observation time, so `ts` is the best known time (open = a few days or
hours before the start, close = 10 minutes before the start), every row has `interval_s = 0`, and the
manifest records `ts_quality: "approx"`. Home spread sign follows the contract: negative = home favored.
Spread/total ticks carry p_home = null (a cover price is not a win probability).

Model inputs only (importance, closeness, volatility). Never a label, never shown, never a betting hook.

CLI:
  python scripts/markets/lines_history.py --source sbro --in <dir of xlsx|csv> --league nba --out out/markets
  python scripts/markets/lines_history.py --source nflverse --in games.csv --out out/markets
  python scripts/markets/lines_history.py --source odds_api --in snapshot.json --league nba --out out/markets
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import statistics
import sys
from collections import defaultdict
from typing import Iterable, Iterator
from zoneinfo import ZoneInfo

TAG = "personal-use, not for commercial release"
ET = ZoneInfo("America/New_York")
UTC = dt.timezone.utc

LICENSES = {
    "nflverse_lines": "yellow-B personal use: nflverse/nfldata packaging (CC-BY-4.0 style, credit nflverse); "
                      "line values are third-party sportsbook facts; model input only",
    "sbro": "yellow-B personal use: Wayback copies of SBRO xlsx, no license stated, odds are weakly "
            "copyrightable facts; Nick approves + logs; never run scrapers against live SBR",
    "odds_api": "yellow: The Odds API free tier, terms allow storage/ML use, no resale or redistribution; "
                "forward only (historical is paid, never)",
}

# Closing tick sits 10 min before start (point-in-time safe); open offset per league.
CLOSE_LEAD_S = 600
OPEN_LEAD_S = {"nfl": 6.5 * 86400, "cfb": 6.0 * 86400, "nba": 20 * 3600, "cbb": 20 * 3600,
               "nhl": 20 * 3600, "mlb": 18 * 3600}
# SBRO has no start times; default local start (ET) per league.
DEFAULT_START_ET = {"nfl": (13, 0), "cfb": (12, 0), "nba": (19, 30), "cbb": (19, 0),
                    "nhl": (19, 0), "mlb": (19, 5)}
# Season start month for SBRO MMDD dates (months >= this belong to the first year of "2019-20").
SEASON_START_MONTH = {"nfl": 8, "cfb": 8, "nba": 9, "cbb": 10, "nhl": 9, "mlb": 1}

# ------------------------------------------------------------------------------------------------ teams
# SBRO compact names (no spaces) and The Odds API nicknames -> abbreviations used in game_key.
_SBRO = {
    "nba": {"atlanta": "ATL", "boston": "BOS", "brooklyn": "BKN", "newjersey": "NJN", "charlotte": "CHA",
            "chicago": "CHI", "cleveland": "CLE", "dallas": "DAL", "denver": "DEN", "detroit": "DET",
            "goldenstate": "GSW", "houston": "HOU", "indiana": "IND", "laclippers": "LAC",
            "lalakers": "LAL", "memphis": "MEM", "miami": "MIA", "milwaukee": "MIL", "minnesota": "MIN",
            "neworleans": "NOP", "newyork": "NYK", "oklahomacity": "OKC", "seattle": "SEA",
            "orlando": "ORL", "philadelphia": "PHI", "phoenix": "PHX", "portland": "POR",
            "sacramento": "SAC", "sanantonio": "SAS", "toronto": "TOR", "utah": "UTA",
            "washington": "WAS"},
    "nfl": {"arizona": "ARI", "atlanta": "ATL", "baltimore": "BAL", "buffalo": "BUF", "carolina": "CAR",
            "chicago": "CHI", "cincinnati": "CIN", "cleveland": "CLE", "dallas": "DAL", "denver": "DEN",
            "detroit": "DET", "greenbay": "GB", "houston": "HOU", "houstontexans": "HOU",
            "indianapolis": "IND", "jacksonville": "JAX", "kansas": "KC", "kansascity": "KC",
            "oakland": "LV", "lvraiders": "LV", "lasvegas": "LV", "sandiego": "LAC", "lachargers": "LAC",
            "stlouis": "LA", "larams": "LA", "losangeles": "LA", "miami": "MIA", "minnesota": "MIN",
            "newengland": "NE", "neworleans": "NO", "nygiants": "NYG", "nyjets": "NYJ",
            "philadelphia": "PHI", "pittsburgh": "PIT", "sanfrancisco": "SF", "seattle": "SEA",
            "tampa": "TB", "tampabay": "TB", "tennessee": "TEN", "washington": "WAS",
            "washingtoncom": "WAS"},
    "nhl": {"anaheim": "ANA", "arizona": "ARI", "phoenix": "ARI", "boston": "BOS", "buffalo": "BUF",
            "calgary": "CGY", "carolina": "CAR", "chicago": "CHI", "colorado": "COL", "columbus": "CBJ",
            "dallas": "DAL", "detroit": "DET", "edmonton": "EDM", "florida": "FLA", "losangeles": "LAK",
            "minnesota": "MIN", "montreal": "MTL", "nashville": "NSH", "newjersey": "NJD",
            "nyislanders": "NYI", "nyrangers": "NYR", "ottawa": "OTT", "philadelphia": "PHI",
            "pittsburgh": "PIT", "sanjose": "SJS", "seattle": "SEA", "stlouis": "STL",
            "tampabay": "TBL", "toronto": "TOR", "vancouver": "VAN", "vegas": "VGK",
            "washington": "WSH", "winnipeg": "WPG", "atlanta": "ATL", "utah": "UTA"},
    # SBRO MLB uses 3-letter codes, several nonstandard.
    "mlb": {"ari": "ARI", "atl": "ATL", "bal": "BAL", "bos": "BOS", "cub": "CHC", "chc": "CHC",
            "cws": "CWS", "chw": "CWS", "cin": "CIN", "cle": "CLE", "col": "COL", "det": "DET",
            "hou": "HOU", "kan": "KC", "kc": "KC", "laa": "LAA", "lan": "LAD", "lad": "LAD", "mia": "MIA",
            "fla": "MIA", "mil": "MIL", "min": "MIN", "nym": "NYM", "nyy": "NYY", "oak": "OAK",
            "phi": "PHI", "pit": "PIT", "sdg": "SD", "sd": "SD", "sea": "SEA", "sfo": "SF", "sf": "SF",
            "stl": "STL", "tam": "TB", "tb": "TB", "tex": "TEX", "tor": "TOR", "was": "WSH",
            "wsh": "WSH"},
}
_NICK = {
    "nba": {"hawks": "ATL", "celtics": "BOS", "nets": "BKN", "hornets": "CHA", "bulls": "CHI",
            "cavaliers": "CLE", "mavericks": "DAL", "nuggets": "DEN", "pistons": "DET", "warriors": "GSW",
            "rockets": "HOU", "pacers": "IND", "clippers": "LAC", "lakers": "LAL", "grizzlies": "MEM",
            "heat": "MIA", "bucks": "MIL", "timberwolves": "MIN", "pelicans": "NOP", "knicks": "NYK",
            "thunder": "OKC", "magic": "ORL", "76ers": "PHI", "suns": "PHX", "blazers": "POR",
            "kings": "SAC", "spurs": "SAS", "raptors": "TOR", "jazz": "UTA", "wizards": "WAS"},
    "nfl": {"cardinals": "ARI", "falcons": "ATL", "ravens": "BAL", "bills": "BUF", "panthers": "CAR",
            "bears": "CHI", "bengals": "CIN", "browns": "CLE", "cowboys": "DAL", "broncos": "DEN",
            "lions": "DET", "packers": "GB", "texans": "HOU", "colts": "IND", "jaguars": "JAX",
            "chiefs": "KC", "raiders": "LV", "chargers": "LAC", "rams": "LA", "dolphins": "MIA",
            "vikings": "MIN", "patriots": "NE", "saints": "NO", "giants": "NYG", "jets": "NYJ",
            "eagles": "PHI", "steelers": "PIT", "49ers": "SF", "seahawks": "SEA", "buccaneers": "TB",
            "titans": "TEN", "commanders": "WAS"},
    "nhl": {"ducks": "ANA", "bruins": "BOS", "sabres": "BUF", "flames": "CGY", "hurricanes": "CAR",
            "blackhawks": "CHI", "avalanche": "COL", "blue jackets": "CBJ", "stars": "DAL",
            "red wings": "DET", "oilers": "EDM", "panthers": "FLA", "kings": "LAK", "wild": "MIN",
            "canadiens": "MTL", "predators": "NSH", "devils": "NJD", "islanders": "NYI", "rangers": "NYR",
            "senators": "OTT", "flyers": "PHI", "penguins": "PIT", "sharks": "SJS", "kraken": "SEA",
            "blues": "STL", "lightning": "TBL", "maple leafs": "TOR", "canucks": "VAN",
            "golden knights": "VGK", "capitals": "WSH", "jets": "WPG", "mammoth": "UTA",
            "hockey club": "UTA"},
    "mlb": {"diamondbacks": "ARI", "braves": "ATL", "orioles": "BAL", "red sox": "BOS", "cubs": "CHC",
            "white sox": "CWS", "reds": "CIN", "guardians": "CLE", "rockies": "COL", "tigers": "DET",
            "astros": "HOU", "royals": "KC", "angels": "LAA", "dodgers": "LAD", "marlins": "MIA",
            "brewers": "MIL", "twins": "MIN", "mets": "NYM", "yankees": "NYY", "athletics": "OAK",
            "phillies": "PHI", "pirates": "PIT", "padres": "SD", "mariners": "SEA", "giants": "SF",
            "cardinals": "STL", "rays": "TB", "rangers": "TEX", "blue jays": "TOR", "nationals": "WSH"},
}


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def norm_team(league: str, name: str) -> str:
    """Normalize a team name (SBRO compact, nflverse abbr, or Odds API full name) to an abbreviation.

    Unknown names (college teams) fall back to an upper-case slug so keys stay stable."""
    raw = str(name or "").strip()
    if not raw:
        return ""
    s = _slug(raw)
    hit = _SBRO.get(league, {}).get(s)
    if hit:
        return hit
    words = raw.lower().split()
    nick = _NICK.get(league, {})
    for k in (" ".join(words[-2:]), words[-1] if words else ""):
        if k in nick:
            return nick[k]
    return s.upper()


def game_key(league: str, local_date: dt.date, away: str, home: str) -> str:
    if not (away and home):
        return ""
    return f"{league}:{local_date:%Y%m%d}:{away}@{home}"


# ------------------------------------------------------------------------------------------------ odds math
def american_to_prob(o) -> float | None:
    """American odds -> raw implied probability (includes vig). None on blank/invalid."""
    o = _num(o)
    if o is None or o == 0 or -100 < o < 100:
        return None
    return 100.0 / (o + 100.0) if o > 0 else -o / (-o + 100.0)


def decimal_to_prob(d) -> float | None:
    d = _num(d)
    if d is None or d <= 1.0:
        return None
    return 1.0 / d


def vig_free_home(p_home_raw: float | None, p_away_raw: float | None) -> float | None:
    """Proportional (multiplicative) vig removal. With one side known, returns that side's raw prob."""
    if p_home_raw is not None and p_away_raw is not None:
        tot = p_home_raw + p_away_raw
        return round(p_home_raw / tot, 6) if tot > 0 else None
    if p_home_raw is not None:
        return round(p_home_raw, 6)
    if p_away_raw is not None:
        return round(1.0 - p_away_raw, 6)
    return None


def p_home_from_moneylines(home_ml, away_ml) -> float | None:
    return vig_free_home(american_to_prob(home_ml), american_to_prob(away_ml))


def _num(x) -> float | None:
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return None if x != x else float(x)  # NaN check
    s = str(x).strip().lower()
    if s in ("", "na", "nan", "nl", "none", "-", "x"):
        return None
    if s in ("pk", "pick", "p", "pk'"):
        return 0.0
    s = s.replace("½", ".5").replace("−", "-")
    s = re.sub(r"^(o|u)", "", s)
    try:
        return float(s)
    except ValueError:
        return None


def tick(source, league, gk, market_id, kind, phase, ts, p_home=None, line=None, volume=None,
         n_trades=None, interval_s=0, ext_game_id="") -> dict:
    return {"source": source, "league": league, "game_key": gk, "ext_game_id": str(ext_game_id or ""),
            "market_id": market_id,
            "market_kind": kind, "phase": phase, "ts": float(ts),
            "p_home": None if p_home is None else round(float(p_home), 6),
            "line": None if line is None else float(line), "volume": volume, "n_trades": n_trades,
            "interval_s": int(interval_s), "license": LICENSES[source], "tag": TAG}


def _open_close_ts(league: str, start_utc: dt.datetime) -> tuple[float, float]:
    t0 = start_utc.timestamp()
    return t0 - OPEN_LEAD_S.get(league, 86400), t0 - CLOSE_LEAD_S


# ------------------------------------------------------------------------------------------------ nflverse
def nflverse_rows_to_ticks(rows: Iterable[dict]) -> list[dict]:
    """nflverse games.csv rows -> closing ticks (moneyline, spread, total). Rows without lines are skipped.

    nflverse `spread_line` is positive when the home team is favored; the contract wants negative = home
    favored, so line = -spread_line."""
    out: list[dict] = []
    for r in rows:
        league = "nfl"
        gid = r.get("game_id") or ""
        day = r.get("gameday") or ""
        if not day:
            continue
        hh, mm = 13, 0
        gt = (r.get("gametime") or "").strip()
        if re.match(r"^\d{1,2}:\d{2}", gt):
            hh, mm = int(gt[:2].rstrip(":")), int(gt.split(":")[1][:2])
        local = dt.datetime.strptime(day[:10], "%Y-%m-%d").replace(hour=hh, minute=mm, tzinfo=ET)
        start = local.astimezone(UTC)
        _, t_close = _open_close_ts(league, start)
        home, away = norm_team(league, r.get("home_team")), norm_team(league, r.get("away_team"))
        gk = game_key(league, local.date(), away, home)
        p = p_home_from_moneylines(r.get("home_moneyline"), r.get("away_moneyline"))
        if p is not None:
            out.append(tick("nflverse_lines", league, gk, f"{gid}:ml", "moneyline", "close", t_close, p_home=p,
                            ext_game_id=gid))
        sp = _num(r.get("spread_line"))
        if sp is not None:
            out.append(tick("nflverse_lines", league, gk, f"{gid}:spread", "spread", "close", t_close,
                            line=-sp, ext_game_id=gid))  # cover price != win prob: p_home stays null
        tl = _num(r.get("total_line"))
        if tl is not None:
            out.append(tick("nflverse_lines", league, gk, f"{gid}:total", "total", "close", t_close, line=tl,
                            ext_game_id=gid))
    return out


# ------------------------------------------------------------------------------------------------ SBRO
SPREADLIKE_MAX = {"nfl": 30.0, "cfb": 60.0, "nba": 30.0, "cbb": 45.0}  # |value| <= this -> spread


# SBRO file-name prefixes ("nba odds 2019-20.xlsx", "ncaa football 2015-16.xlsx") -> league.
SBRO_FILE_LEAGUE = {"nba": "nba", "nfl": "nfl", "nhl": "nhl", "mlb": "mlb", "ncaaf": "cfb", "ncaab": "cbb",
                    "cfb": "cfb", "cbb": "cbb"}


def _season_years(fname: str) -> tuple[int, int] | None:
    """'nba odds 2019-20.xlsx' -> (2019, 2020); 'mlb odds 2015.xlsx' -> (2015, 2015)."""
    m = re.search(r"(20\d\d|19\d\d)\s*[-_]\s*(\d{2,4})", fname)
    if m:
        y1 = int(m.group(1))
        y2 = int(m.group(2))
        if y2 < 100:
            y2 = (y1 // 100) * 100 + y2
        return y1, y2
    m = re.search(r"(20\d\d|19\d\d)", fname)
    return (int(m.group(1)),) * 2 if m else None


def _sbro_date(mmdd, years: tuple[int, int], league: str) -> dt.date | None:
    v = _num(mmdd)
    if v is None:
        return None
    v = int(v)
    month, day = v // 100, v % 100
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    y = years[0] if month >= SEASON_START_MONTH.get(league, 1) else years[1]
    try:
        return dt.date(y, month, day)
    except ValueError:
        return None


def _col(r: dict, *names):
    """Case/space-insensitive column lookup; SBRO headers vary across files."""
    keys = {_slug(k): k for k in r.keys() if k is not None}
    for n in names:
        k = keys.get(_slug(n))
        if k is not None and r[k] not in (None, ""):
            return r[k]
    return None


def _split_spread_total(v_val, h_val, league):
    """Football/basketball Open or Close pair -> (home_spread_contract_sign, total).

    The favorite's row carries the spread, the other row the total. Smaller magnitude = spread."""
    a, b = _num(v_val), _num(h_val)
    if a is None and b is None:
        return None, None
    if a is None or b is None:
        x = a if a is not None else b
        if abs(x) <= SPREADLIKE_MAX.get(league, 30.0):
            # on the V row -> visitor favored -> home line +|x|; on the H row -> home favored -> -|x|
            return (abs(x) if a is not None else -abs(x)), None
        return None, x
    if abs(a) <= abs(b):  # visitor row holds spread -> visitor favored by |a|, home line +|a|
        return abs(a), b
    return -abs(b), a     # home row holds spread -> home favored, line negative


def sbro_rows_to_ticks(rows: Iterable[dict], league: str, years: tuple[int, int],
                       file_tag: str = "") -> list[dict]:
    """SBRO two-rows-per-game rows (V then H; N = neutral, treated in order) -> open/close ticks."""
    rows = [r for r in rows if _col(r, "VH") not in (None, "")]
    out: list[dict] = []
    i = 0
    while i + 1 < len(rows):
        v, h = rows[i], rows[i + 1]
        vh_v, vh_h = str(_col(v, "VH")).strip().upper(), str(_col(h, "VH")).strip().upper()
        if vh_v == "H" or (vh_v == "V" and vh_h != "H"):  # out of sync: skip one row
            i += 1
            continue
        i += 2
        d = _sbro_date(_col(v, "Date"), years, league)
        if d is None:
            continue
        hh, mm = DEFAULT_START_ET.get(league, (19, 0))
        local = dt.datetime(d.year, d.month, d.day, hh, mm, tzinfo=ET)
        t_open, t_close = _open_close_ts(league, local.astimezone(UTC))
        away, home = norm_team(league, _col(v, "Team")), norm_team(league, _col(h, "Team"))
        gk = game_key(league, d, away, home)
        rot = _col(v, "Rot")
        mid = f"sbro:{league}:{d:%Y%m%d}:{int(_num(rot)) if _num(rot) is not None else file_tag}:{away}@{home}"
        if league in ("nhl", "mlb"):
            for phase, col, ts in (("open", "Open", t_open), ("close", "Close", t_close)):
                p = p_home_from_moneylines(_col(h, col), _col(v, col))
                if p is not None:
                    out.append(tick("sbro", league, gk, mid + ":ml", "moneyline", phase, ts, p_home=p))
            sp_col = "PuckLine" if league == "nhl" else "RunLine"
            hs = _num(_col(h, sp_col, "Puck Line", "Run Line"))
            if hs is not None:
                out.append(tick("sbro", league, gk, mid + ":spread", "spread", "close", t_close, line=hs))
            for phase, col, ts in (("open", "OpenOU", t_open), ("close", "CloseOU", t_close)):
                t = _num(_col(h, col)) or _num(_col(v, col))
                if t is not None:
                    out.append(tick("sbro", league, gk, mid + ":total", "total", phase, ts, line=t))
        else:
            for phase, col, ts in (("open", "Open", t_open), ("close", "Close", t_close)):
                spread, total = _split_spread_total(_col(v, col), _col(h, col), league)
                if spread is not None:
                    out.append(tick("sbro", league, gk, mid + ":spread", "spread", phase, ts, line=spread))
                if total is not None:
                    out.append(tick("sbro", league, gk, mid + ":total", "total", phase, ts, line=total))
            p = p_home_from_moneylines(_col(h, "ML"), _col(v, "ML"))
            if p is not None:
                out.append(tick("sbro", league, gk, mid + ":ml", "moneyline", "close", t_close, p_home=p))
    return out


def read_table(path: str) -> list[dict]:
    """xlsx via openpyxl (pandas not needed) or csv. Returns list of header->value dicts."""
    if path.lower().endswith((".xlsx", ".xlsm")):
        import openpyxl  # optional dependency; only needed for real SBRO files
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.worksheets[0]
        it = ws.iter_rows(values_only=True)
        header = [str(h).strip() if h is not None else "" for h in next(it)]
        # SBRO leaves odds columns after PuckLine/RunLine/OpenOU/CloseOU unnamed; name them <prev>Odds.
        for j, hname in enumerate(header):
            if not hname and j:
                header[j] = header[j - 1] + "Odds"
        return [dict(zip(header, row)) for row in it if any(c is not None for c in row)]
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


# ------------------------------------------------------------------------------------------------ Odds API
ODDS_API_SPORT = {"nfl": "americanfootball_nfl", "cfb": "americanfootball_ncaaf",
                  "nba": "basketball_nba", "cbb": "basketball_ncaab", "nhl": "icehockey_nhl",
                  "mlb": "baseball_mlb"}

# Free tier: 500 credits/month; cost per call = markets x regions (regions=us, markets=h2h,spreads,totals
# -> 3). /sports and /events are free, empty responses are not charged. Snapshot plan (ET), checked by
# monthly_credits() and by a test; the collector also stops when x-requests-remaining < 30.
ODDS_API_SCHEDULE = [
    # (league, weekdays 0=Mon, local time, markets)
    ("nba", (0, 1, 2, 3, 4, 5, 6), "11:00", 3),   # morning number (after overnight moves)
    ("nba", (0, 1, 2, 3, 4, 5, 6), "18:45", 3),   # pre-tip: second snapshot gives a line jump
    ("nhl", (0, 1, 2, 3, 4, 5, 6), "18:30", 3),
    ("nfl", (3,), "19:30", 3),                    # TNF pre-kick (also captures Sunday look-ahead lines)
    ("nfl", (6,), "11:30", 3),                    # Sunday morning: whole slate
    ("nfl", (6,), "15:45", 3),                    # late-window
    ("nfl", (0,), "19:30", 3),                    # MNF
    ("cfb", (5,), "10:30", 3),
    ("cbb", (5,), "11:00", 1),                    # h2h only (huge slate, 1 credit)
]


def monthly_credits(schedule=ODDS_API_SCHEDULE, days: int = 31, in_season=None) -> int:
    in_season = in_season or {l for l, *_ in schedule}
    weeks = days / 7.0
    return int(round(sum(len(wd) * weeks * c for l, wd, _, c in schedule if l in in_season)))


def _is_american(prices: list[float]) -> bool:
    return any(abs(p) >= 100 for p in prices if p is not None) or any(p is not None and p < 0 for p in prices)


def _to_prob(price, american: bool):
    return american_to_prob(price) if american else decimal_to_prob(price)


def _iso(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def odds_api_to_ticks(events: list[dict], league: str, observed_ts: float | None = None) -> list[dict]:
    """/v4/sports/{sport}/odds response (list of events) -> one consensus tick per market per event.

    p_home = median over bookmakers of vig-free home prob (h2h); spread = median home point; total = median
    Over point. ts = observed_ts (collector's clock) or the latest bookmaker last_update."""
    out: list[dict] = []
    for ev in events or []:
        start = _iso(ev["commence_time"])
        home_name, away_name = ev.get("home_team", ""), ev.get("away_team", "")
        home, away = norm_team(league, home_name), norm_team(league, away_name)
        gk = game_key(league, start.astimezone(ET).date(), away, home)
        h2h, spreads, totals, updates = [], [], [], []
        allp = [o.get("price") for b in ev.get("bookmakers", []) for m in b.get("markets", [])
                for o in m.get("outcomes", [])]
        american = _is_american([_num(x) for x in allp])
        for b in ev.get("bookmakers", []):
            if b.get("last_update"):
                updates.append(_iso(b["last_update"]).timestamp())
            for m in b.get("markets", []):
                oc = {o.get("name"): o for o in m.get("outcomes", [])}
                ho, ao = oc.get(home_name), oc.get(away_name)
                if m.get("key") == "h2h" and ho and ao:
                    p = vig_free_home(_to_prob(ho.get("price"), american), _to_prob(ao.get("price"), american))
                    if p is not None:
                        h2h.append(p)
                elif m.get("key") == "spreads" and ho and _num(ho.get("point")) is not None:
                    spreads.append(_num(ho["point"]))
                elif m.get("key") == "totals":
                    over = oc.get("Over")
                    if over and _num(over.get("point")) is not None:
                        totals.append(_num(over["point"]))
        ts = observed_ts if observed_ts is not None else (max(updates) if updates else None)
        if ts is None:
            continue
        phase = "pregame" if ts < start.timestamp() else "live"
        eid = ev.get("id", "")
        if h2h:
            out.append(tick("odds_api", league, gk, f"{eid}:h2h", "moneyline", phase, ts,
                            p_home=statistics.median(h2h), ext_game_id=eid))
        if spreads:
            out.append(tick("odds_api", league, gk, f"{eid}:spreads", "spread", phase, ts,
                            line=statistics.median(spreads), ext_game_id=eid))
        if totals:
            out.append(tick("odds_api", league, gk, f"{eid}:totals", "total", phase, ts,
                            line=statistics.median(totals), ext_game_id=eid))
    return out


# ------------------------------------------------------------------------------------------------ output
def write_ticks(ticks: list[dict], out_root: str, source: str, extra_manifest: dict | None = None) -> dict:
    """Write out/markets/<source>/<league>/<YYYYMMDD>.jsonl (day = UTC day of ts) + manifest.json."""
    by_file: dict[str, list[dict]] = defaultdict(list)
    for t in ticks:
        day = dt.datetime.fromtimestamp(t["ts"], UTC).strftime("%Y%m%d")
        by_file[os.path.join(out_root, source, t["league"], day + ".jsonl")].append(t)
    for path, rows in by_file.items():
        os.makedirs(os.path.dirname(path), exist_ok=True)
        rows.sort(key=lambda r: (r["ts"], r["game_key"], r["market_kind"]))
        with open(path, "w") as f:
            for r in rows:
                f.write(json.dumps(r, sort_keys=True) + "\n")
    games = {t["game_key"] for t in ticks if t["game_key"]}
    man = {"source": source, "n_ticks": len(ticks), "n_games": len(games), "n_files": len(by_file),
           "phases": dict(sorted(_count(t["phase"] for t in ticks).items())),
           "kinds": dict(sorted(_count(t["market_kind"] for t in ticks).items())),
           "ts_quality": "observed" if source == "odds_api" else "approx",
           "license": LICENSES[source], "tag": TAG,
           "use": "model input only (importance/closeness/volatility); never a label, never shown"}
    man.update(extra_manifest or {})
    os.makedirs(os.path.join(out_root, source), exist_ok=True)
    with open(os.path.join(out_root, source, "manifest.json"), "w") as f:
        json.dump(man, f, indent=2, sort_keys=True)
    return man


def _count(it: Iterator[str]) -> dict:
    c: dict[str, int] = defaultdict(int)
    for x in it:
        c[x] += 1
    return c


def _files(path: str, exts: tuple[str, ...]) -> list[str]:
    if os.path.isdir(path):
        return sorted(os.path.join(path, f) for f in os.listdir(path) if f.lower().endswith(exts))
    return [path]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", required=True, choices=["sbro", "nflverse", "odds_api"])
    ap.add_argument("--in", dest="inp", required=True, help="file or directory")
    ap.add_argument("--league", default="nfl")
    ap.add_argument("--out", default="out/markets")
    ap.add_argument("--observed-ts", type=float, default=None, help="odds_api: collector UTC epoch")
    a = ap.parse_args(argv)
    ticks: list[dict] = []
    if a.source == "nflverse":
        src = "nflverse_lines"
        for p in _files(a.inp, (".csv",)):
            ticks += nflverse_rows_to_ticks(read_table(p))
    elif a.source == "sbro":
        src = "sbro"
        for p in _files(a.inp, (".xlsx", ".csv")):
            first = re.split(r"[\s_-]", os.path.basename(p).lower())[0]
            if first in SBRO_FILE_LEAGUE and SBRO_FILE_LEAGUE[first] != a.league:
                continue  # another league's file in the same folder
            yrs = _season_years(os.path.basename(p))
            if not yrs:
                print(f"skip {p}: no season in file name", file=sys.stderr)
                continue
            ticks += sbro_rows_to_ticks(read_table(p), a.league, yrs, file_tag=os.path.basename(p))
    else:
        src = "odds_api"
        for p in _files(a.inp, (".json",)):
            with open(p) as f:
                ticks += odds_api_to_ticks(json.load(f), a.league, a.observed_ts)
    man = write_ticks(ticks, a.out, src)
    print(json.dumps(man, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
