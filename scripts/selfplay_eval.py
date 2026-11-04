#!/usr/bin/env python3
"""Oracle self-play eval harness: grade any policy against the hindsight oracle, per source.

    python scripts/selfplay_eval.py nba --raw RAW/nba/pbp_2025.parquet --synthetic 4 \
        --policy hand --policy pick:redzone/noggin/models/pick_nba.v1.txt.gz \
        --policy pick-cue:redzone/noggin/models/pick_nba.cue90.txt.gz --json out.json

Sources: `real` = every night of the season as it happened (all games of the night together); `synthetic` =
K self-play slates per real night (scripts/noggin_selfplay.py: random games of the same season re-timed into
a 3 h window, random screen lag). Each night is graded by the app's grader (sportsgrade) for every policy and
for the oracle's three single-line objectives, live (`solve`) and possession-start (`solve_cue`).

pct_of_oracle is northstar.py's definition: for big live, crunch live and live air (100 - dead air), the policy's
share of that objective's ceiling, capped at 100; the worst of the three. Labels never come from Nick; the
locked / scorecard nights are never read (season_nights drops them).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from redzone.lab import oracle as O  # noqa: E402

OBJ = ("big_live", "crunch_live", "dead_air")


def pct_of_oracle(pol: dict, orc: dict) -> dict:
    """pol: totals of a policy; orc: {objective: totals}. Shares (capped 100) and the worst, as northstar."""
    pct = lambda t, a, b: 100.0 * t[a] / t[b] if t.get(b) else None   # noqa: E731
    shares = {}
    for key in OBJ:
        o = orc.get(key)
        if not o:
            continue
        if key == "dead_air":
            app, ceil = pct(pol, "dead_s", "watched_s"), pct(o, "dead_s", "watched_s")
            s = (100 - app) / (100 - ceil) if app is not None and ceil is not None and ceil < 100 else None
        else:
            app, ceil = pct(pol, key, key.split("_")[0]), pct(o, key, key.split("_")[0])
            s = app / ceil if app is not None and ceil else None
        if s is not None:
            shares[key] = round(100 * min(1.0, s), 1)
    return {"shares": shares, "pct": min(shares.values()) if len(shares) == 3 else None,
            "worst": min(shares, key=shares.get) if shares else None}


def _policy_switches(spec: str, n, steps, tab, X, reps, cue_s: float):
    import train_noggin_pick as T
    from redzone.noggin import pick as PK
    kind, _, path = spec.partition(":")
    if kind == "hand":
        return PK.rollout(T._hand(X), X, reps, steps, T.TUNE_S, 0.5, "hand")
    booster = _booster(path)
    cue = O.cue_steps(tab, cue_s) if kind == "pick-cue" else None
    Xp = X if cue is None else PK.features_for_night(reps, dict(tab, land=cue >= 0), steps, n.lag, T.MINE)
    G, K, F = Xp.shape
    sc = booster.predict(Xp.reshape(-1, F), num_threads=1).reshape(G, K)
    return PK.rollout(sc, Xp, reps, steps, T.TUNE_S, _MARGIN.get(path, 0.05), kind, cue)


_BOOSTERS: dict = {}
_MARGIN: dict = {}


def _booster(path: str):
    if path not in _BOOSTERS:
        from redzone.noggin import pick as PK
        _BOOSTERS[path] = PK.load_booster(Path(path))
    return _BOOSTERS[path]


def grade_slates(args) -> list[dict]:
    """[(source, slate id, replays, lag)] -> per slate: counts for every policy and oracle objective."""
    slates, policies, cue_s = args
    import train_noggin_pick as T
    from redzone.noggin import pick as PK
    out = []
    for source, sid, reps, lag in slates:
        if len(reps) < 2:
            continue
        t0, t1 = min(r.wall[0] for r in reps) - 600.0, max(r.wall[-1] for r in reps) + 600.0
        n = O.Night(reps, T.MINE, lag, T.TUNE_S, t0, t1)
        steps = np.arange(t0, t1, O.STEP_S)
        tab = O.tables(n, steps)
        X = PK.features_for_night(reps, tab, steps, lag, T.MINE)
        row = {"source": source, "slate": sid, "games": len(reps), "hours": (t1 - t0) / 3600}
        for obj in OBJ:
            row[f"oracle:{obj}"] = O.counts(O.grade(n, O.solve(n, obj, tab)))
            if cue_s > 0:
                row[f"oracle_cue:{obj}"] = O.counts(O.grade(n, O.solve_cue(n, obj, tab, max_cue_s=cue_s)))
        for spec in policies:
            row[spec] = O.counts(O.grade(n, _policy_switches(spec, n, steps, tab, X, reps, cue_s)))
        out.append(row)
    return out


def totals(rows: list[dict], key: str) -> dict:
    t: dict = {}
    for r in rows:
        for k, v in (r.get(key) or {}).items():
            t[k] = t.get(k, 0) + v
    return t


def summarize(rows: list[dict], policies: list[str], cue_s: float) -> dict:
    out = {}
    for source in sorted({r["source"] for r in rows}):
        rs = [r for r in rows if r["source"] == source]
        orc = {o: totals(rs, f"oracle:{o}") for o in OBJ}
        orc_cue = {o: totals(rs, f"oracle_cue:{o}") for o in OBJ} if cue_s > 0 else {}
        src = {"slates": len(rs), "hours": round(sum(r["hours"] for r in rs), 1), "policies": {}}
        line = lambda t: dict(big_live_pct=round(100 * t["big_live"] / t["big"], 1) if t.get("big") else None,   # noqa: E731
                              crunch_live_pct=round(100 * t["crunch_live"] / t["crunch"], 1) if t.get("crunch") else None,
                              dead_air_pct=round(100 * t["dead_s"] / t["watched_s"], 1) if t.get("watched_s") else None,
                              mid_play=t.get("mid_play_cuts", 0) + t.get("entries_mid_play", 0), switches=t.get("switches", 0))
        src["oracle"] = {o: line(orc[o]) for o in OBJ}
        if orc_cue:
            src["oracle_cue"] = {o: line(orc_cue[o]) for o in OBJ}
        for spec in policies:
            t = totals(rs, spec)
            src["policies"][spec] = dict(line(t), pct_of_oracle=pct_of_oracle(t, orc),
                                         pct_of_oracle_cue=pct_of_oracle(t, orc_cue) if orc_cue else None)
        out[source] = src
    return out


def markdown(summary: dict) -> str:
    lines = ["| source | policy | big live | crunch live | dead air | mid-play | % of oracle (live) | % of oracle (cue) |",
             "|---|---|---:|---:|---:|---:|---:|---:|"]
    for source, s in summary.items():
        for spec, p in s["policies"].items():
            cue = (p.get("pct_of_oracle_cue") or {}).get("pct")
            lines.append(f"| {source} ({s['slates']}) | {spec.split(':')[0]} | {p['big_live_pct']} | {p['crunch_live_pct']} | "
                         f"{p['dead_air_pct']} | {p['mid_play']} | {p['pct_of_oracle']['pct']} | {cue} |")
    return "\n".join(lines)


def main(argv=None) -> int:
    import noggin_selfplay as SP
    import train_noggin_pick as T
    ap = argparse.ArgumentParser()
    ap.add_argument("league")
    ap.add_argument("--raw", type=Path, action="append", required=True, help="season file(s): hoopR parquet or espn jsonl.gz")
    ap.add_argument("--policy", action="append", default=None, help="hand | pick:<model> | pick-cue:<model>")
    ap.add_argument("--synthetic", type=int, default=2, help="self-play slates per real night (0 = real only)")
    ap.add_argument("--nights", type=int, default=0, help="first N nights per season (0 = every night: the full set)")
    ap.add_argument("--cue-s", type=float, default=90.0)
    ap.add_argument("--margin", type=float, default=0.05)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--json", type=Path, default=None)
    a = ap.parse_args(argv)
    policies = a.policy or ["hand"]
    for spec in policies:
        if ":" in spec:
            _MARGIN[spec.partition(":")[2]] = a.margin
    t0 = time.time()
    work = []
    for raw in a.raw:
        nights = T.season_nights(a.league, raw)
        if a.nights:
            nights = dict(sorted(nights.items())[:a.nights])
        work += [("real", night, reps, T.LAG_S) for night, reps in sorted(nights.items())]
        if a.synthetic:
            rng = np.random.default_rng([a.seed, len(work)])
            work += [("synthetic", sid, reps, lag) for sid, reps, lag in SP.synthetic_slates(nights, a.synthetic, rng)]
    chunks = [work[i:i + 8] for i in range(0, len(work), 8)]
    with ProcessPoolExecutor(a.jobs) as ex:
        rows = [r for part in ex.map(grade_slates, [(c, policies, a.cue_s) for c in chunks]) for r in part]
    summary = summarize(rows, policies, a.cue_s)
    res = {"league": a.league, "raw": [str(p) for p in a.raw], "policies": policies, "synthetic": a.synthetic,
           "cue_s": a.cue_s, "seconds": round(time.time() - t0), "summary": summary}
    if a.json:
        a.json.write_text(json.dumps(res, indent=1))
    print(markdown(summary))
    print(json.dumps({k: v["oracle"] for k, v in summary.items()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
