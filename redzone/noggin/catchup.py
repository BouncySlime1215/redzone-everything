"""The catch-up route (arbiter.catchup_route, off): a must-see that aired unseen airs late on the hidden stick, in the screen's dead time.

Why: Nick, Nov 9: "fuck the rewind limit... dvr the game if u need... any missed moment is shown... pls dont be 3 or more min
late on the highlight". The old route needed a whole clip inside one dead ball on a game with the route open, so it saved
almost nothing. Here a clip may run in pieces over consecutive dead balls or a commercial, while the live stick keeps the game on
screen (it is never paused, never left mid-play, and the picture is back on it before play restarts).

The rules this module owns, and tests pin:
  * HARD_CAP_S: no second of a clip airs 180 s or more after that second first aired. Past it, the moment is dropped and counted lost.
  * reach: a piece is only offered within the rewind the stick can do. Real reach is unproven on the TV (95-150 s measured).
  * a piece fits inside the dead time on screen, with a margin, so the flip back lands before the live game restarts.
  * the first piece holds the whole play, so a clip cut short never ended on half a play.
  * a clip whose result the screen has already shown is marked as a replay; it never airs as if it were news.
  * [arbiter.catchup_route_short, off] with another missed must-see waiting, a clip is only its first piece: the lead-in, the whole
    play and a second after. One hidden stick then clears more of the queue inside the cap. Alone, a clip stays whole.

A piece is a separate replay at a later offset (Missed.offset_s): the hidden stick re-seeks between pieces, so no new stick command.

Pure: no clock, no I/O, no decider import. `now` is passed in, so a replay gives the same choice. The audit
(scripts/veto_audit.py) and the decider both call `next_piece`.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from redzone.noggin import arbiter

HARD_CAP_S = 180.0    # Nick: "pls dont be 3 or more min late on the highlight". A constant on purpose: no setting may raise it


@dataclass(frozen=True)
class Moment:
    """One must-see that aired unseen."""
    id: str
    game_id: str
    value: float
    aired: float           # epoch s it finished airing on its game's picture
    play_s: float = 7.0    # how long the play itself runs


@dataclass(frozen=True)
class Route:
    """The route's costs. Built from config by the decider, from the audit's constants by the audit."""
    reach_s: float = 95.0       # deepest seek the stick can do. Measured 95-150 s; deep DVR unproven
    clip_s: float = 30.0        # screen time one whole clip needs
    flip_s: float = 3.2         # one input flip between the sticks
    prep_s: float = 15.0        # the hidden stick tunes and seeks to a clip (again for each later piece)
    margin_s: float = 2.0       # back on the live game at least this long before it restarts
    lead_s: float = 3.0         # a clip starts this long before the play (Missed.back_s lead_s)
    min_piece_s: float = 8.0    # a later piece shorter than this isn't worth two flips
    short: bool = False         # [arbiter.catchup_route_short] a clip is just the play while another clip waits its turn


@dataclass(frozen=True)
class Piece:
    """One stretch of a clip on the screen."""
    moment: Moment
    offset_s: float        # where in the clip it starts
    seconds: float
    flip_at: float         # the flip to the hidden stick starts
    until: float           # the picture is back on the live stick by here
    last: bool             # this piece finishes the clip
    marked: bool           # airs labelled as a replay: the screen already showed this result
    clip_s: float = 0.0    # the whole clip's length this piece was cut from (shorter than Route.clip_s on a short clip)


def first_piece_s(m: Moment, r: Route) -> float:
    """The first piece must hold the lead-in and the whole play, and a second to see how it ended."""
    return min(r.clip_s, r.lead_s + m.play_s + 1.0)


def lateness(m: Moment, up_at: float, offset_s: float, r: Route) -> float:
    """How long after it first aired the picture at `offset_s` into the clip shows, when the picture is up at `up_at`."""
    return up_at - (m.aired - m.play_s - r.lead_s + offset_s)


def on_time(m: Moment, up_at: float, offset_s: float, r: Route) -> bool:
    """Inside the hard cap (strictly: 180 s is already too late) and inside this stick's reach."""
    late = lateness(m, up_at, offset_s, r)
    return late < HARD_CAP_S and late <= r.reach_s


def can_finish(m: Moment, offset_s: float, r: Route, after: float) -> bool:
    """Could a next piece at `offset_s`, starting no sooner than `after`, still be on time? One flip back, a re-seek, one flip over."""
    return on_time(m, after + r.flip_s + r.prep_s + r.flip_s, offset_s, r)


def overdue(m: Moment, offset_s: float, now: float, r: Route) -> bool:
    """A clip in pieces whose next piece can no longer air on time: drop it (it counts as lost, the piece already aired stays aired)."""
    return not on_time(m, now + r.flip_s, offset_s, r)


def room(now: float, end: float, r: Route) -> float:
    """Clip seconds that fit in dead time ending at `end`: a flip over, then a flip back with the margin."""
    return end - now - 2.0 * r.flip_s - r.margin_s


def next_piece(pool: Sequence[Moment], now: float, end: float, *, doing: tuple[Moment, float] | None = None, ready_at: float = float("-inf"),
               known: Callable[[Moment], bool] = lambda m: False, r: Route = Route()) -> Piece | None:
    """The piece to air now, inside the dead time on the live stick that lasts until `end`, or None.
    `doing`: the clip already in pieces (moment, seconds of it already aired); it goes first and alone.
    `ready_at`: when the hidden stick is free for this (its last job). `known`: has the screen already shown this result?"""
    fit = room(now, end, r)
    if doing is not None:
        m, off = doing
        return _piece(m, off, now, fit, ready_at, known(m), r) if now >= ready_at else None
    live = [m for m in pool if m.aired + r.prep_s <= now and now >= ready_at]
    if not live:
        return None
    order = {m.id: m for m in live}
    cands = [(m.id, m.value, m.aired + r.prep_s, m.aired - m.play_s - r.lead_s + min(r.reach_s, HARD_CAP_S - 1.0) - r.flip_s, r.clip_s, max(m.aired + r.prep_s, ready_at))
             for m in live]
    first = arbiter.plan_clips(cands, now, r.flip_s, r.prep_s)
    if first is None:
        return None
    # plan_clips picks the best run; the first clip of it goes, or the next-best one this dead ball can hold
    owed = [m for m in live if on_time(m, now + r.flip_s, 0.0, r)] if r.short else ()
    for m in (order[first], *sorted((x for x in live if x.id != first), key=lambda x: (-x.value, x.aired))):
        short = any(x.id != m.id for x in owed)      # someone else can still make it: this clip is just the play
        p = _piece(m, 0.0, now, fit, ready_at, known(m), r, first_piece_s(m, r) if short else None)
        if p is not None:
            return p
    return None


def _piece(m: Moment, off: float, now: float, fit: float, ready_at: float, marked: bool, r: Route, clip_s: float | None = None) -> Piece | None:
    clip = r.clip_s if clip_s is None else clip_s
    left = clip - off
    secs = min(fit, left)
    need = first_piece_s(m, r) if off == 0.0 else min(r.min_piece_s, left)
    if secs < need or now < ready_at:
        return None
    up = now + r.flip_s
    if not on_time(m, up, off, r):
        return None
    last = secs >= left
    if not last and not can_finish(m, off + secs, r, up + secs):
        return None        # an opening piece of a clip that could never finish: don't start it
    return Piece(m, off, secs, now, up + secs + r.flip_s, last, marked, clip)
