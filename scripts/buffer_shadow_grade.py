"""Time-shift buffer shadow log: schema + Rule #1 (test writers, Cycle 24). Frozen by the integrator, grader v29.

Reads data/buffer_shadow.jsonl (lane 2's redzone/buffer_shadow.py: `timing.behind_live_s.shadow: true` while
`timing.behind_live_s.enabled` is off; a second "editor" Decider runs B s behind live in memory) and checks:

  S1 schema       every row has the required fields with sane values (behind_s within [0, max_s])
  S2 hidden only  the editor itself touches no stick (stick/role null on its rows); any pause/seek/hold row (the
                  seek-reliability piece, item D) must name a stick whose role is "hidden"
  R1 Rule #1      every `kind=entry` row (a switch the buffered show would make) lands its picture on a dead ball:
                  arena_at = picture_at - behind_s - mu - rewind_s must be dead under BOTH graders,
                    (a) the dev cut grader, redzone.lab.cutgrade._dead_at, on the archived play-by-play, and
                    (b) EXP-744 clock truth (scripts/clock_truth_grade.py, basketball only), which shares no code with (a).
                  "live" under either grader is a FAIL. "ambiguous" or no play-by-play is reported and is never a PASS
                  on its own: R1 needs >= 1 entry graded by (a).

    python scripts/buffer_shadow_grade.py data/buffer_shadow.jsonl [--archive data/archive] [--max-s 90] [--json]

Exit 1 on any FAIL. Without an archive for a game, its entries read PENDING.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NUM = (int, float)
REQUIRED = {"ts": NUM, "mode": str, "kind": str, "behind_s": NUM}
NULLABLE = {"league": str, "gid": str, "stick": str, "role": str, "source": str, "action": str}
ENTRY = {"to": str, "picture_at": NUM, "mu": NUM, "sd": NUM, "arena_at": NUM}
ROLES = {"hidden", "visible"}
STICK_ACTIONS = {"hold", "seek", "pause", "resume"}   # item D (seek reliability): hidden stick only
EDITOR_KINDS = {"entry", "decision", "stay_leave"}               # the editor's own rows (entry, decision, stay_leave): no stick at all
ARENA_TOL_S = 1.0


def _clock_truth():
    spec = importlib.util.spec_from_file_location("clock_truth_grade", ROOT / "scripts" / "clock_truth_grade.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def check_row(r: dict, max_s: float = 90.0) -> list[str]:
    """S1 + S2 problems with one row (empty = fine)."""
    bad = []
    for k, t in REQUIRED.items():
        if not isinstance(r.get(k), t) or isinstance(r.get(k), bool):
            bad.append(f"{k} missing or not {t}")
    if bad:
        return bad
    if r["mode"] != "shadow":
        bad.append(f"mode {r['mode']!r} is not 'shadow'")
    for k, t in NULLABLE.items():
        if k not in r:
            bad.append(f"{k} missing (null is fine)")
        elif r[k] is not None and not isinstance(r[k], t):
            bad.append(f"{k} not {t} or null")
    if r.get("role") is not None and r["role"] not in ROLES:
        bad.append(f"role {r['role']!r} unknown")
    if not 0.0 <= float(r["behind_s"]) <= max_s:
        bad.append(f"behind_s {r['behind_s']} outside [0, {max_s}]")
    if r["kind"] in EDITOR_KINDS and (r.get("stick") is not None or r.get("role") is not None):
        bad.append(f"{r['kind']} row names stick {r.get('stick')!r} (S2: the editor touches no stick)")
    if r["kind"] in STICK_ACTIONS and (r.get("role") != "hidden" or not r.get("stick")):
        bad.append(f"{r['kind']} on the {r.get('role')} stick (S2: hidden stick only)")
    if r["kind"] == "entry":
        for k, t in ENTRY.items():
            if not isinstance(r.get(k), t) or isinstance(r.get(k), bool):
                bad.append(f"entry: {k} missing or not {t}")
        rw = r.get("rewind_s") or 0.0
        if not isinstance(rw, NUM) or isinstance(rw, bool) or rw < 0:
            bad.append(f"entry: rewind_s {rw!r} not a number >= 0")
        if r.get("league") is None:
            bad.append("entry: league is null")
        if not bad:
            want = float(r["picture_at"]) - float(r["behind_s"]) - float(r["mu"]) - float(rw)
            if abs(want - float(r["arena_at"])) > ARENA_TOL_S:
                bad.append(f"entry: arena_at {r['arena_at']} != picture_at - behind_s - mu - rewind_s = {want:.1f}")
    return bad


def _replay(archive: Path, league: str, gid: str):
    p = archive / league / f"summary_{gid}.json"
    if not p.exists():
        return None
    from redzone.lab.backtest import Replay
    from redzone.models import League
    try:
        return Replay.load(League(league), p)
    except Exception:
        return None


def grade_entry(r: dict, archive: Path, ct=None) -> dict:
    """(a) cutgrade and (b) clock truth on one entry row: 'dead' | 'live' | 'ambiguous' | 'no_pbp' | 'n/a'."""
    from redzone.lab import cutgrade
    ct = ct or _clock_truth()
    lg, gid, a = r["league"].lower(), str(r["to"]), float(r["arena_at"])
    out = {"ts": r["ts"], "league": lg, "to": gid, "arena_at": a, "cutgrade": "no_pbp", "clock_truth": "n/a"}
    rp = _replay(archive, lg, gid)
    if rp is not None and getattr(rp, "wall", None):
        if a < rp.wall[0] or a > rp.wall[-1]:
            out["cutgrade"] = "outside_game"
        else:
            dead, _brk, _wait = cutgrade._dead_at(rp.league, rp.wall, rp.plays, a)
            out["cutgrade"] = "dead" if dead else "live"
    if lg in ct.LEAGUES:
        ev = ct.events(archive, lg, gid)
        out["clock_truth"] = ct.label(ev, a) if ev else "no_pbp"
    return out


def grade(rows: list[dict], archive: Path, max_s: float = 90.0) -> dict:
    schema = []
    for i, r in enumerate(rows):
        for b in check_row(r, max_s):
            schema.append({"row": i, "problem": b})
    hidden_bad = [s for s in schema if "S2" in s["problem"]]
    ct = _clock_truth()
    entries = [grade_entry(r, archive, ct) for r in rows
               if r.get("kind") == "entry" and not check_row(r, max_s)]
    live = [e for e in entries if "live" in (e["cutgrade"], e["clock_truth"])]
    graded_a = [e for e in entries if e["cutgrade"] in ("dead", "live")]
    r1 = "FAIL" if live else ("PASS" if graded_a else "PENDING")
    s1 = "FAIL" if [s for s in schema if s not in hidden_bad] else "PASS"
    s2 = "FAIL" if hidden_bad else "PASS"
    return {"rows": len(rows), "S1": s1, "S2": s2, "R1": r1,
            "entries": len(entries), "entries_graded_cutgrade": len(graded_a),
            "entries_live": len(live), "entries_clock_ambiguous": sum(e["clock_truth"] == "ambiguous" for e in entries),
            "schema_problems": schema[:50], "live": live, "entry_grades": entries}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("log", type=Path)
    ap.add_argument("--archive", type=Path, default=Path("data/archive"))
    ap.add_argument("--max-s", type=float, default=90.0)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    rows = [json.loads(x) for x in a.log.read_text().splitlines() if x.strip()]
    res = grade(rows, a.archive, a.max_s)
    if a.json:
        print(json.dumps(res, indent=1))
    else:
        for k in ("S1", "S2", "R1"):
            print(f"{res[k]:8} {k}")
        print(f"rows {res['rows']}, entries {res['entries']} (graded by cutgrade {res['entries_graded_cutgrade']}), "
              f"live {res['entries_live']}, clock-ambiguous {res['entries_clock_ambiguous']}")
        for p in res["schema_problems"][:10]:
            print("  schema:", p)
        for e in res["live"][:10]:
            print("  LIVE:", e)
    return 1 if "FAIL" in (res["S1"], res["S2"], res["R1"]) else 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    sys.exit(main())
