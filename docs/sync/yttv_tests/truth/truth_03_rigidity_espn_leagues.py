#!/usr/bin/env python3
"""truth_03 rigidity_espn_leagues: can ESPN's own play wallclocks serve as the truth line per league?

Hypothesis: ESPN summary plays carry wallclock (1 s resolution) + period + clock for NBA and college
basketball, and wallclock gaps match clock gaps to <= 2 s (1 s quantisation on both ends) in >= 70%
of pairs whose first play does not stop the clock. Guard: if ALL pairs (stoppages included) look
rigid (>= 90%), the wallclock is synthetic (computed from the clock, as NHL's appears to be: its clock
counts up and whistles never show as gaps) and that league gets N/A, not PASS - it carries no
real-world time.
This is the fallback truth for leagues without an NBA-grade feed (NCAA on ESPN/ESPN2/ESPNU/BTN).

Procedure: for each ESPN game (league from plays.jsonl, or --league with --game), parse summary plays,
pair consecutive same-period plays, residual = dWall - dClock; per league report has_wallclock fraction,
ran fraction for non-stop pairs, |residual| percentiles. Live: re-fetch the summary for --game every
30 s for --minutes and score at the end. --dry-run: cached summaries of every recorded game.

Pass rule (per league with wallclock): ran-fraction >= 0.70 and p90 |residual| <= 2.0 s.
Overall PASS if NBA and every basketball league with wallclock pass; leagues without wallclock N/A.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import truthlib as T  # noqa: E402

ID = "truth_03"


def main():
    ap = T.parser(ID, __doc__)
    ap.add_argument("--league", default="nba")
    a = ap.parse_args()
    out = T.Out(a.out, ID, a)
    if a.dry_run:
        games = {a.game: a.league} if a.game else T.recorded_games(a.days.split(","))
        print(f"[{ID}] dry-run: would re-fetch ESPN summary every 30 s for {a.minutes} min; "
              f"scoring {len(games)} recorded games")
        summ = {g: T.espn_summary(lg, g, True) for g, lg in games.items()}
    else:
        if not a.game:
            sys.exit("live mode needs --game")
        games = {a.game: a.league}
        t0 = time.time()
        while True:
            summ = {a.game: T.espn_summary(a.league, a.game, False, refresh=True)}
            if time.time() - t0 > a.minutes * 60:
                break
            time.sleep(30)
    per_league = {}
    for g, lg in games.items():
        s = summ.get(g)
        if not s:
            out.write("game", game=g, league=lg, ok=False, why="no summary cached")
            continue
        raw = s.get("plays", [])
        acts = T.espn_actions(s)
        L = per_league.setdefault(lg, dict(raw=0, wall=0, run=0, ran=0, res=[], all=0, all_ran=0))
        L["raw"] += len(raw)
        L["wall"] += len(acts)
        for p in T.pairs(acts, tol=2.0):
            if p["dc"] > 0:
                L["all"] += 1
                L["all_ran"] += p["ran"]
            if p["dc"] <= 0 or T.espn_is_stop(p["a"]):
                continue
            L["run"] += 1
            if p["ran"]:
                L["ran"] += 1
                L["res"].append(abs(p["res"]))
        out.write("game", game=g, league=lg, ok=True, plays=len(raw), with_wallclock=len(acts))
    verdicts = {}
    for lg, L in per_league.items():
        wf = L["wall"] / max(1, L["raw"])
        if wf < 0.5:
            verdicts[lg] = dict(status="N/A no wallclock", wallclock_frac=round(wf, 3))
            continue
        allf = L["all_ran"] / max(1, L["all"])
        if allf >= 0.9:
            verdicts[lg] = dict(status="N/A synthetic wallclock", wallclock_frac=round(wf, 3),
                                all_pairs_ran_frac=round(allf, 3))
            out.write("league", league=lg, **verdicts[lg])
            continue
        frac = L["ran"] / max(1, L["run"])
        p90 = T.pct(L["res"], 0.9)
        verdicts[lg] = dict(status="PASS" if frac >= 0.7 and p90 is not None and p90 <= 2.0 else "FAIL",
                            wallclock_frac=round(wf, 3), ran_frac=round(frac, 3), all_pairs_ran_frac=round(allf, 3), abs_res=T.summ(L["res"]))
        out.write("league", league=lg, **verdicts[lg])
    judged = [v for v in verdicts.values() if not v["status"].startswith("N/A")]
    passed = None if not judged else all(v["status"] == "PASS" for v in judged)
    out.verdict(passed, "per league with wallclock: ran_frac>=0.70 and p90|res|<=2.0 s", leagues=verdicts)


if __name__ == "__main__":
    main()
