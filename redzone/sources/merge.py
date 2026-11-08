"""Multi-source merge: several feeds report the same game; trust whoever is furthest ahead.

Sources disagree mostly because one is behind, not because one is wrong. So for
each game we keep the state with the most *game progress*, and we keep a race
log of which source reported each score change first. The race log is how we
learn which feed is actually fastest for each league instead of guessing.

Guardrail: a source that jumps *backwards* (period or clock goes back) is ignored
for that update, and repeated regressions bench it for that game for a few minutes.
A score that drops at the same period and clock is a correction (overturned goal,
a 3 rescored as a 2, a reversed TD), not a regression: it is accepted.
"""
from __future__ import annotations

import copy
import time
from collections import defaultdict
from dataclasses import dataclass, field

from redzone.fsm import MachineMap
from redzone.machines.platform import MERGE_FEED
from redzone.models import GameState, Phase, Stoppage

STALE_AFTER_S = 60.0       # a live game with no fresh data this long is "stale"
UNHEALTHY_REGRESSIONS = 3  # backwards jumps (per game, within REGRESSION_WINDOW_S) before benching
REGRESSION_WINDOW_S = 300.0
BENCH_S = 300.0            # a bench is temporary: the source re-baselines for that game after this


def progress_key(g: GameState) -> tuple:
    """Bigger = further along. Clock counts down, so negate it."""
    phase_rank = {Phase.PRE: 0, Phase.UNKNOWN: 0, Phase.LIVE: 1, Phase.POST: 2}[g.phase]
    period = g.period or 0
    half = 1 if g.half == "bottom" else 0
    clock = -(g.clock_s if g.clock_s is not None else 0.0)
    total = (g.home.score or 0) + (g.away.score or 0)
    plays = len(g.recent_plays)
    return (phase_rank, period, half, clock, total, plays)


def _position(key: tuple) -> tuple:
    """progress_key without the score total and play count: where the game is."""
    return key[:4]


def race_labels(g: GameState) -> list[str]:
    """What the source race times: each score, and each dead ball (A2: who knows the whistle first).

    A dead ball is keyed by period and the stopped clock, so two feeds reporting the same whistle
    share a label whatever stoppage kind each calls it."""
    if g.home.score is None or g.away.score is None:
        return []
    out = [f"score {g.away.score}-{g.home.score}"]
    if g.stoppage not in (None, Stoppage.NONE, Stoppage.UNKNOWN) and g.clock_s is not None:
        out.append(f"stop p{g.period or 0} {int(g.clock_s)}")
    return out


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


STOPPAGE_OWNER_FRESH_S = 10.0   # an owner's copy this recent decides the game's stoppage
OWNER_ERROR_HOLD_S = 30.0       # [FEED-01] a source that errored this recently is skipped in the trust order


class Merger:
    def __init__(self, clock=time.time, stoppage_owner: dict | None = None):
        self._clock = clock
        # league -> source whose stoppage wins while its copy is fresh (NHL: api-web's live
        # play-by-play, ~17 s behind the ice vs ESPN's ~43 s). Without it, a stale ESPN copy at the
        # same stopped clock takes over on the tie-break and flips the whistle off and on again.
        # [FEED-01/03] A value may also be a list: the league's TRUST ORDER, fastest measured source
        # first (sources.trust_order). The first source whose copy is fresh, unbenched and not
        # erroring owns the stoppage; when it goes dark the next one takes over (failover), and a
        # disagreement with the owner is decided for the owner and logged (self.disagreements).
        self.stoppage_owner = {lg: ([v] if isinstance(v, str) else list(v)) for lg, v in (stoppage_owner or {}).items()}
        self._err_at: dict[str, float] = {}
        self.disagreements: dict[tuple[str, str], int] = defaultdict(int)   # (owner, other) -> count
        self.owner_log: list[tuple[float, str, str]] = []                    # (t, game, new owner) on change
        self._owner_now: dict[str, str] = {}
        self._owner_at: dict[str, float] = {}     # game -> when its owner's copy last arrived
        self.games: dict[str, GameState] = {}
        self._per_source: dict[str, dict[str, GameState]] = defaultdict(dict)
        self.health: dict[str, SourceHealth] = defaultdict(SourceHealth)
        self.race: list[RaceEvent] = []
        self._race_open: dict[tuple[str, str], RaceEvent] = {}
        self._regress_at: dict[tuple[str, str], list[float]] = defaultdict(list)
        # merge.feed, one per (source, game): trusted -> regressing -> benched -> (after BENCH_S) trusted.
        # SourceHealth.benched is derived from it for every source (_refresh_benched).
        self.feeds = MachineMap(MERGE_FEED, clock=clock)
        self._bench_end: dict[tuple[str, str], float] = {}

    @staticmethod
    def _fkey(key: tuple[str, str]) -> str:
        return f"{key[0]}|{key[1]}"

    @property
    def _bench_until(self) -> dict[tuple[str, str], float]:
        """(source, game) -> bench end, for the benched feeds only (a read-only view of merge.feed)."""
        return {k: u for k, u in self._bench_end.items() if self.feeds.state(self._fkey(k)) == "benched"}

    def _benched(self, key: tuple[str, str]) -> bool:
        return self.feeds.state(self._fkey(key)) == "benched"

    def record_error(self, source: str) -> None:
        self.health[source].errors += 1
        self._err_at[source] = self._clock()

    def owner(self, league, game_id: str, now: float | None = None) -> str | None:
        """[FEED-01] Who owns this game's stoppage right now: the first source in the league's trust
        order with a fresh copy, not benched for it, and no error in the last OWNER_ERROR_HOLD_S."""
        order = self.stoppage_owner.get(league)
        if not order:
            return None
        now = self._clock() if now is None else now
        for src in order:
            if src not in self._per_source or game_id not in self._per_source[src]:
                continue
            seen = self._owner_at.get((src, game_id), -1e18)
            if (now - seen <= STOPPAGE_OWNER_FRESH_S and not self._benched((src, game_id))
                    and now - self._err_at.get(src, -1e18) > OWNER_ERROR_HOLD_S):
                return src
        return None

    def update(self, source: str, g: GameState) -> GameState:
        now = self._clock()
        g = self._owned_stoppage(source, g, now)
        h = self.health[source]
        bkey = (source, g.game_id)
        self._expire_bench(source, now)
        prev_same = self._per_source[source].get(g.game_id)
        correction = False
        if prev_same is not None:
            kg, kp = progress_key(g), progress_key(prev_same)
            if _position(kg) < _position(kp):
                h.regressions += 1
                hits = [t for t in self._regress_at[bkey] if now - t < REGRESSION_WINDOW_S]
                hits.append(now)
                self._regress_at[bkey] = hits
                feed = self.feeds[self._fkey(bkey)]
                if len(hits) >= UNHEALTHY_REGRESSIONS:
                    if not feed.is_("benched"):
                        feed.to("benched", f"{len(hits)} backward jumps in {REGRESSION_WINDOW_S:.0f}s", now)
                    self._bench_end[bkey] = now + BENCH_S
                    h.benched = True
                elif not feed.is_("benched"):   # a benched feed stays benched until its bench ends
                    feed.to("regressing", f"{len(hits)} backward jump(s)", now)
                return self.games.get(g.game_id, prev_same)
            if kg < kp:
                # Same period and clock, lower score or fewer plays. A score drop with both
                # scores known is a correction; anything else (a partial payload) is ignored.
                if (g.home.score is None or g.away.score is None
                        or kg[4] >= kp[4]):
                    return self.games.get(g.game_id, prev_same)
                correction = True
        h.updates += 1
        h.last_ok = now
        feed = self.feeds.get(self._fkey(bkey))
        if feed is not None and feed.is_("regressing") and \
                not any(now - t < REGRESSION_WINDOW_S for t in self._regress_at.get(bkey, ())):
            feed.to("trusted", "its backward jumps aged out", now)
            self.feeds.prune()
        self._per_source[source][g.game_id] = g
        self._note_race(source, g, now)

        cur = self.games.get(g.game_id)
        if self._benched(bkey) and cur is not None:
            return cur
        takes = (cur is None or _takes_over(g, cur)
                 or (correction and _position(progress_key(g)) >= _position(progress_key(cur))))
        if takes:
            merged = g
            if cur is not None:
                merged = _fill_gaps(_carry_plays(g, cur), cur)
            merged.sources = dict(cur.sources) if cur else {}
            merged.sources[source] = now
            merged.updated_at = now
            self.games[g.game_id] = merged
            return merged
        # Behind the leader: only contribute fields the leader lacks.
        _fill_gaps(cur, g)
        cur.sources[source] = now
        return cur

    def _owned_stoppage(self, source: str, g: GameState, now: float) -> GameState:
        order = self.stoppage_owner.get(g.league)
        if not order:
            return g
        if source in order:
            self._owner_at[(source, g.game_id)] = now
            # Count this copy as the source's own before choosing (update() stores it right after).
            self._per_source[source].setdefault(g.game_id, g)
        owner = self.owner(g.league, g.game_id, now)
        if owner is not None and self._owner_now.get(g.game_id) != owner:
            self._owner_now[g.game_id] = owner
            self.owner_log.append((now, g.game_id, owner))
            del self.owner_log[:-500]
        if owner is None or source == owner:
            return g          # nobody fresh in the order (or this IS the owner): the feeds decide as usual
        mine = self._per_source[owner].get(g.game_id)
        if mine is None:
            return g
        if g.stoppage == mine.stoppage:
            return g
        self.disagreements[(owner, source)] += 1
        g = copy.copy(g)      # shallow: only the stoppage field differs from the caller's object
        g.stoppage = mine.stoppage
        return g

    def _expire_bench(self, source: str, now: float) -> None:
        """Lift benches older than BENCH_S. The source re-baselines for that game, so a feed
        whose clock was legitimately set back (a review) is judged afresh. Every source's
        SourceHealth.benched is refreshed, not just the one updating: a benched source that went
        quiet used to stay flagged after its bench ended."""
        for key, until in list(self._bench_until.items()):
            if until <= now:
                self.feeds[self._fkey(key)].to("trusted", "bench over: re-baselined", now)
                self._bench_end.pop(key, None)
                self._regress_at.pop(key, None)
                self._per_source[key[0]].pop(key[1], None)
        self.feeds.prune()
        self._refresh_benched()

    def _refresh_benched(self) -> None:
        benched = {k[0] for k in self._bench_until}
        for src, h in self.health.items():
            h.benched = src in benched

    def expire(self, now: float | None = None) -> None:
        """Lift benches that are over without waiting for an update (status readers call this)."""
        self._expire_bench("", self._clock() if now is None else now)

    def _note_race(self, source: str, g: GameState, now: float) -> None:
        for label in race_labels(g):
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
            if not ev.label.startswith("score"):
                continue          # the status page's race is about scores; whistles go to the recorder
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


def _takes_over(g: GameState, cur: GameState) -> bool:
    """g replaces the merged state if it's further along, or level and not missing cur's
    latest play. The tie-break matters on a stopped clock (free throws, timeouts, between
    downs): a push feed and a few-seconds-staler poll sit at the same clock and score, and
    without it they'd take turns, flipping the last play and stoppage back and forth."""
    kg, kc = progress_key(g), progress_key(cur)
    if kg != kc:
        return kg > kc
    a = g.last_play.wallclock if g.last_play else None
    b = cur.last_play.wallclock if cur.last_play else None
    return a is None or b is None or a >= b


CARRY_CLOCK_S = STALE_AFTER_S / 4   # about one poll: how far apart two readings may be for one to speak for the other


def _carry_plays(g: GameState, cur: GameState) -> GameState:
    """[grader v47, F10] A scoreboard poll (clock and score only) that is a few seconds fresher than the
    summary must not wipe the summary's last play and stoppage: the dead-ball flag flickered to UNKNOWN and the
    play timeline saw a play-less state. Carry them over from the previous leader when g has none and the two
    are the same moment (same period, same score, clocks within one poll). Never overwrites anything g knows."""
    if g.last_play is not None or g.recent_plays or (cur.last_play is None and not cur.recent_plays):
        return g
    if (g.period != cur.period or (g.home.score, g.away.score) != (cur.home.score, cur.away.score)
            or g.phase != cur.phase):
        return g
    if g.clock_s is not None and cur.clock_s is not None and abs(g.clock_s - cur.clock_s) > CARRY_CLOCK_S:
        return g
    g = copy.copy(g)   # the per-source copy of this feed stays as the feed sent it
    g.last_play, g.recent_plays = cur.last_play, list(cur.recent_plays)
    if g.stoppage == Stoppage.UNKNOWN:
        g.stoppage = cur.stoppage
    return g


def _fill_gaps(dst: GameState, src: GameState) -> GameState:
    """Copy fields that dst doesn't know but src does. Never overwrites."""
    for attr in ("home_win_prob", "spread", "period", "clock_s", "half"):
        if getattr(dst, attr) is None and getattr(src, attr) is not None:
            setattr(dst, attr, getattr(src, attr))
    if not dst.broadcasts and src.broadcasts:
        dst.broadcasts = list(src.broadcasts)
    for attr in ("leaders", "venue_info", "odds_text", "status_text", "venue"):
        if not getattr(dst, attr) and getattr(src, attr):
            setattr(dst, attr, copy.copy(getattr(src, attr)))
    if dst.spotlight is None and src.spotlight is not None:
        dst.spotlight = src.spotlight
    if dst.stakes is None and src.stakes is not None:
        dst.stakes = src.stakes
    if dst.start_time is None:
        dst.start_time = src.start_time
    dst.national_tv = dst.national_tv or src.national_tv
    for t_dst, t_src in ((dst.home, src.home), (dst.away, src.away)):
        for attr in ("wins", "losses", "ties", "name", "logo", "logo_dark", "color", "alt_color", "id"):
            if not getattr(t_dst, attr) and getattr(t_src, attr):
                setattr(t_dst, attr, getattr(t_src, attr))
    ds, ss = dst.situation, src.situation
    for attr in vars(ss):
        if getattr(ds, attr) is None and getattr(ss, attr) is not None:
            setattr(ds, attr, getattr(ss, attr))
    return dst
