"""EXP-039 grader and the shared whole-night A/B."""
from redzone.lab import also_close, night_ab


def test_manual_share():
    rows = [{"kind": "crunch", "ok": 1}, {"kind": "user", "ok": 1}, {"kind": "user", "ok": 0}, {"kind": "better", "ok": 1}]
    assert also_close.manual_share(rows) == 0.3333
    assert also_close.manual_share([]) is None


def test_night_ab_directions():
    lo_on = [0.05, 0.06, 0.04, 0.05, 0.05, 0.06]
    hi_off = [0.20, 0.21, 0.19, 0.20, 0.22, 0.20]
    assert also_close.line(lo_on, hi_off)["status"] == "PASS"
    assert also_close.line(hi_off[:3], lo_on[:3])["status"] == "FAIL"
    assert also_close.line(lo_on[:3], hi_off[:3])["status"] == "PENDING"            # too few nights
    assert night_ab.compare(hi_off, lo_on, 6, lower_is_better=False)["status"] == "PASS"
    assert night_ab.compare([], [1.0], 6)["status"] == "PENDING"
