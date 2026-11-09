"""Prime confirm without OCR in the poll loops, the purchase guard, the ESPN link form, the warm flag.

Why: a blind CENTER on a Prime purchase screen can spend real money, and OCR inside "playing yet?" loops is the suspected extra 20 s on a warm Prime tune.

Fake stick only: no TV, no adb.
"""
import asyncio

import pytest

from redzone.tuners import tuners as tn
from redzone.tuners.adb import RunResult, Stick

T = "prime:amzn1.dv.gti.test"
PAGE = "League Pass | Heat vs. Lakers | Watch | More purchase options | Watchlist | Included with Prime"
PANEL = "Watch Live | Heat vs. Lakers | Resume | Watch from beginning"
DETAIL = "ActivityRecord{aaa u0 com.amazon.firebat/com.amazon.pyrocumulus.DetailActivity}"
PLAYER = "ActivityRecord{new u0 com.amazon.firebat/com.amazon.ignition.IgnitionActivity}"
OLD_PLAYER = "ActivityRecord{old u0 com.amazon.firebat/com.amazon.ignition.IgnitionActivity}"
BUY_SCREENS = [
    "Heat vs. Lakers | Rent HD | Watchlist",
    "Heat vs. Lakers | Buy HD | Watchlist",
    "Watch | $4.99 | Heat vs. Lakers | Watchlist",
    "Subscribe to NBA League Pass | Watch trailer | Watchlist",
    "Start your free trial | Watchlist | Heat vs. Lakers",
    "Watch Live | Rent HD | More purchase options | Watchlist",
]


class FakeStick(Stick):
    """Prime on a stick. CENTER walks `path` one screen on; `texts` says what a read of each screen returns."""

    def __init__(self, path=("page", "panel", "player"), texts=None, ui=None, activity=None, gap_reads=0):
        super().__init__("p:5555", os="Fire OS 8")
        self.state = "connected"
        self.packages = {"prime": "com.amazon.firebat", "espn": "com.espn.gtv", "youtubetv": "com.yt"}
        self.path, self.at = list(path), 0
        self.texts = {"page": PAGE, "panel": PANEL, "player": "DAL 14 NYG 7 2ND 5:03", **(texts or {})}
        self.ui = ui or {}                 # uiautomator's text per screen: blank unless a test says so
        self.activity = {"page": DETAIL, "panel": DETAIL, "player": PLAYER, **(activity or {})}
        self.gap_reads = gap_reads         # polls of "buffering" right after the press that lands in the player
        self.calls, self.keys = [], []
        self.ocr_reads = self.ui_reads = 0
        self.updated = 1000
        self.pid = "123"
        self.mark_awake()

    @property
    def screen(self):
        return self.path[self.at]

    async def _shell(self, cmd, timeout=8):
        self.calls.append(cmd.split("|")[0].strip()[:30])
        if "mResumedActivity" in cmd:
            return RunResult(True, f"mResumedActivity: {self.activity[self.screen]}", 0.0)
        if cmd.startswith("pidof"):
            return RunResult(True, self.pid, 0.0)
        return RunResult(True, "Status: ok", 0.0)

    async def screen_text(self):
        self.ui_reads += 1
        self.calls.append("screen_text")
        return self.ui.get(self.screen, "")

    async def ocr_text(self):
        self.ocr_reads += 1
        self.calls.append("ocr")
        return self.texts.get(self.screen, "")

    async def key(self, *codes):
        self.keys.extend(codes)
        self.calls.append("key")
        if self.at < len(self.path) - 1:
            self.at += 1
        return RunResult(True, "", 0.0)

    async def playback(self, package=None):
        self.calls.append("playback")
        if self.activity[self.screen] in (DETAIL,):
            return {"pkg": package, "state": "none", "read_ok": True}
        if self.at and self.gap_reads > 0:
            self.gap_reads -= 1
            return {"pkg": package, "state": "buffering", "position": 0, "updated": self.updated, "read_ok": True}
        self.updated += 250
        return {"pkg": package, "state": "playing", "position": self.updated, "updated": self.updated,
                "speed": 1.0, "read_ok": True}


def tuner(fast=False):
    t = tn.PrimeTuner()
    t.fast_confirm = fast
    t.page_wait_s = 0.0
    t.poll_s = 0.0
    t.confirm_timeout = 0.05
    return t


def tune(t, stick, may_cut=None):
    return asyncio.run(t.tune(stick, "amzn1.dv.gti.test", T, may_cut=may_cut))


# --- Real money first ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("fast", [False, True], ids=["flag_off", "flag_on"])
@pytest.mark.parametrize("words", BUY_SCREENS)
def test_a_purchase_or_rent_screen_never_receives_a_press(fast, words):
    stick = FakeStick(path=("page",), texts={"page": words}, ui={"page": words})
    res = tune(tuner(fast), stick)
    assert stick.keys == []
    assert res.ok is False


@pytest.mark.parametrize("fast", [False, True], ids=["flag_off", "flag_on"])
def test_a_purchase_screen_seen_only_by_the_player_ocr_never_receives_a_press(fast):
    """The page is inside a player activity, uiautomator is blank, and only the OCR shows the price."""
    words = "Watch | Rent HD $3.99 | Watchlist | Included with"
    stick = FakeStick(path=("page",), texts={"page": words}, activity={"page": OLD_PLAYER})
    tune(tuner(fast), stick)
    assert stick.keys == []


@pytest.mark.parametrize("words", BUY_SCREENS)
def test_a_purchase_screen_after_a_flip_never_receives_a_press(words):
    stick = FakeStick(path=("page",), texts={"page": words})
    stick.playback = _no_session
    assert asyncio.run(tuner().resume_from_page(stick, "com.amazon.firebat", [])) is False
    assert stick.keys == []


async def _no_session(package=None):
    return {"pkg": package, "state": "none", "read_ok": True}


def test_flag_on_an_unreadable_screen_is_never_pressed():
    stick = FakeStick(path=("page",), texts={"page": ""})
    tune(tuner(True), stick)
    assert stick.keys == []


def test_more_purchase_options_alone_is_not_a_purchase_screen():
    """That row is on every Prime detail page, including games the account already has."""
    assert not tn.PrimeTuner._buying(PAGE)
    assert not tn.PrimeTuner._buying("League Pass | Heat vs. Lakers | Watch | More purchase | options | +")
    assert tn.PrimeTuner._buying(PAGE + " | Rent HD")


# --- The flag ------------------------------------------------------------------------------------------------------

def test_flag_on_two_press_tune_reads_once_per_press():
    stick = FakeStick()
    res = tune(tuner(True), stick)
    assert res.ok is True
    assert len(stick.keys) == 2
    assert stick.ocr_reads == 2          # the exit check: at most 1 OCR per press
    assert stick.ui_reads == 0


def test_flag_on_no_ocr_in_the_poll_loops_or_the_last_wait():
    """Page inside the old player, one press, a stream that never confirms: polling must stay OCR-free."""
    stick = FakeStick(path=("page", "player"), activity={"page": OLD_PLAYER, "player": OLD_PLAYER},
                      texts={"page": "Watch | Watchlist | Included with"})
    t = tuner(True)
    t.page_wait_s = 0.02
    tune(t, stick)
    pressed = len(stick.keys)
    assert pressed >= 1
    assert stick.ocr_reads <= 2 * (pressed + 1)    # two deep-link attempts, each at most presses + 1 reads


def test_flag_on_a_buffering_gap_after_the_press_confirms_without_ocr():
    stick = FakeStick(path=("page", "player"), activity={"page": DETAIL, "player": OLD_PLAYER}, gap_reads=1,
                      texts={"page": "Watch | Watchlist | Included with"})
    t = tuner(True)
    t.page_wait_s = 0.02
    t._before_player[stick.host] = "old"
    t.tune_m[stick.host].to("linked", why="test")
    steps = tn.TimedSteps()
    watch = tn.NewStream({"state": "none"})
    assert asyncio.run(t._confirm(stick, "com.amazon.firebat", steps, watch)) is True
    assert stick.ocr_reads == 1 and len(stick.keys) == 1


def test_flag_on_a_blank_read_never_confirms_the_old_player():
    """With the page unreadable a blank OCR proves nothing: it must not confirm the game behind the page."""
    stick = FakeStick(path=("page",), activity={"page": OLD_PLAYER}, texts={"page": ""})
    t = tuner(True)
    t._before_player[stick.host] = "old"
    t.tune_m[stick.host].to("linked", why="test")
    t._link_at[stick.host] = 0.0
    before = {"pkg": "com.amazon.firebat", "state": "playing", "position": 1000, "updated": 1000, "speed": 1.0}
    watch = tn.NewStream(before)
    assert asyncio.run(t._confirm(stick, "com.amazon.firebat", tn.TimedSteps(), watch)) is False
    assert stick.keys == [] and stick.ocr_reads == 1


def test_flag_off_keeps_todays_calls():
    """Captured from the code before this change: a blank uiautomator read, then two focused-button presses."""
    stick = FakeStick()
    res = tune(tuner(False), stick)
    assert res.ok is True
    assert stick.calls == [
        "dumpsys activity activities", "playback", "am start -W -a android.intent.", "playback", "playback",
        "screen_text", "playback", "dumpsys activity activities", "key", "playback", "playback", "screen_text",
        "playback", "dumpsys activity activities", "key", "playback", "dumpsys activity activities",
    ]
    assert list(res.steps) == ["deep link #1: ok", "pressed: watch (focused button)", "pressed: watch live (panel)",
                               "playing"]


def test_flag_off_unreadable_page_still_presses_as_today():
    """Known risk, kept on purpose: flag off, a blank read gives no text to guard, and seeing it would be a new call."""
    stick = FakeStick()
    tune(tuner(False), stick)
    assert stick.ocr_reads == 0 and len(stick.keys) == 2


def test_apply_flags_sets_the_tuners():
    prime, espn, yt = tn.PrimeTuner(), tn.EspnTuner(), tn.YouTubeTVTuner()
    table = {"prime": prime, "espn": espn, "youtubetv": yt}
    tn.apply_flags({"tuners.prime_fast_confirm": True, "tuners.espn_link_form": False}, table)
    assert prime.fast_confirm is True and espn.link_form is False and yt.warm_probe is True
    tn.apply_flags({}, table)
    assert prime.fast_confirm is False and yt.warm_probe is False


# --- ESPN link form ------------------------------------------------------------------------------------------------

def test_espn_link_form_off_is_todays_url():
    assert tn.EspnTuner().url("abc123") == "https://www.espn.com/watch/player/_/id/abc123"


def test_espn_link_form_on_sends_the_sportscenter_link():
    t = tn.EspnTuner()
    t.link_form = True
    sent = []

    class Capture(FakeStick):
        async def _shell(self, cmd, timeout=8):
            sent.append(cmd)
            return RunResult(True, "Status: ok", 0.0)

    stick = Capture()
    asyncio.run(stick.view(t.url("abc123"), stick.packages["espn"]))
    assert sent == ["am start -a android.intent.action.VIEW "
                    "-d 'sportscenter://x-callback-url/showWatchStream?playID=abc123' com.espn.gtv"]


# --- Warm flag -----------------------------------------------------------------------------------------------------

class WarmStick(FakeStick):
    def __init__(self, before="none", **kw):
        super().__init__(**kw)
        self.before = before

    async def playback(self, package=None):
        self.calls.append("playback")
        if self.before == "paused":
            return {"pkg": package, "state": "paused", "position": 1, "updated": 1, "read_ok": True}
        return {"pkg": package, "state": "none", "read_ok": True}


def yt(probe):
    t = tn.YouTubeTVTuner()
    t.warm_probe = probe
    t.confirm_timeout = 0.02
    t.poll_s = 0.0
    return t


def warm_marks(res):
    return [m["step"] for m in res.steps.marks if m["step"].startswith("warm:")]


def test_warm_flag_off_makes_no_new_call_and_says_unknown():
    stick = WarmStick()
    res = asyncio.run(yt(False).tune(stick, "abc", "youtubetv:abc"))
    assert not any(c.startswith("pidof") for c in stick.calls)
    assert res.warm is None and warm_marks(res) == ["warm: unknown"]


def test_warm_from_a_session_before_the_link_needs_no_call():
    stick = WarmStick(before="paused")
    res = asyncio.run(yt(False).tune(stick, "abc", "youtubetv:abc"))
    assert res.warm is True and not any(c.startswith("pidof") for c in stick.calls)


@pytest.mark.parametrize(("pid", "want"), [("123", True), ("", False)])
def test_warm_probe_reads_the_process_before_the_link(pid, want):
    stick = WarmStick()
    stick.pid = pid
    res = asyncio.run(yt(True).tune(stick, "abc", "youtubetv:abc"))
    assert res.warm is want
    assert [c for c in stick.calls if c.startswith(("pidof", "am start"))][0].startswith("pidof")
    assert warm_marks(res) == [f"warm: {'yes' if want else 'no'}"]


def test_brought_to_the_front_means_it_was_warm():
    stick = WarmStick()

    async def view(url, pkg, wait=False, fresh=False):
        return RunResult(True, "Warning: Activity not started, its current task has been brought to the front", 0.0)
    stick.view = view
    res = asyncio.run(yt(False).tune(stick, "abc", "youtubetv:abc"))
    assert res.warm is True
