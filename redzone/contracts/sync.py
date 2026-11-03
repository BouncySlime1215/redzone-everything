"""SyncModel contract: how far each channel's picture runs behind real life.

Owner: SYNC ("Predict live delay per channel"). Implementation today: redzone/decider/screen.py
DelayBook (wrapped by redzone/contracts/legacy.py LegacySync).
Called by: DecisionCore and Guard (leave gate / entry plan), BreakModel consumers that convert
feed time to screen time. Cadence: estimate() on every decision tick (3-10 s); observe() whenever
a sample arrives (tap, score bug OCR, show-change boundary, mic).

Units: seconds. screen_time = feed_wallclock + delay_s. sigma_s is one standard deviation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

SampleKind = Literal["tap", "screen", "boundary", "mic", "predicted"]
SyncSource = Literal["tap", "screen", "app", "boundary", "predicted", "default"]


@dataclass(frozen=True)
class SyncEstimate:
    target: str              # tuner target, e.g. "youtubetv:espn"
    delay_s: float           # mean picture delay behind real life
    sigma_s: float           # current doubt (grows between calibrations)
    source: SyncSource       # where the number came from
    calibrated_at: float | None = None   # epoch s of the newest sample used, None = never
    behind_s: float = 0.0    # extra rewind applied on entry (already included in delay_s)

    @property
    def calibrated(self) -> bool:
        return self.source not in ("default", "predicted")


@runtime_checkable
class SyncModel(Protocol):
    def estimate(self, target: str, now: float) -> SyncEstimate: ...
    def observe(self, target: str, sample_s: float, kind: SampleKind, at: float) -> None: ...
