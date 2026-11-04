"""eval-data-v2 plumbing (cycle 01 plan, task 4): the side-by-side lock, the locked-nights check and the
backfill's night arithmetic. No network."""
import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


def test_rz_eval_lock_picks_another_pin(monkeypatch):
    from redzone.lab import evaldata
    monkeypatch.setenv("RZ_EVAL_LOCK", "eval-data-v2.lock.json")
    try:
        m = importlib.reload(evaldata)
        assert m.LOCK == ROOT / "eval-data-v2.lock.json"
    finally:
        monkeypatch.delenv("RZ_EVAL_LOCK")
        importlib.reload(evaldata)
    assert evaldata.LOCK == ROOT / "eval-data.lock.json"


def test_needed_nights_matches_the_split_rule():
    import backfill_eval_v2 as b
    from redzone.lab import splits
    n = b.needed_nights(8)
    assert len(splits.split_nights([f"2026{i:04d}" for i in range(n)]).test) >= 8
    assert len(splits.split_nights([f"2026{i:04d}" for i in range(n - 1)]).test) < 8


def _night(d: Path, night: str, gids, abbrs=("AAA", "BBB")):
    ev = [{"id": g, "competitions": [{"competitors": [{"team": {"abbreviation": a}} for a in abbrs]}]} for g in gids]
    (d / f"scoreboard_{night}.json").write_text(json.dumps({"events": ev}))


def test_old_your_team_nights_land_in_train_never_locked(tmp_path, monkeypatch):
    import backfill_eval_v2 as b
    lg = tmp_path / "nba"
    lg.mkdir()
    for i in range(20):                                   # current season
        _night(lg, f"202610{i + 10:02d}", [f"n{i}"])
    for i in range(12):                                   # last season, your-team nights
        _night(lg, f"202603{i + 10:02d}", [f"o{i}"], abbrs=("WSH", "BOS"))
    monkeypatch.setattr(b, "ARCH", tmp_path)
    r = b.split_report("nba")
    assert r["nights"] == 32 and r["locked"] >= 8 and not r["leaks"]
    assert r["locked_from"] >= "20261010"                 # locked = newest current-season nights
    assert r["your_team_games_dev"] == 12 and r["your_team_games_locked"] == 0


def test_locked_nights_check_is_pending_not_fail_when_thin(tmp_path, monkeypatch):
    import leak_check as lc
    lg = tmp_path / "nba"
    lg.mkdir()
    for i in range(13):
        _night(lg, f"202610{i + 10:02d}", [f"g{i}"])
    monkeypatch.setattr(lc, "ARCHIVE", tmp_path)
    monkeypatch.setattr(lc, "LEAGUES", ("nba",))
    rows = lc.check_locked_nights()
    assert rows == [("PENDING", "locked_nights_nba", rows[0][2])] and "4 locked" in rows[0][2]
    for i in range(20):
        _night(lg, f"202611{i + 1:02d}", [f"h{i}"])
    from redzone.lab import evaldata
    monkeypatch.setattr(evaldata, "window_end", lambda league, default=None: None)   # no pin end
    assert lc.check_locked_nights()[0][0] == "PASS"
