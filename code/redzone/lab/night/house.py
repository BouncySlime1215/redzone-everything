"""A fake living room for the end-to-end night: two Fire TV sticks and a TV, in memory.

The real redzone code talks to them exactly as it talks to the hardware: adb shell commands
(dumpsys media_session, input keyevent, am start, dumpsys hdmi_control, uiautomator dump,
screencap). FakeStick is redzone.tuners.adb.Stick with only its transport swapped: instead of an
adb process, each command goes to a FakeFireTV, which answers the way the sticks answered on the
Nov 2 device trials (docs/device-trial-2026-11-02.md):

- YouTube TV: a deep link buffers, then plays the new channel after ~4.5 s, its media session
  naming the channel ("Game, NBC, null"). Losing the TV input pauses it. HOME backgrounds it; the
  relaunch brings it back to the player.
- Prime Video: `am start -W` opens the game's detail page (~3 s); "Watch Live" (CENTER) opens the
  player, which reports "connecting" and plays ~30 s after the link. A new player screen is a new
  ActivityRecord. HOME then REORDER_TO_FRONT lands on the detail page with no session; Watch ->
  Watch Live presses through. Prime keeps playing when its stick loses the TV input.
- HOME makes a stick the HDMI-CEC active source ~0.5 s later and the TV follows; the picture
  settles input_settle_s after that. WAKEUP on a sleeping stick also grabs the TV (one-touch play).
- CENTER inside a playing player pauses it (the hazard the tuners guard against).
- "Are you still watching?" pauses the player and only CENTER on the prompt clears it.

The TV keeps the ground truth: what was on screen, second by second, and every cut away from a
game, so the test can grade mid-play cuts from what the TV actually showed, not from what the
engine believed it did.
"""
from __future__ import annotations

import asyncio
import json
import re
import struct
import time
import zlib
from dataclasses import dataclass, field

from redzone.tuners import adb as adbmod
from redzone.tuners.adb import RunResult, Stick, _adb_problem

YTTV = "com.amazon.firetv.youtube.tv"
PRIME = "com.amazon.firebat"
VLC = "org.videolan.vlc"
LAUNCHER = "com.amazon.tv.launcher"
PACKAGES = (YTTV, PRIME, VLC, "com.amazon.tv.launcher", "com.amazon.firetv.screensaver")

PLAYING, PAUSED, BUFFERING, CONNECTING, NONE = 3, 2, 6, 8, 0


@dataclass
class Timing:
    """How long things take on these sticks (seconds). Defaults: the Nov 2 trials."""
    yttv_tune: float = 4.5          # deep link -> the new channel playing
    yttv_resume: float = 2.0        # brought back to the front after HOME -> playing again
    prime_page: float = 3.0         # `am start -W` returns once the detail page is drawn
    prime_tune: float = 30.0        # deep link -> the game playing (page + Watch Live + stream)
    prime_live: float = 3.5         # Watch Live pressed (after a HOME flip) -> playing
    reel_load: float = 1.5
    home_flip: float = 0.5          # HOME -> this stick is the CEC active source, the TV follows
    tv_settle: float = 1.0          # the TV's picture after an input change (HDMI re-sync)
    cmd: float = 0.03               # a plain shell command over the always-open shell
    uiautomator: float = 1.5
    screencap: float = 1.2
    # After a pause (HOME flip, input lost, a prompt) YouTube TV resumes where it paused, so the
    # picture is now behind live by the time it sat paused. True = it jumps to the live edge instead
    # (an open hardware question: set it to see how the night grades if the stick does that).
    yttv_resume_live_edge: bool = False


@dataclass
class App:
    pkg: str
    task: bool = False              # has a task (was launched and not killed)
    screen: str = ""                # "player" | "detail" | "panel" | ""
    target: str | None = None       # what the player shows ("youtubetv:nbc", "prime:<gti>", "reel:..")
    url: str = ""
    state: int = NONE
    position: int = 0
    updated: int = 0
    ready_at: float | None = None   # loading -> playing at this time
    behind: float = 0.0             # seconds behind the live edge (rewinds)
    token: str = ""                 # Prime's player ActivityRecord id
    subtitle: str = ""
    title: str = ""
    prompt: str = ""                # "still_watching" while that prompt is up
    ends_at: float | None = None    # a reel stops by itself
    page_for: str | None = None     # Prime detail page of this target
    paused_at: float | None = None  # when the player paused (resuming adds the gap to `behind`)


class FakeFireTV:
    def __init__(self, house: "House", host: str, hdmi: str, name: str):
        self.house, self.host, self.hdmi, self.name = house, host, hdmi, name
        self.online = True
        self.wedged = False             # adb transport stuck: answers only after `adb disconnect`
        self.awake = "Awake"
        self.fg = LAUNCHER
        self.apps: dict[str, App] = {p: App(p) for p in (YTTV, PRIME, VLC)}
        self.boot = time.monotonic()
        self.cmds = 0
        self.log: list[tuple[float, str]] = []
        self._tok = 0

    # ---- clocks -----------------------------------------------------------------------
    def uptime_ms(self) -> int:
        return int((time.monotonic() - self.boot) * 1000) + 1_000_000

    def _stamp(self, a: App, state: int, position: int | None = None) -> None:
        now = time.time()
        if state == PAUSED:
            if a.paused_at is None:
                a.paused_at = now
        elif a.paused_at is not None and state in (PLAYING, BUFFERING, CONNECTING):
            # Resuming a paused live stream: it picks up where it stopped, that much behind live.
            if a.pkg != VLC:
                a.behind += max(0.0, now - a.paused_at)
            a.paused_at = None
        a.state, a.updated = state, self.uptime_ms()
        if position is not None:
            a.position = position

    def _live_pos(self, a: App) -> int:
        """Where a stream that is starting (or jumping) now reports it is. Prime reports a
        wall-clock position (minus how far behind live it runs); YouTube TV and the reel player
        count from the start of what they opened, so a fresh tune starts low again."""
        if a.pkg == PRIME:
            return int((time.time() - a.behind) * 1000) - 1_700_000_000_000
        return 15_000 + int(1000 * (self._tok % 7))

    def _now_pos(self, a: App) -> int:
        """Where it is right now: a playing session moves on with the stick's clock."""
        if a.state == PLAYING:
            return a.position + (self.uptime_ms() - a.updated)
        return a.position

    def _token(self) -> str:
        self._tok += 1
        return f"{abs(hash((self.host, self._tok))) % 0xffffff:06x}"

    # ---- time passing -----------------------------------------------------------------
    def tick(self) -> None:
        now = time.time()
        for a in self.apps.values():
            if a.ready_at is not None and now >= a.ready_at and a.screen == "player" and not a.prompt:
                a.ready_at = None
                self._tok += 1
                self._stamp(a, PLAYING, self._live_pos(a))
            if a.ends_at is not None and now >= a.ends_at and a.state == PLAYING:
                a.ends_at = None
                self._stamp(a, 1)   # the reel finished: stopped
        if self.house.faults_on("still_watching", self.host):
            a = self.apps.get(self.fg)
            if a and a.pkg == YTTV and a.screen == "player" and a.state == PLAYING and not a.prompt:
                a.prompt = "still_watching"
                self._stamp(a, PAUSED)
                self.house.event("prompt_shown", self.host, a.target)
                self.house.fault_done("still_watching", self.host)

    # ---- what's showing ---------------------------------------------------------------
    def picture(self) -> tuple[str | None, str, float]:
        """(target, what, seconds behind live): what this stick puts on the HDMI cable."""
        if self.awake != "Awake":
            return None, "asleep", 0.0
        a = self.apps.get(self.fg)
        if a is None:
            return None, "home", 0.0
        if a.prompt:
            return a.target, "prompt", a.behind
        if a.screen != "player":
            return None, f"{a.pkg.split('.')[-1]} {a.screen or 'app'}", 0.0
        what = {PLAYING: "playing", PAUSED: "paused", BUFFERING: "loading", CONNECTING: "loading"}.get(a.state, "idle")
        return a.target, what, a.behind

    # ---- the adb shell ----------------------------------------------------------------
    def latency(self, cmd: str) -> float:
        t = self.house.timing
        if "uiautomator dump" in cmd:
            return t.uiautomator
        if "am start" in cmd and " -W " in cmd:
            return t.prime_page if PRIME in cmd else 1.0
        return t.cmd

    def shell(self, cmd: str) -> tuple[bool, str]:
        self.cmds += 1
        self.tick()
        outs, ok = [], True
        for part in re.split(r"\s*(?:&&|;)\s*", cmd.strip()):
            if not part:
                continue
            good, out = self._one(part)
            outs.append(out)
            if not good:
                ok = False
                if "&&" in cmd:
                    break
        self.tick()
        self.house.observe()
        out = "\n".join(o for o in outs if o)
        if self.house.trace is not None:
            self.house.trace.append((round(time.time(), 2), self.host[-7:], cmd[:110], out[-600:].replace("\n", " / ")))
        return ok, out

    def _one(self, cmd: str) -> tuple[bool, str]:
        cmd = re.sub(r"\s*[12]?>\s*/dev/null(\s+2>&1)?|\s+2>&1", "", cmd).strip()
        if cmd.startswith("echo "):
            return True, cmd[5:].strip("'\"")
        if cmd.startswith("getprop"):
            return True, {"ro.product.model": "AFTKM", "ro.build.version.release": "11",
                          "ro.build.version.name": "Fire OS 8.1.0.3 (PS8103)"}.get(cmd.split()[-1], "")
        if cmd.startswith("settings get global device_name"):
            return True, self.name
        if cmd.startswith(("settings put", "rm -f", "true")):
            return True, ""
        if cmd.startswith("pm list packages"):
            return True, "\n".join(f"package:{p}" for p in PACKAGES)
        if cmd.startswith("dumpsys power"):
            return True, f"  mWakefulness={self.awake}"
        if cmd.startswith("dumpsys hdmi_control"):
            if "-c" in cmd and "mIsActiveSource" in cmd:
                return True, "1" if self.house.tv.source is self else "0"
            return True, f"  mPhysicalAddress: {self.hdmi[-1]}.0.0.0"
        if cmd.startswith("dumpsys media_session"):
            return True, self._media()
        if cmd.startswith("dumpsys activity"):
            return True, self._resumed()
        if cmd.startswith("input keyevent"):
            for k in cmd.split()[2:]:
                self.key(k)
            return True, ""
        if cmd.startswith("am start"):
            return self._am(cmd)
        if cmd.startswith("uiautomator dump") or cmd.startswith("cat /sdcard"):
            return self._ui()
        if cmd.startswith("cmd hdmi_control"):
            return True, "Unknown command: onetouchplay"
        if cmd.startswith("cmd package resolve-activity"):
            return True, ""
        return True, ""

    def _media(self) -> str:
        lines = []
        for a in self.apps.values():
            if not a.task or a.state == NONE and not a.updated:
                continue
            if a.pkg == PRIME and a.screen != "player" and a.state in (NONE, 1):
                continue    # Prime's detail page: no session at all
            lines.append(f"  package={a.pkg}")
            lines.append(f"    state=PlaybackState {{state={a.state}, position={a.position}, buffered position=0, "
                         f"speed={1.0 if a.state == PLAYING else 0.0}, updated={a.updated}, actions=822, "
                         f"custom actions=[], active item id=-1, error=null}}")
            title = a.title or ("PrimeVideo" if a.pkg == PRIME else "null")
            lines.append(f"    metadata: size=4, description={title}, {a.subtitle or 'null'}, null")
        return "\n".join(lines)

    def _resumed(self) -> str:
        if self.fg == LAUNCHER:
            return "  mResumedActivity: ActivityRecord{1a2b3c u0 com.amazon.tv.launcher/.ui.HomeActivity t2}"
        a = self.apps[self.fg]
        if a.pkg == PRIME:
            act = ".IgnitionActivity" if a.screen == "player" else ".DetailActivity"
            tok = a.token if a.screen == "player" else "d" + (a.token or "000000")[1:]
            return f"  mResumedActivity: ActivityRecord{{{tok} u0 {PRIME}/{act} t31}}"
        if a.pkg == YTTV:
            return f"  mResumedActivity: ActivityRecord{{4d5e6f u0 {YTTV}/dev.cobalt.app.MainActivity t40}}"
        return f"  mResumedActivity: ActivityRecord{{7a8b9c u0 {a.pkg}/.gui.video.VideoPlayerActivity t41}}"

    def _ui(self) -> tuple[bool, str]:
        a = self.apps.get(self.fg)
        if a is not None and a.pkg == PRIME and a.screen == "detail":
            return True, ('<?xml version="1.0"?><hierarchy rotation="0"><node text="Watch Live"/>'
                          '<node text="Add to Watchlist"/><node text="Included with Prime"/></hierarchy>')
        if a is not None and a.pkg == PRIME and a.screen == "panel":
            return True, '<hierarchy rotation="0"><node text="Watch Live"/><node text="Resume"/></hierarchy>'
        return False, "ERROR: could not get idle state."

    def ocr(self) -> tuple[str, int]:
        """(text a screenshot shows, its brightness): YouTube TV draws its UI itself (OCR only)."""
        tgt, what, _ = self.picture()
        a = self.apps.get(self.fg)
        if what == "prompt":
            return "Are you still watching? | Continue watching", 140
        if a is not None and a.pkg == PRIME and a.screen == "detail":
            return "Watch | Watchlist | More purchase options | Included with Prime", 150
        if a is not None and a.pkg == PRIME and a.screen == "panel":
            return "Watch Live | Resume", 150
        if what == "home":
            return "Home | Find | Live | Settings", 130
        return "", 0        # protected video: DRM-black

    # ---- keys -------------------------------------------------------------------------
    def key(self, k: str) -> None:
        k = k.removeprefix("KEYCODE_")
        self.log.append((time.time(), k))
        a = self.apps.get(self.fg)
        if k == "HOME":
            if a is not None:
                self._background(a)
            self.fg = LAUNCHER
            self.house.claim(self, self.house.timing.home_flip)
        elif k == "WAKEUP":
            if self.awake != "Awake":
                self.awake = "Awake"
                self.house.claim(self, self.house.timing.home_flip)   # one-touch play: grabs the TV
        elif k == "BACK":
            if self.awake == "Dreaming":
                self.awake = "Awake"
        elif a is None:
            return
        elif k == "DPAD_CENTER":
            self._center(a)
        elif k == "MEDIA_PLAY":
            if a.screen == "player" and a.state == PAUSED and not a.prompt:
                self._stamp(a, PLAYING, a.position)
        elif k == "MEDIA_PAUSE":
            if a.screen == "player" and a.state == PLAYING:
                self._stamp(a, PAUSED, self._now_pos(a))
        elif k == "MEDIA_REWIND":
            if a.screen == "player" and a.state in (PLAYING, PAUSED) and a.pkg == YTTV:
                a.behind += float(self.house.seek_step)
                self._stamp(a, a.state, max(1000, self._now_pos(a) - int(1000 * self.house.seek_step)))

    def _center(self, a: App) -> None:
        if a.prompt:
            a.prompt = ""
            self._stamp(a, PLAYING, self._now_pos(a))
            self.house.event("prompt_cleared", self.host, a.target)
        elif a.pkg == PRIME and a.screen == "detail":
            target = a.page_for
            if a.target == target and a.state == NONE and a.token:
                a.screen = "panel"      # after a HOME flip: Watch opens the Watch Live / Resume panel
                return
            self._prime_player(a, target, self.house.timing.prime_tune - self.house.timing.prime_page)
        elif a.pkg == PRIME and a.screen == "panel":
            self._prime_player(a, a.page_for, self.house.timing.prime_live)
        elif a.screen == "player" and a.state == PLAYING:
            self._stamp(a, PAUSED, self._now_pos(a))      # OK in a player pauses it
            self.house.event("center_paused_player", self.host, a.target)
        elif a.screen == "player" and a.state == PAUSED:
            self._stamp(a, PLAYING, a.position)

    def _prime_player(self, a: App, target: str | None, load_s: float) -> None:
        a.screen, a.target, a.behind, a.prompt, a.paused_at = "player", target, 0.0, "", None
        a.token = self._token()
        a.ready_at = time.time() + max(0.5, load_s)
        self._stamp(a, CONNECTING, 0)

    def _background(self, a: App) -> None:
        if a.screen == "player" and a.state in (PLAYING, BUFFERING, CONNECTING):
            if a.pkg == PRIME:
                pass                       # Prime's player keeps running in the background
            else:
                a.ready_at = None
                self._stamp(a, PAUSED, self._now_pos(a))

    def lost_input(self) -> None:
        """The TV went to the other stick: YouTube TV pauses, Prime keeps playing."""
        a = self.apps.get(self.fg)
        if a is not None and a.pkg == YTTV and a.screen == "player" and a.state in (PLAYING, BUFFERING):
            a.ready_at = None
            self._stamp(a, PAUSED, self._now_pos(a))

    # ---- app launches -----------------------------------------------------------------
    def _am(self, cmd: str) -> tuple[bool, str]:
        m = re.search(r"-n\s+(\S+)", cmd)
        if m and "-f 0x00020000" in cmd:           # REORDER_TO_FRONT of Prime's activity
            pkg = m.group(1).split("/")[0]
            a = self.apps.get(pkg)
            if a is None or not a.task:
                return False, "Error: Activity not started, unable to resolve Intent"
            self.fg = pkg
            if pkg == PRIME and a.screen == "player":
                # Prime comes back on the game's detail page, its session gone (soak, Nov 2).
                a.screen, a.page_for, a.state, a.updated, a.ready_at = "detail", a.target, NONE, 0, None
            return True, "Starting: Intent { flg=0x20000 cmp=" + m.group(1) + " }"
        url = re.search(r"-d\s+'?([^'\s]+)'?", cmd)
        pkg = cmd.split()[-1]
        a = self.apps.get(pkg)
        if a is None or url is None:
            return False, "Error: Activity not started, unable to resolve Intent"
        url = url.group(1)
        target = self.house.target_for_url(url)
        fresh = "-f 0x14000000" in cmd
        if self.awake != "Awake":
            self.awake = "Awake"
        was_fg = self.fg == pkg
        self.fg = pkg
        if pkg == YTTV:
            if a.task and not was_fg and not fresh:
                # In the background: Android brings the task forward; the app keeps its own link.
                if a.screen == "player":
                    if a.prompt:   # the sim's model: coming forward reloads the player, prompt gone
                        a.prompt = ""
                        self.house.event("prompt_cleared", self.host, f"{a.target} (brought forward)")
                    if self.house.timing.yttv_resume_live_edge:
                        a.behind, a.paused_at = 0.0, None
                    a.ready_at = time.time() + self.house.timing.yttv_resume
                    self._stamp(a, BUFFERING, self._now_pos(a))
                return True, ("Starting: Intent { act=android.intent.action.VIEW }\n"
                              "Warning: Activity not started, its current task has been brought to the front")
            if a.prompt:
                self.house.event("prompt_cleared", self.host, f"{a.target} (relink)")
            a.task, a.screen, a.url, a.target, a.behind, a.prompt, a.paused_at = True, "player", url, target, 0.0, "", None
            a.subtitle = self.house.channel_name(target)
            a.title = self.house.show_title(target)
            a.ready_at = time.time() + self.house.timing.yttv_tune
            self._stamp(a, BUFFERING, self._now_pos(a))
            return True, "Starting: Intent { act=android.intent.action.VIEW }"
        if pkg == PRIME:
            a.task = True
            if a.screen == "player" and a.target == target and a.state in (PLAYING, CONNECTING) and was_fg:
                return True, "Starting: Intent { act=android.intent.action.VIEW }"   # same game: ignored
            # The detail page opens over whatever was playing (the old game keeps running behind it).
            a.page_for = target
            if a.screen == "player":
                a.screen = "detail"
                a.target = a.target   # still the old stream's session until Watch Live
            else:
                a.screen = "detail"
            return True, "Starting: Intent { act=android.intent.action.VIEW }"
        if pkg == VLC:
            if a.prompt:
                self.house.event("prompt_cleared", self.host, f"{a.target} (relink)")
            a.task, a.screen, a.url, a.target, a.behind, a.prompt, a.paused_at = True, "player", url, target, 0.0, "", None
            a.title, a.subtitle = "reel.mp4", ""
            a.ready_at = time.time() + self.house.timing.reel_load
            a.ends_at = time.time() + self.house.timing.reel_load + self.house.reel_seconds
            self._stamp(a, BUFFERING, 0)
            return True, "Starting: Intent { act=android.intent.action.VIEW }"
        return False, "Error: Activity not started"

    def _media_detail_fix(self) -> None:
        pass


class FakeTV:
    """Shows whichever stick is the HDMI-CEC active source. Records everything it showed."""

    def __init__(self, house: "House"):
        self.house = house
        self.source: FakeFireTV | None = None
        self.other: str | None = None    # tv_other_input: the input a person picked (not a stick)
        self.input_at = 0.0
        self.flips = 0


@dataclass
class Shown:
    t: float
    host: str | None
    target: str | None
    what: str
    behind: float
    cause: str


@dataclass
class House:
    timing: Timing = field(default_factory=Timing)
    seek_step: float = 10.0
    reel_seconds: float = 45.0
    sticks: dict[str, FakeFireTV] = field(default_factory=dict)
    urls: dict[str, str] = field(default_factory=dict)        # deep link -> target
    names: dict[str, str] = field(default_factory=dict)       # target -> channel name in the media session
    titles: dict[str, str] = field(default_factory=dict)
    shown: list[Shown] = field(default_factory=list)
    events: list[tuple[float, str, str, str | None]] = field(default_factory=list)
    active_faults: dict[str, set] = field(default_factory=dict)
    trace: list | None = None                                  # every shell command, when tracing
    adb_scale: float = 1.0                                     # wifi_degrade: every adb reply this much slower

    def __post_init__(self):
        self.tv = FakeTV(self)
        self._claims: list[asyncio.TimerHandle] = []
        self._cause = "start"

    def add(self, host: str, hdmi: str, name: str) -> FakeFireTV:
        d = FakeFireTV(self, host, hdmi, name)
        self.sticks[host] = d
        return d

    # ---- faults the scenario turns on and off -------------------------------------------
    def faults_on(self, kind: str, host: str | None = None) -> bool:
        s = self.active_faults.get(kind)
        return bool(s) and (host is None or host in s or "*" in s)

    def fault_done(self, kind: str, host: str) -> None:
        """A one-shot fault fired (on this host, or on any host for a "*" fault)."""
        s = self.active_faults.get(kind, set())
        s.discard(host)
        s.discard("*")

    def event(self, kind: str, host: str, detail: str | None = None) -> None:
        self.events.append((time.time(), kind, host, detail))

    # ---- lookups ------------------------------------------------------------------------
    def target_for_url(self, url: str) -> str | None:
        base = url.split("?")[0]
        if "reel.mp4" in url:
            return "reel:" + (url.split("v=")[-1] if "v=" in url else "")
        return self.urls.get(base) or self.urls.get(url)

    def channel_name(self, target: str | None) -> str:
        return self.names.get(target or "", "")

    def show_title(self, target: str | None) -> str:
        return self.titles.get(target or "", "Live Sports")

    # ---- the TV input -------------------------------------------------------------------
    def claim(self, stick: FakeFireTV, after: float) -> None:
        """This stick announced <Active Source>: the TV follows `after` seconds later, unless a
        fault makes the TV ignore it."""
        if self.faults_on("flip_ignored", stick.host):
            self.event("flip_ignored", stick.host)
            self.fault_done("flip_ignored", stick.host)
            return
        loop = asyncio.get_running_loop()
        self._claims.append(loop.call_later(after, self._switch_to, stick))

    def tv_input(self, stick: FakeFireTV) -> None:
        """The TV's own input change (SmartThings): nothing was pressed on any stick."""
        if self.faults_on("tv_input_ignored", stick.host):
            self.event("tv_input_ignored", stick.host)
            self.fault_done("tv_input_ignored", stick.host)
            return
        self._switch_to(stick)

    def _switch_to(self, stick: FakeFireTV | None) -> None:
        old = self.tv.source
        if old is stick:
            return
        if stick is not None:
            self.tv.other = None   # one of the sticks took the TV back
        self.tv.source = stick
        self.tv.input_at = time.time()
        self.tv.flips += 1
        if old is not None:
            old.lost_input()
        self.observe(cause="input")

    def screen(self) -> tuple[str | None, str | None, str, float]:
        s = self.tv.source
        if s is None:
            return None, None, "other input" if self.tv.other else "no input", 0.0
        if time.time() - self.tv.input_at < self.timing.tv_settle:
            return s.host, None, "settling", 0.0
        tgt, what, behind = s.picture()
        return s.host, tgt, what, behind

    def observe(self, cause: str = "cmd") -> None:
        for s in self.sticks.values():
            s.tick()
        host, tgt, what, behind = self.screen()
        last = self.shown[-1] if self.shown else None
        if last and (last.host, last.target, last.what, round(last.behind)) == (host, tgt, what, round(behind)):
            return
        self.shown.append(Shown(time.time(), host, tgt, what, behind, cause))


# ---- the Stick transport, swapped ---------------------------------------------------------

HOUSE: House | None = None


class FakeStick(Stick):
    """redzone.tuners.adb.Stick talking to a FakeFireTV instead of an adb process."""

    def _dev(self) -> FakeFireTV | None:
        return HOUSE.sticks.get(self.host) if HOUSE else None

    async def _fake(self, cmd: str, timeout: float) -> RunResult:
        t0 = time.monotonic()
        d = self._dev()
        if d is None or not d.online:
            await asyncio.sleep(min(timeout, 3.0))
            text = f"error: device '{self.host}' not found"
            self.state = _adb_problem(text) or "offline"
            return RunResult(False, text, time.monotonic() - t0)
        if d.wedged:
            await asyncio.sleep(min(timeout, 2.0))
            return RunResult(False, "error: closed", time.monotonic() - t0)
        lat = d.latency(cmd) * d.house.adb_scale
        if lat > timeout:
            await asyncio.sleep(timeout)
            ok, out = d.shell(cmd)     # it still ran on the stick; we just never heard back
            return RunResult(False, "timeout/error: ", time.monotonic() - t0)
        await asyncio.sleep(lat)
        ok, out = d.shell(cmd)
        if ok:
            self.last_ok = time.time()
        return RunResult(ok, out, time.monotonic() - t0)

    async def _shell(self, cmd: str, timeout: float) -> RunResult:
        return await self._fake(cmd, timeout)

    async def run(self, *args: str, timeout: float = 8.0) -> RunResult:
        if args and args[0] == "shell":
            return await self._fake(" ".join(args[1:]), timeout)
        return RunResult(False, "fake adb: can't do " + " ".join(args), 0.0)

    async def screencap(self, timeout: float = 8.0) -> bytes:
        d = self._dev()
        if d is None or not d.online or d.wedged:
            await asyncio.sleep(min(timeout, 3.0))
            return b""
        await asyncio.sleep(HOUSE.timing.screencap)
        d.tick()
        text, luma = d.ocr()
        return fake_png(text, luma)

    def _shell_session(self):
        return None

    async def close(self) -> None:
        return None

    def drop_session(self) -> None:
        return None


async def fake_adb(*args: str, timeout: float = 8.0) -> RunResult:
    """adb's own commands (connect, disconnect, version, start-server) against the fake house."""
    await asyncio.sleep(0.05)
    return RunResult(*adb_cmd(list(args)), 0.05)


def adb_cmd(args: list[str]) -> tuple[bool, str]:
    what = args[0] if args else ""
    if what == "version":
        return True, "Android Debug Bridge version 1.0.41 (fake)"
    if what == "start-server":
        return True, ""
    host = args[1] if len(args) > 1 else ""
    d = HOUSE.sticks.get(host) if HOUSE else None
    if what == "disconnect":
        if d is not None:
            d.wedged = False
            HOUSE.event("adb_disconnect", host)
        return True, f"disconnected {host}"
    if what == "connect":
        if d is None or not d.online:
            return False, f"failed to connect to '{host}': No route to host"
        return True, f"already connected to {host}"
    return False, f"fake adb: can't do {what}"


# ---- screenshots: a tiny real PNG carrying the text and brightness the screen shows ----------

def fake_png(text: str, luma: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    w, h = 16, 9
    raw = b"".join(b"\x00" + bytes([luma]) * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
            + chunk(b"tEXt", b"rz\x00" + json.dumps({"text": text, "luma": luma}).encode())
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def png_info(data: bytes) -> dict | None:
    i = data.find(b"tEXtrz\x00")
    if i < 0:
        return None
    n = struct.unpack(">I", data[i - 4:i])[0]
    try:
        return json.loads(data[i + 7:i + 4 + n].decode())
    except ValueError:
        return None


def fake_recognize(path, timeout: float = 8.0):
    """redzone.screen_ocr.recognize for fake screenshots: the words the screen shows, laid out in
    one row each (Mac Vision OCR is not available, and not needed, off the Mac)."""
    from redzone.screen_ocr import OCRResult, TextBox
    try:
        with open(path, "rb") as f:
            info = png_info(f.read())
    except OSError:
        info = None
    if info is None:
        return OCRResult("error", error="unreadable")
    parts = [p.strip() for p in info["text"].split("|") if p.strip()]
    boxes = tuple(TextBox(p, 0.95, 0.3, 0.3 + 0.08 * i, 0.4, 0.06) for i, p in enumerate(parts))
    return OCRResult("ok", boxes, 1920, 1080)


def fake_luma_path(path):
    try:
        with open(path, "rb") as f:
            return fake_luma(f.read())
    except OSError:
        return None


def fake_luma(data: bytes):
    import numpy as np
    info = png_info(data)
    if info is None:
        return None
    return np.full((9, 16), float(info["luma"]))


def install(house: House) -> None:
    """Point redzone's adb layer at the fake house."""
    global HOUSE
    HOUSE = house
    adbmod._adb = fake_adb
