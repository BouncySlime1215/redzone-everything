"""Read a park_sim sweep CSV: the best safe settings beside today's physics, and what each knob does on average.

    python scripts/park_report.py sweep.csv [--league nba] [--top 5]

A row is SAFE when its Rule #1 counters are all zero (mid-play entries, mid-play leaves, high-leverage leaves), in the worse
delay-error arm when it has one. Settings are compared within one league, game count, brain and value source, because the
value source changes what the brain asks for and a yes-rate that rises because the brain asks for less does not count.
Stdout only, numbers only. No TV, no network.

Why: thousands of sweep rows are only useful once the safe ones are ranked beside the baseline in Nick's format.
"""
from __future__ import annotations

import argparse
import csv
import statistics
from collections import defaultdict

KNOBS = ("pause_holds", "load_s", "pos_sd", "delay_sd", "stamp_sd", "key_sd", "pad_k", "delay_error", "min_dead", "max_behind", "ping_pong",
         "min_stay", "min_live_s", "land_wait_max", "margin", "repark_margin", "lookahead_s", "bore_w", "skip_dead", "clips", "leave_rule",
         "park_at", "park_target", "shots", "prepark", "live_w")
TODAY_SD, SPEC_SD = 2.5, 0.75      # total error sd: the TV as measured (about 3 s) and the TV the next session has to prove (0.5 s)
WINDOWS = (5, 10, 20, 30, 45, 60)


def num(v: str) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def breaks(r: dict) -> int:
    return int(num(r["mid_entry"]) + num(r["mid_leave"]) + num(r["hl_leave"]))


def total_sd(r: dict) -> float:
    """The four error parts together (root of the summed squares), plus the fixed delay error when the row has one."""
    return (num(r["pos_sd"]) ** 2 + num(r["delay_sd"]) ** 2 + num(r["stamp_sd"]) ** 2 + num(r["key_sd"]) ** 2) ** 0.5 + abs(num(r["delay_error"]))


def served(r: dict) -> float:
    """Jumps a minute times the yes-rate: pace that was asked for and delivered."""
    y = num(r["yes_rate"])
    return num(r["jumps_per_min"]) * (y if y == y else 0.0)


def line(r: dict) -> str:
    win = "  ".join(f"{w}m {r[f'per_{w}m_med']}/{r[f'per_{w}m_p10']}/{r[f'per_{w}m_max']}" for w in WINDOWS)
    return (f"{r['tag']:14s} {num(r['jumps_per_min']):.2f}/min  {win}  yes {100 * num(r['yes_rate']):.0f}% (leave-rule ceiling {100 * num(r['yes_ceiling']):.0f}%)  "
            f"asks {r['wanted_s_per_min']} s/min  play-visits {r['play_visit_share']}  "
            f"big {100 * num(r['big_seen']):.0f}% (live {100 * num(r['big_live']):.0f} parked {100 * num(r['big_parked']):.0f} clip {100 * num(r['big_clip']):.0f})  "
            f"v2 {100 * num(r['v2_seen']):.0f}%  live-share {r['live_share']}  ads {r['commercial_share']}  behind {r['behind_med_s']}/{r['behind_p90_s']} s  "
            f"R1 {r['mid_entry']}/{r['mid_leave']}/{r['hl_leave']}  visit {r['visit_med_s']} s")


def knobs(r: dict) -> str:
    return " ".join(f"{k}={r[k]}" for k in KNOBS)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("csv")
    ap.add_argument("--league", default=None)
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--value", default="dense")
    ap.add_argument("--effects", action="store_true", help="also the mean of each knob's values over the safe-or-not rows")
    a = ap.parse_args()
    rows = [r for r in csv.DictReader(open(a.csv)) if not a.league or r["league"] == a.league]
    groups = defaultdict(list)
    for r in rows:
        groups[(r["league"], int(r["games"]), r["brain"])].append(r)
    for (lg, ng, brain), rs in sorted(groups.items()):
        base = [r for r in rs if r["tag"] == "baseline_live"]
        cand = [r for r in rs if r["mode"] == "park" and r["value"] == a.value and r["tag"] != "default"]
        safe = [r for r in cand if breaks(r) == 0 and r["leave_rule"] == "dead"]
        real = [r for r in safe if total_sd(r) >= TODAY_SD]          # the error the TV has today
        spec = [r for r in safe if total_sd(r) <= SPEC_SD]           # the error the next TV session has to prove
        print(f"\n== {lg} ({ng} games), brain {brain}: {len(rs)} rows, {len(cand)} park rows on value={a.value}, {len(safe)} safe, "
              f"{len(real)} safe at today's error, {len(spec)} safe at the spec error")
        for r in base:
            print("  today's physics  " + line(r))
        for title, pool, key in (("TODAY'S ERROR, safe: most served pace (jumps x yes)", real, served),
                                 ("TODAY'S ERROR, safe: most jumps", real, lambda r: num(r["jumps_per_min"])),
                                 ("TODAY'S ERROR, safe: best yes-rate", real, lambda r: num(r["yes_rate"]) if r["yes_rate"] != "" else -1.0),
                                 ("SPEC ERROR (sd 0.5 s), safe: most served pace", spec, served),
                                 ("SPEC ERROR, safe: most jumps", spec, lambda r: num(r["jumps_per_min"])),
                                 ("SPEC ERROR, safe: best yes-rate", spec, lambda r: num(r["yes_rate"]) if r["yes_rate"] != "" else -1.0)):
            print(f"  -- {title}")
            for r in sorted(pool, key=key, reverse=True)[:a.top]:
                print("     " + line(r))
                print("        " + knobs(r))
        if a.effects:
            print("  -- knob effects over all park rows on this value (mean jumps/min | mean yes % | share safe | n)")
            for k in KNOBS:
                by = defaultdict(list)
                for r in cand:
                    by[r[k]].append(r)
                if len(by) < 2:
                    continue
                parts = []
                for v, xs in sorted(by.items(), key=lambda kv: (num(kv[0]), kv[0])):
                    ys = [num(x["yes_rate"]) for x in xs if x["yes_rate"] != ""]
                    parts.append(f"{v}: {statistics.mean(num(x['jumps_per_min']) for x in xs):.2f} | {100 * statistics.mean(ys) if ys else float('nan'):.0f} | "
                                 f"{sum(1 for x in xs if breaks(x) == 0) / len(xs):.2f} | {len(xs)}")
                print(f"     {k:14s} " + "   ".join(parts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
