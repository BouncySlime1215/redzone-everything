"""Feeds: each game's state plus how fresh it really is (contracts/feeds.py).

Why not just Merger.is_stale: that flags a live game only after 60 s with no game progress, and the
poll loop hands the merger the cached copy of a board while ESPN is down (Fetcher returns the last
good body), so the merger can't tell "nothing happened" from "we can't see". Fault drill, before:
a 30 s ESPN 503 outage was never flagged at all.

Here a game's freshness is the last time a request for it actually came back (200/304), from the
Fetcher's own clock, or the last FastCast push for it:
  fresh_at(game) = max(fetched_at(summary url), fetched_at(league scoreboard url), last push)
and a LIVE game is stale when
  - the merger says so (60 s with no progress: a feed stuck on an old state), or
  - its data is more than FEED_STALE_S (5 s) past its next due poll (hot 3 s / other 10 s), or
  - its host is backing off after a failed request and the data is already past due.
So an ESPN outage is flagged at the first failed poll (<= one poll interval) and at most 5 s after
the data was due. Stale games are never entered (Candidate.stale / Engine._entry_valid), which only
holds a switch; it never forces one.
"""
from __future__ import annotations

import time
from typing import Callable

from redzone.contracts.feeds import FeedSnapshot, GameFeed
from redzone.models import GameState, Phase
from redzone.sources.http import FEED_STALE_S, Fetcher
from redzone.sources.merge import Merger

PUSH_SOURCES = ("espn_fastcast",)


class FeedBook:
    def __init__(self, merger: Merger, fetcher: Fetcher, urls_of: Callable[[GameState], list[str]],
                 cadence_of: Callable[[str], float], clock: Callable[[], float] = time.time):
        self.merger = merger
        self.fetcher = fetcher
        self.urls_of = urls_of            # game -> the URLs its state comes from (summary, league board)
        self.cadence_of = cadence_of      # game id -> seconds between its polls (polling.hot_s / live_s)
        self._clock = clock

    def polled_stale(self, gid: str, now: float | None = None) -> bool:
        """Is the POLLED data for this live game dark (pushes ignored)? hardening.feeds merges FastCast
        pushes for exactly these games, and stops once polling is fresh again, so a push that freshens
        the game can't switch its own fallback off and on every few seconds."""
        g = self.merger.games.get(gid)
        if g is None or g.phase != Phase.LIVE:
            return False
        times = [t for t in (self.fetcher.fetched_at(u) for u in self.urls_of(g)) if t is not None]
        if not times:
            return False
        now = self._clock() if now is None else now
        age = now - max(times)
        cadence = float(self.cadence_of(gid))
        return age > cadence + FEED_STALE_S or (self._backing_off(g) and age >= cadence)

    def fresh_at(self, gid: str) -> float | None:
        g = self.merger.games.get(gid)
        if g is None:
            return None
        times = [t for t in (self.fetcher.fetched_at(u) for u in self.urls_of(g)) if t is not None]
        times += [t for src, t in (g.sources or {}).items() if src in PUSH_SOURCES]
        return max(times) if times else None

    def _backing_off(self, g: GameState) -> bool:
        return any(self.fetcher.backing_off(u) for u in self.urls_of(g))

    def stale(self, gid: str, now: float | None = None) -> bool:
        g = self.merger.games.get(gid)
        if g is None:
            return True
        if g.phase != Phase.LIVE:
            return False
        if self.merger.is_stale(gid):
            return True
        fresh = self.fresh_at(gid)
        if fresh is None:
            return False          # never fetched by URL (a test, a demo): the merger's rule decides
        now = self._clock() if now is None else now
        age = now - fresh
        cadence = float(self.cadence_of(gid))
        return age > cadence + FEED_STALE_S or (self._backing_off(g) and age >= cadence)

    def snapshot(self, now: float) -> FeedSnapshot:
        games = {}
        for gid, g in self.merger.games.items():
            fresh = self.fresh_at(gid)
            src = max((g.sources or {}).items(), key=lambda kv: kv[1], default=("", 0.0))[0] or "merged"
            games[gid] = GameFeed(state=g, fetched_at=fresh if fresh is not None else (g.updated_at or 0.0),
                                  source=src, stale=self.stale(gid, now))
        return FeedSnapshot(at=now, games=games)
