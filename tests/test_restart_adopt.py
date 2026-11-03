"""After a restart the TV may already show the game: YouTube TV is adopted by its channel name."""
import pytest

from redzone import control as ctl
from redzone.channels import ChannelMap
from test_control import FakeStick, FakeTuner


class ShowingStick(FakeStick):
    """A stick whose YouTube TV media session names a channel (MediaDescription's 2nd field)."""

    def __init__(self, host, subtitle="ESPN", state="playing", pkg="yttv.pkg"):
        super().__init__(host)
        self.subtitle, self.playback_state, self.showing_pkg = subtitle, state, pkg
        self.reads = 0

    async def playback(self, package=None):
        self.reads += 1
        return {"state": self.playback_state, "pkg": self.showing_pkg, "position": 1000, "updated": 1000,
                "title": "SportsCenter", "subtitle": self.subtitle, "speed": 1.0}


@pytest.fixture
def one(cfg, tmp_path, monkeypatch):
    cfg.update({"mode": {"dry_run": False}, "two_stick": {"enabled": False}})
    ch = ChannelMap(tmp_path / "ch.json")
    ch.codes.update({"espn": "AAAAAAAAAAA", "fox": "BBBBBBBBBBB"})
    c = ctl.Controller(cfg, ch)
    tuner = FakeTuner()
    monkeypatch.setitem(ctl.TUNERS, "youtubetv", tuner)
    return c, tuner


async def test_restart_adopts_youtube_tv_showing_the_target_channel(one):
    c, tuner = one
    c.sticks = [ShowingStick("a:5555", subtitle="ESPN")]
    out = await c.execute("youtubetv:espn")
    assert out.ok and out.path == "already"
    assert tuner.calls == []            # no re-tune: the game on screen was not cut
    assert c.on_target == "youtubetv:espn"
    assert c.main.tuned["target"] == "youtubetv:espn"
    assert c._adopt_once is False


@pytest.mark.parametrize("subtitle,state,pkg", [
    ("FOX", "playing", "yttv.pkg"),      # another channel
    ("", "playing", "yttv.pkg"),         # no channel name to go on
    ("ESPN", "paused", "yttv.pkg"),      # not moving
    ("ESPN", "playing", "prime.pkg"),    # another app
])
async def test_restart_does_not_adopt_without_a_channel_match(one, subtitle, state, pkg):
    c, tuner = one
    c.sticks = [ShowingStick("a:5555", subtitle=subtitle, state=state, pkg=pkg)]
    out = await c.execute("youtubetv:espn")
    assert out.path != "already"
    assert tuner.calls == [("a:5555", "youtubetv:espn")]


async def test_a_mismatch_keeps_the_adopt_chance_for_the_next_switch(one):
    c, tuner = one
    stick = ShowingStick("a:5555", subtitle="ESPN")
    c.sticks = [stick]
    assert not await c._still_on_visible("youtubetv:fox")
    assert c._adopt_once is True
    assert await c._still_on_visible("youtubetv:espn")


async def test_a_replay_ends_adoption(one):
    c, tuner = one
    c.sticks = [ShowingStick("a:5555", subtitle="ESPN")]
    done = []
    c.submit("youtubetv:espn", 30.0, done.append)
    await c._task
    assert c._adopt_once is False
    c.on_target = None
    assert not await c._still_on_visible("youtubetv:espn")   # rewound picture: never adopted as live


async def test_non_youtube_tv_adoption_is_unchanged(one, monkeypatch):
    c, tuner = one
    monkeypatch.setitem(ctl.TUNERS, "prime", FakeTuner())
    stick = ShowingStick("a:5555", subtitle="", pkg="prime.pkg")
    c.sticks = [stick]
    assert await c._still_on_visible("prime:abc")
    assert c.on_target == "prime:abc" and c._adopt_once is False
