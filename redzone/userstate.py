"""What you told the phone (pause, "Watch this", focus, lock, commit), kept across service restarts.

A restart (launchd, a crash, an update) used to drop all of it: a paused TV started switching
again and a held game was abandoned. It lives in a small JSON file next to the config
(data/user_state.json), written whenever it changes and read once at start.

Pause is kept however old it is: you paused it, so it stays paused. Game-specific choices (hold,
lock, focus) are about tonight's games; after STALE_S they are dropped rather than pointed at a
game that ended hours ago.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)

STALE_S = 6 * 3600.0
HOLD_KEYS = ("game_id", "since", "auto_resume")
COMMIT_MODES = ("my_teams", "stop_after", "end_at")   # [commit] EXP-036


def commit_value(value, game_id: str | None, now: float | None) -> tuple[dict | None, str | None]:
    """A phone commit made safe to keep: ({mode, game_id?, at?}, None), (None, None) to clear, or
    (None, why) when it can't be used. stop_after needs a game; a new end_at needs a time still ahead
    (now=None skips that: a restored end_at that passed while we were down still ends tonight's slate)."""
    if value is None or value == "off" or (isinstance(value, dict) and value.get("mode") in (None, "off")):
        return None, None
    if not isinstance(value, dict) or value.get("mode") not in COMMIT_MODES:
        return None, f"Pick one of: {', '.join(COMMIT_MODES)}"
    mode = value["mode"]
    if mode == "my_teams":
        return {"mode": mode}, None
    if mode == "stop_after":
        gid = value.get("game_id") or game_id
        return ({"mode": mode, "game_id": gid}, None) if isinstance(gid, str) and gid else (None, "Pick a game to stop after")
    try:
        at = float(value.get("at"))
    except (TypeError, ValueError):
        return None, "Pick a time to end at"
    return ({"mode": mode, "at": at}, None) if now is None or at > now else (None, "That time has already passed")


def capture(user, hold: dict | None) -> dict:
    """The part of the decider's UserState (and the engine's hold) worth keeping."""
    return {
        "paused": bool(user.paused),
        "locked_game": user.locked_game,
        "focus_game": user.focus_game,
        "focus_mode": getattr(user.focus_mode, "value", user.focus_mode),
        "hold": {k: hold.get(k) for k in HOLD_KEYS} if hold else None,
        "muted": sorted(getattr(user, "muted", set()) or ()),
        "commit": user.commit,
    }


def save(path: Path, state: dict, now: float | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({**state, "saved": now if now is not None else time.time()}))
    tmp.replace(path)   # atomic: a crash mid-save can't corrupt it


def load(path: Path, now: float | None = None) -> dict | None:
    """The saved state, with stale game choices dropped. Missing or broken file -> None."""
    try:
        d = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    now = now if now is not None else time.time()
    try:
        saved = float(d.get("saved") or 0.0)
    except (TypeError, ValueError):
        saved = 0.0
    out = {"paused": bool(d.get("paused")), "locked_game": None, "focus_game": None,
           "focus_mode": "autopilot", "hold": None, "commit": None}
    if now - saved <= STALE_S:
        hold = d.get("hold")
        if isinstance(hold, dict) and isinstance(hold.get("game_id"), str):
            out["hold"] = {"game_id": hold["game_id"], "since": float(hold.get("since") or saved),
                           "auto_resume": bool(hold.get("auto_resume", True))}
        for k in ("locked_game", "focus_game"):
            if isinstance(d.get(k), str):
                out[k] = d[k]
        if isinstance(d.get("focus_mode"), str):
            out["focus_mode"] = d["focus_mode"]
        out["commit"] = commit_value(d.get("commit"), None, None)[0]   # kept for the night, even an end_at now past
    # Muted games stay muted all night (games that can't play here, the user's own mutes).
    if now - saved <= 12 * 3600 and isinstance(d.get("muted"), list):
        out["muted"] = [g for g in d["muted"] if isinstance(g, str)]
    return out


def apply(state: dict, user, focus_mode_type) -> dict | None:
    """Put a loaded state back on the decider's UserState; returns the engine's hold dict."""
    user.paused = bool(state.get("paused"))
    user.muted = set(state.get("muted") or ())
    user.locked_game = state.get("locked_game")
    user.focus_game = state.get("focus_game")
    user.commit = state.get("commit")
    try:
        user.focus_mode = focus_mode_type(state.get("focus_mode") or "autopilot")
    except ValueError:
        user.focus_game, user.focus_mode = None, focus_mode_type("autopilot")
    if user.focus_game is None:
        user.focus_mode = focus_mode_type("autopilot")
    h = state.get("hold")
    if not h:
        return None
    user.locked_game = h["game_id"]   # holding IS a lock (engine._hold)
    user.paused = False               # ...and replaces a pause
    return {"game_id": h["game_id"], "since": h["since"], "auto_resume": h["auto_resume"],
            "resume_at": None, "why": "", "force": False}
