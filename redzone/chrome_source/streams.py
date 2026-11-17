"""ChromeStreams: N YouTube TV tabs as the streams of the PARK design. Park one at a second, jump to one, read them.

    streams = ChromeStreams(AppleScriptBridge(), channel_map)
    streams.open(["espn", "tnt", "nbatv", "abc"])      # one tab per channel, every one muted through the player
    streams.park(2, content_second)                    # seek tab 2 just short of that second on YouTube's clock, pause it
    streams.jump(2)                                    # bring tab 2 to the front, play it, pause the tab that was left
    streams.position(2), streams.health(2)

What it will not do, whatever it is asked: touch the Mac's or the TV's volume or power, change a Chrome or account
setting, type into a page, or sign in. Sound is only the player's own mute. A tab with no player (not signed in, an
error page, still loading) is reported as such by health() and left alone.

Step times fed to the simulator (scripts/park_sim.DEVICES["chrome"]): pause exact, resume 0.28 s, a forward seek 0.46 s
landing 0.22 s past its target (MEASURED by hand, Nov 10, one seek); a tab switch 0.3 s (ASSUMED). `lead_s` aims every
park that much early so the overshoot cannot carry it past the second asked for. Nothing in this file has run against
the real browser: it was written with no Mac, and tested on FakeBridge only.

Why: a park has to be "a spot the feed has already reported" to the second, and a jump has to be three small steps in
a fixed order (front, play, pause the other) so the timing measured for each step adds up to the jump the planner
assumes.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from . import page_script
from .bridge import Bridge

WATCH_URL = "https://tv.youtube.com/watch/{code}"
LEAD_S = 0.3             # ASSUMED margin over the one measured overshoot (0.22 s)


@dataclass
class Stream:
    index: int
    channel: str
    tab: int
    audio: bool = False      # False = muted through the player


class ChromeStreams:
    def __init__(self, bridge: Bridge, channel_map, *, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep, lead_s: float = LEAD_S):
        self.bridge, self.channels, self.clock, self.sleep, self.lead_s = bridge, channel_map, clock, sleep, lead_s
        self.streams: list[Stream] = []
        self.on_screen: int | None = None

    # ---- the tabs -------------------------------------------------------------------------------------------------

    def url_for(self, channel: str) -> str:
        code = self.channels.yttv_code(f"youtubetv:{channel}")
        if not code:
            raise ValueError(f"no YouTube TV watch code for channel {channel!r}")
        return WATCH_URL.format(code=code)

    def open(self, channels: list[str], *, audio: int | None = None, wait_s: float = 30.0, poll_s: float = 0.5) -> list[Stream]:
        """Open one tab per channel and mute each through its player as soon as the player is there. `audio` = the one
        stream left with sound (None = every tab muted). The first stream is the one on screen."""
        urls = [self.url_for(c) for c in channels]          # every code is checked before any tab is opened
        for c, url in zip(channels, urls):
            self.streams.append(Stream(len(self.streams), c, self.bridge.open_tab(url)))
        new = self.streams[-len(channels):] if channels else []
        todo, deadline = list(new), self.clock() + wait_s
        while todo:
            for s in list(todo):
                s.audio = audio == s.index
                if self._call(s, "unmute" if s.audio else "mute").get("player"):
                    todo.remove(s)
            if not todo or self.clock() >= deadline:
                break
            self.sleep(poll_s)
        if self.on_screen is None and self.streams:
            self.on_screen = self.streams[0].index
        return new

    def close(self) -> None:
        for s in self.streams:
            self.bridge.close_tab(s.tab)
        self.streams, self.on_screen = [], None

    def _call(self, s: Stream, cmd: str, **args) -> dict:
        return self.bridge.run_js(s.tab, page_script.build(cmd, **args))

    def _get(self, stream: int) -> Stream:
        if not 0 <= stream < len(self.streams):
            raise ValueError(f"no stream {stream}")
        return self.streams[stream]

    # ---- sound: the player's own mute, nothing else ------------------------------------------------------------------

    def mute(self, stream: int, on: bool = True) -> dict:
        s = self._get(stream)
        r = self._call(s, "mute" if on else "unmute")
        if r.get("player") and r.get("ok", 1):
            s.audio = not on
        return self._small(r)

    # ---- park, jump -----------------------------------------------------------------------------------------------------

    def park(self, stream: int, content_second: float, lead_s: float | None = None) -> dict:
        """Seek a stream to `lead_s` short of an epoch second on YouTube's clock, then pause it. Returns where it is.
        A second outside what the tab can seek to is refused and the tab is left as it was."""
        s = self._get(stream)
        lead = self.lead_s if lead_s is None else lead_s
        r = self._call(s, "seek_epoch", t=float(content_second) - lead)
        if not r.get("player") or not r.get("ok"):
            return {"ok": False, "why": r.get("why", "no_player"), "stream": stream}
        p = self._call(s, "pause")
        epoch = page_script.epoch_s(p.get("epoch"))
        return {"ok": bool(p.get("player")) and p.get("state") == 2, "stream": stream, "state": page_script.state_name(p.get("state")),
                "epoch": epoch, "short_s": None if epoch is None else round(float(content_second) - epoch, 3)}

    def jump(self, to_stream: int) -> dict:
        """Bring a stream to the front, play it, then pause the one that was left. In that order: the new picture first."""
        s, left = self._get(to_stream), self.on_screen
        if left == to_stream:
            return {"ok": True, "to": to_stream, "left": None, "state": page_script.state_name(self._call(s, "state").get("state"))}
        self.bridge.activate(s.tab)
        a = self._call(s, "play")
        b = self._call(self._get(left), "pause") if left is not None else {}
        self.on_screen = to_stream
        return {"ok": bool(a.get("player")) and a.get("state") in (1, 3), "to": to_stream, "left": left,
                "state": page_script.state_name(a.get("state")), "left_state": page_script.state_name(b.get("state")) if b else None,
                "left_epoch": page_script.epoch_s(b.get("epoch")) if b else None}

    def go_live(self, stream: int) -> dict:
        return self._small(self._call(self._get(stream), "live"))

    # ---- reading ----------------------------------------------------------------------------------------------------------

    def position(self, stream: int) -> dict:
        """Where a stream is: `epoch` = YouTube's clock time of the frame on screen, `behind_live_s` = seconds short of the
        newest second the tab can show."""
        return self._small(self._call(self._get(stream), "state"))

    def health(self, stream: int) -> dict:
        """player (is there one), state, lag_s behind real time by this machine's clock, error (an error overlay is up)."""
        r = self._call(self._get(stream), "state")
        out = self._small(r)
        epoch = out.get("epoch")
        return {"player": out["player"], "state": out.get("state", "none"), "lag_s": None if epoch is None else round(self.clock() - epoch, 3),
                "error": bool(r.get("error")), "muted": None if r.get("muted") is None else bool(r.get("muted")),
                "at_live": bool(r.get("live")), "on_screen": self.on_screen == stream}

    @staticmethod
    def _small(r: dict) -> dict:
        if not r.get("player"):
            return {"player": False}
        if not r.get("ok", 1):
            return {"player": True, "ok": False, "why": str(r.get("why", ""))[:24]}
        cur, end = r.get("current"), r.get("end")
        return {"player": True, "ok": True, "state": page_script.state_name(r.get("state")), "epoch": page_script.epoch_s(r.get("epoch")),
                "current": cur, "at_live": bool(r.get("live")),
                "behind_live_s": None if cur is None or end is None else round(end - cur, 3)}
