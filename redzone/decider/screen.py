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

from redzone.models import REAL_BREAKS, GameState, League, Phase, Stoppage

# Leagues where the ball/puck is live continuously; only official stoppages are gaps.
CONTINUOUS = {League.NBA, League.NHL}
FEED_LAG_GUESS_S = 6.0  # used only when a feed gives no wallclock
PLAY_LEN_S = {League.NFL: 10.0, League.MLB: 8.0, League.NBA: 0.0, League.NHL: 0.0}
AFTER_PLAY_BUFFER_S = 3.0
FIRST_POLL_RECENT_S = 60.0  # on a game's first poll, plays newer than this may not have aired yet
# [mid-play guard] A play with no wallclock happened somewhere in [arrived - MAX, arrived - MIN].
# Timing it at one guessed point can put its on-screen window in the wrong place entirely.
FEED_LAG_MIN_S = 2.0
FEED_LAG_MAX_S = 30.0
LAG_EST_DEFAULT_S = 10.0   # feed lag assumed until we've measured some
STALE_DATA_S = 20.0        # older than this, "no play in the data" no longer means "no play on screen"
ORDER_SLACK_S = 120.0      # plays can reach us this far out of real-time order
# Official breaks that always last at least this long (stoppage entry -> next snap). From 270 archived
# NFL games: end of quarter p1 171 s, halftime p1 816 s, two-minute warning p1 55 s. Timeouts and
# score breaks can be 5-15 s in the data, so they get no floor. Used only when we can't see the resume.
MIN_BREAK_S = {Stoppage.END_PERIOD: 120.0, Stoppage.HALFTIME: 600.0, Stoppage.TWO_MINUTE_WARNING: 50.0}


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
# Dead-ball entries that happen DURING a whistle, not action that ends it. Without these, every
# free throw "ended" the stoppage and NBA whistles became too short to ever cut in. Per league on
# purpose: "foul" in baseball is a live pitch ("foul ball") — matching it everywhere caused a cut.
_DEAD_BALL = {
    League.NBA: ("foul", "substitution", "violation", "kicked_ball", "technical", "flagrant", "delay",
                 "free_throw_-_1_of_2", "free_throw_-_1_of_3", "free_throw_-_2_of_3"),
    League.NHL: ("hooking", "holding", "tripping", "roughing", "interference", "slashing", "high-sticking",
                 "fighting", "unsportsmanlike", "cross-checking", "too_many", "delay"),
}


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

    @property
    def lo(self) -> float:
        return self.seen - self.spread


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
        # [mid-play guard] freshness, measured feed lag, and the final whistle
        self.fresh_at: float | None = None    # when this game's data was last fresh
        self._lags: deque[float] = deque(maxlen=12)
        self.final_at: float | None = None    # when we first saw it Final
        self.end_t: float | None = None       # latest real time the game could have ended

    def lag_est(self) -> float:
        """How far behind real life this game's feed runs (recent worst case)."""
        return max(self._lags) if self._lags else LAG_EST_DEFAULT_S

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
        for p in action:
            # First poll: skip history, but keep plays recent enough to still be airing
            # (an opening-kickoff return TD is in the very first poll of a game).
            if first_poll and _t(p, now) < now - FIRST_POLL_RECENT_S:
                continue
            # In basketball every basket "scores"; only flag true highlights there.
            big = p.kind in ("td", "turnover", "goal", "hr") or (p.scoring and self.league != League.NBA)
            lo, hi = _bounds(p, now, prev_fresh)
            self.events.append(Event(hi, "play", (p.text or p.kind)[:80], big, p.id, p.kind, hi - lo))
            if p.wallclock and prev_fresh is not None and not first_poll:
                # It wasn't in the data at the last fresh poll, so the feed ran at least this far behind.
                self._lags.append(min(45.0, max(0.0, prev_fresh - p.wallclock)))
        for p in new:
            self._seen_ids.append(p.id)
        if not new and self._last_score is not None and score != self._last_score and None not in score:
            # No play-by-play from this source: a score change is still a play (time unknown: bound it).
            lo, hi = (prev_fresh or now) - FEED_LAG_MAX_S, now - FEED_LAG_MIN_S
            self.events.append(Event(hi, "play", "score change", True, spread=hi - lo))
        self._last_score = score

        lead = PLAY_LEN_S.get(self.league, 0.0) + 2.0  # a play starts before it's logged
        last_start = self.last("stop_start")
        # Real action after a stoppage means the stoppage ended, even if a new one started since
        # (timeout -> touchdown -> break). Missing this once caused a mid-play cut in backtests.
        if self._stopped and action and not first_poll:
            # [mid-play guard] earliest of the new plays (they can land out of order), never before the
            # stoppage it ends: an "end" older than its start reads as a break that never ended.
            t_end = min(_bounds(p, now, prev_fresh)[0] for p in action) - lead
            self.events.append(Event(max(t_end, last_start.seen) if last_start else t_end, "stop_end"))
            self._stopped = False
        lp = g.last_play
        # [mid-play guard] a break starts on screen no earlier than the LATEST it could have started.
        at = _bounds(lp, now, prev_fresh)[1] if lp else now - FEED_LAG_MIN_S
        stopped = g.stoppage not in (Stoppage.NONE, Stoppage.UNKNOWN)
        if stopped and not self._stopped:
            self.events.append(Event(at, "stop_start", g.stoppage.value))
        elif not stopped and self._stopped and g.stoppage == Stoppage.NONE:
            # [mid-play guard] Resumed with no play to time it by. It was still stopped in the data at the
            # last fresh poll; assume the EARLIEST resume consistent with that (was now - 6 - lead,
            # which can be later than the real resume, so a live play looked like a break).
            t_end = (prev_fresh or now) - self.lag_est() - lead
            self.events.append(Event(max(t_end, last_start.seen) if last_start else t_end, "stop_end"))
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
    last = next((e.seen for e in reversed(tl.events) if e.kind == "play" and e.seen <= start.seen), None)
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

    # [mid-play guard] Does our data still cover what your screen is showing? The screen can be
    # showing real time up to now - (mu - margin); a play that started by then was logged by
    # + play_len and reached our feed by + lag, so it's in our data only if that's before fresh_at.
    # If the feed is slow (lag), quiet (dropped source) or the delay is uncertain, "no play in the
    # data" stops meaning "no play on screen".
    age = max(0.0, now - tl.fresh_at) if tl.fresh_at is not None else 0.0
    ahead = mu - margin - play_len - tl.lag_est()   # how far our data runs ahead of your screen
    covered = age <= ahead

    # 1. Is an official stoppage showing on screen (or is the screen done with action before it)?
    start, end = tl.last("stop_start"), tl.last("stop_end")
    in_break = False
    if start is not None:
        resume = end.seen if end is not None and end.seen >= start.seen else None
        # [mid-play guard] A play logged after the stoppage began proves play resumed, even if the
        # stop_end was mistimed or never recorded (late / out-of-order plays, missing wallclock).
        lead = play_len + 2.0
        for e in reversed(tl.events):
            if e.kind == "play" and e.seen > start.seen:
                resume = e.lo - lead if resume is None else min(resume, e.lo - lead)
            elif e.seen < start.seen - ORDER_SLACK_S:
                break
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
            floor = MIN_BREAK_S.get(_stoppage(start.label), 0.0)
            not_ended = covered or now <= start.seen + floor + mu - margin - tune_s
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
        # [mid-play guard] Events are in arrival order, not real-time order (late plays): only stop
        # once we're well past anything that could still be airing.
        if end_air + margin + AFTER_PLAY_BUFFER_S + ORDER_SLACK_S < now:
            break  # older plays have all aired
    return Gate(True, "between plays on screen")


_REAL = {b.value for b in REAL_BREAKS}


def resume_play(tl: Timeline) -> Event | None:
    """The snap that ended the latest stoppage (None while it's still stopped)."""
    start, end = tl.last("stop_start"), tl.last("stop_end")
    if end is None or (start is not None and end.seen < start.seen):
        return None
    return next((e for e in tl.events if e.kind == "play" and e.seen > end.seen), None)


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
    if end is None or end.seen < start.seen:
        return True
    snap = resume_play(tl)
    snap_end = snap.seen if snap else end.seen + PLAY_LEN_S.get(tl.league, 8.0) + 2.0
    return at < snap_end + delay.mu


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
