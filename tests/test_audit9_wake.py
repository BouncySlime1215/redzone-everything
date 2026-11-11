"""AUDIT-9: a slow housekeeping tick (a stick timing out) is not a sleep or a stall."""
from redzone.platform.wake import WakeWatch


class Clk:
    def __init__(self):
        self.wall = self.mono = 1000.0


def test_a_long_keepalive_is_not_a_wake():
    c = Clk()
    w = WakeWatch(wall=lambda: c.wall, mono=lambda: c.mono, expect_s=10.0)
    assert w.check() is None
    c.wall += 50.0
    c.mono += 50.0                   # 10 s sleep + 40 s of keepalive work, awake the whole time
    assert w.check(busy_s=40.0) is None


def test_a_real_stall_and_a_real_sleep_still_count():
    c = Clk()
    w = WakeWatch(wall=lambda: c.wall, mono=lambda: c.mono, expect_s=10.0)
    w.check()
    c.wall += 100.0
    c.mono += 100.0                  # 10 s + 5 s of work, but the loop was frozen for 85 s
    assert w.check(busy_s=5.0) == 85.0
    c.wall += 300.0                  # the Mac slept: wall ran on, monotonic stood still
    c.mono += 10.0
    assert w.check(busy_s=40.0) == 290.0
