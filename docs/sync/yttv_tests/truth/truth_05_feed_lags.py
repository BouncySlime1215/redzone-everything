#!/usr/bin/env python3
"""truth_05 feed_lags: how soon after the real moment does each feed let us certify the truth?

Hypothesis: nba_cdn actions are first seen ~5-15 s after they happen (p50) vs ESPN summary/fastcast
~25-45 s, so a clock reading on YTTV (shown D ~ 29 s after the real moment) can be resolved to a real
wall time within ~1 minute of the observation using nba_cdn: truth availability lag p90 <= 60 s.

Procedure: (1) per source, first-seen lag = arrival t - real wall per distinct play (plays.jsonl).
(2) NBA pbp 'edited - timeActual' per action. (3) truth availability: for every running segment A->B,
sample the clock every 5 s inside it; the wall for those clock values is certified when B arrives,
so avail = first-seen(B) - real wall(c). Report per-source percentiles and availability vs the D
prior (29 s): 'resolved_by_obs+30s' = fraction with avail <= 29 + 30.
Live: poll the pbp for --game every 3 s for --minutes and record first-seen per action ourselves
(read-only HTTP; no stick). --dry-run: recorded plays.jsonl + cached pbp.

Pass rule: nba_cdn first-seen p50 <= 15 s AND truth availability p90 <= 60 s.
"""
import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import truthlib as T  # noqa: E402

ID = "truth_05"


def main():
    a = T.parser(ID, __doc__).parse_args()
    out = T.Out(a.out, ID, a)
    first = {}            # (game, source, play) -> (t, wall)
    if a.dry_run:
        print(f"[{ID}] dry-run: would poll pbp every 3 s for {a.minutes} min and stamp first-seen; "
              "using recorded plays.jsonl")
        for r in T.load_plays(a.days.split(",")):
            if a.game and r["game"] != a.game:
                continue
            k = (r["game"], r["source"], r["play"])
            if k not in first or r["t"] < first[k][0]:
                first[k] = (r["t"], r["wall"], r["league"])
    else:
        if not a.game:
            sys.exit("live mode needs --game")
        nid = T.espn_to_nba(a.game, False)
        t0 = time.time()
        while time.time() - t0 < a.minutes * 60:
            now = time.time()
            for x in T.nba_actions(T.nba_pbp(nid, False, refresh=True)):
                first.setdefault((a.game, "nba_cdn", f"nba{x['n']}"), (now, x["wall"], "nba"))
            time.sleep(3)
    lag = defaultdict(list)
    for (g, src, _), (t, w, lg) in first.items():
        if w:
            lag[f"{lg}:{src}"].append(t - w)
    for k, v in sorted(lag.items()):
        out.write("source", source=k, lag=T.summ(v))
    edited, avail = [], []
    games = {g for (g, src, _) in first if src == "nba_cdn"}
    for g in games:
        nid, acts, _ = T.truth_for(g, True)
        edited += [x["edited"] - x["wall"] for x in acts if x.get("edited")]
        seen = {p: t for (gg, src, p), (t, w, lg) in first.items() if gg == g and src == "nba_cdn"}
        for p in T.pairs(acts):
            if not p["ran"]:
                continue
            fb = seen.get(f"nba{p['b']['n']}")
            if fb is None:
                continue
            c = p["a"]["clock"]
            while c > p["b"]["clock"]:
                w = p["b"]["wall"] - (c - p["b"]["clock"])
                avail.append(fb - w)
                c -= 5
    nba = lag.get("nba:nba_cdn", [])
    rb = sum(x <= 59 for x in avail) / max(1, len(avail))
    passed = None if not nba or not avail else (T.pct(nba, .5) <= 15 and T.pct(avail, .9) <= 60)
    out.verdict(passed, "nba_cdn first-seen p50<=15 s and truth availability p90<=60 s",
                lags={k: T.summ(v) for k, v in lag.items()}, nba_edited_minus_actual=T.summ(edited),
                truth_avail=T.summ(avail), resolved_by_obs_plus_30s=round(rb, 3))


if __name__ == "__main__":
    main()
