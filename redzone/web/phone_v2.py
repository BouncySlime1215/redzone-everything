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
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from redzone.models import PERIOD_S, REGULATION, Phase
from redzone.noggin.priority import HardRules, Preferences

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
ET = ZoneInfo("America/New_York")
NIGHT_START_H = 6       # a "night" runs 6 AM ET to 6 AM ET the next day, so a 1 AM switch is last night
PLAYBOOK = re.compile(r"playbook=([A-Za-z]+-\d+)")
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


def night_window(day: date) -> tuple[float, float]:
    t0 = datetime(day.year, day.month, day.day, NIGHT_START_H, tzinfo=ET)
    return t0.timestamp(), (t0 + timedelta(days=1)).timestamp()


def night_of(ts: float) -> date:
    return (datetime.fromtimestamp(ts, ET) - timedelta(hours=NIGHT_START_H)).date()


def playbooks(reason: str | None) -> list[str]:
    return sorted(set(PLAYBOOK.findall(reason or "")))


def night_view(switches: list[dict], decisions: list[dict], taps: list[dict], name, end: float) -> dict:
    """One night as a timeline. `switches` / `decisions` / `taps` oldest first, all inside the window.
    segments: what was on screen and for how long (a failed or dry-run switch still counts: it is what
    the app believed was on; the last one runs to `end`). events: switches, holds/waits and taps, each
    with chips and playbook ids."""
    segs = []
    for i, sw in enumerate(switches):
        nxt = switches[i + 1]["ts"] if i + 1 < len(switches) else end
        segs.append({"t0": sw["ts"], "t1": max(sw["ts"], nxt), "game": sw["to_game"], "matchup": name(sw["to_game"])})
    events = []
    for sw in switches:
        events.append({"ts": sw["ts"], "type": "switch", "kind": sw.get("kind"), "game": sw["to_game"],
                       "matchup": name(sw["to_game"]), "reason": sw.get("reason") or "",
                       "chips": chips(sw.get("reason"), sw.get("kind")), "playbooks": playbooks(sw.get("reason")),
                       "ok": bool(sw.get("ok")), "dry_run": bool(sw.get("dry_run")),
                       "key": _switch_key(sw["ts"], sw["to_game"])})
    for d in decisions:
        if d.get("action") in ("switch", "replay"):
            continue   # the switch row already says it, with the outcome
        if not playbooks(d.get("reason")) and d.get("action") not in ("hold", "wait"):
            continue   # plain "stay" reason churn is noise on a timeline
        events.append({"ts": d["ts"], "type": "decision", "kind": d.get("action"), "game": d.get("game"),
                       "matchup": name(d.get("game")) if d.get("game") else "", "reason": d.get("reason") or "",
                       "chips": chips(d.get("reason"), d.get("kind")), "playbooks": playbooks(d.get("reason")),
                       "gate": d.get("gate")})
    for t in taps:
        events.append({"ts": t["ts"], "type": "tap", "kind": t["tap"], "game": t.get("game"),
                       "matchup": name(t.get("game")) if t.get("game") else "", "reason": t.get("reason") or "",
                       "switch": t.get("switch")})
    events.sort(key=lambda e: e["ts"])
    on_air: dict[str, float] = {}
    for sg in segs:
        on_air[sg["matchup"]] = on_air.get(sg["matchup"], 0.0) + (sg["t1"] - sg["t0"])
    pb: dict[str, int] = {}
    for e in events:
        for p in e.get("playbooks") or []:
            pb[p] = pb.get(p, 0) + 1
    tally = {k: sum(1 for t in taps if t["tap"] == k) for k in ("up", "down", "missed")}
    return {"segments": segs, "events": events,
            "summary": {"switches": len(switches), "failed": sum(1 for s in switches if not s.get("ok")),
                        "games": len(on_air), "taps": tally,
                        "on_air": sorted(({"matchup": k, "s": round(v)} for k, v in on_air.items()),
                                         key=lambda r: -r["s"])[:8],
                        "playbooks": dict(sorted(pb.items(), key=lambda kv: -kv[1]))}}


# Slate preview: typical wall-clock length of a game and of its crunch window (the last stretch where
# the decider's crunch rules kick in), in minutes. Rough public averages; only used for the preview.
GAME_MIN = {"nfl": 190, "cfb": 210, "nba": 135, "cbb": 120, "wcbb": 120, "nhl": 150, "mlb": 165}
CRUNCH_MIN = {"nfl": 45, "cfb": 50, "nba": 25, "cbb": 20, "wcbb": 20, "nhl": 35, "mlb": 30}


def progress(g) -> float:
    """0..1 of regulation played, from period and clock (MLB by inning)."""
    lg = g.league
    reg = REGULATION.get(lg) or 4
    p = max(1, int(g.period or 1))
    if not PERIOD_S.get(lg):
        return min(1.0, (p - 1) / reg)
    per = PERIOD_S[lg]
    left = per if g.clock_s is None else max(0.0, min(per, float(g.clock_s)))
    return min(1.0, ((p - 1) * per + (per - left)) / (reg * per))


def slate_preview(games, rules: HardRules, now: float) -> dict:
    """Tonight ahead, as a pure function of the board: each live or upcoming game's expected window
    and crunch window, and every stretch where two or more crunch windows overlap, with who gets the
    TV by the L1 order (your team, then league order) and who goes to the second stick / catch-up."""
    rows = []
    for gid, g in (games or {}).items():
        if g.phase not in (Phase.PRE, Phase.LIVE) or (g.phase == Phase.PRE and not g.start_time):
            continue
        lg = g.league.value
        dur = GAME_MIN.get(lg, 150) * 60.0
        if g.phase == Phase.LIVE:
            start = g.start_time or now
            end = max(now + 300.0, now + dur * (1.0 - progress(g)))
        else:
            start = g.start_time
            end = start + dur
        c0 = max(start, end - CRUNCH_MIN.get(lg, 30) * 60.0)
        yours = rules.is_yours(lg, (g.home.abbr, g.away.abbr))
        rows.append({"game_id": gid, "league": lg, "matchup": getattr(g, "matchup", gid), "live": g.phase == Phase.LIVE,
                     "start": start, "end": end, "crunch": [c0, end], "yours": yours,
                     "rank": (0 if yours else 1, rules.league_rank(lg))})
    rows.sort(key=lambda r: (r["start"], r["rank"]))
    # sweep crunch windows: every boundary starts a new stretch with a fixed set of games in crunch
    cuts = sorted({t for r in rows for t in r["crunch"]})
    clashes = []
    for a, b in zip(cuts, cuts[1:]):
        inside = [r for r in rows if r["crunch"][0] <= a and r["crunch"][1] >= b]
        if len(inside) < 2:
            continue
        inside.sort(key=lambda r: r["rank"])
        ids = [r["game_id"] for r in inside]
        if clashes and clashes[-1]["games"] == ids and abs(clashes[-1]["t1"] - a) < 1:
            clashes[-1]["t1"] = b
            continue
        clashes.append({"t0": a, "t1": b, "games": ids, "on_tv": inside[0]["matchup"],
                        "second": [r["matchup"] for r in inside[1:]],
                        "why": "your team" if inside[0]["yours"] and not inside[1]["yours"] else "league order"})
    for r in rows:
        r.pop("rank")
    return {"games": rows, "clashes": [c for c in clashes if c["t1"] - c["t0"] >= 120],
            "yours": sum(1 for r in rows if r["yours"])}


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

    @r.get("/night")
    async def night(day: str = ""):
        if not _enabled(engine):
            return _off()
        now = time.time()
        try:
            d = date.fromisoformat(day) if day else night_of(now)
        except ValueError:
            return {"ok": False, "message": "day is YYYY-MM-DD"}
        t0, t1 = night_window(d)
        sws = engine.store.switches_between(t0, t1)
        decs = engine.store.decisions_between(t0, t1)
        taps = [t for t in engine.store.taps(t0) if t["ts"] < t1]
        # the screen was on until the last live snapshot (the app may have been closed before 6 AM)
        last = engine.store.last_snapshot_between(t0, t1)
        end = min(t1, now) if last is None else min(t1, now, max(last, sws[-1]["ts"] if sws else last))
        nights = sorted({night_of(ts).isoformat() for ts in engine.store.switch_days(21)} | {night_of(now).isoformat()},
                        reverse=True)[:14]
        return {"enabled": True, "day": d.isoformat(), "t0": t0, "t1": t1, "now": now, "nights": nights,
                **night_view(sws, decs, taps, engine._name, end)}

    @r.get("/slate")
    async def slate():
        if not _enabled(engine):
            return _off()
        now = time.time()
        return {"enabled": True, "now": now,
                **slate_preview(getattr(engine.merger, "games", {}), HardRules.from_config(engine.cfg), now)}

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
