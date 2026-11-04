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
