"""AUDIT-5: a link Prime ignored moved nothing, so the picture is as far behind live as it was.

Why: the delay book reads `held`. Zeroing it for a stream still running behind makes the next cut land on the wrong
moment, and the next preload stacks a second hold on top of the first.
"""
import pytest

from redzone import control as ctl
from tests.test_control import two  # noqa: F401  (fixture)
from tests.test_prime_confirm import OLD_PLAYER, PLAYER, FakeStick, T, tuner


class LongAgo(dict):
    """The link went out long enough ago for the 'Prime ignored it' rule (6 s)."""

    def get(self, key, default=None):
        return -1e6

    def __setitem__(self, key, value):
        pass


def ignored_stick():
    return FakeStick(path=("player",), activity={"player": OLD_PLAYER})


def ignoring_tuner(fast):
    t = tuner(fast)
    t._link_at = LongAgo()
    return t


@pytest.mark.parametrize("fast", [False, True], ids=["flag_off", "flag_on"])
async def test_a_link_prime_ignored_is_a_kept_stream(fast):
    """Repro: the same player screen kept playing, no page ever came up, and the tune came back as a fresh one."""
    res = await ignoring_tuner(fast).tune(ignored_stick(), "amzn1.dv.gti.test", T)
    assert res.ok and res.kept


class Reloads(FakeStick):
    """The link opens a new player screen."""

    async def view(self, *a, **kw):
        self.activity["player"] = PLAYER
        return await super().view(*a, **kw)


async def test_a_new_player_screen_is_a_fresh_tune():
    """The other half: a player that really reloaded is at the live edge."""
    s = Reloads(path=("player",), activity={"player": OLD_PLAYER})
    res = await tuner().tune(s, "amzn1.dv.gti.test", T)
    assert res.ok and not res.kept


async def test_an_ignored_link_keeps_the_stream_marked_behind_live(two, monkeypatch):  # noqa: F811
    """Repro: 60 s behind live, a link to the game already playing, Prime ignored it, and `held` went to 0: the delay
    book then timed every cut off a picture a minute away from where it thought."""
    c, _ = two
    monkeypatch.setitem(ctl.TUNERS, "prime", ignoring_tuner(False))
    c._set_held(T, 60.0, how="held")
    res = await c._tune(ignored_stick(), T)
    assert res.ok and c.held.get(T) == 60.0
