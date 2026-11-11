"""What is this Fire TV stick playing right now? Read-only probes for YouTube TV discovery.

Why this module exists: YouTube TV on Fire TV is a Cobalt (web-engine) app. It paints its own
screen, so `uiautomator` sees almost none of it (ADBTuner's Jan 2026 logs show only "> youtubetv").
The trustworthy signals are:
  1. the Android media session: play state + the title/subtitle/description the app publishes
     (`dumpsys media_session`), and
  2. optionally the YouTube "Lounge" now-playing video id (see lounge.py).
Screen text is still read, because prompts ("Who's watching?") and some error dialogs do show up.

Guardrails:
- Only `adb shell` reads, key presses and deep links; nothing is installed or changed on the stick.
- Every adb call has a timeout (Stick.shell enforces it) and nothing here raises: a broken stick
  yields an empty Evidence, never an exception.
- Works the same on a hidden stick (two-stick mode): nothing here assumes the screen is visible.

Why: first added in 634e0a01 (2026-11-02) as "Recover RedZone engine with explicit screen evidence and safe switch transactions"; no separate decision is recorded for this module.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from redzone.tuners.adb import playback_ready

log = logging.getLogger(__name__)

YTTV_DEFAULT_PKG = "com.amazon.firetv.youtube.tv"   # Fire TV build; Google TV uses ...tvunplugged
# The Fire TV app accepts /watch/<code> (ADBTuner, Jan 2026). Some Android TV builds only take
# ?v=<code> (Home Assistant thread, Jul 2026), so that is the fallback style.
URL_STYLES = {"path": "https://tv.youtube.com/watch/{code}", "query": "https://tv.youtube.com/watch?v={code}"}
CODE_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
WATCH_RE = re.compile(r"tv\.youtube\.com/watch(?:/|\?v=)([A-Za-z0-9_-]{11})")
# id-looking fields inside dumpsys / logcat lines (mediaId=..., "videoId":"...", video_id=...)
ID_FIELD_RE = re.compile(r"(?:mediaId|media_id|videoId|video_id|contentId)[\"']?\s*[=:]\s*[\"']?([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])")

STATES = {3: "playing", 2: "paused", 6: "buffering", 8: "connecting", 1: "stopped", 0: "none", 7: "error"}

# Screens that block playback and the keys that clear them (YouTube TV + ADBTuner's list).
PROMPTS = {
    "are you still watching": ["KEYCODE_DPAD_CENTER"],
    "still there": ["KEYCODE_DPAD_CENTER"],
    "continue watching": ["KEYCODE_DPAD_CENTER"],
    "watch live": ["KEYCODE_DPAD_CENTER"],
}
# Text that means "this code/channel does not play for this account".
ERROR_PHRASES = ("isn't available", "is not available", "not available in your", "unavailable",
                 "something went wrong", "can't play", "cannot play", "playback error", "try again later",
                 "no longer available", "not part of your", "upgrade to watch", "get this channel",
                 "add this channel", "video doesn't exist", "this video is private")


@dataclass
class Session:
    """One media session from `dumpsys media_session`."""
    package: str
    state: str = "none"
    position: int | None = None
    active: bool | None = None
    title: str = ""
    subtitle: str = ""
    description: str = ""
    meta_raw: str = ""
    ids: list[str] = field(default_factory=list)   # any video-id-looking fields in the block
    speed: float | None = None


@dataclass
class Evidence:
    """Everything we could read about what is playing, plus helpers to match it."""
    playing: bool = False
    state: str = "none"
    package: str | None = None
    title: str = ""
    subtitle: str = ""
    description: str = ""
    screen: str = ""
    lounge_id: str | None = None
    ids: list[str] = field(default_factory=list)
    read_ok: bool = True        # False when the stick didn't answer at all
    position: int | None = None  # media-session playhead (ms), as dumpsys reports it

    @property
    def meta_text(self) -> str:
        return " | ".join(x for x in (self.title, self.subtitle, self.description) if x)

    @property
    def text(self) -> str:
        """All readable text, lowercased, for alias/error matching."""
        return f"{self.meta_text} | {self.screen}".lower()

    @property
    def error(self) -> str:
        """The first error phrase visible anywhere, or ''."""
        t = self.text
        return next((p for p in ERROR_PHRASES if p in t), "")

    def summary(self, limit: int = 90) -> str:
        bits = [self.state]
        if self.meta_text:
            bits.append(f"'{self.meta_text[:limit]}'")
        if self.lounge_id:
            bits.append(f"id {self.lounge_id}")
        return " ".join(bits)


# --- parsing (pure, unit-tested) ---------------------------------------------------

def _clean(s: str) -> str:
    s = (s or "").strip()
    return "" if s.lower() == "null" else s


def _split_description(desc: str) -> tuple[str, str, str]:
    """MediaDescription.toString() is 'title, subtitle, description' with 'null' for blanks.
    Titles may contain commas, subtitles rarely do, so split from the right."""
    parts = (desc or "").rsplit(", ", 2)
    while len(parts) < 3:
        parts.append("")
    return _clean(parts[0]), _clean(parts[1]), _clean(parts[2])


def parse_media_sessions(out: str) -> list[Session]:
    """Parse `dumpsys media_session` into sessions. Tolerates the Fire OS 6-8 / Android 9-11 formats."""
    sessions: list[Session] = []
    cur: Session | None = None
    for line in (out or "").splitlines():
        m = re.search(r"\bpackage=([\w.]+)", line)
        if m:
            cur = Session(package=m.group(1))
            sessions.append(cur)
            continue
        if cur is None:
            continue
        m = re.search(r"state=PlaybackState \{state=(?:[A-Z_]+\()?(-?\d+)\)?, position=(-?\d+)", line)
        if m:
            cur.state = STATES.get(int(m.group(1)), f"state{m.group(1)}")
            cur.position = int(m.group(2))
            speed = re.search(r"\bspeed=(-?\d+(?:\.\d+)?)", line)
            if speed:
                cur.speed = float(speed.group(1))
        m = re.search(r"\bactive=(true|false)", line)
        if m:
            cur.active = m.group(1) == "true"
        m = re.search(r"metadata:\s*size=\d+,\s*description=(.*)$", line)
        if m:
            cur.meta_raw = m.group(1).strip()
            cur.title, cur.subtitle, cur.description = _split_description(cur.meta_raw)
        cur.ids += [i for i in WATCH_RE.findall(line) + ID_FIELD_RE.findall(line) if i not in cur.ids]
    return sessions


def pick_session(sessions: list[Session], pkg: str | None) -> Session | None:
    """The YouTube TV session if there is one (playing first), else None."""
    mine = [s for s in sessions if pkg is None or s.package == pkg]
    if not mine:
        return None
    return next((s for s in mine if playback_ready({"state": s.state, "speed": s.speed})),
                next((s for s in mine if s.active), mine[0]))


def codes_in(text: str) -> list[str]:
    """Watch codes mentioned in any text (watch URLs or id fields), in order, without duplicates."""
    out: list[str] = []
    for c in WATCH_RE.findall(text or "") + ID_FIELD_RE.findall(text or ""):
        if c not in out:
            out.append(c)
    return out


def input_text_arg(q: str) -> str:
    """Make a query safe for `adb shell input text` (spaces become %s; no shell metacharacters)."""
    safe = re.sub(r"[^A-Za-z0-9 .-]", "", q or "").strip()
    return safe.replace(" ", "%s")


# --- stick calls (async, never raise) ------------------------------------------------

def yttv_package(stick) -> str:
    return (getattr(stick, "packages", None) or {}).get("youtubetv") or YTTV_DEFAULT_PKG


async def _shell(stick, cmd: str, timeout: float = 8.0) -> str:
    try:
        r = await stick.shell(cmd, timeout=timeout)
        return r.out if r.ok else ""
    except Exception as e:  # a fake or broken stick must never crash discovery
        log.debug("shell %r failed: %s", cmd, e)
        return ""


async def sessions(stick) -> list[Session]:
    return parse_media_sessions(await _shell(stick, "dumpsys media_session", timeout=6))


async def screen_text(stick) -> str:
    try:
        return await stick.screen_text()
    except Exception as e:
        log.debug("screen_text failed: %s", e)
        return ""


async def read_evidence(stick, pkg: str | None = None, *, with_screen: bool = True) -> Evidence:
    """Snapshot of what the stick reports for YouTube TV right now."""
    pkg = pkg or yttv_package(stick)
    out = await _shell(stick, "dumpsys media_session", timeout=6)
    s = pick_session(parse_media_sessions(out), pkg)
    ev = Evidence(read_ok=bool(out))
    if s:
        ev.playing = playback_ready({"state": s.state, "speed": s.speed})
        ev.state, ev.package, ev.position = s.state, s.package, s.position
        ev.title, ev.subtitle, ev.description, ev.ids = s.title, s.subtitle, s.description, list(s.ids)
    if with_screen:
        ev.screen = await screen_text(stick)
    return ev


async def now_playing_text(stick, pkg: str | None = None) -> str:
    """Title/subtitle the app publishes plus any screen text: use this instead of screen_text()
    when reading game titles off YouTube TV (e.g. the Sunday Ticket scan)."""
    ev = await read_evidence(stick, pkg)
    return f"{ev.meta_text} | {ev.screen}".strip(" |")


async def type_text(stick, text: str) -> bool:
    """Type into whatever field has focus (the YouTube TV search box). Returns False if nothing to type."""
    arg = input_text_arg(text)
    if not arg:
        return False
    await _shell(stick, f"input text '{arg}'", timeout=6)
    return True


async def press(stick, *keys: str) -> None:
    try:
        await stick.key(*keys)
    except Exception as e:
        log.debug("key %s failed: %s", keys, e)


async def stop_playback(stick, pkg: str | None = None, wait_s: float = 3.0, sleep=asyncio.sleep) -> None:
    """Stop whatever plays, so a later 'playing' really comes from the next tune (ADBTuner does the same)."""
    await press(stick, "KEYCODE_MEDIA_STOP")
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        s = pick_session(await sessions(stick), pkg or yttv_package(stick))
        if s is None or s.state != "playing":
            return
        await sleep(0.5)


async def clear_prompt(stick, steps: list[str]) -> bool:
    text = (await screen_text(stick)).lower()
    from redzone.discovery.providers import classify_screen, is_purchase_screen
    verdict = classify_screen(text)
    if is_purchase_screen(text) or (verdict and verdict.stops):
        steps.append("account or purchase prompt requires the owner")
        return False
    from redzone.tuners.profiles import ProfileSelectionRequired, preference, select_profile
    try:
        selected = await select_profile(stick, text, preference(stick, "youtubetv"), steps)
    except ProfileSelectionRequired:
        return False
    if selected is not None:
        return selected
    for prompt, keys in PROMPTS.items():
        if prompt in text:
            steps.append(f"cleared prompt: {prompt}")
            await press(stick, *keys)
            return True
    return False


async def wait_playing(stick, pkg: str, timeout: float, steps: list[str], sleep=asyncio.sleep) -> bool:
    """Poll the media session until it says playing; clear known prompts once after ~4s."""
    t0 = time.monotonic()
    prompt_checked = False
    while time.monotonic() - t0 < timeout:
        s = pick_session(await sessions(stick), pkg)
        if s and s.state == "playing":
            steps.append(f"playing after {time.monotonic() - t0:.1f}s")
            return True
        if not prompt_checked and time.monotonic() - t0 >= 4:
            prompt_checked = True
            await clear_prompt(stick, steps)
        await sleep(1.0)
    steps.append(f"not playing after {timeout:.0f}s")
    return False


async def open_link(stick, url: str, pkg: str) -> bool:
    try:
        r = await stick.view(url, pkg)
        return bool(r.ok)
    except Exception as e:
        log.debug("view %s failed: %s", url, e)
        return False


async def tune(stick, code: str, pkg: str | None = None, *, style: str = "path", timeout: float = 20.0,
               sleep=asyncio.sleep) -> tuple[bool, list[str]]:
    """Deep-link one watch code and wait for playback. Returns (playing, steps)."""
    pkg = pkg or yttv_package(stick)
    steps: list[str] = []
    if not CODE_RE.match(code or ""):
        return False, [f"not a watch code: {code!r}"]
    await press(stick, "KEYCODE_WAKEUP")
    url = URL_STYLES.get(style, URL_STYLES["path"]).format(code=code)
    sent = await open_link(stick, url, pkg)
    steps.append(f"deep link {style}: {'sent' if sent else 'refused'}")
    if not sent:
        return False, steps
    return await wait_playing(stick, pkg, timeout, steps, sleep=sleep), steps


# --- finding the code of whatever is playing ------------------------------------------

CAPTURE_PROBES = (
    # (label, adb shell command). Each is read-only and cheap; most are expected to find nothing on
    # some firmware, which is fine. UNVERIFIED on a real stick until the device arrives.
    ("media session", "dumpsys media_session"),
    ("activity intents", "dumpsys activity activities | grep -E 'dat=|Intent' | head -n 40"),
    ("logcat", "logcat -d -t 3000 2>/dev/null | grep -i -E 'tv.youtube.com/watch|videoid|video_id' | tail -n 60"),
    ("watch next", "content query --uri content://android.media.tv/watch_next_program "
                   "--projection title:intent_uri 2>&1 | tail -n 40"),
)


async def capture_codes(stick, exclude: set[str] | frozenset[str] = frozenset()) -> list[tuple[str, str]]:
    """Every watch code the stick lets slip about what it is playing: [(code, where)], best first.
    `exclude` drops codes we already know (e.g. the ones we deep-linked ourselves)."""
    found: list[tuple[str, str]] = []
    for label, cmd in CAPTURE_PROBES:
        out = await _shell(stick, cmd, timeout=8)
        codes = codes_in(out)
        if label == "logcat":
            codes.reverse()   # logcat is oldest-first; the latest mention is the one playing now
        for c in codes:
            if c not in exclude and all(c != f for f, _ in found):
                found.append((c, label))
    return found
