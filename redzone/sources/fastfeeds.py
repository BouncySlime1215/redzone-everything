"""[tracker item 5, Nov 5] Fast league feeds in SHADOW: polled faster than ESPN, logged, never merged.

Three public endpoints, each on its own cadence (config `fast_feeds`, all OFF by default):

    NBA  liveData play-by-play (S3 mirror of cdn.nba.com)   every fast_feeds.nba_s   (1 s; off by default:
         lane 2's polling.league_fast already polls it at 1 s into plays.jsonl, #326)
    NHL  api-web.nhle.com play-by-play                       every fast_feeds.nhl_s   (2 s)
    MLB  statsapi.mlb.com feed/live                          every fast_feeds.mlb_s   (3 s)

Each new event is written once to data/recordings/<day>/fast_feeds.jsonl with the moment WE first saw
it (`seen`) and, where the feed has one, the moment it happened (`real`: NBA timeActual, MLB pitch /
at-bat endTime; the NHL feed has none). ESPN's own first-seen times are already in plays.jsonl from the
live poll, so `scripts/fast_feed_race.py` lines the two up per league: that is the lag race crew A
measures tonight.

Politeness: its own Fetcher (so a shadow backoff never blinds the live poll's cache), ETag /
If-Modified-Since on every request (a quiet second costs a 304), per-host backoff on 403/429/5xx
(redzone/sources/http.py), at most fast_feeds.max_games games per league, and nothing is polled for a
league with no live game. Nothing here reaches the merger, the decider or the TV.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

from redzone.models import GameState, League, Phase
from redzone.sources import leagues
from redzone.sources.espn import _get, _int
from redzone.sources.http import Fetcher

log = logging.getLogger(__name__)
FAST_LOG = "fast_feeds.jsonl"
KEEP_EVENTS = 60      # only the newest events of each poll are looked at (a full game has ~500)

# ESPN abbreviation -> MLB statsapi abbreviation, where they differ.
MLB_ABBR = {"CHW": "CWS", "ARI": "AZ"}


# --- per-feed event parsers (pure: payload + first-seen book -> rows) -------------------------------

def nba_rows(g: GameState, pbp, seen: dict[str, float], now: float) -> list[dict]:
    return [{"league": "nba", "game": g.game_id, "source": "nba_cdn_fast", "event": p.id,
             "seen": round(p.seen_at or now, 2), "real": round(p.wallclock, 2) if p.wallclock else None,
             "period": p.period, "clock": p.clock, "kind": p.kind, "score": list(p.score) if p.score else None,
             "text": (p.text or "")[:80]} for p in leagues.nba_plays(pbp, seen, now)]


def nhl_rows(g: GameState, pbp, seen: dict[str, float], now: float) -> list[dict]:
    return [{"league": "nhl", "game": g.game_id, "source": "nhl_api_fast", "event": p.id,
             "seen": round(p.seen_at or now, 2), "real": None, "period": p.period, "clock": p.clock,
             "kind": p.kind, "score": None, "text": (p.text or "")[:80]} for p in leagues.nhl_plays(pbp, seen, now)]


def mlb_rows(g: GameState, live, seen: dict[str, float], now: float) -> list[dict]:
    """Every pitch / action (playEvents) and every finished at-bat in statsapi's feed/live, as rows.
    The first look at a game is history (remembered, not returned), like nba_plays / nhl_plays."""
    plays = (_get(live, "liveData", "plays", "allPlays") or []) if isinstance(live, dict) else []
    found: list[tuple[str, dict, dict]] = []
    for ab in plays[-KEEP_EVENTS:]:
        i = ab.get("about", {}).get("atBatIndex", ab.get("atBatIndex"))
        if i is None:
            continue
        for ev in ab.get("playEvents") or []:
            if ev.get("index") is not None:
                found.append((f"mlb{i}.{ev['index']}", ab, ev))
        if _get(ab, "about", "isComplete"):
            found.append((f"mlb{i}.end", ab, {}))
    if not seen:
        seen.update({k: 0.0 for k, _, _ in found})
        seen.setdefault("_primed", 0.0)
        return []
    out = []
    for key, ab, ev in found:
        t = seen.setdefault(key, now)
        if not t:
            continue
        about = ab.get("about") or {}
        real = leagues._iso_ts(ev.get("endTime") or ev.get("startTime")) if ev else leagues._iso_ts(about.get("endTime"))
        text = (_get(ev, "details", "description") if ev else _get(ab, "result", "description")) or ""
        kind = (_get(ev, "details", "event") or _get(ev, "type") or "pitch") if ev else (_get(ab, "result", "event") or "at_bat")
        out.append({"league": "mlb", "game": g.game_id, "source": "mlb_stats_fast", "event": key,
                    "seen": round(t, 2), "real": round(real, 2) if real else None,
                    "period": _int(about.get("inning")), "clock": None, "kind": str(kind)[:30],
                    "score": None, "text": str(text)[:80]})
    return out


def mlb_index(schedule: dict) -> dict[tuple[str, str], dict]:
    """(away, home) abbreviations -> game from /api/v1/schedule?hydrate=team."""
    out = {}
    for d in _get(schedule, "dates") or []:
        for gm in d.get("games") or []:
            a = _get(gm, "teams", "away", "team", "abbreviation")
            h = _get(gm, "teams", "home", "team", "abbreviation")
            if a and h:
                out[(a.upper(), h.upper())] = gm
    return out


def mlb_match(g: GameState, idx: dict) -> dict | None:
    return idx.get((MLB_ABBR.get(g.away.abbr, g.away.abbr).upper(), MLB_ABBR.get(g.home.abbr, g.home.abbr).upper()))


# --- the poller ----------------------------------------------------------------------------------

class FastFeeds:
    """Polls the three feeds for live games while `fast_feeds.enabled` is on; idles otherwise.

    games:  () -> {game id: GameState} (the merger's board; read only)
    write:  rows -> None (Recorder.fast_feed)"""

    def __init__(self, cfg, games: Callable[[], dict], write: Callable[[list[dict]], None],
                 fetcher: Fetcher | None = None, clock: Callable[[], float] = time.time):
        self.cfg = cfg
        self._games = games
        self._write = write
        self._fetcher = fetcher
        self._clock = clock
        self._seen: dict[tuple[str, str], dict[str, float]] = {}    # (source, game) -> event -> first seen
        self._ids: dict[str, str] = {}                              # our game id -> the league feed's id
        self.counts: dict[str, int] = {}                            # source -> rows written (status)

    def on(self) -> bool:
        return bool(self.cfg.get("fast_feeds.enabled", False))

    def every(self, league: League) -> float:
        return float(self.cfg.get(f"fast_feeds.{league.value}_s"))

    def _fetch(self) -> Fetcher:
        if self._fetcher is None:
            self._fetcher = Fetcher()
        return self._fetcher

    def live(self, league: League) -> list[GameState]:
        if league.value not in (self.cfg.get("fast_feeds.leagues") or []):
            return []
        gs = [g for g in (self._games() or {}).values() if g.league == league and g.phase == Phase.LIVE]
        return sorted(gs, key=lambda g: g.game_id)[:int(self.cfg.get("fast_feeds.max_games"))]

    def _emit(self, rows: list[dict]) -> None:
        if not rows:
            return
        for r in rows:
            self.counts[r["source"]] = self.counts.get(r["source"], 0) + 1
        try:
            self._write(rows)
        except Exception:
            log.exception("fast feeds: could not write %d rows", len(rows))

    async def tick(self) -> None:
        await asyncio.gather(self._nba(), self._nhl(), self._mlb(), return_exceptions=True)

    async def _nba(self) -> None:
        gs = self.live(League.NBA)
        if not gs:
            return
        f, every = self._fetch(), self.every(League.NBA)
        sb = await f.get_json(leagues.NBA_SCOREBOARD, min_interval=float(self.cfg.get("fast_feeds.board_s")))
        idx = leagues.nba_index(sb) if sb else {}

        async def one(g: GameState):
            gm = leagues.nba_match(g, idx)
            if not gm or not gm.get("gameId") or gm.get("gameStatus") != 2:
                return
            pbp = await f.get_json(leagues.NBA_PBP.format(id=gm["gameId"]), min_interval=every * 0.9)
            if pbp:
                self._emit(nba_rows(g, pbp, self._seen.setdefault(("nba", g.game_id), {}), self._clock()))
        await asyncio.gather(*(one(g) for g in gs))

    async def _nhl(self) -> None:
        gs = self.live(League.NHL)
        if not gs:
            return
        f, every = self._fetch(), self.every(League.NHL)
        today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
        sc = await f.get_json(leagues.NHL_SCORE.format(date=today), min_interval=float(self.cfg.get("fast_feeds.board_s")))
        idx = leagues.nhl_index(sc) if sc else {}

        async def one(g: GameState):
            gm = leagues.nhl_match(g, idx)
            if not gm or gm.get("id") is None:
                return
            pbp = await f.get_json(leagues.NHL_PBP.format(id=gm["id"]), min_interval=every * 0.9)
            if pbp:
                self._emit(nhl_rows(g, pbp, self._seen.setdefault(("nhl", g.game_id), {}), self._clock()))
        await asyncio.gather(*(one(g) for g in gs))

    async def _mlb(self) -> None:
        gs = self.live(League.MLB)
        if not gs:
            return
        f, every = self._fetch(), self.every(League.MLB)
        today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
        sched = await f.get_json(leagues.MLB_SCHEDULE.format(date=today), min_interval=float(self.cfg.get("fast_feeds.board_s")))
        idx = mlb_index(sched) if sched else {}

        async def one(g: GameState):
            gm = mlb_match(g, idx)
            if not gm or gm.get("gamePk") is None:
                return
            live = await f.get_json(leagues.MLB_LIVE.format(pk=gm["gamePk"]), min_interval=every * 0.9)
            if live:
                self._emit(mlb_rows(g, live, self._seen.setdefault(("mlb", g.game_id), {}), self._clock()))
        await asyncio.gather(*(one(g) for g in gs))

    async def run(self) -> None:
        """Forever: poll while on, at a quarter of the fastest cadence; check the switch every few
        seconds while off (the setting can flip at runtime)."""
        while True:
            try:
                if self.on():
                    await self.tick()
                    pause = min(self.every(lg) for lg in (League.NBA, League.NHL, League.MLB)) / 4
                else:
                    pause = float(self.cfg.get("fast_feeds.board_s")) / 3
            except Exception:
                log.exception("fast feeds tick")
                pause = float(self.cfg.get("fast_feeds.board_s")) / 3
            await asyncio.sleep(pause)
