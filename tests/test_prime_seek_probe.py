import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("psp", Path(__file__).resolve().parents[1] / "scripts" / "prime_seek_probe.py")
psp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(psp)

PKG = "com.amazon.firebat"


def _dump(pos_ms, upd_ms, state=3):
    return (f"1000.00 2000.00\n  package={PKG}\n    active=true\n"
            f"    state=PlaybackState {{state={state}, position={pos_ms}, buffered position=0, speed=1.0, "
            f"updated={upd_ms}, actions=0}}\n")


def test_behind_extrapolates_from_updated():
    now = 1_800_000_000.0
    # uptime 1,000,000 ms; updated 2 s ago; position = 60 s behind at that moment -> 60 s behind now
    r = psp.behind_of(_dump(int((now - 2 - 60) * 1000), 998_000), PKG, now)
    assert r["behind_s"] == 60.0 and r["age_s"] == 2.0 and not r["stale"]


def test_stale_and_unreadable():
    now = 1_800_000_000.0
    assert psp.behind_of(_dump(int((now - 30) * 1000), 900_000), PKG, now)["stale"]
    assert psp.behind_of("", PKG, now)["behind_s"] is None
    assert psp.behind_of(_dump(12_345, 999_000), PKG, now)["behind_s"] is None   # not a wall-clock position
