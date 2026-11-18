"""One table per slate and timing error: live-edge physics, the last sweep's best pick, the planner, the greedy ceiling.

    nice -n 19 python scripts/park_compare.py --sweep sweep4.csv --grid error_grid_pause.csv --cache <dir> \
        --bonus today=0.5 middle=0.5 spec=0.5 --csv park_compare.csv
    --slates "nba 4" "mnf+3"   --errors today middle spec   --repick 10   --plan key=value

No TV, no adb, no engine, no network, no training. Dev (`fit`) nights only, ALL of them per slate (`--cap`).
Markdown on stdout, every row with every column in the CSV. Numbers only.

Rows, all on the same nights, the same padding (pad_k x the summed error) and the hindsight brain's wants for the yes-rate:
    live-edge physics         both sticks at the live edge, as the last sweep ran it (dead balls only), and with the any-shot rule
    last sweep's pick         park_pick's best safe setting on the sweep CSV, rerun as it was (no lateness cap), then under the
                              cap; under the foresight brain too (the hindsight brain reads the future, an app cannot)
    re-picked under the cap   the `--repick` best safe sweep rows of that slate and error rerun under the cap on all the nights,
                              then picked by the same rule (zero Rule #1 breaks, at least live-edge physics' graded value on
                              screen, most served pace). The sweep has no middle error: there its today and spec rows are used
    planner                   park_plan.Planner at the picked jump_bonus (it reads only what the feed has reported), with the
                              yes-rate on the foresight brain's wants beside it
    greedy ceiling            the supply ceiling: the grid CSV's cell (its own nights, padding and 315 s reach, rewind allowed),
                              and park_ceiling's greedy viewer rerun on THESE nights at THIS padding inside the lateness cap,
                              with the rewind route and without it (the planner has no rewind). It wants nothing but jumps and
                              knows the whole night, so it has no yes-rate and its pace is not a target for a feed-only brain.

Why: Nick has to see in one place how much of the gap to the supply ceiling the planner closes, on every dev night the
slate has, with the lateness cap he asked for, before anything is wired into the live decider.
"""
from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT.as_posix(), (ROOT / "scripts").as_posix()):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import park_ceiling as pc  # noqa: E402
import park_pick  # noqa: E402
import park_plan as pp  # noqa: E402
import park_sim as ps  # noqa: E402
from redzone.noggin import park_core as core  # noqa: E402  (the simulator core: one copy for the app and the scripts)
from redzone.noggin import park_plan as plan_core  # noqa: E402  (the planner: the same)

WINDOWS = (5, 10, 20, 30, 45, 60)
GRID_SD = {"today": 3.0, "middle": 1.0, "spec": 0.5}      # the grid's total-error cell nearest each scenario (0.25 .. 3 s)
HEAD = ("| row | nights | jumps/min | " + " | ".join(f"{w} min" for w in WINDOWS) + " | yes-rate | planned jump delivered (all / firm) | "
        "graded value seen | big plays seen | behind live s (median / p90 / max) | mid-play joins / leaves / high-leverage leaves | flips |")


def pct(v) -> str:
    return f"{100 * v:.0f}%" if isinstance(v, (int, float)) else "-"


def line(name: str, r: dict) -> str:
    win = " | ".join(str(r.get(f"per_{w}m_med", "-")) for w in WINDOWS)
    deliv = f"{pct(r.get('planned_delivered', ''))} / {pct(r.get('firm_delivered', ''))}" if r.get("planned_jumps") else "-"
    beh = f"{r['behind_med_s']} / {r['behind_p90_s']} / {r['behind_max_s']}" if "behind_med_s" in r else "-"
    brk = f"{r['mid_entry']} / {r['mid_leave']} / {r['hl_leave']}" if "mid_entry" in r else "0 / 0 / 0 by construction"
    return (f"| {name} | {r['nights']} | {r['jumps_per_min']:.2f} | {win} | {pct(r.get('yes_rate', ''))} | {deliv} | {pct(r.get('v2_seen', ''))} | "
            f"{pct(r.get('big_seen', ''))} | {beh} | {brk} | {r.get('flips', '-')} |")


def sim_row(nights: list, brain: str, s: core.Setting) -> dict:
    runs = [(n, core.run(n, ps.BRAINS[brain](), s)[0]) for n in nights]
    r = core.summarize(runs)
    r["flips"] = sum(len(x.hops) for _, x in runs)
    return r


def plan_row(nights: list, s: core.Setting, plan: plan_core.Plan, brain: str) -> dict:
    r = pp.evaluate(nights, s, plan, brain)
    r["flips"] = round(r["hops_per_min"] * r["minutes"])
    return r


def greedy_row(slates: list, pad: int, cap: float, rewind: bool) -> dict:
    """park_ceiling's greedy max-pace viewer on these slates at this padding, landing no further behind than `cap`."""
    keep = pc.LOAD_1_S, pc.RESCRUB_1_S
    if not rewind:
        pc.LOAD_1_S = pc.RESCRUB_1_S = 1e7          # no rewind load ever finishes: tune, pause and forward skips only
    try:
        runs = [pc.park_run(x, sticks=2, e=float(pad), shots=True, pick="short", tune_park=True, reach=cap) for x in slates]
    finally:
        pc.LOAD_1_S, pc.RESCRUB_1_S = keep
    out = pc.summarize(runs, 0.0)
    row = {"nights": out["nights"], "jumps_per_min": out["jumps_per_min"], "flips": sum(len(r["jumps"]) for r in runs)}
    for w in WINDOWS:
        row[f"per_{w}m_med"] = out[f"per_{w}m"][0]
    return row


def safe(r: dict, base: dict) -> bool:
    return r["mid_entry"] + r["mid_leave"] + r["hl_leave"] == 0 and (r["v2_seen"] or 0.0) >= (base["v2_seen"] or 0.0)


def served(r: dict) -> float:
    return r["jumps_per_min"] * (r["yes_rate"] or 0.0)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sweep", required=True, help="the park_sim sweep CSV the last pick was made on (sweep4.csv)")
    ap.add_argument("--grid", required=True, help="park_ceiling's error grid (error_grid_pause.csv)")
    ap.add_argument("--slates", nargs="+", default=["nba 2", "nba 4", "nba 8", "mnf+1", "mnf+3"])
    ap.add_argument("--errors", nargs="+", default=["today", "middle", "spec"], choices=sorted(pp.ERRORS))
    ap.add_argument("--bonus", nargs="+", default=[], help="the picked jump_bonus per error: today=0.5 middle=0.5 spec=0.5")
    ap.add_argument("--plan", action="append", default=[], help="key=value on park_plan.Plan (repeatable)")
    ap.add_argument("--repick", type=int, default=10, help="how many of the sweep's best safe rows are rerun under the cap")
    ap.add_argument("--cap", type=int, default=10, help="dev nights per league (10 = all of them)")
    ap.add_argument("--cache", default=None)
    ap.add_argument("--csv", default=None)
    a = ap.parse_args()
    cache = Path(a.cache) if a.cache else None
    bonus = {k: float(v) for k, v in (b.split("=", 1) for b in a.bonus)}
    base_plan = plan_core.Plan(**pp.parse_plan(a.plan))
    sweep = list(csv.DictReader(open(a.sweep)))
    grid = list(csv.DictReader(open(a.grid)))
    cap_s = core.Setting().late_cap
    out_rows = []

    def emit(slate: str, err: str, name: str, r: dict) -> None:
        print(line(name, r))
        out_rows.append({"slate": slate, "error": err, "row": name, **r})

    for slate in a.slates:
        nights = ps.slate_nights(slate, a.cap, cache)
        raw = pc.slates_for(slate, a.cap, cache)
        if not nights:
            continue
        for err in a.errors:
            sd = pp.ERRORS[err]
            timing = {"delay_sd": sd, **pp.ERR_FIXED}
            s_plan = pp.setting(err, False)      # (the any-shot rule is a what-if outside Rule #1: off)
            pl = plan_core.Planner()
            core.run(nights[0], ps.Foresight(), s_plan, t0=nights[0].span[0], t1=nights[0].span[0] + 1, planner=pl)
            pad = pl.G
            mins = sum((n.span[1] - n.span[0]) / 60.0 for n in nights)
            print(f"\n**{slate}, {err} (channel delay known to sd {sd} s, padding {pad} s), {len(nights)} dev nights, {mins:.0f} minutes"
                  f"{', SYNTHETIC slate' if '+' in slate else ''}. Simulated.**\n")
            print(HEAD)
            print("|" + "---|" * HEAD.count("|"))
            # the sweep's rows for this slate: its own error, or both of its errors when it has none for this one
            mine = [r for r in sweep if r["league"] == slate and r["brain"] == "hindsight" and abs(float(r["delay_sd"]) - sd) < 1e-9]
            src = mine or [r for r in sweep if r["league"] == slate and r["brain"] == "hindsight"]
            base_row = next(r for r in src if r["tag"] == "baseline_live")
            s_live = replace(park_pick.setting_of(base_row), **timing)
            live = sim_row(nights, "hindsight", s_live)
            emit(slate, err, "live-edge physics (as in the last sweep: dead balls only)", live)
            emit(slate, err, "live-edge physics, any-shot rule on", sim_row(nights, "hindsight", replace(s_live, shots=True)))
            # the last sweep's pick, as park_pick makes it on the sweep's own two nights
            picks = park_pick.picks(src)
            olds = [(k, v[1]) for k, v in sorted(picks.items())]
            cands = []
            for (_, _, d), row in olds:
                s_old = replace(park_pick.setting_of(row), **timing)
                tag = f"last sweep's pick ({row['tag']}{'' if mine else f', picked at delay sd {d} s'})"
                emit(slate, err, f"{tag}, as it was: no lateness cap, hindsight brain", sim_row(nights, "hindsight", s_old))
                capped = sim_row(nights, "hindsight", replace(s_old, late_cap=cap_s))
                emit(slate, err, f"{tag}, under the {cap_s:.0f} s cap, hindsight brain", capped)
                emit(slate, err, f"{tag}, under the {cap_s:.0f} s cap, foresight brain", sim_row(nights, "foresight", replace(s_old, late_cap=cap_s)))
                cands.append((f"{row['tag']}", capped))
            # re-pick under the cap: the sweep's best safe rows rerun on all the nights
            pool = sorted((r for r in src if r["mode"] == "park" and r["tag"] not in ("baseline_live", "default") and park_pick.breaks(r) == 0),
                          key=park_pick.served, reverse=True)[:a.repick]
            seen = {c[0] for c in cands}
            for r in pool:
                if r["tag"] not in seen:
                    seen.add(r["tag"])
                    cands.append((r["tag"], sim_row(nights, "hindsight", replace(park_pick.setting_of(r), late_cap=cap_s, **timing))))
            ok = [c for c in cands if safe(c[1], live)]
            if ok:
                tag, best = max(ok, key=lambda c: served(c[1]))
                emit(slate, err, f"re-picked under the cap ({tag}; {len(ok)} safe of {len(cands)} rerun), hindsight brain", best)
            else:
                print(f"| re-picked under the cap: none of the {len(cands)} rerun rows is safe on all {len(nights)} nights | | | | | | | | | | | | | | |")
            # the planner
            b = bonus.get(err, base_plan.jump_bonus)
            plan = replace(base_plan, jump_bonus=b)
            got = plan_row(nights, s_plan, plan, "hindsight")
            fore = plan_row(nights, s_plan, plan, "foresight")
            got["yes_rate_foresight_wants"] = fore["yes_rate"]
            emit(slate, err, f"PLANNER, jump_bonus {b:g} (yes-rate on the foresight brain's wants: {pct(fore['yes_rate'])})", got)
            # the ceiling
            cell = next((g for g in grid if g["slate"] == slate and g["sticks"] == "2" and g["any_shot"] == "1"
                         and abs(float(g["sd_s"]) - GRID_SD[err]) < 1e-9 and abs(float(g["k"]) - 4.0) < 1e-9), None)
            if cell:
                emit(slate, err, f"greedy ceiling, grid CSV (total sd {GRID_SD[err]} s, padding {cell['pad_s']} s, its {cell['nights']} nights, 315 s reach)",
                     {"nights": int(cell["nights"]), "jumps_per_min": float(cell["jumps_per_min"])})
            emit(slate, err, f"greedy ceiling on these nights, padding {pad} s, inside the {cap_s:.0f} s cap", greedy_row(raw, pad, cap_s, True))
            emit(slate, err, "the same without the rewind route (tune, pause and skips only, as the planner)", greedy_row(raw, pad, cap_s, False))
    if a.csv and out_rows:
        cols = []
        for r in out_rows:
            cols += [c for c in r if c not in cols]
        with open(a.csv, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, restval="")
            w.writeheader()
            w.writerows(out_rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
