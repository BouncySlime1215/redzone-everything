"""Clock-truth re-grade of entries (PROPOSAL, test writers, EXP-744). Not frozen; the integrator freezes it.

Lane 3 (Nov 5) suspects the dev cut grader shares the tracker's idea of "dead": the seconds after a made basket or
a rebound count as dead, though the ball is live on screen. This grader labels each entry's landing from the
play-by-play GAME CLOCK alone, with no shared code from redzone.grading / cutgrade / the decider:

  live       the screen's arena moment a sits between two consecutive events of one period and the game clock ran
             >= 0.8 x wall time - 2 s between them
  dead       a is within 10 s after a whistle event (foul, timeout, violation, free throw, end of period), or the
             two events straddle a period change, or the clock ran < 0.3 x wall time between them
  ambiguous  anything else, or no event on one side

Thresholds are lane 3's pre-registered truth (/mnt/project-files/lane3/exp741/PREREG.md), unchanged.
--band S also labels a +-S s band around a and calls it ambiguous unless every second agrees (delay doubt).

    python scripts/entry_autopsy.py nba --split fit --real-lag --out autopsy.json
    python scripts/clock_truth_grade.py autopsy.json [--archive data/archive] [--band 0] [--json]

Input rows need gid, league, screen_wall (arena epoch seconds on screen at the picture) and mid_play (the dev grader's
label). Basketball only (NBA, CBB): the clock rule needs a running game clock.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

WHISTLE = ("foul", "timeout", "violation", "free throw", "end of")
LEAGUES = {"nba", "cbb"}
_cache: dict[tuple[str, str], list[tuple[float, int, float, str]]] = {}


def _secs(v) -> float:
    v = str(v)
    if ":" in v:
        m, s = v.split(":")
        return int(m) * 60 + float(s)
    return float(v)


def events(archive: Path, league: str, gid: str) -> list[tuple[float, int, float, str]]:
    """(wall epoch, period, clock seconds left, type text) for every play with a wallclock, in wall order."""
    key = (league, gid)
    if key not in _cache:
        p = archive / league / f"summary_{gid}.json"
        ev = []
        if p.exists():
            for x in json.loads(p.read_text()).get("plays", []):
                w = x.get("wallclock")
                if not w:
                    continue
                ev.append((dt.datetime.fromisoformat(w.replace("Z", "+00:00")).timestamp(), int(x["period"]["number"]),
                           _secs(x["clock"]["displayValue"]), ((x.get("type") or {}).get("text") or "").lower()))
        ev.sort(key=lambda e: e[0])
        _cache[key] = ev
    return _cache[key]


def label_at(ev: list[tuple[float, int, float, str]], a: float) -> str:
    prev = [e for e in ev if e[0] <= a]
    nxt = [e for e in ev if e[0] > a]
    if not prev or not nxt:
        return "ambiguous"
    w1, p1, c1, t1 = prev[-1]
    w2, p2, c2, _ = nxt[0]
    if a - w1 <= 10 and any(k in t1 for k in WHISTLE):
        return "dead"
    if p1 != p2:
        return "dead"
    gap, drop = w2 - w1, c1 - c2
    if drop >= 0.8 * gap - 2:
        return "live"
    if drop < 0.3 * gap:
        return "dead"
    return "ambiguous"


def label(ev, a: float, band: float = 0.0) -> str:
    labs = {label_at(ev, a + d) for d in range(-int(band), int(band) + 1)}
    return labs.pop() if len(labs) == 1 else "ambiguous"


def grade(rows: list[dict], archive: Path, band: float = 0.0) -> dict:
    out, per = [], {}
    for r in rows:
        lg = str(r.get("league") or "").lower()
        if lg not in LEAGUES or r.get("screen_wall") is None:
            continue
        ev = events(archive, lg, str(r["gid"]))
        lab = label(ev, float(r["screen_wall"]), band) if ev else "no_pbp"
        out.append({**{k: r.get(k) for k in ("gid", "matchup", "night_et", "kind", "reason", "stop", "landing_play",
                                              "s_after_play", "screen_wall")},
                    "grader": "mid_play" if r.get("mid_play") else "dead", "truth": lab})
        n = per.setdefault(r.get("night_et"), [0, 0, 0])
        n[0] += 1
        n[1] += lab == "live"
        n[2] += bool(r.get("mid_play"))
    graded = [x for x in out if x["truth"] != "no_pbp"]
    n = len(graded)

    def per100(k: int) -> float | None:
        return round(100.0 * k / n, 2) if n else None
    live = sum(x["truth"] == "live" for x in graded)
    grader_mid = sum(x["grader"] == "mid_play" for x in graded)
    disagree = [x for x in graded if x["truth"] == "live" and x["grader"] != "mid_play"]
    return {"entries": len(out), "graded": n, "no_pbp": len(out) - n,
            "grader_mid_play": grader_mid, "grader_mid_play_per100": per100(grader_mid),
            "truth_live": live, "truth_live_per100": per100(live),
            "truth_ambiguous": sum(x["truth"] == "ambiguous" for x in graded),
            "truth_dead": sum(x["truth"] == "dead" for x in graded),
            "grader_dead_but_truth_live": len(disagree),
            "by_kind_truth_live": _count(x["kind"] for x in graded if x["truth"] == "live"),
            "by_stop_truth_live": _count(x["stop"] for x in graded if x["truth"] == "live"),
            "per_night": {k: {"entries": v[0], "truth_live": v[1], "grader_mid_play": v[2]} for k, v in sorted(per.items())},
            "rows": out}


def _count(xs) -> dict:
    c: dict = {}
    for x in xs:
        c[x] = c.get(x, 0) + 1
    return dict(sorted(c.items(), key=lambda kv: -kv[1]))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("autopsy", type=Path)
    ap.add_argument("--archive", type=Path, default=Path("data/archive"))
    ap.add_argument("--band", type=float, default=0.0)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    doc = json.loads(a.autopsy.read_text())
    res = grade(doc["rows"] if isinstance(doc, dict) else doc, a.archive, a.band)
    if a.json:
        print(json.dumps(res, indent=1))
    else:
        print(json.dumps({k: v for k, v in res.items() if k != "rows"}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
