#!/usr/bin/env python3
"""EuroLeague + EuroCup live API -> play table: one row per play-by-play action, season codes 2007-2025.

Serves: EXP-1000 / EXP-097 world-model pretraining scale (pro basketball outside the NBA, FIBA rules).
Owner: Data Planner A (crew A).

    python scripts/pbp/euroleague.py --league euroleague --seasons 2026-2008 --out DIR [--max-games N] [--keep-raw]

`season` everywhere in this module is the play-table season LABEL = the year the season ends: season code E2023
(2023-24) -> season 2024, shard <out>/euroleague/2024.parquet. Competition codes: "E" EuroLeague, "U" EuroCup.

Endpoints (public, no key; the shapes the euroleague-api Python package reads; this box cannot reach the host, so
the URL templates are constants below and easy to fix):
  PBP_URL     live.euroleague.net/api/PlayByPlay?gamecode={n}&seasoncode={E2023}: TeamA/TeamB, CodeTeamA/CodeTeamB
              and quarter arrays FirstQuarter/SecondQuarter/ThirdQuarter/ForthQuarter (sic)/ExtraTime of plays with
              PLAYTYPE, PLAYER_ID, CODETEAM, MARKERTIME "MM:SS" (time LEFT in the period), NUMBEROFPLAY, MINUTE,
              POINTS_A, POINTS_B (null except on scoring plays), PLAYINFO (free text, never stored).
  HEADER_URL  live.euroleague.net/api/Header?gamecode=&seasoncode=: Date "dd/mm/yyyy", TeamA/TeamB, ScoreA/ScoreB, Live.
  V2_PBP_URL  api-live.euroleague.net/v2/... (optional, fetch_v2=True): tried only when v1 returns nothing, and
              converted only when its payload carries the same quarter arrays (its real shape is unverified).
Game codes run 1..N per season; a season stops after STOP_AFTER_EMPTY consecutive empty/404 game codes. Live
(unfinished) games are skipped.

Mapping (play -> row): home = CodeTeamA, away = CodeTeamB (in this API team A is the home team: Header's TeamA is the
host club and POINTS_A is its score). period 1-4 from the quarter array; ExtraTime plays are 5, then +1 after each
EP (end period) inside ExtraTime (or MINUTE 41-45 -> 5, 46-50 -> 6 when given). clock_s = MARKERTIME seconds (10-min
quarters, 5-min OT); a blank MARKERTIME is null, except BP = full period and EP/EG = 0. Scores = POINTS_A/POINTS_B
carried forward (a scoring play with null points adds its value to the acting side, resynced by the next non-null).
side by CODETEAM; kind = PLAYTYPE lowercased (2fgm, 3fga, ftm, d, o, to, st, as, blk, cm, rv, tout, bp, ep, eg, ...);
value 2FGM 2 / 3FGM 3 / FTM 1. stop: TOUT/TOUT_TV -> timeout, EP -> end_period (halftime after Q2), EG ->
end_period, else none. possession (derived, best effort): made FG / TO -> the other team, D / O rebound and ST ->
the actor's team, else carried. actor_id = PLAYER_ID stripped. wall_ts null (no real clock in the feed). night =
Header date (local European date) as YYYYMMDD.

Plan B: personal use only (PERSONAL tag, LICENSE). Polite: one requests.Session, UA "redzone-personal-research/1.0",
<= 1 request/s (a larger rps is capped), backoff on 429/5xx honouring Retry-After, 404 -> empty, raw gz JSON cached
per game under <out>/<league>/raw/<seasoncode>/ (resumable), deleted after the shard unless keep_raw.
Why: Converts the EuroLeague live API into the shared play table (docstring, Data A EuroLeague commit).
"""
from __future__ import annotations

import argparse
import gzip
import json
import logging
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

try:
    from pbp import schema
except ImportError:  # pragma: no cover - import paths differ between repo, tests and the Kaggle notebook
    try:
        from scripts.pbp import schema
    except ImportError:
        import schema

log = logging.getLogger("pbp.euroleague")

PBP_URL = "https://live.euroleague.net/api/PlayByPlay"            # ?gamecode={n}&seasoncode={E2023}
HEADER_URL = "https://live.euroleague.net/api/Header"             # ?gamecode={n}&seasoncode={E2023}
V2_PBP_URL = "https://api-live.euroleague.net/v2/competitions/{comp}/seasons/{seasoncode}/games/{gamecode}/playbyplay"
UA = "redzone-personal-research/1.0"
MAX_RPS = 1.0
STOP_AFTER_EMPTY = 20
LICENSE = ("Euroleague Basketball terms §8f: statistics for private, non-commercial use; never republish; "
           "plan B personal use")
COMP = {"euroleague": "E", "eurocup": "U"}
SEASON_CODES = (2007, 2025)          # season codes E2007..E2025 -> labels 2008..2026
SOURCE = {"id": "euroleague", "sport": "basketball",
          "leagues": {lg: {"competition": c, "level": "pro", "seasons": [SEASON_CODES[0] + 1, SEASON_CODES[1] + 1]}
                      for lg, c in COMP.items()},
          "license": LICENSE, "tag": schema.PERSONAL, "data_plan": "B",
          "est_events_per_game": 450}
QUARTERS = (("FirstQuarter", 1), ("SecondQuarter", 2), ("ThirdQuarter", 3), ("ForthQuarter", 4),
            ("FourthQuarter", 4), ("ExtraTime", 5))
POINTS = {"2FGM": 2, "3FGM": 3, "FTM": 1}
QUARTER_S, OT_S = 600.0, 300.0


def seasoncode(league: str, season: int) -> str:
    """Play-table season label (end year) -> API season code: ("euroleague", 2024) -> "E2023"."""
    return f"{COMP[league]}{int(season) - 1}"


def _s(v) -> str:
    return "" if v is None else str(v).strip()


def _clock(v) -> float | None:
    m, sep, s = _s(v).partition(":")
    if not sep:
        return None
    try:
        return float(int(m) * 60 + float(s))
    except ValueError:
        return None


def _int(v) -> int | None:
    try:
        return None if v is None or _s(v) == "" else int(float(v))
    except ValueError:
        return None


def _night(date: str) -> str:
    """'12/10/2023' (dd/mm/yyyy) -> '20231012'; '' when unparseable."""
    p = _s(date).split(" ")[0].replace("-", "/").split("/")
    if len(p) == 3 and all(x.isdigit() for x in p):
        d, m, y = p if len(p[2]) == 4 else (p[2], p[1], p[0])   # tolerate yyyy-mm-dd too
        return f"{int(y):04d}{int(m):02d}{int(d):02d}"
    return ""


def has_plays(pbp: dict | None) -> bool:
    return bool(pbp) and any(pbp.get(k) for k, _ in QUARTERS)


def convert(pbp: dict, header: dict, league: str, season: int, gamecode: int | str) -> list[dict]:
    """One PlayByPlay + Header payload -> play-table rows (game order, seq 0..)."""
    home_code, away_code = _s(pbp.get("CodeTeamA") or header.get("CodeTeamA")), _s(
        pbp.get("CodeTeamB") or header.get("CodeTeamB"))
    base = dict(source="euroleague", sport="basketball", league=league, level="pro", season=int(season),
                game_id=f"{seasoncode(league, season)}_{gamecode}", night=_night(header.get("Date", "")),
                home=home_code, away=away_code, license=LICENSE, tag=schema.PERSONAL)
    rows: list[dict] = []
    hs = as_ = 0
    poss = ""
    seen_fourth = False
    for key, q in QUARTERS:
        plays = pbp.get(key) or []
        if q == 4:
            if seen_fourth or not plays:
                continue
            seen_fourth = True
        period, bump = q, False
        for p in sorted(plays, key=lambda x: _int(x.get("NUMBEROFPLAY")) or 0):
            pt = _s(p.get("PLAYTYPE")).upper()
            if bump and pt != "EG":
                period, bump = period + 1, False
            if q == 5:
                minute = _int(p.get("MINUTE"))
                if minute and minute > 40:
                    period = 5 + (minute - 41) // 5
            length = QUARTER_S if period <= 4 else OT_S
            code = _s(p.get("CODETEAM"))
            side = "home" if code and code == home_code else "away" if code and code == away_code else ""
            value = POINTS.get(pt, 0)
            pa, pb = _int(p.get("POINTS_A")), _int(p.get("POINTS_B"))
            if pa is not None and pb is not None:
                hs, as_ = pa, pb
            elif value and side == "home":
                hs += value
            elif value and side == "away":
                as_ += value
            other = "away" if side == "home" else "home" if side else ""
            if side and (pt in ("2FGM", "3FGM", "TO")):
                poss = other
            elif side and pt in ("D", "O", "ST"):
                poss = side
            clock = _clock(p.get("MARKERTIME"))
            if clock is None:
                clock = length if pt == "BP" else 0.0 if pt in ("EP", "EG") else None
            if pt in ("TOUT", "TOUT_TV"):
                stop = "timeout"
            elif pt == "EP":
                stop = "halftime" if period == 2 else "end_period"
            elif pt == "EG":
                stop = "end_period"
            else:
                stop = "none"
            rows.append(schema.row(**base, seq=len(rows), period=period, clock_s=clock, home_score=hs,
                                   away_score=as_, side=side, possession=poss, kind=pt.lower(),
                                   actor_id=_s(p.get("PLAYER_ID")), value=float(value), stop=stop))
            if q == 5 and pt == "EP" and not _int(p.get("MINUTE")):
                bump = True          # no MINUTE: the next OT starts after this marker (EG stays in this period)
    return rows


class Client:
    """Polite GET with rate limit (<= MAX_RPS), exponential backoff on 429/5xx, Retry-After, 404/non-JSON -> None."""

    def __init__(self, session=None, rps: float = MAX_RPS, sleep=time.sleep, retries: int = 6):
        if session is None:
            import requests
            session = requests.Session()
        self.s = session
        if hasattr(self.s, "headers"):
            self.s.headers.update({"User-Agent": UA, "Accept": "application/json", "Accept-Encoding": "gzip"})
        self.gap, self.sleep, self.retries, self.last = 1.0 / min(max(rps, 1e-3), MAX_RPS), sleep, retries, None

    def get(self, url: str, params: dict | None = None) -> dict | None:
        for attempt in range(self.retries):
            if self.last is not None:
                wait = self.gap - (time.monotonic() - self.last)
                if wait > 0:
                    self.sleep(wait)
            self.last = time.monotonic()
            try:
                r = self.s.get(url, params=params, timeout=30)
            except Exception as ex:  # connection reset / timeout: back off and retry
                log.warning("GET %s failed (%s), retry %d", url, ex, attempt)
                self.sleep(2 ** attempt)
                continue
            if r.status_code == 404:
                return None
            if r.status_code == 429 or r.status_code >= 500:
                ra = (getattr(r, "headers", None) or {}).get("Retry-After")
                self.sleep(float(ra) if ra and str(ra).replace(".", "", 1).isdigit() else 2 ** attempt)
                continue
            r.raise_for_status()
            try:
                js = r.json()
            except ValueError:
                try:
                    js = json.loads((getattr(r, "text", "") or "").strip().lstrip("﻿"))
                except ValueError:
                    return None          # an empty body / HTML error page = no game at this code
            return js if isinstance(js, dict) else None
        raise RuntimeError(f"giving up on {url} after {self.retries} tries")


def _fetch(client: Client, league: str, code: str, n: int, fetch_v2: bool) -> dict | None:
    q = {"gamecode": n, "seasoncode": code}
    pbp = client.get(PBP_URL, q)
    if not has_plays(pbp) and fetch_v2:
        v2 = client.get(V2_PBP_URL.format(comp=COMP[league], seasoncode=code, gamecode=n))
        pbp = v2 if has_plays(v2) else pbp
    if not has_plays(pbp):
        return None
    return {"pbp": pbp, "header": client.get(HEADER_URL, q) or {}}


def pull_season(league: str, season: int, out_dir, session=None, sleep=time.sleep, max_games=None,
                keep_raw: bool = False, rps: float = MAX_RPS, fetch_v2: bool = False) -> dict:
    """Probe game codes 1.. for one league-season, cache, convert, validate and shard. Returns the summary
    (shard sidecar + games / dropped / missing / live / probed)."""
    out_dir = Path(out_dir)
    code = seasoncode(league, season)
    client = Client(session, rps, sleep)
    raw_dir = out_dir / league / "raw" / code
    raw_dir.mkdir(parents=True, exist_ok=True)
    rows, raws = [], []
    dropped = live = gap = missing = got = n = 0
    while gap < STOP_AFTER_EMPTY and not (max_games and got >= max_games):
        n += 1
        raw = raw_dir / f"{n}.json.gz"
        if raw.exists():
            js = json.loads(gzip.decompress(raw.read_bytes()))
        else:
            js = _fetch(client, league, code, n, fetch_v2)
            if js is None:
                gap += 1
                continue
            raw.write_bytes(gzip.compress(json.dumps(js, separators=(",", ":")).encode()))
        missing += gap           # empties followed by a real game are gaps inside the season
        gap = 0
        raws.append(raw)
        got += 1
        if js["header"].get("Live") is True or js["pbp"].get("Live") is True:
            live += 1
            raws.pop()
            raw.unlink(missing_ok=True)      # unfinished: refetch next run
            continue
        try:
            game = convert(js["pbp"], js["header"], league, season, n)
            errs = schema.validate(game)
        except Exception as ex:
            game, errs = [], [f"convert failed: {ex!r}"]
        if errs:
            dropped += 1
            log.warning("%s %s game %d dropped: %s", league, code, n, "; ".join(errs[:3]))
            continue
        rows.extend(game)
    summary = {"league": league, "season": season, "seasoncode": code, "games": 0, "events": 0,
               "dropped": dropped, "missing": missing, "live": live, "probed": n}
    if rows:
        meta = {"source": "euroleague", "league": league, "season": season, "seasoncode": code, "license": LICENSE,
                "tag": schema.PERSONAL, "data_plan": "B"}
        summary.update(schema.write_shard(rows, out_dir / league / f"{season}.parquet", meta))
        summary.update(dropped=dropped, missing=missing, live=live, probed=n)
        if not keep_raw:
            shutil.rmtree(raw_dir, ignore_errors=True)
    return summary


def _seasons(spec: str) -> list[int]:
    a, _, b = spec.partition("-")
    a, b = int(a), int(b or a)
    return list(range(a, b - 1, -1)) if a >= b else list(range(a, b + 1))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--league", required=True, choices=sorted(COMP))
    ap.add_argument("--seasons", required=True, help="season labels (end year), e.g. 2026-2008 or 2024")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--max-games", type=int)
    ap.add_argument("--rps", type=float, default=MAX_RPS)
    ap.add_argument("--keep-raw", action="store_true")
    ap.add_argument("--v2", action="store_true", help="also try the newer api-live v2 feed when v1 is empty")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    for y in _seasons(a.seasons):
        s = pull_season(a.league, y, a.out, max_games=a.max_games, keep_raw=a.keep_raw, rps=a.rps, fetch_v2=a.v2)
        print(json.dumps({k: s.get(k) for k in ("league", "season", "seasoncode", "games", "events", "dropped",
                                                "missing", "v1_tokens")}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
