"""The shape of a show director: exactly what `park_show.Show` uses of the object it weighs games with (task A-04).

`Show` reads, of its director, only these (redzone/noggin/park_show.py, `Show.pref`, `Show.prospect`, `Show._weighed`,
`Show._jump`, `Show._landed`):
  at      the show second the last weights were made (None before the first); `Show._weighed` re-weighs when it differs
          from the show's second or when `now` does not hold one entry per game
  now     the last weights, one per game, each with a float `w` (the multiplier on `Show.pref` and `Show.prospect`)
  weigh(t, on, arrs, edges, seen)  make the weights at show second t (`on`: the game on screen, `arrs`: the show's
          arrays, `edges[g]`: the feed's last reported second of game g, `seen`: the show's `seen.SeenTracker`); it must
          set `now` and `at`; the show ignores what it returns
  why(g1, g2, base_why) -> {"driver": "a" | "b" | "c", "director": words}  the goal that drove a jump from g1 to g2
Director v1 (`redzone/noggin/director.Director`) is the reference and satisfies this unchanged.

The flag `park.director_v2` (default OFF) lets a caller hand `Show` such an object (`Show(..., director_v2=True,
director_obj=obj)`). Off, or on with nothing handed over, the show builds Director v1 from its params exactly as before.
The director only weighs games: the planner still plans, every jump still needs its warrant, and Rule #1 (the show's
guard, the padding, the lateness cap) stays outside it.

Why: task A-04 (redzone-agents/v2/WORKPLAN.md, track A-director section 9): the trained director of track A needs one
seam in the live loop; `Show.__init__` built Director v1 from params, so nothing else could be plugged in.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol, runtime_checkable

DRIVERS = frozenset({"a", "b", "c"})     # the letters `Show._landed` counts in `Show.drivers`


@runtime_checkable
class GameWeight(Protocol):
    """One game's weight at one choice: `w` multiplies `Show.pref` and `Show.prospect`."""

    @property
    def w(self) -> float: ...


@runtime_checkable
class ShowDirector(Protocol):
    """What `park_show.Show` calls on its director (see the module docstring)."""

    at: int | None
    now: Sequence[GameWeight]

    def weigh(self, t: int, on: int, arrs: Sequence, edges: Sequence[int], seen) -> Sequence[GameWeight]: ...

    def why(self, g1: int, g2: int, base_why: str = "") -> dict: ...


def is_show_director(obj: object) -> bool:
    """True when `obj` has every attribute and method `Show` uses (`at`, `now`, a callable `weigh` and `why`)."""
    return isinstance(obj, ShowDirector) and callable(getattr(obj, "weigh", None)) and callable(getattr(obj, "why", None))


def director_v2_on(get: Callable[..., object]) -> bool:
    """park.director_v2 from a config getter (`cfg.get`): True only for a literal True, as every park flag."""
    return get("park.director_v2", False) is True
