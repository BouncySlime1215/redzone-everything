"""[EXP-603] the hidden stick's scrub entry: ready keys + one label read after the tune, at 15 N + 15 s behind."""
import pytest

from redzone.config import Config
from redzone.lab import backtest as B


class _Clock:
    t = 0.0


def _stick(entry, behind, arm="wait"):
    return B.HiddenStick(arm, behind, 1.0, Config(), _Clock(), 0, {}, entry=entry)


def test_hold_unchanged():
    s = _stick("hold", 75.0)
    assert s.hold_of("youtubetv:x") == 75.0 + B.HS_PREREG["resume"]
    assert s.hold_of("prime:x") == B.HS_PREREG["resume"]


def test_scrub_cost_is_keys_plus_read():
    for b in (75.0, 90.0):
        s = _stick("scrub", b)
        assert s.hold_of("youtubetv:x") == 18.0
        assert s.hold_of("prime:x") == 0.0
        s.load("g", "youtubetv:g", 0.0)
        assert s.ready_at - s.tuned_at == 18.0
    assert _stick("scrub", 75.0).card()["entry"] == "scrub"


@pytest.mark.parametrize("b", [0.0, 15.0, 67.0, 80.0])
def test_scrub_rejects_unreachable_b(b):
    with pytest.raises(ValueError):
        _stick("scrub", b)


def test_bad_entry():
    with pytest.raises(ValueError):
        _stick("seek", 75.0)


def test_scrub_measured_cost():
    s = _stick("scrub_measured", 90.0)
    assert s.hold_of("youtubetv:x") == 22.5 and s.hold_of("prime:x") == 0.0 and s.true_sync is False
