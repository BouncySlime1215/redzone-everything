"""Coming back to the game we left is cheap (fake sticks, no adb, no TV):
- Prime keeps its player when its stick loses the TV input: the game we left stays the standby with
  no re-tune (~33 s), and going back is HOME + REORDER_TO_FRONT;
- Prime's player gets its own (longer) grace to report after the flip before anything is re-linked;
- YouTube TV / Peacock pause: their refresh waits while the planner wants the stick for something else;
- a Prime standby is judged on its wall-clock position (~1% drift), not on one odd read."""
import asyncio
import time

import pytest

from redzone import control as ctl
from redzone.tuners.adb import RunResult
from redzone.tuners.tuners import TuneResult
from test_hot_standby import FakeStick, FakeTuner, refreshed, two  # noqa: F401  (fixture)

PRIME_FOX = "prime:amzn1.dv.gti.fox"
PRIME_CBS = "prime:amzn1.dv.gti.cbs"


class PrimeStick(FakeStick):
    """Prime's live player: every game is one "PrimeVideo" session on a wall-clock position that
    runs ~1% fast. `pause()` freezes it (what YouTube TV does on input loss), `stall_s` makes it
    report nothing for a while after HOME (its player re-registering after REORDER_TO_FRONT)."""

    def __init__(self, host, **kw):
        super().__init__(host, **kw)
        self.rate = 1.01
        self._t0 = time.monotonic()
        self._p0 = 1_793_640_000_000
        self.frozen: tuple[int, int] | None = None
        self.stall_s = 0.0
        self._stall_until = 0.0
        self.fail_reads = 0

    def _now(self):
        el = time.monotonic() - self._t0
        return int(self._p0 + el * 1000 * self.rate), int(el * 1000) + 1000

    def pause(self):
        self.frozen = self._now()
        self.playing = "paused"

    def seek_back(self, seconds):
        self._p0 -= int(seconds * 1000)

    async def shell(self, cmd, timeout=8.0):
        if "KEYCODE_HOME" in cmd and self.stall_s:
            self._stall_until = time.monotonic() + self.stall_s
        return await super().shell(cmd, timeout)

    async def playback(self, package=None):
        if self.fail_reads:
            self.fail_reads -= 1
            return {"state": "none", "pkg": None, "position": None, "updated": None, "read_ok": False}
        if time.monotonic() < self._stall_until:
            return {"state": "none", "pkg": None, "position": None, "updated": None, "read_ok": True}
        if self.playing == "paused" and self.frozen:
            pos, upd = self.frozen
        else:
            pos, upd = self._now()
        return {"state": self.playing, "pkg": package, "position": pos, "updated": upd,
                "title": "PrimeVideo", "read_ok": True}


class PrimeTuner(FakeTuner):
    async def tune(self, stick, ref, target, may_cut=None):
        self.calls.append((stick.host, target))
        if target in self.slow:
            await asyncio.sleep(self.slow[target])
        if self.ok:
            stick.playing = "playing"
            stick.frozen = None
            stick.remember(target, await stick.playback(stick.packages.get("prime")))
        return TuneResult(self.ok, target, 33.0, ["playing"], "" if self.ok else "video never started")


@pytest.fixture
def prime(two, monkeypatch):
    c, yttv = two
    c.sticks = [PrimeStick("a:5555"), PrimeStick("b:5555")]
    tuner = PrimeTuner()
    monkeypatch.setitem(ctl.TUNERS, "prime", tuner)
    monkeypatch.setattr(ctl, "FLIP_POLL_S", 0.01)
    monkeypatch.setattr(ctl, "RESUME_POLL_S", 0.01)
    monkeypatch.setattr(ctl, "RELAUNCH_AFTER_S", 0.0)
    monkeypatch.setattr(ctl, "RESUME_WAIT_S", 0.2)
    monkeypatch.setattr(ctl, "RESUME_PLAY_WAIT_S", 0.2)
    monkeypatch.setattr(ctl, "REFRESH_POLL_S", 0.01)
    FakeStick.last_input_host = "a:5555"
    yield c, tuner, yttv
    FakeStick.last_input_host = None


async def on_screen(c, tuner, target):
    """Stick a shows `target`, tuned and confirmed by us."""
    await tuner.tune(c.main, "", target)
    tuner.calls.clear()
    c.on_target = target


# --- the flip back: Prime's player gets its own grace ------------------------------------------

async def test_prime_flip_waits_for_its_player_instead_of_relinking(prime, monkeypatch):
    """At 6 s Prime's player still read "none" after REORDER_TO_FRONT and was re-linked (~33 s, one
    switch timed out at 75 s on Nov 2). Its own grace lets the playing game come back by itself."""
    monkeypatch.setattr(ctl, "PRIME_RESUME_WAIT_S", 2.0)
    c, tuner, _ = prime
    await on_screen(c, tuner, PRIME_FOX)
    assert c.preload(PRIME_CBS)
    await c._preload_task
    tuner.calls.clear()
    c.hidden.stall_s = 0.5                               # longer than RESUME_WAIT_S (0.2 here)
    out = await c.execute(PRIME_CBS, may_cut=lambda: True)
    assert out.ok and out.path == "preloaded" and out.resumed is True
    assert c.last_flip["how"] == "auto" and tuner.calls == []    # no re-link


def stale_paused_prime(b, resume_after_s):
    """The older stick's Prime (device trial, Nov 2): it pauses when its stick loses the TV input,
    keeps reporting that stale "paused" after HOME + REORDER_TO_FRONT, ignores MEDIA_PLAY, and plays
    again by itself ~10 s later (`resume_after_s` here)."""
    real = b.shell
    b.resume_at = None

    async def shell(cmd, timeout=8.0):
        if "KEYCODE_HOME" in cmd:
            b.pause()
            b.resume_at = time.monotonic() + resume_after_s
        return await real(cmd, timeout)
    b.shell = shell

    async def key(*codes):
        b.log.extend(codes)                       # MEDIA_PLAY goes unanswered
        return RunResult(True, "", 0.01)
    b.key = key
    read = b.playback

    async def playback(package=None):
        if b.resume_at is not None and time.monotonic() >= b.resume_at:
            b.playing, b.frozen, b.resume_at = "playing", None, None
        return await read(package)
    b.playback = playback


async def test_a_stale_paused_prime_after_a_flip_waits_out_its_grace(prime, monkeypatch):
    """Review blocker (control-return-home): a 2 s "paused" shortcut sent MEDIA_PLAY to the VISIBLE
    stick, waited 3 s and re-linked on a stale read: a 33-55 s on-screen Prime tune or the 75 s
    switch failure, instead of the ~10 s self-resume. Prime gets no shortcut and no MEDIA_PLAY
    inside its grace, and no re-link."""
    monkeypatch.setattr(ctl, "PRIME_RESUME_WAIT_S", 1.5)
    monkeypatch.setattr(ctl, "RESUME_PAUSED_S", 0.05)
    c, tuner, _ = prime
    await on_screen(c, tuner, PRIME_FOX)
    assert c.preload(PRIME_CBS)
    await c._preload_task
    tuner.calls.clear()
    b = c.hidden
    stale_paused_prime(b, resume_after_s=0.8)       # well past RESUME_PAUSED_S, inside the grace
    t0 = time.monotonic()
    out = await c.execute(PRIME_CBS, may_cut=lambda: True)
    assert out.ok and out.path == "preloaded" and out.resumed is True
    assert c.last_flip["how"] == "auto"
    assert time.monotonic() - t0 >= 0.8             # it waited for the player
    assert "KEYCODE_MEDIA_PLAY" not in b.log        # no play press on the visible stick
    assert tuner.calls == []                        # no re-link


async def test_prime_reasserted_resume_also_skips_the_paused_shortcut(prime, monkeypatch):
    """The re-assert path (TV input re-taken) shares _resume_playback with Prime's full grace."""
    monkeypatch.setattr(ctl, "PRIME_RESUME_WAIT_S", 1.5)
    monkeypatch.setattr(ctl, "RESUME_PAUSED_S", 0.05)
    c, tuner, _ = prime
    await on_screen(c, tuner, PRIME_FOX)
    a = c.main
    stale_paused_prime(a, resume_after_s=0.6)
    await a.shell("input keyevent KEYCODE_HOME")
    how = await c._resume_playback(a, PRIME_FOX, c._resume_wait(PRIME_FOX))
    assert how == "auto" and "KEYCODE_MEDIA_PLAY" not in a.log and tuner.calls == []


async def test_playback_is_read_again_before_any_relink(prime, monkeypatch):
    """The re-link decision used the read taken before MEDIA_PLAY and the screenshot check. A
    player that came back meanwhile must not be re-tuned on screen."""
    monkeypatch.setattr(ctl, "PRIME_RESUME_WAIT_S", 0.1)
    c, tuner, _ = prime
    await on_screen(c, tuner, PRIME_FOX)
    a = c.main
    a.pause()

    async def key(*codes):                            # MEDIA_PLAY unanswered
        a.log.extend(codes)
        return RunResult(True, "", 0.01)
    a.key = key

    async def prompt_check(stick, target, pkg):       # the ~2 s screenshot read: no prompt found,
        a.playing, a.frozen = "playing", None         # but the player came back meanwhile
        return False
    monkeypatch.setattr(c, "_clear_screen_prompt", prompt_check)
    how = await c._resume_playback(a, PRIME_FOX, c._resume_wait(PRIME_FOX))
    assert how == "auto" and tuner.calls == []      # no re-link on a stale read


async def test_youtube_tv_paused_after_the_grace_still_gets_media_play(prime, monkeypatch):
    """Unchanged for the other apps: a paused player after its grace gets MEDIA_PLAY."""
    c, tuner, _ = prime
    a = c.main
    a.playing, a.frozen = "paused", (1, 1)

    async def key(*codes):
        a.log.extend(codes)
        if "KEYCODE_MEDIA_PLAY" in codes:
            a.playing, a.frozen = "playing", None
        return RunResult(True, "", 0.01)
    a.key = key
    monkeypatch.setattr(c, "_plays_target", lambda stick, target, pb, pkg: pb.get("state") == "playing")
    how = await c._resume_playback(a, "youtubetv:espn", 0.1)
    assert how == "media_play"


async def test_youtube_tv_keeps_its_short_grace(two, monkeypatch):
    monkeypatch.setattr(ctl, "RESUME_WAIT_S", 0.2)
    assert ctl.Controller._resume_wait("youtubetv:espn") == 0.2
    assert ctl.Controller._resume_wait(PRIME_FOX) == ctl.PRIME_RESUME_WAIT_S


# --- leaving a Prime game: keep it, don't re-tune it ---------------------------------------------

@pytest.fixture
def keep_fast(monkeypatch):
    monkeypatch.setattr(ctl, "KEEP_CONFIRM_S", 0.05)
    monkeypatch.setattr(ctl, "PRIME_KEEP_WAIT_S", 0.5)


async def left_prime(c, tuner):
    """Watching Prime FOX on a; switch to Prime CBS (tuned on b). a now holds FOX, unconfirmed."""
    await on_screen(c, tuner, PRIME_FOX)
    out = await c.execute(PRIME_CBS, may_cut=lambda: True)
    assert out.ok and c.main.host == "b:5555"
    assert c.standby == (PRIME_FOX, None) and not c.hidden_ready
    return c.sticks[0]


async def test_a_prime_game_we_left_that_keeps_playing_is_kept_not_retuned(prime, keep_fast):
    c, tuner, _ = prime
    a = await left_prime(c, tuner)
    await refreshed(c)
    assert c.standby == (PRIME_FOX, None) and c.hidden_ready
    assert tuner.calls == [("b:5555", PRIME_CBS)]              # no ~33 s reload of FOX on a
    assert not any("HOME" in x or "WAKEUP" in x for x in a.log)
    assert c.deadline_cost(PRIME_FOX) < c.deadline_cost("prime:amzn1.dv.gti.nbc")   # the decider plans a flip
    out = await c.execute(PRIME_FOX, may_cut=lambda: True)       # back: one flip
    assert out.ok and out.path == "preloaded" and c.main is a
    assert tuner.calls == [("b:5555", PRIME_CBS)]


async def test_a_prime_game_we_left_that_paused_is_reloaded_in_the_background(prime, keep_fast):
    c, tuner, _ = prime
    a = await left_prime(c, tuner)
    a.pause()                                                    # it did pause this time
    await refreshed(c)
    assert tuner.calls[-1] == ("a:5555", PRIME_FOX) and c.hidden_ready
    assert not any("HOME" in x or "WAKEUP" in x for x in a.log)


async def test_a_prime_game_we_left_that_fell_behind_is_reloaded(prime, keep_fast):
    c, tuner, _ = prime
    a = await left_prime(c, tuner)
    a.seek_back(30)                                              # playing, but 30 s behind where it was
    await refreshed(c)
    assert tuner.calls[-1] == ("a:5555", PRIME_FOX)


async def test_a_prime_game_we_left_that_never_reports_is_reloaded_after_its_grace(prime, keep_fast):
    c, tuner, _ = prime
    a = await left_prime(c, tuner)
    a.playing = "none"
    await refreshed(c)
    assert tuner.calls[-1] == ("a:5555", PRIME_FOX)


async def test_one_failed_read_doesnt_cost_a_reload(prime, keep_fast):
    c, tuner, _ = prime
    a = await left_prime(c, tuner)
    a.fail_reads = 3                                             # adb hiccups, then normal reads
    await refreshed(c)
    assert c.hidden_ready and tuner.calls == [("b:5555", PRIME_CBS)]


async def test_the_planners_pick_wins_over_keeping_the_prime_game(prime, keep_fast, monkeypatch):
    monkeypatch.setattr(ctl, "KEEP_CONFIRM_S", 5.0)              # still confirming when the planner asks
    c, tuner, _ = prime
    await left_prime(c, tuner)
    task = c._refresh_task
    assert c.preload("prime:amzn1.dv.gti.nbc")
    await c._preload_task
    await asyncio.gather(task, return_exceptions=True)
    assert c.standby == ("prime:amzn1.dv.gti.nbc", None) and c.hidden_ready
    assert ("a:5555", PRIME_FOX) not in tuner.calls


async def test_youtube_tv_we_left_is_still_reloaded(two, monkeypatch):
    """YouTube TV pauses on input loss: the old refresh, unchanged (no keep check for it)."""
    monkeypatch.setattr(ctl, "REFRESH_POLL_S", 0.01)
    c, tuner = two
    c.on_target = "youtubetv:fox"
    c.main.remember(c.on_target, await c.main.playback("yttv.pkg"))
    await c.execute("youtubetv:espn", may_cut=lambda: True)
    await refreshed(c)
    assert tuner.calls[-1] == ("a:5555", "youtubetv:fox") and c.hidden_ready


# --- is the Prime standby still live? -------------------------------------------------------------

async def prime_standby(c, tuner):
    """Prime CBS preloaded and confirmed on the hidden stick, Prime FOX on screen."""
    await on_screen(c, tuner, PRIME_FOX)
    assert c.preload(PRIME_CBS)
    await c._preload_task
    assert c.standby == (PRIME_CBS, None) and c.hidden_ready
    return c.hidden


async def verify(c):
    c._verified_at = 0
    await c.verify_standby()


async def test_a_prime_standby_that_keeps_pace_stays_ready(prime):
    c, tuner, _ = prime
    await prime_standby(c, tuner)
    await asyncio.sleep(0.1)
    await verify(c)
    assert c.hidden_ready


async def test_a_prime_standby_that_resumed_behind_live_is_dropped(prime):
    """Playing, "PrimeVideo", same app: only the wall-clock position shows it's 40 s behind."""
    c, tuner, _ = prime
    b = await prime_standby(c, tuner)
    b.seek_back(40)
    await verify(c)
    assert c.standby == (None, None)


async def test_a_paused_prime_standby_is_dropped_at_once(prime):
    c, tuner, _ = prime
    b = await prime_standby(c, tuner)
    b.pause()
    await verify(c)
    assert c.standby == (None, None)


async def test_one_missing_prime_session_is_read_again_before_dropping(prime):
    c, tuner, _ = prime
    b = await prime_standby(c, tuner)
    b.playing = "none"
    await verify(c)
    assert c.hidden_ready                                         # one read: not believed yet
    assert c._verified_at > time.time() - c._verify_every()       # ...but re-checked soon
    await verify(c)
    assert c.standby == (None, None)                              # twice: gone


async def test_a_missing_prime_session_that_comes_back_resets(prime):
    c, tuner, _ = prime
    b = await prime_standby(c, tuner)
    b.playing = "none"
    await verify(c)
    b.playing = "playing"
    await verify(c)
    b.playing = "none"
    await verify(c)
    assert c.hidden_ready                                         # two misses, not in a row


async def test_a_failed_read_never_drops_a_standby(prime):
    c, tuner, _ = prime
    b = await prime_standby(c, tuner)
    b.fail_reads = 5
    for _ in range(3):
        await verify(c)
    assert c.hidden_ready


async def test_a_youtube_tv_standby_with_no_session_is_still_dropped_at_once(two):
    c, _ = two
    c.on_target = "youtubetv:fox"
    assert c.preload("youtubetv:cbs")
    await c._preload_task
    c.hidden.playing = "none"
    await verify(c)
    assert c.standby == (None, None)


# --- YouTube TV: the reload waits while the planner wants the stick ------------------------------

async def test_a_refresh_holds_off_while_the_planner_wants_another_game(two, monkeypatch):
    monkeypatch.setattr(ctl, "REFRESH_POLL_S", 0.01)
    c, tuner = two
    c.on_target = "youtubetv:fox"
    await c.execute("youtubetv:espn", may_cut=lambda: True)
    c._last_preload = time.time()                       # the planner's preload gap isn't over yet...
    assert not c.preload("youtubetv:cbs")               # ...so its pick is refused for now
    await refreshed(c)
    assert ("a:5555", "youtubetv:fox") not in tuner.calls   # no reload it would cancel mid-tune
    c._last_preload = 0
    assert c.preload("youtubetv:cbs")                   # the planner's next tick gets the stick
    await c._preload_task
    assert c.standby == ("youtubetv:cbs", None) and c.hidden_ready


async def test_a_wish_from_before_the_flip_doesnt_hold_the_refresh(two, monkeypatch):
    monkeypatch.setattr(ctl, "REFRESH_POLL_S", 0.01)
    c, tuner = two
    c.on_target = "youtubetv:fox"
    c._wish = ("youtubetv:cbs", time.time())            # asked before this switch
    await c.execute("youtubetv:espn", may_cut=lambda: True)
    await refreshed(c)
    assert tuner.calls[-1] == ("a:5555", "youtubetv:fox") and c.hidden_ready


async def test_a_wish_the_hidden_stick_cant_play_doesnt_hold_the_refresh(two, monkeypatch):
    monkeypatch.setattr(ctl, "REFRESH_POLL_S", 0.01)
    c, tuner = two
    c.on_target = "youtubetv:fox"
    await c.execute("youtubetv:espn", may_cut=lambda: True)
    c._failed["youtubetv:cbs"] = time.time() + 60       # CBS failed lately: can't be preloaded
    assert not c.preload("youtubetv:cbs")
    await refreshed(c)
    assert tuner.calls[-1] == ("a:5555", "youtubetv:fox") and c.hidden_ready
