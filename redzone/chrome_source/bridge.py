"""The bridge to Chrome: run_js(tab, script) -> dict, open a tab, bring a tab to the front. Two of them.

AppleScriptBridge drives the real Google Chrome on the Mac through `osascript` (`tell application "Google Chrome"` ...
`execute <tab> javascript`). Chrome refuses page script from AppleScript until its owner switches on
View > Developer > Allow JavaScript from Apple Events; the bridge only detects that and says so. It never changes that
setting, any other Chrome setting or anything outside Chrome: its AppleScript opens a tab, selects a tab, raises a
Chrome window, closes a tab it opened, and runs a page script. Nothing else.

FakeBridge is a small player model for tests: tabs that play, pause and seek on a clock the test controls.

MEASURED on Nick's Mac, Nov 10: `set active tab index` and `set index of window` work; `execute javascript` answers
"Executing JavaScript through AppleScript is turned off" until the menu item is on. NOT measured: how long a tab switch
takes, and anything in this file run against the real browser (it was written in a cloud session with no Mac).

Why: the stream backend must not care whether it talks to Chrome or to a test, and the one thing that can stop it on
the Mac (the menu item) has to produce a plain instruction for Nick instead of a stack trace.
"""
from __future__ import annotations

import json
import subprocess
from typing import Callable, Protocol

from . import page_script

JS_OFF_TEXT = "Executing JavaScript through AppleScript is turned off"
JS_OFF_HELP = ("Chrome will not run page script from AppleScript yet. Nick: in Chrome's menu bar choose "
               "View > Developer > Allow JavaScript from Apple Events, then try again. "
               "RedZone never changes that setting itself.")
APP = "Google Chrome"

# Every AppleScript this module can send. Arguments arrive through `on run argv`, never pasted into the text.
AS_RUN_JS = f"""on run argv
set tabId to (item 1 of argv) as integer
set js to item 2 of argv
tell application "{APP}"
repeat with w in windows
repeat with t in tabs of w
if (id of t) = tabId then return (execute t javascript js)
end repeat
end repeat
end tell
error "RZ_TAB_NOT_FOUND"
end run"""
AS_OPEN = f"""on run argv
tell application "{APP}"
if (count of windows) = 0 then make new window
set t to make new tab at end of tabs of window 1 with properties {{URL:(item 1 of argv)}}
return id of t
end tell
end run"""
AS_ACTIVATE = f"""on run argv
set tabId to (item 1 of argv) as integer
tell application "{APP}"
repeat with w in windows
set i to 0
repeat with t in tabs of w
set i to i + 1
if (id of t) = tabId then
set active tab index of w to i
set index of w to 1
return i
end if
end repeat
end repeat
end tell
error "RZ_TAB_NOT_FOUND"
end run"""
AS_CLOSE = f"""on run argv
set tabId to (item 1 of argv) as integer
tell application "{APP}"
repeat with w in windows
repeat with t in tabs of w
if (id of t) = tabId then
close t
return 1
end if
end repeat
end repeat
end tell
return 0
end run"""
SCRIPTS = {"run_js": AS_RUN_JS, "open": AS_OPEN, "activate": AS_ACTIVATE, "close": AS_CLOSE}


class BridgeError(RuntimeError):
    """The bridge could not do what was asked (no such tab, no answer, an answer that is not the small JSON expected)."""


class JavaScriptFromAppleEventsOff(BridgeError):
    """Chrome's "Allow JavaScript from Apple Events" is off. Only Nick switches it on."""

    def __init__(self) -> None:
        super().__init__(JS_OFF_HELP)


class Bridge(Protocol):
    def open_tab(self, url: str) -> int: ...
    def run_js(self, tab: int, script: str) -> dict: ...
    def activate(self, tab: int) -> None: ...
    def close_tab(self, tab: int) -> None: ...


class AppleScriptBridge:
    """Google Chrome on this Mac, through osascript. `runner` is subprocess.run or a stand-in with the same call."""

    def __init__(self, runner: Callable = subprocess.run, timeout_s: float = 5.0):
        self.runner, self.timeout_s = runner, timeout_s

    def _osa(self, script: str, *args: str) -> str:
        try:
            r = self.runner(["osascript", "-", *args], input=script, capture_output=True, text=True, timeout=self.timeout_s)
        except FileNotFoundError as e:
            raise BridgeError("osascript is not on this machine: the Chrome bridge runs on the Mac only") from e
        except subprocess.TimeoutExpired as e:
            raise BridgeError(f"Chrome did not answer within {self.timeout_s:g} s") from e
        err = (r.stderr or "").strip()
        if JS_OFF_TEXT in err or JS_OFF_TEXT in (r.stdout or ""):
            raise JavaScriptFromAppleEventsOff()
        if r.returncode != 0:
            raise BridgeError("the tab is gone" if "RZ_TAB_NOT_FOUND" in err else f"osascript failed: {err[:200]}")
        return (r.stdout or "").strip()

    def open_tab(self, url: str) -> int:
        if not url.startswith("https://tv.youtube.com/watch/"):
            raise BridgeError("the bridge opens YouTube TV watch pages only")
        return int(self._osa(AS_OPEN, url))

    def run_js(self, tab: int, script: str) -> dict:
        out = self._osa(AS_RUN_JS, str(int(tab)), script)
        try:
            data = json.loads(out)
        except ValueError as e:
            raise BridgeError(f"the page script did not return JSON (got {out[:60]!r})") from e
        if not isinstance(data, dict):
            raise BridgeError("the page script did not return a JSON object")
        return data

    def activate(self, tab: int) -> None:
        self._osa(AS_ACTIVATE, str(int(tab)))

    def close_tab(self, tab: int) -> None:
        self._osa(AS_CLOSE, str(int(tab)))


class FakeTab:
    """One fake tab's player: media time runs with the clock while it plays; ingestionTime = current + offset."""

    def __init__(self, url: str, now: float, lag_s: float, offset: float, dvr_s: float):
        self.url, self.lag_s, self.offset, self.dvr_s = url, lag_s, offset, dvr_s
        self.state, self.muted, self.error, self.loads = 1, False, False, 0
        self.at, self.t0 = now - lag_s - offset, now          # media second `at` was on screen at clock time t0

    def current(self, now: float) -> float:
        return self.at + (now - self.t0 if self.state == 1 else 0.0)

    def end(self, now: float) -> float:
        return now - self.lag_s - self.offset                  # the live head, in media time


class FakeBridge:
    """Tabs with a model player, for tests. `clock` is the test's time; `load_polls` = state reads before a new tab's
    player exists; `seek_over_s` = how far past its target a seek lands; `js_off` = Chrome's menu item is off."""

    def __init__(self, clock: Callable[[], float], *, lag_s: float = 25.0, offset: float = 1.7e9, dvr_s: float = 3600.0,
                 load_polls: int = 0, seek_over_s: float = 0.0, js_off: bool = False, ms: bool = False):
        self.clock, self.lag_s, self.offset, self.dvr_s = clock, lag_s, offset, dvr_s
        self.load_polls, self.seek_over_s, self.js_off, self.ms = load_polls, seek_over_s, js_off, ms
        self.tabs: dict[int, FakeTab] = {}
        self.active: int | None = None
        self.calls: list[tuple] = []          # ("open", tab, url) | ("js", tab, cmd, args) | ("activate", tab) | ("close", tab)

    def open_tab(self, url: str) -> int:
        tab = 100 + len(self.tabs)
        self.tabs[tab] = FakeTab(url, self.clock(), self.lag_s, self.offset, self.dvr_s)
        self.calls.append(("open", tab, url))
        return tab

    def activate(self, tab: int) -> None:
        if tab not in self.tabs:
            raise BridgeError("the tab is gone")
        self.active = tab
        self.calls.append(("activate", tab))

    def close_tab(self, tab: int) -> None:
        self.tabs.pop(tab, None)
        self.calls.append(("close", tab))

    def _state(self, p: FakeTab, now: float) -> dict:
        cur = p.current(now)
        epoch = cur + p.offset
        return {"player": 1, "state": p.state, "current": round(cur, 3), "epoch": round(epoch * 1000 if self.ms else epoch, 3),
                "live": int(p.end(now) - cur < 1.0), "start": round(p.end(now) - p.dvr_s, 3), "end": round(p.end(now), 3),
                "muted": int(p.muted), "error": int(p.error)}

    def run_js(self, tab: int, script: str) -> dict:
        cmd, a = page_script.parse_tag(script)
        self.calls.append(("js", tab, cmd, a))
        if self.js_off:
            raise JavaScriptFromAppleEventsOff()
        if tab not in self.tabs:
            raise BridgeError("the tab is gone")
        p, now = self.tabs[tab], self.clock()
        p.loads += 1
        if p.loads <= self.load_polls:
            return {"player": 0}
        if cmd in ("pause", "play"):
            p.at, p.t0, p.state = p.current(now), now, (2 if cmd == "pause" else 1)
        elif cmd in ("mute", "unmute"):
            p.muted = cmd == "mute"
        elif cmd == "live":
            p.at, p.t0 = p.end(now), now
        elif cmd in ("seek", "seek_epoch"):
            to = a["t"] if cmd == "seek" else page_script.media_second(p.current(now), p.current(now) + p.offset, a["t"])
            if to < p.end(now) - p.dvr_s or to > p.end(now):
                return {"player": 1, "ok": 0, "why": "out_of_range", "to": round(to, 3), "start": round(p.end(now) - p.dvr_s, 3), "end": round(p.end(now), 3)}
            p.at, p.t0 = to + self.seek_over_s, now
            return {**self._state(p, now), "ok": 1, "to": round(to, 3)}
        return self._state(p, now)
