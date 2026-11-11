"""AUDIT-7: a drift read that finishes after the stick flipped is not a failed check."""
from types import SimpleNamespace as NS

from tests.test_control import two  # noqa: F401  (fixture used by scrub)
from tests.test_scrub_entry import _preload, run, scrub  # noqa: F401  (fixture)

T = "youtubetv:fox"


def test_a_flip_during_the_drift_read_keeps_the_read(scrub, tmp_path):
    c, tuner, hid, calls, result, behind = scrub
    c.cfg.update({"join": {"drift": {"enabled": True, "first_s": 0}}})
    c.buffer_log_path = tmp_path / "buffer.jsonl"

    class Probe:
        def arm(self, stick, behind_before_s=0.0):
            return True

        async def overlay_reader(self, stick):
            return None

        async def __call__(self, stick):
            return 67.0

        async def probe(self, stick, still_hidden=None):
            c._flipping = True          # the stick goes to the screen while the label is read
            return NS(behind_live_s=76.0)
    c.label_probe = Probe()
    run(_preload(c, T))
    run(c._drift_tick())
    assert c.held[T] == 76.0
