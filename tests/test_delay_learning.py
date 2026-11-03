"""Delay learning: taps persist across restarts, are shared across an app's channels, and stop
being asked for once an app's delay is settled. Never at the cost of a too-narrow interval."""
from redzone.decider.screen import (MIN_TAP_SIGMA_S, SHARED_MARGIN_S, STALE_MARGIN_S, DelayBook)
from redzone.store import Store

DAY = 86400.0


class Clock:
    def __init__(self, t=1_000_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def book(clock):
    return DelayBook({"youtubetv": 45.0, "prime": 40.0}, 15.0, clock=clock)


def test_default_until_any_tap():
    b = book(Clock())
    d = b.get("youtubetv:espn")
    assert (d.mu, d.sigma, d.calibrated_at, d.source) == (45.0, 15.0, None, "default")
    assert not b.measured("youtubetv:espn")


def test_one_tap_calibrates_every_channel_of_the_app_with_a_wider_sigma():
    c = Clock()
    b = book(c)
    tnt = b.get("youtubetv:tnt")                 # already in the book before the tap
    b.calibrate("youtubetv:espn", 30.0)
    espn, fox = b.get("youtubetv:espn"), b.get("youtubetv:fox")
    assert espn.mu == tnt.mu == fox.mu == 30.0
    assert espn.source == "tap" and tnt.source == fox.source == "app"
    assert tnt.sigma == fox.sigma == espn.sigma + SHARED_MARGIN_S
    assert tnt.calibrated_at is not None
    assert b.get("prime:tnf").mu == 40.0 and b.get("prime:tnf").calibrated_at is None   # other apps untouched


def test_own_taps_beat_the_app_pool():
    c = Clock()
    b = book(c)
    b.calibrate("youtubetv:espn", 30.0)
    b.calibrate("youtubetv:tnt", 50.0)
    assert b.get("youtubetv:tnt").mu == 50.0 and b.get("youtubetv:espn").mu == 30.0


def test_contradicting_taps_widen_sigma():
    b = book(Clock())
    for s in (30.0, 31.0, 50.0):
        b.calibrate("youtubetv:espn", s)
    d = b.get("youtubetv:espn")
    assert d.mu == 31.0 and d.sigma >= 10.0                # half the 20 s range: 50 still fits in 2 sigma
    assert d.mu + 2 * d.sigma >= 50.0


def test_restart_keeps_tonights_taps(tmp_path):
    c = Clock()
    store = Store(tmp_path / "rz.db")
    b = book(c)
    for s in (32.0, 33.0):
        d = b.calibrate("youtubetv:espn", s)
        store.calibration("youtubetv:espn", s, d.mu, d.sigma)
    rows = store.calibrations(since=c() - 30 * DAY)
    restarted = book(c)
    assert restarted.load(rows) == 2
    d = restarted.get("youtubetv:espn")
    assert d.mu == b.get("youtubetv:espn").mu and d.source == "tap"
    assert restarted.get("youtubetv:abc").mu == d.mu               # and shared after the restart


def test_old_taps_forgotten_recent_weighted_more_and_cross_night_margin():
    c = Clock()
    b = book(c)
    rows = [(c() - 40 * DAY, "youtubetv:espn", 90.0),                  # past 30 days: ignored
            (c() - 6 * DAY, "youtubetv:espn", 50.0),
            (c() - 2 * DAY, "youtubetv:espn", 30.0),
            (c() - 1 * DAY, "youtubetv:espn", 31.0)]
    # recency-weighted median of the three taps inside 30 days
    assert b.load(rows) == 3
    d = b.get("youtubetv:espn")
    assert d.mu == 31.0
    assert d.sigma == 10.0 + SHARED_MARGIN_S                          # another night: +2 on the spread


def test_stale_taps_get_the_wider_margin():
    c = Clock()
    b = book(c)
    b.load([(c() - 9 * DAY, "prime:tnf", 38.0)])
    d = b.get("prime:tnf")
    assert d.mu == 38.0 and d.sigma == 6.0 + STALE_MARGIN_S          # one tap: 6 s spread, stale: +4


def test_settled_after_three_consistent_taps_only():
    c = Clock()
    b = book(c)
    for s in (40.0, 42.0):
        b.calibrate("youtubetv:espn", s)
    assert not b.settled("youtubetv")
    b.calibrate("youtubetv:tnt", 44.0)
    assert b.settled("youtubetv") and b.measured("youtubetv:fox")
    assert not b.settled("prime")
    b.calibrate("youtubetv:fox", 70.0)                                 # a contradiction un-settles it
    assert not b.settled("youtubetv")
    c.t += 8 * DAY
    assert not b.settled("youtubetv")                                  # stale: ask again


def test_settled_delay_stays_usable_but_never_below_the_tap_floor():
    c = Clock()
    b = book(c)
    for s in (40.0, 41.0, 42.0):
        b.calibrate("youtubetv:espn", s)
    d = b.get("youtubetv:espn")
    assert d.sigma == MIN_TAP_SIGMA_S
    c.t += 3 * 3600
    assert d.current_sigma(c()) <= 8.0                                # still "calibrated" hours later
    b2 = book(c)
    b2.calibrate("youtubetv:espn", 40.0)                               # one tap: doubt grows as before
    c.t += 3 * 3600
    assert b2.get("youtubetv:espn").current_sigma(c()) == 6.0 + 6.0


class Hooks:
    def __init__(self, delays, on_screen):
        self.delays, self.on_screen, self.toasts = delays, on_screen, []

    def mic_on(self):
        return False

    def current_target(self):
        return self.on_screen

    def toast(self, msg, level="info"):
        self.toasts.append(msg)


def test_phone_stops_asking_once_the_app_is_settled():
    from redzone.autonomy import Calibrator
    c = Clock()
    b = book(c)
    hooks = Hooks(b, "youtubetv:espn")
    cal = Calibrator(hooks, {}, clock=c)
    assert cal.on_tune("youtubetv:espn") == "tap" and len(hooks.toasts) == 1   # first night: ask
    b.calibrate("youtubetv:espn", 40.0)
    cal.poll()
    assert "40s behind live" in hooks.toasts[-1]
    c.t += 3600
    hooks.on_screen = "youtubetv:tnt"
    assert cal.on_tune("youtubetv:tnt") == "tap"            # one tap isn't settled yet: ask on a new channel
    for t, s in (("youtubetv:tnt", 42.0), ("youtubetv:fox", 41.0)):
        b.calibrate(t, s)
    cal.poll()
    hooks.on_screen = "youtubetv:nbc"
    n = len(hooks.toasts)
    assert cal.on_tune("youtubetv:nbc") is None and len(hooks.toasts) == n   # settled: never nag again
    hooks.on_screen = "prime:tnf"
    assert cal.on_tune("prime:tnf") == "tap"                                 # other apps still learn


def test_engine_start_reloads_taps_from_the_store(tmp_path):
    from redzone.config import Config
    from redzone.engine import Engine
    store = Store(tmp_path / "rz.db")
    store.calibration("prime:tnf", 37.0, 37.0, 6.0)
    eng = Engine(Config(user_path=tmp_path / "config.yaml", history_dir=tmp_path / "hist"), store)
    d = eng.delays.get("prime:nba")
    assert d.source == "app" and d.mu == 37.0 and d.sigma == 6.0 + SHARED_MARGIN_S
