"""Phone page v2 (flag `phone.v2`, default OFF): why each switch happened, rate it, "I missed that",
and the Noggin Priority L2 sliders.

North Star: none of these move a scoring line directly. They feed two of them:
  * Nick, Nov 4 10:55 AM: the Noggin is fully self-taught. Votes and "I missed that" are recorded for
    the phone page only; no training, labelling, suggestion or North Star path reads them.
Nothing here touches the decider, the tick loop or the TV. Votes and misses only write rows to the
existing `feedback` table (note = "switch" | "missed"), so turning the flag off leaves no trace in play.

Why: first added in de443478 (2026-11-04) as "Phone v2 (flag off): reason chips + 👍/👎 per switch, 'I missed that', Priority L2 sliders"; no separate decision is recorded for this module.
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

from redzone.crunch import ONE_SCORE_NFL
from redzone.fsm import MachineMap
from redzone.machines.platform import CRUNCH
from redzone.models import PERIOD_S, REGULATION, League, Phase, league_order
from redzone.noggin.priority import HardRules, Preferences
from redzone.web import ledger as ledger_mod

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
LIVE_NIGHTS = DATA / "live_nights"         # <night>/summary.json: the supervisor's auto-log (collect_live_night)
REPORTS = (DATA / "reports", DATA.parent / "docs" / "nights")   # <night>.md: night report (supervisor, lane 4 #137)
BUFFER_LOG = DATA / "buffer.jsonl"        # STK-06 holds that went bad and their rebuilds (control._buffer_log)
CRUNCH_DIR = DATA / "crunch"              # <night>.json: learned crunch spans vs the oracle's (lane 1 grader)
WORTH_LOG = DATA / "worth.jsonl"          # [EXP-040] "worth it?" per night: EVALUATION ONLY, never a training label
DEPLOY_STATE = DATA / "deploy" / "state.json"   # scripts/deploy.py: good commit + deploy / rollback history
RELEASE_NAMES = DATA.parent / "docs" / "releases.json"   # {"<7-char commit>": "RC6.1", ...} (lane 4 keeps it)
REEL_REQUESTS = DATA / "reel_requests.jsonl"   # "I missed that" -> a catch-up request (record only)
REPLAY_DUE_S = 180.0    # Nick, Nov 4 11:00 AM: a missed key moment airs within 3 min, at the next dead moment
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


def reel_request(guess: dict | None, now: float, reel_play: bool) -> dict | None:
    """A catch-up request for the play Nick says he missed: replay it at the next dead moment, due within
    REPLAY_DUE_S. RECORD ONLY: nothing reads these to train, grade or suggest (self-taught rule), and with
    `reel.play` off (the default) nothing plays them either; the state says so. None without a play."""
    if not guess:
        return None
    return {"ts": round(now, 3), "game_id": guess["game_id"], "matchup": guess.get("matchup"),
            "play_id": guess.get("play_id"), "kind": guess.get("kind"), "text": (guess.get("text") or "")[:160],
            "play_ago_s": guess.get("ago_s"), "due_by": round(now + REPLAY_DUE_S, 3),
            "state": "queued" if reel_play else "recorded_reels_off"}


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


# Crunch timeline: the learned crunch state (#166) beside the hindsight oracle's crunch, per game, so the
# crunch_exit_acc misses are visible. data/crunch/<night>.json (written offline by lane 1's grader, the same
# rule as scripts/train_watch_value.crunch_card):
#   {"tol_s": 60, "games": {"<gid>": {"learned": [[t0, t1], ...], "oracle": [[t0, t1], ...]}}}
# Entry / exit is the first-on / last-on time of each; it's right when within tol_s of the oracle's. Until
# the file exists, the learned side is sampled from the switches' provenance (crunch_state, #173) and the
# oracle side is pending.
def read_crunch(path: Path) -> dict | None:
    try:
        d = json.loads(Path(path).read_text())
        games = {str(g): {k: sorted([float(a), float(b)] for a, b in (v.get(k) or []) if float(b) >= float(a))   # t0 == t1: one row
                          for k in ("learned", "oracle")} for g, v in (d.get("games") or {}).items()}
        tol = float(d["tol_s"])      # the grader's tolerance (train_watch_value.EXIT_TOL_S); required
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        return None
    return {"tol_s": tol, "games": games}


def crunch_timeline(crunch: dict | None, switches: list[dict], name) -> dict:
    """Per game: learned and oracle crunch spans, entry / exit deltas and whether each is within tol_s."""
    from redzone import trace as tr
    rows, tol = [], None
    if crunch:
        tol = crunch["tol_s"]
        for gid, v in crunch["games"].items():
            le, orc = v["learned"], v["oracle"]
            r = {"game": gid, "matchup": name(gid), "learned": le, "oracle": orc, "points": []}
            if le and orc:
                r["entry_s"], r["exit_s"] = round(le[0][0] - orc[0][0], 1), round(le[-1][1] - orc[-1][1], 1)
                r["entry_ok"], r["exit_ok"] = abs(r["entry_s"]) <= tol, abs(r["exit_s"]) <= tol
            elif orc:
                r["entry_ok"] = r["exit_ok"] = False        # the oracle saw crunch; the model never did
            rows.append(r)
    else:
        pts: dict[str, list] = {}
        for s in switches:
            p = (tr.of_row(s) or {}).get("provenance")
            cs = p.get("crunch_state") if isinstance(p, dict) else None
            sides = cs if isinstance(cs, dict) else {"to": cs}
            for side, gid in (("to", s.get("to_game")), ("from", s.get("frm"))):
                st = sides.get(side)
                if gid and isinstance(st, str) and st in CRUNCH_STATES:
                    pts.setdefault(gid, []).append({"ts": float(s["ts"]), "state": st})
        rows = [{"game": g, "matchup": name(g), "learned": [], "oracle": [], "points": p} for g, p in pts.items()]
    graded = [r for r in rows if "exit_ok" in r]
    rows.sort(key=lambda r: (r.get("exit_ok", True), r["game"]))     # misses first
    return {"source": "graded" if crunch else ("provenance" if rows else None), "tol_s": tol, "games": rows,
            "exits": len(graded), "exits_ok": sum(1 for r in graded if r["exit_ok"]),
            "entries_ok": sum(1 for r in graded if r["entry_ok"])}


def _on_at(segs: list[dict], ts: float, key: str = "game") -> str | None:
    for sg in segs:
        if sg["t0"] <= ts < sg["t1"]:
            return sg[key]
    return None


# Provenance: which module contributed what to one switch. Lane 3's trace may carry a `provenance` dict
# (module -> what it said, e.g. {"watch_value": {...}, "crunch_state": "in", "importance": 0.8,
# "floors": [...], "planner": "crunch-overlap"}); until it does, the stub rebuilds what the row already
# holds: the decider's reason and kind, chips and playbook ids, the delays it planned with, and the
# guard's last check. Read only: nothing here is on the decision path.
PROV_ORDER = ("decider", "watch_value", "crunch_state", "importance", "stakes", "floors", "planner", "playbook", "guard",
              "control")   # lane 3: path, flip, resume, STK-07 left-hold


def provenance_view(row: dict, name) -> dict:
    """One switch as a chain of stages (trace contract events) plus each module's contribution."""
    from redzone import trace as tr
    t = tr.of_row(row) or {}
    chain = [{"stage": e.stage.value, "at": e.at, "component": e.component, "detail": e.detail}
             for e in (tr.events(t) if t else [])]
    reason = row.get("reason") or ""
    given = t.get("provenance")
    if isinstance(given, dict) and given:
        parts = [{"module": k, "value": given[k]} for k in PROV_ORDER if k in given]
        parts += [{"module": k, "value": v} for k, v in given.items() if k not in PROV_ORDER]
        source = "trace"
    else:
        parts = [{"module": "decider", "value": {"kind": row.get("kind") or "", "reason": reason}}]
        if playbooks(reason):
            parts.append({"module": "playbook", "value": playbooks(reason)})
        if PLAN.search(reason):
            parts.append({"module": "planner", "value": PLAN.search(reason).group(1)})
        if t.get("leave_delay") or t.get("enter_delay"):
            parts.append({"module": "sync", "value": {"leave": t.get("leave_delay"), "enter": t.get("enter_delay")}})
        g = next((c for c in chain if c["stage"] == "guarded"), None)
        if g:
            parts.append({"module": "guard", "value": g["detail"]})
        source = "stub"
    return {"key": _switch_key(row["ts"], row.get("to_game")), "ts": row["ts"], "from": name(row["frm"]) if row.get("frm") else "",
            "to": name(row.get("to_game")) if row.get("to_game") else "", "kind": row.get("kind") or "",
            "ok": bool(row.get("ok")), "dry_run": bool(row.get("dry_run")), "chips": chips(reason, row.get("kind")),
            "chain": chain, "parts": parts, "source": source, "has_trace": bool(t)}


def capture_view(night: str, switches: list[dict], summary: dict | None, buffer_rows: list[dict],
                 report: str | None, held_now: dict | None = None) -> dict:
    """"Live night" morning view: did tonight become gold data? The supervisor's auto-log
    (data/live_nights/<night>/summary.json), how many switches carry lane 3's provenance (#173) and which
    modules spoke, the hidden stick's held buffer (control.held now; data/buffer.jsonl for the night), and
    the night report if one is written. Read only: every input is already read."""
    from redzone import trace as tr
    keys: dict[str, int] = {}
    with_prov = with_trace = 0
    for row in switches:
        t = tr.of_row(row) or {}
        with_trace += bool(t)
        p = t.get("provenance")
        if isinstance(p, dict) and p:
            with_prov += 1
            for k in p:
                keys[k] = keys.get(k, 0) + 1
    order = [k for k in PROV_ORDER if k in keys] + sorted(k for k in keys if k not in PROV_ORDER)
    s = summary if isinstance(summary, dict) else None
    auto = None
    if s is not None:
        auto = {"rows": {k: v for k, v in (s.get("rows") or {}).items() if isinstance(v, int)},
                **{k: s.get(k) for k in ("cuts_graded", "entries_mid_play", "leaves_mid_play", "switch_p95_s",
                                         "picture_within_3s", "press_to_tv_p95_s")}}
    kinds: dict[str, int] = {}
    for r in buffer_rows:
        kinds[str(r.get("kind") or "?")] = kinds.get(str(r.get("kind") or "?"), 0) + 1
    n = len(switches)
    checks = [{"key": "autolog", "label": "Auto-log collected", "ok": auto is not None},
              {"key": "provenance", "label": "Switches with provenance", "ok": bool(n) and with_prov == n},
              {"key": "report", "label": "Night report written", "ok": report is not None}]
    return {"night": night, "switches": n, "with_trace": with_trace, "with_provenance": with_prov,
            "provenance_keys": [{"module": k, "n": keys[k]} for k in order], "autolog": auto,
            "held": {"now": {k: round(float(v), 1) for k, v in (held_now or {}).items()}, "log": kinds},
            "report": report, "checks": checks, "gold": all(c["ok"] for c in checks)}


SPOILER_SAFE_LABEL = "A play you missed"   # [EXP-026 display] no names, no play type, no score until it plays


def held_view(hidden_game: str | None, hidden_ready: bool, behind_s: float, home: str | None,
              home_return_at: float | None, queue: list, current: str | None, name, now: float,
              spoiler_safe: bool = False) -> dict | None:
    """Crunch vs crunch, "held + coming back": the game the hidden stick holds, how far behind live it
    is held (the time-shift buffer), when we head back to the game we left for a break, and the next
    catch-up in line. Display only. None when the hidden stick holds nothing and nothing is queued."""
    nxt = next((m for m in queue if not m.replayed), None)
    if not hidden_game and not home and nxt is None:
        return None
    back = None
    if home and home != current:
        back = {"game_id": home, "matchup": name(home), "kind": "return",
                "in_s": max(0, round(home_return_at - now)) if home_return_at else None,
                "text": f"back to {name(home)}" + (f" within {max(1, round((home_return_at - now) / 60))} min"
                                                   if home_return_at and home_return_at > now else " at its restart")}
    catch = None
    if nxt is not None:
        catch = {"game_id": nxt.game_id, "matchup": name(nxt.game_id), "kind": "play" if spoiler_safe else nxt.kind,
                 "label": SPOILER_SAFE_LABEL if spoiler_safe else nxt.label[:100],
                 "ago_s": max(0, round(now - nxt.t)), "mine": nxt.mine,
                 "due_in_s": max(0, round(nxt.t + nxt.due_s - now)) if nxt.due_s else None,
                 "text": "catch-up at the next dead moment"}
    return {"held": {"game_id": hidden_game, "matchup": name(hidden_game) if hidden_game else "",
                     "ready": bool(hidden_ready), "behind_s": round(behind_s, 1)} if hidden_game else None,
            "back": back, "catchup": catch, "queued": sum(1 for m in queue if not m.replayed)}


# Night scorecard: the release gate's per-night lines, so each morning shows last night at a glance.
# (key, label, bar, hard): hard lines must sit at the bar every night; the rest must not regress.
SCORE_LINES = (("mid_play_entries", "Mid-play entries", 0, True),
               ("high_leverage_leaves", "High-leverage mid-play leaves", 0, True),
               ("plays_missed_entry", "Plays missed on entry", 0, False),
               ("dead_air_pct", "Dead air %", 0, False))


def _night_line(key: str, metrics: dict, summary: dict):
    """One gate line for one night: the night report's metrics sidecar (data/reports/<night>.json) first,
    then the supervisor's auto-log summary; None (pending) when neither measures it."""
    v = metrics.get(key)
    if v is None and key == "mid_play_entries":
        v = metrics.get("landed_mid_play", summary.get("entries_mid_play"))
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def night_scorecard(nights: list[tuple[str, dict | None, dict | None]]) -> dict:
    """Per-night release-gate lines (oldest first) and each line's trend; `nights` is (night, metrics,
    auto-log summary). Read only."""
    rows = [(n, m or {}, s or {}) for n, m, s in sorted(nights) if m or s]
    lines = []
    for key, label, bar, hard in SCORE_LINES:
        pts = [{"night": n, "v": _night_line(key, m, s)} for n, m, s in rows]
        vals = [p["v"] for p in pts if p["v"] is not None]
        latest = pts[-1]["v"] if pts else None
        lines.append({"key": key, "label": label, "bar": bar, "hard": hard, "points": pts, "latest": latest,
                      "status": None if latest is None else ("PASS" if latest <= bar else ("FAIL" if hard else "OVER")),
                      "nights_at_bar": sum(1 for v in vals if v <= bar), "measured": len(vals)})
    last = rows[-1][0] if rows else None
    hard_ok = [ln["status"] == "PASS" for ln in lines if ln["hard"] and ln["latest"] is not None]
    return {"nights": [n for n, _, _ in rows], "last": last, "lines": lines,
            "gate": None if not hard_ok else all(hard_ok)}


def _short(c) -> str | None:
    return str(c)[:7] if c else None


def release_that_ran(state: dict, t0: float, t1: float, names: dict) -> dict | None:
    """Which release drove the TV at the end of the night [t0, t1), from scripts/deploy.py's state.json:
    the last history entry before t1 ("deployed" or "manual_rollback" -> its commit; "rolled_back" -> the
    commit it went back to), else the last good commit. `changes` lists every deploy / rollback inside the
    night, so "RC6 rolled back to RC5.1" shows. None when there's no deploy record at all."""
    state = state if isinstance(state, dict) else {}
    hist = sorted((h for h in state.get("history") or [] if isinstance(h, dict)
                   and isinstance(h.get("at"), (int, float))), key=lambda h: h["at"])
    nm = lambda c: names.get(_short(c)) if c else None   # noqa: E731
    before = [h for h in hist if h["at"] < t1]
    if before:
        last = before[-1]
        res = last.get("result")
        ran = last.get("from") if res == "rolled_back" else None if res == "rollback_failed" else last.get("commit")
    else:
        ran = (state.get("good") or {}).get("commit")
    changes = [{"result": h.get("result"), "commit": _short(h.get("commit")), "name": nm(h.get("commit")),
                "from": _short(h.get("from")), "from_name": nm(h.get("from")), "at": h["at"]}
               for h in hist if t0 <= h["at"] < t1]
    if not ran and not changes:
        return None
    return {"commit": _short(ran), "name": nm(ran), "changes": changes}


LAST_NIGHT_LINES = ("mid_play_entries", "high_leverage_leaves", "dead_air_pct")


def last_night_card(night: str, metrics: dict | None, summary: dict | None, worth: dict | None,
                    release: dict | None) -> dict:
    """The morning "Last night" card: worth it (EXP-040, evaluation only), mid-play entries and high-leverage
    leaves (hard, bar 0), dead air %, and which release ran. A line nobody measured is None (pending), never 0."""
    m, s = metrics or {}, summary or {}
    lines = {k: _night_line(k, m, s) for k in LAST_NIGHT_LINES}
    hard = [lines[k] for k in ("mid_play_entries", "high_leverage_leaves") if lines[k] is not None]
    r = (worth or {}).get("rating")
    return {"night": night, "worth": r if isinstance(r, int) and 1 <= r <= 5 else None, **lines,
            "rule1": None if not hard else all(v <= 0 for v in hard), "release": release,
            "measured": any(v is not None for v in lines.values())}


LEFT_END = {"left_resumed": "resumed", "left_expired": "expired", "left_lost": "lost"}   # STK-07 (#171)


def catchup_queue(buffer_rows: list[dict], target_game: dict, hidden_target: str | None, hidden_ready: bool,
                  held: dict, queue: list, scored: dict, name, now: float, view: dict | None = None) -> dict:
    """The catch-up queue (your-team catch-ups were the locked blocker: 73.5 vs 82.4): what the hidden stick
    holds now (control.held: seconds behind live), tonight's STK-07 left-game holds from data/buffer.jsonl
    (left_held -> left_resumed | left_expired | left_lost) with their age, and every catch-up waiting in the
    decider's queue, your team first, each with whether its game is in a dead window right now
    (Scored.is_break). Display only."""
    holds: list[dict] = []
    open_: dict = {}
    for r in sorted(buffer_rows, key=lambda r: float(r.get("ts") or 0)):
        k, t = r.get("kind"), r.get("target")
        if k == "left_held":
            open_[t] = {"target": t, "game_id": target_game.get(t), "held_at": float(r["ts"]),
                        "base_s": float(r.get("behind_s") or 0.0), "end": None, "ended_at": None}
            holds.append(open_[t])
        elif k in LEFT_END and t in open_:
            h = open_.pop(t)
            h["end"], h["ended_at"] = LEFT_END[k], float(r["ts"])
    for h in holds:
        h["matchup"] = name(h["game_id"]) if h["game_id"] else str(h["target"])
        h["age_s"] = round((h["ended_at"] or now) - h["held_at"] + h["base_s"], 1)
        h["open"] = h["end"] is None and h["target"] == hidden_target
    hid = target_game.get(hidden_target) if hidden_target else None
    sc = scored or {}
    dead = lambda gid: bool(sc[gid].is_break) if gid in sc else None   # noqa: E731
    # [EXP-026 display] `view` = {"visible": n} (phone.catchup_view): the decider's own queue order (what plays
    # next, whatever order the planner picked), spoiler-safe labels, and only the first n shown
    if view:
        waiting = [m for m in queue if not m.replayed]
    else:
        waiting = sorted((m for m in queue if not m.replayed), key=lambda m: (not m.must, not m.mine, m.t))
    safe = bool(view)
    items = [{"game_id": m.game_id, "matchup": name(m.game_id), "label": SPOILER_SAFE_LABEL if safe else m.label[:100],
              "kind": "play" if safe else m.kind,
              "mine": bool(m.mine), "must": bool(m.must), "ago_s": max(0, round(now - m.t)),
              "due_in_s": max(0, round(m.t + m.due_s - now)) if m.due_s else None, "dead_now": dead(m.game_id)}
             for m in waiting]
    ends = [h["end"] for h in holds]
    total = len(items)
    if view:
        items = items[:max(1, int(view["visible"]))]
    return {"spoiler_safe": safe, "more": total - len(items), "hidden": {"game_id": hid, "matchup": name(hid) if hid else (hidden_target or ""), "target": hidden_target,
                       "ready": bool(hidden_ready), "behind_s": round(float(held.get(hidden_target, 0.0)), 1),
                       "dead_now": dead(hid) if hid else None} if hidden_target else None,
            "holds": holds[::-1], "queue": items,
            "counts": {"held": len(holds), "resumed": ends.count("resumed"), "expired": ends.count("expired"),
                       "lost": ends.count("lost"), "queued": total,
                       "yours": sum(1 for m in waiting if m.mine)}}


CATCHUP_KINDS = {"replay"}                                # a catch-up replay of a missed play
INTERLEAVE_KINDS = {"break_visit", "return"}             # visit another game in a break, then come back
PLAN = re.compile(r"\bplan=([A-Za-z0-9_-]+)")


def plan_marks(switches: list[dict], name) -> list[dict]:
    """The planner's catch-ups and interleaves on the night's switches, oldest first, for the replay
    viewer: a catch-up is a replay of a missed play or the catch-up reel; an interleave is a break visit
    to another game and the return home. A `plan=<name>` tag in the reason is kept (multi-step plans)."""
    out = []
    for sw in switches:
        kind, to = sw.get("kind") or "", sw.get("to_game")
        if kind in CATCHUP_KINDS or to == "reel":
            typ = "catchup"
        elif kind in INTERLEAVE_KINDS:
            typ = "interleave"
        else:
            continue
        m = PLAN.search(sw.get("reason") or "")
        out.append({"ts": sw["ts"], "type": typ, "kind": kind, "game": to,
                    "matchup": "Catch-up reel" if to == "reel" else name(to), "reason": sw.get("reason") or "",
                    "plan": m.group(1) if m else None, "ok": bool(sw.get("ok")) or bool(sw.get("dry_run"))})
    return out


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


# EXP-035 "why we're here": the tag shows only when the game on screen is at or over a fixed, logged
# threshold on the 0-100 scale (learned watch value when it exists, else today's score), so a label never
# claims more than the number behind it. lab/decision_records grades every switch that crossed it:
# "alerted but nothing happened" = no big play in that game within its outcome window. The threshold is
# config phone.why_tag_min (one number for the phone and the grader).


def why_tag(s, threshold: float) -> dict | None:
    """The tag for the game on screen (a Scored), or None when it's under the threshold or has no why."""
    if s is None:
        return None
    learned = s.watch_value is not None
    val = float(s.watch_value) if learned else float(s.score)
    text = ((s.watch_why if learned else "") or s.factor or s.reason or "").strip()
    if val < threshold or not text:
        return None
    return {"text": text[:100], "value": int(round(min(100.0, val))), "threshold": threshold,
            "source": "learned" if learned else "stub"}


# EXP-037: one question the game will answer soon, in the trailing side's words ("UNC down 3, 1:12 left,
# no timeouts"). Only late and close, so it never frames a question the game won't answer soon: the last
# regulation period or overtime, at most CONTEXT_LATE_FRAC of a period left, within one score.
CONTEXT_MARGIN = {League.NFL: ONE_SCORE_NFL, League.NBA: 6, League.NHL: 1, League.MLB: 3}
CONTEXT_LATE_FRAC = 0.42      # NFL/NBA: about the last 5-6 minutes of the 4th (and the last 8 of an NHL 3rd)


def _clock(s: float) -> str:
    s = max(0, int(round(s)))
    return f"{s // 60}:{s % 60:02d}"


def _ord(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def context_line(g) -> str | None:
    """The one-question context line for a live game (a GameState), or None when it isn't late and close."""
    if g is None or g.phase != Phase.LIVE or g.home.score is None or g.away.score is None or not g.period:
        return None
    base = g.league.base
    diff = g.home.score - g.away.score
    if abs(diff) > CONTEXT_MARGIN.get(base, 0):
        return None
    reg = REGULATION.get(g.league, 4)
    trail, lead = (g.home, g.away) if diff < 0 else (g.away, g.home)
    sit = g.situation
    who = "Tied" if diff == 0 else f"{trail.abbr} down {abs(diff)}"
    if base == League.MLB:
        if g.period < reg:
            return None
        bits = [who, f"{(g.half or '').lower()} {_ord(g.period)}".strip()]
        if sit.outs is not None:
            bits.append(f"{sit.outs} out" + ("" if sit.outs == 1 else "s"))
        if sit.runners:
            on = sum(1 for r in sit.runners if r)
            bits.append("bases loaded" if on == 3 else f"{on} on")
        return ", ".join(bits)
    if g.clock_s is None or g.period < reg:
        return None
    ot = g.period > reg or (g.period_type or "").upper() in ("OT", "SO")
    if not ot and g.clock_s > CONTEXT_LATE_FRAC * PERIOD_S.get(g.league, 900):
        return None
    bits = [who, ("OT, " if ot else "") + f"{_clock(g.clock_s)} left"]
    if base == League.NFL and sit.possession and sit.down:
        bits.append(f"{sit.possession} ball, {_ord(sit.down)} & {'goal' if sit.goal_to_go else sit.distance}"
                    + (f" at the {sit.yards_to_goal}" if sit.yards_to_goal is not None else ""))
    if base == League.NHL:
        if sit.empty_net:
            bits.append("goalie pulled" if sit.empty_net == trail.abbr else f"{sit.empty_net} net empty")
        elif sit.power_play:
            bits.append(f"{sit.power_play} power play")
    if base in (League.NFL, League.NBA) and diff != 0:
        to = sit.timeouts_home if trail is g.home else sit.timeouts_away
        if to is not None:
            bits.append("no timeouts" if to == 0 else f"{to} timeout" + ("" if to == 1 else "s"))
    return ", ".join(bits)


def _period_label(g) -> str:
    """'Q4' / '2nd half' / 'P3' / 'OT' / 'Bot 9th'."""
    reg, p = REGULATION.get(g.league, 4), g.period or 0
    base = g.league.base
    if base == League.MLB:
        return f"{'Top' if (g.half or '').lower() == 'top' else 'Bot'} {_ord(p)}" if p else ""
    if p > reg or (g.period_type or "").upper() in ("OT", "SO"):
        return "SO" if (g.period_type or "").upper() == "SO" else ("OT" if p == reg + 1 else f"{p - reg}OT")
    if reg == 2:
        return f"{_ord(p)} half" if p else ""
    return (f"P{p}" if base == League.NHL else f"Q{p}") if p else ""


def orientation(g) -> dict | None:
    """[EXP-038] The 2-3 s orientation beat after a switch: who, the score, the clock and one situation
    fact, so the first seconds on a new game aren't spent working out what's going on. Plain facts only
    (playbook "line" section: no hype, no loaded wording); None when the game isn't live."""
    if g is None or g.phase != Phase.LIVE:
        return None
    sit, base = g.situation, g.league.base
    score = (f"{g.away.abbr} {g.away.score} · {g.home.abbr} {g.home.score}"
             if g.away.score is not None and g.home.score is not None else g.matchup)
    clock = " ".join(x for x in (_period_label(g), _clock(g.clock_s) if g.clock_s is not None and base != League.MLB
                                 else "") if x)
    fact = ""
    if base == League.NFL and sit.possession and sit.down:
        fact = f"{sit.possession} ball, {_ord(sit.down)} & {'goal' if sit.goal_to_go else sit.distance}" + (
            f" at the {sit.yards_to_goal}" if sit.yards_to_goal is not None else "")
    elif base == League.NHL:
        fact = (f"{sit.empty_net} net empty" if sit.empty_net else
                f"{sit.power_play} power play" + (f" ({sit.strength})" if sit.strength else "") if sit.power_play else "")
    elif base == League.MLB:
        bits = []
        if sit.outs is not None:
            bits.append(f"{sit.outs} out" + ("" if sit.outs == 1 else "s"))
        if sit.runners:
            on = sum(1 for r in sit.runners if r)
            bits.append("bases loaded" if on == 3 else f"{on} on" if on else "bases empty")
        fact = ", ".join(bits)
    return {"game_id": g.game_id, "league": g.league.value, "matchup": g.matchup, "score": score,
            "clock": clock, "fact": fact, "context": context_line(g)}


ALSO_CLOSE_MAX = 2   # [EXP-039] at most this many: a quiet line, not a second scoreboard


def also_close(games: dict, current: str | None, order: tuple[str, ...]) -> list[dict]:
    """[EXP-039] Other live games that are late and within one score right now (context_line says so),
    in league order, at most ALSO_CLOSE_MAX. It answers "is anything else close?" without a switch: the
    phone shows it as text with no Watch button. The game on screen is never listed."""
    rows = []
    for gid, g in (games or {}).items():
        if gid == current:
            continue
        ctx = context_line(g)
        if ctx:
            rows.append({"game_id": gid, "matchup": g.matchup, "league": g.league.value, "context": ctx,
                         "rank": order.index(g.league.value) if g.league.value in order else len(order)})
    rows.sort(key=lambda r: (r["rank"], r["matchup"]))
    for r in rows:
        r.pop("rank")
    return rows[:ALSO_CLOSE_MAX]


FOLLOW_MAX = 12        # [EXP-043] followed players kept at most (a short list, not a fantasy roster)
FOLLOW_NAME_MAX = 40   # characters per name


def follow_names(raw) -> list[str] | None:
    """[EXP-043] Clean a followed-players list from the phone: trimmed, 2-40 characters, letters and
    . ' - and spaces only, case-insensitive duplicates dropped, at most FOLLOW_MAX. None when it isn't a list."""
    if isinstance(raw, str):
        raw = raw.split(",")
    if not isinstance(raw, list):
        return None
    out, seen = [], set()
    for x in raw:
        if not isinstance(x, str):
            continue
        n = " ".join(x.split())[:FOLLOW_NAME_MAX]
        if len(n) < 2 or not re.fullmatch(r"[A-Za-z\u00C0-\u024F.'\- ]+", n) or n.lower() in seen:
            continue
        seen.add(n.lower())
        out.append(n)
    return out[:FOLLOW_MAX]


def _surname(name: str) -> str:
    parts = [p for p in name.lower().replace(".", " ").split() if p not in ("jr", "sr", "ii", "iii", "iv")]
    return parts[-1] if parts else ""


def followed_in(g, players: list[str]) -> list[dict]:
    """[EXP-043] Which followed players show up in this game right now, and where: "spotlight" (the
    latest big play), "leader" (a top performer) or "flag" (a going-off line like "Jokic 38 pts").
    Full names match leaders and the spotlight exactly (case-insensitive); the short forms ("N. Jokic",
    flag lines) match on the surname as a whole word. One row per player, the most specific place wins."""
    if g is None or not players:
        return []
    out = []
    for name in players:
        full, sur = name.lower(), _surname(name)
        if not sur:
            continue
        word = re.compile(rf"(?<![a-z]){re.escape(sur)}(?![a-z])")

        def hit(d) -> bool:
            n, sh = str((d or {}).get("name") or "").lower(), str((d or {}).get("short") or "").lower()
            if n:   # a full name on the row is decisive: "Keenan Allen" never matches Josh Allen's row
                return n == full or (" " not in full and _surname(n) == sur)
            if not (sh and word.search(sh)):
                return False
            # "J. Allen": the initial must agree with the followed first name when both exist
            return " " not in full or " " not in sh.strip() or sh.strip()[0] == full[0]

        sp = g.spotlight if isinstance(getattr(g, "spotlight", None), dict) else None
        if sp and hit(sp):
            out.append({"player": name, "where": "spotlight", "text": str(sp.get("text") or "")[:80]})
            continue
        lead = next((d for d in (getattr(g, "leaders", None) or []) if isinstance(d, dict) and hit(d)), None)
        if lead:
            out.append({"player": name, "where": "leader",
                        "text": " ".join(str(lead.get(k) or "") for k in ("short", "stat")).strip()[:80]})
            continue
        flag = next((f for f in (getattr(g, "player_flags", None) or []) if word.search(str(f).lower())), None)
        if flag:
            out.append({"player": name, "where": "flag", "text": str(flag)[:80]})
    return out


def follow_view(games: dict, players: list[str], current: str | None) -> list[dict]:
    """[EXP-043] Live games with a followed player in them, the game on screen first, then by matchup.
    Display only: no score, rank or switch reads it (lane 1 owns any weight)."""
    rows = []
    for gid, g in (games or {}).items():
        if getattr(g, "phase", None) != Phase.LIVE:
            continue
        hits = followed_in(g, players)
        if hits:
            rows.append({"game_id": gid, "matchup": g.matchup, "league": g.league.value,
                         "on": gid == current, "players": hits})
    rows.sort(key=lambda r: (not r["on"], r["matchup"]))
    return rows


RISING_MIN = 1.0    # watch_rising (points per minute) at or above this shows an up arrow, at or below -this a down one


def slate_watch(rows: list[dict], scored: dict, on: bool) -> list[dict]:
    """HEAT 2.0 on the slate: each live game's learned watch value (0-100), its why line and a rising /
    falling arrow, from Scored.watch_value / watch_why / watch_rising (#166 contract). Hidden (no "wv" key)
    when scoring.watch_value is off or the game has no learned value yet. Display only."""
    for r in rows:
        s = (scored or {}).get(r["game_id"]) if on and r.get("live") else None
        if s is None or s.watch_value is None:
            continue
        rise = s.watch_rising
        r["wv"] = {"value": int(round(max(0.0, min(100.0, float(s.watch_value))))), "why": (s.watch_why or "")[:120],
                   "rising": None if rise is None else round(float(rise), 1),
                   "arrow": "" if rise is None or abs(rise) < RISING_MIN else ("up" if rise > 0 else "down")}
    return rows


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
    # EXP-036: nothing live, nothing still to start, and tonight had games: the slate is complete (the
    # phone shows that and nothing after it: no autoplay, no "up next")
    done = sum(1 for g in (games or {}).values() if g.phase == Phase.POST)
    return {"games": rows, "clashes": [c for c in clashes if c["t1"] - c["t0"] >= 120],
            "yours": sum(1 for r in rows if r["yours"]), "complete": not rows and done > 0, "finished": done}


def worth_rows(path: Path | None = None) -> dict[str, dict]:
    """[EXP-040] night -> its last "worth it?" row. Evaluation only (redzone/lab/worth.py grades it)."""
    out: dict[str, dict] = {}
    try:
        lines = (path or WORTH_LOG).read_text().splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("night"):
            out[str(row["night"])] = row
    return out


def worth_view(rows: dict[str, dict], night: date, complete: bool, finished: int, probe_days: int) -> dict:
    """What the Slate complete card asks: the rating (once a night) and, at most once every `probe_days`
    nights, the agency / regret probes. Nothing is asked before the slate is complete."""
    key = night.isoformat()
    last_probe = max((n for n, r in rows.items() if r.get("agency") is not None and n < key), default=None)
    gap = (night - date.fromisoformat(last_probe)).days if last_probe else None
    mine = rows.get(key) or {}
    return {"night": key, "complete": complete, "finished": finished, "rating": mine.get("rating"),
            "probe_due": complete and mine.get("agency") is None and (gap is None or gap >= probe_days),
            "agency": mine.get("agency"), "regret": mine.get("regret")}


def worth_row(body: dict, night: date, now: float, prev: dict | None) -> dict | None:
    """Validate one tap: rating 1-5, optional agency 1-5 and regret yes/no. Later taps fill in or replace
    fields on tonight's row (the last row per night wins), so a probe after the rating keeps the rating."""
    row = {"night": night.isoformat(), "ts": round(now, 1)}
    for k in ("rating", "agency", "regret"):
        if k in (prev or {}):
            row[k] = prev[k]
    r, a, g = body.get("rating"), body.get("agency"), body.get("regret")
    if r is not None:
        if not isinstance(r, int) or isinstance(r, bool) or not 1 <= r <= 5:
            return None
        row["rating"] = r
    if a is not None:
        if not isinstance(a, int) or isinstance(a, bool) or not 1 <= a <= 5:
            return None
        row["agency"] = a
    if g is not None:
        if not isinstance(g, bool):
            return None
        row["regret"] = g
    return row if any(k in row for k in ("rating", "agency", "regret")) else None


CRUNCH_STATES = ("entering", "in", "leaving")
STAKES_BIG, STAKES_SOME = 0.6, 0.3     # stub only: Scored.stakes (0..1) until lane 1 sets stakes_line


class CrunchTracker:
    """phone.crunch, one machine per game, for the stub (no crunch model yet). The flags alone are
    memoryless, so "leaving" could never be produced: it needs to know the game WAS in crunch time.
    in -> leaving when crunch_now drops; leaving shows for leave_s, then entering/out by the flags."""

    def __init__(self, clock=time.time, leave_s: float = 60.0):
        self.clock, self.leave_s = clock, leave_s
        self.games = MachineMap(CRUNCH, clock=clock)

    def state(self, gid: str, raw: str | None) -> str | None:
        """raw: the flags' reading ("in" | "entering" | None). Returns what the phone shows."""
        m, now = self.games[gid], self.clock()
        if raw == "in":
            m.to("in", "inside the crunch window", now)
        elif m.is_("in"):
            m.to("leaving", "dropped out of the crunch window", now)
        elif m.is_("leaving"):
            if m.age(now) >= self.leave_s:
                m.to(raw or "out", f"{self.leave_s:.0f}s after leaving crunch time", now)
        else:
            m.to(raw or "out", "crunch run-up" if raw else "not crunch time", now)
        return None if m.is_("out") else m.state

    def keep(self, gids: set) -> None:
        """Games no longer listed (ended, not live) go back to out and are forgotten."""
        for gid, m in self.games.items():
            if gid not in gids and not m.is_("out"):
                m.to("out", "no longer a live game", self.clock())
        self.games.prune()


def crunch_tracker(engine) -> CrunchTracker | None:
    """The engine's tracker (created on first use); None when the engine can't hold one."""
    t = getattr(engine, "_crunch_tracker", None)
    if t is None:
        try:
            t = CrunchTracker(leave_s=float(engine.cfg.get("phone.crunch_leave_s", 60.0)))
            engine._crunch_tracker = t
        except Exception:
            return None
    return t


def watch_values(scored: dict, games: dict, tracker: CrunchTracker | None = None) -> dict:
    """Each live game's 0-100 watch value and its one-line why, best first (display only).
    Contract with lane 1 (HEAT 2.0): Scored.watch_value (0-100) and Scored.watch_why. While watch_value is
    None, the stub shows today's priority score clipped to 0-100 with its biggest factor, marked
    source="stub" so the phone says it's the old score.
    crunch_state: lane 1's crunch model sets `Scored.crunch_state` in CRUNCH_STATES; until then the stub
    reads today's flags (crunch_now -> "in", crunch -> "entering"); with a CrunchTracker (the live /watch
    route passes the engine's) a game that leaves crunch time shows "leaving" for phone.crunch_leave_s.
    stakes_line: lane 1 sets `Scored.stakes_line` (e.g. "AFC 1-seed race, top-5 matchup"); until then the
    stub turns Scored.stakes into "Big stakes" / "Some stakes" / ""."""
    rows = []
    for gid, s in (scored or {}).items():
        g = (games or {}).get(gid)
        if g is not None and g.phase != Phase.LIVE:
            continue
        learned = s.watch_value is not None
        val = float(s.watch_value) if learned else float(s.score)
        why = (s.watch_why if learned else "") or s.factor or s.reason
        cs = s.crunch_state                           # lane 1's crunch model: entering | in | leaving
        if cs not in CRUNCH_STATES:
            cs = "in" if s.crunch_now else ("entering" if s.crunch else None)
            if tracker is not None:
                cs = tracker.state(gid, cs)
        sl = s.stakes_line                            # lane 1: e.g. "AFC 1-seed race, top-5 matchup"
        stakes_rough = sl is None                     # #172 writes it (None = no stakes); the stub is "rough"
        if sl is None:
            sl = "Big stakes" if s.stakes >= STAKES_BIG else ("Some stakes" if s.stakes >= STAKES_SOME else "")
        rows.append({"game_id": gid, "matchup": g.matchup if g is not None else gid,
                     "crunch_state": cs, "stakes_line": sl[:100], "context": context_line(g), "stakes_rough": stakes_rough and bool(sl),
                     "league": s.league.value, "wv": int(round(max(0.0, min(100.0, val)))),
                     "why": str(why or "")[:120], "crunch": bool(s.crunch),
                     "is_break": bool(s.is_break), "source": "learned" if learned else "stub"})
    if tracker is not None:
        tracker.keep({r["game_id"] for r in rows})
    rows.sort(key=lambda r: -r["wv"])
    return {"games": rows, "source": "learned" if rows and all(r["source"] == "learned" for r in rows)
            else ("stub" if rows else None)}


ALERT_AHEAD_S = 1800.0   # warn about a your-team crunch clash this far ahead


def clash_alert(slate: dict, now: float, ahead: float = ALERT_AHEAD_S) -> dict | None:
    """The next crunch clash that involves Nick's team, starting within `ahead` s (or under way), as one
    phone banner: who gets the TV and who goes to the second stick / catch-up. Display only."""
    yours = {g["game_id"] for g in slate.get("games") or [] if g.get("yours")}
    for c in slate.get("clashes") or []:
        if c["t1"] <= now or c["t0"] - now > ahead or not yours.intersection(c["games"]):
            continue
        return {"t0": c["t0"], "t1": c["t1"], "in_s": max(0, round(c["t0"] - now)), "on_tv": c["on_tv"],
                "second": c["second"], "why": c["why"], "now": c["t0"] <= now}
    return None


def ladder_line(control, now: float) -> dict | None:
    """[fallback ladder] The last join that fell back (15 min), e.g. "Stick on HDMI2: buffered failed (no label
    read) -> live edge ok", and rungs cooling on a stick. None with the flag off. Display only."""
    lad = getattr(control, "ladder", None)
    if lad is None or not lad.enabled:
        return None
    v = lad.phone_view(now)
    return v if (v["last"] or v["cooling"]) else None


BEHIND_MIN_S = 5.0   # a stick held this far behind live is showing the past


def screen_behind(engine, gid: str | None) -> bool:
    """True when the TV shows `gid` behind live: a catch-up replay, or a stick held (time-shifted) by more than
    BEHIND_MIN_S. The live feed would spoil whatever is on screen, so the phone must say nothing from it."""
    dec = engine.decider
    if gid is None or gid != dec.current:
        return False
    if getattr(dec, "replaying", None):
        return True
    target = getattr(engine.where.get(gid), "target", None)
    try:
        return bool(target) and float(engine.control.held.get(target) or 0.0) >= BEHIND_MIN_S
    except (TypeError, ValueError):
        return False


def router(engine) -> APIRouter:
    r = APIRouter(prefix="/api/v2")

    @r.get("/status")
    async def status():
        return {"enabled": _enabled(engine), "commit": _enabled(engine) and bool(engine.cfg.get("phone.commit")),
                "orient": _enabled(engine) and bool(engine.cfg.get("phone.orient")),
                "worth": bool(engine.cfg.get("phone.worth")),
                "follow": _enabled(engine) and bool(engine.cfg.get("phone.follow")),
                "last_night": _enabled(engine) and bool(engine.cfg.get("phone.last_night"))}

    def _worth_now():
        now = time.time()
        v = slate_preview(getattr(engine.merger, "games", {}), HardRules.from_config(engine.cfg), now)
        return now, night_of(now), v

    @r.get("/worth")
    async def worth_get():
        # [EXP-040] its own flag: the rating card works with phone.v2 off
        if not engine.cfg.get("phone.worth"):
            return _off()
        _, night, v = _worth_now()
        return {"enabled": True, **worth_view(worth_rows(), night, v["complete"], v["finished"],
                                              int(engine.cfg.get("phone.worth_probe_days")))}

    @r.post("/worth")
    async def worth_post(body: dict):
        if not engine.cfg.get("phone.worth"):
            return _off()
        now, night, _ = _worth_now()
        row = worth_row(body or {}, night, now, worth_rows().get(night.isoformat()))
        if row is None:
            return JSONResponse({"ok": False, "message": "Pick 1 to 5"}, status_code=400)
        try:
            with WORTH_LOG.open("a") as f:
                f.write(json.dumps(row) + "\n")
        except OSError:
            return {"ok": False, "message": "Couldn't save that"}
        return {"ok": True, "message": "Thanks, noted", "row": row}

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
        try:
            v = int(body.get("vote") or 0)
        except (TypeError, ValueError):   # "x", [1], {}: a 200 with ok false, not a 500 and a logged traceback
            return {"ok": False, "message": "vote is -1, 0 or 1"}
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
        req = reel_request(guess, now, bool(engine.cfg.get("reel.play", False)))
        if req:
            try:
                with REEL_REQUESTS.open("a") as f:
                    f.write(json.dumps(req) + "\n")
            except OSError:
                req = None   # a full disk or read-only data dir: the tap is still logged above
        if guess:
            msg = f"Logged: {guess['matchup']} ({guess['text'] or guess['kind']}, {guess['ago_s'] // 60} min ago)"
            if req:
                msg += ". Queued for a catch-up replay" if req["state"] == "queued" else ". Saved for catch-up (reels are off)"
        else:
            msg = "Logged. No big play found in the feeds; it still counts as a miss"
        return {"ok": True, "message": msg, "guess": guess, "candidates": cands[:5], "reel_request": req}

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
        marks = plan_marks(sws, engine._name)
        crunch = crunch_timeline(read_crunch(CRUNCH_DIR / f"{d.isoformat()}.json"), sws, engine._name)
        return {"crunch": crunch, "enabled": True, "day": d.isoformat(), "t0": t0, "t1": t1, "now": now, "nights": nights,
                **view, **over, "plan_marks": marks,
                "plan_counts": {"catchup": sum(1 for m in marks if m["type"] == "catchup"),
                                "interleave": sum(1 for m in marks if m["type"] == "interleave")}}

    @r.get("/trend")
    async def get_trend(split: str = "dev"):
        if not _enabled(engine):
            return _off()
        if split not in ("dev", "locked"):
            return {"ok": False, "message": "split is dev or locked"}
        return {"enabled": True, **trend(read_history(HISTORY), split)}

    @r.get("/watch")
    async def watch():
        if not _enabled(engine):
            return _off()
        cur = engine.decider.current
        return {"enabled": True, "now": time.time(), "current": cur,
                "why_tag": why_tag(engine.scored.get(cur), float(engine.cfg.get("phone.why_tag_min"))) if cur else None,
                "also_close": also_close(getattr(engine.merger, "games", {}), cur, league_order(engine.cfg))
                if engine.cfg.get("phone.also_close") else [],
                **watch_values(getattr(engine, "scored", {}), getattr(engine.merger, "games", {}),
                               crunch_tracker(engine))}

    @r.get("/follow")
    async def get_follow():
        # [EXP-043] followed players: the list and where they are live now; flag phone.follow (off) on top of phone.v2
        if not _enabled(engine) or not engine.cfg.get("phone.follow"):
            return _off()
        players = follow_names(engine.cfg.get("noggin.priority.prefs.followed_players") or []) or []
        return {"enabled": True, "players": players, "max": FOLLOW_MAX,
                "live": follow_view(getattr(engine.merger, "games", {}), players, engine.decider.current)}

    @r.post("/follow")
    async def set_follow(body: dict):
        if not _enabled(engine) or not engine.cfg.get("phone.follow"):
            return _off()
        players = follow_names((body or {}).get("players"))
        if players is None:
            return {"ok": False, "message": "players must be a list of names"}
        from redzone.config import ConfigError
        try:
            engine.cfg.update({"noggin": {"priority": {"prefs": {"followed_players": players}}}})
        except ConfigError as e:
            return {"ok": False, "message": str(e)}
        engine.broadcast()
        return {"ok": True, "message": "Saved" if players else "Cleared", "players": players}

    @r.get("/orient")
    async def orient(game: str = ""):
        # [EXP-038] the phone asks right after it sees a new switch; flag phone.orient (off) on top of phone.v2
        if not _enabled(engine) or not engine.cfg.get("phone.orient"):
            return _off()
        gid = game or engine.decider.current
        beat = orientation(getattr(engine.merger, "games", {}).get(gid)) if gid else None
        if beat is not None and screen_behind(engine, gid):
            beat = None   # the beat is the LIVE score and clock: never flash it over a replay or a time-shifted stream
        return {"enabled": True, "beat_s": float(engine.cfg.get("phone.orient_beat_s")), "beat": beat}

    @r.get("/provenance")
    async def get_provenance(key: str = ""):
        if not _enabled(engine):
            return _off()
        try:
            ts = float(key.split("|", 1)[0])
        except ValueError:
            return {"ok": False, "message": "key is <ts>|<game>"}
        row = next((w for w in engine.store.switches_between(ts - 0.01, ts + 0.01)
                    if _switch_key(w["ts"], w["to_game"]) == key), None)
        if row is None:
            return {"ok": False, "message": "No switch with that key"}
        return {"ok": True, "enabled": True, **provenance_view(row, engine._name)}

    @r.get("/capture")
    async def capture(day: str = ""):
        if not _enabled(engine):
            return _off()
        now = time.time()
        try:
            d = date.fromisoformat(day) if day else night_of(now)
        except ValueError:
            return {"ok": False, "message": "day is YYYY-MM-DD"}
        from redzone.supervisor import read_jsonl as rows_between
        t0, t1 = night_window(d)
        try:
            summary = json.loads((LIVE_NIGHTS / d.isoformat() / "summary.json").read_text())
        except (OSError, ValueError):
            summary = None
        rep = next((p for p in (base / f"{d.isoformat()}.md" for base in REPORTS) if p.exists()), None)
        held_now = dict(engine.control.held) if t0 <= now < t1 else {}
        v = capture_view(d.isoformat(), engine.store.switches_between(t0, t1), summary,
                         rows_between(BUFFER_LOG, t0, t1), rep.name if rep else None, held_now)
        return {"enabled": True, "now": now, **v}

    @r.get("/capture/report")
    async def capture_report(day: str = ""):
        if not _enabled(engine):
            return _off()
        try:
            d = date.fromisoformat(day)
        except ValueError:
            return {"ok": False, "message": "day is YYYY-MM-DD"}
        rep = next((p for p in (base / f"{d.isoformat()}.md" for base in REPORTS) if p.exists()), None)
        return {"ok": rep is not None, "day": d.isoformat(), "text": rep.read_text(errors="replace")[:60000] if rep else ""}

    @r.get("/scorecard")
    async def scorecard(n: int = 14):
        if not _enabled(engine):
            return _off()
        found = {p.stem for base in REPORTS if base.is_dir() for p in base.glob("20??-??-??.json")}
        if LIVE_NIGHTS.is_dir():
            found |= {p.name for p in LIVE_NIGHTS.glob("20??-??-??") if p.is_dir()}
        days = sorted(found)[-max(1, min(n, 60)):]

        def load(p: Path) -> dict | None:
            try:
                d = json.loads(p.read_text())
            except (OSError, ValueError):
                return None
            return d if isinstance(d, dict) else None
        nights = [(d, next((m for m in (load(b / f"{d}.json") for b in REPORTS) if m), None),
                   load(LIVE_NIGHTS / d / "summary.json")) for d in days]
        return {"enabled": True, **night_scorecard(nights)}

    @r.get("/lastnight")
    async def lastnight():
        # the morning "Last night" card; flag phone.last_night (off) on top of phone.v2. Read only
        if not _enabled(engine) or not engine.cfg.get("phone.last_night"):
            return _off()
        now = time.time()
        today = night_of(now)
        night = (today - timedelta(days=1)).isoformat()
        hours = (now - night_window(today)[0]) / 3600.0
        show = hours < float(engine.cfg.get("phone.last_night_hours"))

        def load(p: Path):
            try:
                return json.loads(p.read_text())
            except (OSError, ValueError):
                return None
        metrics = next((m for m in (load(b / f"{night}.json") for b in REPORTS) if isinstance(m, dict)), None)
        names = load(RELEASE_NAMES)
        t0, t1 = night_window(date.fromisoformat(night))
        rel = release_that_ran(load(DEPLOY_STATE) or {}, t0, t1, names if isinstance(names, dict) else {})
        return {"enabled": True, "show": show,
                **last_night_card(night, metrics, load(LIVE_NIGHTS / night / "summary.json"),
                                  worth_rows().get(night), rel)}

    @r.get("/catchup")
    async def catchup():
        if not _enabled(engine):
            return _off()
        from redzone.supervisor import read_jsonl as rows_between
        now = time.time()
        c, dec = engine.control, engine.decider
        t0, _ = night_window(night_of(now))
        tg = {t: g for g, w in engine.where.items() if (t := getattr(w, "target", None))}
        return {"enabled": True, "now": now, **catchup_queue(
            rows_between(BUFFER_LOG, t0, now + 1), tg, c.hidden_target if c.two_stick else None, c.hidden_ready,
            dict(c.held), list(dec.queue), engine.scored, engine._name, now,
            {"visible": int(engine.cfg.get("phone.catchup_visible"))} if engine.cfg.get("phone.catchup_view") else None),
            "on_screen": {"game_id": dec.current, "matchup": engine._name(dec.current)} if dec.current else None}

    @r.get("/held")
    async def held():
        if not _enabled(engine):
            return _off()
        c, dec, now = engine.control, engine.decider, time.time()
        t = c.hidden_target if c.two_stick else None
        gid = next((g for g, w in engine.where.items() if getattr(w, "target", None) == t), None) if t else None
        return {"enabled": True, "now": now,
                "view": held_view(gid, c.hidden_ready, float(c.held.get(t, 0.0)) if t else 0.0,
                                  dec.home_break_visit, dec.home_return_at, list(dec.queue), dec.current,
                                  engine._name, now, spoiler_safe=bool(engine.cfg.get("phone.catchup_view"))),
                "ladder": ladder_line(c, now)}

    @r.get("/ledger")
    async def ledger():
        if not _enabled(engine):
            return _off()
        return {"enabled": True, **ledger_mod.view(ledger_mod.read(ledger_mod.LEDGER_FILE))}

    @r.get("/slate")
    async def slate():
        if not _enabled(engine):
            return _off()
        now = time.time()
        v = slate_preview(getattr(engine.merger, "games", {}), HardRules.from_config(engine.cfg), now)
        wv_on = bool(engine.cfg.get("scoring.watch_value", False))
        slate_watch(v["games"], engine.scored, wv_on)
        return {"enabled": True, "now": now, **v, "alert": clash_alert(v, now), "watch_value": wv_on}

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
