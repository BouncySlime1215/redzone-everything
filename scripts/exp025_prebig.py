"""EXP-025: arrive at the dead ball BEFORE the big play (pre-registered in docs/prereg/exp-025.md).

Paired scorecards on DEV nights only (the `fit` split = train + valid; the locked `test` split is
never read): every night is replayed twice per seed, the app as it ships (control) and the app with
`noggin.prebig.enabled=true` (redzone/noggin/prebig.py), under the measured feed-lag model (seeded,
like the North Star cards). Deltas get a night-cluster bootstrap 95 % CI.

    .venv/bin/python scripts/exp025_prebig.py --data-root <checkout with data/archive> --json out.json
    .venv/bin/python scripts/exp025_prebig.py --leagues nba nhl --seeds 0 1 2 --md table.md

Heavy-ish (minutes per league): run it with `taskpolicy -b` on the Mac, never during a live game.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from redzone.config import Config  # noqa: E402
from redzone.lab import buildup, evaldata, sportsgrade  # noqa: E402
from redzone.lab.backtest import run  # noqa: E402
from redzone.models import League  # noqa: E402

LEAGUES = ("nfl", "nba", "nhl")
SEEDS = (0, 1, 2)
SPLIT = "fit"          # dev nights only; "test" (LOCKED) is refused below
MIN_NIGHTS = 8         # under this per league: PENDING
BAR_PTS = 2.0          # big plays seen live must rise by >= this (seed mean, points) ...
BAR_LEAGUES = 2        # ... in at least this many of NFL / NBA / NHL
BOOT = 2000
FLAG = "noggin.prebig.enabled"


def night_row(card, rep, values) -> dict:
    cs = card.cut.summary()
    landed, n_land = card.cut.landings()
    b = buildup.summarize(values)
    return {"big": card.big, "big_live": card.big_live, "landings": n_land, "landings_dead": landed,
            "entries": cs["entries"], "entries_mid_play": cs["entries_mid_play"], "cuts": len(card.cuts),
            "cuts_high": len(card.cuts_high), "plays_missed_entry": card.cut.plays_missed_entry,
            "dead_s": round(card.dead, 1), "watched_s": round(card.watched, 1),
            "crunch": card.crunch, "crunch_live": card.crunch_live, "switches": card.switches,
            "prebig_switches": sum(1 for s in rep.switches if s.kind == "prebig"),
            "buildup": [v for v in values if v is not None], "buildup_n_big": b["big"],
            "arrived_before": b["arrived_before"]}


def run_league(lg: League, seeds, data_root: Path, max_nights: int | None) -> dict:
    import eval_sports as ES
    ES.ROOT = data_root   # read the archive from the checkout that has it (read-only)
    pinned = data_root / "data/eval_config.yaml"
    end = evaldata.window_end(lg.value, ES.EVAL_END)
    nights = list(ES.nights(lg, 999, end, SPLIT))
    if max_nights:
        nights = nights[-max_nights:]
    out = {"nights": [d for d, _, _ in nights], "rows": {}}
    for seed in seeds:
        for arm in ("control", "prebig"):
            cfg = Config(user_path=pinned if pinned.exists() else data_root / "data/config.yaml")
            evaldata.use_shipped_switch_costs(cfg)
            cfg.data.setdefault("noggin", {}).setdefault("prebig", {})["enabled"] = arm == "prebig"
            mine = set(cfg.get(f"teams.{lg.value}") or ["WSH"])
            kn = dict(seed=seed, feed_lag_model="measured")
            for day, reps, targets in nights:
                if arm == "prebig":
                    with buildup.prebig_runs():
                        rep = run(reps, cfg, mine, targets=targets, **kn)
                else:
                    rep = run(reps, cfg, mine, targets=targets, **kn)
                card = sportsgrade.grade(reps, rep.switches, mine, rep.lag_true, 5.0, rep.t0, rep.t1, rep.replayed)
                vals = buildup.buildup(reps, rep.switches, rep.lag_true, 5.0, rep.t1)
                out["rows"].setdefault(f"{arm}:{seed}", {})[day] = night_row(card, rep, vals)
            print(f"  {lg.value} seed {seed} {arm}: {len(nights)} nights", flush=True)
    return out


# ---- analysis ------------------------------------------------------------------------------------------

def _rate(rows, a, b):
    den = sum(r[b] for r in rows)
    return 100.0 * sum(r[a] for r in rows) / den if den else float("nan")


def _sum_seeds(res: dict, arm: str, seeds) -> dict[str, dict]:
    """Per night, counts summed over seeds (= the seed mean of a pooled rate's numerator and denominator)."""
    out: dict[str, dict] = {}
    for s in seeds:
        for day, r in res["rows"][f"{arm}:{s}"].items():
            acc = out.setdefault(day, {})
            for k, v in r.items():
                if isinstance(v, (int, float)):
                    acc[k] = acc.get(k, 0) + v
    return out


def delta_ci(ctl: dict, arm: dict, a: str, b: str, scale: float = 1.0, seed: int = 7):
    """Pooled-rate delta (arm - control) and its 95 % night-cluster bootstrap interval (paired nights)."""
    days = sorted(set(ctl) & set(arm))
    if not days:
        return None, None, None
    pt = scale * (_rate([arm[d] for d in days], a, b) - _rate([ctl[d] for d in days], a, b)) / 100.0
    rng = random.Random(seed)
    ds = []
    for _ in range(BOOT):
        pick = [days[rng.randrange(len(days))] for _ in days]
        x = scale * (_rate([arm[d] for d in pick], a, b) - _rate([ctl[d] for d in pick], a, b)) / 100.0
        if x == x:
            ds.append(x)
    ds.sort()
    if not ds:
        return pt, None, None
    return pt, ds[int(0.025 * len(ds))], ds[min(len(ds) - 1, int(0.975 * len(ds)))]


def _median(xs):
    xs = sorted(xs)
    return round(xs[len(xs) // 2] if len(xs) % 2 else (xs[len(xs) // 2 - 1] + xs[len(xs) // 2]) / 2, 1) if xs else None


def analyse(results: dict, seeds) -> dict:
    out = {}
    for lg, res in results.items():
        ctl, arm = _sum_seeds(res, "control", seeds), _sum_seeds(res, "prebig", seeds)
        per_seed = []
        for s in seeds:
            c, p = res["rows"][f"control:{s}"], res["rows"][f"prebig:{s}"]
            per_seed.append(round(_rate(list(p.values()), "big_live", "big") - _rate(list(c.values()), "big_live", "big"), 2))
        line = {
            "nights": len(res["nights"]),
            "big": sum(r["big"] for r in ctl.values()) // max(1, len(seeds)),
            "big_live_control": round(_rate(list(ctl.values()), "big_live", "big"), 2),
            "big_live_prebig": round(_rate(list(arm.values()), "big_live", "big"), 2),
            "big_live_delta": delta_ci(ctl, arm, "big_live", "big", 100.0),
            "big_live_delta_per_seed": per_seed,
            "big_live_delta_seed_mean": round(sum(per_seed) / len(per_seed), 2),
            "landings_dead_delta": delta_ci(ctl, arm, "landings_dead", "landings", 100.0),
            "dead_air_delta": delta_ci(ctl, arm, "dead_s", "watched_s", 100.0),
            "plays_missed_entry_per100_delta": delta_ci(ctl, arm, "plays_missed_entry", "landings", 100.0),
            "crunch_live_delta": delta_ci(ctl, arm, "crunch_live", "crunch", 100.0),
            "entries_mid_play": {"control": sum(r["entries_mid_play"] for r in ctl.values()),
                                 "prebig": sum(r["entries_mid_play"] for r in arm.values())},
            "cuts_high": {"control": sum(r["cuts_high"] for r in ctl.values()),
                          "prebig": sum(r["cuts_high"] for r in arm.values())},
            "switches": {"control": sum(r["switches"] for r in ctl.values()),
                         "prebig": sum(r["switches"] for r in arm.values())},
            "prebig_switches": sum(r["prebig_switches"] for r in arm.values()),
            # SECONDARY (info, this module's own measure, not a frozen grader)
            "buildup_median_s": {
                arm_name: _median([v for s in seeds for r in res["rows"][f"{arm_name}:{s}"].values() for v in r["buildup"]])
                for arm_name in ("control", "prebig")},
            "arrived_before": {"control": sum(r["arrived_before"] for r in ctl.values()),
                               "prebig": sum(r["arrived_before"] for r in arm.values())},
        }
        out[lg] = line
    return verdict(out)


def _ci_has_zero_or_better(ci, better_sign: int) -> bool:
    """'No worse': the CI of the delta includes 0, or lies wholly on the better side."""
    pt, lo, hi = ci
    if lo is None:
        return True
    return lo <= 0 <= hi or (better_sign > 0 and lo > 0) or (better_sign < 0 and hi < 0)


def verdict(lines: dict) -> dict:
    passes, pending, fails = [], [], []
    for lg, x in lines.items():
        why = []
        pt, lo, hi = x["big_live_delta"]
        if x["nights"] < MIN_NIGHTS:
            x["status"] = "PENDING"
            x["why"] = f"{x['nights']} nights < {MIN_NIGHTS}"
            pending.append(lg)
            continue
        if x["entries_mid_play"]["prebig"] > x["entries_mid_play"]["control"] or x["entries_mid_play"]["prebig"]:
            why.append("mid-play entries > 0")
        if x["cuts_high"]["prebig"]:
            why.append("high-leverage leaves > 0")
        if not _ci_has_zero_or_better(x["dead_air_delta"], -1):
            why.append("dead air worse")
        if not _ci_has_zero_or_better(x["plays_missed_entry_per100_delta"], -1):
            why.append("plays missed on entry worse")
        if hi is not None and hi < 0:
            why.append("big plays live worse beyond its CI")
        lift = x["big_live_delta_seed_mean"] >= BAR_PTS and lo is not None and lo > 0
        x["lift"] = lift
        x["guard_fail"] = why
        x["status"] = "FAIL" if why else ("PASS" if lift else "NO LIFT")
        (fails if why else passes if lift else []).append(lg)
    if any(lines[lg]["status"] == "FAIL" for lg in lines):
        overall = "FAIL"
    elif len(passes) >= BAR_LEAGUES:
        overall = "PASS"
    elif len(passes) + len(pending) >= BAR_LEAGUES:
        overall = "PENDING"
    else:
        overall = "FAIL"
    return {"leagues": lines, "overall": overall, "passing": passes, "pending": pending}


def _fmt(ci):
    pt, lo, hi = ci
    if pt is None:
        return "n/a"
    if lo is None:
        return f"{pt:+.2f}"
    return f"{pt:+.2f} [{lo:+.2f}, {hi:+.2f}]"


def markdown(an: dict) -> str:
    rows = ["| League | Nights | Big plays | Big live ctl -> prebig | Delta (seed mean; per seed) | Delta CI 95 % |"
            " Landings dead delta | Dead air delta | Missed on entry /100 delta | Crunch live delta |"
            " Mid-play entries | HL leaves | Pre-big switches | Build-up median s (secondary) | Status |",
            "|---|---:|---:|---|---|---|---|---|---|---|---|---|---:|---|---|"]
    for lg, x in an["leagues"].items():
        rows.append(f"| {lg.upper()} | {x['nights']} | {x['big']} | {x['big_live_control']} -> {x['big_live_prebig']} | "
                    f"{x['big_live_delta_seed_mean']:+.2f}; {', '.join(f'{v:+.2f}' for v in x['big_live_delta_per_seed'])} | "
                    f"{_fmt(x['big_live_delta'])} | {_fmt(x['landings_dead_delta'])} | {_fmt(x['dead_air_delta'])} | "
                    f"{_fmt(x['plays_missed_entry_per100_delta'])} | {_fmt(x['crunch_live_delta'])} | "
                    f"{x['entries_mid_play']['control']} -> {x['entries_mid_play']['prebig']} | "
                    f"{x['cuts_high']['control']} -> {x['cuts_high']['prebig']} | {x['prebig_switches']} | "
                    f"{x['buildup_median_s']['control']} -> {x['buildup_median_s']['prebig']} | "
                    f"{x['status']}{' (' + '; '.join(x.get('guard_fail') or []) + ')' if x.get('guard_fail') else ''}"
                    f"{' (' + x['why'] + ')' if x.get('why') else ''} |")
    rows += ["", f"**Overall: {an['overall']}** (bar: >= +{BAR_PTS} pts with CI lower bound > 0 in >= {BAR_LEAGUES} of "
             f"NFL/NBA/NHL; guards: mid-play entries 0, high-leverage leaves 0, dead air and plays missed on entry no worse)"]
    return "\n".join(rows)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--leagues", nargs="+", default=list(LEAGUES), choices=LEAGUES)
    ap.add_argument("--seeds", nargs="+", type=int, default=list(SEEDS))
    ap.add_argument("--data-root", type=Path, default=ROOT, help="checkout whose data/archive holds the pinned nights")
    ap.add_argument("--max-nights", type=int, default=None, help="newest N dev nights per league (default: all)")
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--md", type=Path, default=None)
    ap.add_argument("--split", default=SPLIT)
    a = ap.parse_args(argv)
    if a.split != SPLIT:
        sys.exit("EXP-025 reads dev nights only (split 'fit'); the locked 'test' split is not the lane's to read")
    t0 = time.time()
    results = {lg: run_league(League(lg), a.seeds, a.data_root, a.max_nights) for lg in a.leagues}
    an = analyse(results, a.seeds)
    an["seconds"] = round(time.time() - t0)
    an["seeds"] = a.seeds
    an["split"] = SPLIT
    an["eval_data"] = evaldata.stamp(a.data_root)
    md = markdown(an)
    print(md)
    if a.json:
        a.json.write_text(json.dumps({"analysis": an, "raw": results}, indent=1, default=str))
    if a.md:
        a.md.write_text(md + "\n")


if __name__ == "__main__":
    main()
