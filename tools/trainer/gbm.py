"""Gradient-boosted-trees baseline for the same task as the event model: predict one field of the next event.

Why: WORKPLAN B-03 ("GBM baseline wrapper") and track B section 2: no sequence model has beaten tuned boosted
trees on play-by-play in the literature we read, so every model claim (B3, B5) is a log-loss gain over this
baseline on the same held-out games. TRAINING-PLAN rule 5: the baseline gets the same tuning effort.

The task: the token at field f of event e, given the previous k events (all six fields) and this event's earlier
fields, the league token and the event's index in the game. Features are causal by construction (only earlier
tokens). The model is a LightGBM multiclass over the field's most frequent tokens plus an OTHER class whose mass
is spread evenly over the rest of the field's ids, mixed with a small uniform floor, so its NLL is a proper
log loss over the whole field and directly comparable with the event model's NLL on that field (metrics.py).
LightGBM runs single-threaded (L102: its OpenMP is not fork-safe after use).
"""
from __future__ import annotations

import math

import numpy as np

# id ranges per field in vocab v1 (scripts/noggin_tokens.py): kind 4x2048, dt 32 + UNK_DT, clock 10x16,
# margin 41, pts 16, stop 12
FIELD_VOCAB_V1 = (8192, 33, 160, 41, 16, 12)
GBM_UNIFORM_FLOOR = 1e-4


def field_examples(docs: list, field: int, k_prev: int = 2, tokens_per_event: int = 6, header: int = 3,
                   max_rows: int | None = None, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """(X, y) for one field over every event of every game. X columns: league token, event index, the previous
    k_prev events' tokens (-1 when there is none), this event's tokens before `field` (-1 for later fields)."""
    if not 0 <= field < tokens_per_event:
        raise ValueError("field out of range")
    X_rows, ys = [], []
    for d in docs:
        t = np.asarray(d.tokens, np.int64)
        n_ev = (len(t) - header - 1) // tokens_per_event
        if n_ev <= 0:
            continue
        ev = t[header: header + n_ev * tokens_per_event].reshape(n_ev, tokens_per_event)
        for e in range(n_ev):
            prev = np.full((k_prev, tokens_per_event), -1, np.int64)
            lo = max(0, e - k_prev)
            if e > lo:
                prev[k_prev - (e - lo):] = ev[lo:e]
            cur = np.full(tokens_per_event - 1, -1, np.int64)
            cur[:field] = ev[e, :field]
            X_rows.append(np.concatenate([[t[0], min(e, 4095)], prev.ravel(), cur]))
            ys.append(ev[e, field])
    if not X_rows:
        raise ValueError("no events")
    X, y = np.asarray(X_rows, np.int64), np.asarray(ys, np.int64)
    if max_rows is not None and len(y) > max_rows:
        keep = np.sort(np.random.default_rng(seed).choice(len(y), max_rows, replace=False))
        X, y = X[keep], y[keep]
    return X, y


class GBMFieldBaseline:
    def __init__(self, field: int, field_vocab: int, max_classes: int = 64, num_boost_round: int = 200,
                 params: dict | None = None, seed: int = 0):
        self.field, self.field_vocab, self.max_classes = field, int(field_vocab), int(max_classes)
        self.num_boost_round, self.seed = num_boost_round, seed
        self.params = {"objective": "multiclass", "learning_rate": 0.1, "num_leaves": 31, "min_data_in_leaf": 20,
                       "num_threads": 1, "deterministic": True, "force_row_wise": True, "seed": seed,
                       "verbose": -1, **(params or {})}
        self.head: np.ndarray | None = None
        self.booster = None

    def _labels(self, y: np.ndarray) -> np.ndarray:
        pos = {int(tok): i for i, tok in enumerate(self.head)}
        other = len(self.head)
        return np.asarray([pos.get(int(v), other) for v in y], np.int64)

    def fit(self, X: np.ndarray, y: np.ndarray, X_valid=None, y_valid=None, early_stopping: int = 20):
        import lightgbm as lgb
        toks, counts = np.unique(y, return_counts=True)
        order = np.lexsort((toks, -counts))                     # most frequent first, ties by id: deterministic
        self.head = toks[order][: self.max_classes - 1]
        lab = self._labels(y)
        n_class = len(self.head) + 1
        params = {**self.params, "num_class": n_class}
        cat = list(range(X.shape[1]))
        cat.remove(1)                                           # event index is ordinal
        train = lgb.Dataset(X, lab, categorical_feature=cat, free_raw_data=False)
        valid, cbs = [], []
        if X_valid is not None:
            valid = [lgb.Dataset(X_valid, self._labels(y_valid), categorical_feature=cat, reference=train)]
            cbs = [lgb.early_stopping(early_stopping, verbose=False)]
        self.booster = lgb.train(params, train, num_boost_round=self.num_boost_round, valid_sets=valid,
                                 callbacks=cbs)
        return self

    def token_prob(self, X: np.ndarray, y: np.ndarray) -> np.ndarray:
        """P(y) over the whole field vocabulary: head class, or OTHER spread over the non-head ids, plus a floor."""
        p = self.booster.predict(X, num_iteration=self.booster.best_iteration or None)
        lab = self._labels(y)
        other = len(self.head)
        n_rest = max(1, self.field_vocab - len(self.head))
        q = np.where(lab < other, p[np.arange(len(y)), lab], p[:, other] / n_rest)
        return (1.0 - GBM_UNIFORM_FLOOR) * q + GBM_UNIFORM_FLOOR / self.field_vocab

    def nll(self, X: np.ndarray, y: np.ndarray) -> float:
        """Mean negative log-likelihood in nats per target over the field (the unit of metrics.nll_field<f>)."""
        return float(-np.mean(np.log(self.token_prob(X, y))))


def relative_gain(model_nll: float, baseline_nll: float) -> float:
    """(baseline - model) / baseline: the 'at least 3% relative log-loss gain' unit of bar B3."""
    if not (math.isfinite(model_nll) and math.isfinite(baseline_nll)) or baseline_nll <= 0:
        raise ValueError("NLLs must be finite and the baseline positive")
    return (baseline_nll - model_nll) / baseline_nll
