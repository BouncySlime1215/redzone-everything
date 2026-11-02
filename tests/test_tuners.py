"""Tuning fast without fooling ourselves: plain `am start`, quick "playing yet?" checks, the old
stream never mistaken for the new one, WAKEUP only when asleep, the screen read only when slow,
no re-tune when already on it, and Prime's press-through still working."""
import time

import pytest

from fake_adb import media
from redzone import control as ctl
from redzone.channels import ChannelMap
from redzone.tuners import adb
from redzone.tuners.adb import RunResult, Stick
from redzone.tuners.tuners import NewStream, PrimeTuner, YouTubeTVTuner

YT, PRIME = "yttv.pkg", "prime.pkg"


class ScriptedStick(Stick):
    """A stick that answers from a script. After a deep link, each "playing yet?" check takes the
    next report from `after` (the last one repeats). Everything sent is kept in `cmds`."""

    def __init__(self, now="", after=(), wake="Awake", screen="", on_key=None):
        super().__init__("fake:5555", name="fake", os="Fire OS 8")
        self.state = "connected"
        self.packages = {"youtubetv": YT, "prime": PRIME}
        self.now, self.after, self.wake, self.screen, self.on_key = now, list(after), wake, screen, on_key
        self.linked = False
        self.cmds: list[str] = []
        self.screen_reads = 0
        self.am_reply = "Starting: Intent { act=android.intent.action.VIEW }"
        self.am_ok = True

    async def _shell(self, cmd, timeout=8.0):
        self.cmds.append(cmd)
        if cmd.startswith("am start"):
            self.linked = True
            return RunResult(self.am_ok, self.am_reply, 0.0)
        if "media_session" in cmd:
            if self.linked and self.after:
                self.now = self.after.pop(0) if len(self.after) > 1 else self.after[0]
            return RunResult(True, self.now, 0.0)
        if "dumpsys power" in cmd:
            return RunResult(True, f"  mWakefulness={self.wake}\n", 0.0)
        if cmd.startswith("input keyevent"):
            if "WAKEUP" in cmd:
                self.wake = "Awake"
            if self.on_key:
                self.on_key(self, cmd.split()[-1])
        return RunResult(True, "", 0.0)

    async def screen_text(self):
        self.screen_reads += 1
        return self.screen

    def links(self):
        return [c for c in self.cmds if c.startswith("am start")]

    def keys(self):
        return [c.split()[-1] for c in self.cmds if c.startswith("input keyevent")]

    def asked(self, what):
        return sum(what in c for c in self.cmds)


def quick(cls=YouTubeTVTuner, **kw):
    """A tuner with the same logic and test-sized waits."""
    t = cls()
    t.poll_s, t.prompt_grace_s, t.loading_grace_s, t.confirm_timeout = 0.002, 0.05, 0.3, 0.4
    for k, v in kw.items():
        setattr(t, k, v)
    return t


def yt(state, pos=0, upd=0):
    return media(YT, state, pos, upd)


async def test_defaults_are_fast():
    t = YouTubeTVTuner()
    assert t.poll_s <= 0.25 and t.prompt_grace_s <= 3.0
    assert adb.AWAKE_TTL_S >= 60


async def test_happy_path_is_quick_and_quiet():
    st = ScriptedStick(after=[yt(8), yt(6), yt(3, 100, 5000)])
    res = await quick().tune(st, "CODE", "youtubetv:espn")
    assert res.ok and res.took < 1.0
    assert st.links() == [f"am start -a android.intent.action.VIEW -d 'https://tv.youtube.com/watch/CODE' {YT}"]
    assert "-W" not in st.links()[0]               # we don't wait for the app to finish opening
    assert st.screen_reads == 0                    # no slow screen read when it just works
    assert st.keys() == []                         # awake: no WAKEUP press
    assert st.tuned["target"] == "youtubetv:espn"
    assert any("confirmed by stick" in s for s in res.steps)


async def test_the_old_channel_still_playing_isnt_taken_for_the_new_one():
    old = yt(3, 1000, 5000)
    st = ScriptedStick(now=old, after=[old, old, old, yt(6, 0, 7000), yt(3, 20, 8000)])
    res = await quick().tune(st, "CODE", "youtubetv:fox")
    assert res.ok
    assert st.tuned["report"]["updated"] == 8000   # confirmed on the new stream, not the old one
    assert st.asked("media_session") >= 6


async def test_a_jump_in_the_playback_clock_means_a_new_stream():
    old = yt(3, 1000, 5000)
    st = ScriptedStick(now=old, after=[old, yt(3, 900_000, 6000)])
    res = await quick().tune(st, "CODE", "youtubetv:fox")
    assert res.ok and st.tuned["report"]["position"] == 900_000


async def test_ignored_link_does_not_confirm_unchanged_old_stream():
    old = yt(3, 1000, 5000)
    st = ScriptedStick(now=old, after=[old])
    res = await quick(confirm_timeout=0.05).tune(st, "CODE", "youtubetv:fox")
    assert not res.ok and st.tuned is None


def test_new_stream_rules():
    w = NewStream({"state": "none"})
    assert w.check({"state": "playing"}, now=0.1)          # wasn't playing before: any playing is new
    old = {"state": "playing", "position": 1000, "updated": 5000}
    w = NewStream(old)
    assert not w.check(dict(old), now=0.1)
    assert not w.check({"state": "buffering"}, now=0.2)
    assert not w.check(dict(old), now=0.3)                 # old channel may simply have buffered
    w = NewStream(old)
    assert not w.check(dict(old), now=9.9) and not w.check(dict(old), now=1000.0)
    assert not w.check({"state": "none"}, now=1001.0)
    assert not w.check(dict(old), now=1002.0)              # failed query is not a transition


def test_fire_os_reload_playing_with_zero_speed_does_not_confirm():
    old = {"state": "playing", "speed": 1.0, "position": 46801268, "updated": 860547,
           "title": "Philadelphia Eagles at Washington Commanders"}
    watch = NewStream(old)
    assert not watch.check({"state": "paused", "speed": 0.0})
    for _ in range(4):
        assert not watch.check({"state": "playing", "speed": 0.0, "position": 0,
                                "updated": 900000, "title": ""})
    assert watch.report is None
    ready = {"state": "playing", "speed": 1.0, "position": 100, "updated": 904000,
             "title": old["title"]}
    assert watch.check(ready)
    assert watch.report == ready


@pytest.mark.parametrize("report", [
    {"state": "playing", "speed": 0.0},
    {"state": "playing", "speed": -1.0},
    {"state": "playing", "speed": float("nan")},
    {"state": "playing", "speed": "unknown"},
    {"state": "playing", "speed": 1.0, "read_ok": False},
    {"state": "paused", "speed": 1.0},
])
def test_not_ready_transport_never_confirms_from_idle(report):
    assert not NewStream({"state": "none"}).check(report)


def test_remembered_target_is_not_ready_when_explicitly_stationary():
    stick = Stick("fake:5555")
    report = {"state": "playing", "speed": 1.0, "pkg": YT, "position": 1000, "updated": 5000}
    stick.remember("youtubetv:espn", report)
    assert not stick.still_on("youtubetv:espn", dict(report, speed=0.0))


async def test_wakeup_only_when_asleep():
    asleep = ScriptedStick(after=[yt(3, 100, 5000)], wake="Asleep")
    awake = ScriptedStick(after=[yt(3, 100, 5000)], wake="Awake")
    assert (await quick().tune(asleep, "CODE", "youtubetv:espn")).ok
    assert (await quick().tune(awake, "CODE", "youtubetv:espn")).ok
    assert asleep.keys() == ["KEYCODE_WAKEUP"]
    assert asleep.cmds.index("input keyevent KEYCODE_WAKEUP") < asleep.cmds.index(asleep.links()[0])
    assert awake.keys() == []


async def test_wakefulness_is_asked_at_most_once_a_minute():
    st = ScriptedStick(after=[yt(8), yt(3, 100, 5000)])
    t = quick()
    await t.tune(st, "A", "youtubetv:a")
    st.tuned = None
    st.linked, st.now, st.after = False, "", [yt(3, 999_999, 9000)]
    await t.tune(st, "B", "youtubetv:b")
    assert st.asked("dumpsys power") == 1
    st._awake_at -= adb.AWAKE_TTL_S + 1
    st.tuned, st.linked, st.now, st.after = None, False, "", [yt(3, 5, 9500)]
    await t.tune(st, "C", "youtubetv:c")
    assert st.asked("dumpsys power") == 2


async def test_unknown_wakefulness_presses_wakeup_to_be_safe():
    st = ScriptedStick(after=[yt(3, 100, 5000)], wake="")
    st.wake = "???"
    assert (await quick().tune(st, "CODE", "youtubetv:espn")).ok
    assert st.keys() == ["KEYCODE_WAKEUP"]


async def test_already_playing_skips_the_wake_check():
    st = ScriptedStick(now=yt(3, 1000, 5000), after=[yt(6), yt(3, 5, 9000)])
    assert (await quick().tune(st, "CODE", "youtubetv:fox")).ok
    assert st.asked("dumpsys power") == 0


async def test_screen_is_read_only_when_its_slow():
    st = ScriptedStick(after=[yt(1)], screen="Home | Live | Library | Recommended for you")   # never plays
    res = await quick().tune(st, "CODE", "youtubetv:espn")
    assert not res.ok and res.error == "video never started"
    assert st.screen_reads == 2                     # once per deep-link attempt, after the grace period
    assert st.keys() == []                          # no prompt on screen: nothing pressed


async def test_a_blank_screen_read_was_too_early_so_look_again():
    screens = ["", "", "Are you still watching? | Continue"]

    def on_key(stick, key):
        stick.after = [yt(6), yt(3, 100, 5000)]
    st = ScriptedStick(after=[yt(0)], on_key=on_key)

    async def screen_text():
        st.screen_reads += 1
        return screens.pop(0) if screens else ""
    st.screen_text = screen_text
    res = await quick(reread_s=0.01, confirm_timeout=2).tune(st, "CODE", "youtubetv:espn")
    assert res.ok and st.screen_reads == 3 and st.keys() == ["KEYCODE_DPAD_CENTER"]
    assert any("are you still watching" in s for s in res.steps)


async def test_loading_gets_longer_grace_before_reading_the_screen():
    st = ScriptedStick(after=[yt(6)])               # buffering the whole time
    await quick(loading_grace_s=10).tune(st, "CODE", "youtubetv:espn")
    assert st.screen_reads == 0


async def test_a_prompt_on_screen_is_cleared():
    def on_key(stick, key):
        if key == "KEYCODE_DPAD_CENTER":
            stick.after = [yt(3, 100, 5000)]
    st = ScriptedStick(after=[yt(2)], screen="Are you still watching? | Continue", on_key=on_key)
    res = await quick().tune(st, "CODE", "youtubetv:espn")
    assert res.ok and "KEYCODE_DPAD_CENTER" in st.keys()
    assert any("cleared prompt: are you still watching" in s for s in res.steps)


async def test_already_on_target_skips_the_deep_link():
    st = ScriptedStick(after=[yt(3, 100, 5000)])
    t = quick()
    assert (await t.tune(st, "CODE", "youtubetv:espn")).ok
    again = await t.tune(st, "CODE", "youtubetv:espn")     # same report: nothing changed since
    assert again.ok and "already on it" in again.steps[0] and len(st.links()) == 1
    st.now = yt(3, 4100, 9000)                              # 4s later, played on smoothly
    assert (await t.tune(st, "CODE", "youtubetv:espn")).ok and len(st.links()) == 1


async def test_a_key_press_or_a_jump_means_tune_again():
    st = ScriptedStick(after=[yt(3, 100, 5000)])
    t = quick()
    await t.tune(st, "CODE", "youtubetv:espn")
    await st.key("KEYCODE_DPAD_CENTER")                     # we no longer know what's on
    st.linked, st.after = False, [yt(6), yt(3, 50, 9000)]
    assert (await t.tune(st, "CODE", "youtubetv:espn")).ok
    assert len(st.links()) == 2
    st.now = yt(3, 600_000, 9000)                           # someone changed the channel or seeked
    st.linked, st.after = False, [yt(6), yt(3, 7, 9900)]
    await t.tune(st, "CODE", "youtubetv:espn")
    assert len(st.links()) == 3


async def test_a_link_the_app_cant_open_fails_fast():
    st = ScriptedStick(after=[yt(1)])
    st.am_reply = "Error: Activity not started, unable to resolve Intent { act=android.intent.action.VIEW }"
    res = await quick(confirm_timeout=5).tune(st, "CODE", "youtubetv:espn")
    assert not res.ok and "couldn't open" in res.error and res.took < 1
    assert len(st.links()) == 1


async def test_prime_presses_through_to_live():
    def on_key(stick, key):
        if key == "KEYCODE_DPAD_CENTER":
            stick.after = [media(PRIME, 6), media(PRIME, 3, 50, 6000)]
    st = ScriptedStick(after=[media(PRIME, 1)], screen="Thursday Night Football | Watch Live | Details",
                       on_key=on_key)
    res = await quick(PrimeTuner, page_wait_s=0.05).tune(st, "GTI", "prime:GTI")
    assert res.ok
    assert "app.primevideo.com/detail?gti=GTI" in st.links()[0]
    assert st.links()[0].startswith("am start -W ")   # Prime reads the page next: wait for it to be drawn
    assert any("pressed through: watch live" in s for s in res.steps)


async def test_prime_blackout_is_reported():
    st = ScriptedStick(after=[media(PRIME, 1)], screen="This video is not available in your location")
    res = await quick(PrimeTuner, page_wait_s=0.02).tune(st, "GTI", "prime:GTI")
    assert not res.ok and any("blackout" in s for s in res.steps)


@pytest.fixture
def one_stick(cfg, tmp_path, monkeypatch):
    cfg.update({"mode": {"dry_run": False}})
    ch = ChannelMap(tmp_path / "ch.json")
    ch.codes["fox"] = "FOXCODE"
    c = ctl.Controller(cfg, ch)
    st = ScriptedStick(after=[yt(3, 100, 5000)])
    c.sticks = [st]
    monkeypatch.setitem(ctl.TUNERS, "youtubetv", quick())
    return c, st


async def test_replay_of_the_game_on_screen_skips_the_retune(one_stick):
    c, st = one_stick
    assert (await c.execute("youtubetv:fox")).ok
    out = await c.execute("youtubetv:fox", replay_back_s=20)
    assert out.ok and len(st.links()) == 1          # no second deep link: straight to rewinding
    assert "KEYCODE_MEDIA_REWIND" in " ".join(st.cmds) and st.keys()[-1] == "KEYCODE_MEDIA_PLAY"
    st.now = yt(3, 80, 9000)                        # rewound: the stream's clock went backwards
    st.linked, st.after = False, [yt(6), yt(3, 9000, 9500)]
    assert (await c.execute("youtubetv:fox")).ok   # back to live after a replay: that needs a real tune
    assert len(st.links()) == 2


async def test_a_link_android_didnt_deliver_fails_without_remembering_wrong_game():
    old = yt(3, 1000, 5000)
    st = ScriptedStick(now=old, after=[old])
    st.am_reply = "Warning: Activity not started, its current task has been brought to the front"
    res = await quick().tune(st, "CODE", "youtubetv:fox")
    # A backgrounded app gets the link resent as a fresh task; if video still never starts, fail.
    assert not res.ok and any("resent the link as a new task" in x for x in res.steps)
    assert st.tuned is None


async def test_failed_launch_never_confirms_or_repeats_uncertain_command():
    st = ScriptedStick(after=[yt(3, 100, 5000)])
    st.am_ok = False
    st.am_reply = "timeout/error: no reply"
    res = await quick().tune(st, "CODE", "youtubetv:fox")
    assert not res.ok and st.tuned is None and len(st.links()) == 1


async def test_a_retry_requires_its_own_playback_transition():
    old = yt(3, 1000, 5000)
    st = ScriptedStick(now=old, after=[yt(6), old])
    res = await quick(confirm_timeout=0.03).tune(st, "CODE", "youtubetv:fox")
    assert not res.ok and st.tuned is None and len(st.links()) == 2


async def test_prime_unreadable_page_never_guesses_focused_watch_button():
    st = ScriptedStick(after=[media(PRIME, 1)], screen="")
    result = await quick(PrimeTuner, page_wait_s=.001, confirm_timeout=.01).tune(st, "GTI", "prime:GTI")
    assert not result.ok and not st.keys()


async def test_background_link_retry_rechecks_cut_permission_after_launch():
    st = ScriptedStick(after=[yt(1)])
    st.am_reply = "Warning: Activity not started, its current task has been brought to the front"
    result = await quick().tune(st, "CODE", "youtubetv:fox", may_cut=lambda: not st.linked)
    assert not result.ok and "window closed" in result.error
    assert len(st.links()) == 1


async def test_a_continue_watching_row_on_an_app_home_page_is_never_pressed():
    st = ScriptedStick(after=[yt(1)], screen="Home | Continue watching | The Office | Recommended for you")
    res = await quick().tune(st, "CODE", "youtubetv:espn")
    assert not res.ok and st.keys() == []           # OK there would start whatever tile has focus


async def test_prime_presses_watch_live_only_once_per_deep_link():
    # The player is slow (pre-roll) and the detail page still reads "Watch Live" under it.
    st = ScriptedStick(after=[media(PRIME, 1)], screen="Thursday Night Football | Watch Live | Details")
    res = await quick(PrimeTuner, page_wait_s=0.01, confirm_timeout=0.2).tune(st, "GTI", "prime:GTI")
    assert not res.ok
    # one press per deep link (2 attempts), never a second CENTER on the same link
    assert len(st.links()) == 2 and st.keys().count("KEYCODE_DPAD_CENTER") == 2


async def test_prime_never_presses_once_the_stream_is_loading():
    def on_key(stick, key):
        stick.after = [media(PRIME, 6)]          # buffering from here on
    st = ScriptedStick(after=[media(PRIME, 6)], screen="Thursday Night Football | Watch Live | Details",
                       on_key=on_key)
    res = await quick(PrimeTuner, page_wait_s=0.01, confirm_timeout=0.05).tune(st, "GTI", "prime:GTI")
    assert not res.ok and st.keys() == []


async def test_prime_slow_cold_start_is_watched_not_failed_or_resent():
    def on_key(stick, key):
        if key == "KEYCODE_DPAD_CENTER":
            stick.after = [media(PRIME, 6), media(PRIME, 3, 50, 6000)]
    st = ScriptedStick(after=[media(PRIME, 1)], screen="Thursday Night Football | Watch Live | Details",
                       on_key=on_key)
    st.am_ok, st.am_reply = False, "timeout/error: no reply in 30s"
    res = await quick(PrimeTuner, page_wait_s=0.01).tune(st, "GTI", "prime:GTI")
    assert res.ok and len(st.links()) == 1


async def test_prime_slow_cold_start_that_never_plays_is_not_resent():
    st = ScriptedStick(after=[media(PRIME, 1)], screen="")
    st.am_ok, st.am_reply = False, "timeout/error: no reply in 30s"
    res = await quick(PrimeTuner, page_wait_s=0.01, confirm_timeout=0.05).tune(st, "GTI", "prime:GTI")
    assert not res.ok and len(st.links()) == 1 and st.tuned is None
