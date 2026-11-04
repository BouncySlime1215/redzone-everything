"""The break forecast's rules + brain blend (breaks.blend / mix) and its fitter (lab/breakblend.py)."""
import math

import numpy as np
import pytest

from redzone.config import Config
from redzone.lab import breakblend as BB
from redzone.lab import splits
from redzone.models import GameState, League, Phase, Play, Situation, Team
from redzone.scoring import breaks


def _g(league=League.NBA):
    g = GameState(league=league, game_id="g", home=Team("WSH", score=50), away=Team("BOS", score=48),
                  phase=Phase.LIVE, period=2, clock_s=500.0, situation=Situation())
    g.last_play = Play("1", "Jump shot", "jump_shot", wallclock=1_700_000_000.0)
    g.recent_plays = [g.last_play]
    return g


def test_no_rule_evidence_means_the_brain_alone():
    # The bug: 0.5 * p + 0.5 * 0 halved every brain-only forecast (live ECE 0.12-0.17).
    for w in (0.5, 0.2, (1.0, 0.3, 0.9), {"*": 0.5}):
        assert breaks.mix(0.0, 0.37, w if not isinstance(w, dict) else 0.5) == pytest.approx(0.37)
        assert breaks.blend(breaks.NONE, 0.37, _g(), w).p == pytest.approx(0.37)


def test_log_odds_mix_with_evidence():
    lo = lambda p: math.log(p / (1 - p))  # noqa: E731
    sig = lambda z: 1 / (1 + math.exp(-z))  # noqa: E731
    assert breaks.mix(0.7, 0.3, 0.5) == pytest.approx(0.5)
    assert breaks.mix(0.8, 0.4, 0.25) == pytest.approx(sig(0.25 * lo(0.4) + 0.75 * lo(0.8)))
    assert breaks.mix(0.75, 0.2, (1.0, 0.33, 0.92)) == pytest.approx(sig(1.0 + 0.33 * lo(0.2) + 0.92 * lo(0.75)))
    assert breaks.mix(0.6, 0.2, (0.0, 1.0, 0.0)) == pytest.approx(0.2)      # brain alone everywhere
    assert 0 < breaks.mix(1.0, 1.0, (30, 30, 30)) <= 1                        # never overflows
    rules = breaks.Forecast(0.75, 150.0, "punt")
    got = breaks.blend(rules, 0.2, _g(League.NFL), (1.0, 0.33, 0.92))
    assert got.expect_s == 150.0 and got.why == "punt" and not got.now


def test_per_league_weights():
    w = {"*": 0.5, "nfl": {"a": 1.0, "b": 0.3, "c": 0.9}}
    assert breaks.weight_for(w, _g(League.NFL)) == (1.0, 0.3, 0.9)
    assert breaks.weight_for(w, _g(League.NHL)) == 0.5
    assert breaks.weight_for(0.3, _g()) == 0.3


def test_break_in_the_data_is_never_blended():
    now = breaks.Forecast(1.0, 188.0, "long timeout", True)
    assert breaks.blend(now, 0.01, _g(), {"nba": {"a": -5, "b": 1, "c": 0}}) is now


def test_vectorised_twin_matches_breaks_blend():
    rng = np.random.default_rng(0)
    rules = np.where(rng.random(400) < 0.5, 0.0, rng.choice([0.5, 0.6, 0.75, 0.8], 400))
    r = BB.Rows(np.array(["20261020"] * 400), np.zeros(400), rules, rng.random(400) < 0.05, rng.random(400))
    coef = (0.9, 0.6, 1.1)
    got = BB.apply(coef, r)
    for i in range(400):
        f = breaks.Forecast(float(r.rules[i]), 150.0, "x", bool(r.now[i]))
        assert got[i] == pytest.approx(breaks.blend(f, float(r.brain[i]), _g(), coef).p, abs=1e-9)


def test_fit_recovers_known_coefficients_and_falls_back():
    rng = np.random.default_rng(1)
    n = 20000
    rules = rng.choice([0.5, 0.6, 0.7, 0.8], n)
    brain = rng.uniform(0.05, 0.95, n)
    z = 0.4 + 0.6 * BB._logit(brain) + 0.8 * BB._logit(rules)
    y = (rng.random(n) < 1 / (1 + np.exp(-z))).astype(float)
    r = BB.Rows(np.array(["20261020"] * n), y, rules, np.zeros(n, bool), brain)
    a, b, c = BB.fit(r, l2=1e-4)
    assert (a, b, c) == pytest.approx((0.4, 0.6, 0.8), abs=0.15)
    # Separable data (a rule level that is always right) stays finite.
    sep = BB.Rows(r.night, (rules >= 0.8).astype(float), rules, r.now, brain)
    assert all(math.isfinite(v) and abs(v) < 20 for v in BB.fit(sep))
    # Too few rows with rule evidence: the brain alone.
    tiny = r.take(np.arange(n) < 50)
    assert BB.fit(tiny) == (0.0, 1.0, 0.0)


def test_grade_reads_only_held_out_folds_and_ships_only_a_winner():
    rng = np.random.default_rng(2)
    nights = [f"202610{d:02d}" for d in range(10, 22)]
    rows = []
    for nt in nights:
        rules = rng.choice([0.0, 0.0, 0.7, 0.8], 600)
        brain = rng.uniform(0.05, 0.95, 600)
        p = np.where(rules > 0, 1 / (1 + np.exp(-(1.0 + 0.5 * BB._logit(brain)))), brain)
        rows.append((np.full(600, nt), (rng.random(600) < p).astype(float), rules, np.zeros(600, bool), brain))
    r = BB.Rows(*(np.concatenate(x) for x in zip(*rows)))
    rep = BB.grade(r, nights)
    fit_check = splits.folds(nights, BB.FOLDS)
    assert [f["check"] for f in rep["folds"]] == [[c[0], c[-1]] for _, c in fit_check]
    for f, (fit_n, check_n) in zip(rep["folds"], fit_check):
        assert max(fit_n) < min(check_n)           # each fold is graded on newer, unseen nights
    assert rep["fitted_blend"]["ece_mean"] < rep["legacy_blend"]["ece_mean"]
    assert rep["ship"] == dict(zip("abc", rep["coef"]))
    # A blend that can't beat the brain alone isn't shipped.
    honest = BB.Rows(r.night, (rng.random(len(r.y)) < r.brain).astype(float), r.rules, r.now, r.brain)
    rep2 = BB.grade(honest, nights)
    if rep2["fitted_blend"]["ece_worst"] > rep2["brain"]["ece_worst"] or \
            rep2["fitted_blend"]["ece_mean"] > rep2["brain"]["ece_mean"]:
        assert rep2["ship"] == {"a": 0.0, "b": 1.0, "c": 0.0}


def test_eligible_nights_skip_locked_and_brain_training_nights(tmp_path):
    import json
    for d in ["20251101", "20251103", "20251106", "20251109", "20251113", "20251116", "20251120", "20251123"]:
        (tmp_path / f"scoreboard_{d}.json").write_text(json.dumps({"events": [{"id": d}]}))
    got = BB.eligible_nights("nfl", archive=tmp_path)
    s = splits.split_nights(splits.archive_nights(tmp_path))
    assert not set(got) & set(s.test)
    assert all(n > BB.BRAIN_SEEN_THROUGH["nfl"] for n in got)
    assert got == [n for n in s.train + s.valid if n > "20251104"]


def test_shipped_blend_is_valid_config(tmp_path):
    cfg = Config(user_path=tmp_path / "config.yaml", history_dir=tmp_path / "hist")
    bb = cfg.get("brain.break_blend")
    assert set(bb) == {"nfl", "nba", "nhl"}
    for v in bb.values():
        assert set(v) == {"a", "b", "c"}
    assert cfg.get("brain.enabled") is False        # the blend only matters with the brain on
    cfg.update({"brain": {"break_blend": {"nhl": {"c": -0.5}}}})   # a negative weight is a legal edit
    assert cfg.get("brain.break_blend.nhl.c") == -0.5
