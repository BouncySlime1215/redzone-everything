"""[tracker item 5] The feed lag race: fast league feeds (fast_feeds.jsonl) vs ESPN (plays.jsonl), per league.

    python scripts/fast_feed_race.py --day 2026-11-05 [--root data/recordings]

For each source, the lag (when we first saw an event minus when it happened) where the feed says when
it happened: NBA liveData (timeActual) and MLB statsapi (endTime) on the fast side, ESPN (wallclock) on
the other. NHL api-web has no event time, so its goals are paired with ESPN's goals in order, game by
game, and the lead is ESPN's first-seen minus api-web's. Read only; prints one JSON object.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def read_jsonl(path: Path) -> list[dict]:
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if isinstance(r, dict):
            out.append(r)
    return out


def _q(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))], 2)


def summary(xs: list[float]) -> dict:
    return {"n": len(xs), "p50": _q(xs, 0.5), "p90": _q(xs, 0.9)}


def race(fast: list[dict], espn: list[dict]) -> dict:
    """{league: {source: lag summary, ..., "nhl_goal_lead_s": summary}}"""
    out: dict[str, dict] = {}
    for r in fast:
        if r.get("real") is not None and r.get("seen") is not None:
            out.setdefault(r["league"], {}).setdefault(r["source"], []).append(float(r["seen"]) - float(r["real"]))
    for r in espn:
        if r.get("lag") is not None and r.get("league"):
            out.setdefault(r["league"], {}).setdefault(r.get("source") or "espn", []).append(float(r["lag"]))
    res = {lg: {src: summary(v) for src, v in srcs.items()} for lg, srcs in out.items()}
    leads = []
    games = {r["game"] for r in fast if r.get("league") == "nhl"}
    for gid in games:
        mine = sorted(float(r["seen"]) for r in fast if r.get("game") == gid and r.get("kind") == "goal")
        theirs = sorted(float(r["t"]) for r in espn if r.get("game") == gid and r.get("league") == "nhl"
                        and "goal" in str(r.get("type") or "").lower())
        leads += [e - m for m, e in zip(mine, theirs)]
    if leads:
        res.setdefault("nhl", {})["nhl_goal_lead_s"] = summary(leads)
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--day", required=True, help="YYYY-MM-DD (the recordings folder)")
    ap.add_argument("--root", type=Path, default=ROOT / "data" / "recordings")
    a = ap.parse_args(argv)
    d = a.root / a.day
    fast, espn = read_jsonl(d / "fast_feeds.jsonl"), read_jsonl(d / "plays.jsonl")
    print(json.dumps({"day": a.day, "fast_rows": len(fast), "espn_rows": len(espn), "race": race(fast, espn)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
