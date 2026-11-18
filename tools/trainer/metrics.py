"""Held-out metrics for the event model: NLL per token, per event and per field, top-1 accuracy, calibration error.

Why: TRAINING-PLAN principle 7 and track B section 2 ("judge by log loss and calibration, not accuracy"): every
evaluation reports nats per token, nats per event (the sum over the six fields, the unit B1 compares), NLL per
field (the unit the GBM baseline is compared on) and top-label expected calibration error (bar: at most 0.02).

Backends hand over per-target arrays (nll, top-1 probability, top-1 correct) so a 10,511-way softmax never leaves
the GPU. The aggregator keeps sums only, so it can run over any number of batches. Numpy only.
"""
from __future__ import annotations

import math

import numpy as np

ECE_BINS = 15


class EvalAggregator:
    def __init__(self, n_fields: int = 6, bins: int = ECE_BINS):
        self.n_fields, self.bins = n_fields, bins
        self.nll_sum = 0.0
        self.n = 0
        self.correct = 0.0
        self.field_nll = np.zeros(n_fields)
        self.field_n = np.zeros(n_fields, np.int64)
        self.bin_n = np.zeros(bins, np.int64)
        self.bin_conf = np.zeros(bins)
        self.bin_acc = np.zeros(bins)

    def add(self, nll, p_top1, correct, mask, field) -> None:
        """Arrays of one batch, shape [B, T]; mask is 1 on real targets, field is 0..5 on event fields, else -1."""
        m = np.asarray(mask) > 0
        nll = np.asarray(nll, np.float64)[m]
        conf = np.asarray(p_top1, np.float64)[m]
        cor = np.asarray(correct, np.float64)[m]
        fld = np.asarray(field)[m]
        self.nll_sum += float(nll.sum())
        self.n += int(m.sum())
        self.correct += float(cor.sum())
        for f in range(self.n_fields):
            sel = fld == f
            self.field_nll[f] += float(nll[sel].sum())
            self.field_n[f] += int(sel.sum())
        b = np.clip((conf * self.bins).astype(np.int64), 0, self.bins - 1)
        np.add.at(self.bin_n, b, 1)
        np.add.at(self.bin_conf, b, conf)
        np.add.at(self.bin_acc, b, cor)

    def result(self) -> dict:
        if not self.n:
            return {"tokens": 0}
        per_field = [float(self.field_nll[f] / self.field_n[f]) if self.field_n[f] else None
                     for f in range(self.n_fields)]
        nz = self.bin_n > 0
        ece = float(np.sum(np.abs(self.bin_acc[nz] - self.bin_conf[nz])) / self.n)
        nll_tok = self.nll_sum / self.n
        out = {"tokens": self.n, "nll_token": nll_tok, "ppl_token": math.exp(min(nll_tok, 50.0)),
               "acc_top1": self.correct / self.n, "ece_top1": ece}
        for f, v in enumerate(per_field):
            if v is not None:
                out[f"nll_field{f}"] = v
        if all(v is not None for v in per_field):
            out["nll_event"] = float(sum(per_field))
        return out


def stable_logsoftmax(logits: np.ndarray) -> np.ndarray:
    m = logits.max(axis=-1, keepdims=True)
    z = logits - m
    return z - np.log(np.exp(z).sum(axis=-1, keepdims=True))


def per_target(logits: np.ndarray, y: np.ndarray) -> dict:
    """nll, top-1 probability and top-1 correctness for each target, from full logits [B, T, V] (numpy backends)."""
    lp = stable_logsoftmax(logits)
    nll = -np.take_along_axis(lp, y[..., None], axis=-1)[..., 0]
    top = lp.argmax(axis=-1)
    return {"nll": nll, "p_top1": np.exp(lp.max(axis=-1)), "correct": (top == y).astype(np.float64)}
