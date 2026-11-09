"""AUDIT-9: wire.py ranks each league by its own slot in the league order (NFL > CFB > NBA > CBB), not its base."""
from redzone.decider.decider import Candidate
from redzone.models import League
from redzone.noggin import wire
from tests.test_decider import dec, sc  # noqa: F401  (fixture)


def _c(gid, score, league):
    s = sc(gid, score, league=league)
    s.crunch = True
    return Candidate(s, f"youtubetv:{gid}")


def test_cfb_and_cbb_keep_their_own_league():
    assert wire.game_input("a", _c("a", 50, League.CFB), False).league == "cfb"
    assert wire.game_input("b", _c("b", 50, League.CBB), False).league == "cbb"


def test_nfl_crunch_beats_hotter_cfb_crunch(dec):  # noqa: F811
    w = {"cfb": _c("cfb", 95, League.CFB), "nfl": _c("nfl", 60, League.NFL)}
    assert wire.pick(dec.cfg, w, ["cfb", "nfl"], set(), {"cfb": 95, "nfl": 60}) == "nfl"


def test_nba_crunch_beats_hotter_cbb_crunch(dec):  # noqa: F811
    w = {"cbb": _c("cbb", 95, League.CBB), "nba": _c("nba", 60, League.NBA)}
    assert wire.pick(dec.cfg, w, ["cbb", "nba"], set(), {"cbb": 95, "nba": 60}) == "nba"
