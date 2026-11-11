"""Clairvoyance: what the feed already knows that the screen hasn't shown yet (cycle 02, lane 2).

With the time-shift buffer the screen runs mu seconds behind real life (stream delay + B), but the feed
learns of each play ~45 s after it happens, so for B=90 the app sees ~1.5 minutes of every game BEFORE it
airs. `ahead(tl, delay, now)` is that window for one game: the plays and dead windows between the
screen's "now" (now - mu, in the timeline's real time, the same convention as Decider.entry_plan) and the
newest data. It is a pure function of the timeline and the DelayBook entry, so the live app and the sims
read exactly the same thing. When the delay's doubt is too wide to say where the screen is
(sigma > max_sigma_s, the SAFE-02 threshold) it returns nothing: no clairvoyance on a guess.

Cost: a scan of the timeline's last 200 events and its dead windows (<0.1 ms per game).

Why: first added in 55a69759 (2026-11-04) as "Clairvoyance: lookahead view + GAME-04 in the candidate, crunch_now=wait"; no separate decision is recorded for this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Ahead:
    screen_t: float                                  # real time the screen shows now
    plays: list = field(default_factory=list)        # screen.Event, kind "play", not aired yet
    dead: list = field(default_factory=list)         # cuts.Window starting after screen_t

    @property
    def big(self):
        """The first big play still to air, else None."""
        return next((e for e in self.plays if e.big), None)

    def next_dead(self, after: float):
        """The first dead window that starts at or after real time `after`, else None."""
        return next((w for w in self.dead if w.start >= after), None)

    def last_dead_before(self, t: float, after: float):
        """The last dead window ending by real time t that still starts after `after`, else None."""
        best = None
        for w in self.dead:
            if w.start >= after and w.end is not None and w.end <= t:
                best = w
        return best


def ahead(tl, delay, now: float, max_sigma_s: float = 10.0) -> Ahead | None:
    """What game tl will show in the next mu seconds, or None (no timeline, or the screen's position is
    too uncertain to say)."""
    if tl is None or delay is None or delay.current_sigma(now) > max_sigma_s:
        return None
    st = now - delay.mu
    plays = [e for e in tl.events if e.kind == "play" and e.seen > st]
    dead = [w for w in tl.dead.windows() if w.start > st] if tl.dead.marks else []
    return Ahead(st, plays, dead)
