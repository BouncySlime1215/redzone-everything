"""Decisive-Q dataset and the leak fence (A-02): locked, tuning and after-cutoff nights never enter the slate pool; an
explicit fenced night is refused (FenceError) before any game file is read; the CLI prints the allowed-night count.
"""
from __future__ import annotations

import pytest

from redzone.lab import fence
from redzone.lab.gym2 import qdata as Q
from scripts import qdata_build
from tests.qdata_fakes import FENCED_NIGHTS, LATE_NIGHTS, TRAIN_NIGHTS, write_archive


@pytest.fixture(scope="module")
def root(tmp_path_factory):
    return write_archive(tmp_path_factory.mktemp("qfence") / "archive")


def test_the_pool_holds_training_nights_only(root):
    pool = Q.night_pool(root)
    assert sorted(pool.nights) == sorted(TRAIN_NIGHTS)
    assert pool.refused["fenced"] == len(FENCED_NIGHTS) and pool.refused["after_cutoff"] == len(LATE_NIGHTS)
    for i in range(200):
        d = Q.draw_slate(pool, 5, i)
        assert d.night in TRAIN_NIGHTS and Q.night_refusal(d.night) is None
        assert 2 <= len(d.games) <= Q.QD_MAX_GAMES and d.pad in Q.QD_PADDINGS


@pytest.mark.parametrize("night", ["20251212", "20251215", "20260105", "20261001", "20261020", "20261031"])
def test_a_fenced_night_is_refused(night):
    assert "window" in Q.night_refusal(night)
    with pytest.raises(fence.FenceError, match="REFUSED"):
        Q.assert_training_night(night)


def test_the_cutoff_is_a_fence_too():
    assert Q.night_refusal("20250930") is None
    assert "cutoff" in Q.night_refusal("20251001") and "cutoff" in Q.night_refusal("20251101")
    assert Q.night_refusal("20250930", cutoff="20250901") is not None


def test_an_explicit_locked_night_is_refused_before_anything_is_read(root):
    with pytest.raises(fence.FenceError, match="locked"):
        Q.night_pool(root, nights=["20250301", "20251215"])
    assert sorted(Q.night_pool(root, nights=["20250301"]).nights) == ["20250301"]


class _NoRead:
    def game(self, league, gid):
        raise AssertionError("a game file was opened for a fenced night")


def test_a_slate_on_a_fenced_night_is_refused_before_loading():
    for night in ("20251215", "20261020", "20251101"):
        d = Q.SlateDraw(index=0, night=night, leagues=("nba", "nba"), games=("1", "2"), u=0.5, pad=8, seed=1)
        with pytest.raises(fence.FenceError):
            Q.build_slate(d, _NoRead())


def test_the_cli_prints_the_allowed_night_count(root, capsys):
    assert qdata_build.main(["pool", "--data-root", str(root)]) == 0
    out = capsys.readouterr().out
    assert f"allowed training nights: {len(TRAIN_NIGHTS)}" in out and "'fenced': 2" in out
    assert qdata_build.main(["pool", "--data-root", str(root), "--nights", "20261020"]) == 2
    assert "REFUSED" in capsys.readouterr().err
