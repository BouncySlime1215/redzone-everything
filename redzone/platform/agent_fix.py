"""The watchdog's window into the app, and the narrow fixes it may ask the app to run.

agent/watchdog.py runs as its own process and sees the app only through /api/state. Two things
here widen that view without giving it a hand on the TV:

  view(engine)               the "agent" block of /api/health: which stick is on screen, how long
                             the screen has been dead and how (paused / frozen / stopped), safe mode,
                             a person's pause, how stale the live feeds are, whether every source is
                             down at once (the Mac is offline), push-feed crashes, a fault only a
                             person can fix.
  run_fix(engine, fix, tgt)  POST /api/agent/fix. Runs one of FIXES through the supervisor's own
                             rate-limited, journaled fix() (data/supervisor.jsonl, rule "agent").

The app repairs itself first (controller visible-stick watch, supervisor rules); the watchdog is the
second line, with longer thresholds, for when those didn't work. Every fix is OFF unless
hardening.watchdog is true: then the endpoint answers {"ok": false, "message": "off: ..."} and the
watchdog tells a person instead. Stick fixes only touch the stick the TV is showing, and only while
its screen is already not playing, so none of them can cut a live play.
"""
from __future__ import annotations

import time
from typing import Any

FIXES = ("resume_player", "clear_prompt", "retune_stick", "refresh_feed", "restart_component", "safe_mode_off")
COMPONENTS = ("feeds", "fastcast")      # parts that restart on their own without a full app restart
STICK_FIXES = ("resume_player", "clear_prompt", "retune_stick")
PERSON_FAULTS = ("tv_auth",)            # PlatformHealth codes only a person can clear

# Supervisor limit keys (redzone/supervisor.py LIMITS): (seconds between two, max per hour)
LIMITS = {"agent_resume_player": (120.0, 6), "agent_clear_prompt": (120.0, 6), "agent_retune_stick": (300.0, 3),
          "agent_refresh_feed": (300.0, 4), "agent_restart_component": (600.0, 3), "agent_safe_mode_off": (600.0, 3)}


def enabled(engine: Any) -> bool:
    try:
        return bool(engine.cfg.get("hardening.watchdog", False))
    except Exception:
        return False


def _screen(ctl: Any) -> str:
    raw = str(getattr(ctl, "_visible_state", "") or "stopped")
    if raw == "paused":
        return "paused"
    if raw in ("frozen", "buffering", "connecting", "playing"):
        return "frozen"
    if raw == "prompt":
        return "prompt"
    return "stopped"


def view(engine: Any, now: float | None = None) -> dict:
    """Cheap (no adb, no network): read from what the controller, decider and feeds already hold."""
    now = time.time() if now is None else now
    ctl, dec = engine.control, engine.decider
    main = getattr(ctl, "main", None)
    hidden = getattr(ctl, "hidden", None) if getattr(ctl, "two_stick", False) else None
    bad = getattr(ctl, "_visible_bad_since", None)
    dead = bool(bad and getattr(ctl, "on_target", None) and main is not None)
    safe = bool(getattr(dec, "safe_mode", False))
    from redzone.models import Phase
    games = getattr(getattr(engine, "merger", None), "games", {}) or {}
    live = [g for g in games.values() if g.phase == Phase.LIVE]
    srcs = list((getattr(engine, "source_status", {}) or {}).values())
    comps = []
    task = getattr(engine, "_fastcast_task", None)
    if task is not None and task.done() and not task.cancelled() and task.exception() is not None:
        comps.append("fastcast")
    sup = getattr(engine, "supervisor", None)
    person = ""
    for f in getattr(sup, "last_faults", None) or ():
        if getattr(f, "code", "") in PERSON_FAULTS:
            person = str(getattr(f, "detail", "") or getattr(f, "code", ""))[:200]
            break
    return {
        "on_screen": getattr(main, "host", "") or "",
        "standby": getattr(hidden, "host", "") or "",
        "screen": _screen(ctl) if dead else "",
        "screen_dead_s": round(now - bad, 1) if dead else 0.0,
        "safe_mode": safe,
        "safe_min": round((now - float(getattr(dec, "safe_since", now) or now)) / 60, 1) if safe else 0.0,
        "paused": bool(getattr(getattr(dec, "user", None), "paused", False)),
        "feed_stale_s": round(max((now - g.updated_at for g in live), default=0.0), 1),
        "net_down": bool(srcs) and not any(s.get("ok") for s in srcs),
        "components": comps,
        "person": person,
        "fixes_on": enabled(engine),
    }


async def run_fix(engine: Any, fix: str, target: str = "") -> tuple[bool, str]:
    """One watchdog-requested fix. (ok, message); message starts with "off:" when the flag is off."""
    if fix not in FIXES:
        return False, f"unknown fix {fix!r}"
    if not enabled(engine):
        return False, "off: hardening.watchdog is false"
    ctl, dec = engine.control, engine.decider
    sup = engine.supervisor

    async def stick_fix():
        stick, tgt = getattr(ctl, "main", None), getattr(ctl, "on_target", None)
        if stick is None or not tgt or stick.host != target:
            return False, f"{target or '?'} isn't the stick on screen"
        if getattr(ctl, "_visible_bad_since", None) is None:
            return True, "already playing"
        if ctl._busy() or ctl._flipping or ctl.input_unknown:
            return False, "a switch or flip is running"
        if fix == "clear_prompt":
            ok = await ctl._clear_screen_prompt(stick, tgt, ctl._package(stick, tgt))
            return bool(ok), "prompt cleared" if ok else "no prompt seen"
        how = await ctl.restore_visible(tgt, relink=fix == "retune_stick", why=f"watchdog: {fix}")
        return bool(how), how or "still not playing"

    def component():
        if target == "feeds":
            return engine.reset_feeds(hard=True)
        if target == "fastcast":
            engine._stop_fastcast()
            return True, "push feed restarting"
        return False, f"{target!r} isn't a restartable part"

    def safe_off():
        if getattr(getattr(dec, "user", None), "paused", False):
            return False, "a person paused auto-switching"
        if not getattr(dec, "safe_mode", False):
            return True, "safe mode was already off"
        return bool(dec.auto_resume(0)), "safe mode off"

    fn = {"refresh_feed": lambda: engine.reset_feeds(hard=False), "restart_component": component,
          "safe_mode_off": safe_off}.get(fix, stick_fix)
    ok = await sup.fix("agent", f"{fix}:{target}", f"watchdog asked: {fix} {target}".strip(), fn,
                       limit=f"agent_{fix}")
    if ok is None:
        return False, "rate-limited by the app"
    last = sup.fixes[-1] if sup.fixes else None
    return bool(ok), (last.detail if last is not None else "") or ("done" if ok else "failed")
