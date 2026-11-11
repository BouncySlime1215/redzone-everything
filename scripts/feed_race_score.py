#!/usr/bin/env python3
"""Score tonight's feed race: which source knows a play first, and how far ahead of the screen it is.

Serves: tonight's tracker items 4 (TNF Prime: score bug vs ESPN vs Polymarket) and 5 (lane 5's faster feeds:
nba_cdn at 1 s, NHL api-web, MLB statsapi). Owner: Data Planner A (measurer). Read-only on the logs; runs anywhere
(the Mac only copies its logs out). Nothing in the app reads its output.

    python scripts/feed_race_score.py --rec data/recordings/2026-11-05 --sync data/sync.jsonl \
        [--poly out/markets_live/polymarket/nfl/20261105.jsonl] [--screen prime:...] [--json out.json] [--md out.md]

Inputs (all optional except --rec):
  plays.jsonl  (recorder) every play each source reported: t = first seen by us, wall = the play's official time
  race.jsonl   (recorder) per score / dead-ball label, the first source and each follower's lag behind it
  sync.jsonl   (delay filter) per channel: mu = screen delay behind the real play; kind "score" rows are score-bug
               reads (t = when the new score reached that screen, z = seconds behind the play)
  --poly       (crew D #315) Polymarket shadow log; "trigger" rows carry recv_ts

Report, per league and source:
  lag          t - wall over tracked batches (redzone/lab/timing.tracked): n, p10, p50, p90, mean
  head_to_head same play seen by two sources (same game, walls within MATCH_S): median seconds A is ahead of B
  race         first% and median seconds behind the first copy, from race.jsonl
  screen_lead  per channel: its median delay mu minus the source's p50 lag = seconds of head start on that screen
  score_bug    per score-bug read: the screen onset minus each source's arrival for the matched play (and the
               nearest Polymarket trigger), i.e. the measured lead, and which source arrived first
"""
from __future__ import annotations

import argparse
import bisect
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from redzone.lab import timing  # noqa: E402

MATCH_S = 1.5          # two sources' copies of one play: same game, official walls this close
BUG_MATCH_S = 3.0      # a score-bug read's play wall (onset - z) vs a feed's play wall
POLY_BEFORE_S = 20.0   # a Polymarket trigger counts for a score if it lands from 20 s before the play ...
POLY_AFTER_S = 120.0   # ... to 120 s after it


def _q(v: list[float]) -> dict:
    if not v:
        return {"n": 0}
    s = sorted(v)

    def at(p):
        return round(s[min(len(s) - 1, int(p * len(s)))], 2)
    return {"n": len(s), "p10": at(0.10), "p50": at(0.50), "p90": at(0.90), "mean": round(statistics.fmean(s), 2)}


def _jsonl(path: Path | None) -> list[dict]:
    if not path or not Path(path).exists():
        return []
    out = []
    for ln in Path(path).read_text().splitlines():
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out


def lag_table(rows) -> dict:
    by: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        by[r.league][r.source].append(r.lag)
    return {lg: {s: _q(v) for s, v in sorted(srcs.items())} for lg, srcs in sorted(by.items())}


def head_to_head(rows, match_s: float = MATCH_S) -> dict:
    """{league: {"A>B": seconds A saw the play before B (median, positive = A first), n}} over matched plays."""
    by: dict[tuple[str, str], dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        by[(r.league, r.game)][r.source].append(r)
    out: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for (lg, _), srcs in by.items():
        names = sorted(srcs)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                bs = sorted(srcs[b], key=lambda r: r.wall)
                walls = [r.wall for r in bs]
                used = set()
                for ra in srcs[a]:
                    k = bisect.bisect_left(walls, ra.wall)
                    best = None
                    for j in (k - 1, k):
                        if 0 <= j < len(bs) and j not in used and abs(walls[j] - ra.wall) <= match_s:
                            if best is None or abs(walls[j] - ra.wall) < abs(walls[best] - ra.wall):
                                best = j
                    if best is not None:
                        used.add(best)
                        out[lg][f"{a}>{b}"].append(bs[best].t - ra.t)
    return {lg: {k: _q(v) for k, v in sorted(d.items())} for lg, d in sorted(out.items())}


def screen_delays(sync: list[dict]) -> dict:
    """Per channel: median filter delay mu over the night and the score-bug samples (z of kind "score")."""
    mu, bug = defaultdict(list), defaultdict(list)
    for r in sync:
        if r.get("mu") is not None and r.get("kind") != "tune":
            mu[r["target"]].append(float(r["mu"]))
        if r.get("kind") == "score" and r.get("z") is not None:
            bug[r["target"]].append(float(r["z"]))
    return {t: {"mu": _q(mu[t]), "score_bug_z": _q(bug[t])} for t in sorted(set(mu) | set(bug))}


def screen_lead(lags: dict, delays: dict) -> dict:
    """{channel: {league: {source: seconds of head start = channel delay p50 - source lag p50}}}."""
    out = {}
    for tgt, d in delays.items():
        z = d["score_bug_z"] if d["score_bug_z"].get("n") else d["mu"]
        if not z.get("n"):
            continue
        out[tgt] = {lg: {s: round(z["p50"] - q["p50"], 1) for s, q in srcs.items() if q.get("n")}
                    for lg, srcs in lags.items()}
    return out


def score_bug_race(rows, sync: list[dict], poly: list[dict], screen: str | None = None,
                   match_s: float = BUG_MATCH_S) -> dict:
    """Every score-bug read on the screen channel: each source's measured lead (onset - its arrival) for the
    matched play, the Polymarket trigger's lead, the other channels' onsets, and the race order."""
    bugs = [r for r in sync if r.get("kind") == "score" and r.get("z") is not None]
    if not bugs:
        return {"reads": 0}
    chans = sorted({b["target"] for b in bugs})
    screen = screen or max(chans, key=lambda c: sum(b["target"] == c for b in bugs))
    trig = sorted(float(p["recv_ts"]) for p in poly if p.get("row") == "trigger" and p.get("recv_ts"))
    lead, first, events = defaultdict(list), defaultdict(int), []
    for b in (x for x in bugs if x["target"] == screen):
        onset, wall = float(b["t"]), float(b["t"]) - float(b["z"])
        arr = {}
        for r in rows:
            if abs(r.wall - wall) <= match_s and (r.source not in arr or r.t < arr[r.source]):
                arr[r.source] = r.t
        hits = [t for t in trig if wall - POLY_BEFORE_S <= t <= wall + POLY_AFTER_S]
        if hits:
            arr["polymarket_trigger"] = hits[0]
        for o in bugs:
            if o["target"] != screen and abs((float(o["t"]) - float(o["z"])) - wall) <= match_s:
                arr[f"screen:{o['target']}"] = min(arr.get(f"screen:{o['target']}", 1e18), float(o["t"]))
        for s, t in arr.items():
            lead[s].append(onset - t)
        if arr:
            first[min(arr, key=arr.get)] += 1
        events.append({"onset": round(onset, 2), "play_wall": round(wall, 2),
                       "lead_s": {s: round(onset - t, 2) for s, t in sorted(arr.items(), key=lambda kv: kv[1])}})
    n = len(events)
    return {"reads": n, "screen": screen, "lead_s": {s: _q(v) for s, v in sorted(lead.items())},
            "first": {s: {"n": c, "pct": round(100.0 * c / n, 1)} for s, c in sorted(first.items())},
            "events": events}


def score(rec: Path, sync_path: Path | None = None, poly_path: Path | None = None, screen: str | None = None,
          leagues: set[str] | None = None) -> dict:
    rows = timing.tracked(timing.load_rows([rec / "plays.jsonl"]))
    if leagues:
        rows = [r for r in rows if r.league in leagues]
    sync = _jsonl(sync_path)
    lags = lag_table(rows)
    delays = screen_delays(sync)
    race = timing.race_report([rec / "race.jsonl"]) if (rec / "race.jsonl").exists() else {}
    if leagues:
        race = {k: v for k, v in race.items() if k in leagues}
    return {"rec": str(rec), "plays": len(rows), "lag": lags, "head_to_head": head_to_head(rows), "race": race,
            "screen_delay": delays, "screen_lead": screen_lead(lags, delays),
            "score_bug": score_bug_race(rows, sync, _jsonl(poly_path), screen),
            "notes": ["lag includes each source's polling interval",
                      "screen_lead uses the channel's score-bug delay when it has reads, else the filter's mu",
                      "recv/seen times are the host clock: keep NTP on"]}


def render(r: dict) -> str:
    out = [f"# Feed race: {r['rec']}", "", f"{r['plays']:,} tracked plays.", "",
           "## Arrival lag (first seen - official play time, s)", "",
           "| league | source | n | p10 | p50 | p90 | mean |", "|---|---|---:|---:|---:|---:|---:|"]
    for lg, srcs in r["lag"].items():
        for s, q in srcs.items():
            if q.get("n"):
                out.append(f"| {lg} | {s} | {q['n']} | {q['p10']} | {q['p50']} | {q['p90']} | {q['mean']} |")
    out += ["", "## Head to head (same play; positive = the first source saw it sooner, s)", "",
            "| league | pair | n | p10 | p50 | p90 |", "|---|---|---:|---:|---:|---:|"]
    for lg, d in r["head_to_head"].items():
        for k, q in d.items():
            if q.get("n"):
                out.append(f"| {lg} | {k} | {q['n']} | {q['p10']} | {q['p50']} | {q['p90']} |")
    if r["screen_lead"]:
        out += ["", "## Head start on each screen (channel delay p50 - source lag p50, s)", ""]
        for tgt, lgs in r["screen_lead"].items():
            for lg, srcs in lgs.items():
                out.append(f"- {tgt} / {lg}: " + ", ".join(f"{s} {v:+.1f}" for s, v in srcs.items()))
    b = r["score_bug"]
    if b.get("reads"):
        out += ["", f"## Score-bug race on {b['screen']} ({b['reads']} reads; lead = screen onset - arrival, s)", "",
                "| source | n | p10 | p50 | p90 | first % |", "|---|---:|---:|---:|---:|---:|"]
        for s, q in b["lead_s"].items():
            out.append(f"| {s} | {q['n']} | {q['p10']} | {q['p50']} | {q['p90']} | "
                       f"{b['first'].get(s, {}).get('pct', 0.0)} |")
    else:
        out += ["", "No score-bug reads in the sync log."]
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rec", type=Path, required=True, help="data/recordings/<day>")
    ap.add_argument("--sync", type=Path)
    ap.add_argument("--poly", type=Path)
    ap.add_argument("--screen", help="the channel Nick watches (default: the one with most score-bug reads)")
    ap.add_argument("--league", action="append")
    ap.add_argument("--json", type=Path)
    ap.add_argument("--md", type=Path)
    a = ap.parse_args(argv)
    r = score(a.rec, a.sync, a.poly, a.screen, set(a.league) if a.league else None)
    if a.json:
        a.json.write_text(json.dumps(r, indent=1))
    md = render(r)
    if a.md:
        a.md.write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
