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


async def test_read_screen_learns_the_live_channel_after_a_restart(one):
    """[overseer Nov 7 23:00] on_target None after a restart: a read-back (no tune) names the screen's channel."""
    c, tuner = one
    stick = ShowingStick("a:5555", subtitle="ESPN")
    c.sticks = [stick]
    assert await c.read_screen(["youtubetv:fox", "youtubetv:espn"]) == "youtubetv:espn"
    assert c.on_target == "youtubetv:espn" and tuner.calls == []
    assert await c.read_screen(["youtubetv:espn"]) is None    # already known: no second read
    assert stick.reads == 1


@pytest.mark.parametrize("subtitle,state", [("FOX", "playing"), ("", "playing"), ("ESPN", "paused")])
async def test_read_screen_needs_one_ready_channel_match(one, subtitle, state):
    c, tuner = one
    c.sticks = [ShowingStick("a:5555", subtitle=subtitle, state=state)]
    assert await c.read_screen(["youtubetv:espn"]) is None
    assert c.on_target is None


async def test_read_screen_never_after_a_replay(one):
    c, tuner = one
    c.sticks = [ShowingStick("a:5555", subtitle="ESPN")]
    c.submit("youtubetv:espn", 30.0, lambda out: None)
    await c._task
    c.on_target = None
    assert await c.read_screen(["youtubetv:espn"]) is None    # a rewound picture is never read back as live



class PrimeShowing(ShowingStick):
    def __init__(self, host, text):
        super().__init__(host, subtitle="", pkg="prime.pkg")
        self.text = text

    async def ocr_text(self):
        return self.text


@pytest.mark.parametrize("text,want", [
    ("MAGIC 98 WARRIORS 100 4TH 1:25", "prime:orlgs"),   # the score bug names one live Prime game
    ("MAGIC 98 4TH", None),                              # one name only
    ("LAKERS 90 BLAZERS 88", None),                      # another game
])
async def test_read_screen_learns_a_prime_game_by_its_score_bug(one, text, want):
    """[overseer Nov 7 23:50] Prime names no game: its score bug's team names pick the one live Prime target."""
    c, tuner = one
    c.sticks = [PrimeShowing("a:5555", text)]
    c.target_names = lambda t: {"prime:orlgs": ["Magic", "Warriors"], "prime:nyknj": ["Knicks", "Nets"]}[t]
    assert await c.read_screen(["prime:orlgs", "prime:nyknj"]) == want
    assert c.on_target == want and tuner.calls == []


def test_held_offsets_survive_a_restart(cfg, tmp_path):
    """[overseer Nov 7 23:50] the screen ran ~79 s behind; after a restart the app thought 17 s."""
    import time as _t
    a = ctl.Controller(cfg, ChannelMap(tmp_path / "ch.json"))
    a.buffer_log_path = tmp_path / "buffer.jsonl"
    a._set_held("prime:x", 78.9)
    b = ctl.Controller(cfg, ChannelMap(tmp_path / "ch.json"))
    b.buffer_log_path = tmp_path / "buffer.jsonl"
    seen = []
    b.on_behind = lambda t, v, old: seen.append((t, v))
    assert b.restore_held() == {"prime:x": 78.9} and b.held["prime:x"] == 78.9 and seen == [("prime:x", 78.9)]
    c = ctl.Controller(cfg, ChannelMap(tmp_path / "ch.json"))
    c.buffer_log_path = tmp_path / "buffer.jsonl"
    assert c.restore_held(now=_t.time() + 601) == {}     # too old: nothing trusted
