"""[overseer Nov 7 22:30] FOX carried IOWA@NU live plus Sunday's NFL games: Engine._game_on returned None for a
channel shared by several games, so behind_of/scrub_of read 0/off and the buffered standby was never scrubbed."""
from types import SimpleNamespace

from redzone.engine import Engine
from redzone.models import Phase


def _stub(phases: dict, cur=None):
    where = {gid: SimpleNamespace(target="youtubetv:fox") for gid in phases}
    games = {gid: SimpleNamespace(phase=p) for gid, p in phases.items()}
    return SimpleNamespace(where=where, merger=SimpleNamespace(games=games), decider=SimpleNamespace(current=cur))


def test_single_game_on_channel():
    assert Engine._game_on(_stub({"a": Phase.PRE}), "youtubetv:fox", False) == "a"


def test_shared_channel_picks_the_one_live_game():
    s = _stub({"nfl1": Phase.PRE, "cfb": Phase.LIVE, "nfl2": Phase.PRE})
    assert Engine._game_on(s, "youtubetv:fox", False) == "cfb"


def test_shared_channel_two_live_games_stays_unknown():
    s = _stub({"x": Phase.LIVE, "y": Phase.LIVE})
    assert Engine._game_on(s, "youtubetv:fox", False) is None


def test_shared_channel_none_live_stays_unknown():
    s = _stub({"x": Phase.PRE, "y": Phase.POST})
    assert Engine._game_on(s, "youtubetv:fox", False) is None
