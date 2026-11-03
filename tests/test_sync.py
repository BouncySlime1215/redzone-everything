"""[auto sync] Per-channel Kalman filter for the screen delay (redzone/decider/sync.py)."""
import random

from redzone.decider.screen import DelayBook
from redzone.decider.sync import SYNC_TARGET_S, SyncBook, clock_sample

T = 1_793_700_000.0


class Clock:
    def __init__(self, t=T):
        self.t = t

    def __call__(self):
        return self.t


def sync(prior=(45.0, 15.0), clock=None):
    return SyncBook(lambda target: prior, clock=clock or Clock())


def test_starts_at_the_prior_and_is_not_synced():
    s = sync()
    f = s.get("youtubetv:espn")
    assert f.mu(T) == 45.0 and abs(f.sd(T) - 15.0) < 1e-6 and not f.synced(T)
    assert s.estimate("youtubetv:espn") is None


def test_one_flip_plus_position_reads_sync_within_two_minutes_and_after_every_tune():
    c = Clock()
    s = sync(clock=c)
    true_delay, origin = 26.4, T - 50_000.0
    pos = lambda t, d: t - origin - d                    # stream position of the frame on screen at t
    s.tuned("youtubetv:espn", c.t)
    s.measure("youtubetv:espn", true_delay + 0.5, "flip", t=c.t)         # networks flip a beat late
    s.measure("youtubetv:espn", true_delay - 0.5, "score", t=c.t + 1)
    s.position("youtubetv:espn", pos(c.t, true_delay), t=c.t)
    for _ in range(24):                                                    # reads every 5 s for 2 min
        c.t += 5
        s.position("youtubetv:espn", pos(c.t, true_delay), t=c.t)
    f = s.get("youtubetv:espn")
    assert f.has_origin and abs(f.mu(c.t) - true_delay) < 2.0
    # a new tune lands 7 s further behind the live edge: the first position read re-syncs it, no flip
    c.t += 60
    s.tuned("youtubetv:espn", c.t)
    assert s.estimate("youtubetv:espn") is None
    c.t += 3
    s.position("youtubetv:espn", pos(c.t, true_delay + 7.0), t=c.t)
    assert abs(f.mu(c.t) - (true_delay + 7.0)) < 2.0


def test_scores_alone_converge_under_two_seconds():
    c = Clock()
    s = sync(clock=c)
    rng = random.Random(1)
    s.tuned("prime:x", c.t)
    for _ in range(6):
        c.t += 40
        s.measure("prime:x", 33.0 + rng.gauss(0, 2.0), "score", t=c.t)
    est = s.estimate("prime:x")
    assert est is not None and abs(est[0] - 33.0) < 2.0 and est[1] <= SYNC_TARGET_S


def test_a_disagreeing_signal_is_not_used_and_flags_the_channel():
    c = Clock()
    s = sync(clock=c)
    for _ in range(6):
        c.t += 20
        s.measure("prime:x", 33.0, "score", t=c.t)
    c.t += 20
    assert not s.measure("prime:x", 60.0, "score", t=c.t)
    f = s.get("prime:x")
    assert f.uncertain(c.t) and abs(f.mu(c.t) - 33.0) < 1.0 and s.estimate("prime:x") is None


def test_two_disagreements_in_a_row_restart_from_the_newest():
    c = Clock()
    s = sync(clock=c)
    for _ in range(6):
        c.t += 20
        s.measure("prime:x", 33.0, "score", t=c.t)
    for _ in range(2):                                  # the stream really jumped 25 s behind
        c.t += 20
        s.measure("prime:x", 58.0, "score", t=c.t)
    for _ in range(4):
        c.t += 20
        s.measure("prime:x", 58.0, "score", t=c.t)
    f = s.get("prime:x")
    assert abs(f.mu(c.t) - 58.0) < 1.5


def test_drift_is_tracked():
    c = Clock()
    s = sync(clock=c)
    rng = random.Random(4)
    d0, rate = 30.0, 1.0 / 600                          # falls 1 s behind every 10 minutes
    for k in range(60):
        c.t += 30
        s.measure("prime:x", d0 + rate * 30 * (k + 1) + rng.gauss(0, 1.5), "clock", t=c.t)
    f = s.get("prime:x")
    assert abs(f.x[1] - rate) < rate * 0.6
    assert abs(f.mu(c.t + 300) - (d0 + rate * (30 * 60 + 300))) < 2.0


def test_clock_sample_interpolates_only_while_the_clock_ran():
    plays = [(T, 600.0), (T + 20, 580.0), (T + 60, 579.0), (T + 80, 559.0)]
    assert abs(clock_sample(590.0, T + 10 + 31.0, plays) - 31.0) < 1e-6      # ran 20 s in 20 s: ok
    assert clock_sample(579.5, T + 100, plays) is None                       # stopped: 40 s for 1 s
    assert abs(clock_sample(570.0, T + 69 + 30.0, plays) - 30.0) < 1e-6
    assert clock_sample(700.0, T + 100, plays) is None


def test_delay_book_uses_a_synced_channel_but_never_below_the_floor():
    c = Clock()
    b = DelayBook({"youtubetv": 45.0}, 15.0, clock=c)
    s = b.attach_sync(min_sigma_s=4.0)
    for _ in range(8):
        c.t += 20
        s.measure("prime:x", 33.0, "score", t=c.t)
    d = b.get("prime:x")
    assert d.source == "sync" and abs(d.mu - 33.0) < 1.0 and d.sigma == 4.0 and d.calibrated_at is not None
    # a tune: until it re-syncs, the predictor's figure is back
    s.tuned("prime:x", c.t)
    d = b.get("prime:x")
    assert d.source != "sync"


def test_delay_book_keeps_sync_after_a_refresh_and_reports_status():
    c = Clock()
    b = DelayBook({"youtubetv": 45.0}, 15.0, clock=c)
    s = b.attach_sync(min_sigma_s=4.0)
    for _ in range(8):
        c.t += 20
        s.measure("youtubetv:espn", 30.0, "score", t=c.t)
    b.calibrate("youtubetv:fs1", 42.0)                  # refreshes every youtubetv channel
    assert b.get("youtubetv:espn").source == "sync"
    st = {x["target"]: x for x in s.status()}
    assert st["youtubetv:espn"]["synced"] and st["youtubetv:espn"]["error_s"] < 2.0


def test_position_jump_without_a_tune_restarts_cleanly():
    """An unseen seek: two position reads disagree with the filter; it re-syncs on positions."""
    c = Clock()
    s = sync(clock=c)
    origin = T - 40_000.0
    s.measure("youtubetv:espn", 26.0, "flip", t=c.t)
    for _ in range(10):
        c.t += 5
        s.position("youtubetv:espn", c.t - origin - 26.0, t=c.t)
    for _ in range(12):                                 # the stick seeked 30 s back, nobody told us
        c.t += 5
        s.position("youtubetv:espn", c.t - origin - 56.0, t=c.t)
    f = s.get("youtubetv:espn")
    assert f.rejected >= 1
    # with the origin dropped, positions alone can't pin d; it must not claim to be synced on a wrong value
    assert not f.synced(c.t) or abs(f.mu(c.t) - 56.0) < 2.0
    s.measure("youtubetv:espn", 56.0, "score", t=c.t)
    for _ in range(6):
        c.t += 5
        s.position("youtubetv:espn", c.t - origin - 56.0, t=c.t)
    assert abs(f.mu(c.t) - 56.0) < 2.5


def test_a_synced_channel_needs_no_tap():
    c = Clock()
    b = DelayBook({"youtubetv": 45.0}, 15.0, clock=c)
    s = b.attach_sync()
    assert not b.measured("prime:x")
    for _ in range(8):
        c.t += 20
        s.measure("prime:x", 33.0, "score", t=c.t)
    assert b.measured("prime:x")


def test_rewind_on_entry_resyncs_and_keeps_the_offset_on_the_predictor_figure():
    c = Clock()
    b = DelayBook({"youtubetv": 45.0}, 15.0, clock=c)
    s = b.attach_sync()
    for _ in range(8):
        c.t += 20
        s.measure("prime:x", 33.0, "score", t=c.t)
    assert b.get("prime:x").source == "sync"
    b.set_behind("prime:x", 20.0)                       # a DVR rewind on entry
    d = b.get("prime:x")
    assert d.source != "sync" and d.mu == 40.0 + 20.0 and d.behind == 20.0


def test_sync_log_rows(tmp_path):
    import json
    c = Clock()
    s = SyncBook(lambda t: (45.0, 15.0), clock=c, log_path=tmp_path / "sync.jsonl")
    s.tuned("prime:x", c.t)
    s.measure("prime:x", 33.0, "score", t=c.t)
    rows = [json.loads(x) for x in (tmp_path / "sync.jsonl").read_text().splitlines()]
    assert [r["kind"] for r in rows] == ["tune", "score"] and rows[1]["used"] and "sd" in rows[1]
    from redzone.lab.syncsim import log_summary
    assert log_summary(tmp_path / "sync.jsonl")["prime:x"]["tunes"] == 1
