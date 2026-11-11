"""AUDIT-3: a crash between the report text and its JSON sidecar must not leave a report without a sidecar."""
from datetime import date

import pytest

from redzone import supervisor as sup
from redzone.supervisor import night_window
from tests.test_audit2_supervisor import _sup


async def test_crash_writing_the_sidecar_leaves_no_half_report(tmp_path, monkeypatch):
    now = [night_window(date(2026, 11, 3))[0] + 600]
    s = _sup(tmp_path, lambda: now[0])
    real, calls = sup.write_metrics, []

    def flaky(path, m):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("killed")
        return real(path, m)
    monkeypatch.setattr(sup, "write_metrics", flaky)
    with pytest.raises(OSError):
        await s.maybe_report(now[0])
    md = tmp_path / "reports" / "2026-11-02.md"
    assert not md.exists()                      # else the retry sees the .md and never writes the sidecar
    now[0] += sup.REPORT_RETRY_S + 1
    await s.maybe_report(now[0])
    assert md.exists() and md.with_suffix(".json").exists()
