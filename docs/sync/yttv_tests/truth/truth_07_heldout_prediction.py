#!/usr/bin/env python3
"""truth_07 heldout_prediction: predict held-out plays' real time from the truth line.

Hypothesis: with an action removed, the truth line built from the remaining actions predicts its
real time (timeActual) from its (period, clock) to <= 0.6 s at the median and <= 1.5 s at p90 in
every game when the clock was running around it, and the stated error bar covers >= 85% (|err| <=
2 x bar). This is the accuracy ceiling for any D computed from an on-screen clock reading.

Procedure: games ordered by tip (forward chaining, one fold per game). For fold i, feed_sigma is
estimated ONLY from games < i (default 0.35 s for the first fold) - no leakage. For every action k
whose clock lies strictly between its neighbours', drop k, build TruthLine(period acts - k), predict
wall(period, clock_k, display='exact'), err = pred - timeActual. 'rigid' set = both neighbour pairs ran;
'all' set = everything (includes unlogged stops; reported, not gated). Live: at the end of
--minutes, run the same on --game's fresh pbp. --dry-run: recorded games.

Pass rule (rigid set): mean-over-folds p50 |err| <= 0.6 s, worst-fold p90 |err| <= 1.5 s,
coverage >= 0.85.
"""
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import truthlib as T  # noqa: E402

ID = "truth_07"


def sigma_from(games_acts):
    res = [p["res"] for acts in games_acts for p in T.pairs(acts) if p["ran"]]
    return statistics.pstdev(res) / 2 ** 0.5 if len(res) > 20 else 0.35


def fold(acts, sigma):
    rig, alls, cov = [], [], []
    by = {}
    for x in acts:
        by.setdefault(x["period"], []).append(x)
    for per, xs in by.items():
        for i in range(1, len(xs) - 1):
            p, k, n = xs[i - 1], xs[i], xs[i + 1]
            if not (n["clock"] < k["clock"] < p["clock"]):
                continue
            tl = T.TruthLine(xs[:i] + xs[i + 1:], feed_sigma=sigma)
            w = tl.wall(per, k["clock"], display="exact")
            if not w:
                continue
            e = w["wall"] - k["wall"]
            alls.append(abs(e))
            r1 = abs((k["wall"] - p["wall"]) - (p["clock"] - k["clock"])) <= 1.0
            r2 = abs((n["wall"] - k["wall"]) - (k["clock"] - n["clock"])) <= 1.0
            if r1 and r2:
                rig.append(abs(e))
                cov.append(abs(e) <= 2 * max(w["err"], 1e-6))
    return rig, alls, cov


def main():
    a = T.parser(ID, __doc__).parse_args()
    out = T.Out(a.out, ID, a)
    if a.dry_run:
        games = [a.game] if a.game else list(T.recorded_games(a.days.split(","), "nba"))
        print(f"[{ID}] dry-run: would re-run on --game's fresh pbp after {a.minutes} min; {len(games)} recorded folds")
        offline = True
    else:
        if not a.game:
            sys.exit("live mode needs --game")
        time.sleep(a.minutes * 60)
        nid = T.espn_to_nba(a.game, False)
        T.nba_pbp(nid, False, refresh=True)
        games, offline = [a.game], True
    loaded = []
    for g in games:
        nid, acts, _ = T.truth_for(g, offline)
        if acts:
            loaded.append((min(x["wall"] for x in acts), g, acts))
    loaded.sort()
    folds, allcov = [], []
    for i, (_, g, acts) in enumerate(loaded):
        sig = sigma_from([x[2] for x in loaded[:i]])
        rig, alls, cov = fold(acts, sig)
        allcov += cov
        f = dict(game=g, sigma=round(sig, 3), rigid=T.summ(rig), all=T.summ(alls),
                 coverage=round(sum(cov) / max(1, len(cov)), 3))
        folds.append(f)
        out.write("fold", **f)
    ok = [f for f in folds if f["rigid"]["n"]]
    mean_p50 = round(statistics.fmean(f["rigid"]["p50"] for f in ok), 3) if ok else None
    worst_p90 = max((f["rigid"]["p90"] for f in ok), default=None)
    cov = round(sum(allcov) / max(1, len(allcov)), 3)
    passed = None if not ok else (mean_p50 <= 0.6 and worst_p90 <= 1.5 and cov >= 0.85)
    out.verdict(passed, "rigid: mean p50|err|<=0.6, worst-fold p90<=1.5, coverage>=0.85",
                folds=len(ok), mean_p50=mean_p50, worst_fold_p90=worst_p90,
                mean_p90=round(statistics.fmean(f["rigid"]["p90"] for f in ok), 3) if ok else None,
                coverage=cov, all_set_mean_p50=round(statistics.fmean(f["all"]["p50"] for f in ok), 3) if ok else None)


if __name__ == "__main__":
    main()
