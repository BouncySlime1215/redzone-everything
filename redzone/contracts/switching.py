"""SwitchPaths contract: every way to put a target on screen, each with a measured cost.

Why: Architecture contract v1 (2026-11-03): one interface for switch paths and their measured costs (decisions 2026-11-04, switch costs have one source of truth).

The path TYPE is the decision core's redzone/decider/core.py `SwitchPath(kind, cost_s, cost_p90_s,
preloaded)`; this file defines who produces them. Owner: SWITCHING ("Switch games instantly via TV
input"). Implementation today: redzone/control.py Controller (switch_costs / deadline_cost /
standby). Called while building the WorldState, once per target per tick; costs update after each
completed switch (LatencyBook). Units: seconds from command sent to picture confirmed.
"""
from __future__ import annotations

from dataclasses import dataclass
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
#              live lab Nov 4, CEC off: 30/30, p50 1.89 s, p95 1.98 s, max 2.02 s) + two_stick.input_settle_s, exactly as
#              Controller.flip_estimate / _flip_upper price the tv_flip path before 5 own samples.
TUNE_APPS = ("youtubetv", "prime", "espn")
# [live lab] SmartThings input flips on the user's TV, Nov 4 2026 (docs/livelab-2026-11-04.md on the Mac):
# command to confirmed readback, 64/64 confirmed, 0 retries. p50 1.89, p95 2.18, max 2.58 s.
FLIP_PRIOR_CEC_ON = {"median": 1.89, "p95": 2.18, "max": 2.58}   # those 64 flips (CEC on)
# [live lab, Nick Nov 4 11:35 AM] with HDMI-CEC off the stick we leave keeps PLAYING (83/83) and the
# flip tightens: 30/30, p50 1.89 s, p95 1.98 s, max 2.02 s. The planner's prior.
FLIP_PRIOR = {"median": 1.89, "p95": 1.98, "max": 2.02}
# Hidden-stick pre-tune to PLAYING (same live lab), by ADB model code: prefer the 4K Plus for hot pre-tunes.
PRETUNE_PRIOR_S = {
    "AFTMA08C15": {"name": "Fire TV Stick 4K Plus", "median": 2.33, "p95": 3.06},
    "AFTKM": {"name": "Fire TV Stick 4K (2023)", "median": 3.74, "p95": 5.50},
}


def sample_prior(prior: dict, rng) -> float:
    """One draw from a measured (median, p95[, max]) prior: a lognormal through the median and p95,
    clipped at the measured max. The sims' spread for flips and pre-tunes."""
    import math
    med, p95 = float(prior["median"]), float(prior.get("p95", prior["median"]))
    sd = math.log(max(p95, med) / med) / 1.645
    x = med * math.exp(rng.gauss(0.0, sd))
    return min(x, float(prior["max"])) if "max" in prior else x


def flip_bound(prior: dict) -> tuple[float, float]:
    """(median, planning upper bound) of the TV flip prior. The bound is the measured max: landing
    deadlines guard rule #1 (decisions.md, Nov 4); the p95 only where no max was measured."""
    med = float(prior.get("median", FLIP_PRIOR["median"]))
    hi = prior.get("max", prior.get("p95", FLIP_PRIOR["max"]))
    return med, max(med, float(hi))


@dataclass(frozen=True)
class SeekModel:
    """YouTube TV's DVR rewind as the stick really does it (live lab, Nov 4 2026, Nick 12:10 PM):
    each press steps `step` s, the FIRST press of a batch is swallowed, and each seek stalls the
    picture `stall_lo`..`stall_hi` s (the stall adds to how far behind the picture lands). A "60 s"
    rewind of 6 presses at an assumed 10 s step landed 75-80 s back, a "20 s" one 15-19 s back.

    effective(back_s): the presses that count, rounded UP so the landing is never short of back_s
    by more than late_ok (a replay shows its snap; an entry's plan already holds the stall).
    sent(back_s): what control.py sends (effective + swallowed). landing(n_eff, stall): how far
    behind the picture comes up."""
    step: float = 15.0
    swallowed: int = 0
    stall_lo: float = 0.0
    stall_hi: float = 0.0
    late_ok: float = 1.0

    @property
    def stall_mid(self) -> float:
        return (self.stall_lo + self.stall_hi) / 2

    def effective(self, back_s: float) -> int:
        import math
        return min(60, max(0, math.ceil((back_s - self.late_ok - self.stall_lo) / self.step - 1e-9)))

    def sent(self, back_s: float) -> int:
        n = self.effective(back_s)
        return n + self.swallowed if n else 0

    def landed(self, sent: int, stall: float | None = None) -> float:
        """Seconds behind for `sent` presses on THIS model's stick (stall: the mid by default)."""
        n = max(0, sent - self.swallowed)
        return n * self.step + (self.stall_mid if stall is None else stall) if sent else 0.0


# The stick as measured (the sims' truth). Config replay.seek_* is what control.py ASSUMES.
YTTV_SEEK = SeekModel(step=15.0, swallowed=1, stall_lo=2.0, stall_hi=4.0)


_CALIBRATED_SEEK: SeekModel | None = None


def set_calibrated_seek(model: SeekModel | None) -> None:
    """[calib.auto] last nights' accepted seek fit (redzone/calibration.sync_prior), in memory; None = config."""
    global _CALIBRATED_SEEK
    _CALIBRATED_SEEK = model


def seek_model(cfg) -> SeekModel:
    """What control.py assumes about the rewind (replay.seek_step_s / seek_wake_presses /
    seek_stall_min_s / seek_stall_max_s), or the calibrated fit when calib.auto set one at start."""
    if _CALIBRATED_SEEK is not None:
        return _CALIBRATED_SEEK
    lo = float(cfg.get("replay.seek_stall_min_s", 0.0) or 0.0)
    hi = float(cfg.get("replay.seek_stall_max_s", lo) or 0.0)
    return SeekModel(step=float(cfg.get("replay.seek_step_s", 15) or 15),
                     swallowed=int(cfg.get("replay.seek_wake_presses", 0) or 0),
                     stall_lo=min(lo, hi), stall_hi=max(lo, hi))


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
