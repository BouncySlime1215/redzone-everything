"""One place for "this test needs the pinned eval data, and CI has it only when the release is published".

Why: the adversarial replay and the commit-gate replay each carried their own inline pytest.skip, which scripts/fix_needs_test.py
(rightly) counts as a new skip on every PR that touches them. The reason lives here once; a test says what it needs.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def needs_eval_data(league: str) -> None:
    """Outcome 'skipped, reason shown by -rs' when data/archive/<league> is not unpacked (python scripts/fetch_eval_data.py)."""
    if not (ROOT / "data/archive" / league).exists():
        raise pytest.skip.Exception("pinned eval data not fetched (scripts/fetch_eval_data.py)", allow_module_level=False)
