"""One table per slate and timing scenario: two sticks as simulated until now, two sticks on the honest feed, and Chrome tabs.

    nice -n 19 python scripts/park_profiles.py --cache <dir> --csv park_profiles.csv
    --slates "nba 2" "nba 4" "nba 8" "mnf+1" "mnf+3"   --errors today middle spec   --delays 0 45 90   --streams 2 4 8
    --open-wait 26      an open stoppage is trusted only 26 s behind the feed (the grader's own bound) instead of at once
    --cap 4             fewer recorded nights per league (quicker)
    --hand-made         no archive: one typed night, to show the command runs. Its numbers mean nothing.

No TV, no stick, no browser, no engine, no network, no training. Dev (`fit`) nights only. Markdown on stdout, every row with
every column in the CSV. Every number is SIMULATED; the step times fed in are measured or assumed as park_sim.DEVICES says.

WHICH PLANNER. Every row runs scripts/park_plan_n.py inside scripts/park_sim_n.py: a script-side copy of the simulator
and planner as they stood on Nov 10, plus the new settings. It is NOT the planner the app runs today
(redzone/noggin/park_plan.py, reworked since for the app's controller). So the rows compare the picture sources with each
other under one planner; they do not say what the app's controller would do.

Rows, all by park_plan_n.Planner at one jump_bonus, on the same nights, with the hindsight brain's wants for the yes-rate:
    sticks, lead (old)       the two-stick show as the Nov 10 simulator ran it: every step instant, and the end of each dead
                             ball read `feed_lead` (27 s) ahead of the stream. This is the optimistic row.
    sticks, rows (honest)    the same two sticks with their measured step times, and a restart known only once a later play
                             row whose game clock has moved has been reported.
    chrome, rows, ...        browser tabs as streams (2 / 4 / 8 of them), a park is one forward seek and a pause, on the
                             honest feed, with the whole show 0 / 45 / 90 s behind the stream's live edge. Further behind,
                             more of what is shown is already complete in the feed. The 145 s lateness cap stays hard.
Columns: jumps a minute; jumps in 5 / 10 / 20 / 30 / 45 / 60 minutes (median window); yes-rate (the audit's: the brain wanted
a game and its picture was up within 20 s); planned jumps delivered; graded value seen; big plays seen; seconds behind the
stream's live edge (median / p90); Rule #1 counters judged by the grader's facts at the TRUE positions.

Why: the simulator's pace was about twice what a row-by-row replay of the real feed gave, because it read restarts the feed
had not reported, and Nick's Chrome idea removes the two sticks' three limits (one spare stream, a 2 s jump, no fast skip
ahead). The coordinator needs one command that shows, on the recorded nights, what is left of the pace once the feed is
honest and what Chrome tabs and a show delay give back.
"""
from __future__ import annotations

import argparse
import csv
import random
import sys
from dataclasses import asdict, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT.as_posix(), (ROOT / "scripts").as_posix()):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import park_plan_n as pp  # noqa: E402
import park_sim_n as ps  # noqa: E402

WINDOWS = (5, 10, 20, 30, 45, 60)
SLATES = ("nba 2", "nba 4", "nba 8", "mnf+1", "mnf+3")
HEAD = ("| row | padding s | jumps/min | " + " | ".join(f"{w} min" for w in WINDOWS) + " | yes-rate | planned jumps delivered | graded value seen | "
        "big plays seen | behind live s (median / p90) | mid-play joins / mid-play leaves / high-leverage leaves | s per night |")


def pct(v) -> str:
    return f"{100 * v:.0f}%" if isinstance(v, (int, float)) else "-"


def line(r: dict) -> str:
    win = " | ".join(str(r.get(f"per_{w}m_med", "-")) for w in WINDOWS)
    return (f"| {r['row']} | {r['padding_s']} | {r['jumps_per_min']:.2f} | {win} | {pct(r['yes_rate'])} | {pct(r['planned_delivered'])} | {pct(r['v2_seen'])} | "
            f"{pct(r['big_seen'])} | {r['behind_med_s']} / {r['behind_p90_s']} | {r['mid_entry']} / {r['mid_leave']} / {r['hl_leave']} | {r['s_per_night']} |")


def settings(err: str, delays: tuple, streams: tuple, open_wait: float, extra: dict | None = None) -> list[tuple[str, ps.Setting]]:
    """Every row's park_sim_n.Setting. The old row is park_plan_n.setting untouched; `extra` goes on the others."""
    honest = {"restart_known": "rows", "open_wait": open_wait, **(extra or {})}
    out = [("sticks, lead (old)", pp.setting(err, True)),
           ("sticks, rows (honest)", pp.setting(err, True, **{**ps.profile("sticks"), **honest}))]
    for d in delays:
        for n in streams:
            out.append((f"chrome, rows, show delay {d:g} s, {n} streams", pp.setting(err, True, **{**ps.profile("chrome", streams=n, show_delay=float(d)), **honest})))
    return out


def padding_of(night: ps.Night, s: ps.Setting) -> int:
    pl = pp.Planner()
    ps.run(night, ps.Foresight(), s, t0=night.span[0], t1=night.span[0] + 1, planner=pl)
    return pl.G


def table(slate: str, nights: list, err: str, *, delays: tuple = (0, 45, 90), streams: tuple = (2, 4, 8), open_wait: float = 0.0,
          bonus: float = 0.5, brain: str = "hindsight", plan: pp.Plan | None = None, extra: dict | None = None, note: str = "") -> list[dict]:
    """Print one table and return its rows (each with the Setting it ran under)."""
    plan = replace(plan or pp.Plan(), jump_bonus=bonus)
    mins = sum((n.span[1] - n.span[0]) / 60.0 for n in nights)
    print(f"\n**{slate}, {err} (channel delay known to sd {pp.ERRORS[err]} s), {len(nights)} nights, {mins:.0f} minutes"
          f"{', SYNTHETIC slate' if '+' in slate else ''}{note}. jump_bonus {bonus:g}, open_wait {open_wait:g} s. Simulated.**\n")
    print(HEAD)
    print("|" + "---|" * (HEAD.count("|") - 1))
    out = []
    for name, s in settings(err, delays, streams, open_wait, extra):
        r = pp.evaluate(nights, s, plan, brain)
        r = {"slate": slate, "error": err, "row": name, "padding_s": padding_of(nights[0], s), **r, **{f"set_{k}": v for k, v in asdict(s).items()}}
        print(line(r), flush=True)
        out.append(r)
    return out


def hand_made(games: int = 4, minutes: int = 40, seed: int = 11) -> ps.Night:
    """A typed night for `--hand-made`: live stretches and dead balls of random lengths, a shot row 6 s before the whistle of
    each longer stretch, a row at every whistle. NOT a recorded night."""
    rng, t0, out = random.Random(seed), 1000, []
    for g in range(games):
        t, live, shots, v2, big = t0 + rng.randrange(0, 20), [], [], [], []
        end = t0 + minutes * 60
        while t < end - 60:
            d = rng.choice([6, 9, 14, 20, 24, 35])
            a, b = t, min(end - 30, t + d)
            live.append((a, b))
            if d >= 14:
                shots.append(b - 6)
                if rng.random() < 0.4:
                    v2.append((b - 6, rng.choice([1.0, 2.0, 3.0]), 0.0))
                if rng.random() < 0.04:
                    big.append((b - 6, 5.0))
            t = b + rng.choice([3, 5, 8, 12, 30, 45, 70])
        dead = [(x, y) for x, y in zip([t0] + [b for _, b in live], [a for a, _ in live] + [end]) if y > x]
        stamps = sorted(set(shots) | {b for _, b in live} | {t0, end})
        lev = [(w, 1.0, 5, 2880.0 - sum(max(0, min(w, b) - a) for a, b in live)) for w in stamps]
        out.append({"gid": f"typed{g}", "t0": t0, "t1": end, "dead": dead, "brk": [False] * len(dead), "shots": shots, "big": big, "hl": [],
                    "lev": lev, "v2": v2})
    return ps.Night(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--slates", nargs="+", default=list(SLATES), help='Nick\'s slates by name: "nba 4" "mnf+3" (the mixed one is synthetic)')
    ap.add_argument("--errors", nargs="+", default=["today", "middle", "spec"], choices=sorted(pp.ERRORS))
    ap.add_argument("--delays", nargs="+", type=float, default=[0, 45, 90], help="show delays for the chrome rows, seconds")
    ap.add_argument("--streams", nargs="+", type=int, default=[2, 4, 8], help="stream counts for the chrome rows")
    ap.add_argument("--open-wait", type=float, default=0.0, help="seconds behind the feed before an open stoppage is believed (26 = the grader's bound)")
    ap.add_argument("--bonus", type=float, default=0.5, help="the planner's jump_bonus, the same for every row")
    ap.add_argument("--brain", default="hindsight", choices=sorted(ps.BRAINS), help="whose wants the yes-rate is counted on")
    ap.add_argument("--plan", action="append", default=[], help="key=value on park_plan.Plan (repeatable)")
    ap.add_argument("--set", action="append", default=[], help="key=value on park_sim.Setting for every row but the old one (repeatable)")
    ap.add_argument("--cap", type=int, default=10, help="dev nights per league (10 = all of them)")
    ap.add_argument("--cache", default=None, help="a folder OUTSIDE the repo for the per-game facts")
    ap.add_argument("--csv", default=None)
    ap.add_argument("--hand-made", action="store_true", help="run on one typed night instead of the archive (proves the command; the numbers mean nothing)")
    a = ap.parse_args()
    cache = Path(a.cache) if a.cache else None
    plan, extra = pp.Plan(**pp.parse_plan(a.plan)), ps.parse_sets(a.set)
    rows = []
    jobs = [("hand-made 4", [hand_made()], ", ONE TYPED NIGHT, NOT A RECORDED ONE: the numbers mean nothing")] if a.hand_made else \
        [(name, ps.slate_nights(name, a.cap, cache), "") for name in a.slates]
    for name, nights, note in jobs:
        if not nights:
            print(f"\n**{name}: no dev night has this slate.**")
            continue
        for err in a.errors:
            rows += table(name, nights, err, delays=tuple(a.delays), streams=tuple(a.streams), open_wait=a.open_wait, bonus=a.bonus,
                          brain=a.brain, plan=plan, extra=extra, note=note)
    if a.csv and rows:
        cols = []
        for r in rows:
            cols += [c for c in r if c not in cols]
        with open(a.csv, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, restval="")
            w.writeheader()
            w.writerows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
