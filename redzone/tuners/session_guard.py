"""[sync hardening, item 4] Session safety: what is this stick's session doing, is its buffer still true, and what is
the ONE safe thing to do about it.

Nick's order (Nov 7, 07:33 UTC): handle banners, 'Who's watching', 'Are you still watching', app crash, stick reboot,
adb drop, the Prime detail-page start, the program-boundary label reset, and a UI change detected by unexpected OCR
text. Fail closed. Runbook: docs/memory/runbooks/stick-session.md (one entry per failure, with the Nov 5-7 evidence).

Two parts:
  * `classify(snap, prev)` is pure: one Snapshot of the stick (adb facts, and the OCR rows when a screen was read)
    plus the previous one -> a Verdict: what happened (`kind`), whether a buffered position is still true
    (`buffer`: valid | unknown | lost), and the one action allowed (`action`). No I/O, so every real failure is a test.
  * `SessionGuard` keeps the previous snapshot per stick, logs every non-ok verdict (numbers and kinds only: the repo
    and logs are public, so no OCR text beyond a short hash of unexpected words), counts them for the night report and
    the phone, rate-limits recoveries (no key storms), and decides whether an action may run (`allowed`).

Fail-closed rules (the whole point):
  * Anything this module can't prove keeps the buffer at "unknown" or "lost". A stick whose buffer isn't "valid" must
    never be shown as buffered, and the caller never flips onto it on the strength of an old reading.
  * Keys go only to a HIDDEN stick, except the three prompts that already cover the picture on screen (YouTube TV's
    profile picker, "Still watching?", and the auto-play prompt), and the auto-play prompt is only ever answered
    "Ask later" (Nick: never anything else, never change account settings).
  * Fire OS's own "Who's watching" (Amazon profiles), account / payment / unavailable screens, crash dialogs and any
    unexpected dialog-shaped text: no key at all, hold and alert.
  * At most `max_actions` recoveries per stick per `action_window_s`; past that, hold and alert.

Flags (default_config.yaml, both off): session_guard.enabled (observe, log, demote buffer trust, wait out the
post-tune banner before a scrub) and session_guard.act (run recoveries). Rule #1 is unchanged: nothing here flips.

Read-only probe for the hardware: `python -m redzone.tuners.session_guard <stick> --pkg youtubetv [--screen]`
prints the verdict and never sends a key; a screenshot, when asked for, is OCR'd and deleted at once.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable

log = logging.getLogger("redzone.session_guard")

DEFAULTS = {
    "enabled": False,
    "act": False,
    "banner_s": 28.0,          # Nov 6: the LIVE/title banner sat up 20-25+ s after a tune; wait this long before a scrub
    "banner_wait_max_s": 30.0,  # never wait longer than this for it
    "reboot_slack_s": 30.0,    # /proc/uptime dropping by more than this between snapshots = the stick rebooted
    "label_slack_s": 20.0,     # a label read this far below where the playhead should be = the label reset
    "label_ahead_s": 45.0,     # ...or this far above it (a re-tune to the live edge, a skipped program)
    "max_actions": 3,          # recoveries per stick per window; past that: hold + alert
    "action_window_s": 600.0,
}

YTTV = ("youtube.tv", "youtubetv", "youtube.tvunplugged")
PRIME = ("com.amazon.firebat", "com.amazon.avod", "amazonvideo")
# Fire OS's own surfaces: the launcher/home, its profile picker, the screensaver, Alexa's overlay.
FIRE_OS = ("com.amazon.tv.launcher", "com.amazon.tv.", "com.amazon.firetv.screensaver", "com.amazon.vizzini",
           "com.amazon.tv.profile")

PICKER = ("who's watching", "who is watching", "choose an account", "choose a profile", "choose your profile")
STILL = ("are you still watching", "still watching?", "still there")
AUTOPLAY = ("auto-play", "autoplay", "auto play")
ASK_LATER = ("ask later", "ask me later")
CRASH = ("isn't responding", "is not responding", "has stopped", "keeps stopping", "close app", "app info")
PLAYER_ERROR = ("something went wrong", "playback error", "an error occurred", "can't play", "cannot play",
                "unable to play", "error code", "try again later", "video unavailable", "there was a problem")
# Prime's title/detail page (Nov 7: the first CENTER only wakes the nav; #390 handles the start)
PRIME_DETAIL = ("watch now", "watch live", "resume", "start over", "more ways to watch", "watch trailer",
                "episodes", "details", "watchlist", "more details", "ways to watch")
# YouTube TV player chrome: the program window, the scrub label, and the row under the bar
ROW = ("hd", "cc", "networks", "multiview", "episodes", "more to watch", "stats", "key plays", "scores",
       "live chat", "info", "record", "library")
LIVE_BANNER = ("live", "live now", "watching live")
# Short lines shaped like a dialog's buttons that no rule above handles: a new prompt or a UI change. Never pressed.
DIALOG = ("got it", "learn more", "not now", "dismiss", "no thanks", "maybe later", "update", "update now",
          "try it", "try now", "turn on", "turn off", "allow", "don't allow", "ok", "okay", "accept", "agree",
          "continue", "skip", "cancel", "close", "introducing", "what's new", "sign in",
          "set up", "remind me later", "enable", "disable", "yes", "no")

_WIN = re.compile(r"(\d{1,2}:\d{2})\s*([AaPp][Mm])\s*[-–—~]\s*(\d{1,2}:\d{2})\s*([AaPp][Mm])")
_MMSS = re.compile(r"^(?:\d{1,2}:)?\d{1,2}:\d{2}$")

# Verdict kinds, in the order classify checks them.
OK = "ok"
ADB_DROP = "adb_drop"
REBOOT = "reboot"
APP_CRASH = "app_crash"
CRASH_DIALOG = "crash_dialog"
HOME = "fire_tv_home"
FIRE_OS_PROFILE = "fire_os_profile"
ACCOUNT = "account_screen"
PROFILE = "yttv_profile_picker"
STILL_WATCHING = "still_watching"
AUTOPLAY_PROMPT = "autoplay_prompt"
PLAYER_ERR = "player_error"
PRIME_DETAIL_PAGE = "prime_detail_page"
BANNER = "post_tune_banner"
LABEL_RESET = "label_reset"
UI_UNKNOWN = "ui_unknown"
WRONG_APP = "wrong_app"
NOT_PLAYING = "not_playing"

# Actions. Only the ones in PRESSES send keys / links; the caller supplies how (guard.recover).
NONE, WAIT, HOLD = "none", "wait", "hold"
RECONNECT = "reconnect"            # adb only: no key, no picture change
RELINK = "relink_live"             # a fresh deep link at the live edge (a known position)
RESTORE = "restore_target"         # visible stick lost its game: the existing restore path (hardening.sticks)
CLEAR_PROFILE = "clear_profile"    # screen_prompts.select_profile_ocr (configured profile only, lit-up proof)
CLEAR_STILL = "clear_still_watching"
ASK_LATER_KEY = "ask_later"        # CENTER only when 'Ask later' is the one lit-up button
PRIME_START = "prime_start"        # re-run the Prime tuner's start (#390, tuners.prime_wake_nav)
REANCHOR = "reanchor_label"        # re-read the label against the new program window; no keys
PRESSES = {RELINK, RESTORE, CLEAR_PROFILE, CLEAR_STILL, ASK_LATER_KEY, PRIME_START}
# The prompts that already cover the picture: answering them on the stick on screen changes nothing Nick could watch.
ON_SCREEN_OK = {CLEAR_PROFILE, CLEAR_STILL, ASK_LATER_KEY, RESTORE}

ALERT = {CRASH_DIALOG, FIRE_OS_PROFILE, ACCOUNT, UI_UNKNOWN, REBOOT, APP_CRASH}   # the phone shows these loud


def _cfg(cfg, key: str):
    v = cfg.get(f"session_guard.{key}") if cfg is not None and hasattr(cfg, "get") else None
    return DEFAULTS[key] if v is None else v


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9':?\- ]+", " ", (s or "").lower().replace("’", "'"))).strip()


@dataclass(frozen=True)
class Row:
    """One OCR line, normalized top-left box (screen_ocr.TextBox fields)."""
    text: str
    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0
    conf: float = 1.0
    lit: bool | None = None   # this line is drawn highlighted (focused), when the pixels were read


@dataclass
class Snapshot:
    """What one look at a stick found. Every field may be unknown (None): unknown is never read as fine."""
    host: str = ""
    wall: float = 0.0
    reachable: bool | None = None      # adb answered at all
    breaker_open: bool = False         # the stick's adb breaker is open (STICK_HUNG)
    uptime_s: float | None = None      # /proc/uptime
    pid: int | None = None             # pidof the expected app
    package: str = ""                  # foreground package
    want_pkg: str = ""                 # the app the session should be in (youtubetv / prime package)
    state: str | None = None           # media_session state: playing | paused | buffering | ...
    since_tune_s: float | None = None  # seconds since this stick was last tuned
    rows: list[Row] | None = None      # OCR rows, when the screen was read (None = not read)
    expect: str = ""                   # what the caller expects on screen: "" | "overlay" | "player"
    label_s: float | None = None       # overlay time label (seconds since program start), when read
    window: str = ""                   # program window text '9:00 PM - 11:00 PM', when read

    @property
    def text(self) -> str:
        return " | ".join(_norm(r.text) for r in self.rows or [])


@dataclass
class Verdict:
    kind: str
    action: str
    buffer: str                         # valid | unknown | lost: may a buffered position still be trusted?
    reason: str = ""
    wait_s: float = 0.0                 # with WAIT: how long
    unknown_hash: str = ""              # UI_UNKNOWN: short hash of the unexpected words (never the words)

    @property
    def ok(self) -> bool:
        return self.kind == OK

    @property
    def alert(self) -> bool:
        return self.kind in ALERT


def _has(text: str, words: Iterable[str]) -> bool:
    return any(w in text for w in words)


def _lines(snap: Snapshot) -> list[str]:
    return [_norm(r.text) for r in snap.rows or []]


def _is_yttv(pkg: str) -> bool:
    p = (pkg or "").lower()
    return any(k in p for k in YTTV)


def _is_prime(pkg: str) -> bool:
    p = (pkg or "").lower()
    return any(k in p for k in PRIME)


def _is_fire_os(pkg: str) -> bool:
    p = (pkg or "").lower()
    return any(p.startswith(k) for k in FIRE_OS)


def _same_app(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return a == b or a.startswith(b) or b.startswith(a) or (_is_yttv(a) and _is_yttv(b)) or (
        _is_prime(a) and _is_prime(b))


def _known_line(line: str) -> bool:
    """Lines the YouTube TV / Prime player chrome normally shows: times, the window, the row, the LIVE badge."""
    return bool(_MMSS.match(line) or _WIN.search(line) or line in ROW or line in LIVE_BANNER or not line)


def unexpected(lines: list[str]) -> list[str]:
    """Short, button-shaped lines (<= 4 words) that are one of the DIALOG words: a prompt we have no rule for."""
    out = []
    for ln in lines:
        if _known_line(ln):
            continue
        if len(ln.split()) <= 4 and (ln in DIALOG or ln.rstrip("!.") in DIALOG):
            out.append(ln)
    return out


def _hash(words: list[str]) -> str:
    return hashlib.sha1("|".join(sorted(set(words))).encode()).hexdigest()[:8] if words else ""


def _lost_action(hidden_hint: bool) -> str:
    return RELINK if hidden_hint else RESTORE


def classify(snap: Snapshot, prev: Snapshot | None = None, cfg=None, *, hidden: bool = True,
             last_label: tuple[float, float, str] | None = None) -> Verdict:
    """One snapshot (and the one before it) -> what happened and the one allowed action. Pure.
    `last_label` = (wall, label_s, window) of the last good overlay label read on this stick and channel."""
    # 1. adb: no answer means no evidence about anything else
    if snap.breaker_open or snap.reachable is False:
        return Verdict(ADB_DROP, RECONNECT, "unknown", "adb not answering: the picture can't be checked")
    # 2. reboot: uptime went backwards
    if prev is not None and prev.uptime_s is not None and snap.uptime_s is not None \
            and snap.uptime_s + float(_cfg(cfg, "reboot_slack_s")) < prev.uptime_s:
        return Verdict(REBOOT, _lost_action(hidden), "lost",
                       f"uptime {prev.uptime_s:.0f}s -> {snap.uptime_s:.0f}s: the stick rebooted")
    text = snap.text
    lines = _lines(snap)
    # 3. a crash dialog over anything: never pressed (its buttons close or wait on the app)
    if snap.rows is not None and _has(text, CRASH):
        return Verdict(CRASH_DIALOG, HOLD, "lost", "a system crash / not-responding dialog is up")
    # 4. app crash: same app, new process (or no process) since the last look
    if snap.want_pkg and prev is not None and prev.pid and _same_app(prev.package, snap.want_pkg):
        if snap.pid is None and not _same_app(snap.package, snap.want_pkg):
            return Verdict(APP_CRASH, _lost_action(hidden), "lost", "the app's process is gone")
        if snap.pid is not None and snap.pid != prev.pid:
            return Verdict(APP_CRASH, _lost_action(hidden), "lost",
                           "the app restarted (new process): the player starts again at the live edge")
    # 5. Fire OS surfaces: its own profile picker is never ours to answer
    if _is_fire_os(snap.package):
        if snap.rows is not None and _has(text, PICKER):
            return Verdict(FIRE_OS_PROFILE, HOLD, "lost", "Fire OS's own profile picker (not YouTube TV's)")
        return Verdict(HOME, _lost_action(hidden), "lost", f"Fire OS is in front ({snap.package})")
    # 6. account / payment / unavailable
    if snap.rows is not None and text:
        try:
            from redzone.discovery.providers import classify_screen, is_purchase_screen
            v = classify_screen(text)
            if is_purchase_screen(text) or (v is not None and getattr(v, "stops", False)):
                return Verdict(ACCOUNT, HOLD, "lost", "account, payment or unavailable screen: never pressed")
        except Exception:   # the provider tables are optional here; the words below still catch the usual ones
            if _has(text, ("subscribe", "sign up", "start free trial", "buy", "rent", "upgrade")):
                return Verdict(ACCOUNT, HOLD, "lost", "account or payment screen: never pressed")
    if snap.rows is not None and any(ln in ("unavailable", "not available", "content unavailable") for ln in lines):
        return Verdict(ACCOUNT, HOLD, "lost", "the page says UNAVAILABLE (Nov 7 Prime standby): never pressed")
    # 7. the prompts that cover the picture
    if snap.rows is not None and _has(text, PICKER):
        return Verdict(PROFILE, CLEAR_PROFILE, "lost", "YouTube TV's profile picker: the player restarted")
    if snap.rows is not None and _has(text, STILL):
        return Verdict(STILL_WATCHING, CLEAR_STILL, "unknown",
                       "'Still watching?': the picture is paused behind it, the buffer grew by an unknown amount")
    if snap.rows is not None and _has(text, AUTOPLAY):
        if any(_has(ln, ASK_LATER) for ln in lines):
            return Verdict(AUTOPLAY_PROMPT, ASK_LATER_KEY, "unknown", "auto-play prompt: only 'Ask later' is pressed")
        return Verdict(AUTOPLAY_PROMPT, HOLD, "unknown", "auto-play prompt with no 'Ask later' button read: hold")
    if snap.rows is not None and _has(text, PLAYER_ERROR):
        return Verdict(PLAYER_ERR, _lost_action(hidden), "lost", "the player shows an error")
    # 8. wrong app in front
    if snap.want_pkg and snap.package and not _same_app(snap.package, snap.want_pkg):
        return Verdict(WRONG_APP, _lost_action(hidden), "lost", f"{snap.package} is in front, not {snap.want_pkg}")
    # 9. Prime's detail page instead of the player (#390 owns the start keys)
    if _is_prime(snap.package or snap.want_pkg) and snap.state != "playing" and snap.rows is not None \
            and sum(1 for w in PRIME_DETAIL if w in text) >= 2:
        return Verdict(PRIME_DETAIL_PAGE, PRIME_START if hidden else HOLD, "lost",
                       "Prime's detail page, not the player (the first CENTER may only wake the nav)")
    # 10. anything dialog-shaped we have no rule for: a new prompt or a UI change. Never pressed.
    odd = unexpected(lines) if snap.rows is not None else []
    if odd:
        return Verdict(UI_UNKNOWN, HOLD, "unknown",
                       f"{len(odd)} unexpected button-like line(s) on screen: UI changed? nothing pressed",
                       unknown_hash=_hash(odd))
    if snap.rows is not None and snap.expect == "overlay" and lines \
            and not any(_WIN.search(ln) or _MMSS.match(ln) or ln in ROW for ln in lines):
        return Verdict(UI_UNKNOWN, HOLD, "unknown", "expected the player overlay, read none of its parts",
                       unknown_hash=_hash([ln for ln in lines if not _known_line(ln)][:6]))
    # 11. the post-tune LIVE/title banner (Nov 6): looks like the overlay, keys do nothing on it
    banner_s = float(_cfg(cfg, "banner_s"))
    if _is_yttv(snap.package or snap.want_pkg) and snap.since_tune_s is not None and snap.since_tune_s < banner_s:
        # Screen read and nothing drawn (the video is DRM-black, no UI): the banner is gone, go now. Unread: assume it
        # is up for the whole window. Text but no scrub label: the banner (or an overlay) is still up.
        if snap.rows is None or (lines and not any(_MMSS.match(ln) for ln in lines)):
            return Verdict(BANNER, WAIT, "valid" if snap.state == "playing" else "unknown",
                           "inside the post-tune banner window: scrub keys would do nothing",
                           wait_s=round(min(banner_s - snap.since_tune_s, float(_cfg(cfg, "banner_wait_max_s"))), 1))
    # 12. the program-boundary label reset (Nov 7 ~1:00 AM): the label restarts at a new program
    if snap.label_s is not None and last_label is not None:
        lw, ll, lwin = last_label
        expect_s = ll + max(0.0, snap.wall - lw)
        if (snap.window and lwin and snap.window != lwin) or snap.label_s < expect_s - float(_cfg(cfg, "label_slack_s")):
            return Verdict(LABEL_RESET, REANCHOR, "unknown",
                           f"label {snap.label_s:.0f}s, expected ~{expect_s:.0f}s: new program (label reset)")
        if snap.label_s > expect_s + float(_cfg(cfg, "label_ahead_s")):
            return Verdict(LABEL_RESET, REANCHOR, "unknown",
                           f"label {snap.label_s:.0f}s ahead of ~{expect_s:.0f}s: the position jumped (live edge?)")
    # 13. not playing
    if snap.state is None:
        return Verdict(NOT_PLAYING, WAIT, "unknown", "playback state unread", wait_s=2.0)
    if snap.state != "playing":
        return Verdict(NOT_PLAYING, WAIT if snap.state in ("buffering", "connecting") else _lost_action(hidden),
                       "unknown", f"player is {snap.state}", wait_s=3.0)
    return Verdict(OK, NONE, "valid", "playing")


# --- the stateful part -----------------------------------------------------------------------------------------------

@dataclass
class Event:
    t: float
    host: str
    kind: str
    action: str
    buffer: str
    hidden: bool
    done: str = ""          # what happened to the action: allowed | blocked:<why> | ran:<ok> | observe
    reason: str = ""
    unknown_hash: str = ""

    def row(self) -> dict:
        d = asdict(self)
        d["t"] = round(self.t, 1)
        return d


@dataclass
class SessionGuard:
    """Per-stick memory, the action gate and the event log. `cfg` is the app Config (or None = defaults)."""
    cfg: Any = None
    clock: Callable[[], float] = time.time
    on_event: Callable[[dict], None] | None = None
    prev: dict[str, Snapshot] = field(default_factory=dict)
    labels: dict[str, tuple[float, float, str]] = field(default_factory=dict)   # host -> last good label read
    actions: dict[str, deque] = field(default_factory=dict)
    events: deque = field(default_factory=lambda: deque(maxlen=500))
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def enabled(self) -> bool:
        return bool(_cfg(self.cfg, "enabled"))

    @property
    def acting(self) -> bool:
        return self.enabled and bool(_cfg(self.cfg, "act"))

    def observe(self, snap: Snapshot, *, hidden: bool) -> Verdict:
        """Classify against this stick's previous look, remember it, and log anything not ok."""
        v = classify(snap, self.prev.get(snap.host), self.cfg, hidden=hidden, last_label=self.labels.get(snap.host))
        # keep the last values that were actually read (an adb drop must not erase the uptime a reboot is judged by)
        old = self.prev.get(snap.host)
        if old is not None and snap.reachable is not True:
            snap.uptime_s = snap.uptime_s if snap.uptime_s is not None else old.uptime_s
            snap.pid = snap.pid if snap.pid is not None else old.pid
            snap.package = snap.package or old.package
        self.prev[snap.host] = snap
        if v.kind in (REBOOT, APP_CRASH, HOME, PROFILE, LABEL_RESET, WRONG_APP, PLAYER_ERR):
            self.labels.pop(snap.host, None)   # the old label anchor no longer applies
        elif v.ok and snap.label_s is not None:
            self.labels[snap.host] = (snap.wall, snap.label_s, snap.window)
        if not v.ok and v.kind != BANNER:   # a banner is expected after every tune: polled, not an event
            self._log(snap.host, v, hidden, "observe" if not self.acting else "")
        return v

    def note_tune(self, host: str) -> None:
        """Our own tune just ran on this stick: its process / label anchors are no longer comparable."""
        old = self.prev.get(host)
        if old is not None:
            old.pid = None
        self.labels.pop(host, None)

    def forget_label(self, host: str) -> None:
        self.labels.pop(host, None)

    def note_label(self, host: str, wall: float, label_s: float, window: str = "") -> None:
        """A good label read made elsewhere (the scrub's own read): the anchor for the next boundary check."""
        self.labels[host] = (wall, label_s, window)

    def allowed(self, v: Verdict, *, hidden: bool, host: str = "") -> tuple[bool, str]:
        """May this verdict's action run now? (False, why) for anything not provably safe."""
        if v.action in (NONE, HOLD, WAIT, REANCHOR, RECONNECT):
            return True, "no keys"
        if not self.acting:
            return False, "session_guard.act is off"
        if v.action in PRESSES and not hidden and v.action not in ON_SCREEN_OK:
            return False, "the stick is on screen: keys only on the hidden stick"
        now = self.clock()
        q = self.actions.setdefault(host, deque())
        win = float(_cfg(self.cfg, "action_window_s"))
        while q and now - q[0] > win:
            q.popleft()
        if len(q) >= int(_cfg(self.cfg, "max_actions")):
            return False, f"{len(q)} recoveries in {win:.0f}s: holding (no key storms)"
        return True, "ok"

    async def recover(self, v: Verdict, *, host: str, hidden: bool,
                      handlers: dict[str, Callable[[], Any]]) -> bool | None:
        """Run the verdict's action through the caller's handler for it (relink_live, restore_target, clear_profile,
        clear_still_watching, ask_later, prime_start, reconnect, reanchor_label). None = nothing ran."""
        ok, why = self.allowed(v, hidden=hidden, host=host)
        fn = handlers.get(v.action)
        if not ok or fn is None:
            self._log(host, v, hidden, f"blocked:{why if not ok else 'no handler'}")
            return None
        if v.action in (NONE, HOLD):
            return None
        if v.action == WAIT:
            await asyncio.sleep(max(0.0, v.wait_s))
            return True
        if v.action in PRESSES:
            self.actions.setdefault(host, deque()).append(self.clock())
        try:
            res = fn()
            if asyncio.iscoroutine(res):
                res = await res
            done = bool(getattr(res, "ok", res))
        except Exception as e:   # a handler bug must never take the switch path down
            log.warning("session guard: %s on %s failed: %r", v.action, host, e)
            done = False
        self._log(host, v, hidden, f"ran:{'ok' if done else 'failed'}")
        return done

    def banner_wait(self, since_tune_s: float | None) -> float:
        """Seconds to wait before scrubbing a fresh YouTube TV tune (0 = go): the banner looks like the overlay."""
        if not self.enabled or since_tune_s is None:
            return 0.0
        left = float(_cfg(self.cfg, "banner_s")) - since_tune_s
        return round(max(0.0, min(left, float(_cfg(self.cfg, "banner_wait_max_s")))), 1)

    def _log(self, host: str, v: Verdict, hidden: bool, done: str) -> None:
        ev = Event(self.clock(), host, v.kind, v.action, v.buffer, hidden, done, v.reason[:120], v.unknown_hash)
        self.events.append(ev)
        self.counts[v.kind] = self.counts.get(v.kind, 0) + 1
        (log.warning if v.alert else log.info)("session guard: %s on %s -> %s (%s) %s", v.kind, host, v.action,
                                                v.buffer, done)
        if self.on_event is not None:
            try:
                self.on_event(ev.row())
            except Exception:
                log.exception("session guard: on_event")

    def summary(self) -> dict:
        """For the night report and the phone: counts by kind, alerts, and the last few events (numbers and kinds)."""
        evs = list(self.events)
        return {"counts": dict(sorted(self.counts.items())),
                "alerts": sum(1 for e in evs if e.kind in ALERT),
                "blocked": sum(1 for e in evs if e.done.startswith("blocked")),
                "recoveries": sum(1 for e in evs if e.done.startswith("ran:")),
                "recoveries_ok": sum(1 for e in evs if e.done == "ran:ok"),
                "last": [e.row() for e in evs[-10:]]}


# --- reading a stick (adb facts only unless a screen read is asked for) ------------------------------------------------

async def _shell(stick, cmd: str, timeout: float = 4.0):
    try:
        return await stick.shell(cmd, timeout=timeout)
    except Exception:
        return None


async def snapshot(stick, want_pkg: str = "", *, since_tune_s: float | None = None, read_screen: bool = False,
                   expect: str = "", clock: Callable[[], float] = time.time) -> Snapshot:
    """One look at a stick. adb facts always (uptime, pid, foreground, playback state); the screen only when
    `read_screen` (one screencap -> local OCR -> the PNG deleted by screen_prompts.read_frame's temp dir)."""
    snap = Snapshot(host=getattr(stick, "host", ""), wall=clock(), want_pkg=want_pkg, since_tune_s=since_tune_s,
                    expect=expect)
    br = getattr(stick, "breaker", None)
    snap.breaker_open = bool(br is not None and getattr(br, "state", "closed") == "open")
    if snap.breaker_open:
        return snap
    up = await _shell(stick, "cat /proc/uptime", 3.0)
    if up is None or not getattr(up, "ok", False):
        snap.reachable = False
        return snap
    snap.reachable = True
    try:
        snap.uptime_s = float(up.out.split()[0])
    except (ValueError, IndexError, AttributeError):
        snap.uptime_s = None
    try:
        snap.package = ((await stick.foreground()) or "").split("/")[0]
    except Exception:
        snap.package = ""
    pkg = want_pkg or snap.package
    if pkg and "." in pkg:
        r = await _shell(stick, f"pidof {pkg}", 3.0)
        try:
            snap.pid = int((r.out or "").split()[0]) if r is not None and r.ok and r.out.strip() else None
        except (ValueError, IndexError):
            snap.pid = None
    try:
        snap.state = (await stick.playback(want_pkg or None)).get("state")
    except Exception:
        snap.state = None
    if read_screen:
        try:
            from redzone.tuners.screen_prompts import lit, read_frame
            fr = await read_frame(stick)
            if fr.ocr.read_ok:
                boxes = list(fr.ocr.observations)
                hot = lit(fr.luma, boxes, pct=50) if fr.luma is not None else None
                snap.rows = [Row(b.text, b.x, b.y, b.width, b.height, b.confidence,
                                 None if fr.luma is None else (i == hot)) for i, b in enumerate(boxes)]
                for r in snap.rows:
                    m = _WIN.search(r.text)
                    if m:
                        snap.window = m.group(0)
        except Exception:
            log.debug("session guard: screen read failed", exc_info=True)
            snap.rows = None
    return snap


def ask_later_lit(snap: Snapshot) -> bool:
    """True only if the screen was read with pixels and 'Ask later' is THE highlighted line (CENTER is then safe)."""
    lit_rows = [r for r in snap.rows or [] if r.lit]
    return len(lit_rows) == 1 and _has(_norm(lit_rows[0].text), ASK_LATER)


async def settle_banner(guard: "SessionGuard", stick, want_pkg: str, since_tune: Callable[[], float | None], *,
                        still_hidden: Callable[[], bool] = lambda: True, poll_s: float = 2.0,
                        look: Callable[..., Any] | None = None) -> Verdict:
    """Before a scrub's first key: look at the screen (no key), and while the post-tune banner is up, wait and look
    again, never past banner_wait_max_s. Returns the verdict of the last look (adb facts + screen), which the caller
    must see `ok` before pressing anything. When the screen can't be read (OCR off), the whole window is waited."""
    look = look or snapshot
    waited, cap = 0.0, float(_cfg(guard.cfg, "banner_wait_max_s"))
    while True:
        snap = await look(stick, want_pkg, since_tune_s=since_tune(), read_screen=True)
        v = guard.observe(snap, hidden=True)
        if v.kind != BANNER or waited >= cap or not still_hidden():
            return v
        step = v.wait_s if snap.rows is None else min(poll_s, v.wait_s)
        step = max(0.0, min(step, cap - waited))
        await asyncio.sleep(step)
        waited += step
        if step <= 0:
            return v


# --- read-only probe ------------------------------------------------------------------------------------------------

async def _probe(host: str, pkg: str, screen: bool, looks: int, gap: float) -> list[dict]:
    from redzone.tuners.adb import PRIME_PACKAGES, YTTV_PACKAGES, Stick
    want = {"youtubetv": YTTV_PACKAGES[0], "prime": PRIME_PACKAGES[0]}.get(pkg, pkg)
    stick = Stick(host)
    guard = SessionGuard()
    out = []
    try:
        for i in range(max(1, looks)):
            snap = await snapshot(stick, want, read_screen=screen)
            v = guard.observe(snap, hidden=True)
            out.append({"look": i, "kind": v.kind, "action": v.action, "buffer": v.buffer, "reason": v.reason,
                        "reachable": snap.reachable, "uptime_s": snap.uptime_s, "pid": snap.pid,
                        "package": snap.package, "state": snap.state, "screen_read": snap.rows is not None,
                        "ask_later_lit": ask_later_lit(snap) if v.kind == AUTOPLAY_PROMPT else None,
                        "unknown_hash": v.unknown_hash})
            if i + 1 < looks:
                await asyncio.sleep(gap)
    finally:
        await stick.close()
    return out


def main() -> None:
    """Read-only: prints what the guard sees on a stick. Never sends a key or a link."""
    import argparse
    import json
    p = argparse.ArgumentParser(description=main.__doc__)
    p.add_argument("host")
    p.add_argument("--pkg", default="youtubetv", help="youtubetv | prime | a package name")
    p.add_argument("--screen", action="store_true", help="also read the screen (screencap -> OCR -> deleted)")
    p.add_argument("--looks", type=int, default=1, help="look this many times (a reboot/crash shows across looks)")
    p.add_argument("--gap", type=float, default=10.0)
    a = p.parse_args()
    print(json.dumps(asyncio.run(_probe(a.host, a.pkg, a.screen, a.looks, a.gap)), indent=2))


if __name__ == "__main__":
    main()
