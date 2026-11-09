"""AUDIT-2, supervisor: a crashed night report is retried, not lost.

Why: maybe_report marked the night as checked before building the report, so one crash (a full disk, a bad
row) meant no report that night and none ever after, with nothing on the phone saying so.
"""
from datetime import date
from types import SimpleNamespace

import pytest

from redzone import supervisor as sup
from redzone.config import Config
from redzone.store import Store
from redzone.supervisor import Supervisor, night_window


def _sup(tmp_path, clock):
    e = SimpleNamespace(store=Store(tmp_path / "t.db"), cfg=Config(tmp_path / "c.yaml"),
                        decider=SimpleNamespace(timelines={}, safe_mode=False))
    return Supervisor(e, root=tmp_path, clock=clock)


async def test_a_crashed_report_is_retried_later(tmp_path, monkeypatch):
    now = [night_window(date(2026, 11, 3))[0] + 600]
    s = _sup(tmp_path, lambda: now[0])
    real, calls = sup.night_report, []

    def flaky(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("disk full")
        return real(*a, **k)
    monkeypatch.setattr(sup, "night_report", flaky)
    with pytest.raises(OSError):
        await s.maybe_report(now[0])
    path = tmp_path / "reports" / "2026-11-02.md"
    assert not path.exists()
    await s.maybe_report(now[0] + 1)             # too soon: no hammering
    assert len(calls) == 1
    now[0] += sup.REPORT_RETRY_S + 1
    await s.maybe_report(now[0])
    assert path.exists() and len(calls) == 2
    await s.maybe_report(now[0] + 5)             # written: not again
    assert len(calls) == 2


async def test_a_report_that_keeps_crashing_gives_up(tmp_path, monkeypatch):
    now = [night_window(date(2026, 11, 3))[0] + 600]
    s = _sup(tmp_path, lambda: now[0])
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise OSError("x")
    monkeypatch.setattr(sup, "night_report", boom)
    for _ in range(sup.REPORT_TRIES + 3):
        now[0] += sup.REPORT_RETRY_S + 1
        with pytest.raises(OSError):
            await s.maybe_report(now[0])
        if len(calls) >= sup.REPORT_TRIES:
            break
    await s.maybe_report(now[0] + sup.REPORT_RETRY_S + 1)
    assert len(calls) == sup.REPORT_TRIES
