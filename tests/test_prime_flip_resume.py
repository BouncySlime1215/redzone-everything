"""Prime after a HOME flip (hardware soak, Nov 2): REORDER_TO_FRONT lands on the game's detail page
with no media session, and it never plays again by itself. The resume presses through Watch ->
Watch Live (~5 s) instead of waiting it out and re-linking (~50 s of a static page on screen)."""
import asyncio

from redzone import control as ctl
from redzone.channels import ChannelMap
from redzone.tuners import tuners as tn
from redzone.tuners.adb import RunResult, Stick

T = "prime:amzn1.dv.gti.test"


class PrimeStick(Stick):
    """Prime on a stick after HOME + reorder: 'page' (detail page), 'panel' (Watch Live / Resume),
    'loading' -> 'playing'. CENTER moves page -> panel -> loading."""

    def __init__(self, screen="page", plays_itself_after=None):
        super().__init__("p:5555", os="Fire OS 8")
        self.state = "connected"
        self.packages = {"prime": "com.amazon.firebat"}
        self.screen = screen
        self.keys, self.ocr_reads = [], 0
        self.updated = 1000
        self.loading_reads = 0
        self.plays_itself_after = plays_itself_after   # reads until the player comes back by itself
        self.reads = 0

    async def _shell(self, command, timeout=8):
        return RunResult(True, "", 0.0)

    async def key(self, *codes):
        self.keys.extend(codes)
        if "KEYCODE_DPAD_CENTER" in codes:
            if self.screen == "page":
                self.screen = "panel"
            elif self.screen == "panel":
                self.screen = "loading"
            elif self.screen == "playing":
                self.screen = "paused"    # CENTER in a player pauses it: must never happen
        return RunResult(True, "", 0.0)

    async def ocr_text(self):
        self.ocr_reads += 1
        return {"page": "League Pass | Watch | More purchase options | Watchlist | Included with",
                "panel": "Watch Live | Warriors Broadcast | Resume | Watch from beginning | Watchlist"}.get(
            self.screen, "")

    async def playback(self, package=None):
        self.reads += 1
        if self.plays_itself_after is not None and self.reads >= self.plays_itself_after:
            self.screen = "playing"
        if self.screen == "loading":
            self.loading_reads += 1
            if self.loading_reads > 2:
                self.screen = "playing"
            else:
                return {"pkg": package, "state": "buffering", "position": 0, "updated": self.updated,
                        "read_ok": True}
        self.updated += 250
        if self.screen == "playing":
            return {"pkg": package, "state": "playing", "speed": 1.0, "position": self.updated,
                    "updated": self.updated, "read_ok": True, "title": "PrimeVideo"}
        if self.screen == "paused":
            return {"pkg": package, "state": "paused", "speed": 0.0, "position": self.updated,
                    "updated": self.updated, "read_ok": True}
        return {"pkg": None, "state": "none", "position": None, "updated": None, "read_ok": True}


def controller(cfg, tmp_path, monkeypatch, stick):
    cfg.update({"mode": {"dry_run": False}})
    c = ctl.Controller(cfg, ChannelMap(tmp_path / "ch.json"))
    c.sticks = [stick]
    c.hdmi = {stick.host: "HDMI2"}
    monkeypatch.setattr(ctl, "RESUME_POLL_S", 0.0)
    monkeypatch.setattr(ctl, "PRIME_PAGE_AFTER_S", 0.0)
    monkeypatch.setattr(tn.PrimeTuner, "PANEL_WAIT_S", 0.5)
    monkeypatch.setattr(tn.PrimeTuner, "PANEL_BLIND_S", 0.0)
    real_sleep = asyncio.sleep
    monkeypatch.setattr(tn.asyncio, "sleep", lambda s: real_sleep(0))

    class NoRelink:
        calls = 0

        async def tune(self, *a, **k):
            NoRelink.calls += 1
            return tn.TuneResult(False, T, 0.0, [], "re-link should not be needed")
    prime = tn.PrimeTuner()
    prime.tune = NoRelink().tune
    monkeypatch.setitem(ctl.TUNERS, "prime", prime)
    return c, NoRelink


def test_detail_page_after_flip_is_pressed_through_not_relinked(cfg, tmp_path, monkeypatch):
    stick = PrimeStick("page")
    c, relink = controller(cfg, tmp_path, monkeypatch, stick)
    how = asyncio.run(c._resume_playback(stick, T, ctl.RESUME_WAIT_S))
    assert how == "watch_live"
    assert stick.keys == ["KEYCODE_DPAD_CENTER", "KEYCODE_DPAD_CENTER"]   # Watch, then Watch Live
    assert relink.calls == 0
    assert stick.tuned["target"] == T


def test_panel_already_open_gets_one_press(cfg, tmp_path, monkeypatch):
    stick = PrimeStick("panel")
    c, relink = controller(cfg, tmp_path, monkeypatch, stick)
    assert asyncio.run(c._resume_playback(stick, T, ctl.RESUME_WAIT_S)) == "watch_live"
    assert stick.keys == ["KEYCODE_DPAD_CENTER"]
    assert relink.calls == 0


def test_player_that_comes_back_by_itself_is_never_pressed(cfg, tmp_path, monkeypatch):
    stick = PrimeStick("playing")
    c, relink = controller(cfg, tmp_path, monkeypatch, stick)
    assert asyncio.run(c._resume_playback(stick, T, ctl.RESUME_WAIT_S)) == "auto"
    assert stick.keys == [] and stick.ocr_reads == 0 and relink.calls == 0


def test_unknown_screen_is_not_pressed_and_falls_back_to_relink(cfg, tmp_path, monkeypatch):
    stick = PrimeStick("home")             # neither the page nor the panel: never press blind
    c, relink = controller(cfg, tmp_path, monkeypatch, stick)
    monkeypatch.setattr(ctl, "PRIME_RESUME_WAIT_S", 0.05)
    monkeypatch.setattr(ctl, "RESUME_PLAY_WAIT_S", 0.0)
    assert asyncio.run(c._resume_playback(stick, T, 0.0)) is None
    assert "KEYCODE_DPAD_CENTER" not in stick.keys
    assert relink.calls == 1


def test_press_through_refuses_while_a_stream_is_paused(monkeypatch):
    stick = PrimeStick("paused")
    steps = []
    assert not asyncio.run(tn.PrimeTuner().resume_from_page(stick, "com.amazon.firebat", steps))
    assert stick.keys == [] and not steps


class SlowCecStick(Stick):
    """The TV follows the SECOND HOME, ~3 s after it (the first is swallowed by Prime's player)."""

    def __init__(self):
        super().__init__("s:5555", os="Fire OS 8")
        self.state = "connected"
        self.packages = {"prime": "com.amazon.firebat"}
        self.homes = []

    async def _shell(self, command, timeout=8):
        now = ctl.time.monotonic()
        if "KEYCODE_HOME" in command:
            self.homes.append(now)
        if "mIsActiveSource" in command:
            on = len(self.homes) >= 2 and now - self.homes[1] >= 3.0
            return RunResult(True, "1" if on else "0", 0.0)
        if "mResumedActivity" in command:
            return RunResult(True, "mResumedActivity: ActivityRecord{1 u0 com.amazon.firebat/com.amazon.pyrocore.IgnitionActivity t1}", 0.0)
        return RunResult(True, "", 0.0)


def test_second_prime_home_gets_a_full_window(cfg, tmp_path, monkeypatch):
    cfg.update({"mode": {"dry_run": False}})
    c = ctl.Controller(cfg, ChannelMap(tmp_path / "ch.json"))
    stick = SlowCecStick()
    c.sticks = [stick]
    c.hdmi = {stick.host: "HDMI2"}
    clock = {"t": 0.0}
    monkeypatch.setattr(ctl.time, "monotonic", lambda: clock["t"])

    async def fake_sleep(s):
        clock["t"] += max(s, 0.1)
    monkeypatch.setattr(ctl.asyncio, "sleep", fake_sleep)
    assert asyncio.run(c._home_flip_prime(stick, "com.amazon.firebat", T))
    assert len(stick.homes) == 2
    assert c._flip_s is not None and c._flip_s >= 5.5   # counted from the first HOME


class NotExportedStick(Stick):
    """4K Plus: Prime's player is the non-exported PlaybackActivity, so the reorder is refused."""

    def __init__(self):
        super().__init__("n:5555", os="Fire OS 8")
        self.state = "connected"
        self.packages = {"prime": "com.amazon.firebat"}
        self.commands = []

    async def _shell(self, command, timeout=8):
        self.commands.append(command)
        if "mIsActiveSource" in command:
            return RunResult(True, "1", 0.0)
        if "mResumedActivity" in command:
            return RunResult(True, "mResumedActivity: ActivityRecord{1 u0 com.amazon.firebat/"
                                   "com.amazon.firebatcore.playback.inappplayback.PlaybackActivity t1}", 0.0)
        if command.startswith("am start -f 0x00020000"):
            return RunResult(True, "java.lang.SecurityException: Permission Denial: starting Intent "
                                   "... not exported from uid 10142", 0.0)
        return RunResult(True, "", 0.0)


def test_refused_prime_reorder_reopens_the_game_page(cfg, tmp_path, monkeypatch):
    cfg.update({"mode": {"dry_run": False}})
    c = ctl.Controller(cfg, ChannelMap(tmp_path / "ch.json"))
    stick = NotExportedStick()
    c.sticks = [stick]
    c.hdmi = {stick.host: "HDMI2"}
    monkeypatch.setattr(ctl, "RELAUNCH_AFTER_S", 0.0)
    assert asyncio.run(c._home_flip_prime(stick, "com.amazon.firebat", T))
    links = [x for x in stick.commands if "android.intent.action.VIEW" in x]
    assert len(links) == 1 and "detail?gti=amzn1.dv.gti.test" in links[0]


def test_page_is_recognised_when_ocr_splits_the_button_row(cfg, tmp_path, monkeypatch):
    stick = PrimeStick("page")
    stick.ocr_text = _ocr_split
    c, relink = controller(cfg, tmp_path, monkeypatch, stick)
    assert asyncio.run(c._resume_playback(stick, T, ctl.RESUME_WAIT_S)) == "watch_live"
    assert relink.calls == 0


async def _ocr_split():
    return "League Pass | Heat vs. Lakers | Watch | More purchase | options | +"
