"""Warmup-stable-decay (WSD) learning-rate schedule with branchable cooldowns.

Why: TRAINING-PLAN 3.2 "Schedule" and track B ADOPT item 2 (Hagele; MiniCPM; Kimi K2): warm up about 2%,
hold the peak, cool down over the last 10-20%; one long stable run can branch a cooldown at 2, 4, 8 and 16
epochs to price the epoch count cheaply.

    lr = wsd_lr(step, total_steps=1000, peak=3e-3)                     # one full run
    lr = wsd_lr(step, total_steps=1000, peak=3e-3, decay_start=400)    # a branch cooling down from step 400

Steps are optimizer steps counted from 0. Pure Python, no numpy.
"""
from __future__ import annotations

import math

DECAY_SHAPES = ("linear", "sqrt", "cosine")


def wsd_lr(step: int, total_steps: int, peak: float, warmup_frac: float = 0.02, decay_frac: float = 0.15,
           final_frac: float = 0.0, shape: str = "sqrt", decay_start: int | None = None,
           warmup_steps: int | None = None) -> float:
    """The learning rate at optimizer step `step` (0-based).

    warmup: linear from peak/warmup to peak over the first warmup steps (warmup_frac of total, at least 1);
    stable: peak; decay: from peak to final_frac*peak over the last decay_frac of total (or from decay_start
    to total_steps for a branch). shape 'sqrt' is 1 - sqrt(progress) (Hagele et al.'s best), 'linear' and
    'cosine' are the usual ones. Steps at or past total_steps return the final value."""
    if total_steps <= 0 or peak < 0:
        raise ValueError("total_steps must be positive and peak non-negative")
    if shape not in DECAY_SHAPES:
        raise ValueError(f"shape {shape!r} not in {DECAY_SHAPES}")
    if not 0.0 <= final_frac <= 1.0:
        raise ValueError("final_frac must be in [0, 1]")
    warm = warmup_steps if warmup_steps is not None else max(1, int(round(warmup_frac * total_steps)))
    d0 = decay_start if decay_start is not None else total_steps - int(round(decay_frac * total_steps))
    if not 0 <= warm <= d0 <= total_steps:
        raise ValueError(f"need 0 <= warmup ({warm}) <= decay start ({d0}) <= total ({total_steps})")
    final = final_frac * peak
    if step < warm:
        return peak * (step + 1) / warm
    if step < d0:
        return peak
    span = total_steps - d0
    if span <= 0 or step >= total_steps:
        return final
    p = (step - d0) / span                      # 0 at the first cooldown step, < 1 before the end
    if shape == "linear":
        f = 1.0 - p
    elif shape == "sqrt":
        f = 1.0 - math.sqrt(p)
    else:
        f = 0.5 * (1.0 + math.cos(math.pi * p))
    return final + (peak - final) * f


def branch_points(steps_per_epoch: int, epochs=(2, 4, 8, 16)) -> list[int]:
    """Optimizer steps at which a cooldown branch starts, one per epoch count (the stable run's checkpoints)."""
    if steps_per_epoch <= 0:
        raise ValueError("steps_per_epoch must be positive")
    return [int(e * steps_per_epoch) for e in epochs]
