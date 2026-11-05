"""Measured NBA stoppage lengths (cdn action log): restart back-dating, timeout ordinals, period breaks."""
import sys
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from pbp import nba_stoppages as S  # noqa: E402


def _g(rows):
    return pd.DataFrame([{"gameId": 1, "actionNumber": i, "orderNumber": i, "clock": c, "timeActual": t, "period": p,
                          "actionType": a, "subType": s} for i, (c, t, p, a, s) in enumerate(rows)])


def test_timeouts_free_throws_and_period_break():
    rows = [("PT12M00.00S", "2025-01-01T00:00:00Z", 1, "period", "start"),
            ("PT11M50.00S", "2025-01-01T00:00:10Z", 1, "2pt", "Jump Shot"),
            ("PT11M40.00S", "2025-01-01T00:00:20Z", 1, "foul", "personal"),          # stop at 11:40
            ("PT11M40.00S", "2025-01-01T00:00:40Z", 1, "freethrow", "1 of 2"),
            ("PT11M40.00S", "2025-01-01T00:00:55Z", 1, "freethrow", "2 of 2"),
            ("PT11M30.00S", "2025-01-01T00:01:20Z", 1, "2pt", "Layup"),               # restart = 01:10
            ("PT11M30.00S", "2025-01-01T00:01:21Z", 1, "timeout", "full"),            # 1st timeout
            ("PT11M20.00S", "2025-01-01T00:04:31Z", 1, "turnover", "bad pass"),       # restart = 04:21
            ("PT11M20.00S", "2025-01-01T00:04:32Z", 1, "timeout", "full"),            # 2nd
            ("PT11M10.00S", "2025-01-01T00:07:42Z", 1, "2pt", "Layup"),
            ("PT11M10.00S", "2025-01-01T00:07:43Z", 1, "timeout", "full"),            # 3rd -> short
            ("PT11M00.00S", "2025-01-01T00:09:33Z", 1, "2pt", "Layup"),
            ("PT00M00.00S", "2025-01-01T00:20:00Z", 1, "period", "end"),
            ("PT12M00.00S", "2025-01-01T00:22:30Z", 2, "period", "start"),
            ("PT11M50.00S", "2025-01-01T00:22:50Z", 2, "2pt", "Jump Shot")]          # restart = 22:40
    out = S.game_stoppages(_g(rows))
    kinds = [(t, round(s)) for t, _, _, s in out]
    assert ("free_throws", 50) in kinds
    assert [t for t, *_ in out if t.startswith("timeout")] == ["timeout_long", "timeout_long", "timeout_short"]
    assert ("period_break", 160) in kinds
    assert S.bucket(130) == "3-2" and S.bucket(500) == "12-7"
