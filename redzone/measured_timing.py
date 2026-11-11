"""[timing.measured, off] One source for the stick timings measured on Nick's TV, Nov 9.

Why: the flip was 3.2 / 3.0 / 2.5 / 1.89 / 5.0 s across files and the clip prep a constant 15 s; the TV measured a 1.9 s flip
(max 2.2), an 18-25 s clip load and a 175 s seek reach (docs/plans/2026-11-09-anchor-mode.md sections 7-8).

Small samples, on a busy Mac (TV test 3e re-times the prep on a quiet one):
  input flip (SmartThings)      9 of 9: 1.83 / 1.87 / 2.22 s and six at 2.1-2.2 s; median about 1.9, max 2.2
  tune, picture confirmed       2.3-4.3 s (n = 13)
  clip load on the hidden stick tune + 3 s wait + seek: 19.0 / 18.6 / 24.8 s, and 18.0-18.8 s in a 3-clip chain (n = 6)
  seek landing (time label)     28.7 s + 16.5 s per LEFT press behind live (6 of 6, two games); deepest about 175-177 s

Flag off, every caller keeps reading its own constant exactly as before: `route_costs` returns None and the caller falls back.
Pure: reads a config-like object (`get(dotted, default)`), no I/O.
"""
from __future__ import annotations

TIMING_FLAG = "timing.measured"
# The shipped values (default_config.yaml timing.*), used when a config lacks a key.
MEASURED = {"flip_s": 1.9, "flip_max_s": 2.2, "tune_s": 3.0, "tune_max_s": 4.3, "clip_prep_s": 19.0, "clip_prep_max_s": 25.0,
            "seek_base_s": 28.7, "seek_per_press_s": 16.5, "reach_s": 175.0}


def on(cfg) -> bool:
    return bool(cfg is not None and hasattr(cfg, "get") and cfg.get(TIMING_FLAG, False))


def value(cfg, key: str) -> float:
    """timing.<key> from the config, else the measured value shipped here."""
    v = cfg.get(f"timing.{key}") if cfg is not None and hasattr(cfg, "get") else None
    return float(MEASURED[key] if v is None else v)


def route_costs(cfg) -> dict[str, float] | None:
    """The catch-up route's costs with the flag on: every flip planned at the slowest measured one (the flip back must land before
    the live game restarts), a clip load at its typical time, the seek reach. None with the flag off."""
    if not on(cfg):
        return None
    return {"flip_s": value(cfg, "flip_max_s"), "prep_s": value(cfg, "clip_prep_s"), "reach_s": value(cfg, "reach_s")}

