"""Noggin Priority wired into Decider._best (noggin.priority.enabled): L1 holds through the decider."""
import pytest

from redzone.decider.decider import Candidate
from redzone.models import League
from tests.test_decider import cands, dec, sc  # noqa: F401  (fixture)


@pytest.fixture
def on(dec):  # noqa: F811
    dec.cfg.update({"noggin": {"priority": {"enabled": True}}}, record=False)
    return dec


def _c(gid, score, league=League.NFL, app="youtubetv", **kw):
    s = sc(gid, score, league=league)
    for k, v in kw.items():
        setattr(s, k, v)
    return Candidate(s, f"{app}:{gid}")


def test_off_by_default_keeps_todays_pick(dec):  # noqa: F811
    assert dec.cfg.get("noggin.priority.enabled") is False
    assert dec._best(cands(a=40, b=80), None) == "b"


def test_open_tier_is_still_by_score(on):
    assert on._best(cands(a=40, b=80), None) == "b"


def test_your_team_big_moment_beats_a_hotter_game(on):
    w = {"wsh": _c("wsh", 30, lock="red zone drive", personal=10.0), "x": _c("x", 90)}
    assert on._best(w, None) == "wsh"


def test_crunch_by_league_order_not_score(on):
    w = {"nhl": _c("nhl", 95, League.NHL, crunch=True), "nfl": _c("nfl", 60, crunch=True), "o": _c("o", 99)}
    assert on._best(w, None) == "nfl"


def test_never_peacock(on):
    w = {"p": _c("p", 99, app="peacock", crunch=True), "y": _c("y", 10)}
    assert on._best(w, None) == "y"
    assert on._best({"p": w["p"]}, None) is None


def test_a_your_team_lock_that_is_not_a_big_moment_stays_open_tier(on):
    w = {"wsh": _c("wsh", 30, lock="some other hold", personal=10.0), "x": _c("x", 90)}
    assert on._best(w, None) == "x"


def test_pick_rows_reach_the_recorder_and_a_bad_sink_never_breaks_a_decision(dec, tmp_path):  # noqa: F811
    """Live lab Nov 4: PICK in shadow on the Mac wrote only to an in-memory deque, so nothing could be read
    back. Rows (and why it's silent) now go to recordings/<day>/pick_shadow.jsonl."""
    import json
    from redzone.recorder import Recorder
    rec = Recorder(tmp_path)
    dec.on_pick = rec.pick
    dec._emit_pick({"t": 1.0, "on": "a", "pick": "b", "app_action": "stay", "app_target": None, "agree": False})
    files = list(tmp_path.glob("recordings/*/pick_shadow.jsonl"))
    assert len(files) == 1 and json.loads(files[0].read_text())["pick"] == "b"

    def boom(row):
        raise OSError("disk full")
    dec.on_pick = boom
    dec._emit_pick({"t": 2.0})          # swallowed and logged


def test_engine_wires_pick_rows_to_its_recorder(cfg, tmp_path, monkeypatch):
    from redzone.engine import Engine
    from redzone.recorder import Recorder
    e = Engine(cfg)
    e.recorder = Recorder(tmp_path)
    e.decider._emit_pick({"t": 1.0, "gated": "screen lag 40s < feed p90 + tune 75s"})
    assert list(tmp_path.glob("recordings/*/pick_shadow.jsonl"))
