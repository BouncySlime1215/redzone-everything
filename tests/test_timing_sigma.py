"""[timing, delay.timing.app_sigma, OFF] An app's stated doubt comes from a hold-out replay and a live meter, never a wish.

Why: the join-after-a-bucket rule fires only with the stick's position known to about 2 s; flag off must be today exactly.
"""
import json
import sys
from pathlib import Path

from redzone.decider.delaymodel import timing_replay_allows
from redzone.decider.screen import DelayBook, TimingSigma
from redzone.decider.sync import ChannelSync

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import timing_replay  # noqa: E402

PRIME = "prime:game1"
GOOD = {"method": "whole_night_holdout", "app": "prime",
        "nights": [{"night": "2026-11-04", "n": 22, "rmse": 0.53}, {"night": "2026-11-07", "n": 3, "rmse": 0.31}]}


def book(now):
    b = DelayBook({"prime": 40.0}, 15.0, clock=lambda: now[0])
    b.attach_sync(min_sigma_s=4.0)
    return b


def feed(b, now, anchors=3):
    """Anchors (bug clock, sd 1.5) then a position read: what the meter + K path sees."""
    for i in range(anchors):
        now[0] += 2.0
        b.sync.measure(PRIME, 16.0 + 0.1 * i, "clock", t=now[0], sd=1.5)
    now[0] += 1.0
    b.sync.position(PRIME, now[0] - 16.0, t=now[0], sd=0.3)
    return b.get(PRIME)


def test_flag_off_is_exactly_todays_floors():
    now = [1000.0]
    with_flag, without = book(now), book(now)
    a, b = feed(with_flag, now), feed(without, now)
    assert with_flag.timing == {} and without.timing == {}
    assert (a.mu, a.sigma, a.source) == (b.mu, b.sigma, b.source)
    assert a.sigma >= 4.0                     # the 4 s floor, the gates' tested floor, still holds


def test_a_wish_is_refused_and_changes_nothing():
    now = [1000.0]
    b = book(now)
    assert b.set_timing_sigma("prime", 1.0, None) is False
    assert b.set_timing_sigma("prime", 0.3, GOOD) is False            # worse than the worst night
    assert b.set_timing_sigma("prime", 1.0, {**GOOD, "app": "youtubetv"}) is False
    assert b.timing == {}
    assert feed(b, now).sigma >= 4.0


def test_too_little_evidence_is_refused():
    assert timing_replay_allows(GOOD, 1.0, "prime") is True
    assert timing_replay_allows({**GOOD, "nights": GOOD["nights"][:1]}, 1.0, "prime") is False   # one night is no hold-out
    thin = {**GOOD, "nights": [{"night": "a", "n": 4, "rmse": 0.2}, {"night": "b", "n": 3, "rmse": 0.2}]}
    assert timing_replay_allows(thin, 1.0, "prime") is False


def test_earned_doubt_needs_anchors_and_a_live_meter():
    now = [1000.0]
    b = book(now)
    assert b.set_timing_sigma("prime", 1.0, GOOD, min_anchors=2, meter_s=15.0) is True
    b.timing["prime"] = TimingSigma(1.0, 2, 15.0)

    d = feed(b, now, anchors=1)               # one anchor only: not earned yet
    assert d.sigma >= 4.0

    d = feed(b, now, anchors=1)               # second anchor + meter: the earned doubt, not the wish
    assert 1.0 <= d.sigma < 4.0               # the filter's own doubt floored at the earned 1.0, not the 4 s floor
    assert b.get(PRIME).sigma == d.sigma

    now[0] += 60.0                            # meter dead: the earned doubt goes away on its own
    assert b.get(PRIME).sigma >= 4.0


def test_youtube_tv_never_borrows_primes_doubt():
    now = [1000.0]
    b = book(now)
    b.set_timing_sigma("prime", 1.0, GOOD)
    target = "youtubetv:espn"
    for i in range(3):
        now[0] += 2.0
        b.sync.measure(target, 35.0, "clock", t=now[0], sd=1.5)
    now[0] += 1.0
    b.sync.position(target, now[0] - 35.0, t=now[0], sd=0.3)
    assert b.get(target).sigma >= 4.0


def test_a_new_tune_starts_the_anchors_over():
    f = ChannelSync("prime:x", 0.0)
    f.reset(0.0, 40.0, 15.0, new_stream=True)
    f.delay(1.0, 16.0, "clock", 1.5)
    f.position(2.0, 2.0 - 16.0, 0.3)
    assert f.anchors == 1 and f.meter_anchored(2.0, 1, 15.0)
    f.reset(3.0, 40.0, 15.0, new_stream=False)            # a seek keeps this stream's origin and its anchors
    assert f.anchors == 1
    f.reset(4.0, 40.0, 15.0, new_stream=True)
    assert f.anchors == 0 and not f.meter_anchored(4.0, 1, 15.0)


def test_replay_truth_uses_only_a_running_clock():
    reads = [{"game": "g", "t0": t, "clock": 95.0} for t in (0.0, 6.0, 12.0)]      # a frozen clock read three times
    assert not any(timing_replay.running(reads, i) for i in range(3))
    reads = [{"game": "g", "t0": t, "clock": 100.0 - t} for t in (0.0, 6.0, 12.0)]
    assert all(timing_replay.running(reads, i) for i in range(3))


def test_bracket_needs_a_clock_that_ran():
    plays = {1: [(100.0, 700.0), (110.0, 690.0), (140.0, 690.0)]}
    assert timing_replay.bracket(695.0, 1, plays, 130.0) == 130.0 - (100.0 + 5.0)
    assert timing_replay.bracket(690.0, 1, {1: [(110.0, 690.0), (140.0, 690.0)]}, 150.0) is None


def test_capture_slope_flags_a_midpoint_frame():
    anc = [{"night": "n", "z": 20.0 - 0.5 * c, "cap": c} for c in (3.0, 4.0, 5.0, 6.0, 3.5, 4.5, 5.5, 6.5, 7.0)]
    assert timing_replay.capture_slope(anc)["n"]["slope"] == -0.5


def test_recommended_config_passes_its_own_gate():
    night = {"n": 22, "rmse": 0.53, "mean": 0.0}
    r = {"nights": ["a", "b"], "estimators": {"meter": {"after_1_anchor": {"a": night, "b": {**night, "n": 3, "rmse": 0.2}}}}}
    rec = timing_replay.recommended(r)["prime"]
    assert timing_replay_allows(rec["replay"], rec["sigma_s"], "prime")
    assert json.loads(json.dumps(rec)) == rec
