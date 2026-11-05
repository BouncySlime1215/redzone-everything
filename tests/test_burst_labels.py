"""[burst labels] EXP-021: burst onsets graded as dead-ball labels. Serves: EXP-021. Owner: lane 3."""
import random

from redzone.lab import burst_labels as bl


def _night(rng, gap=40.0, jitter=1.0, n=50, delay=45.0, honest=True):
    # breaks every ~3 min (the onsets follow timeouts and breaks, not every whistle)
    dead = [i * 180.0 + rng.uniform(-20, 20) for i in range(60)]
    picks = rng.sample(dead, n)
    ons = []
    for i, w in enumerate(sorted(picks)):
        real = (w + gap + rng.gauss(0, jitter)) if honest else rng.uniform(0, 3 * 3600)
        ons.append((i % 2, real + delay))
    return ons, dead, delay


def test_bursts_that_follow_dead_balls_grade_as_labels_and_beat_the_null():
    rng = random.Random(4)
    nights = [bl.grade_night(*_night(rng)[:2], 45.0, seed=i) for i in range(3)]
    out = bl.grade(nights)
    assert out["grade"] == "PASS" and all(v["labels"] >= bl.REGISTERED["min_labels"] for v in out["sticks"].values())
    assert all(abs(g - 40.0) < 2 for g in out["gap_s"]) and out["gap_ok"]
    assert all(r < 0.5 for r in out["null_hit_rate"])


def test_bursts_at_random_moments_fail_and_one_night_is_pending():
    rng = random.Random(5)
    bad = [bl.grade_night(*_night(rng, honest=False)[:2], 45.0) for _ in range(3)]
    assert bl.grade(bad)["grade"] == "FAIL"
    one = bl.grade([bl.grade_night(*_night(rng)[:2], 45.0)])
    assert one["grade"] == "PENDING"            # one night: no night-bootstrap bound, no gap spread


def test_the_gap_is_fitted_on_the_first_half_and_only_graded_on_the_second():
    ons = [(0, 100.0 + 45.0 + 30.0), (0, 400.0 + 45.0 + 30.0), (0, 700.0 + 45.0 + 99.0), (0, 1000.0 + 45.0 + 99.0)]
    g = bl.grade_night(ons, [100.0, 400.0, 700.0, 1000.0], 45.0)
    assert g.gap_s == 30.0 and g.hits == {0: [False, False]}   # the second half moved: the fit doesn't follow it
