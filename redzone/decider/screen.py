"""What's on your screen right now, estimated from the data and the stream delay.

Event times are when the play actually happened (the feed's wallclock). When a
feed has no wallclock we use "when we saw it, minus the usual feed lag".
The delay is how far your screen runs behind real life; the "I just saw it"
button measures it directly (press_time - wallclock of the play).

    event airs on screen at  t + delay  (± 2·sigma)

That's the whole trick: the data is ahead of your stream, so we know what
your screen is about to show.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

from redzone.models import REAL_BREAKS, GameState, League, Stoppage

# Leagues where the ball/puck is live continuously; only official stoppages are gaps.
CONTINUOUS = {League.NBA, League.NHL}
FEED_LAG_GUESS_S = 6.0  # used only when a feed gives no wallclock
PLAY_LEN_S = {League.NFL: 10.0, League.MLB: 8.0, League.NBA: 0.0, League.NHL: 0.0}
AFTER_PLAY_BUFFER_S = 3.0


@dataclass
class Delay:
    mu: float
    sigma: float
    calibrated_at: float | None = None
    samples: deque = field(default_factory=lambda: deque(maxlen=12))

    def current_sigma(self, now: float) -> float:
        if self.calibrated_at is None:
            return self.sigma
        # Stream delay is stable unless the stream buffers (see buffered()); doubt grows slowly.
        drift = min(6.0, (now - self.calibrated_at) / 900.0)
        return min(20.0, self.sigma + drift)


class DelayBook:
    """Per-channel delay estimates. Key is the tuner target, e.g. 'youtubetv:espn'."""

    def __init__(self, defaults: dict[str, float], sigma_default: float, clock=time.time):
        self._defaults = defaults
        self._sigma_default = sigma_default
        self._clock = clock
        self.book: dict[str, Delay] = {}

    def get(self, target: str) -> Delay:
        if target not in self.book:
            app = target.split(":", 1)[0]
            self.book[target] = Delay(self._defaults.get(app, 40.0), self._sigma_default)
        return self.book[target]

    def calibrate(self, target: str, sample_s: float) -> Delay:
        """One "I just saw it" measurement. Outliers are clipped, not trusted blindly."""
        d = self.get(target)
        if not 2.0 <= sample_s <= 180.0:
            return d
        d.samples.append(sample_s)
        s = sorted(d.samples)
        d.mu = s[len(s) // 2]  # median: one sloppy tap can't wreck it
        spread = (s[-1] - s[0]) / 2 if len(s) > 1 else 6.0
        d.sigma = max(2.0, min(15.0, spread))
        d.calibrated_at = self._clock()
        return d

    def buffered(self, target: str) -> None:
        """The stream stalled: it's now further behind, and we're less sure by how much."""
        d = self.get(target)
        d.mu += 3.0
        d.sigma = min(20.0, d.sigma + 5.0)


_MARKERS = ("timeout", "end_", "two-minute", "period_end", "period_start", "start_inning",
            "start_batter", "end_batter", "coin_toss", "end_game", "end_period", "stoppage")


def _is_marker(kind: str) -> bool:
    """Bookkeeping entries in play-by-play (timeouts, period ends), not action on the field."""
    k = (kind or "").lower()
    return any(m in k for m in _MARKERS)


def _t(p, now: float) -> float:
    return p.wallclock or p.seen_at or now - FEED_LAG_GUESS_S


@dataclass
class Event:
    seen: float
    kind: str        # "play" | "stop_start" | "stop_end"
    label: str = ""
    big: bool = False
    pid: str = ""
    play_kind: str = ""


class Timeline:
    """Per-game event history, fed every poll from the merged GameState."""

    def __init__(self, league: League, clock=time.time):
        self.league = league
        self._clock = clock
        self.events: deque[Event] = deque(maxlen=200)
        self._seen_ids: deque[str] = deque(maxlen=400)
        self._last_score: tuple | None = None
        self._stopped = False
        self.stoppage: Stoppage = Stoppage.UNKNOWN

    def observe(self, g: GameState) -> None:
        now = self._clock()
        score = (g.home.score, g.away.score)
        # Every play we haven't recorded yet, not just the latest: two plays can land in one poll.
        plays = list(g.recent_plays) or ([g.last_play] if g.last_play else [])
        if g.last_play and plays and plays[-1].id != g.last_play.id:
            plays.append(g.last_play)
        new = [p for p in plays if p.id and p.id not in self._seen_ids]
        first_poll = not self._seen_ids
        action = [p for p in new if not _is_marker(p.kind)]
        if not first_poll:
            for p in action:
                # In basketball every basket "scores"; only flag true highlights there.
                big = p.kind in ("td", "turnover", "goal", "hr") or (p.scoring and self.league != League.NBA)
                self.events.append(Event(_t(p, now), "play", (p.text or p.kind)[:80], big, p.id, p.kind))
        for p in new:
            self._seen_ids.append(p.id)
        if not new and self._last_score is not None and score != self._last_score and None not in score:
            # No play-by-play from this source: a score change is still a play.
            self.events.append(Event(now - FEED_LAG_GUESS_S, "play", "score change", True))
        self._last_score = score

        lead = PLAY_LEN_S.get(self.league, 0.0) + 2.0  # a play starts before it's logged
        # Real action after a stoppage means the stoppage ended, even if a new one started since
        # (timeout -> touchdown -> break). Missing this once caused a mid-play cut in backtests.
        if self._stopped and action and not first_poll:
            self.events.append(Event(_t(action[0], now) - lead, "stop_end"))
            self._stopped = False
        lp = g.last_play
        at = _t(lp, now) if lp else now - FEED_LAG_GUESS_S
        stopped = g.stoppage not in (Stoppage.NONE, Stoppage.UNKNOWN)
        if stopped and not self._stopped:
            self.events.append(Event(at, "stop_start", g.stoppage.value))
        elif not stopped and self._stopped and g.stoppage == Stoppage.NONE:
            self.events.append(Event(now - FEED_LAG_GUESS_S - lead, "stop_end"))
        self._stopped = stopped if g.stoppage != Stoppage.UNKNOWN else self._stopped
        self.stoppage = g.stoppage

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


def leave_gate(tl: Timeline, delay: Delay, now: float, uncalibrated_sigma: float,
               tune_s: float = 6.0) -> Gate:
    """Is it safe to cut away from this game *as your screen shows it right now*?"""
    mu = delay.mu
    sig = delay.current_sigma(now)
    margin = 2 * sig

    # 1. Is an official stoppage showing on screen?
    start, end = tl.last("stop_start"), tl.last("stop_end")
    in_break = False
    if start is not None and (end is None or end.seen < start.seen):
        in_break = now >= start.seen + mu + margin           # break has definitely started on screen
    elif start is not None and end is not None and end.seen >= start.seen:
        aired_start = now >= start.seen + mu + margin
        not_ended = now <= end.seen + mu - margin - tune_s   # resume hasn't reached the screen
        in_break = aired_start and not_ended
    if in_break:
        real = start.label in {b.value for b in REAL_BREAKS}
        return Gate(True, "break on screen" if real else "whistle on screen", real)

    # 2. Continuous-play sports, or we don't trust our delay: only breaks are safe.
    if tl.league in CONTINUOUS:
        return Gate(False, "play is live (only leave at a stoppage)")
    if sig > uncalibrated_sigma:
        return Gate(False, "delay not calibrated yet — waiting for an official break")

    # 3. Discrete sports: make sure no seen play is airing (or about to) on screen.
    play_len = PLAY_LEN_S[tl.league]
    for e in reversed(tl.events):
        if e.kind != "play":
            continue
        end_air = e.seen + mu
        start_air = end_air - play_len
        if start_air - margin - 1.0 <= now <= end_air + margin + AFTER_PLAY_BUFFER_S:
            return Gate(False, "a play is on screen")
        if end_air + margin + AFTER_PLAY_BUFFER_S < now:
            break  # older plays have all aired
    return Gate(True, "between plays on screen")


def catchable(tl: Timeline, delay: Delay, now: float, tune_s: float) -> Event | None:
    """A big play that already happened in the data but hasn't aired yet:
    switch now and you'll see it live."""
    play_len = PLAY_LEN_S.get(tl.league, 8.0) or 8.0
    for e in reversed(tl.events):
        if e.kind == "play" and e.big:
            start_air = e.seen + delay.mu - play_len
            if now + tune_s + 2 * delay.current_sigma(now) < start_air:
                return e
            return None
    return None
