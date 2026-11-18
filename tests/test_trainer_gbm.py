"""B-03 GBM baseline tests: causal features, a proper log loss over the field, and it learns real structure.

LightGBM is in requirements-ci.txt and the Mac venv; every fit here is single-threaded and takes well under a second.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.trainer import data, gbm, testkit  # noqa: E402


@pytest.fixture(scope="module")
def split(tmp_path_factory):
    c = testkit.toy_corpus(tmp_path_factory.mktemp("gbmcorpus"), events=(20, 30))
    return data.split_walk_forward(c, "20240220", "20240301")


def test_features_use_only_earlier_tokens(split):
    train, _ = split
    d = train[0]
    X, y = gbm.field_examples([d], field=3, k_prev=2)
    t2 = d.tokens.copy()
    t2[3 + 6 * 5 + 3:] = 9                                  # change event 5 from field 3 on, and everything after
    X2, y2 = gbm.field_examples([data.Doc(d.league, d.gid, d.night, t2)], field=3, k_prev=2)
    assert np.array_equal(X[:6], X2[:6])                    # features of events 0..5 are unchanged
    assert not np.array_equal(X[6], X2[6])                  # event 6 does see event 5's changed fields
    assert (X[0, 2:14] == -1).all() and np.array_equal(X[0, 14:17], d.tokens[3:6])


def test_gbm_learns_the_toy_structure_and_its_nll_is_a_proper_log_loss(split):
    train, held = split
    for field in (0, 4):
        Xtr, ytr = gbm.field_examples(train, field)
        Xte, yte = gbm.field_examples(held, field)
        m = gbm.GBMFieldBaseline(field, data.TOY_FIELD_IDS, num_boost_round=60, seed=1).fit(Xtr, ytr)
        nll = m.nll(Xte, yte)
        assert nll < 0.75 * math.log(data.TOY_FIELD_IDS)    # far below the uniform log loss over the field
        # a proper distribution: summed over every id of the field, P is 1 for each row
        ids = data.TOY_FIELD_BASE + field * data.TOY_FIELD_IDS + np.arange(data.TOY_FIELD_IDS)
        total = sum(m.token_prob(Xte[:20], np.full(20, i)) for i in ids)
        assert np.allclose(total, 1.0, atol=1e-6)


def test_unseen_tokens_get_a_finite_loss_and_fits_are_deterministic(split):
    train, held = split
    Xtr, ytr = gbm.field_examples(train, 0)
    a = gbm.GBMFieldBaseline(0, 64, max_classes=3, num_boost_round=20, seed=2).fit(Xtr, ytr)
    b = gbm.GBMFieldBaseline(0, 64, max_classes=3, num_boost_round=20, seed=2).fit(Xtr, ytr)
    Xte, yte = gbm.field_examples(held, 0)
    assert a.nll(Xte, yte) == b.nll(Xte, yte)
    p = a.token_prob(Xte[:5], np.full(5, 63))               # an id never seen in training
    assert np.all(p > 0) and np.all(np.isfinite(np.log(p)))


def test_relative_gain():
    assert gbm.relative_gain(0.97, 1.0) == pytest.approx(0.03)
    with pytest.raises(ValueError):
        gbm.relative_gain(1.0, 0.0)
