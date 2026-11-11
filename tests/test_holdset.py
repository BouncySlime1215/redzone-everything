"""WP-09: holdset.py and reach_grid.py open dev-night summaries only; a locked game's file is never read.

Why: both scripts need per-game summaries, whose date is inside the file. The night comes from the scoreboards
instead, so a locked summary can stay closed (build plan WP-09, dev-only filter test).
"""
import json

import pytest

from scripts import holdset, reach_grid

NIGHTS = [f"202611{d:02d}" for d in range(1, 9)]


@pytest.fixture
def arch(tmp_path):
    d = tmp_path / "mlb"          # no lock window and no dev_before for mlb: plain newest 25% (2 of 8) locked
    d.mkdir()
    for i, n in enumerate(NIGHTS):
        gid = f"g{i}"
        (d / f"scoreboard_{n}.json").write_text(json.dumps({"events": [{"id": gid}]}))
        body = {"plays": []} if i < 6 else "{ locked summary: opening this would raise"
        (d / f"summary_{gid}.json").write_text(body if isinstance(body, str) else json.dumps(body))
    return tmp_path


def test_holdset_games_yield_dev_nights_only(arch, monkeypatch):
    monkeypatch.setattr(holdset, "A", arch)
    got = list(holdset.games("mlb"))              # would raise JSONDecodeError if a locked summary were opened
    assert [n for n, _, _ in got] == NIGHTS[:6]
    assert [g for _, g, _ in got] == [f"g{i}" for i in range(6)]


def test_reach_grid_games_yield_dev_nights_only(arch, monkeypatch):
    monkeypatch.setattr(reach_grid, "A", arch)
    assert len(list(reach_grid.games("mlb"))) == 6


def test_refused_league_yields_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(reach_grid, "A", tmp_path)
    assert list(reach_grid.games("nfl")) == []
    assert "REFUSED" in capsys.readouterr().out
    monkeypatch.setattr(holdset, "A", tmp_path)
    with pytest.raises(holdset.LockedNightError):
        list(holdset.games("nfl"))


def test_holdset_main_skips_refused_league(tmp_path, capsys):
    out = tmp_path / "o.json"
    holdset.main(["--archive", str(tmp_path), "--out", str(out), "--leagues", "nfl"])
    assert "REFUSED" in capsys.readouterr().out
    assert json.loads(out.read_text()) == {}


def test_capture_and_fit_basics():
    dec = [[("a", (1, 0, 0, 0), 1.0, 5.0, 0), ("b", (1, 0, 1, 0), 2.0, 0.0, 0), ("c", (1, 0, 2, 0), 3.0, 1.0, 0)]]
    got, tot, best, n = holdset.capture(dec, lambda x: x[3], 1)
    assert (got, tot, best, n) == (5.0, 6.0, 5.0, 1)
    pred, glob = holdset.fit(dec)
    assert glob == pytest.approx(2.0) and isinstance(pred((1, 0, 0, 0)), float)
