"""Two sticks where only one can play an app (Peacock signed in on .181 only): the game goes to
the stick that can, never a failed switch, and what was learned names the stick that tuned."""

import pytest

from redzone import control as ctl
from redzone.discovery import providers as prov
from tests.test_hot_standby import FakeStick, FakeTuner, flips, two  # noqa: F401  (fixture)


@pytest.fixture
def walled(two, monkeypatch):
    """Stick b can't play Peacock; stick a (showing FOX) can."""
    c, tuner = two
    peacock = FakeTuner()
    monkeypatch.setitem(ctl.TUNERS, "peacock", peacock)
    for s in c.sticks:
        s.packages["peacock"] = "peacock.pkg"
    c.can_play = lambda host, target: not (host == "b:5555" and target.startswith("peacock:"))
    c.on_target = "youtubetv:fox"
    return c, tuner, peacock


async def test_hidden_stick_that_cannot_play_routes_to_the_visible_one(walled):
    c, _, peacock = walled
    assert c.main.host == "a:5555" and c.hidden.host == "b:5555"
    out = await c.execute("peacock:main", may_cut=lambda: True)
    assert out.ok and out.path == "single" and peacock.calls == [("a:5555", "peacock:main")]
    assert c.main.host == "a:5555" and not flips(c.sticks[1])     # no flip to the walled stick


async def test_no_safe_moment_means_no_tune_on_the_visible_stick(walled, monkeypatch):
    monkeypatch.setattr(ctl, "MAX_SAFE_WAIT_S", 0.05)
    c, _, peacock = walled
    out = await c.execute("peacock:main", may_cut=lambda: False)
    assert not out.ok and out.screen_unchanged and peacock.calls == []


async def test_hidden_stick_that_can_play_still_flips(walled):
    c, _, peacock = walled
    c.can_play = lambda host, target: True
    out = await c.execute("peacock:main", may_cut=lambda: True)
    assert out.path == "tuned" and peacock.calls == [("b:5555", "peacock:main")]


async def test_preload_skips_a_walled_hidden_stick_but_the_game_stays_tunable(walled):
    c, _, _ = walled
    assert c.can_tune("peacock:main")                             # stick a can: the planner may pick it
    assert not c.preload("peacock:main")
    c.can_play = lambda host, target: False
    assert not c.can_tune("peacock:main")


async def test_a_broken_can_play_never_blocks_a_switch(walled):
    c, _, peacock = walled
    def boom(host, target):
        raise RuntimeError("bad record")
    c.can_play = boom
    assert c.can_tune("peacock:main")


async def test_outcome_names_the_stick_that_tuned(walled):
    c, _, _ = walled
    seen = []
    c.submit("peacock:main", on_done=seen.append, may_cut=lambda: True)
    await c._task
    assert seen and seen[0].ok and seen[0].stick == "a:5555"


async def test_runtime_teaches_the_book_per_stick(tmp_path):
    from types import SimpleNamespace
    from redzone.discovery.runtime import DiscoveryRuntime
    rt = DiscoveryRuntime.__new__(DiscoveryRuntime)
    rt.book = prov.ProviderBook(tmp_path / "p.json")
    rt.book.saw_apps("a", {"peacock": "p"})
    rt.book.saw_apps("b", {"peacock": "p"})
    rt.engine = SimpleNamespace(toast=lambda *a: None)
    rt.autonomy = SimpleNamespace(on_tune=lambda *a: None)
    wall = prov.Verdict("signin", "sign in").message("Peacock")
    rt.on_outcome(ctl.Outcome(False, "peacock:x", 1.0, wall, False, stick="b"))
    assert not rt.book.can_play("b", "peacock:y") and rt.book.can_play("a", "peacock:y")
    rt.on_preload_fail("peacock:z", wall, host="a")
    assert not rt.book.states()["peacock"].usable                 # both sticks walled now
