"""Loss and gradient-norm spike monitor: OLMo 2's spike score plus a divergence halt.

Why: TRAINING-PLAN 3.2 "Stability" and track B ADOPT item 6 (OLMo 2): log a spike score (the share of recent
values at least z standard deviations above the rolling mean of the values before them) for the loss and the
gradient norm, and stop a run whose loss goes non-finite or keeps spiking instead of burning GPU hours.

The state is plain lists and numbers so it goes into every checkpoint and a resumed run flags exactly what an
uninterrupted run flags. Pure Python.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class SpikeMonitor:
    window: int = 200            # rolling history the mean and std come from (OLMo 2 used 1000 on long runs)
    z: float = 7.0               # OLMo 2's threshold
    min_history: int = 20        # no verdicts before this many values
    halt_after: int = 5          # this many spikes in a row (loss) stops the run
    loss_hist: list = field(default_factory=list)
    gnorm_hist: list = field(default_factory=list)
    loss_flags: list = field(default_factory=list)     # 0/1 per recent step, same window
    gnorm_flags: list = field(default_factory=list)
    streak: int = 0
    total_loss_spikes: int = 0
    total_gnorm_spikes: int = 0
    nonfinite: int = 0

    @staticmethod
    def _is_spike(hist: list, v: float, z: float, min_history: int) -> bool:
        if len(hist) < min_history:
            return False
        n = len(hist)
        mean = sum(hist) / n
        var = sum((h - mean) ** 2 for h in hist) / n
        std = math.sqrt(var)
        return v > mean + z * max(std, 1e-12 * max(1.0, abs(mean)))

    def _push(self, hist: list, flags: list, v: float, spike: bool) -> None:
        hist.append(v)
        flags.append(1 if spike else 0)
        del hist[:-self.window]
        del flags[:-self.window]

    def update(self, loss: float, grad_norm: float | None = None) -> dict:
        """Record one optimizer step. Returns {'loss_spike', 'gnorm_spike', 'halt', 'reason'}."""
        out = {"loss_spike": False, "gnorm_spike": False, "halt": False, "reason": ""}
        if not math.isfinite(loss) or (grad_norm is not None and not math.isfinite(grad_norm)):
            self.nonfinite += 1
            out.update(halt=True, reason=f"non-finite {'loss' if not math.isfinite(loss) else 'gradient norm'}")
            return out
        ls = self._is_spike(self.loss_hist, loss, self.z, self.min_history)
        self._push(self.loss_hist, self.loss_flags, loss, ls)
        out["loss_spike"] = ls
        if ls:
            self.total_loss_spikes += 1
            self.streak += 1
        else:
            self.streak = 0
        if grad_norm is not None:
            gs = self._is_spike(self.gnorm_hist, grad_norm, self.z, self.min_history)
            self._push(self.gnorm_hist, self.gnorm_flags, grad_norm, gs)
            out["gnorm_spike"] = gs
            self.total_gnorm_spikes += int(gs)
        if self.streak >= self.halt_after:
            out.update(halt=True, reason=f"{self.streak} loss spikes in a row")
        return out

    @property
    def loss_spike_score(self) -> float:
        """Share of the last `window` steps whose loss was a spike (OLMo 2's spike score, as a fraction)."""
        return sum(self.loss_flags) / len(self.loss_flags) if self.loss_flags else 0.0

    @property
    def gnorm_spike_score(self) -> float:
        return sum(self.gnorm_flags) / len(self.gnorm_flags) if self.gnorm_flags else 0.0

    def state(self) -> dict:
        return {k: (list(v) if isinstance(v, list) else v) for k, v in self.__dict__.items()}

    @classmethod
    def from_state(cls, st: dict) -> "SpikeMonitor":
        return cls(**{k: (list(v) if isinstance(v, list) else v) for k, v in st.items()})
