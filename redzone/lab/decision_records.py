"""Decision records for the Teacher's nightly review (backlog lane 5 item 2, E23 under the self-taught rule).

One JSONL file per night (6 AM to 6 AM ET, the supervisor's night). Every line is one JSON object with a
`type`, oldest first after the header:

    {"type": "night", "schema": 1, "night": "2026-11-04", "t0": ..., "t1": ..., "counts": {...},
     "sources": [...], "never": ["feedback", "taps", "votes", "overrides"]}
    {"type": "decision", "ts", "action", "game", "kind", "state", "gate", "reason", "playbooks",
     "on_screen", "candidates": [{"game", "league", "score", "moment", "reason"}],
     "outcome": {"window_s": 300, "big_plays": n, "big_on_target": n, "big_seen": n, "big_missed": n,
                 "missed": [{"ts", "game", "kind"}]}}
    {"type": "switch", "ts", "frm", "to_game", "kind", "reason", "playbooks", "ok", "dry_run",
     "timing": {"decide_to_picture_s", "dead_air_s", "path"}, "landing": {"leave", "leave_why", "enter", "enter_why"},
     "alert": {"value", "threshold", "fired", "nothing_happened"} | null}   # EXP-035 why-tag grading
    {"type": "moment", "ts", "game", "league", "kind", "seen", "on_screen"}

Sources are objective only: the `decisions`, `switches` and `snapshots` tables, the supervisor's cut grades
(data/supervisor_cuts.jsonl: did we leave or land mid-play, judged from the feed) and big-play moments
(data/supervisor_moments.jsonl: was each big play on screen, judged from the feed). The `feedback` table
(Nick's 👍/👎 and "I missed that") is never read: the Noggin is self-taught (decisions.md, Nov 4 10:55 AM).
`candidates` is the last per-game priority snapshot at or before the decision (taken every ~60 s, so up
to `SNAP_MAX_AGE_S` old); the Teacher joins records to archived pbp by (game, ts) for anything finer.

Read-only and offline: nothing here writes to the store, uploads, or changes switching.
"""
from __future__ import annotations

import bisect
import json
import re
from pathlib import Path
from typing import Iterable

SCHEMA = 1
OUTCOME_WINDOW_S = 300.0      # big plays in the 5 minutes after a decision are its outcome
SNAP_MAX_AGE_S = 180.0        # a candidate snapshot older than this is too stale to attach
NEVER = ["feedback", "taps", "votes", "overrides"]
ALERT_MIN = 70.0              # EXP-035: = redzone.web.phone_v2.WHY_TAG_MIN (pinned by a test)
PLAYBOOK = re.compile(r"playbook=([A-Za-z]+-\d+)")


def playbooks(reason: str | None) -> list[str]:
    return sorted(set(PLAYBOOK.findall(reason or "")))


def read_jsonl(path: Path | None, t0: float, t1: float) -> list[dict]:
    """Rows of a JSONL log with t0 <= ts < t1; a missing file or a bad line is skipped, never fatal."""
    if path is None or not Path(path).exists():
        return []
    out = []
    for line in Path(path).read_text(errors="replace").splitlines():
        try:
            r = json.loads(line)
            ts = float(r.get("ts"))
        except (ValueError, TypeError, AttributeError):
            continue
        if t0 <= ts < t1:
            out.append(r)
    return out


def _f(x) -> float | None:
    try:
        return None if x is None else round(float(x), 3)
    except (TypeError, ValueError):
        return None


def _snap_index(snapshots: Iterable[tuple]) -> tuple[list[float], list[list[dict]]]:
    """Group snapshot rows (ts, game, league, score, moment, reason, state) by tick, oldest first."""
    ticks: dict[float, list[dict]] = {}
    for ts, game, league, score, moment, reason, _state in snapshots:
        ticks.setdefault(float(ts), []).append({"game": game, "league": league, "score": _f(score),
                                                "moment": _f(moment), "reason": reason or ""})
    keys = sorted(ticks)
    return keys, [sorted(ticks[k], key=lambda c: -(c["score"] or 0)) for k in keys]


def _candidates(keys: list[float], vals: list[list[dict]], ts: float) -> list[dict]:
    i = bisect.bisect_right(keys, ts) - 1
    if i < 0 or ts - keys[i] > SNAP_MAX_AGE_S:
        return []
    return vals[i]


def _on_screen(switches: list[dict], ts: float) -> str | None:
    """The game on screen at `ts`: the target of the last switch that went through before it."""
    cur = None
    for s in switches:
        if float(s["ts"]) > ts:
            break
        if s.get("ok") or s.get("dry_run"):
            cur = s.get("to_game")
    return cur


def _trace(row: dict) -> dict:
    d = row.get("detail")
    if isinstance(d, str):
        try:
            d = json.loads(d or "{}")
        except ValueError:
            d = {}
    t = d.get("trace") if isinstance(d, dict) else None
    return t if isinstance(t, dict) else {}


def build(night: str, t0: float, t1: float, decisions: list[dict], switches: list[dict],
          snapshots: Iterable[tuple] = (), cuts: list[dict] = (), moments: list[dict] = ()) -> list[dict]:
    """All of one night's records, header first. Pure: every input is already read."""
    decisions = sorted(decisions, key=lambda r: float(r["ts"]))
    switches = sorted(switches, key=lambda r: float(r["ts"]))
    moments = sorted(moments, key=lambda r: float(r["ts"]))
    keys, vals = _snap_index(snapshots)
    mts = [float(m["ts"]) for m in moments]
    cut_by_id = {c.get("id"): c for c in cuts if c.get("id") is not None}

    out: list[dict] = []
    for d in decisions:
        ts = float(d["ts"])
        lo, hi = bisect.bisect_left(mts, ts), bisect.bisect_left(mts, ts + OUTCOME_WINDOW_S)
        after = moments[lo:hi]
        target = d.get("game")
        missed = [m for m in after if not m.get("seen")]
        out.append({
            "type": "decision", "ts": round(ts, 3), "action": d.get("action"), "game": target,
            "kind": d.get("kind") or "", "state": d.get("state") or "", "gate": d.get("gate") or "",
            "reason": d.get("reason") or "", "playbooks": playbooks(d.get("reason")),
            "on_screen": _on_screen(switches, ts), "candidates": _candidates(keys, vals, ts),
            "outcome": {"window_s": OUTCOME_WINDOW_S, "big_plays": len(after),
                        "big_on_target": sum(1 for m in after if m.get("game") == target),
                        "big_seen": len(after) - len(missed), "big_missed": len(missed),
                        "missed": [{"ts": _f(m["ts"]), "game": m.get("game"), "kind": m.get("kind")}
                                   for m in missed]}})
    alerts = 0
    nothing = 0
    for s in switches:
        t = _trace(s)
        c = cut_by_id.get(t.get("id")) or {}
        # EXP-035: the switch's value as the decision saw it (provenance: learned watch value, else importance)
        prov = t.get("provenance") if isinstance(t.get("provenance"), dict) else {}
        wv, imp = prov.get("watch_value"), prov.get("importance")
        val = _f(wv.get("to") if isinstance(wv, dict) else wv)
        if val is None and isinstance(imp, dict):
            val = _f(imp.get("to"))
        fired = val is not None and val >= ALERT_MIN and not s.get("dry_run")
        ts0 = float(s["ts"])
        hit = any(m.get("game") == s.get("to_game") for m in
                  moments[bisect.bisect_left(mts, ts0):bisect.bisect_left(mts, ts0 + OUTCOME_WINDOW_S)])
        alerts += fired
        nothing += fired and not hit
        out.append({
            "type": "switch", "ts": round(float(s["ts"]), 3), "frm": s.get("frm"), "to_game": s.get("to_game"),
            "kind": s.get("kind") or "", "reason": s.get("reason") or "", "playbooks": playbooks(s.get("reason")),
            "ok": bool(s.get("ok")), "dry_run": bool(s.get("dry_run")),
            "timing": {"decide_to_picture_s": _f((t.get("spans") or {}).get("to_picture")),
                       "dead_air_s": _f((t.get("spans") or {}).get("dead_air")), "path": t.get("path")},
            "landing": {k: c.get(k) for k in ("leave", "leave_why", "enter", "enter_why")} if c else None,
            "alert": {"value": val, "threshold": ALERT_MIN, "fired": fired,
                      "nothing_happened": fired and not hit} if val is not None else None})
    for m in moments:
        out.append({"type": "moment", "ts": _f(m["ts"]), "game": m.get("game"), "league": m.get("league"),
                    "kind": m.get("kind"), "seen": bool(m.get("seen")), "on_screen": m.get("on_screen")})
    out.sort(key=lambda r: (r["ts"], r["type"]))

    landed = [r["landing"] for r in out if r["type"] == "switch" and r["landing"]]
    head = {"type": "night", "schema": SCHEMA, "night": night, "t0": t0, "t1": t1,
            "counts": {"decisions": len(decisions), "switches": len(switches), "moments": len(moments),
                       "big_seen": sum(1 for m in moments if m.get("seen")),
                       "landings_graded": len(landed),
                       "entries_mid_play": sum(1 for g in landed if g.get("enter") == "play"),
                       "leaves_mid_play": sum(1 for g in landed if g.get("leave") == "play"),
                       "alerts": alerts, "alerts_nothing_happened": nothing,
                       "alerts_nothing_pct": round(100.0 * nothing / alerts, 1) if alerts else None},
            "sources": ["decisions", "switches", "snapshots", "supervisor_cuts.jsonl", "supervisor_moments.jsonl"],
            "never": NEVER}
    return [head] + out


def from_db(db: Path, night: str, t0: float, t1: float, data_dir: Path | None = None) -> list[dict]:
    """Read one night from the app's database, opened READ-ONLY (never Store(), which may set a bad file
    aside), plus the supervisor's JSONL logs in data_dir, and build its records."""
    import sqlite3
    con = sqlite3.connect(f"file:{Path(db).resolve()}?mode=ro", uri=True)
    try:
        def rows(sql: str, args: tuple) -> list[dict]:
            cur = con.execute(sql, args)
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
        dec = rows("SELECT ts, action, game, kind, state, gate, reason FROM decisions "
                   "WHERE ts >= ? AND ts < ? ORDER BY ts", (t0, t1))
        sw = rows("SELECT ts, frm, to_game, target, kind, reason, ok, dry_run, detail FROM switches "
                  "WHERE ts >= ? AND ts < ? ORDER BY ts", (t0, t1))
        snaps = con.execute("SELECT ts, game, league, score, moment, reason, state FROM snapshots "
                            "WHERE ts >= ? AND ts < ? ORDER BY ts", (t0 - SNAP_MAX_AGE_S, t1)).fetchall()
    finally:
        con.close()
    data_dir = Path(data_dir) if data_dir else None
    return build(night, t0, t1, dec, sw, snaps,
                 read_jsonl(data_dir / "supervisor_cuts.jsonl" if data_dir else None, t0, t1),
                 read_jsonl(data_dir / "supervisor_moments.jsonl" if data_dir else None, t0, t1))


def write(records: list[dict], path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, separators=(",", ":"), default=str) + "\n" for r in records))
    return path
