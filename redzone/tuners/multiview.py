"""YouTube TV multiview as a switch path (SWITCHING). OFF until the at-home probe has measured it
(`multiview.enabled`, scripts/multiview_probe.py).

YouTube TV can play up to four games in one 2x2 screen, and Sunday Ticket can build NFL
multiviews. Expanding a tile to full screen should be near-instant, because all four streams are
already loaded, and BACK returns to the grid with the streams still warm. So:

  hidden stick   opens a multiview of the top YTTV candidates (MultiviewPlan). To show one of them,
                 expand its tile on the hidden stick, then flip: the same cost as a preloaded flip,
                 but for any of 4 games instead of 1.
  visible stick  showing an expanded tile, another tile of the same multiview is BACK + D-pad +
                 CENTER away: no HOME, no deep link, no app reload (vs ~4.5 s for an in-app tune).

Everything here works with any object that has `key(*codes)` and `playback(pkg)` (a Stick or a
fake). Each step is verified from the media session (YouTube TV names the channel in the
subtitle) or from an injected screen check. A wrong guess never presses CENTER on a
full-screen player: expand() only runs from a grid that has been confirmed.

Costs are SwitchPath values (redzone/decider/core.py), kind MULTIVIEW. The defaults are guesses
until the probe measures them; samples=0 says so.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from redzone.decider.core import SwitchPath

MULTIVIEW = "multiview_expand"
COLS = 2                     # 2x2 grid, row-major tile order (YouTube TV's 4-up layout)
MAX_TILES = 4
DEFAULT_COSTS = {            # seconds; replaced by measurements (scripts/multiview_probe.py)
    "mv_open": 15.0,         # cold: open a multiview from the app (navigation + 4 streams)
    "mv_expand": 1.5,        # grid -> one tile full screen (stream already loaded)
    "mv_collapse": 1.0,      # BACK: full screen -> grid
    "mv_step": 0.35,         # one D-pad move between tiles
}
KEY = {"up": "KEYCODE_DPAD_UP", "down": "KEYCODE_DPAD_DOWN", "left": "KEYCODE_DPAD_LEFT",
       "right": "KEYCODE_DPAD_RIGHT", "ok": "KEYCODE_DPAD_CENTER", "back": "KEYCODE_BACK"}


def moves(src: int, dst: int, cols: int = COLS) -> list[str]:
    """D-pad presses from tile src to tile dst in a row-major grid."""
    (r0, c0), (r1, c1) = divmod(src, cols), divmod(dst, cols)
    out = ["down" if r1 > r0 else "up"] * abs(r1 - r0)
    out += ["right" if c1 > c0 else "left"] * abs(c1 - c0)
    return out


@dataclass
class MultiviewSession:
    """What one stick's multiview shows. `expanded` = the tile full screen (None: the grid)."""
    stick: str
    tiles: tuple[str, ...]
    focus: int = 0
    expanded: str | None = None
    opened_at: float = 0.0
    grid_confirmed: bool = False

    def index(self, target: str) -> int | None:
        return self.tiles.index(target) if target in self.tiles else None


def plan_tiles(ranked: list[str], current: tuple[str, ...] = (), k: int = MAX_TILES) -> tuple[str, ...]:
    """The tiles for a multiview: the top-k YouTube TV targets, best first, de-duplicated. A tile
    already open keeps its position when it's still wanted, so a small change in ranking doesn't
    cost a reopen (the caller decides with needs_reopen)."""
    want = []
    for t in ranked:
        if t.startswith("youtubetv:") and t not in want:
            want.append(t)
        if len(want) == k:
            break
    keep = [t if t in want else None for t in current[:k]]
    rest = [t for t in want if t not in keep]
    out = [t if t is not None else (rest.pop(0) if rest else None) for t in keep] + rest
    return tuple(t for t in out if t)[:k]


def needs_reopen(session: MultiviewSession | None, ranked: list[str], must_have: int = 2) -> bool:
    """Reopen (expensive) only when one of the top `must_have` YouTube TV targets isn't a tile."""
    top = [t for t in ranked if t.startswith("youtubetv:")][:must_have]
    if not top:
        return False
    return session is None or any(t not in session.tiles for t in top)


@dataclass
class MultiviewCosts:
    """Median-style costs from measured samples (seconds), falling back to DEFAULT_COSTS."""
    samples: dict[str, list[float]] = field(default_factory=dict)
    defaults: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_COSTS))

    def record(self, key: str, seconds: float, keep: int = 50) -> None:
        xs = self.samples.setdefault(key, [])
        xs.append(float(seconds))
        del xs[:-keep]

    def get(self, key: str, q: float = 0.5) -> float:
        xs = sorted(self.samples.get(key) or ())
        if not xs:
            return self.defaults[key]
        return xs[min(len(xs) - 1, int(q * len(xs)))]

    def n(self, key: str) -> int:
        return len(self.samples.get(key) or ())

    def path(self, session: MultiviewSession | None, target: str, on_screen: bool,
             flip_s: float = 0.0, flip_p90_s: float = 0.0) -> SwitchPath | None:
        """The SwitchPath to `target` through this multiview, or None if it isn't a tile.

        on_screen   the multiview's stick is the one the TV shows: collapse, move, expand; no flip
        else        expand on the hidden stick first (nothing on screen changes), then flip; a tile
                    already expanded there is a plain preloaded flip
        """
        if session is None or (i := session.index(target)) is None:
            return None
        if on_screen and session.expanded == target:
            return None                                 # already on screen: not a switch
        steps = len(moves(session.focus, i))
        prep = steps * self.get("mv_step") + self.get("mv_expand")
        prep90 = steps * self.get("mv_step", .9) + self.get("mv_expand", .9)
        if session.expanded is not None and session.expanded != target:
            prep += self.get("mv_collapse")
            prep90 += self.get("mv_collapse", .9)
        n = min(self.n("mv_expand"), self.n("mv_step") or self.n("mv_expand"))
        if on_screen:
            return SwitchPath(MULTIVIEW, round(prep, 2), round(prep90, 2), preloaded=True, stick=session.stick,
                              samples=n)
        if session.expanded == target:
            return SwitchPath(MULTIVIEW, round(flip_s, 2), round(flip_p90_s or flip_s, 2), preloaded=True,
                              stick=session.stick, samples=n)
        # Expanding on the hidden stick happens before the cut (off screen), so only the flip is
        # dead air, but the deadline has to budget the expand too.
        return SwitchPath(MULTIVIEW, round(flip_s, 2), round((flip_p90_s or flip_s) + prep90, 2), preloaded=True,
                          stick=session.stick, samples=n)


@dataclass
class StepResult:
    ok: bool
    took: float
    detail: str = ""


class MultiviewDriver:
    """Moves around one stick's multiview, verifying every step.

    matches(target, report) -> True/False/None: does this playback report show `target` full
    screen (YouTube TV's subtitle names the channel)? grid_check() -> True/False/None: is the
    grid on screen (a screenshot read)? None means "can't tell", and the driver then refuses
    rather than guesses.
    """

    def __init__(self, stick, pkg: str, session: MultiviewSession,
                 matches: Callable[[str, dict], bool | None],
                 grid_check: Callable[[], Awaitable[bool | None]] | None = None,
                 costs: MultiviewCosts | None = None, clock: Callable[[], float] = time.monotonic,
                 poll_s: float = 0.25, timeout_s: float = 6.0):
        self.stick, self.pkg, self.s = stick, pkg, session
        self.matches, self.grid_check = matches, grid_check
        self.costs = costs or MultiviewCosts()
        self.clock, self.poll_s, self.timeout_s = clock, poll_s, timeout_s

    async def _wait(self, cond: Callable[[dict], bool]) -> dict | None:
        t0 = self.clock()
        while True:
            pb = await self.stick.playback(self.pkg)
            if pb.get("read_ok") is not False and cond(pb):
                return pb
            if self.clock() - t0 >= self.timeout_s:
                return None
            await asyncio.sleep(self.poll_s)

    async def confirm_grid(self) -> bool:
        if self.grid_check is None:
            return self.s.grid_confirmed
        seen = await self.grid_check()
        self.s.grid_confirmed = seen is True
        return self.s.grid_confirmed

    async def collapse(self) -> StepResult:
        """Full screen -> grid (BACK). Verified when the media session stops naming the tile alone,
        or by the screen check."""
        if self.s.expanded is None:
            return StepResult(True, 0.0, "already the grid")
        was = self.s.expanded
        t0 = self.clock()
        await self.stick.key(KEY["back"])
        self.s.expanded = None
        self.s.grid_confirmed = False
        if self.grid_check is not None:
            ok = await self.confirm_grid()
        else:
            ok = await self._wait(lambda pb: self.matches(was, pb) is not True) is not None
            self.s.grid_confirmed = ok
        took = self.clock() - t0
        if ok:
            self.costs.record("mv_collapse", took)
        return StepResult(ok, took, "grid" if ok else "BACK sent; grid not confirmed")

    async def expand(self, target: str) -> StepResult:
        """Show `target` full screen: from the grid, D-pad to its tile, CENTER, and wait for the
        media session to name it."""
        i = self.s.index(target)
        if i is None:
            return StepResult(False, 0.0, f"{target} is not a tile")
        t0 = self.clock()
        if self.s.expanded == target:
            pb = await self.stick.playback(self.pkg)
            if self.matches(target, pb) is not False:
                return StepResult(True, 0.0, "already full screen")
        if self.s.expanded is not None:
            r = await self.collapse()
            if not r.ok:
                return StepResult(False, self.clock() - t0, r.detail)
        if not await self.confirm_grid():
            return StepResult(False, self.clock() - t0, "grid not confirmed: CENTER not sent")
        path = moves(self.s.focus, i)
        if path:
            m0 = self.clock()
            await self.stick.key(*(KEY[k] for k in path))
            self.costs.record("mv_step", (self.clock() - m0) / len(path))
        self.s.focus = i
        e0 = self.clock()
        await self.stick.key(KEY["ok"])
        self.s.grid_confirmed = False
        pb = await self._wait(lambda pb: pb.get("state") == "playing" and self.matches(target, pb) is True)
        if pb is None:
            # Where are we? Unknown, so the next step has to confirm the grid before any CENTER.
            self.s.expanded = None
            return StepResult(False, self.clock() - t0, f"{target} not confirmed full screen")
        self.s.expanded = target
        self.costs.record("mv_expand", self.clock() - e0)
        return StepResult(True, self.clock() - t0, f"tile {i + 1} full screen")
