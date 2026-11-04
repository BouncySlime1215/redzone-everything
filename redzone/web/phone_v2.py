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
from pathlib import Path
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
DATA = Path(__file__).resolve().parent.parent.parent / "data"
ORACLE_DIR = DATA / "oracle"            # <night>.json: the hindsight oracle's schedule for that night
MOMENTS = DATA / "supervisor_moments.jsonl"   # every big play, judged from the feed: seen live or not
HISTORY = DATA.parent / "docs" / "northstar-history.jsonl"   # scripts/northstar.py appends a row per run
MISS_WINDOW_S = 600.0   # "I missed that" looks back 10 min, a phone convenience; the graded window is big_seen_3min


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


def read_oracle(path: Path) -> dict | None:
    """The oracle schedule file for one night, or None. Format (docs/decision-records.md):
    {"objective": "combined", "switches": [{"t": <unix s>, "to": <game id>}, ...]} in time order; written
    offline from the archived night by the hindsight oracle (redzone/lab/oracle.py). A bad file is None."""
    try:
        d = json.loads(Path(path).read_text())
        sw = [{"t": float(x["t"]), "to": str(x["to"])} for x in d.get("switches") or []]
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None
    return {"objective": str(d.get("objective") or "combined"), "switches": sorted(sw, key=lambda x: x["t"])}


def _on_at(segs: list[dict], ts: float, key: str = "game") -> str | None:
    for sg in segs:
        if sg["t0"] <= ts < sg["t1"]:
            return sg[key]
    return None


def oracle_overlay(segments: list[dict], oracle: dict | None, moments: list[dict], name, end: float) -> dict:
    """What the hindsight oracle would have shown next to what we did, and a marker per big play.
    agree_pct: share of our on-screen time showing the oracle's game; markers: each big play in the
    window (seen live or missed, judged from the feed by the supervisor), with what was on at the time."""
    osegs = []
    if oracle:
        sw = oracle["switches"]
        for i, x in enumerate(sw):
            t1 = min(end, sw[i + 1]["t"]) if i + 1 < len(sw) else end
            if t1 > x["t"]:
                osegs.append({"t0": x["t"], "t1": t1, "game": x["to"], "matchup": name(x["to"])})
    agree = total = 0.0
    if osegs:
        for sg in segments:
            total += sg["t1"] - sg["t0"]
            for o in osegs:
                lo, hi = max(sg["t0"], o["t0"]), min(sg["t1"], o["t1"])
                if hi > lo and o["game"] == sg["game"]:
                    agree += hi - lo
    t0 = segments[0]["t0"] if segments else None
    markers = [{"ts": float(m["ts"]), "game": m.get("game"), "matchup": m.get("matchup") or name(m.get("game")),
                "kind": m.get("kind") or "", "label": (m.get("label") or "")[:80], "seen": bool(m.get("seen")),
                "on": name(_on_at(segments, float(m["ts"]))) if _on_at(segments, float(m["ts"])) else ""}
               for m in moments if t0 is not None and t0 <= float(m["ts"]) < end]
    return {"oracle": {"objective": oracle["objective"], "segments": osegs,
                       "agree_pct": round(100 * agree / total, 1) if total else None} if oracle else None,
            "markers": markers,
            "big": {"plays": len(markers), "seen": sum(1 for m in markers if m["seen"]),
                    "missed": sum(1 for m in markers if not m["seen"])}}


# North Star trend: the headline lines on the phone, short labels. key, label, bar, direction (+1 higher is
# better). tests/test_phone_v2.py checks every key, bar and direction against scripts/northstar.py METRICS.
TREND_LINES = [
    ("mid_play", "Mid-play cuts per 100 landings", 0.0, -1),
    ("landings_dead", "Landings in dead time %", 98.0, 1),
    ("pct_of_oracle", "% of hindsight-oracle value", 95.0, 1),
    ("big_live_nfl", "Big plays live, NFL %", 95.0, 1),
    ("big_live_nba", "Big plays live, NBA %", 95.0, 1),
    ("big_live_nhl", "Big plays live, NHL %", 95.0, 1),
    ("big_seen_3min", "Big plays seen within 3 min %", 99.0, 1),
    ("your_team_big", "Your-team big plays seen %", 100.0, 1),
    ("your_team_crunch", "Your-team crunch live %", 100.0, 1),
    ("crunch_live_nfl", "Crunch live, NFL %", 95.0, 1),
    ("crunch_live_nba", "Crunch live, NBA %", 95.0, 1),
    ("crunch_live_nhl", "Crunch live, NHL %", 95.0, 1),
    ("dead_air", "Dead air %", 0.0, -1),
    ("switch_p95", "Switch time p95 (s)", 3.0, -1),
    ("press_to_tv_p95", "Phone press to TV p95 (s)", 3.0, -1),
]


def trend(rows: list[dict], split: str = "dev", last: int = 30) -> dict:
    """The North Star history for one split as one series per headline line, oldest first. Each line:
    points (ts, commit, v, status), latest value and status, change since the first point (in the good
    direction = positive `better`), and the gap to its bar. Read-only: the harness writes the file."""
    rows = [r for r in rows if r.get("split") == split][-last:]
    lines = []
    for key, label, bar, d in TREND_LINES:
        pts = [{"ts": r.get("ts"), "commit": r.get("commit"), "v": (r.get("values") or {}).get(key),
                "status": (r.get("status") or {}).get(key)} for r in rows]
        have = [p for p in pts if isinstance(p["v"], (int, float))]
        first, latest = (have[0]["v"], have[-1]["v"]) if have else (None, None)
        lines.append({"key": key, "label": label, "bar": bar, "dir": d, "points": pts, "latest": latest,
                      "status": pts[-1]["status"] if pts else None,
                      "better": round((latest - first) * d, 2) if have else None,
                      "gap": round(max(0.0, (bar - latest) * d), 2) if have else None})
    return {"split": split, "runs": len(rows), "commit": rows[-1].get("commit") if rows else None,
            "ts": rows[-1].get("ts") if rows else None, "lines": lines,
            "passing": sum(1 for x in lines if x["status"] == "PASS")}


def read_history(path: Path) -> list[dict]:
    try:
        text = Path(path).read_text()
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


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
        view = night_view(sws, decs, taps, engine._name, end)
        from redzone.lab.decision_records import read_jsonl
        over = oracle_overlay(view["segments"], read_oracle(ORACLE_DIR / f"{d.isoformat()}.json"),
                              read_jsonl(MOMENTS, t0, t1), engine._name, end)
        return {"enabled": True, "day": d.isoformat(), "t0": t0, "t1": t1, "now": now, "nights": nights,
                **view, **over}

    @r.get("/trend")
    async def get_trend(split: str = "dev"):
        if not _enabled(engine):
            return _off()
        if split not in ("dev", "locked"):
            return {"ok": False, "message": "split is dev or locked"}
        return {"enabled": True, **trend(read_history(HISTORY), split)}

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
