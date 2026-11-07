"""tuners.prime_wake_nav (off by default): on a live game's Prime detail page the first CENTER only
wakes the nav; focus needs one move and a second CENTER starts it (Nick, POR@LAL, Nov 7). The fix
moves focus once, only on a screen read as the page itself, and never on the Watch Live panel, a
loading stream or a player in front (CENTER there pauses it)."""
import pytest

from fake_adb import media
from redzone import control as ctl
from redzone.channels import ChannelMap
from redzone.tuners.tuners import PrimeTuner, TuneResult
from test_tuners import PRIME, ScriptedStick, quick

PAGE = "Portland at Los Angeles | Watch | + | Watchlist | More purchase options | Included with Prime"
PANEL = "Watch Live | Resume"
DOWN, CENTER = "KEYCODE_DPAD_DOWN", "KEYCODE_DPAD_CENTER"


class PageStick(ScriptedStick):
    """The detail page as Nick found it: CENTER #1 wakes the nav, a focus move lands on Watch, and
    only a CENTER after the move starts the stream."""

    def __init__(self, ocr=PAGE, starts_on_first=False, loads_after_first=False):
        super().__init__(after=[media(PRIME, 1)], screen="", on_key=self._on)
        self.ocr, self.moved, self.centers = ocr, False, 0
        self.starts_on_first, self.loads_after_first = starts_on_first, loads_after_first
        self.page = True

    def _on(self, stick, key):
        if key == CENTER:
            self.centers += 1
            if (self.centers == 1 and self.starts_on_first) or (self.moved and self.page):
                self.page = False
                self.after = [media(PRIME, 6), media(PRIME, 3, 50, 6000)]
            elif self.centers == 1 and self.loads_after_first:
                self.after = [media(PRIME, 6)]
        elif key.startswith("KEYCODE_DPAD_"):
            self.moved = True

    async def ocr_text(self):
        return self.ocr


@pytest.fixture
def on_page(monkeypatch):
    async def on_detail(self, stick):
        return getattr(stick, "page", False)
    monkeypatch.setattr(PrimeTuner, "_on_detail_page", on_detail)


def prime(**kw):
    return quick(PrimeTuner, page_wait_s=0.01, WAKE_SETTLE_S=0.01, confirm_timeout=0.2, **kw)


async def test_off_by_default_and_the_bug_it_fixes(on_page):
    assert PrimeTuner.wake_nav_fix is False
    st = PageStick()
    res = await prime().tune(st, "GTI", "prime:GTI")
    # Old path: two blind CENTERs per link, no move: the game never starts ("playback not confirmed").
    assert not res.ok and DOWN not in st.keys()


async def test_on_moves_focus_once_then_starts_it(on_page):
    st = PageStick()
    res = await prime(wake_nav_fix=True).tune(st, "GTI", "prime:GTI")
    assert res.ok and len(st.links()) == 1
    assert st.keys() == [CENTER, DOWN, CENTER]
    assert "pressed: watch (after the focus move)" in res.steps


async def test_on_a_first_press_that_starts_it_sends_nothing_more(on_page):
    st = PageStick(starts_on_first=True)
    res = await prime(wake_nav_fix=True).tune(st, "GTI", "prime:GTI")
    assert res.ok and st.keys() == [CENTER]


async def test_never_moves_on_the_watch_live_panel(on_page):
    st = PageStick(ocr=PANEL)
    res = await prime(wake_nav_fix=True).tune(st, "GTI", "prime:GTI")
    assert DOWN not in st.keys()   # a move there puts focus on Resume


async def test_never_moves_on_an_unreadable_screen(on_page):
    st = PageStick(ocr="")
    res = await prime(wake_nav_fix=True).tune(st, "GTI", "prime:GTI")
    assert not res.ok and DOWN not in st.keys()
    assert any("not read as the detail page" in s for s in res.steps)


async def test_never_moves_or_presses_once_the_stream_is_loading(on_page):
    st = PageStick(loads_after_first=True)
    res = await prime(wake_nav_fix=True).tune(st, "GTI", "prime:GTI")
    assert DOWN not in st.keys() and st.keys().count(CENTER) == 1


async def test_never_moves_when_the_player_is_in_front(monkeypatch):
    async def off_page(self, stick):
        return False
    monkeypatch.setattr(PrimeTuner, "_on_detail_page", off_page)
    st = PageStick()
    res = await prime(wake_nav_fix=True).tune(st, "GTI", "prime:GTI")
    assert not res.ok and st.keys() == []


async def test_a_key_outside_the_four_directions_is_refused(on_page):
    st = PageStick()
    res = await prime(wake_nav_fix=True, wake_move_key="KEYCODE_BACK").tune(st, "GTI", "prime:GTI")
    assert "KEYCODE_BACK" not in st.keys() and not res.ok


async def test_a_closed_cut_window_stops_before_the_move(on_page):
    st = PageStick()
    allowed = {"n": 0}

    def may_cut():
        allowed["n"] += 1
        return DOWN not in st.keys() and st.centers == 0   # closes right after the first press
    res = await prime(wake_nav_fix=True).tune(st, "GTI", "prime:GTI", may_cut=may_cut)
    assert not res.ok and DOWN not in st.keys() and "window closed" in res.error


async def test_controller_sets_the_flag_from_config(cfg, tmp_path, monkeypatch):
    seen = {}

    class Spy(PrimeTuner):
        async def tune(self, stick, ref, target, **kw):
            seen.update(fix=self.wake_nav_fix, key=self.wake_move_key)
            return TuneResult(False, target, 0.0, [], "spy")
    monkeypatch.setitem(ctl.TUNERS, "prime", Spy())
    c = ctl.Controller(cfg, ChannelMap(tmp_path / "ch.json"))
    st = ScriptedStick()
    await c._tune(st, "prime:GTI")
    assert seen == {"fix": False, "key": "KEYCODE_DPAD_DOWN"}
    cfg.update({"tuners": {"prime_wake_nav": True, "prime_wake_move_key": "KEYCODE_DPAD_RIGHT"}})
    await c._tune(st, "prime:GTI")
    assert seen == {"fix": True, "key": "KEYCODE_DPAD_RIGHT"}
