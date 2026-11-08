"""The watchdog's judgment: what's wrong and what (if anything) to do. Stdlib only.

Two layers:
  rules()      hard cases with one right answer (service down, disk full, a known error) — no model
  ask_model()  an error nobody has seen before: the local model (agent/llm.py) picks from the same
               fixed action set, and guard() still has the last word.

The same rules label the fine-tuning data (agent/train/make_data.py), so the model learns this
project's judgment, and the held-out test measures how well it carries over to new wordings.
Why: The watchdog's decide step: what is wrong and what to do (watchdog agent commit).
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

ACTIONS = ("none", "notify", "restart_service", "reconnect_stick", "prune_disk",
           # fixes the app runs for the watchdog (POST /api/agent/fix, redzone/platform/agent_fix.py):
           # each one narrow, guarded below and rate-limited in agent/watchdog.py and in the app
           "resume_player", "clear_prompt", "retune_stick", "refresh_feed", "restart_component",
           "safe_mode_off")
APP_FIXES = ACTIONS[5:]
STICK_FIXES = ("resume_player", "clear_prompt", "retune_stick")
COMPONENTS = ("feeds", "fastcast")      # parts the app restarts on their own

# The 13 fault kinds (the same text the Kaggle teacher uses: agent/distill/teacher.py KIND_HELP on
# kaggle-inputs, agent/train/make_data.py NOVEL on agent-training). Every verdict names one.
KIND_HELP = {
    "core_loop": "the app's own decision/score loop crashed with a Python exception (a bug in redzone code)",
    "component": "one part other than the core loop (score feeds, screen reader, catch-up clips, stick control, "
                 "supervisor) crashed or stopped responding",
    "stick_link": "the adb network link to a Fire TV stick failed (refused, unauthorized, reset, host down)",
    "storage": "disk or database storage is failing (full, quota, I/O error)",
    "person": "something only a person can fix: sign-in, PIN, subscription, payment, a dialog on the TV, "
              "someone using the remote",
    "upstream": "one score source (ESPN, NHL, MLB API, fastcast websocket) failed, timed out or rate-limited",
    "network": "the Mac itself is off the internet: every source fails at once, DNS gone, no route",
    "cosmetic": "harmless warning noise (deprecations, slow-task warnings, favicon 404s, cache misses)",
    "resource": "the app process running out of memory, file descriptors, threads or workers, or its event "
                "loop wedged",
    "permission": "macOS or the file system refusing access: Screen Recording, Apple events, keychain, "
                  "quarantine, read-only files",
    "prompt": "a streaming app shows an 'are you still watching / still there' prompt on a stick "
              "(say 'standby' or 'hidden' stick, or 'on screen')",
    "paused": "a stick's player paused by itself (lost HDMI, buffering stop, paused in background) "
              "(say 'standby' or 'hidden' stick, or 'on screen')",
    "frozen": "a stick answers adb but its picture is stuck: frozen frame, endless spinner, black screen, "
              "error page (say 'standby' or 'hidden' stick, or 'on screen')",
}
KINDS = tuple(KIND_HELP)
# What each kind may lead to (after guard); the first is the usual answer.
KIND_ACTIONS = {
    "core_loop": ("restart_service", "safe_mode_off", "notify"), "component": ("restart_component", "notify"),
    "stick_link": ("reconnect_stick", "notify", "none"), "storage": ("prune_disk",),
    "person": ("notify",), "upstream": ("refresh_feed", "restart_component", "notify"),
    "network": ("notify", "none"), "cosmetic": ("none",), "resource": ("restart_service", "notify"),
    "permission": ("notify",), "prompt": ("clear_prompt", "retune_stick", "notify"),
    "paused": ("resume_player", "clear_prompt", "retune_stick", "notify"),
    "frozen": ("retune_stick", "notify"),
}
# A dead screen the app's own watch didn't fix: try these in order; one that failed is skipped.
SCREEN_LADDER = {"paused": ("resume_player", "clear_prompt", "retune_stick"),
                 "prompt": ("clear_prompt", "retune_stick"),
                 "frozen": ("retune_stick",), "stopped": ("retune_stick",)}
# Obs fields the trained model never saw: kept out of Obs.text(), its prompt.
NEW_FIELDS = ("launchd_exit", "on_screen", "screen", "screen_dead_s", "safe_mode", "safe_min", "paused",
              "feed_stale_s", "net_down", "components", "person", "failed", "trying")


@dataclass
class Obs:
    api_ok: bool = True
    api_fail_streak: int = 0
    log_age_s: float = 0.0            # seconds since the service last wrote its log
    live_games: int = 0
    crunch_on_screen: bool = False    # crunch time / your team late is on the TV right now
    sticks_offline: list[str] = field(default_factory=list)
    offline_min: float = 0.0          # how long the longest-offline stick has been gone
    away: bool = False                # the Mac isn't on the home network: sticks can't answer, not a fault
    route_issue: str = ""             # at home, but traffic to the sticks goes into a tunnel (Tailscale)
    disk_free_mb: float = 99999.0
    restarts_last_hour: int = 0
    errors: dict[str, int] = field(default_factory=dict)   # error signature -> count, last 15 min
    launchd_exit: int | None = None   # the app's launchd job: last exit code (read only while the API is down)
    # From /api/health "agent" (redzone/platform/agent_fix.py); defaults keep older callers working.
    on_screen: str = ""               # host of the stick the TV shows
    screen: str = ""                  # its screen is dead: paused | prompt | frozen | stopped ("" = fine)
    screen_dead_s: float = 0.0        # ... for this long
    safe_mode: bool = False           # the app stopped auto-switching after repeated errors
    safe_min: float = 0.0             # ... for this many minutes
    paused: bool = False              # a person paused auto-switching (never undone here)
    feed_stale_s: float = 0.0         # the stalest live game: seconds since its score data moved
    net_down: bool = False            # every score source is failing at once
    components: list[str] = field(default_factory=list)   # parts that crashed (COMPONENTS)
    person: str = ""                  # a fault only a person can fix (TV sign-in...)
    failed: list[str] = field(default_factory=list)       # fixes already tried on this problem that didn't help
    trying: list[str] = field(default_factory=list)       # fixes that ran and aren't judged yet

    def text(self) -> str:
        """Compact, model-friendly description (also the training prompt)."""
        d = asdict(self)
        for k in NEW_FIELDS:              # not in the trained prompt; the rules handle them
            d.pop(k, None)
        d["errors"] = [{"sig": k[:160], "n": v} for k, v in sorted(self.errors.items(), key=lambda t: -t[1])[:8]]
        return json.dumps(d, separators=(",", ":"))


@dataclass
class Verdict:
    action: str
    why: str
    target: str = ""
    source: str = "rule"   # rule | model | guard
    kind: str = ""         # one of KINDS (journal and fix stats; not part of the model's answer)

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
    (r"Too many open files|MemoryError|Cannot allocate memory|can't start new thread", "resource",
     "the app ran out of memory, files or threads"),
    (r"PermissionError|Permission denied|Operation not permitted|Screen Recording|Apple ?events|keychain",
     "permission", "macOS refused the app access to something"),
]
# Known-error category -> fault kind (KINDS).
CAT_KIND = {"benign": "cosmetic", "flip": "person", "source": "upstream", "stick": "stick_link", "prime": "upstream",
            "switch": "core_loop", "feed": "upstream", "config": "person", "disk": "storage",
            "resource": "resource", "permission": "permission"}

STALE_LOG_S = 240.0       # a live night with no log line this long: the service is hung
LOW_DISK_MB = 1200.0
MAX_RESTARTS_HOUR = 3
SCREEN_DEAD_S = 90.0      # the app's own visible-stick watch acts at 8 s; past this it didn't work
SAFE_STALE_MIN = 3.0      # safe mode on this long with no errors left: leave it
STALE_FEED_S = 180.0      # a live game's score data still this long: refresh the feed


def known(sig: str) -> tuple[str, str] | None:
    for pat, cat, why in KNOWN:
        if re.search(pat, sig, re.I):
            return cat, why
    return None


# launchd exit codes a restart can't fix: a person (or a deploy) has to change something first.
LAUNCHD_FATAL = {
    78: "launchd can't start the app (exit 78, EX_CONFIG): macOS privacy blocks a path under ~/Documents. "
        "Re-install the job (scripts/service.py install) and check Full Disk Access for the venv's python3.12",
    126: "launchd can't run the app's python (exit 126, not executable): rebuild the venv (scripts/deploy.py)",
    127: "launchd can't find the app's python (exit 127): the venv is missing; re-run scripts/deploy.py",
}


def rules(o: Obs) -> Verdict | None:
    """The answer when there's a hard rule for it; None when only unknown errors are left. Every
    verdict names its fault kind (KINDS)."""
    v = _rules(o)
    if v is not None and not v.kind:
        v.kind = _kind_of(v, o)
    return v


def _kind_of(v: Verdict, o: Obs) -> str:
    if v.action == "restart_service":
        return "core_loop"
    if v.action == "prune_disk":
        return "storage"
    if v.action == "reconnect_stick" or (v.target and v.target in o.sticks_offline):
        return "stick_link"
    return "cosmetic" if v.action == "none" else "person"


def _screen_fix(o: Obs) -> Verdict | None:
    """The stick on screen has been dead longer than the app's own watch should take."""
    if not (o.screen and o.on_screen and o.screen_dead_s >= SCREEN_DEAD_S) or o.paused:
        return None
    kind = o.screen if o.screen in ("paused", "prompt", "frozen") else "frozen"
    ladder = SCREEN_LADDER.get(o.screen, ("retune_stick",))
    busy = [f for f in STICK_FIXES if f in o.trying]
    if busy:
        return Verdict("none", f"waiting to see if {busy[0]} brought the screen back", o.on_screen, kind=kind)
    for fix in ladder:
        if fix not in o.failed:
            return Verdict(fix, f"the stick on screen has been {o.screen} {int(o.screen_dead_s)} s", o.on_screen,
                           kind=kind)
    return Verdict("notify", f"the stick on screen has been {o.screen} {int(o.screen_dead_s)} s and "
                   f"{', '.join(ladder)} didn't help: check the TV", o.on_screen, kind=kind)


def _rules(o: Obs) -> Verdict | None:
    if not o.api_ok and o.launchd_exit in LAUNCHD_FATAL:
        # Restarting would just fail the same way (and burn the restart cap): say what to fix.
        return Verdict("notify", LAUNCHD_FATAL[o.launchd_exit],
                       kind="permission" if o.launchd_exit == 78 else "core_loop")
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
    if o.route_issue:
        return Verdict("notify", o.route_issue, kind="network")
    if o.person:
        return Verdict("notify", f"needs you: {o.person}", kind="person")
    if cats.get("permission"):
        return Verdict("notify", "macOS refused the app access (Screen Recording, Apple events, keychain or "
                       "a file): check System Settings > Privacy", kind="permission")
    if o.sticks_offline and not o.away:
        if o.offline_min >= 10:
            return Verdict("notify", f"stick {o.sticks_offline[0]} has been offline {int(o.offline_min)} min: "
                           "check its power (wall adapter, not the TV's USB)", o.sticks_offline[0])
        return Verdict("reconnect_stick", "a stick stopped answering: drop and reconnect its adb link",
                       o.sticks_offline[0])
    if o.net_down and o.live_games and not o.away:
        return Verdict("notify", "every score source is failing at once: is the Mac's internet down?", kind="network")
    v = _screen_fix(o)
    if v is not None:
        return v
    if o.components:
        part = o.components[0]
        if "restart_component" in o.trying:
            return Verdict("none", f"waiting to see if restarting {part} worked", part, kind="component")
        if "restart_component" in o.failed:
            return Verdict("notify", f"the {part} part keeps crashing; restarting it didn't help", part,
                           kind="component")
        return Verdict("restart_component", f"the {part} part crashed", part, kind="component")
    if cats.get("resource", 0) >= 3 and not o.crunch_on_screen:
        return Verdict("restart_service", f"the app ran out of memory, files or threads {cats['resource']}x "
                       "in 15 min", kind="resource")
    if cats.get("switch", 0) >= 3 and not o.crunch_on_screen:
        return Verdict("restart_service", f"{cats['switch']} switches hung in 15 min")
    if cats.get("flip", 0) >= 3:
        return Verdict("notify", "the TV keeps ignoring input flips: is HDMI-CEC (Anynet+) still on?")
    if cats.get("feed", 0) >= 10:
        return Verdict("notify", "the score feed keeps failing (internet or ESPN down?)", kind="upstream")
    if cats.get("prime", 0) >= 4:
        return Verdict("notify", f"Prime needed {cats['prime']} slow reloads in 15 min", kind="upstream")
    if cats.get("config"):
        return Verdict("notify", "a saved setting was rejected and ignored", kind="person")
    if cats.get("source", 0) >= 50:
        return Verdict("notify", "NHL's extra feed has failed all night", kind="upstream")
    if not unknown:
        if o.safe_mode and o.safe_min >= SAFE_STALE_MIN and not o.paused and "safe_mode_off" not in o.trying:
            return Verdict("safe_mode_off", f"safe mode has been on {int(o.safe_min)} min and its errors are gone",
                           kind="core_loop")
        if o.live_games and o.feed_stale_s >= STALE_FEED_S and not o.net_down:
            if {"refresh_feed", "restart_component"} & set(o.trying):
                return Verdict("none", "waiting to see if the feed refresh worked", "feeds", kind="upstream")
            if "refresh_feed" not in o.failed:
                return Verdict("refresh_feed", f"live games but no score data for {int(o.feed_stale_s)} s",
                               "feeds", kind="upstream")
            if "restart_component" not in o.failed:
                return Verdict("restart_component", f"no score data for {int(o.feed_stale_s)} s after a refresh",
                               "feeds", kind="upstream")
            return Verdict("notify", f"no score data for {int(o.feed_stale_s)} s; refresh and restart didn't help",
                           kind="upstream")
        return Verdict("none", "nothing needs doing" if not cats else "only known, harmless errors",
                       kind="cosmetic")
    return None


def guard(v: Verdict, o: Obs) -> Verdict:
    """Last word on any verdict (rule or model): never restart in the middle of crunch time unless
    the app is down, never restart in a loop, never act on a stick that isn't offline, a stick fix
    only on the stick on screen while its screen is dead. A guarded verdict keeps the fault kind."""
    g = _guard(v, o)
    if not g.kind:
        g.kind = v.kind or _kind_of(g, o)
    return g


def _guard(v: Verdict, o: Obs) -> Verdict:
    if v.action not in ACTIONS:
        return Verdict("notify", f"model gave an unknown action {v.action!r}", source="guard")
    if v.action == "restart_service":
        if o.restarts_last_hour >= MAX_RESTARTS_HOUR:
            return Verdict("notify", f"wanted a restart ({v.why}) but already restarted "
                           f"{o.restarts_last_hour}x this hour", source="guard")
        if o.crunch_on_screen and o.api_ok:
            return Verdict("notify", f"wanted a restart ({v.why}) but crunch time is on screen", source="guard")
    if v.action in STICK_FIXES:
        if o.away or o.route_issue:
            return Verdict("none", "away from home or routes broken: a stick fix can't help", source="guard",
                           kind=v.kind)
        if not o.on_screen or not o.screen:
            return Verdict("none", "the screen isn't dead: no stick fix needed", source="guard", kind=v.kind)
        if v.target != o.on_screen:   # only ever the stick the TV shows, and only while it's dead
            v = Verdict(v.action, v.why, o.on_screen, v.source, v.kind)
        if v.target in o.sticks_offline:
            return Verdict("reconnect_stick", f"{v.why}; the stick is offline, so reconnect first", v.target,
                           "guard", "stick_link")
    if v.action == "restart_component" and v.target not in COMPONENTS:
        return Verdict("notify", f"wanted to restart {v.target!r} ({v.why}), which isn't a restartable part",
                       source="guard", kind=v.kind)
    if v.action == "refresh_feed" and o.net_down:
        return Verdict("none", "every source is down (internet): refreshing feeds can't help", source="guard",
                       kind="network")
    if v.action == "safe_mode_off":
        if not o.safe_mode:
            return Verdict("none", "safe mode isn't on", source="guard", kind=v.kind)
        if o.paused:
            return Verdict("none", "a person paused auto-switching; leaving it paused", source="guard", kind=v.kind)
    if v.action == "reconnect_stick" and (o.away or o.route_issue):
        return Verdict("none", "away from home or routes broken: reconnecting a stick can't help", source="guard")
    if v.action == "reconnect_stick" and v.target not in o.sticks_offline:
        if not o.sticks_offline:
            return Verdict("none", "no stick is offline", source="guard")
        v = Verdict("reconnect_stick", v.why, o.sticks_offline[0], v.source)
    return v


SYSTEM = (
    "You watch over RedZone Everything, a Mac app that switches a TV between live games. "
    "Given the app's status as JSON, pick ONE action: none, notify, restart_service, reconnect_stick, prune_disk. "
    "restart_service only when the app is down, hung, or an error will keep breaking switching; "
    "notify when a person must do something or should know; reconnect_stick for a stick that stopped answering; "
    "prune_disk when disk space is the problem; none for harmless noise. "
    'Answer with JSON only: {"action": "...", "target": "", "why": "<one short sentence>"}'
)


def parse_answer(text: str) -> Verdict | None:
    """The model's JSON answer, tolerant of thinking blocks and code fences; None if unusable."""
    from redzone.jsonfind import first_json_object
    d = first_json_object(text, accept=lambda x: str(x.get("action", "")).strip() in ACTIONS)
    if d is None:
        return None
    a = str(d.get("action", "")).strip()
    if a not in ACTIONS:
        return None
    return Verdict(a, str(d.get("why", ""))[:200], str(d.get("target", "") or ""), "model")
