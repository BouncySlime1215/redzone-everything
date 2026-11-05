"""ReelProvider contract: a ready catch-up reel of plays you missed. Feeds core.ReelView / Option
through redzone/contracts/views.py reel_option().

Owner: DECISION (redzone/reel.py; it was ported with drive priority work). Called by: DecisionCore
as a break filler candidate. Cadence: refreshed from the poll loop; ready() per decision tick.

Units: seconds of video, value 0..100 on the Scorer's scale.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ReelOffer:
    target: str                 # tuner target, e.g. "reel:1730671200"
    seconds: float              # how long it plays
    value: float                # 0..100: what it's worth as a candidate
    built_at: float             # epoch s
    clip_ids: tuple[str, ...] = field(default_factory=tuple)


@runtime_checkable
class ReelProvider(Protocol):
    def ready(self, now: float) -> ReelOffer | None: ...
    def played(self, offer: ReelOffer) -> None:
        """It went on screen: its plays count as seen."""
        ...


@dataclass(frozen=True)
class ReelJump:
    """redzone/reel.py reel_jump_value(): is jumping to the reel now worth more than staying?"""
    ev_reel: float      # average points per second on screen over the window if the reel plays (core.ev units)
    ev_stay: float      # the same for staying on the current game
    cost_s: float       # switch dead air there and back (SwitchPaths cost_p90_s)
    ok: bool            # ev_reel - ev_stay pays for cost_s AND the reel fits (core.reel_fits on the p10 end)
    reason: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "ReelJump":
        return cls(float(d["ev_reel"]), float(d["ev_stay"]), float(d["cost"]), bool(d["ok"]), str(d.get("reason", "")))


@runtime_checkable
class ReelJumper(Protocol):
    def jump_value(self, world: "object", offer: ReelOffer, window_s: float) -> ReelJump: ...
