"""What's on your screen right now, estimated from the data and the stream delay.

Event times come from the feed's wallclock. When a feed has no wallclock,
we retain an interval covering its possible arrival lag, not one guessed time.
The delay is how far your screen runs behind real life; the "I just saw it"
button measures it directly (press_time - wallclock of the play).

    event airs on screen at  t + delay  (± 2·sigma)

This estimates what may be airing when the data runs ahead of the stream.
It does not inspect TV pixels, identify a broadcast replay, or turn a media
player's PLAYING state into evidence that the ball is live.
"""
from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field

from redzone.models import REAL_BREAKS, ByLeague, GameState, League, Phase, Stoppage, pro_period

# Leagues where the ball/puck is live continuously; only official stoppages are gaps.
CONTINUOUS = {League.NBA, League.NHL, League.CBB, League.WCBB}
FEED_LAG_GUESS_S = 6.0  # used only when a feed gives no wallclock
PLAY_LEN_S = ByLeague({League.NFL: 10.0, League.MLB: 8.0, League.NBA: 0.0, League.NHL: 0.0})
AFTER_PLAY_BUFFER_S = 3.0
FIRST_POLL_RECENT_S = 60.0  # on a game's first poll, plays newer than this may not have aired yet
# [mid-play guard] A play with no wallclock happened somewhere in [arrived - MAX, arrived - MIN].
# Timing it at one guessed point can put its on-screen window in the wrong place entirely.
FEED_LAG_MIN_S = 2.0
FEED_LAG_MAX_S = 30.0
LAG_EST_DEFAULT_S = 10.0   # feed lag assumed until we've measured some
# The least doubt "I just saw it" taps can earn. Taps that agree to the second still share a bias
# (reaction time, the feed's wallclock rounding): stated 2 s, a real 6 s error cut 3 plays mid-play
# (eval.py --sigma 2 --true-delay 41); 4 s held 0 cuts out to 8 s of error for ~0.3 pt dead air.
MIN_TAP_SIGMA_S = 4.0
STALE_DATA_S = 20.0        # older than this, "no play in the data" no longer means "no play on screen"
# Conservative break durations used by the model (stoppage entry -> next snap). From 270 archived
# NFL games: end of quarter p1 171 s, halftime p1 816 s, two-minute warning p1 55 s. Timeouts and
# score breaks can be 5-15 s in the data, so they get no floor. Used only when we can't see the resume;
# these empirical bounds are not direct observation of the broadcast.
MIN_BREAK_S = ByLeague({
    League.NFL: {Stoppage.END_PERIOD: 120.0, Stoppage.HALFTIME: 800.0,
                 Stoppage.TWO_MINUTE_WARNING: 50.0},
    League.NBA: {Stoppage.HALFTIME: 800.0},
    # NHL 1st/2nd intermission: 5th percentile 1120 s over 190 archived intermissions.
    League.NHL: {Stoppage.INTERMISSION: 1080.0},
    # A baseball inning change can be shorter than an NFL quarter break.
    # No minimum is assumed without feed coverage of the current screen.
})
# NFL halftime p5 829 s (200 games), NBA halftime p5 931 s (92 games): HALFTIME 800 is safe for both.


# Remembered taps ("I just saw it"), shared across channels and nights. A tap times one tune of one
# channel; another channel of the same app, or the same channel another night, lands a few seconds
# differently behind the live edge, so those estimates carry an extra margin. Too LOW a delay cuts
# plays mid-play, so stale or disagreeing taps always widen the interval, never narrow it.
TAP_HISTORY_S = 30 * 86400     # taps older than this are forgotten
TAP_HALF_LIFE_S = 7 * 86400    # a week-old tap counts half as much as tonight's
TAP_KEEP = 12                  # most recent taps used per channel / per app
CROSS_NIGHT_S = 6 * 3600       # newest tap older than this: it was measured on another night
STALE_TAP_S = 7 * 86400        # newest tap older than this: stale
SHARED_MARGIN_S = 2.0          # app-level or another night's estimate
STALE_MARGIN_S = 4.0           # stale estimate
SETTLED_SAMPLES = 3            # this many taps for an app ...
SETTLED_SPREAD_S = 6.0         # ... agreeing within this (half the range): stop asking for taps
SETTLED_DRIFT_S = 4.0          # settled delays proved stable: doubt grows to at most this
# Delay measured automatically from show changes on the quarter hour (redzone/screen/boundary.py).
# Weaker than a tap: a network may switch the title a few seconds off the hour, a show may start
# late. Each sample counts with this sigma; one alone never narrows the interval below the default.
BOUNDARY_SIGMA_S = 6.0
BOUNDARY_MIN_SAMPLES = 2
# Delay measured from the broadcast's own score bug (redzone/screen/scorebug.py): the first frame
# showing a new score, against the feed wallclock of the play that made it. Frame-accurate to half
# the ~2 s capture gap, so stronger than a tap: this many agreeing samples (pooled with any taps)
# lower the floor to SCREEN_SIGMA_S and settle the app on their own.
SCREEN_SIGMA_S = 4.0


@dataclass
class Delay:
    mu: float
    sigma: float
    calibrated_at: float | None = None
    samples: deque = field(default_factory=lambda: deque(maxlen=TAP_KEEP))
    source: str = "default"    # "tap" | "screen" (this channel) | "app" (another channel of the same app) | "boundary" | "default"
    drift_cap: float = 6.0
    live: float | None = None  # this tune's own delay from the channel's stream origin (boundary.py)

    def current_sigma(self, now: float) -> float:
        if self.calibrated_at is None:
            return self.sigma
        # Stream delay is stable unless the stream buffers (see buffered()); doubt grows slowly.
        drift = min(self.drift_cap, max(0.0, now - self.calibrated_at) / 900.0)
        return self.sigma + drift


def _estimate(taps: list[tuple[float, float]], now: float) -> tuple[float, float, float]:
    """(mu, spread, newest ts) of the most recent taps: recency-weighted upper median (one sloppy
    tap can't wreck it; ties lean late, the safe side) and half the range they cover."""
    recent = sorted(taps)[-TAP_KEEP:]
    w = {i: 0.5 ** (max(0.0, now - ts) / TAP_HALF_LIFE_S) for i, (ts, _) in enumerate(recent)}
    order = sorted(range(len(recent)), key=lambda i: recent[i][1])
    half, acc, mu = sum(w.values()) / 2, 0.0, recent[order[-1]][1]
    for i in order:
        acc += w[i]
        if acc > half:
            mu = recent[i][1]
            break
    vals = [x for _, x in recent]
    spread = (max(vals) - min(vals)) / 2 if len(vals) > 1 else 6.0
    return mu, spread, recent[-1][0]


class DelayBook:
    """Per-channel delay estimates. Key is the tuner target, e.g. 'youtubetv:espn'.

    Each channel uses its own taps; with none, the pooled taps of its app (all 'youtubetv:*'); with
    none of those, the config default. Taps persist in the store and are reloaded at start (load)."""

    def __init__(self, defaults: dict[str, float], sigma_default: float, clock=time.time):
        self._defaults = defaults
        self._sigma_default = sigma_default
        self._clock = clock
        self.book: dict[str, Delay] = {}
        self.taps: dict[str, list[tuple[float, float]]] = {}   # target -> [(ts, sample)]
        self.bounds: dict[str, list[tuple[float, float]]] = {}  # target -> [(ts, sample)] from show changes
        self.screens: dict[str, list[tuple[float, float]]] = {}  # target -> [(ts, sample)] from the score bug
        self.live: dict[str, float] = {}                         # target -> this tune's delay (origin model)

    @staticmethod
    def app(target: str) -> str:
        return target.split(":", 1)[0]

    def _app_taps(self, app: str) -> list[tuple[float, float]]:
        """Direct measurements for this app: taps and score-bug samples, all its channels."""
        return [x for d in (self.taps, self.screens) for t, v in d.items() if self.app(t) == app for x in v]

    def _own(self, target: str) -> list[tuple[float, float]]:
        return (self.taps.get(target) or []) + (self.screens.get(target) or [])

    def _floor(self, pool: list[tuple[float, float]]) -> float:
        """The tap floor, or the score-bug floor once enough of the recent pool came from the bug."""
        screen = {x for v in self.screens.values() for x in v}
        n = sum(1 for x in sorted(pool)[-TAP_KEEP:] if x in screen)
        return SCREEN_SIGMA_S if n >= SETTLED_SAMPLES else MIN_TAP_SIGMA_S

    def get(self, target: str) -> Delay:
        if target not in self.book:
            self.book[target] = Delay(self._defaults.get(self.app(target), 40.0), self._sigma_default)
            self._refresh(target)
        return self.book[target]

    def _refresh(self, target: str) -> None:
        d, now = self.book[target], self._clock()
        own = self._own(target)
        taps = own or self._app_taps(self.app(target))
        if not taps:
            self._refresh_boundary(target)   # taps win; without any, show changes may measure it
            return
        mu, spread, newest = _estimate(taps, now)
        sigma = max(self._floor(taps), spread)   # contradicting taps widen the interval
        age = now - newest
        if age > STALE_TAP_S:
            sigma += STALE_MARGIN_S
        elif not own or age > CROSS_NIGHT_S:
            sigma += SHARED_MARGIN_S
        d.mu, d.sigma, d.calibrated_at = mu, sigma, newest
        d.source = ("tap" if self.taps.get(target) else "screen") if own else "app"
        d.samples.clear()
        d.samples.extend(x for _, x in sorted(taps)[-TAP_KEEP:])
        d.drift_cap = SETTLED_DRIFT_S if self.settled(self.app(target)) else 6.0
        d.live = None

    def _app_bounds(self, app: str) -> list[tuple[float, float]]:
        return [x for t, v in self.bounds.items() if self.app(t) == app for x in v]

    def _refresh_boundary(self, target: str) -> None:
        """No taps for this app: use show-change samples (this channel's, else the app's) once there
        are two. Never below the boundary sigma (> the tap floor); disagreeing samples widen it."""
        d, now = self.book[target], self._clock()
        own = self.bounds.get(target) or []
        pool = own if len(own) >= BOUNDARY_MIN_SAMPLES else self._app_bounds(self.app(target))
        if len(pool) < BOUNDARY_MIN_SAMPLES:
            return
        mu, spread, newest = _estimate(pool, now)
        sigma = max(MIN_TAP_SIGMA_S, BOUNDARY_SIGMA_S, spread)
        age = now - newest
        if age > STALE_TAP_S:
            sigma += STALE_MARGIN_S
        elif pool is not own or age > CROSS_NIGHT_S:
            sigma += SHARED_MARGIN_S
        d.mu, d.sigma, d.calibrated_at = mu, sigma, newest
        d.source = "boundary"
        d.samples.clear()
        d.samples.extend(x for _, x in sorted(pool)[-TAP_KEEP:])
        d.drift_cap = 6.0
        # This tune's own delay (origin model) re-centres the interval when it agrees with the
        # pooled samples; it never narrows it.
        live = self.live.get(target)
        d.live = live if live is not None and abs(live - mu) <= 2 * sigma else None
        if d.live is not None:
            d.mu = d.live

    def boundary_count(self, app: str) -> int:
        """Show-change samples remembered for this app (all its channels)."""
        return len(self._app_bounds(app))

    def boundary(self, target: str, sample_s: float, ts: float | None = None) -> Delay:
        """One delay sample measured from a show change on the quarter hour (no tap)."""
        d = self.get(target)
        if not self._add(target, self._clock() if ts is None else ts, sample_s, self.bounds):
            return d
        self._refresh_app(self.app(target))
        return d

    def screen(self, target: str, sample_s: float, ts: float | None = None) -> Delay:
        """One delay sample from the score bug: a new score's first frame minus its play's wallclock."""
        d = self.get(target)
        if not self._add(target, self._clock() if ts is None else ts, sample_s, self.screens):
            return d
        self._refresh_app(self.app(target))
        return d

    def set_live(self, target: str, delay_s: float | None) -> None:
        """This tune's own delay from the channel's stream origin, or None when it's unknown."""
        if delay_s is None:
            if self.live.pop(target, None) is None:
                return
        else:
            self.live[target] = delay_s
        if target in self.book:
            self._refresh(target)

    def _refresh_app(self, app: str) -> None:
        for t in self.book:
            if self.app(t) == app:
                self._refresh(t)

    def settled(self, app: str) -> bool:
        """Enough recent, agreeing taps (or score-bug samples) for this app that asking for more is nagging."""
        taps = sorted(self._app_taps(app))[-TAP_KEEP:]
        if len(taps) < SETTLED_SAMPLES or self._clock() - taps[-1][0] > STALE_TAP_S:
            return False
        vals = [x for _, x in taps]
        return (max(vals) - min(vals)) / 2 <= SETTLED_SPREAD_S

    def measured(self, target: str) -> bool:
        """This channel was tapped itself, or its app is settled: no need to ask for a tap."""
        return bool(self._own(target)) or self.settled(self.app(target))

    def _add(self, target: str, ts: float, sample_s: float, into: dict | None = None) -> bool:
        into = self.taps if into is None else into
        if not 2.0 <= sample_s <= 180.0 or ":" not in target:
            return False
        into.setdefault(target, []).append((ts, sample_s))
        into[target] = sorted(into[target])[-TAP_KEEP:]
        return True

    def load(self, rows) -> int:
        """Samples remembered from earlier runs: (ts, target, sample[, kind]) rows from the store.
        kind 'tap' ("I just saw it"; older rows have none), 'boundary' (a show change) or 'screen'
        (the score bug)."""
        cutoff, n = self._clock() - TAP_HISTORY_S, 0
        for row in rows:
            ts, target, sample = row[:3]
            kind = (row[3] if len(row) > 3 else None) or "tap"
            into = {"tap": self.taps, "boundary": self.bounds, "screen": self.screens}.get(kind)
            if into is not None and ts is not None and ts >= cutoff and target and sample is not None:
                n += self._add(str(target), float(ts), float(sample), into)
        for app in {self.app(t) for t in (*self.taps, *self.bounds, *self.screens)}:
            self._refresh_app(app)
        return n

    def calibrate(self, target: str, sample_s: float) -> Delay:
        """One "I just saw it" measurement. Outliers are clipped, not trusted blindly."""
        d = self.get(target)
        if not self._add(target, self._clock(), sample_s):
            return d
        self._refresh_app(self.app(target))   # every channel of this app without its own taps learns too
        return d

    def buffered(self, target: str) -> None:
        """The stream stalled: it's now further behind, and we're less sure by how much."""
        d = self.get(target)
        d.mu += 3.0
        d.sigma += 5.0


_MARKERS = ("timeout", "end_", "two-minute", "period_end", "period_start", "start_inning",
            "start_batter", "end_batter", "coin_toss", "end_game", "end_period", "stoppage", "lineup_change")
# Dead-ball entries that happen DURING a whistle, not action that ends it. Without these, every
# free throw "ended" the stoppage and NBA whistles became too short to ever cut in. Per league on
# purpose: "foul" in baseball is a live pitch ("foul ball") — matching it everywhere caused a cut.
# Basketball: after a timeout/inbound the ball is live with nothing logged until the next shot,
# foul or turnover — up to a full shot clock. Play may have resumed that long before the first
# logged event (backtests: assuming 2s caused 7 cuts once break visits got more frequent).
SILENT_LIVE_S = ByLeague({League.NBA: 24.0, League.CBB: 30.0, League.WCBB: 30.0})  # college: 30 s shot clock


def resume_lead(league: League) -> float:
    """How long before the first logged event after a stoppage play may already have restarted."""
    return PLAY_LEN_S.get(league, 0.0) + 2.0 + SILENT_LIVE_S.get(league, 0.0)


_DEAD_BALL = ByLeague({
    League.NBA: ("foul", "substitution", "violation", "kicked_ball", "technical", "flagrant", "delay", "team_rebound",
                 "free_throw_-_1_of_2", "free_throw_-_1_of_3", "free_throw_-_2_of_3"),
    League.NHL: ("hooking", "holding", "tripping", "roughing", "interference", "slashing", "high-sticking",
                 "fighting", "unsportsmanlike", "cross-checking", "too_many", "delay"),
})


def _is_marker(kind: str, league: League | None = None) -> bool:
    """Bookkeeping entries in play-by-play (timeouts, period ends, dead-ball entries), not action."""
    k = (kind or "").lower()
    return any(m in k for m in _MARKERS) or any(m in k for m in _DEAD_BALL.get(league, ()))


def _t(p, now: float) -> float:
    return p.wallclock or p.seen_at or now - FEED_LAG_GUESS_S


def _bounds(p, now: float, prev_fresh: float | None = None) -> tuple[float, float]:
    """(earliest, latest) real time this play could have happened.

    With a wallclock it's exact. Without one, all we know is it reached us after the last fresh
    poll and before now, and the feed runs MIN..MAX seconds behind real life."""
    if p.wallclock:
        return p.wallclock, p.wallclock
    arrived = p.seen_at or now
    return min(arrived, prev_fresh or arrived) - FEED_LAG_MAX_S, arrived - FEED_LAG_MIN_S


def _stoppage(label: str) -> Stoppage | None:
    try:
        return Stoppage(label)
    except ValueError:
        return None


def _is_game_end(kind: str) -> bool:
    k = (kind or "").lower()
    return "game" in k and "end" in k


@dataclass
class Event:
    seen: float      # when it happened (the LATEST it could have, if unsure)
    kind: str        # "play" | "stop_start" | "stop_end"
    label: str = ""
    big: bool = False
    pid: str = ""
    play_kind: str = ""
    spread: float = 0.0  # unsure by this much: it happened in [seen - spread, seen]
    score: tuple[int, int] | None = None  # "score" events: (home, away) right after the play

    @property
    def lo(self) -> float:
        return self.seen - self.spread


class Timeline:
    """Per-game event history, fed every poll from the merged GameState."""

    def __init__(self, league: League, clock=time.time):
        self.league = league
        self._clock = clock
        self.events: deque[Event] = deque(maxlen=200)
        self.scores: deque[Event] = deque(maxlen=60)   # score changes (kind "score"), oldest first
        self._seen_ids: deque[str] = deque(maxlen=400)
        self._last_score: tuple | None = None
        self._stopped = False
        self.stoppage: Stoppage = Stoppage.UNKNOWN
        # [mid-play guard] freshness, measured feed lag, and the final whistle
        self.fresh_at: float | None = None    # when this game's data was last fresh
        self._lags: deque[float] = deque(maxlen=12)
        self.final_at: float | None = None    # when we first saw it Final
        self.end_t: float | None = None       # latest real time the game could have ended

    def lag_est(self) -> float:
        """How far behind real life this game's feed runs (recent worst case)."""
        return max(self._lags) if self._lags else LAG_EST_DEFAULT_S

    def _nba_clutch(self, p, g: GameState) -> bool:
        """NBA highlight: a field goal in the last 2:00 of Q4/OT within 5 (what the scorecard counts).
        ESPN's NBA kinds are never "turnover"/"td", so without this no NBA play was ever big."""
        if self.league.base != League.NBA or not p.scoring or "free_throw" in (p.kind or ""):
            return False
        if pro_period(g.league, g.period, g.clock_s) < 4 or g.clock_s is None or g.clock_s > 120:
            return False
        hs, as_ = self._last_score or (g.home.score, g.away.score)
        return hs is not None and as_ is not None and abs(hs - as_) <= 5

    def observe(self, g: GameState) -> None:
        now = self._clock()
        prev_fresh = self.fresh_at
        self.fresh_at = g.updated_at or now
        score = (g.home.score, g.away.score)
        # Every play we haven't recorded yet, not just the latest: two plays can land in one poll.
        plays = list(g.recent_plays) or ([g.last_play] if g.last_play else [])
        if g.last_play and plays and plays[-1].id != g.last_play.id:
            plays.append(g.last_play)
        new = [p for p in plays if p.id and p.id not in self._seen_ids]
        first_poll = not self._seen_ids
        action = [p for p in new if not _is_marker(p.kind, self.league)]
        scorers = [p for p in new if p.scoring]
        for p in action:
            # First poll: skip history, but keep plays recent enough to still be airing
            # (an opening-kickoff return TD is in the very first poll of a game).
            if first_poll and _t(p, now) < now - FIRST_POLL_RECENT_S:
                continue
            # In basketball every basket "scores"; only flag true highlights there.
            big = p.kind in ("td", "turnover", "goal", "hr") or (p.scoring and self.league.base != League.NBA) \
                or self._nba_clutch(p, g)
            lo, hi = _bounds(p, now, prev_fresh)
            self.events.append(Event(hi, "play", (p.text or p.kind)[:80], big, p.id, p.kind, hi - lo))
            if p.wallclock and prev_fresh is not None and not first_poll:
                # It wasn't in the data at the last fresh poll, so the feed ran at least this far behind.
                self._lags.append(min(45.0, max(0.0, prev_fresh - p.wallclock)))
        for p in scorers:
            # Every score change with its feed time, free throws included (they're dead-ball markers
            # above), for timing the broadcast's score bug (redzone/screen/scorebug.py).
            sc = p.score
            if sc is None and len(scorers) == 1 and None not in score:
                sc = score   # the poll's only new score: the game's score is what it made
            if sc is not None and not (first_poll and _t(p, now) < now - FIRST_POLL_RECENT_S):
                lo, hi = _bounds(p, now, prev_fresh)
                self.scores.append(Event(hi, "score", (p.text or p.kind)[:80], pid=p.id, play_kind=p.kind,
                                         spread=hi - lo, score=tuple(sc)))
        for p in new:
            self._seen_ids.append(p.id)
        if not new and self._last_score is not None and score != self._last_score and None not in score:
            # No play-by-play from this source: a score change is still a play (time unknown: bound it).
            lo, hi = (prev_fresh or now) - FEED_LAG_MAX_S, now - FEED_LAG_MIN_S
            self.events.append(Event(hi, "play", "score change", True, spread=hi - lo))
        self._last_score = score

        lead = resume_lead(self.league)  # play restarts before the first logged event after a stoppage
        last_start = self.last("stop_start")
        # Real action after a stoppage means the stoppage ended, even if a new one started since
        # (timeout -> touchdown -> break). Missing this once caused a mid-play cut in backtests.
        if self._stopped and action and not first_poll:
            # [mid-play guard] earliest of the new plays (they can land out of order), never before the
            # stoppage it ends: an "end" older than its start reads as a break that never ended.
            t_end = min(_bounds(p, now, prev_fresh)[0] for p in action) - lead
            self.events.append(Event(max(t_end, last_start.lo) if last_start else t_end, "stop_end"))
            self._stopped = False
        lp = g.last_play
        # [mid-play guard] a break starts on screen no earlier than the LATEST it could have started.
        stop_lo, at = _bounds(lp, now, prev_fresh) if lp else (
            (prev_fresh or now) - FEED_LAG_MAX_S, now - FEED_LAG_MIN_S)
        stopped = g.stoppage not in (Stoppage.NONE, Stoppage.UNKNOWN)
        if stopped and not self._stopped:
            self.events.append(Event(at, "stop_start", g.stoppage.value,
                                     pid=lp.id if lp else "", spread=at - stop_lo))
        elif not stopped and self._stopped and g.stoppage == Stoppage.NONE:
            # [mid-play guard] Resumed with no play to time it by. It was still stopped in the data at the
            # last fresh poll; assume the EARLIEST resume consistent with that (was now - 6 - lead,
            # which can be later than the real resume, so a live play looked like a break).
            t_end = (prev_fresh or now) - self.lag_est() - lead
            self.events.append(Event(max(t_end, last_start.lo) if last_start else t_end, "stop_end"))
        self._stopped = stopped if g.stoppage != Stoppage.UNKNOWN else self._stopped
        self.stoppage = g.stoppage

        if g.phase == Phase.POST and self.final_at is None:
            # [mid-play guard] Final in the data. The last play hasn't necessarily aired yet; remember the
            # latest it could have happened so the decider stays for it.
            self.final_at = now
            ends = [_bounds(p, now, prev_fresh)[1] for p in new if _is_game_end(p.kind)]
            known = [e.seen for e in self.events if e.kind == "play"]
            self.end_t = max(ends + known + ([] if ends else [now - FEED_LAG_MIN_S]))

    def last(self, kind: str) -> Event | None:
        for e in reversed(self.events):
            if e.kind == kind:
                return e
        return None


@dataclass
class Gate:
    safe: bool
    why: str
    on_screen_break: bool = False


def _stoppage_reached(tl: Timeline, start: Event, mu: float, margin: float) -> float:
    """From when the screen has nothing left to show before this stoppage.

    The stoppage itself airs at start + delay, but a timeout is often logged well after the
    play before it. Once that last play has surely finished airing, no action can reach the
    screen until the stoppage ends (the feed is ~35s ahead), so waiting longer only shows
    the huddle and then the ad. Not in continuous sports: the ball is live between logged plays.
    """
    reached = start.seen + mu + margin
    if tl.league in CONTINUOUS:
        return reached
    last = max((e.seen for e in tl.events if e.kind == "play" and e.seen <= start.seen), default=None)
    if last is not None:
        reached = min(reached, last + mu + margin + AFTER_PLAY_BUFFER_S)
    return reached


def leave_gate(tl: Timeline, delay: Delay, now: float, uncalibrated_sigma: float,
               tune_s: float = 6.0) -> Gate:
    """Is it safe to cut away from this game *as your screen shows it right now*?"""
    mu = delay.mu
    sig = delay.current_sigma(now)
    margin = 2 * sig
    play_len = PLAY_LEN_S.get(tl.league, 0.0)

    if tl.fresh_at is None or not tl.events:
        return Gate(False, "no observed play timing — screen state unknown")

    # [mid-play guard] Does our data still cover what your screen is showing? The screen can be
    # showing real time up to now - (mu - margin); a play that started by then was logged by
    # + play_len and reached our feed by + lag, so it's in our data only if that's before fresh_at.
    # If the feed is slow (lag), quiet (dropped source) or the delay is uncertain, "no play in the
    # data" stops meaning "no play on screen".
    age = max(0.0, now - tl.fresh_at)
    # how far our data runs ahead of your screen (basketball can be live ~a shot clock with no event)
    ahead = mu - margin - play_len - SILENT_LIVE_S.get(tl.league, 0.0) - tl.lag_est()
    covered = age <= ahead

    # 1. Is an official stoppage showing on screen (or is the screen done with action before it)?
    start, end = tl.last("stop_start"), tl.last("stop_end")
    in_break = False
    if start is not None:
        resume = end.seen if end is not None and end.seen >= start.lo else None
        # [mid-play guard] A play logged after the stoppage began proves play resumed, even if the
        # stop_end was mistimed or never recorded (late / out-of-order plays, missing wallclock).
        lead = resume_lead(tl.league)
        for e in reversed(tl.events):
            if (e.kind == "play" and e.seen > start.lo
                    and not (start.pid and e.pid == start.pid)):
                resume = e.lo - lead if resume is None else min(resume, e.lo - lead)
            # Arrival order is not event order. A very late old entry cannot
            # hide a newer play earlier in this bounded (200-event) deque.
        # The break is on screen once the play before it has surely finished airing — waiting for
        # the stoppage entry itself to air only shows the huddle and then the ad.
        aired_start = now >= _stoppage_reached(tl, start, mu, margin)
        if resume is not None:
            safe_until = resume + mu - margin                     # the snap that ends it can't be on screen yet
            not_ended = now <= safe_until - tune_s
            if aired_start and not not_ended and now <= safe_until and tl.league not in CONTINUOUS:
                # Still safe to cut (it's the break you're seeing), just too late to visit and come back.
                # Not in continuous sports: play resumes before the first logged event after it.
                return Gate(True, "break ending on screen")
        else:
            # No resume in the data: trust that only if the data covers your screen, or this kind
            # of break always runs long enough that it can't have ended on screen yet.
            floor = MIN_BREAK_S.get(tl.league, {}).get(_stoppage(start.label), 0.0)
            not_ended = covered or now <= start.lo + floor + mu - margin - tune_s
        in_break = aired_start and not_ended
    if in_break:
        real = start.label in {b.value for b in REAL_BREAKS}
        return Gate(True, "break on screen" if real else "whistle on screen", real)

    # 2. Continuous-play sports, or we don't trust our delay: only breaks are safe.
    if tl.league in CONTINUOUS:
        return Gate(False, "play is live (only leave at a stoppage)")
    if sig > uncalibrated_sigma:
        return Gate(False, "delay not calibrated yet — waiting for an official break")
    # [mid-play guard] Stale or slow data: same deal, only official stoppages.
    if age > STALE_DATA_S:
        return Gate(False, f"no fresh data for {age:.0f}s — waiting for an official break")
    if not covered:
        return Gate(False, "data is running behind your screen — waiting for an official break")

    # 3. Discrete sports: make sure no seen play is airing (or about to) on screen.
    for e in reversed(tl.events):
        if e.kind != "play":
            continue
        end_air = e.seen + mu
        start_air = e.lo + mu - play_len   # a play with no wallclock could be anywhere in its spread
        if start_air - margin - 1.0 <= now <= end_air + margin + AFTER_PLAY_BUFFER_S:
            return Gate(False, "a play is on screen")
        # Check the whole bounded deque: an old event delivered last does not
        # imply the entries before it have already aired.
    return Gate(True, "between plays on screen")


def screen_evidence(tl: Timeline | None, delay: Delay, now: float,
                    uncalibrated_sigma: float) -> dict:
    """Expose the timing model's evidence without pretending to observe TV pixels.

    A provider's PLAYING state confirms transport activity only. It is never
    input to this sports-play estimate, and these statuses are not probabilities.
    """
    observed = bool(tl is not None and tl.fresh_at is not None and tl.events)
    gate = leave_gate(tl, delay, now, uncalibrated_sigma) if observed else Gate(
        False, "no observed play timing — screen state unknown")
    if not observed:
        status = "unknown"
    elif gate.safe:
        status = "break_estimated" if gate.on_screen_break or "break" in gate.why or "whistle" in gate.why else "gap_estimated"
    elif gate.why in ("a play is on screen", "play is live (only leave at a stoppage)"):
        status = "play_possible"
    else:
        status = "unknown"
    return {"status": status, "safe_to_leave": gate.safe, "reason": gate.why,
            "basis": "sports_feed_and_delay_model",
            "feed_age_s": round(max(0.0, now - tl.fresh_at), 1) if tl is not None and tl.fresh_at is not None else None,
            "delay_s": delay.mu, "delay_margin_s": 2 * delay.current_sigma(now),
            "calibrated": delay.calibrated_at is not None}


def leave_window(tl: Timeline, delay: Delay, now: float, uncalibrated_sigma: float,
                 required_s: float) -> Gate:
    """Require an open exit window for the whole command/HDMI latency interval.

    Checking only the instant a command is sent can cut a play that begins
    before the TV reacts. Checking two endpoints also misses a complete play
    between them, so test intersection with every observed play interval.
    Future coverage uses the same received data; it never assumes another poll.
    """
    if not math.isfinite(required_s) or required_s < 0:
        return Gate(False, "switch duration unknown — screen state unknown")
    until = now + required_s
    # Freeze the widest uncertainty across the interval. Reusing the mutable
    # estimate at 'now' alone would omit drift during a long pending operation.
    sigma = max(delay.current_sigma(now), delay.current_sigma(until))
    bounded = Delay(delay.mu, sigma)
    start = leave_gate(tl, bounded, now, uncalibrated_sigma, tune_s=required_s)
    if not start.safe or required_s == 0:
        return start
    end = leave_gate(tl, bounded, until, uncalibrated_sigma, tune_s=0)
    if not end.safe:
        return Gate(False, "exit window closes before switch completes: " + end.why)
    margin = 2 * sigma
    length = PLAY_LEN_S.get(tl.league, 0.0)
    for event in tl.events:
        if event.kind != "play":
            continue
        play_start = event.lo + delay.mu - length - margin - 1.0
        play_end = event.seen + delay.mu + margin + AFTER_PLAY_BUFFER_S
        if play_start <= until and play_end >= now:
            return Gate(False, "a play may air before switch completes")
    return start


_REAL = {b.value for b in REAL_BREAKS}


def resume_play(tl: Timeline) -> Event | None:
    """The snap that ended the latest stoppage (None while it's still stopped)."""
    start, end = tl.last("stop_start"), tl.last("stop_end")
    if end is None or (start is not None and end.seen < start.lo):
        return None
    return min((e for e in tl.events if e.kind == "play" and e.seen > end.seen),
               key=lambda e: e.seen, default=None)


def break_on_arrival(tl: Timeline, delay: Delay, now: float, at: float) -> bool:
    """Would you land in a commercial if the picture came up at `at`?

    A break fills the screen from when the play before it has aired until the first snap
    after it has aired: landing during that snap still shows mostly ad and a huddle."""
    start, end = tl.last("stop_start"), tl.last("stop_end")
    if start is None or start.label not in _REAL:
        return False
    margin = 2 * delay.current_sigma(now)
    if at < min(start.seen + delay.mu - margin, _stoppage_reached(tl, start, delay.mu, margin)):
        return False
    if end is None or end.seen < start.lo:
        return True
    snap = resume_play(tl)
    snap_end = snap.seen if snap else end.seen + PLAY_LEN_S.get(tl.league, 8.0) + 2.0
    return at < snap_end + delay.mu


def final_play_aired(tl: Timeline, delay: Delay, now: float) -> bool:
    """A finished game is safe to leave once its last play has finished on screen: the data
    says "final" a full stream delay before your screen shows the last kneel-down."""
    last = max((e.seen for e in tl.events if e.kind == "play"), default=None)
    return last is not None and now > last + delay.mu + 2 * delay.current_sigma(now) + AFTER_PLAY_BUFFER_S


def catchable(tl: Timeline, delay: Delay, now: float, tune_s: float) -> Event | None:
    """A big play that already happened in the data but hasn't aired yet:
    switch now and you'll see it live."""
    play_len = PLAY_LEN_S.get(tl.league, 8.0) or 8.0
    for e in reversed(tl.events):
        if e.kind == "play" and e.big:
            start_air = e.lo + delay.mu - play_len   # earliest it could start airing
            if now + tune_s + 2 * delay.current_sigma(now) < start_air:
                return e
            return None
    return None
