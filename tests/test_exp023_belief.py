"""[EXP-023] Belief tracker: flag off by default, SyncBook-compatible, learns across tunes, robust breaks."""
import random

import pytest

from redzone.config import Config
from redzone.decider.screen import DelayBook
from redzone.noggin import belief
from redzone.noggin.belief import BeliefBook, BeliefDelayBook, BeliefParams, FeedLagBelief, Rhythm


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def book(prior=(43.0, 3.0), behind=0.0, **kw):
    clk = Clock()
    b = BeliefBook(lambda tg: prior, behind=lambda tg: behind, clock=clk, params=BeliefParams(**kw))
    return b, clk


def test_flag_is_off_by_default_and_not_in_the_default_config():
    assert belief.enabled(Config()) is False
    assert belief.enabled(None) is False
    cfg = Config()
    cfg.data.setdefault("noggin", {}).setdefault("belief", {})["enabled"] = True
    assert belief.enabled(cfg) is True


def test_no_anchor_no_estimate():
    b, clk = book()
    b.tuned("youtubetv:a")
    assert b.estimate("youtubetv:a", better_than=3.0) is None


def test_break_anchors_pull_a_wrong_kickoff_toward_the_truth_and_survive_tunes():
    b, clk = book()
    rng = random.Random(1)
    tg = "youtubetv:a"
    truth = 49.0
    b.get(tg)
    for k in range(30):
        clk.t += 300
        if k % 5 == 0:
            b.tuned(tg)                     # tunes in between: the base must be kept
        b.measure(tg, truth + rng.gauss(0, 10), "break", sd=10.0)
    mu, sd = b.estimate(tg, better_than=3.0)
    assert abs(mu - truth) < abs(43.0 - truth)
    assert mu > 45.0
    assert sd < 6.0


def test_a_tune_widens_by_the_landing_jitter_but_keeps_the_mean():
    b, clk = book()
    tg = "youtubetv:a"
    for _ in range(20):
        clk.t += 60
        b.measure(tg, 43.0, "clock", sd=1.5)
    f = b.filters[tg]
    mu0, sd0 = f.mu(clk.t), f.sd(clk.t)
    b.tuned(tg)
    assert abs(f.mu(clk.t) - mu0) < 1.0
    assert f.sd(clk.t) > sd0


def test_behind_is_known_not_estimated():
    behind = {"v": 0.0}
    clk = Clock()
    b = BeliefBook(lambda tg: (43.0 + behind["v"], 3.0), behind=lambda tg: behind["v"], clock=clk)
    tg = "youtubetv:a"
    b.get(tg)
    behind["v"] = 30.0
    b.tuned(tg)
    assert b.filters[tg].mu(clk.t) == pytest.approx(73.0, abs=2.0)


def test_one_wild_break_moves_the_estimate_less_than_a_gaussian_would():
    b, clk = book(prior=(45.0, 2.0))
    tg = "youtubetv:a"
    for _ in range(10):
        clk.t += 300
        b.measure(tg, 45.0, "break", sd=10.0)
    before = b.filters[tg].mu(clk.t)
    clk.t += 300
    b.measure(tg, 120.0, "break", sd=10.0)
    assert b.filters[tg].mu(clk.t) - before < 3.0


def test_out_of_range_anchor_is_refused():
    b, _ = book()
    assert b.measure("youtubetv:a", 500.0, "break", sd=10.0) is False


def test_rhythm_learns_a_steady_broadcast_narrower_than_a_messy_one():
    steady, messy = Rhythm(10.0), Rhythm(10.0)
    rng = random.Random(3)
    for k in range(30):
        steady.update(rng.gauss(0, 2), t=600.0 * k)
        messy.update(rng.gauss(0, 20), t=600.0 * k)
    assert steady.scale < 6.0 < messy.scale
    assert 400 < steady.mean_interval() < 800


def test_belief_delay_book_installs_the_tracker_and_delaybook_reads_it():
    clk = Clock()
    db = BeliefDelayBook({"youtubetv": 43.0}, sigma_default=3.0, clock=clk)
    sync = db.attach_sync(min_sigma_s=4.0)
    assert isinstance(sync, BeliefBook)
    tg = "youtubetv:a"
    db.get(tg)
    for _ in range(25):
        clk.t += 300
        db.anchor(tg, 52.0, "break", clk.t, 10.0)
    d = db.get(tg)
    assert d.source == "sync" and d.mu > 45.0 and d.sigma >= 4.0
    # the plain DelayBook still uses the Kalman sync (live is untouched)
    assert not isinstance(DelayBook({"youtubetv": 43.0}, 3.0, clock=clk).attach_sync(), BeliefBook)


def test_feed_lag_belief_tracks_a_shifted_lag():
    fb = FeedLagBelief(mu0=8.0, sd0=6.0, seed=1)
    rng = random.Random(2)
    for k in range(200):
        fb.observe(30.0 + rng.gauss(0, 4), 10.0 * k)
    assert fb.estimate()[0] == pytest.approx(30.0, abs=2.0)


def test_harness_refuses_locked_nights(tmp_path):
    import importlib.util
    from pathlib import Path
    from redzone.models import League
    spec = importlib.util.spec_from_file_location(
        "exp023", Path(__file__).resolve().parent.parent / "scripts" / "exp023_belief.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with pytest.raises(SystemExit):
        next(mod.load_nights(tmp_path, League.NBA, "test"))
