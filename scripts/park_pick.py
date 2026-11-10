"""Pick the best safe PARK setting per slate from a park_sim sweep, print it beside today's physics, then vary one thing at a time.

    python scripts/park_pick.py sweep4.csv --cache <dir> [--what-if "nba 4" "mnf+3"] [--csv what_if.csv]

The pick, per slate, brain and error scenario (the `delay_sd` the sweep was run at): among rows with every Rule #1 counter
at zero and at least as much graded value on screen as live-edge physics shows on the same nights, the row with the most
SERVED pace (jumps a minute times the yes-rate: pace that was asked for and delivered). The row with the most jumps is
printed under it. A jump is a flip whose visit showed live play.
`--what-if` reruns the picked setting of those slates (hindsight brain) with one knob changed at a time: a failed or slow
load, a wrong delay, the pause reach, the minimum stay, the banner lead, a shorter feed lead, the scrub-only fallback, the
rewind route, ping-pong, the any-shot rule. Markdown tables on stdout, numbers only. No TV, no network, dev nights only.

Why: Nick asked for the best safe setting beside today's baseline in one format, and the brief's considerations (a slow or
failed load, a wrong delay, how far behind live, the cost of a minimum stay) are to be settled with numbers, not opinion.
"""
from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import fields, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT.as_posix(), (ROOT / "scripts").as_posix()):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import park_sim as ps  # noqa: E402

WINDOWS = (5, 10, 20, 30, 45, 60)
HEAD = ("| slate | brain | error | row | jumps/min | " + " | ".join(f"{w} min" for w in WINDOWS) + " | yes-rate | yes ceiling | asks s/min | big plays seen "
        "(live / parked / clip) | graded value seen | behind live s (median / p90) | live share | ads share | mid-play joins / leaves / "
        "high-leverage leaves | walk joins / leaves | visit s |")
VARIANTS = (("as picked", {}), ("a load fails 1 time in 5", {"load_fail_p": 0.2}), ("every load 10 s slower", {"load_slow_s": 10.0}),
            ("true delay 3 s off, unknown to the app", {"delay_error": 3.0}), ("pause reach 175 s", {"max_behind": 175.0}),
            ("pause reach 300 s", {"max_behind": 300.0}), ("pause reach 600 s", {"max_behind": 600.0}), ("minimum stay 0 s", {"min_stay": 0.0}),
            ("minimum stay 15 s", {"min_stay": 15.0}), ("minimum stay 30 s", {"min_stay": 30.0}), ("banner lead 0 s", {"resume_lead_s": 0.0}),
            ("banner lead 2 s", {"resume_lead_s": 2.0}), ("feed lead 10 s", {"feed_lead": 10.0}), ("feed lead 27 s", {"feed_lead": 27.0}),
            ("only scrub positions hold (fallback)", {"pause_holds": False}), ("every park a rewind", {"route": "rewind"}),
            ("ping-pong on", {"ping_pong": True}), ("ping-pong off", {"ping_pong": False}), ("any-shot rule on", {"shots": True}),
            ("any-shot rule off", {"shots": False}), ("restless brain off (bore 0)", {"bore_w": 0.0}), ("restless brain 0.1", {"bore_w": 0.1}))


def num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def breaks(r: dict) -> int:
    return int(num(r["mid_entry"]) + num(r["mid_leave"]) + num(r["hl_leave"]))


def served(r: dict) -> float:
    y = num(r["yes_rate"])
    return num(r["jumps_per_min"]) * (y if y == y else 0.0)


def pct(v) -> str:
    x = num(v)
    return f"{100 * x:.0f}%" if x == x else "-"


def line(slate: str, brain: str, err: str, name: str, r: dict) -> str:
    win = " | ".join(f"{r[f'per_{w}m_med']}/{r[f'per_{w}m_p10']}/{r[f'per_{w}m_max']}" for w in WINDOWS)
    return (f"| {slate} | {brain} | {err} | {name} | {num(r['jumps_per_min']):.2f} | {win} | {pct(r['yes_rate'])} | {pct(r['yes_ceiling'])} | "
            f"{r['wanted_s_per_min']} | {pct(r['big_seen'])} ({pct(r['big_live'])} / {pct(r['big_parked'])} / {pct(r['big_clip'])}) | {pct(r['v2_seen'])} | "
            f"{r['behind_med_s']} / {r['behind_p90_s']} | {r['live_share']} | {r['commercial_share']} | {r['mid_entry']} / {r['mid_leave']} / {r['hl_leave']} | "
            f"{r['walk_entry']} / {r['walk_leave']} | {r['visit_med_s']} |")


def setting_of(r: dict) -> ps.Setting:
    kw = {}
    for f in fields(ps.Setting):
        v = r[f.name]
        kw[f.name] = (v == "True") if f.type == "bool" else float(v) if f.type == "float" else int(float(v)) if f.type == "int" else v
    return ps.Setting(**kw)


def picks(rows: list[dict]) -> dict:
    """{(slate, brain, delay_sd): (baseline row, served-pace pick, most-jumps pick, rows, safe rows)}."""
    out = {}
    keys = sorted({(r["league"], r["brain"], r["delay_sd"]) for r in rows})
    for slate, brain, d in keys:
        rs = [r for r in rows if (r["league"], r["brain"], r["delay_sd"]) == (slate, brain, d)]
        base = next((r for r in rs if r["tag"] == "baseline_live"), None)
        pool = [r for r in rs if r["mode"] == "park" and r["tag"] not in ("baseline_live", "default")]
        safe = [r for r in pool if breaks(r) == 0 and (base is None or num(r["v2_seen"]) >= num(base["v2_seen"]))]
        if safe:
            out[(slate, brain, d)] = (base, max(safe, key=served), max(safe, key=lambda r: num(r["jumps_per_min"])), len(pool), len(safe))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("csv")
    ap.add_argument("--what-if", nargs="*", default=[], help="slates whose picked setting is rerun with one knob changed at a time")
    ap.add_argument("--cap", type=int, default=2)
    ap.add_argument("--cache", default=None)
    ap.add_argument("--out", default=None, help="CSV for the what-if rows")
    a = ap.parse_args()
    rows = list(csv.DictReader(open(a.csv)))
    got = picks(rows)
    print(HEAD)
    print("|" + "---|" * HEAD.count("|"))
    for (slate, brain, d), (base, best, most, n_pool, n_safe) in got.items():
        err = f"delay doubt {d} s"
        if base:
            print(line(slate, brain, err, "live-edge physics", base))
        print(line(slate, brain, err, f"best served pace ({best['tag']}; {n_safe} safe of {n_pool})", best))
        if most["tag"] != best["tag"]:
            print(line(slate, brain, err, f"most jumps ({most['tag']})", most))
    print()
    for (slate, brain, d), (_, best, _, _, _) in got.items():
        print(f"{slate} | {brain} | delay doubt {d} s | {best['tag']}: " + " ".join(f"{f.name}={best[f.name]}" for f in fields(ps.Setting)
                                                                                 if str(best[f.name]) != str(getattr(ps.Setting(), f.name))))
    out_rows = []
    for slate in a.what_if:
        nights = ps.slate_nights(slate, a.cap, Path(a.cache) if a.cache else None)
        for (sl, brain, d), (_, best, _, _, _) in got.items():
            if sl != slate or brain != "hindsight":
                continue
            s0 = setting_of(best)
            print(f"\n**What if, {slate}, delay doubt {d} s, from {best['tag']}** (hindsight brain)\n")
            print(HEAD.replace("| slate | brain | error | row |", "| change |"))
            print("|" + "---|" * (HEAD.count("|") - 3))
            for name, kw in VARIANTS:
                if any(getattr(s0, k) == v for k, v in kw.items()) and kw:
                    continue
                r = ps.evaluate(nights, "hindsight", replace(s0, **kw))
                print(line(slate, brain, d, name, {"tag": name, **r}).replace(f"| {slate} | {brain} | {d} | ", "| "))
                out_rows.append({"slate": slate, "delay_sd": d, "from": best["tag"], "change": name, **r})
    if a.out and out_rows:
        with open(a.out, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(out_rows[0]))
            w.writeheader()
            w.writerows(out_rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
