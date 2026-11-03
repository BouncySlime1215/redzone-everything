"""The scorecard every change is judged by. Same 16 real 2025 NFL slates every time.

    .venv/bin/python scripts/eval.py            # totals
    .venv/bin/python scripts/eval.py --slates   # plus per-slate lines
    .venv/bin/python scripts/eval.py --dead     # plus dead air by cause (attack the biggest)

Real-world messiness (all off by default; see backtest.run):
    --true-delay 41      the stream really runs 41 s behind (the app still believes 35)
    --sigma 5            the uncertainty the app states for its delay estimate
    --lag-jitter         each play reaches the feed 3-25 s after it happened (seeded)
    --drop [RATE]        the source goes silent 60-120 s, RATE times per game-minute (0.1)
    --late-plays [FRAC]  this fraction of plays arrive a poll late / out of order (0.15)
    --no-wallclock [FRAC]  this fraction of plays come without a wallclock (0.5)
    --seed N             which random draw of the above

Targets: mid_play_cuts 0 (hard), dead_air_pct -> 0, big_seen_pct -> 100,
crunch_live_pct as high as physically possible, your_team_crunch_live_pct 100.
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone.config import Config  # noqa: E402
from redzone.lab.backtest import fmt_time, run  # noqa: E402
from redzone.lab.cutgrade import CutCard  # noqa: E402
from redzone.lab.optimize import load_slates  # noqa: E402


def eval_set(n=16):
    slates = load_slates(ROOT / "data/archive/nfl")
    # Every slate with Washington in it, then an even spread of the rest.
    wsh = [s for s in slates if any("WSH" in r.matchup.split() for r in s)]
    rest = [s for s in slates if s not in wsh]
    k = max(0, n - min(len(wsh), n // 2))
    step = max(1, len(rest) // max(1, k))
    return (wsh[: n // 2] + rest[::step][:k])[:n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slates", action="store_true")
    ap.add_argument("--dead", action="store_true", help="print dead air by cause")
    ap.add_argument("--n", type=int, default=16)
    ap.add_argument("--true-delay", type=float, default=None)
    ap.add_argument("--sigma", type=float, default=3.0)
    ap.add_argument("--lag-jitter", action="store_true")
    ap.add_argument("--drop", type=float, nargs="?", const=0.1, default=0.0)
    ap.add_argument("--late-plays", type=float, nargs="?", const=0.15, default=0.0)
    ap.add_argument("--no-wallclock", type=float, nargs="?", const=0.5, default=0.0)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    knobs = dict(true_delay=a.true_delay, sigma=a.sigma, lag_jitter=(3.0, 25.0) if a.lag_jitter else None,
                 drop=a.drop, late_plays=a.late_plays, no_wallclock=a.no_wallclock, seed=a.seed)
    # The live service rewrites data/config.yaml (measured switch costs, taps): scorecards read a
    # pinned copy when one exists, so a before/after comparison isn't moved by the live night.
    pinned = ROOT / "data/eval_config.yaml"
    cfg = Config(user_path=pinned if pinned.exists() else ROOT / "data/config.yaml")
    t0 = time.time()
    tot = dict(big=0, seen=0, live=0, cuts=0, dead=0.0, watched=0.0, crunch=0, crunch_live=0,
               my_crunch=0, my_crunch_live=0, switches=0, hours=0.0, my_big=0, my_seen=0, blackout=0.0)
    causes: dict[str, float] = {}
    cut = CutCard()
    for reps in eval_set(a.n):
        r = run(reps, cfg, set(cfg.get("teams.nfl")), **knobs)
        tot["big"] += r.big_plays; tot["seen"] += r.big_plays_caught; tot["live"] += r.big_plays_live
        tot["cuts"] += len(r.mid_play_cuts); tot["dead"] += r.dead_air_s; tot["watched"] += r.watched_s
        tot["crunch"] += r.crunch; tot["crunch_live"] += r.crunch_live
        tot["my_crunch"] += r.my_crunch; tot["my_crunch_live"] += r.my_crunch_live
        tot["switches"] += len(r.switches); tot["hours"] += r.hours
        tot["blackout"] += r.tuning_blackout_s
        tot["my_big"] += r.my_big_plays; tot["my_seen"] += r.my_big_plays_live
        cut.add(r.cut)
        for k, v in r.dead_causes.items():
            causes[k] = causes.get(k, 0.0) + v
        if a.slates:
            print(" ", reps[0].matchup, f"+{len(reps)-1}", json.dumps(r.summary))
            for t, what in r.mid_play_cuts:
                print("     cut:", fmt_time(t), what)
            for t, kind, what in r.cut.mid_entries:
                print("     mid-play entry:", fmt_time(t), kind, what)
    pct = lambda x, y: round(100 * x / max(1, y), 1)
    out = {"mid_play_cuts": tot["cuts"], "dead_air_pct": pct(tot["dead"], tot["watched"]),
           "big_seen_pct": pct(tot["seen"], tot["big"]), "big_live_pct": pct(tot["live"], tot["big"]),
           "crunch_live_pct": pct(tot["crunch_live"], tot["crunch"]),
           "your_team_crunch_live_pct": pct(tot["my_crunch_live"], tot["my_crunch"]),
           "your_team_big_seen_pct": pct(tot["my_seen"], tot["my_big"]),
           "switches_per_hour": round(tot["switches"] / max(0.1, tot["hours"]), 1),
           "tuning_blackout_s": round(tot["blackout"], 1),
           "tuning_blackout_pct": pct(tot["blackout"], tot["hours"] * 3600),
           "big_plays": tot["big"], "crunch_plays": tot["crunch"], **cut.summary(),
           "seconds": round(time.time() - t0)}
    print(json.dumps(out, indent=1))
    if a.dead:
        print("dead air by cause (points of dead_air_pct):")
        for k, v in sorted(causes.items(), key=lambda kv: -kv[1]):
            print(f"  {pct(v, tot['watched']):5.1f}  {k}")


if __name__ == "__main__":
    main()
