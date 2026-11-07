"""[YTTV key plays, item 8] redzone/tuners/yttv_keyplays: flag off, hidden-only keys, Ask later only, fail closed.
Offline: a fake YouTube TV panel (UP+CENTER opens Scores/Stats/Key plays) and a reader that renders it as OCR rows."""
import asyncio
import json
import os

import pytest

from redzone import screen_ocr
from redzone.tuners import yttv_keyplays as kp
from redzone.tuners.adb import RunResult

YT = "com.amazon.firetv.youtube.tv"
TABS = ["Scores", "Stats", "Key plays"]


class Cfg(dict):
    def get(self, k, d=None):
        return super().get(k, d)


def cfg(**kw):
    c = {"yttv_keyplays.enabled": True, "yttv_keyplays.open_wait_s": 0.0, "yttv_keyplays.reopen_wait_s": 0.0,
         "yttv_keyplays.read_wait_s": 0.0, "yttv_keyplays.poll_s": 0.0, "yttv_keyplays.settle_s": 0.05,
         "yttv_keyplays.key_gap_s": 0.0, "yttv_keyplays.relink_settle_s": 0.05}
    c.update({f"yttv_keyplays.{k}": v for k, v in kw.items()})
    return Cfg(c)


PLAYS = [(4, "2:31", "Curry 3-pt jumper"), (4, "4:10", "Butler dunk"), (4, "6:02", "Adebayo block"),
         (3, "0:45", "Herro 3-pt"), (3, "3:12", "Green steal"), (3, "7:40", "Kuminga dunk"),
         (2, "1:05", "Curry layup"), (2, "5:55", "Butler and-one")]


class FakePanel:
    """Player -> (UP, CENTER) -> panel. Focus walks tab bar -> catch-up button -> rows. Selecting a row the first time
    raises the auto-play prompt (buttons stacked vertically). `setting_changed` must never become True."""

    def __init__(self, plays=PLAYS, banner=0, prompt_buttons=("Turn on", "Ask later"), first_use=True,
                 front=YT, window=5, kp_mode="rows", tab_focus=True, open_on_card=False, hidden_n=8):
        self.plays, self.banner, self.front, self.window = plays, banner, front, window
        self.prompt_buttons, self.first_use = list(prompt_buttons), first_use
        self.state = "player"     # player | up | panel | prompt | reel
        self.tab, self.pos = 0, "tab"   # pos: "tab" | "catch" | int row
        self.pfocus, self.top = 0, 0
        self.setting_changed = False
        self.reel_at = None
        self.keys, self.relinks = [], 0
        self.focus_visible = True
        self.card_selected = False
        self.kp_mode, self.tab_focus, self.open_on_card, self.hidden_n = kp_mode, tab_focus, open_on_card, hidden_n

    async def key(self, *codes):
        for c in codes:
            self._press(c.replace("KEYCODE_", ""))
        return RunResult(True, "", 0.01)

    def _press(self, k):
        self.keys.append(k)
        if self.banner > 0 and self.state in ("player", "up"):
            self.banner -= 1
            return
        if self.state == "player":
            if k == "DPAD_UP":
                self.state = "up"
        elif self.state == "up":
            if k == "DPAD_CENTER":
                self.state, self.tab, self.pos = "panel", 0, ("card" if self.open_on_card else "tab")
        elif self.state == "panel":
            if self.pos == "card":
                if k == "DPAD_UP":
                    self.pos = "tab"
                elif k == "DPAD_CENTER":
                    self.card_selected = True
            elif self.pos == "tab":
                if k == "DPAD_RIGHT":
                    self.tab = min(2, self.tab + 1)
                elif k == "DPAD_LEFT":
                    self.tab = max(0, self.tab - 1)
                elif k == "DPAD_DOWN" and self.tab == 2 and self.kp_mode in ("rows", "hidden"):
                    self.pos = "catch"
            elif self.pos == "catch":
                if k == "DPAD_DOWN" and self.kp_mode == "rows":
                    self.pos = 0
                elif k == "DPAD_UP":
                    self.pos = "tab"
                elif k == "DPAD_CENTER":
                    self._select(0)
            elif isinstance(self.pos, int):
                if k == "DPAD_DOWN":
                    self.pos = min(len(self.plays) - 1, self.pos + 1)
                elif k == "DPAD_UP":
                    self.pos = self.pos - 1 if self.pos > 0 else "catch"
                elif k == "DPAD_CENTER":
                    self._select(self.pos)
                if isinstance(self.pos, int):
                    if self.pos >= self.top + self.window:
                        self.top = self.pos - self.window + 1
                    if self.pos < self.top:
                        self.top = self.pos
        elif self.state == "prompt":
            if k in ("DPAD_DOWN", "DPAD_RIGHT"):
                self.pfocus = min(len(self.prompt_buttons) - 1, self.pfocus + 1)
            elif k in ("DPAD_UP", "DPAD_LEFT"):
                self.pfocus = max(0, self.pfocus - 1)
            elif k == "DPAD_CENTER":
                b = self.prompt_buttons[self.pfocus].lower()
                if b != "ask later":
                    self.setting_changed = True
                self.state = "reel"

    def _select(self, i):
        self.reel_at = i
        if self.first_use:
            self.first_use, self.state, self.pfocus = False, "prompt", 0
        else:
            self.state = "reel"

    async def foreground(self):
        return f"{self.front}/.MainActivity"

    async def playback(self, package=None):
        return {"state": "playing", "pkg": YT}

    def render(self):
        obs, focus = [], []
        if self.state in ("panel",):
            for i, t in enumerate(TABS):
                obs.append({"text": t, "x": .1 + .15 * i, "y": .15, "w": .1, "h": .03})
            if self.pos == "tab" and self.tab_focus:
                focus.append({"text": TABS[self.tab], "x": .1 + .15 * self.tab - .01, "y": .14, "w": .12, "h": .05})
            if self.pos == "card":
                focus.append({"text": "MIA @ GSW", "x": .1, "y": .5, "w": .3, "h": .2})
            if self.tab == 0:
                # the Scores tab carries game clocks too: they must never be read as key plays
                obs.append({"text": "Q4 2:31 MIA 98 GSW 101", "x": .1, "y": .3, "w": .3, "h": .03})
                obs.append({"text": "MIA @ GSW", "x": .1, "y": .5, "w": .3, "h": .03})
            if self.tab == 2 and self.kp_mode == "unavailable":
                obs.append({"text": "Key plays currently unavailable", "x": .1, "y": .3, "w": .4, "h": .03})
                obs.append({"text": "Try again later", "x": .1, "y": .35, "w": .3, "h": .03})
            if self.tab == 2 and self.kp_mode == "hidden":
                obs.append({"text": "Catch up via key plays", "x": .1, "y": .22, "w": .3, "h": .03})
                obs.append({"text": f"{self.hidden_n} hidden key plays", "x": .1, "y": .3, "w": .3, "h": .03})
                if self.pos == "catch":
                    focus.append({"text": "Catch up via key plays", "x": .09, "y": .21, "w": .32, "h": .05})
            if self.tab == 2 and self.kp_mode == "rows":
                obs.append({"text": "Catch up via key plays", "x": .1, "y": .22, "w": .3, "h": .03})
                if self.pos == "catch":
                    focus.append({"text": "Catch up via key plays", "x": .09, "y": .21, "w": .32, "h": .05})
                for j, i in enumerate(range(self.top, min(len(self.plays), self.top + self.window))):
                    p, c, d = self.plays[i]
                    y = .30 + .1 * j
                    obs.append({"text": f"Q{p} {c}", "x": .1, "y": y, "w": .1, "h": .03})
                    obs.append({"text": d, "x": .1, "y": y + .035, "w": .3, "h": .03})
                    if self.pos == i:
                        focus.append({"text": f"Q{p} {c}\n{d}", "x": .09, "y": y - .01, "w": .4, "h": .08})
        elif self.state == "prompt":
            obs.append({"text": "Auto-play key plays?", "x": .35, "y": .35, "w": .3, "h": .04})
            for i, b in enumerate(self.prompt_buttons):
                obs.append({"text": b, "x": .42, "y": .5 + .07 * i, "w": .16, "h": .04})
            b = self.prompt_buttons[self.pfocus]
            focus.append({"text": b, "x": .41, "y": .49 + .07 * self.pfocus, "w": .18, "h": .06})
        elif self.state == "reel":
            n = self.hidden_n if self.kp_mode == "hidden" else len(self.plays)
            obs.append({"text": f"{self.reel_at + 1} of {n} key plays", "x": .05, "y": .85,
                        "w": .2, "h": .03})
        if not self.focus_visible:
            focus = []
        return obs, focus


def reader(stick):
    async def read(_s):
        obs, focus = stick.render()
        return kp.parse_panel(obs, focus)
    return read


def relinker(stick):
    async def relink():
        stick.relinks += 1
        stick.state, stick.pos, stick.top = "player", "tab", 0
        return True
    return relink


def go(coro):
    return asyncio.run(coro)


# --- parsing ---------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text,want", [("Q4 2:31", (4, 151.0)), ("2:31 4th", (4, 151.0)), ("3rd Qtr 0:45", (3, 45.0)),
                                       ("OT 1:02", (5, 62.0)), ("OT2 0:10", (6, 10.0)), ("2H 11:20", (2, 680.0)),
                                       ("9:00 PM - 11:00 PM", (None, None)), ("Butler dunk", (None, None))])
def test_parse_clock(text, want):
    assert kp.parse_clock(text) == want


def test_match_play_nearest_same_period_and_tie_is_none():
    rows = [kp.KeyPlay(0, 4, 151.0, "Q4 2:31", ""), kp.KeyPlay(1, 3, 150.0, "Q3 2:30", ""),
            kp.KeyPlay(2, 4, 171.0, "Q4 2:51", "")]
    assert kp.match_play(rows, 4, 155.0).index == 0
    assert kp.match_play(rows, 4, 161.0) is None           # 151 and 171 are both 10 s away
    assert kp.match_play(rows, 2, 151.0) is None


def test_panel_rows_only_under_tabs_and_with_description():
    s = FakePanel()
    s.state, s.tab = "panel", 2
    r = kp.parse_panel(*s.render())
    assert r.panel and r.on_key_plays and r.catch_up
    assert [(k.period, k.clock_s) for k in r.rows[:2]] == [(4, 151.0), (4, 250.0)]
    assert r.rows[0].text == "Curry 3-pt jumper"


# --- flag / preconditions ----------------------------------------------------------------------------------------
def test_flag_off_presses_nothing():
    s = FakePanel()
    res = go(kp.read_key_plays(s, cfg=Cfg(), read=reader(s), relink=relinker(s)))
    assert not res.ok and "off" in res.reason and s.keys == [] and s.relinks == 0


def test_not_hidden_presses_nothing():
    s = FakePanel()
    res = go(kp.catch_up(s, kp.Want(period=4, clock_s=151), cfg=cfg(), read=reader(s), relink=relinker(s),
                         still_hidden=lambda: False))
    assert not res.ok and s.keys == [] and s.relinks == 0


def test_wrong_app_presses_nothing():
    s = FakePanel(front="com.amazon.avod")
    res = go(kp.catch_up(s, kp.Want(index=0), cfg=cfg(), read=reader(s), relink=relinker(s)))
    assert not res.ok and "not in front" in res.reason and s.keys == []


# --- the runs --------------------------------------------------------------------------------------------------------
def test_read_list_then_live_never_selects():
    s = FakePanel()
    res = go(kp.read_key_plays(s, cfg=cfg(), read=reader(s), relink=relinker(s)))
    assert res.ok, res.steps
    assert len(res.rows) == 5 and res.rows[0]["clock_s"] == 151.0
    assert s.relinks == 1 and s.reel_at is None and res.relinked
    assert s.keys[:2] == ["DPAD_UP", "DPAD_CENTER"] and s.keys.count("DPAD_RIGHT") == 2


def test_catch_up_by_clock_answers_ask_later_only():
    s = FakePanel()
    res = go(kp.catch_up(s, kp.Want(period=3, clock_s=47), cfg=cfg(), read=reader(s), relink=relinker(s)))
    assert res.ok, res.steps
    assert s.reel_at == 3 and res.chosen["clock_s"] == 45.0
    assert res.prompt_seen and res.prompt_answer == "ask later" and not s.setting_changed
    assert res.reel == (4, 8) and res.playing and s.relinks == 0


def test_prompt_without_ask_later_fails_closed_without_choosing():
    s = FakePanel(prompt_buttons=("Turn on", "Not now"))
    res = go(kp.catch_up(s, kp.Want(index=0), cfg=cfg(), read=reader(s), relink=relinker(s)))
    assert not res.ok and "Ask later" in res.reason
    assert not s.setting_changed and s.relinks == 1 and res.prompt_answer is None


def test_scrolls_to_an_older_play():
    s = FakePanel()
    res = go(kp.catch_up(s, kp.Want(period=2, clock_s=355), cfg=cfg(), read=reader(s), relink=relinker(s)))
    assert res.ok, res.steps
    assert s.reel_at == 7 and not s.setting_changed


def test_missing_play_fails_closed_and_never_selects():
    s = FakePanel()
    res = go(kp.catch_up(s, kp.Want(period=1, clock_s=600), cfg=cfg(scroll_max=12), read=reader(s),
                         relink=relinker(s)))
    assert not res.ok and "not in the Key plays list" in res.reason
    assert s.reel_at is None and s.relinks == 1


def test_unreadable_focus_never_selects():
    s = FakePanel()
    s.focus_visible = False
    res = go(kp.catch_up(s, kp.Want(index=0), cfg=cfg(), read=reader(s), relink=relinker(s)))
    assert not res.ok and s.reel_at is None and s.relinks == 1


def test_banner_swallows_first_open_then_retries():
    s = FakePanel(banner=2)
    res = go(kp.read_key_plays(s, cfg=cfg(), read=reader(s), relink=relinker(s)))
    assert res.ok, res.steps
    assert s.keys[:4] == ["DPAD_UP", "DPAD_CENTER", "DPAD_UP", "DPAD_CENTER"]


def test_becoming_visible_stops_keys_and_does_not_relink():
    s = FakePanel()
    budget = {"n": 6}

    def hidden():
        budget["n"] -= 1
        return budget["n"] > 0
    res = go(kp.catch_up(s, kp.Want(index=4), cfg=cfg(), read=reader(s), relink=relinker(s), still_hidden=hidden))
    assert not res.ok and "not hidden" in res.reason
    assert s.relinks == 0 and s.reel_at is None


def test_return_to_live_times_the_deep_link():
    s = FakePanel()
    ok, secs = go(kp.return_to_live(s, relinker(s), cfg=cfg()))
    assert ok and secs is not None and secs < 1.0 and s.relinks == 1


# --- screen reads -------------------------------------------------------------------------------------------------------
def test_ocr_panel_deletes_the_frame(monkeypatch):
    seen = {}

    class S:
        async def screenshot(self, path):
            with open(path, "wb") as f:
                f.write(b"\x89PNG\r\n\x1a\n")
            seen["path"] = path
            seen["mode"] = oct(os.stat(os.path.dirname(path)).st_mode & 0o777)
            return True

    def fake_recognize(path, timeout=8.0, focus_labels=None):
        seen["labels"] = focus_labels
        return screen_ocr.OCRResult("ok", (screen_ocr.TextBox("Key plays", .9, .3, .15, .1, .03),), 1920, 1080)
    monkeypatch.setattr(screen_ocr, "recognize", fake_recognize)
    r = go(kp.ocr_panel(S()))
    assert r is not None and not os.path.exists(seen["path"]) and not os.path.exists(os.path.dirname(seen["path"]))
    assert seen["mode"] == "0o700" and seen["labels"] == kp.FOCUS_LABELS


def _raw(button_text):
    return json.dumps({"status": "ok", "coordinate_system": "normalized_top_left", "width": 1920, "height": 1080,
                       "observations": [{"text": button_text, "confidence": .9,
                                         "box": {"x": .42, "y": .5, "width": .16, "height": .04}}],
                       "focus_candidates": [{"x": .41, "y": .49, "width": .18, "height": .06, "confidence": .8,
                                             "source": "neutral_filled_button"}]})


def test_screen_ocr_focus_labels_is_opt_in():
    assert screen_ocr.parse_result(_raw("Ask later")).focus_status == "none"      # unchanged default
    r = screen_ocr.parse_result(_raw("Ask later"), kp.FOCUS_LABELS)
    assert r.focus_status == "unique" and r.focus_boxes[0].text == "Ask later"


# --- live layouts (Nov 7): tabs focus invisible, card focused on open, unavailable / spoiler mode ---------------------
LIVE = dict(tab_focus=False, open_on_card=True)


def test_live_layout_rows_reaches_tab_by_content_and_picks_row():
    s = FakePanel(**LIVE)
    res = go(kp.catch_up(s, kp.Want(period=4, clock_s=250), cfg=cfg(), read=reader(s), relink=relinker(s)))
    assert res.ok, res.steps
    assert res.mode == "rows" and s.reel_at == 1 and not s.card_selected and not s.setting_changed


def test_scores_tab_clocks_are_not_key_plays():
    s = FakePanel(**LIVE)
    s.state, s.pos, s.tab = "panel", "tab", 0
    r = kp.parse_panel(*s.render())
    assert r.panel and r.mode == "" and not r.on_key_plays


def test_unavailable_fails_closed_and_selects_nothing():
    s = FakePanel(kp_mode="unavailable", **LIVE)
    res = go(kp.read_key_plays(s, cfg=cfg(), read=reader(s), relink=relinker(s)))
    assert not res.ok and res.mode == "unavailable" and "unavailable" in res.reason
    assert s.relinks == 1 and s.reel_at is None and "DPAD_CENTER" not in s.keys[2:]


def test_spoiler_mode_read_reports_hidden_count():
    s = FakePanel(kp_mode="hidden", **LIVE)
    res = go(kp.read_key_plays(s, cfg=cfg(), read=reader(s), relink=relinker(s)))
    assert res.ok and res.mode == "hidden" and res.hidden_n == 8 and res.rows == [] and s.relinks == 1


def test_spoiler_mode_refuses_a_row_pick():
    s = FakePanel(kp_mode="hidden", **LIVE)
    res = go(kp.catch_up(s, kp.Want(period=4, clock_s=151), cfg=cfg(), read=reader(s), relink=relinker(s)))
    assert not res.ok and "spoiler" in res.reason and s.reel_at is None and s.relinks == 1


def test_spoiler_mode_catch_up_from_start():
    s = FakePanel(kp_mode="hidden", **LIVE)
    res = go(kp.catch_up(s, kp.Want(from_start=True), cfg=cfg(), read=reader(s), relink=relinker(s)))
    assert res.ok, res.steps
    assert s.reel_at == 0 and res.reel == (1, 8) and res.prompt_answer == "ask later" and not s.setting_changed


def test_catch_up_needs_visible_focus_on_the_button():
    s = FakePanel(kp_mode="hidden", **LIVE)
    s.focus_visible = False
    res = go(kp.catch_up(s, kp.Want(from_start=True), cfg=cfg(), read=reader(s), relink=relinker(s)))
    assert not res.ok and s.reel_at is None and s.relinks == 1
