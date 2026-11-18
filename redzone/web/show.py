"""The phone's Show view (flag `show.ui`, default OFF): read-only routes over the files the show writes.

    GET /api/show                  state.json, eyes_state.json under "eyes", and "stale_s" (seconds since "updated")
    GET /api/show/events?since=t   events.jsonl lines with t >= since (newest EVENTS_MAX)

The folder is config `show.state_dir`; eyes_state.json is read from `show.eyes_dir` when set, else from the state
folder, else from its sibling `eyes/` folder (the contract's layout). The contract: redzone-agents tools/STATE_CONTRACT.md. Any field may be missing or null.
Only the contract's fields leave here, each checked for type: numbers, on/off values and short text. Text that
looks like an address, a link, an account or a long id is dropped (the repo and the page are public-safe).
Nothing here reads or changes the engine, the decider or the TV; with the flag off the router is not included,
the page gets no markup and no script, so decisions are identical.

Why: Nick runs the new show from the phone app he already uses (Nov 10), not from a separate page.
"""
from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

SCRIPT_TAG = '<script type="module" src="/static/rz-show.js?v=7"></script>'
STATE_MAX_BYTES = 1_000_000      # a state file bigger than this is not the show's
EVENTS_TAIL_BYTES = 2_000_000    # only the end of a long events file is read
EVENTS_MAX = 500                 # newest events per answer
TEXT_MAX = 140                   # longest text kept
MAP_MAX = 40                     # most keys kept in an open-ended map (stay reasons, channels, sticks)
LIST_MAX = 40                    # most games kept
PACE_WINDOWS = ("1", "5", "10", "20", "30", "45", "60")

# Text that could identify a device, an account or a place on the network.
_UNSAFE = re.compile(
    r"\d{1,3}(\.\d{1,3}){3}"           # IPv4
    r"|[0-9a-f]{2}(:[0-9a-f]{2}){3,}"  # MAC / IPv6-ish
    r"|://|\S@\S|\\|/Users/|/home/"    # links, emails and handles, paths ("MSU @ DUKE" is fine)
    r"|[A-Za-z0-9_\-]{24,}",           # tokens, serials, long ids
    re.I)

NUM, BOOL, TEXT = "num", "bool", "text"


def _map(spec):
    return ("map", spec)


def _list(spec):
    return ("list", spec)


_ON_SCREEN = {"game": TEXT, "league": TEXT, "channel": TEXT, "stick": TEXT, "input": TEXT, "period": TEXT,
              "clock": TEXT, "score": TEXT, "behind_live_s": NUM, "since": NUM, "look_s": NUM, "state": TEXT}
STATE_SPEC = {
    "updated": NUM, "mode": TEXT,
    "session": {"started": NUM, "minutes": NUM, "planned_minutes": NUM},
    "sound": {"mute": TEXT, "volume": NUM, "read_back_at": NUM, "ok": BOOL},
    "on_screen": _ON_SCREEN,
    "parked": {"game": TEXT, "league": TEXT, "channel": TEXT, "stick": TEXT, "ready": BOOL,
               "parked_for_s": NUM, "behind_live_s": NUM},
    "next": {"action": TEXT, "to_game": TEXT, "in_s": NUM, "reason": TEXT},
    "pace": {"jumps": NUM, "per_min_now": NUM, "per_min_session": NUM, "windows": {w: NUM for w in PACE_WINDOWS}},
    "planned": {"scheduled": NUM, "delivered": NUM},
    "rule1": {"mid_play_joins": NUM, "mid_play_leaves": NUM, "high_leverage_leaves": NUM, "judged": TEXT},
    "timing": {"padding_s": NUM,
               "channels": _map({"k_s": NUM, "k_spread_s": NUM, "tune_offset_known": BOOL})},
    "stay_reasons": _map(NUM),
    "health": {"tv": TEXT, "feed": TEXT, "sticks": _map(TEXT), "eyes": TEXT, "last_error": TEXT},
    "games": _list({"game": TEXT, "league": TEXT, "channel": TEXT, "period": TEXT, "clock": TEXT, "score": TEXT,
                    "state": TEXT, "value": NUM, "on_screen": BOOL, "parked": BOOL,
                    # [SEEN] park.seen: seconds since it was on screen, its staleness, big plays missed, the templated recap
                    "last_seen_s": NUM, "stale": NUM, "missed": NUM, "recap": TEXT}),
}
EYES_SPEC = {
    "updated": NUM, "status": TEXT, "why": TEXT, "channel": TEXT, "league": TEXT, "frame_y": NUM, "period": NUM, "clock_s": NUM,
    "clock_running": BOOL, "last_stop_y": NUM, "last_restart_y": NUM, "bug_missing_since_y": NUM,
    "k": _map({"k_s": NUM, "spread_s": NUM, "n": NUM}),
    "reads_per_min": NUM, "failed_reads_per_min": NUM, "cpu_pct": NUM, "mem_mb": NUM,
}
EVENT_SPEC = {
    "t": NUM, "kind": TEXT, "from": TEXT, "to": TEXT, "reason": TEXT, "jump_s": NUM, "look_s": NUM, "game": TEXT,
    "channel": TEXT, "took_s": NUM, "for_s": NUM, "what": TEXT, "seen": BOOL, "level": TEXT, "text": TEXT,
    # [park.enabled] the app's own show: which jump (n), how long its landing was held, what each end rested on, and,
    # after the resume, the stream's position reports and whether its place held ("held" / "loose" / "drifted" / "unread")
    "n": NUM, "held_s": NUM, "verdict": TEXT, "change_s": NUM, "at_s": NUM, "pos_s": NUM, "updated": NUM, "lag_s": NUM,
    "leave_on": {"kind": TEXT, "second": NUM, "since_s": NUM, "left_s": NUM, "open": BOOL, "feed_edge_s": NUM,
                 "row": {"type": TEXT, "period": NUM, "clock": TEXT, "age_s": NUM, "rows_in": NUM}},
    "join_on": {"kind": TEXT, "second": NUM, "since_s": NUM, "left_s": NUM, "open": BOOL, "feed_edge_s": NUM,
                "row": {"type": TEXT, "period": NUM, "clock": TEXT, "age_s": NUM, "rows_in": NUM}},
}


def _num(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return v if math.isfinite(v) else None


def _text(v):
    if not isinstance(v, str):
        return None
    v = " ".join(v.split())[:TEXT_MAX]
    return None if not v or _UNSAFE.search(v) else v


def clean(value, spec):
    """value reduced to spec: unknown keys dropped, wrong types -> None. Never raises."""
    if spec == NUM:
        return _num(value)
    if spec == BOOL:
        return value if isinstance(value, bool) else None
    if spec == TEXT:
        return _text(value)
    if isinstance(spec, tuple) and spec[0] == "map":
        if not isinstance(value, dict):
            return None
        out = {}
        for k, v in list(value.items())[:MAP_MAX]:
            key = _text(k)
            if key is not None:
                out[key] = clean(v, spec[1])
        return out
    if isinstance(spec, tuple) and spec[0] == "list":
        if not isinstance(value, list):
            return None
        return [c for c in (clean(v, spec[1]) for v in value[:LIST_MAX]) if c]
    if isinstance(spec, dict):
        if not isinstance(value, dict):
            return None
        return {k: clean(value[k], sub) for k, sub in spec.items() if k in value}
    return None


def _read_json(path: Path):
    """The JSON object at path, or None (missing, too big, unreadable, not an object)."""
    try:
        if not path.is_file() or path.stat().st_size > STATE_MAX_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def _folder(cfg, key: str = "show.state_dir") -> Path | None:
    raw = cfg.get(key, "") or ""
    if isinstance(raw, str) and raw.strip():
        return Path(raw).expanduser()
    if key == "show.state_dir" and cfg.get("park.enabled", False):
        return Path("data/show")   # [park.enabled] where the app's own show writes when no folder is set (redzone/park_run.py)
    return None


def build_state(folder: Path | None, now: float | None = None, eyes_dir: Path | None = None) -> dict:
    """The /api/show answer. A missing or broken state file gives {"eyes": ..., "stale_s": None}."""
    now = time.time() if now is None else now
    raw = _read_json(folder / "state.json") if folder else None
    out = clean(raw, STATE_SPEC) if raw is not None else {}
    eyes = None
    places = [eyes_dir] if eyes_dir else [folder, folder.parent / "eyes"] if folder else []
    for d in places:
        eyes = _read_json(d / "eyes_state.json")
        if eyes is not None:
            break
    out["eyes"] = clean(eyes, EYES_SPEC) if eyes is not None else None
    upd = out.get("updated")
    out["stale_s"] = round(max(0.0, now - upd), 1) if upd is not None else None
    return out


def read_events(folder: Path | None, since: float | None = None) -> list[dict]:
    """Events with t >= since (all when since is None), oldest first, at most EVENTS_MAX (the newest)."""
    if not folder:
        return []
    path = folder / "events.jsonl"
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            if size > EVENTS_TAIL_BYTES:
                f.seek(size - EVENTS_TAIL_BYTES)
                f.readline()   # drop the partial line at the cut
            data = f.read()
    except OSError:
        return []
    out = []
    for line in data.decode("utf-8", errors="replace").split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except (ValueError, RecursionError):
            continue
        ev = clean(ev, EVENT_SPEC)
        if not ev or ev.get("t") is None or not ev.get("kind"):
            continue
        if since is not None and ev["t"] < since:
            continue
        out.append(ev)
    return out[-EVENTS_MAX:]


def enabled(cfg) -> bool:
    try:
        return bool(cfg.get("show.ui", False))
    except Exception:
        return False


def inject(html: str) -> str:
    """index.html with the Show view's script, loaded after app.js."""
    return html.replace("</body>", f"  {SCRIPT_TAG}\n</body>", 1)


def router(engine) -> APIRouter:
    r = APIRouter(prefix="/api/show")

    def off():
        return JSONResponse({"ok": False, "message": "the Show view is off"}, status_code=404)

    @r.get("")
    def show_state():
        if not enabled(engine.cfg):
            return off()
        return JSONResponse(build_state(_folder(engine.cfg), eyes_dir=_folder(engine.cfg, "show.eyes_dir")))

    @r.get("/events")
    def show_events(since: str | None = None):
        if not enabled(engine.cfg):
            return off()
        try:
            t = float(since) if since not in (None, "") else None
        except ValueError:
            t = None
        if t is not None and not math.isfinite(t):
            t = None
        return JSONResponse({"events": read_events(_folder(engine.cfg), t)})

    return r
