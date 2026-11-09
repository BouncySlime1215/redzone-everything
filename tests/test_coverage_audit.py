"""WP-09: the audit scripts read dev nights only; locked nights are refused and never opened.

Why: the planner scripts read every night. A tool that can grade or read a locked night breaks the release gate's
"scored once per release" rule, so the filter is the thing under test (build plan WP-09).
"""
import json
from types import SimpleNamespace

import pytest

from scripts import coverage_audit as ca

NIGHTS = [f"202611{d:02d}" for d in range(1, 9)]          # 8 nights: the newest 2 (25%) are locked
GARBAGE = "{ locked file: opening this would raise"


def make_archive(root, league="mlb", garbage_locked=True):
    d = root / league
    d.mkdir(parents=True)
    for i, n in enumerate(NIGHTS):
        (d / f"scoreboard_{n}.json").write_text(json.dumps({"events": [{"id": f"{league}{i}", "name": "x"}]}))
    return d


@pytest.fixture
def arch(tmp_path):
    make_archive(tmp_path)
    return tmp_path


def test_dev_filter_allows_old_nights_and_refuses_locked(arch):
    dev = ca.DevOnly(arch, "mlb")
    assert dev.first_locked == "20261107"
    assert dev.allows("20261101") and dev.allows("20261106")
    assert not dev.allows("20261107") and not dev.allows("20261108")
    assert not dev.allows("20261201")            # fresh: after the archive window, never seen


def test_require_raises_on_a_locked_night(arch):
    dev = ca.DevOnly(arch, "mlb")
    dev.require(["20261101"])
    with pytest.raises(ca.LockedNightError):
        dev.require(["20261101", "20261108"])


def test_league_without_a_known_split_is_refused(tmp_path):
    with pytest.raises(ca.LockedNightError):
        ca.DevOnly(tmp_path, "cfb")              # no folder
    (tmp_path / "cbb").mkdir()
    with pytest.raises(ca.LockedNightError):
        ca.DevOnly(tmp_path, "cbb")              # empty folder


def test_game_ids_are_dev_only(arch):
    ids = ca.DevOnly(arch, "mlb").game_ids()
    assert set(ids.values()) <= set(NIGHTS[:6]) and len(ids) == 6


def test_scoreboard_files_skip_locked_by_name(arch, tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    for n in NIGHTS + ["20261230"]:
        (raw / f"mlb_{n}.json").write_text("{}")
    dev = ca.DevOnly(arch, "mlb")
    assert [n for n, _ in ca.scoreboard_files(arch, None, "mlb", dev)] == NIGHTS[:6]
    assert [n for n, _ in ca.scoreboard_files(arch, raw, "mlb", dev)] == NIGHTS[:6]


def test_audit_never_opens_a_locked_file(arch, tmp_path):
    for n in NIGHTS[6:]:
        (arch / "mlb" / f"scoreboard_{n}.json").write_text(GARBAGE)
    # archive_nights tolerates the garbage (it still names the night), the audit must not parse it
    res = ca.audit(arch, None, tmp_path / "channels.json", leagues=("mlb",))
    assert res["rows"] == [] and res["refused"] == {}


def test_audit_reports_refused_leagues(arch, tmp_path):
    res = ca.audit(arch, None, tmp_path / "channels.json", leagues=("mlb", "cfb"))
    assert "cfb" in res["refused"]


def test_cli_exits_2_when_a_locked_night_is_named(arch, tmp_path, capsys):
    rc = ca.main(["--archive", str(arch), "--channels", str(tmp_path / "c.json"), "--leagues", "mlb", "--nights", "20261107"])
    assert rc == 2
    assert "refused" in capsys.readouterr().err.lower()
    assert ca.main(["--archive", str(arch), "--channels", str(tmp_path / "c.json"), "--leagues", "mlb", "--nights", "20261101"]) == 0


def test_classify_routes():
    cm = SimpleNamespace(codes={"espn": 1})
    w = lambda t, label="": SimpleNamespace(target=t, label=label)   # noqa: E731
    assert ca.classify(w("youtubetv:espn"), cm) == "yttv_code"
    assert ca.classify(w("youtubetv:espn3"), cm) == "yttv_no_code"
    assert ca.classify(w("youtubetv:st1"), cm) == "sunday_ticket"
    assert ca.classify(w("youtubetv:cbs"), cm) == "yttv_local_search"
    assert ca.classify(w("youtubetv:ev:9"), cm) == "espn_event"
    assert ca.classify(w("prime:abc"), cm) == "prime"
    assert ca.classify(w(None, "Peacock"), cm) == "peacock_only"
    assert ca.classify(w(None, "Netflix"), cm) == "other_app"
    assert ca.classify(w(None, "?"), cm) == "no_route"


def test_reach_table_blackouts_leave_numerator_not_base():
    rows = [dict(l="nba", c="prime", mine=False, blackout=False),
            dict(l="nba", c="prime", mine=False, blackout=True),
            dict(l="nba", c="yttv_code", mine=True, blackout=False),
            dict(l="nhl", c="espn_event", mine=False, blackout=False)]
    t = ca.reach_table(rows)
    assert t["nba"]["games"] == 3 and t["nba"]["reachable"] == 2 and t["nba"]["fast"] == 1
    assert t["all"]["games"] == 4 and t["all"]["reachable"] == 2 and t["all"]["plus_espn_events"] == 3
    assert t["mine"]["games"] == 1
