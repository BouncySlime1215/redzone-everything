"""Scenes and slate modes: which games the rotation may cut to right now, and whether a cut pays off.

Why: Nick's "stay with a few games for a few minutes, then catch up", the planner's slate table (product-per-league
section 6) and the RedZone habits in docs/agents/data/2026-11-09-redzone-viewers-production.md (late narrowing, a
one-game ending). The decider asks one pure question, once a tick, instead of growing a branch per slate size.

    scene = plan(facts, now=t, block=b, peak=n, params=p)   # Scene(mode, phase, games, block, late, why)
    may_cut_to(scene, gid)                                  # may the rotation cut to this game now?

Modes, by how many games are live:
    off       none            nothing to do; the fill ladder has the screen
    single    one, all night  stay
    ending    one, after more stay: the last game plays out (no whip, the night's montage is the fill ladder's job)
    anchor    one top-league game plus lower leagues: it holds the TV; the others get its breaks
    whip      2-4             every game is in the rotation
    block     5-16            2-4 games for a few minutes, then a short catch-up, then the next block
    filtered  17+             the same, after a relevance filter down to about 8

Rules that hold in every mode: your team, a crunch game and an unseen must-show are always allowed (the floor), and
late in the slate only close games and the floor stay on the list (late narrowing). Nothing here is a safety check:
it can only shrink the list of games the rotation may pick. Every cut still passes the commit gate and Rule #1.

Pure: reads the facts it is handed; no clock, no config (except through `Params.from_config`), no I/O, no decider
import. Flag `slate.enabled` (code default OFF, read by the decider). Off, the decider never calls this file.
"""
from __future__ import annotations

import dataclasses as dc
from collections.abc import Callable, Iterable, Mapping

from redzone.models import league_index

OFF, SINGLE, ENDING, ANCHOR, WHIP, BLOCK, FILTERED = "off", "single", "ending", "anchor", "whip", "block", "filtered"
HOLD, CATCH_UP = "hold", "catch_up"


@dc.dataclass(frozen=True)
class Params:
    """Every number the slate uses. Read as `slate.<name>`; a bad value keeps its default."""
    enabled: bool = False
    leagues: tuple[str, ...] = ("nhl", "mlb")   # added to jump_mode and buffered_jump's own league lists
    whip_max: int = 4                # up to this many live games: every game in the rotation
    filter_over: int = 16            # more than this many live: the relevance filter first
    filter_to: int = 8
    block_size: int = 3              # "a few games"
    hold_s: float = 240.0            # "for a few minutes"
    catchup_s: float = 45.0          # then the catch-up scene (a reel or the block's missed plays)
    anchor_leagues: tuple[str, ...] = ("nfl", "cfb")
    late_narrow: bool = True
    return_gap_s: float = 60.0       # no return to a game this soon without something new; 0 turns it off
    payoff: bool = True              # a cut needs a snap coming, a big moment, a break to fill, or a must-show
    leave_after_shot: bool = True    # basketball may leave in the walk after a shot, low leverage only
    walk_safe_made_s: float = 6.0    # a join after a make is up 4 s after it with 2 s of doubt: the frozen grader's 4 s dead ball, inside the measured 7 s (5th percentile of the next live action, fit nights, n=4,403)
    walk_safe_rebound_s: float = 5.0  # the same after a defensive rebound (n=3,633); a transition has no walk at all

    @classmethod
    def from_config(cls, get: Callable[..., object]) -> Params:
        base, out = cls(), {}
        for f in dc.fields(cls):
            old, new = getattr(base, f.name), get(f"slate.{f.name}", None)
            try:
                if isinstance(old, bool):
                    out[f.name] = old if new is None else bool(new)
                elif isinstance(old, tuple):
                    if isinstance(new, (list, tuple)):
                        out[f.name] = tuple(str(x).lower() for x in new)
                elif new is not None:
                    out[f.name] = type(old)(new)
            except (TypeError, ValueError):
                continue
        return dc.replace(base, **out)


def enabled(get: Callable[..., object]) -> bool:
    """The flag, `slate.enabled`; off unless config says True."""
    return Params.from_config(get).enabled


@dc.dataclass(frozen=True)
class Facts:
    """One live game as the slate reads it. Built by the decider."""
    game_id: str
    league: str
    score: float = 0.0
    mine: bool = False
    crunch: bool = False
    close: bool = False            # close and late, or overtime
    late: bool = False             # final period
    on_break: bool = False
    owed: bool = False             # an unseen must-show is on its way to this game's picture
    unseen: bool = False           # a big play aired here while the screen was elsewhere


@dc.dataclass(frozen=True)
class Block:
    """A few games for a few minutes. A value handed back to the decider each tick, not a machine: `phase` is read off
    the clock, so nothing here can get stuck."""
    games: tuple[str, ...]
    since: float


@dc.dataclass(frozen=True)
class Scene:
    mode: str
    phase: str = HOLD
    games: frozenset[str] = frozenset()   # the games the rotation may cut to now
    block: Block | None = None
    late: bool = False
    why: str = ""


def _forced(fs: Iterable[Facts]) -> frozenset[str]:
    """The floor: never filtered out by any mode."""
    return frozenset(f.game_id for f in fs if f.mine or f.crunch or f.owed)


def _key(f: Facts) -> tuple:
    return (not f.mine, not f.crunch, league_index(f.league), -f.score, f.game_id)


def anchor_of(fs: list[Facts], p: Params) -> Facts | None:
    """The one game of the highest league live, when that league anchors a night and other leagues are also on."""
    if len(fs) < 2:
        return None
    best = min(league_index(f.league) for f in fs)
    top = [f for f in fs if league_index(f.league) == best]
    return top[0] if len(top) == 1 and top[0].league in p.anchor_leagues else None


def mode_of(fs: list[Facts], peak: int, p: Params) -> str:
    n = len(fs)
    if n == 0:
        return OFF
    if n == 1:
        return ENDING if peak >= 2 else SINGLE
    if anchor_of(fs, p) is not None:
        return ANCHOR
    if n <= p.whip_max:
        return WHIP
    return BLOCK if n <= p.filter_over else FILTERED


def relevant(fs: list[Facts], p: Params) -> list[Facts]:
    """The about-8 games worth a block on a huge slate: your teams, crunch, league order, then the score."""
    return sorted(fs, key=_key)[: max(1, p.filter_to)]


def _pick_block(fs: list[Facts], prev: Block | None, p: Params) -> tuple[str, ...]:
    """The next few games. Your team and crunch games lead every block; the last block's other games go to the back."""
    was = set(prev.games) if prev is not None else set()
    order = sorted(fs, key=lambda f: (not f.mine, not f.crunch, f.game_id in was, *_key(f)[2:]))
    return tuple(f.game_id for f in order[: max(1, p.block_size)])


def step_block(fs: list[Facts], block: Block | None, now: float, p: Params) -> tuple[Block, str]:
    """(the block, its phase). A new block starts when none is running, the cycle ended, or every game in it finished."""
    live = {f.game_id for f in fs}
    expired = block is not None and now >= block.since + p.hold_s + p.catchup_s
    if block is None or expired or not (set(block.games) & live):
        return Block(_pick_block(fs, block if expired else None, p), now), HOLD
    return block, HOLD if now < block.since + p.hold_s else CATCH_UP


def plan(facts: Iterable[Facts], *, now: float, block: Block | None = None, peak: int = 0,
         params: Params | None = None) -> Scene:
    """The scene for this tick: which games the rotation may cut to. The decider stores `scene.block` for the next tick."""
    p = params or Params()
    fs = sorted(facts, key=lambda f: f.game_id)
    m = mode_of(fs, peak, p)
    if m in (OFF, SINGLE, ENDING):
        return Scene(m, why=f"{len(fs)} live")
    forced, ids = _forced(fs), frozenset(f.game_id for f in fs)
    close = frozenset(f.game_id for f in fs if f.close)
    late = p.late_narrow and bool(close) and any(f.late for f in fs)
    phase, nb, why = HOLD, None, f"{len(fs)} live"
    if m == ANCHOR:
        a = anchor_of(fs, p)
        allowed = (ids if a.on_break else frozenset({a.game_id})) | forced
        why = f"anchor {a.game_id}{' on break' if a.on_break else ''}"
    elif m == WHIP:
        allowed = ids
    else:
        pool = relevant(fs, p) if m == FILTERED else fs
        nb, phase = step_block(pool, block, now, p)
        allowed = frozenset(nb.games)
        if phase == CATCH_UP:
            allowed |= frozenset(f.game_id for f in fs if f.unseen)
        allowed = (allowed & ids) | forced
        why = f"block of {len(nb.games)}"
    if late:
        allowed = allowed & (close | forced)
        why += ", late"
    return Scene(m, phase, allowed, nb, late, why)


def may_cut_to(scene: Scene | None, gid: str) -> bool:
    """No scene (flag off, or not computed yet): today's rotation."""
    return scene is None or gid in scene.games


def returns_too_soon(left_at: Mapping[str, float], gid: str, now: float, in_play: int, owed: bool, p: Params) -> bool:
    """Anti-ping-pong: no return inside return_gap_s without something new. With only two games live, ping-pong IS the
    show (the parked hidden stick), so the rule reads from three games up. A must-show always may."""
    if p.return_gap_s <= 0 or owed or in_play <= 2:
        return False
    t = left_at.get(gid)
    return t is not None and now - t < p.return_gap_s


def walk_safe_s(shot: str, p: Params) -> float:
    """How long after this shot a join in the walk up court is still ahead of the next live action. A make gives more
    than a rebound: the inbound has to happen first."""
    return p.walk_safe_made_s if shot == "made" else p.walk_safe_rebound_s


def pays_off(snap: bool, big: bool, on_break: bool, owed: bool, p: Params) -> bool:
    """A cut needs something to show: the next snap about to air, a big moment, a break to fill, a must-show."""
    return not p.payoff or snap or big or on_break or owed


_NOT_A_TRY = ("punt", "field goal", "kickoff", "timeout", "penalty")


def football_word(ptype: str, gain: int | None, down: int | None, to_go: int | None) -> str:
    """The moments/tiers word for a live football play, from what the Timeline now carries. "" for none. Matches
    detect._football: a 10+ yard rush or pass, a 3rd-and-5-or-longer conversion, a 4th-down try (not a punt or kick)."""
    t = (ptype or "").lower()
    if down == 4 and not any(k in t for k in _NOT_A_TRY):
        return "4th_try"
    if gain is None:
        return ""
    if down == 3 and (to_go or 0) >= 5 and gain >= (to_go or 99):
        return "big3rd"
    return "gain10" if gain >= 10 and any(k in t for k in ("rush", "pass")) else ""
