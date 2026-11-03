"""[per-channel delay] Each channel's stream delay predicted on its own (redzone/decider/delaymodel.py).

The real samples from the sticks on Nov 2 2026 (YouTube TV): truTV 22 s and ESPN 26.4 s from show
changes, FS1 42 s from a tap. Channels of one app differ by ~20 s, so one channel's tap must not
hand every other channel a narrow interval."""
import math
import random

from redzone.decider.delaymodel import TAU_PRIOR_S, app_prior, combine, tau_estimate
from redzone.decider.screen import (BOUNDARY_SIGMA_S, MIN_TAP_SIGMA_S, SHARED_MARGIN_S, DelayBook)
from redzone.lab.backtest import Lags, channel_scenario, lag_of

T0 = 1_793_700_000.0
REAL = [(T0 + 0, "youtubetv:trutv", 22.0, "boundary"),
        (T0 + 4654, "youtubetv:espn", 26.4, "boundary"),
        (T0 + 8766, "youtubetv:fs1", 42.0, "tap")]


class Clock:
    def __init__(self, t=T0 + 9000):
        self.t = t

    def __call__(self):
        return self.t


def book(model="channel", clock=None):
    return DelayBook({"youtubetv": 45.0, "prime": 40.0}, 15.0, clock=clock or Clock(), model=model)


def covers(d, x):
    return d.mu - 2 * d.sigma <= x <= d.mu + 2 * d.sigma


# ------------------------------------------------------------------ the model's pieces

def test_tau_starts_at_the_prior_and_learns_the_spread():
    assert tau_estimate([]) == tau_estimate([30.0]) == TAU_PRIOR_S
    agree = tau_estimate([40.0, 41.0, 42.0, 41.0, 40.0, 42.0])
    differ = tau_estimate([22.0, 26.4, 42.0])
    assert agree < TAU_PRIOR_S < differ                     # many agreeing channels narrow it; the real ones widen it


def test_combine_is_precision_weighted():
    mu, sd = combine(40.0, 8.0, 22.0, 6.0)
    assert 22.0 < mu < 31.0 and sd < 6.0
    assert abs(combine(40.0, 1e6, 22.0, 6.0)[0] - 22.0) < 1e-6


def test_each_channel_counts_once_in_the_app_prior():
    b = book()
    for _ in range(12):
        b.calibrate("youtubetv:espn", 26.0)                   # one channel tapped a dozen times
    b.calibrate("youtubetv:fs1", 42.0)
    p = app_prior(b.centres("youtubetv"), "youtubetv:cbs")
    assert p.n == 2 and p.m == 34.0                          # not dragged to 26 by the dozen taps


# ------------------------------------------------------------------ the real Nov 2 samples

def test_real_samples_every_channel_inside_its_interval_when_held_out():
    """Hide each measured channel, predict it from the others: its real delay must be inside."""
    for target in {r[1] for r in REAL}:
        truth = next(r[2] for r in REAL if r[1] == target)
        b = book()
        b.load([r for r in REAL if r[1] != target])
        assert covers(b.get(target), truth), target


def test_real_samples_legacy_book_put_trutv_outside():
    b = book("legacy")
    b.load([r for r in REAL if r[1] != "youtubetv:trutv"])
    assert not covers(b.get("youtubetv:trutv"), 22.0)


def test_real_samples_trutv_uses_its_own_show_change():
    b = book()
    b.load(REAL)
    d = b.get("youtubetv:trutv")
    assert d.source == "boundary" and abs(d.mu - 22.0) < abs(d.mu - 42.0) and covers(d, 22.0)
    assert d.sigma >= BOUNDARY_SIGMA_S


def test_unmeasured_channel_of_a_disagreeing_app_waits_for_breaks():
    b = book()
    b.load(REAL)
    cbs = b.get("youtubetv:cbs")
    assert cbs.source == "app" and cbs.sigma > 8.0           # above decider.uncalibrated_sigma_s: breaks only
    assert covers(cbs, 22.0) and covers(cbs, 42.0)
    assert not b.measured("youtubetv:cbs")                   # so the phone still asks for one tap there


def test_agreeing_channels_do_calibrate_a_new_one():
    b = book()
    for t, s in (("espn", 40.0), ("tnt", 41.0), ("fox", 42.0), ("abc", 41.0), ("nbc", 40.0)):
        b.calibrate(f"youtubetv:{t}", s)
    d = b.get("youtubetv:cbs")
    assert 40.0 <= d.mu <= 42.0 and d.sigma <= 8.0 and b.measured("youtubetv:cbs")


def test_own_taps_are_never_shrunk_toward_other_channels():
    b, old = book(), book("legacy")
    for x in (b, old):
        for s in (22.0, 23.0):
            x.calibrate("youtubetv:trutv", s)
        x.calibrate("youtubetv:fs1", 42.0)
    for t in ("youtubetv:trutv", "youtubetv:fs1"):
        assert (b.get(t).mu, b.get(t).sigma) == (old.get(t).mu, old.get(t).sigma)


def test_never_narrower_than_the_floors_or_margins():
    b = book()
    b.calibrate("youtubetv:espn", 30.0)
    d = b.get("youtubetv:fox")
    assert d.sigma >= MIN_TAP_SIGMA_S + SHARED_MARGIN_S
    b.boundary("youtubetv:fox", 31.0)
    d = b.get("youtubetv:fox")
    assert d.source == "boundary" and d.sigma >= BOUNDARY_SIGMA_S + SHARED_MARGIN_S   # one show change


def test_live_origin_recentres_a_predicted_channel_but_never_narrows():
    b = book()
    b.load(REAL)
    d = b.get("youtubetv:cbs")
    sigma = d.sigma
    b.set_live("youtubetv:cbs", 30.0)
    assert b.get("youtubetv:cbs").mu == 30.0 and b.get("youtubetv:cbs").sigma == sigma
    b.set_live("youtubetv:cbs", 170.0)                       # inconsistent: ignored
    assert b.get("youtubetv:cbs").live is None


def test_rewind_offset_still_applies_to_a_predicted_channel():
    b = book()
    b.load(REAL)
    base = b.get("youtubetv:cbs").mu
    b.set_behind("youtubetv:cbs", 20.0)
    assert b.get("youtubetv:cbs").mu == base + 20.0
    b.calibrate("youtubetv:espn", 27.0)                      # a refresh keeps the rewind
    fresh = book()
    fresh.load(REAL)
    fresh.calibrate("youtubetv:espn", 27.0)
    d = b.get("youtubetv:cbs")
    assert d.behind == 20.0 and math.isclose(d.mu, fresh.get("youtubetv:cbs").mu + 20.0)


def test_other_apps_untouched():
    b = book()
    b.load(REAL)
    d = b.get("prime:tnf")
    assert (d.mu, d.sigma, d.source) == (40.0, 15.0, "default")


def test_unknown_model_falls_back_to_channel():
    assert DelayBook({}, 15.0, model="nonsense").model == "channel"


def test_simulated_apps_cover_the_hidden_channel_far_more_often():
    """Many random apps, channels spread like the real ones: hold one channel out, predict it."""
    rng = random.Random(7)
    miss = {"channel": 0, "legacy": 0}
    n = 300
    for _ in range(n):
        base = rng.uniform(25, 50)
        chans = {f"youtubetv:c{i}": base + rng.gauss(0, 8) for i in range(rng.randint(2, 6))}
        rows = []
        for t, true in chans.items():
            for _ in range(rng.randint(1, 3)):
                rows.append((T0 + rng.uniform(0, 3000), t, true + rng.gauss(0, 2), rng.choice(["tap", "boundary"])))
        hidden = rng.choice(sorted(chans))
        for model in miss:
            b = book(model)
            b.load([r for r in rows if r[1] != hidden])
            miss[model] += not covers(b.get(hidden), chans[hidden])
    assert miss["channel"] <= 0.06 * n                       # ~2 sigma: about 1 in 20 at most
    assert miss["channel"] * 3 < miss["legacy"]


# ------------------------------------------------------------------ the scorecard scenario

class R:
    def __init__(self, gid):
        self.gid = gid


def test_lags_is_the_plain_figure_unless_a_game_has_its_own():
    lag = Lags(43.0, {"a": 30.0})
    assert lag == 43.0 and lag_of(lag, "a") == 30.0 and lag_of(lag, "b") == 43.0 and lag_of(43.0, "a") == 43.0


def test_channel_scenario_is_seeded_and_spread():
    reps = [R(str(i)) for i in range(40)]
    a, rows_a = channel_scenario(reps, None, 8.0, 35.0, 8.0, 0.6, 3, T0)
    b, rows_b = channel_scenario(reps, None, 8.0, 35.0, 8.0, 0.6, 3, T0)
    assert a.per == b.per and rows_a == rows_b
    vals = list(a.per.values())
    assert min(vals) >= 23.0 and max(vals) <= 83.0 and max(vals) - min(vals) > 15.0
    assert {r[3] for r in rows_a} == {"tap", "boundary"} and all(r[0] < T0 for r in rows_a)
    measured = {r[1] for r in rows_a}
    assert 0.5 * 40 < len(measured) < 40                     # some channels never measured
