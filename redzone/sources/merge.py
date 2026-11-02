"""Multi-source merge: several feeds report the same game; trust whoever is furthest ahead.

Sources disagree mostly because one is behind, not because one is wrong. So for
each game we keep the state with the most *game progress*, and we keep a race
log of which source reported each score change first. The race log is how we
learn which feed is actually fastest for each league instead of guessing.

Guardrail: a source that jumps *backwards* (score drops, period goes back) is
ignored for that update, and repeated regressions mark it unhealthy.
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field

from redzone.models import GameState, Phase

STALE_AFTER_S = 60.0       # a live game with no fresh data this long is "stale"
UNHEALTHY_REGRESSIONS = 3  # backwards jumps before a source is benched


def progress_key(g: GameState) -> tuple:
    """Bigger = further along. Clock counts down, so negate it."""
    phase_rank = {Phase.PRE: 0, Phase.UNKNOWN: 0, Phase.LIVE: 1, Phase.POST: 2}[g.phase]
    period = g.period or 0
    half = 1 if g.half == "bottom" else 0
    clock = -(g.clock_s if g.clock_s is not None else 0.0)
    total = (g.home.score or 0) + (g.away.score or 0)
    plays = len(g.recent_plays)
    return (phase_rank, period, half, clock, total, plays)


@dataclass
class RaceEvent:
    game_id: str
    label: str            # e.g. "score 14-10"
    first_source: str
    first_at: float
    others: dict[str, float] = field(default_factory=dict)  # source -> seconds behind first


@dataclass
class SourceHealth:
    updates: int = 0
    regressions: int = 0
    errors: int = 0
    last_ok: float = 0.0
    benched: bool = False


class Merger:
    def __init__(self, clock=time.time):
        self._clock = clock
        self.games: dict[str, GameState] = {}
        self._per_source: dict[str, dict[str, GameState]] = defaultdict(dict)
        self.health: dict[str, SourceHealth] = defaultdict(SourceHealth)
        self.race: list[RaceEvent] = []
        self._race_open: dict[tuple[str, str], RaceEvent] = {}

    def record_error(self, source: str) -> None:
        self.health[source].errors += 1

    def update(self, source: str, g: GameState) -> GameState:
        now = self._clock()
        h = self.health[source]
        prev_same = self._per_source[source].get(g.game_id)
        if prev_same is not None and progress_key(g) < progress_key(prev_same):
            h.regressions += 1
            if h.regressions >= UNHEALTHY_REGRESSIONS:
                h.benched = True
            return self.games.get(g.game_id, prev_same)
        h.updates += 1
        h.last_ok = now
        self._per_source[source][g.game_id] = g
        self._note_race(source, g, now)

        cur = self.games.get(g.game_id)
        if h.benched and cur is not None:
            return cur
        if cur is None or progress_key(g) >= progress_key(cur):
            merged = g
            if cur is not None:
                merged = _fill_gaps(g, cur)
            merged.sources = dict(cur.sources) if cur else {}
            merged.sources[source] = now
            merged.updated_at = now
            self.games[g.game_id] = merged
            return merged
        # Behind the leader: only contribute fields the leader lacks.
        _fill_gaps(cur, g)
        cur.sources[source] = now
        return cur

    def _note_race(self, source: str, g: GameState, now: float) -> None:
        if g.home.score is None or g.away.score is None:
            return
        label = f"score {g.away.score}-{g.home.score}"
        key = (g.game_id, label)
        ev = self._race_open.get(key)
        if ev is None:
            ev = RaceEvent(g.game_id, label, source, now)
            self._race_open[key] = ev
            self.race.append(ev)
            if len(self.race) > 2000:
                old = self.race.pop(0)
                self._race_open.pop((old.game_id, old.label), None)
        elif source != ev.first_source and source not in ev.others:
            ev.others[source] = round(now - ev.first_at, 2)

    def is_stale(self, game_id: str) -> bool:
        g = self.games.get(game_id)
        if g is None:
            return True
        if g.phase != Phase.LIVE:
            return False
        return self._clock() - g.updated_at > STALE_AFTER_S

    def race_summary(self) -> dict[str, dict[str, float]]:
        """Per source: how often it was first, and its average lag when not first."""
        firsts: dict[str, int] = defaultdict(int)
        lags: dict[str, list[float]] = defaultdict(list)
        for ev in self.race:
            firsts[ev.first_source] += 1
            for s, lag in ev.others.items():
                lags[s].append(lag)
        out = {}
        for s in set(firsts) | set(lags):
            ls = lags.get(s, [])
            out[s] = {
                "first": firsts.get(s, 0),
                "avg_lag_s": round(sum(ls) / len(ls), 1) if ls else 0.0,
            }
        return out


def _fill_gaps(dst: GameState, src: GameState) -> GameState:
    """Copy fields that dst doesn't know but src does. Never overwrites."""
    for attr in ("home_win_prob", "spread", "period", "clock_s", "half"):
        if getattr(dst, attr) is None and getattr(src, attr) is not None:
            setattr(dst, attr, getattr(src, attr))
    if not dst.broadcasts and src.broadcasts:
        dst.broadcasts = list(src.broadcasts)
    dst.national_tv = dst.national_tv or src.national_tv
    for t_dst, t_src in ((dst.home, src.home), (dst.away, src.away)):
        for attr in ("wins", "losses", "ties", "name"):
            if not getattr(t_dst, attr) and getattr(t_src, attr):
                setattr(t_dst, attr, getattr(t_src, attr))
    ds, ss = dst.situation, src.situation
    for attr in vars(ss):
        if getattr(ds, attr) is None and getattr(ss, attr) is not None:
            setattr(ds, attr, getattr(ss, attr))
    return dst
