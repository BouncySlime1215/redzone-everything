"""SwitchPaths contract: every way to put a target on screen, each with a measured cost.

The path TYPE is the decision core's redzone/decider/core.py `SwitchPath(kind, cost_s, cost_p90_s,
preloaded)`; this file defines who produces them. Owner: SWITCHING ("Switch games instantly via TV
input"). Implementation today: redzone/control.py Controller (switch_costs / deadline_cost /
standby). Called while building the WorldState, once per target per tick; costs update after each
completed switch (LatencyBook). Units: seconds from command sent to picture confirmed.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from redzone.decider.core import SwitchPath

# core.SwitchPath.kind values
TV_FLIP, HOME_FLIP, IN_APP_TUNE, REPLAY_SEEK, NONE = "tv_flip", "home_flip", "in_app_tune", "replay_seek", "none"
# A tile of a YouTube TV multiview expanded to full screen (redzone/tuners/multiview.py; flag multiview.enabled).
MULTIVIEW = "multiview_expand"
KINDS = (TV_FLIP, HOME_FLIP, IN_APP_TUNE, REPLAY_SEEK, MULTIVIEW, NONE)


@runtime_checkable
class SwitchPaths(Protocol):
    def paths(self, target: str, now: float) -> tuple[SwitchPath, ...]:
        """Ready paths only (stick online, not busy); empty = can't reach it now."""
        ...

    def busy(self) -> bool:
        """A switch is in flight (the 'no overlap' guard reads this)."""
        ...


def deadline(paths: tuple[SwitchPath, ...]) -> float:
    """The planning bound the entry/trip guards budget: the cheapest path's p90 (cost_s if unset)."""
    if not paths:
        return float("inf")
    p = min(paths, key=lambda x: x.cost_s)
    return p.cost_p90_s or p.cost_s


# --- one source of truth for switch costs outside the live controller ---------------------------
# The live Controller prices paths from its own LatencyBook samples. Everything that prices a switch
# WITHOUT the controller (jump / reel labels, scorecards, the Kaggle teacher's briefs) uses
# ConfigSwitchPaths below, so it reads the same numbers the controller falls back to:
#   tunes      decider.switch_cost.<app>: the Mac's measured medians (switch_speed.learn wrote them
#              to data/config.yaml; eval-data-v1 pinned that snapshot), shipped in default_config.yaml
#   TV flip    two_stick.tv_flip_prior_s (SmartThings setInputSource on the user's TV, Nov 3 2026:
#              live lab Nov 4: 64/64 confirmed, median 1.89 s, p95 2.18 s) + two_stick.input_settle_s, exactly as
#              Controller.flip_estimate / _flip_upper price the tv_flip path before 5 own samples.
TUNE_APPS = ("youtubetv", "prime", "espn")
# [live lab] SmartThings input flips on the user's TV, Nov 4 2026 (docs/livelab-2026-11-04.md on the Mac):
# command to confirmed readback, 64/64 confirmed, 0 retries. p50 1.89, p95 2.18, max 2.58 s.
LIVELAB_FLIPS_S = (1.6, 1.66, 1.71, 1.71, 1.76, 1.77, 1.82, 1.83, 1.83, 1.83, 1.84, 1.84, 1.85, 1.85, 1.85,
                   1.86, 1.86, 1.86, 1.86, 1.87, 1.87, 1.87, 1.87, 1.87, 1.88, 1.88, 1.88, 1.88, 1.88, 1.88,
                   1.88, 1.89, 1.89, 1.9, 1.9, 1.9, 1.9, 1.9, 1.91, 1.91, 1.92, 1.92, 1.93, 1.93, 1.94, 1.95,
                   1.95, 2.0, 2.0, 2.02, 2.02, 2.02, 2.02, 2.04, 2.04, 2.06, 2.08, 2.1, 2.12, 2.14, 2.18, 2.23,
                   2.36, 2.58)
FLIP_PRIOR_CEC_ON = {"median": 1.89, "p95": 2.18, "max": 2.58}   # the 64 flips above (CEC on)
# [live lab, Nick Nov 4 11:35 AM] with HDMI-CEC off the stick we leave keeps PLAYING (83/83) and the
# flip tightens: 30/30, p50 1.89 s, p95 1.98 s, max 2.02 s. The planner's prior.
FLIP_PRIOR = {"median": 1.89, "p95": 1.98, "max": 2.02}
# Hidden-stick pre-tune to PLAYING (same live lab), by ADB model code: prefer the 4K Plus for hot pre-tunes.
PRETUNE_PRIOR_S = {
    "AFTMA08C15": {"name": "Fire TV Stick 4K Plus", "median": 2.33, "p95": 3.06},
    "AFTKM": {"name": "Fire TV Stick 4K (2023)", "median": 3.74, "p95": 5.50},
}
INPUT_SETTLE_S = 1.0   # two_stick.input_settle_s default: the picture after the input change


def sample_prior(prior: dict, rng) -> float:
    """One draw from a measured (median, p95[, max]) prior: a lognormal through the median and p95,
    clipped at the measured max. The sims' spread for flips and pre-tunes."""
    import math
    med, p95 = float(prior["median"]), float(prior.get("p95", prior["median"]))
    sd = math.log(max(p95, med) / med) / 1.645
    x = med * math.exp(rng.gauss(0.0, sd))
    return min(x, float(prior["max"])) if "max" in prior else x


def flip_bound(prior: dict) -> tuple[float, float]:
    """(median, planning upper bound) of the TV flip prior: its p95 when measured, else its max."""
    med = float(prior.get("median", FLIP_PRIOR["median"]))
    hi = prior.get("p95", prior.get("max", FLIP_PRIOR["p95"]))
    return med, max(med, float(hi))


def half_step(x: float) -> float:
    """Controller.switch_costs rounds to half seconds before writing decider.switch_cost."""
    return round(float(x) * 2) / 2


class ConfigSwitchPaths:
    """SwitchPaths priced from config alone (no stick, no samples). `on_hidden(target)` says whether
    the hidden stick already plays that target (a flip); otherwise a tune, plus the flip with two
    sticks. Read-only: it never touches hardware."""

    def __init__(self, cfg, two_stick: bool = True, on_hidden=None):
        self.cfg, self.two_stick = cfg, two_stick
        self.on_hidden = on_hidden or (lambda target: False)

    def _get(self, key: str, default):
        v = self.cfg.get(key) if hasattr(self.cfg, "get") else None
        return default if v is None else v

    def flip(self) -> SwitchPath:
        settle = float(self._get("two_stick.input_settle_s", 1.0))
        med, hi = flip_bound(self._get("two_stick.tv_flip_prior_s", {}) or {})
        return SwitchPath(TV_FLIP, med + settle, hi + settle)

    def tune(self, app: str) -> float:
        return float(self._get(f"decider.switch_cost.{app}", 5.0))

    def paths(self, target: str, now: float = 0.0) -> tuple[SwitchPath, ...]:
        app = (target or "").split(":", 1)[0]
        if app in ("", "none"):
            return ()
        flip = self.flip()
        if self.two_stick and self.on_hidden(target):
            return (SwitchPath(flip.kind, flip.cost_s, flip.cost_p90_s, preloaded=True),)
        t = self.tune(app)
        if not self.two_stick:
            return (SwitchPath(IN_APP_TUNE, t, t),)
        return (SwitchPath(IN_APP_TUNE, t + flip.cost_s, t + flip.cost_p90_s),)

    def busy(self) -> bool:
        return False

    def costs(self) -> dict[str, float]:
        """decider.switch_cost as the controller would write it on the TV-flip path with no samples."""
        return {"hdmi_flip": half_step(self.flip().cost_s), **{a: self.tune(a) for a in TUNE_APPS}}
