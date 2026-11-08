#!/usr/bin/env python3
"""MLB Stats API (GUMBO live feed) -> play table: one row per pitch / in-game action, MLB 2008-2024 + MiLB.

Serves: EXP-1000 / EXP-097 world-model pretraining scale (baseball pitch streams). Owner: Data Planner A (crew A).

    python scripts/pbp/mlb_statsapi.py --sport 11 --seasons 2024-2005 --out DIR [--max-games N] [--keep-raw]

Endpoints (public, no key): /api/v1/schedule?sportId=N&season=Y&gameType=R|P|F|D|L|W and /api/v1.1/game/{pk}/feed/live.
sportId 1 = MLB (2008-2024 only: 2025+ is held out, schema.EVAL_FIRST_NIGHT["mlb"]); 11/12/13/14 = MiLB
AAA/AA/High-A/Single-A (level "minor", no held-out window). Plan B: personal use only (PERSONAL tag, LICENSE).
Polite: one requests.Session, UA "redzone-personal-research/1.0", <= 1 request/s (scout license record; a larger
rps is capped), backoff on 429/5xx honouring Retry-After, 404 skipped, raw gz JSON cached per game (resumable).

Mapping (GUMBO -> row): each liveData.plays.allPlays[].playEvents[] entry is one row. Pitches: kind = call code
lowercased (b, c, s, f, x, d, e, *b, ...), sub_kind = pitch type code lowercased, except the PA-ending pitch whose
sub_kind = result.eventType. Actions (type "action"/"pickoff"/"stepoff"/"no_pitch"): kind = details.eventType
(stolen_base_2b, pitching_substitution, mound_visit, ...) or the entry's type when it has none (pickoff), sub_kind =
details.code. side = possession = batting team for every row (away in the top half). Counts / outs / bases are
BEFORE the event (balls/strikes from the previous pitch's post-count; outs and bases carried from runners[] movements by
playIndex and reset to matchup.postOn* / count.outs at PA end). value = runs on the event (score delta), scores
after. x = (pX + 2 ft) / 4 ft, y = pZ / 5 ft (catcher's view, clipped 0..1; null for actions).
Why: Converts the MLB Stats API live feed into the play table, one row per action (docstring, Data A commit).
"""
from __future__ import annotations

import argparse
import gzip
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from pbp import schema  # noqa: E402

log = logging.getLogger("pbp.mlb_statsapi")

API = "https://statsapi.mlb.com/api"
UA = "redzone-personal-research/1.0"
MAX_RPS = 1.0
LICENSE = ("MLBAM copyright notice allows individual, non-commercial, non-bulk use; "
           "bulk pull only under data plan B for personal use")
LEAGUES = {1: "mlb", 11: "milb_aaa", 12: "milb_aa", 13: "milb_higha", 14: "milb_a"}
MLB_SEASONS = (2008, 2024)
SOURCE = {"id": "mlb_statsapi", "sport": "baseball",
          "leagues": {"mlb": {"sport_id": 1, "level": "pro", "seasons": list(MLB_SEASONS)},
                      "milb_aaa": {"sport_id": 11, "level": "minor", "seasons": [2005, 2025]},
                      "milb_aa": {"sport_id": 12, "level": "minor", "seasons": [2005, 2025]},
                      "milb_higha": {"sport_id": 13, "level": "minor", "seasons": [2005, 2025]},
                      "milb_a": {"sport_id": 14, "level": "minor", "seasons": [2005, 2025]}},
          "license": LICENSE, "tag": schema.PERSONAL, "data_plan": "B",
          "est_events_per_game": 290}  # ~290 pitches + ~10 actions per 9-inning MLB game
BASE_BIT = {"1B": 1, "2B": 2, "3B": 4}


def _ts(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _clip(v):
    return None if v is None else min(1.0, max(0.0, float(v)))


def _post_bases(m: dict) -> int | None:
    keys = ("postOnFirst", "postOnSecond", "postOnThird")
    if not any(k in m for k in keys):
        return None
    return sum(b for k, b in zip(keys, (1, 2, 4)) if m.get(k))


def convert(feed: dict, sport_id: int) -> list[dict]:
    """One GUMBO feed -> play-table rows (game order, seq 0..)."""
    gd, plays = feed.get("gameData", {}), feed.get("liveData", {}).get("plays", {}).get("allPlays", [])
    league = LEAGUES[sport_id]
    teams = gd.get("teams", {})
    base = dict(source="mlb_statsapi", sport="baseball", league=league, level="pro" if sport_id == 1 else "minor",
                season=int(gd.get("game", {}).get("season") or gd.get("datetime", {}).get("officialDate", "0")[:4]),
                game_id=str(feed.get("gamePk") or gd.get("game", {}).get("pk")),
                night=gd.get("datetime", {}).get("officialDate", "").replace("-", ""),
                home=teams.get("home", {}).get("abbreviation", ""), away=teams.get("away", {}).get("abbreviation", ""),
                license=LICENSE, tag=schema.PERSONAL)
    rows: list[dict] = []
    home = away = outs = bases = 0
    half = None
    for pi, p in enumerate(plays):
        ab, res, m = p.get("about", {}), p.get("result", {}), p.get("matchup", {})
        top = ab.get("isTopInning", ab.get("halfInning") == "top")
        period = 2 * int(ab.get("inning", 1)) - (1 if top else 0)
        if period != half:
            half, outs, bases = period, 0, 0
        side = "away" if top else "home"
        batter, pitcher = str(m.get("batter", {}).get("id", "")), str(m.get("pitcher", {}).get("id", ""))
        evs = p.get("playEvents", [])
        moves: dict[int, list] = {}
        for r in p.get("runners", []):
            moves.setdefault(r.get("details", {}).get("playIndex", len(evs) - 1), []).append(r)
        last_pitch = max((i for i, e in enumerate(evs) if e.get("isPitch")), default=None)
        final = len(evs) - 1
        if evs and not evs[-1].get("isPitch") and evs[-1].get("details", {}).get("eventType") == res.get("eventType"):
            last_pitch = None  # the PA ended on the action itself (e.g. caught stealing for the 3rd out)
        balls = strikes = 0
        half_ends = pi + 1 == len(plays) or plays[pi + 1].get("about", {}).get("inning") != ab.get("inning") \
            or plays[pi + 1].get("about", {}).get("isTopInning") != top
        for i, e in enumerate(evs):
            d = e.get("details", {})
            pitch = bool(e.get("isPitch"))
            if pitch:
                kind = (d.get("call", {}).get("code") or d.get("code") or "pitch").lower()
                sub = res.get("eventType", "") if i == last_pitch else (d.get("type", {}).get("code") or "").lower()
                coords = e.get("pitchData", {}).get("coordinates", {})
                px, pz = coords.get("pX"), coords.get("pZ")
                x, y = (_clip((px + 2) / 4), _clip(pz / 5)) if px is not None and pz is not None else (None, None)
                actor = batter
            else:
                kind = (d.get("eventType") or e.get("type") or "action").lower()
                sub = (d.get("code") or "").lower()
                x = y = None
                runner = next((r.get("details", {}).get("runner", {}).get("id") for r in moves.get(i, [])), None)
                actor = str(e.get("player", {}).get("id") or runner or batter)
            # scores after: actions that score carry details.home/awayScore; the PA's final event takes result's
            if d.get("homeScore") is not None:
                h, a = int(d["homeScore"]), int(d.get("awayScore", away))
            elif i == final and res.get("homeScore") is not None:
                h, a = int(res["homeScore"]), int(res.get("awayScore", away))
            else:
                h, a = home, away
            stop = "end_period" if half_ends and i == final else (
                "pitching_change" if kind == "pitching_substitution" else "none")
            rows.append(schema.row(**base, seq=len(rows), period=period, wall_ts=_ts(e.get("startTime")),
                                   home_score=h, away_score=a, side=side, possession=side, kind=kind, sub_kind=sub,
                                   actor_id=actor, actor2_id=pitcher, x=x, y=y, value=(h - home) + (a - away),
                                   outs=outs, balls=balls, strikes=strikes, bases=bases, stop=stop))
            home, away = h, a
            if pitch and e.get("count"):
                balls, strikes = int(e["count"].get("balls", balls)), int(e["count"].get("strikes", strikes))
            off = sum(BASE_BIT.get(r.get("movement", {}).get("start"), 0) for r in moves.get(i, []))
            on = sum(BASE_BIT.get(r.get("movement", {}).get("end"), 0) for r in moves.get(i, [])
                     if not r.get("movement", {}).get("isOut"))
            bases = (bases & ~off) | on
            outs += sum(1 for r in moves.get(i, []) if r.get("movement", {}).get("isOut"))
        if p.get("count", {}).get("outs") is not None:
            outs = int(p["count"]["outs"])
        pb = _post_bases(m)
        if pb is not None and ab.get("isComplete", True):
            bases = pb
    return rows


class Client:
    """Polite GET with rate limit (<= MAX_RPS), exponential backoff on 429/5xx, Retry-After, 404 -> None."""

    def __init__(self, session=None, rps: float = MAX_RPS, sleep=time.sleep, retries: int = 6):
        import requests
        self.s = session or requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Encoding": "gzip"})
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
            return r.json()
        raise RuntimeError(f"giving up on {url} after {self.retries} tries")


def schedule(client: Client, sport_id: int, season: int) -> list[int]:
    """Final regular-season + postseason gamePks (deduped: suspended games appear on two dates)."""
    pks: list[int] = []
    for gt in ("R", "P", "F", "D", "L", "W"):  # regular, postseason, and the per-round codes some seasons use
        js = client.get(f"{API}/v1/schedule", {"sportId": sport_id, "season": season, "gameType": gt}) or {}
        for day in js.get("dates", []):
            for g in day.get("games", []):
                if g.get("status", {}).get("abstractGameState") == "Final" and g["gamePk"] not in pks:
                    pks.append(g["gamePk"])
    return pks


def pull_season(sport_id: int, season: int, out_dir: Path, rps: float = MAX_RPS, max_games=None,
                keep_raw: bool = False, session=None, sleep=time.sleep) -> dict:
    """Fetch, cache, convert, validate and shard one league-season. Returns a summary (shard sidecar + counts)."""
    league = LEAGUES[sport_id]
    if sport_id == 1 and not MLB_SEASONS[0] <= season <= MLB_SEASONS[1]:
        log.warning("mlb %d outside %s (2025+ held out): skipped", season, MLB_SEASONS)
        return {"league": league, "season": season, "games": 0, "events": 0, "dropped": 0, "skipped": True}
    out_dir = Path(out_dir)
    client = Client(session, rps, sleep)
    raw_dir = out_dir / "raw" / str(sport_id) / str(season)
    raw_dir.mkdir(parents=True, exist_ok=True)
    pks = schedule(client, sport_id, season)[: max_games or None]
    rows, raws, dropped, missing = [], [], 0, 0
    for pk in pks:
        raw = raw_dir / f"{pk}.json.gz"
        if raw.exists():
            feed = json.loads(gzip.decompress(raw.read_bytes()))
        else:
            feed = client.get(f"{API}/v1.1/game/{pk}/feed/live")
            if feed is None:
                missing += 1
                continue
            raw.write_bytes(gzip.compress(json.dumps(feed, separators=(",", ":")).encode()))
        raws.append(raw)
        try:
            game = convert(feed, sport_id)
            errs = schema.validate(game)
        except Exception as ex:
            game, errs = [], [f"convert failed: {ex!r}"]
        if errs:
            dropped += 1
            log.warning("%s %d game %s dropped: %s", league, season, pk, "; ".join(errs[:3]))
            continue
        rows.extend(game)
    summary = {"league": league, "season": season, "games": 0, "events": 0, "dropped": dropped,
               "missing": missing, "scheduled": len(pks)}
    if rows:
        meta = {"source": "mlb_statsapi", "league": league, "season": season, "license": LICENSE,
                "tag": schema.PERSONAL, "data_plan": "B"}
        summary.update(schema.write_shard(rows, out_dir / league / f"{season}.parquet", meta))
        summary["dropped"], summary["missing"] = dropped, missing
        if not keep_raw:
            for f in raws:
                f.unlink(missing_ok=True)
    return summary


def _seasons(spec: str) -> list[int]:
    a, _, b = spec.partition("-")
    a, b = int(a), int(b or a)
    return list(range(a, b - 1, -1)) if a >= b else list(range(a, b + 1))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sport", type=int, required=True, choices=sorted(LEAGUES))
    ap.add_argument("--seasons", required=True, help="e.g. 2024-2005 (pulled in that order) or 2019")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--max-games", type=int)
    ap.add_argument("--rps", type=float, default=MAX_RPS)
    ap.add_argument("--keep-raw", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    for y in _seasons(a.seasons):
        s = pull_season(a.sport, y, a.out, a.rps, a.max_games, a.keep_raw)
        print(json.dumps({k: s.get(k) for k in ("league", "season", "games", "events", "dropped", "missing",
                                                "v1_tokens")}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
