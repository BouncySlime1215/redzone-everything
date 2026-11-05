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


def test_clean_cap_keeps_locked_inside_allowed_nights():
    import backfill_eval_v2 as b
    from redzone.lab import splits
    for ok in (4, 6, 8, 9):
        n = b.max_nights_clean(ok)
        assert len(splits.split_nights([f"{i:08d}" for i in range(n)]).test) <= ok
        assert len(splits.split_nights([f"{i:08d}" for i in range(n + 1)]).test) > ok


def test_locked_nights_that_were_dev_in_the_default_pin_fail(tmp_path, monkeypatch):
    import leak_check as lc
    from redzone.lab import evaldata
    lg = tmp_path / "nba"
    lg.mkdir()
    for i in range(13):                                   # the default pin's 13 nights: Oct 20..Nov 1
        _night(lg, f"2026{(1020 + i) if i < 12 else 1101}", [f"g{i}"])
    for i in range(2):
        _night(lg, f"2026110{i + 2}", [f"f{i}"])          # fresh nights
    for i in range(18):
        _night(lg, f"202603{i + 1:02d}", [f"o{i}"])       # old nights: locked slides back over Oct 26-28
    monkeypatch.setattr(lc, "ARCHIVE", tmp_path)
    monkeypatch.setattr(lc, "LEAGUES", ("nba",))
    monkeypatch.setattr(evaldata, "LOCK", tmp_path / "eval-data-v2.lock.json")
    monkeypatch.setattr(evaldata, "window_end", lambda league, default=None: None)
    rows = lc.check_locked_unseen_by_previous_pin()
    assert rows and rows[0][0] == "FAIL" and "20261028" in rows[0][2]


def test_old_nights_are_never_the_noggins_locked_nights():
    """A backfilled old night lands in the pin's dev (tunable) slice, so it must sit before the Noggin's
    own locked start (agent/distill/split.py BIG_CUTS), and inside the build's window."""
    import re
    from pathlib import Path
    import backfill_eval_v2 as bf
    from agent.distill.split import BIG_CUTS
    for lg, (start, end) in bf.OLD_SEASON.items():
        assert end < BIG_CUTS[lg][2], (lg, end, BIG_CUTS[lg][2])
        assert bf.WINDOW_START[lg] <= start
    wf = (Path(bf.ROOT) / ".github/workflows/eval-data-v2.yml").read_text()
    for lg, st in bf.WINDOW_START.items():
        assert re.search(rf"--window {lg}:{st}:\$END", wf), lg


def test_switch_eval_pin_finds_every_pinned_tag_line():
    import switch_eval_pin as sw
    got = sw.edit_files("eval-data-v9", dry=True)
    assert {g.split(":")[0] for g in got} == {".github/workflows/ci.yml", ".github/workflows/northstar.yml", "CLAUDE.md"}
