#!/usr/bin/env python3
"""truth_02 rigidity_nba: is the NBA game clock rigid against real time between feed actions?

Hypothesis: between two consecutive NBA liveData actions A -> B where A does not stop the clock,
real elapsed time (timeActual B - A) equals game-clock elapsed (clock A - clock B) to <= 1 s, in
>= 80% of pairs; the failures are unlogged stoppages (out of bounds, deflections), not clock drift.
That makes wall(period, clock) exact to ~0.3-0.5 s inside running segments.

Procedure: build consecutive same-period pairs from the pbp; residual = dWall - dClock; classify A as
clock-stopping (truthlib.is_stop) or running; report |residual| percentiles for pairs that ran, the
ran-fraction for running-type A per action type, and per game. Also derive feed_sigma = sd(residual
of ran pairs)/sqrt(2) and store it in cache/calib.json (from the dry-run nights only, never live).
Live: poll --game pbp every 15 s for --minutes, then score the pairs whose both actions arrived
during the run. --dry-run: every recorded NBA game, cached pbp.

Pass rule: ran-fraction (running-type A) >= 0.80 AND p90 |residual| among ran pairs <= 1.0 s AND
no game below 0.70 ran-fraction.
"""
import json
import os
import statistics
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import truthlib as T  # noqa: E402

ID = "truth_02"


def score(acts):
    prs = [p for p in T.pairs(acts) if p["dc"] > 0]
    run = [p for p in prs if not T.is_stop(p["a"])]
    ran = [p for p in prs if p["ran"]]
    return prs, run, ran


def main():
    a = T.parser(ID, __doc__).parse_args()
    out = T.Out(a.out, ID, a)
    games = {}
    if a.dry_run:
        print(f"[{ID}] dry-run: would poll pbp for {a.game or 'all live games'} for {a.minutes} min; "
              "scoring recorded games")
        for eid in ([a.game] if a.game else T.recorded_games(a.days.split(","), "nba")):
            nid, acts, _ = T.truth_for(eid, True)
            if acts:
                games[eid] = acts
    else:
        if not a.game:
            sys.exit("live mode needs --game (ESPN id)")
        t0 = time.time()
        acts = []
        while time.time() - t0 < a.minutes * 60:
            nid = T.espn_to_nba(a.game, False)
            acts = T.nba_actions(T.nba_pbp(nid, False, refresh=True)) if nid else []
            time.sleep(15)
        games[a.game] = [x for x in acts if x["wall"] >= t0 - 60]
    allran, allrun, typ_n, typ_ran, per_game = [], [], Counter(), Counter(), {}
    for eid, acts in games.items():
        prs, run, ran = score(acts)
        allran += ran
        allrun += run
        for p in run:
            typ_n[p["a"]["type"]] += 1
            typ_ran[p["a"]["type"]] += p["ran"]
        frac = round(sum(p["ran"] for p in run) / max(1, len(run)), 3)
        res = [abs(p["res"]) for p in ran]
        per_game[eid] = dict(pairs=len(prs), running=len(run), ran_frac=frac, abs_res=T.summ(res))
        out.write("game", game=eid, **per_game[eid])
    res = [p["res"] for p in allran]
    frac = sum(p["ran"] for p in allrun) / max(1, len(allrun))
    sigma = statistics.pstdev(res) / 2 ** 0.5 if len(res) > 2 else None
    if a.dry_run and sigma:
        cp = os.path.join(T.CACHE, "calib.json")
        cal = json.load(open(cp)) if os.path.exists(cp) else {}
        cal.update(feed_sigma=round(sigma, 3), feed_sigma_from=a.days)
        json.dump(cal, open(cp, "w"), indent=1)
    by_type = {k: dict(n=n, ran=round(typ_ran[k] / n, 2)) for k, n in typ_n.most_common(12)}
    p90 = T.pct([abs(r) for r in res], 0.9)
    worst = min((g["ran_frac"] for g in per_game.values()), default=None)
    passed = None if not allrun else (frac >= 0.80 and p90 is not None and p90 <= 1.0 and worst >= 0.70)
    out.verdict(passed, "ran_frac>=0.80, p90|res|<=1.0 s, every game ran_frac>=0.70",
                games=len(games), ran_frac=round(frac, 3), abs_res=T.summ([abs(r) for r in res]),
                bias=round(statistics.fmean(res), 3) if res else None, feed_sigma=sigma and round(sigma, 3),
                worst_game_ran_frac=worst, by_type=by_type)


if __name__ == "__main__":
    main()
