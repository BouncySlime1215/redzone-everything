"""[EXP-604] the wait arm's post-scrub re-check: the cut moves to the next dead ball the app can see."""
from types import SimpleNamespace

import pytest

from redzone.config import Config
from redzone.lab import backtest as B
from redzone.models import League


class _Clock:
    t = 0.0


def _stick():
    return B.HiddenStick("wait", 75.0, 1.0, Config(), _Clock(), 0, {}, entry="scrub")


def _rep(plays, wall):
    return SimpleNamespace(gid="g", league=League.NBA, plays=plays, wall=wall, matchup="A @ B")


def test_recheck_off_by_default():
    assert _stick().recheck is None


def test_unseen_plays_are_never_safe():
    s = _stick()
    r = _rep([{"type": {"text": "Jump Shot"}}], [1000.0])
    s.recheck = ({"g": r}, 0.0, 10_000.0, 0.0)   # the feed never delivers anything by then
    sw = SimpleNamespace(to="g", t=1500.0, arrive_dt=0.0, offset=0.0)
    assert s.safe_after(sw, 1500.0) == 0.0 and s.recheck_none == 1


def test_recheck_needs_wait_arm():
    from redzone.lab.backtest import run
    with pytest.raises(ValueError):
        run([], Config(), set(), hidden_stick="live_edge", behind_live_s=75.0, hs_entry="scrub", hs_recheck=True)


def test_resync_prime_edge_flag_exists():
    import inspect

    from redzone.lab.backtest import run
    assert inspect.signature(run).parameters["hs_resync_prime_edge"].default is False
