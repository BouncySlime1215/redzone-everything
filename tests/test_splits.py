"""No-overfitting rule: time splits, folds and leakage checks (redzone/lab/splits.py).

The archive-backed test runs wherever the pinned eval data is unpacked (CI does it): it proves the
locked test nights of eval-data-v1 share no night and no game with train/valid.
"""
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

import pytest

from redzone.lab import splits

ROOT = Path(__file__).resolve().parent.parent
ET = ZoneInfo("America/New_York")


def _nights(n):
    return [f"2026{i:04d}" for i in range(n)]


@pytest.mark.parametrize("n", [2, 4, 14, 15, 16, 78, 400])
def test_split_is_time_ordered_and_test_is_at_least_a_quarter(n):
    s = splits.split_nights(reversed(_nights(n)))
    assert len(s.test) >= 0.25 * n and len(s.train) + len(s.valid) + len(s.test) == n
    assert splits.leaks(s) == []
    assert s.cutoff < min(s.test)


def test_folds_chain_forward_and_never_overlap():
    f = splits.folds(_nights(60))
    assert len(f) == splits.FOLDS
    for fit, check in f:
        assert max(fit) < min(check) and not set(fit) & set(check)
    assert [len(x) for x, _ in f] == sorted(len(x) for x, _ in f)
    assert splits.folds(_nights(3)) == []


def test_leaks_catches_shared_nights_games_and_time_order():
    bad = splits.Split(("20261001", "20261005"), ("20261002",), ("20261003", "20261005"))
    found = " ".join(splits.leaks(bad, {"20261001": ["g1"], "20261003": ["g1"]}))
    assert "train and test" in found and "time order" in found and "game g1" in found


def test_causal_flags_future_features():
    assert splits.causal([1.0, 2.0, None], 2.0) == []
    assert splits.causal([1.0, 3.5], 2.0) == [3.5]


def test_fresh_nights_are_after_the_pin():
    assert splits.fresh(["20261101", "20261102", "20261103"], "20261101") == ("20261102", "20261103")


@pytest.mark.parametrize("league", ["nfl", "nba", "nhl"])
def test_pinned_archive_locked_test_set_does_not_leak(league):
    d = ROOT / "data/archive" / league
    if not d.exists():
        pytest.skip("pinned eval data not unpacked")
    by_night: dict[str, list[str]] = {}
    for f in d.glob("summary_*.json"):
        try:
            hdr = json.loads(f.read_text()).get("header", {})
            date = (hdr.get("competitions") or [{}])[0].get("date", "")
        except (OSError, ValueError):
            continue
        if date:
            start = datetime.fromisoformat(date.replace("Z", "+00:00")).astimezone(ET)
            by_night.setdefault(start.strftime("%Y%m%d"), []).append(f.stem.split("_", 1)[1])
    s = splits.split_nights(by_night)
    assert s.test and splits.leaks(s, by_night) == []
