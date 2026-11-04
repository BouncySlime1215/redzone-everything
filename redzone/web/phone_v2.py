"""Phone page v2 (flag `phone.v2`, default OFF): why each switch happened, rate it, "I missed that",
and the Noggin Priority L2 sliders.

North Star: none of these move a scoring line directly. They feed two of them:
  * Nick, Nov 4 10:55 AM: the Noggin is fully self-taught. Votes and "I missed that" are recorded for
    the phone page only; no training, labelling, suggestion or North Star path reads them.
Nothing here touches the decider, the tick loop or the TV. Votes and misses only write rows to the
existing `feedback` table (note = "switch" | "missed"), so turning the flag off leaves no trace in play.
"""
from __future__ import annotations

import json
import re
import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from redzone.noggin.priority import Preferences

# Reason text -> chip. First match per key wins; order = how chips are shown (most important first).
CHIP_RULES: list[tuple[str, str, re.Pattern]] = [
    ("yours", "Your team", re.compile(r"\byour (team|teams)\b|\bmine\b|\bUNC\b|\bWSH\b|Commanders|Wizards|Capitals", re.I)),
    ("crunch", "Crunch time", re.compile(r"crunch|close[- ]late|final (minute|two)|clutch|overtime|\bOT\b", re.I)),
    ("redzone", "Red zone", re.compile(r"red ?zone|goal[- ]to[- ]go|inside the \d+", re.I)),
    ("score", "Score", re.compile(r"touchdown|\bTD\b|field goal|\bgoal\b|home run|\bHR\b|scor", re.I)),
    ("pp", "Power play", re.compile(r"power ?play|empty net|\bPP\b", re.I)),
    ("bet", "Your bet", re.compile(r"\bbet\b|sweat|over/under|spread|parlay", re.I)),
    ("upset", "Upset watch", re.compile(r"upset|comeback|swing|win prob", re.I)),
    ("break", "Break elsewhere", re.compile(r"commercial|break|timeout|dead ball|stoppage|review", re.I)),
    ("replay", "Catch-up", re.compile(r"replay|catch[- ]up|reel|missed", re.I)),
    ("manual", "You picked", re.compile(r"\byou (picked|asked|chose)|manual|override|go to", re.I)),
    ("lock", "Locked in", re.compile(r"lock(ed)? ?in|hold", re.I)),
]
BIG_KINDS = {"td", "fg", "turnover", "goal", "hr", "safety", "interception", "fumble", "pick6",
             "dunk", "three", "and1", "buzzer", "penalty_shot", "grand_slam"}
MISS_WINDOW_S = 600.0   # "I missed that" looks back 10 min, the same window as big_seen_10min


def chips(reason: str | None, kind: str | None = None) -> list[dict]:
    text = f"{reason or ''} {kind or ''}"
    out = [{"key": k, "label": label} for k, label, rx in CHIP_RULES if rx.search(text)]
    return out[:4] or [{"key": "best", "label": "Best on"}]


def _enabled(engine) -> bool:
    return bool(engine.cfg.get("phone.v2", False))


def _off():
    return JSONResponse({"ok": False, "enabled": False, "message": "phone v2 is off"}, status_code=404)


def _votes_by_switch(engine) -> dict[str, int]:
    """switch key "ts|to" -> last vote, from the feedback table."""
    out: dict[str, int] = {}
    try:
        rows = engine.store._x("SELECT vote, context FROM feedback WHERE note = 'switch' ORDER BY ts").fetchall()
    except Exception:
        return out
    for vote, ctx in rows:
        try:
            c = json.loads(ctx or "{}")
        except ValueError:
            continue
        if c.get("switch"):
            out[str(c["switch"])] = int(vote)
    return out


def _switch_key(ts, to) -> str:
    return f"{float(ts):.3f}|{to or ''}"


def miss_candidates(games, on_screen: str | None, now: float, window: float = MISS_WINDOW_S) -> list[dict]:
    """Big plays in the last `window` s on games that were NOT on screen, newest first."""
    out = []
    for gid, g in (games or {}).items():
        if gid == on_screen:
            continue
        for p in getattr(g, "recent_plays", None) or []:
            at = p.seen_at or p.wallclock
            if at is None or now - at > window:
                continue
            if not (p.scoring or (p.kind or "").lower() in BIG_KINDS):
                continue
            out.append({"game_id": gid, "matchup": getattr(g, "matchup", gid), "play_id": p.id,
                        "text": p.text, "kind": p.kind, "ago_s": round(now - at)})
    out.sort(key=lambda r: r["ago_s"])
    return out


def router(engine) -> APIRouter:
    r = APIRouter(prefix="/api/v2")

    @r.get("/status")
    async def status():
        return {"enabled": _enabled(engine)}

    @r.get("/switches")
    async def switches():
        if not _enabled(engine):
            return _off()
        votes = _votes_by_switch(engine)
        out = []
        for row in engine._log():
            key = _switch_key(row["ts"], row["to"])
            out.append({**row, "key": key, "chips": chips(row.get("reason"), row.get("kind")),
                        "vote": votes.get(key, 0)})
        return {"enabled": True, "switches": out}

    @r.post("/vote")
    async def vote(body: dict):
        if not _enabled(engine):
            return _off()
        v = int(body.get("vote") or 0)
        if v not in (-1, 0, 1):
            return {"ok": False, "message": "vote is -1, 0 or 1"}
        key = str(body.get("key") or "")
        row = next((x for x in engine._log() if _switch_key(x["ts"], x["to"]) == key), None)
        if row is None:
            return {"ok": False, "message": "That switch is too old to rate"}
        cs = chips(row.get("reason"), row.get("kind"))
        engine.store.feedback(row["to"], v, note="switch",
                              context={"switch": key, "reason": row.get("reason"), "kind": row.get("kind"),
                                       "terms": {c["key"]: 1.0 for c in cs}})
        msg = {1: "Good switch — noted", -1: "Bad switch — noted", 0: "Rating cleared"}[v]
        return {"ok": True, "message": msg, "vote": v}

    @r.post("/missed")
    async def missed(body: dict):
        if not _enabled(engine):
            return _off()
        now = time.time()
        cur = getattr(engine.decider, "current", None)
        cands = miss_candidates(getattr(engine.merger, "games", {}), cur, now)
        pick = str(body.get("game_id") or "") or None
        guess = next((c for c in cands if c["game_id"] == pick), None) if pick else (cands[0] if cands else None)
        engine.store.feedback(guess["game_id"] if guess else pick, 0, note="missed",
                              context={"on_screen": cur, "guess": guess, "candidates": cands[:5],
                                       "note": str(body.get("note") or "")[:200]})
        if guess:
            msg = f"Logged: {guess['matchup']} ({guess['text'] or guess['kind']}, {guess['ago_s'] // 60} min ago)"
        else:
            msg = "Logged. No big play found in the feeds; it still counts as a miss"
        return {"ok": True, "message": msg, "guess": guess, "candidates": cands[:5]}

    @r.get("/priority")
    async def get_priority():
        if not _enabled(engine):
            return _off()
        prefs = Preferences.from_config(engine.cfg)
        try:
            rows = engine.store._x("SELECT vote, context FROM feedback WHERE note = 'switch'").fetchall()
        except Exception:
            rows = []
        fb = []
        for vote_, ctx in rows:
            try:
                c = json.loads(ctx or "{}")
            except ValueError:
                continue
            if vote_:
                fb.append({"vote": vote_, "terms": c.get("terms") or {}})
        return {"enabled": True,
                "prefs": {k: getattr(prefs, k) for k in (*Preferences.RANGES, "reels")},
                "ranges": {k: list(v) for k, v in Preferences.RANGES.items()},
                "votes": len(fb),
                "suggestions": []}   # self-taught rule: taps never suggest or train anything

    @r.post("/priority")
    async def set_priority(body: dict):
        if not _enabled(engine):
            return _off()
        changes = {}
        for k, (lo, hi) in Preferences.RANGES.items():
            if k in body:
                try:
                    changes[k] = min(hi, max(lo, float(body[k])))
                except (TypeError, ValueError):
                    return {"ok": False, "message": f"{k} must be a number"}
        if "reels" in body:
            changes["reels"] = bool(body["reels"])
        if not changes:
            return {"ok": False, "message": "Nothing to change"}
        from redzone.config import ConfigError
        try:
            engine.cfg.update({"noggin": {"priority": {"prefs": changes}}})
        except ConfigError as e:
            return {"ok": False, "message": str(e)}
        engine.broadcast()
        return {"ok": True, "message": "Saved", "prefs": changes}

    return r
