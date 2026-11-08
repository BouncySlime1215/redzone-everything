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


class _Dec:
    def __init__(self):
        self.current = None

    def adopt(self, gid):
        self.current = gid


class _Ctl:
    def __init__(self, on=None, read=None):
        self.on_target, self.read, self.asked = on, read, None

    async def read_screen(self, targets):
        self.asked = sorted(targets)
        return self.read


def _eng(phases, ctl, pending=None):
    s = _stub(phases)
    s.decider, s.control, s._pending_decision = _Dec(), ctl, pending
    s.recorded = SimpleNamespace(active=False)
    s.cfg = {"mode.dry_run": False}
    s._game_on_target = lambda t: Engine._game_on_target(s, t)
    return s


async def test_relearn_reads_the_stick_back_after_a_restart():
    """[overseer Nov 7 23:00] decider.current None after a restart: the stick's read-back names the live game."""
    s = _eng({"nfl": Phase.PRE, "cfb": Phase.LIVE}, _Ctl(read="youtubetv:fox"))
    assert await Engine._relearn_screen(s) == "cfb" and s.decider.current == "cfb"
    assert s.control.asked == ["youtubetv:fox"]


async def test_relearn_uses_a_known_on_target_without_a_read():
    s = _eng({"cfb": Phase.LIVE}, _Ctl(on="youtubetv:fox", read="youtubetv:espn"))
    assert await Engine._relearn_screen(s) == "cfb" and s.control.asked is None


async def test_relearn_waits_while_a_switch_is_pending():
    s = _eng({"cfb": Phase.LIVE}, _Ctl(read="youtubetv:fox"), pending=object())
    assert await Engine._relearn_screen(s) is None and s.decider.current is None
