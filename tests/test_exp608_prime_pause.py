"""[EXP-608] Prime gets behind live by pause on the hidden stick (Nick's Nov 7 stick run); off by default."""
from types import SimpleNamespace

import pytest

from redzone.config import Config
from redzone.lab import backtest as B


class _Clock:
    t = 0.0


def _stick(prime_entry="edge", entry="scrub_measured", behind=90.0):
    return B.HiddenStick("wait", behind, 1.0, Config(), _Clock(), 0, {}, entry=entry, prime_entry=prime_entry)


def _sw(to="g2", frm="g1", t=1000.0):
    return SimpleNamespace(kind="switch", to=to, frm=frm, t=t, leave_dt=0.0, arrive_dt=0.0, offset=0.0)


def test_default_is_edge_and_card_unchanged():
    s = B.HiddenStick("wait", 90.0, 1.0, Config(), _Clock(), 0, {}, entry="scrub_measured")
    assert s.prime_entry == "edge"
    assert s.hold_of("prime:x") == 0.0
    assert "prime_entry" not in s.card()


def test_pause_cost_is_hold_time_plus_resume():
    s = _stick("pause")
    assert s.hold_of("prime:x") == 90.0 + B.HS_PREREG["resume"]
    assert s.hold_of("youtubetv:x") == 22.5          # YouTube TV keeps the measured scrub
    assert s.card()["prime_entry"] == "pause"


@pytest.mark.parametrize("pe,offset,buffered", [("edge", -90.0, 0), ("pause", 0.0, 1)])
def test_ready_prime_airs_buffered_only_with_pause(pe, offset, buffered):
    s = _stick(pe)
    s.load("g2", "prime:g2", 0.0)
    sw = _sw()
    watching = [(sw.t, "g2")]
    s.on_switch(sw, watching, {"g2": "prime:g2"})
    assert sw.offset == offset and s.buffered == buffered and s.prime == 1


def test_unready_prime_waits_for_the_pause():
    s = _stick("pause")
    sw = _sw(t=10.0)
    watching = [(sw.t, "g2")]
    s.on_switch(sw, watching, {"g2": "prime:g2"})
    assert sw.offset == 0.0 and s.buffered == 1
    assert sw.leave_dt == pytest.approx(s.waits[0]) and s.waits[0] >= 90.0 + 5.0


def test_bad_prime_entry():
    with pytest.raises(ValueError):
        _stick("rewind")


def test_run_rejects_pause_with_edge():
    with pytest.raises(ValueError):
        B.run([], Config(), set(), hidden_stick="wait", hs_prime_pause=True, hs_prime_edge=True)
