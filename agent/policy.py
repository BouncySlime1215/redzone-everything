"""The watchdog's judgment: what's wrong and what (if anything) to do. Stdlib only.

Two layers:
  rules()      hard cases with one right answer (service down, disk full, a known error) — no model
  ask_model()  an error nobody has seen before: the local model (agent/llm.py) picks from the same
               fixed action set, and guard() still has the last word.

The same rules label the fine-tuning data (agent/train/make_data.py), so the model learns this
project's judgment, and the held-out test measures how well it carries over to new wordings.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

ACTIONS = ("none", "notify", "restart_service", "reconnect_stick", "prune_disk")


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

    def text(self) -> str:
        """Compact, model-friendly description (also the training prompt)."""
        d = asdict(self)
        d.pop("launchd_exit", None)       # not in the trained prompt; the rules handle it
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
    """The answer when there's a hard rule for it; None when only unknown errors are left."""
    if not o.api_ok and o.launchd_exit in LAUNCHD_FATAL:
        # Restarting would just fail the same way (and burn the restart cap): say what to fix.
        return Verdict("notify", LAUNCHD_FATAL[o.launchd_exit])
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
        return Verdict("notify", o.route_issue)
    if o.sticks_offline and not o.away:
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
