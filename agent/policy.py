"""The watchdog's judgment: what's wrong and what (if anything) to do. Stdlib only.

Two layers:
  rules()      hard cases with one right answer (service down, disk full, a known error, safe
               mode left on after its cause went away, a stale feed) — no model
  ask_model()  an error nobody has seen before: the local model (agent/llm.py) picks from the same
               fixed action set, and guard() still has the last word.

The same rules label the fine-tuning data (agent/train/make_data.py), so the model learns this
project's judgment, and the held-out test measures how well it carries over to new wordings.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

ACTIONS = ("none", "notify", "restart_service", "reconnect_stick", "prune_disk",
           # fixes (v2): each one is narrow, guarded below and rate-limited in agent/watchdog.py
           "resume_player", "clear_prompt", "retune_stick", "refresh_feed", "restart_component",
           "safe_mode_off")
STICK_ACTIONS = ("reconnect_stick", "resume_player", "clear_prompt", "retune_stick")
# Parts of the app that can be restarted on their own (POST /api/agent/fix), without a full restart.
COMPONENTS = ("feeds", "screen", "reel", "control", "supervisor")


@dataclass
class Obs:
    api_ok: bool = True
    api_fail_streak: int = 0
    log_age_s: float = 0.0            # seconds since the service last wrote its log
    live_games: int = 0
    crunch_on_screen: bool = False    # crunch time / your team late is on the TV right now
    sticks_offline: list[str] = field(default_factory=list)
    offline_min: float = 0.0          # how long the longest-offline stick has been gone
    disk_free_mb: float = 99999.0
    restarts_last_hour: int = 0
    errors: dict[str, int] = field(default_factory=dict)   # error signature -> count, last 15 min
    # v2 (defaults keep older callers working)
    on_screen: str = ""               # host of the stick the TV shows
    standby: str = ""                 # host of the hidden (standby) stick in two-stick mode
    safe_mode: bool = False           # the app stopped auto-switching after repeated errors
    safe_min: float = 0.0             # ... for this many minutes
    paused: bool = False              # a person paused auto-switching (never undo that)
    feed_stale_s: float = 0.0         # oldest live score source: seconds since it last answered

    def text(self) -> str:
        """Compact, model-friendly description (also the training prompt)."""
        d = asdict(self)
        d["errors"] = [{"sig": k[:160], "n": v} for k, v in sorted(self.errors.items(), key=lambda t: -t[1])[:8]]
        return json.dumps(d, separators=(",", ":"))


@dataclass
class Verdict:
    action: str
    why: str
    target: str = ""
    source: str = "rule"   # rule | model | guard

    def json(self) -> str:
        return json.dumps({"action": self.action, "target": self.target, "why": self.why})


# Known error signatures (normalized: numbers -> N). (pattern, category, verdict when it shows up)
KNOWN: list[tuple[str, str, str]] = [
    (r"score bug read failed", "benign", "screenshot read of the score bug failed; it retries every frame"),
    (r"CancelledError|ASGI application|WouldBlock|receive_queue|body_iterator", "benign",
     "a phone closed the live page; nothing to fix"),
    (r"Unknown child process", "benign", "asyncio noise from finished helper processes"),
    (r"Traceback \(most recent call last\)|During handling of the above exception", "benign",
     "the header of a traceback counted on its own line"),
    (r"never became active source|Two-stick mode off", "flip", "the TV didn't follow an input flip"),
    (r"reel: ", "benign", "catch-up reel housekeeping"),
    (r"nhl enrich", "source", "NHL's extra feed failed; ESPN still covers the game"),
    (r"drop adb transport|adb.*offline|not usable \(timeout", "stick", "a Fire TV stick stopped answering"),
    (r"re-loading prime.*timed out|isn't playing prime", "prime", "Prime needed a slow reload"),
    (r"still running after .*giving up", "switch", "a switch hung and was abandoned"),
    (r"poll failed", "feed", "the score feed poll failed"),
    (r"config: ignoring a setting|config: .* unreadable", "config", "a saved setting was rejected"),
    (r"No space left|Errno 28", "disk", "the disk is full"),
]

STALE_LOG_S = 240.0       # a live night with no log line this long: the service is hung
LOW_DISK_MB = 1200.0
MAX_RESTARTS_HOUR = 3
SAFE_STALE_MIN = 3.0      # safe mode on this long with no errors left: leave it
STALE_FEED_S = 180.0      # a live score source silent this long: refresh the feed


def known(sig: str) -> tuple[str, str] | None:
    for pat, cat, why in KNOWN:
        if re.search(pat, sig, re.I):
            return cat, why
    return None


def rules(o: Obs) -> Verdict | None:
    """The answer when there's a hard rule for it; None when only unknown errors are left."""
    if not o.api_ok and o.api_fail_streak >= 2:
        return Verdict("restart_service", f"the app hasn't answered {o.api_fail_streak} checks in a row")
    if o.live_games and o.log_age_s >= STALE_LOG_S:
        return Verdict("restart_service", f"games are live but the app has been silent {int(o.log_age_s)} s (hung)")
    if o.disk_free_mb < LOW_DISK_MB:
        return Verdict("prune_disk", f"only {int(o.disk_free_mb)} MB free on disk")
    cats: dict[str, int] = {}
    unknown = []
    for sig, n in o.errors.items():
        k = known(sig)
        if k is None:
            unknown.append(sig)
        else:
            cats[k[0]] = cats.get(k[0], 0) + n
    if cats.get("disk"):
        return Verdict("prune_disk", "the app hit 'no space left on device'")
    if o.sticks_offline:
        if o.offline_min >= 10:
            return Verdict("notify", f"stick {o.sticks_offline[0]} has been offline {int(o.offline_min)} min: "
                           "check its power (wall adapter, not the TV's USB)", o.sticks_offline[0])
        return Verdict("reconnect_stick", "a stick stopped answering: drop and reconnect its adb link",
                       o.sticks_offline[0])
    if cats.get("switch", 0) >= 3 and not o.crunch_on_screen:
        return Verdict("restart_service", f"{cats['switch']} switches hung in 15 min")
    if cats.get("flip", 0) >= 3:
        return Verdict("notify", "the TV keeps ignoring input flips: is HDMI-CEC (Anynet+) still on?")
    if cats.get("feed", 0) >= 10:
        return Verdict("notify", "the score feed keeps failing (internet or ESPN down?)")
    if cats.get("prime", 0) >= 4:
        return Verdict("notify", f"Prime needed {cats['prime']} slow reloads in 15 min")
    if cats.get("config"):
        return Verdict("notify", "a saved setting was rejected and ignored")
    if cats.get("source", 0) >= 50:
        return Verdict("notify", "NHL's extra feed has failed all night")
    if not unknown:
        if o.safe_mode and o.safe_min >= SAFE_STALE_MIN and not o.paused:
            return Verdict("safe_mode_off", f"safe mode has been on {int(o.safe_min)} min and its errors are gone")
        if o.live_games and o.feed_stale_s >= STALE_FEED_S:
            return Verdict("refresh_feed", f"live games but the score feed hasn't answered in {int(o.feed_stale_s)} s",
                           "feeds")
        return Verdict("none", "nothing needs doing" if not cats else "only known, harmless errors")
    return None


def guard(v: Verdict, o: Obs) -> Verdict:
    """Last word on any verdict (rule or model): never restart in the middle of crunch time unless
    the app is down, never restart in a loop, never act on a stick that isn't offline."""
    if v.action not in ACTIONS:
        return Verdict("notify", f"model gave an unknown action {v.action!r}", source="guard")
    if v.action == "restart_service":
        if o.restarts_last_hour >= MAX_RESTARTS_HOUR:
            return Verdict("notify", f"wanted a restart ({v.why}) but already restarted "
                           f"{o.restarts_last_hour}x this hour", source="guard")
        if o.crunch_on_screen and o.api_ok:
            return Verdict("notify", f"wanted a restart ({v.why}) but crunch time is on screen", source="guard")
    if v.action == "reconnect_stick" and v.target not in o.sticks_offline:
        if not o.sticks_offline:
            return Verdict("none", "no stick is offline", source="guard")
        v = Verdict("reconnect_stick", v.why, o.sticks_offline[0], v.source)
    if v.action in ("resume_player", "clear_prompt", "retune_stick"):
        known_sticks = [h for h in (o.on_screen, o.standby) if h]
        if v.target not in known_sticks:
            if not known_sticks:
                return Verdict("notify", f"wanted to {v.action} ({v.why}) but no stick is known", source="guard")
            v = Verdict(v.action, v.why, known_sticks[0], v.source)
        if v.target in o.sticks_offline:
            return Verdict("reconnect_stick", f"{v.why}; the stick is offline, so reconnect first", v.target, "guard")
        if v.action == "retune_stick" and o.crunch_on_screen and v.target == o.on_screen:
            return Verdict("notify", f"wanted to re-tune the stick on screen ({v.why}) but crunch time is on",
                           source="guard")
    if v.action == "restart_component" and v.target not in COMPONENTS:
        return Verdict("notify", f"wanted to restart {v.target!r} ({v.why}), which isn't a restartable part",
                       source="guard")
    if v.action == "safe_mode_off":
        if not o.safe_mode:
            return Verdict("none", "safe mode isn't on", source="guard")
        if o.paused:
            return Verdict("none", "a person paused auto-switching; leaving it paused", source="guard")
    return v


SYSTEM = (
    "You watch over RedZone Everything, a Mac app that switches a TV between live games. "
    "Given the app's status as JSON, pick ONE action. "
    "none: harmless noise or a one-off that recovers. notify: a person must do something or should know. "
    "restart_service: the whole app is down, hung, or keeps crashing. "
    "restart_component: one part keeps failing (target feeds, screen, reel, control or supervisor). "
    "reconnect_stick: a stick stopped answering. retune_stick: a stick's picture is frozen or stuck. "
    "resume_player: a stick's player paused by itself. clear_prompt: an 'Are you still watching?' style prompt. "
    "refresh_feed: a score feed is stale or broken while the internet works. "
    "prune_disk: disk space is the problem. safe_mode_off: safe mode is on and its cause is gone. "
    "Stick actions target a stick host from the status. "
    'Answer with JSON only: {"action": "...", "target": "", "why": "<one short sentence>"}'
)


def parse_answer(text: str) -> Verdict | None:
    """The model's JSON answer, tolerant of thinking blocks and code fences; None if unusable."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    m = re.search(r"\{.*?\}", text, flags=re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    a = str(d.get("action", "")).strip()
    if a not in ACTIONS:
        return None
    return Verdict(a, str(d.get("why", ""))[:200], str(d.get("target", "") or ""), "model")
