"""Active pair selection for the Game Picker: which card pair to ask Nick next, from the factorised model's posterior.

Why: track D experiment D1 (active selection must beat random pairs by 3+ points at the same budget). Two rules:

    bald      expected information gain about the parameters (BALD, Houlsby et al. 2011, probit approximation of the
              logistic: mu' = mu*sqrt(pi/8), var' = var*pi/8), mu and var = the posterior mean and variance of
              u(a) - u(b) under the Laplace posterior (var = d' Cov d, d = the Jacobian difference)
    max_var   the cheaper rule: among "comparable" pairs (|mu| <= 1, i.e. 27%-73% predicted), the largest var

`replay_curves` is an OFFLINE bandit replay over a fixed pool: the selector may only take answers Nick already gave
(in their real form), so it can show whether ORDERING his existing answers by the rule learns faster than a random
order. It cannot show the gain from generating new pairs, and it cannot know how he would have answered a pair he
was never shown. Nothing in the show imports this module.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from redzone.lab.taste.factorised_bt import (
    Card, FactorisedBT, FactorisedPrior, answer_cards, answer_events, answer_pairs, pairwise_hits,
)

_PROBIT_SCALE = math.pi / 8.0
_BALD_C2 = math.pi * math.log(2.0) / 2.0


def _binary_entropy_bits(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-12, 1 - 1e-12)
    return -(p * np.log2(p) + (1 - p) * np.log2(1 - p))


def bald_gain(mu: np.ndarray, var: np.ndarray) -> np.ndarray:
    """Expected information gain (bits) of one binary comparison with utility difference ~ N(mu, var), logistic link."""
    m = np.asarray(mu, dtype=float) * math.sqrt(_PROBIT_SCALE)
    v = np.maximum(np.asarray(var, dtype=float), 0.0) * _PROBIT_SCALE
    z = m / np.sqrt(1.0 + v)
    p = 0.5 * (1.0 + np.vectorize(math.erf)(z / math.sqrt(2.0)))
    expected = np.sqrt(_BALD_C2) / np.sqrt(v + _BALD_C2) * np.exp(-m * m / (2.0 * (v + _BALD_C2)))
    return np.maximum(_binary_entropy_bits(p) - expected, 0.0)


def pair_moments(model: FactorisedBT, a: list[Card], b: list[Card]) -> tuple[np.ndarray, np.ndarray]:
    """Posterior mean and variance of u(a_i) - u(b_i) for paired lists of cards."""
    if not a:
        return np.zeros(0), np.zeros(0)
    Ja, Jb = model.jacobian(a), model.jacobian(b)
    mu = model.utilities(a) - model.utilities(b)
    d = Ja - Jb
    var = np.einsum("ij,jk,ik->i", d, model.cov, d)
    return mu, var


def pair_gains(model: FactorisedBT, a: list[Card], b: list[Card], rule: str = "bald") -> np.ndarray:
    """Score each pair (a_i, b_i) by the rule; for max_var, pairs that are not comparable score -inf + var (kept as a
    fallback order when no pair is comparable)."""
    mu, var = pair_moments(model, a, b)
    if rule == "bald":
        return bald_gain(mu, var)
    if rule == "max_var":
        return np.where(np.abs(mu) <= 1.0, var, var - 1e6)
    raise ValueError(f"unknown rule {rule!r}")


def next_pair_index(model: FactorisedBT, a: list[Card], b: list[Card], rule: str = "bald") -> int:
    """Index of the pair to ask next (ties: the first)."""
    s = pair_gains(model, a, b, rule)
    return int(np.argmax(s)) if len(s) else -1


def answer_gain_table(model: FactorisedBT, answers: list[dict], rule: str = "bald") -> np.ndarray:
    """One label-free score per answer: the MEAN gain over its card pairs (mean, not sum, so a 5-card rank is not
    favoured only for its size)."""
    a_cards, b_cards, owner = [], [], []
    for i, ans in enumerate(answers):
        cs = answer_cards(ans)
        if ans.get("round") == "stay":
            cs = cs[:2]
        for x in range(len(cs)):
            for y in range(x + 1, len(cs)):
                a_cards.append(cs[x])
                b_cards.append(cs[y])
                owner.append(i)
    out = np.full(len(answers), -np.inf)
    if not owner:
        return out
    g = pair_gains(model, a_cards, b_cards, rule)
    own = np.array(owner)
    for i in range(len(answers)):
        m = own == i
        if m.any():
            out[i] = float(g[m].mean())
    return out


@dataclass
class ReplayCurve:
    """Held-out pairwise hits at each budget (number of answers used) for one arm on one split; cards_per_answer is the
    mean card count of the first 20 answers the arm took after the shared start (exposes a size confound)."""
    arm: str
    budgets: list[int]
    hits: dict[int, list[float]] = field(default_factory=dict)
    cards_per_answer: float = 0.0


def replay_curves(train: list[dict], test: list[dict], budgets: list[int], rng: np.random.Generator,
                  prior: FactorisedPrior | None = None, n_start: int = 5,
                  arms: tuple[str, ...] = ("random", "bald", "max_var")) -> list[ReplayCurve]:
    """Replay the train pool in an order chosen by each arm; after each answer refit; at each budget score the
    held-out pairs of `test`. All arms share the same `n_start` random first answers."""
    pool = [a for a in train if answer_events(a)]
    test_pairs = [p for a in test for p in answer_pairs(a)]
    budgets = sorted({b for b in budgets if n_start <= b <= len(pool)} | ({len(pool)} if pool else set()))
    start = list(rng.permutation(len(pool))[:n_start])
    rand_rest = [i for i in rng.permutation(len(pool)) if i not in start]
    out = []
    for arm in arms:
        chosen = list(start)
        curve = ReplayCurve(arm, budgets)
        model = FactorisedBT(prior)
        while True:
            evs = [e for i in chosen for e in answer_events(pool[i])]
            model = FactorisedBT(prior).fit(evs) if evs else model
            if len(chosen) in budgets:
                curve.hits[len(chosen)] = pairwise_hits(model.utility, test_pairs)
            if len(chosen) >= budgets[-1]:
                break
            left = [i for i in range(len(pool)) if i not in chosen]
            if arm == "random":
                nxt = rand_rest[len(chosen) - n_start]
            else:
                scores = answer_gain_table(model, [pool[i] for i in left], arm)
                nxt = left[int(np.argmax(scores))]
            chosen.append(nxt)
        curve.cards_per_answer = float(np.mean([len(pool[i].get("cards") or []) for i in chosen[n_start:n_start + 20]]))
        out.append(curve)
    return out
