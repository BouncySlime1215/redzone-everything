"""PARK step 1: a fast simulator of the two-stick show on recorded nights, driven by the grader's own facts.

    nice -n 19 python scripts/park_sim.py --leagues nba --games 4 --cap 2 --brains hindsight foresight heuristic
    nice -n 19 python scripts/park_sim.py --leagues nba nfl --sweep 2000 --csv sweep.csv      # random settings, every row kept
    --set pos_err=3 --set ping_pong=false      one setting for this run (repeatable)

No TV, no adb, no engine, no network, dev (`fit`) nights only. One night under one setting runs in well under a second.

The show. Two sticks. The SCREEN stick shows a game some seconds behind the stream's live edge. The HIDDEN stick is being
loaded onto a game, or sits PARKED there: paused just before a play starts (`park_at=end`, one-play visits) or just after
a whistle (`park_at=start`). Because a parked stick is behind live, the play-by-play feed is ahead of its picture, so the
simulator may read each game's facts up to `feed_lead` seconds past the live edge and no further.
Each second a brain scores every game (any object with `score(g, c, t, on_screen, back)`); the game it scores above the
one on screen is what it WANTS. The controller then: jumps to the parked game (legal only when the screen game's believed
position is at least `guard` seconds inside a dead stretch and not in a high-leverage play, or with `leave_rule=low_leverage`
anywhere outside high leverage; and the parked position is at least `guard` inside a dead stretch); or re-parks the hidden
stick on the wanted game (costs `load_s`); or keeps the stick it just left paused where the viewer left it (`ping_pong`)
and skips its dead time forward (`skip_dead`).
`mode=live` is today's physics for comparison: both sticks at the live edge, the hidden one tuned to the wanted game, a
jump only when the screen game is dead now and the target is dead when its picture comes up.

Rule #1 is judged by the grader's facts at the TRUE positions, which differ from what the controller believes: every
park draws a position error (normal, sd 0.6 x `pos_err`, so "right within about 5 s" is sd 3) plus the fixed `delay_error`.
Counters: mid-play entries, mid-play leaves, high-leverage leaves (and, with the any-shot rule, entries and leaves in the
4 s walk after a shot, counted apart as the Nov 8 decision says).
`pause_holds=false` models "only scrub positions hold": a hidden stick cannot stop, it lands on a 15 s lattice and is held
in its dead stretch by one 15 s step back at a time.

yes-rate: of the seconds the brain wanted another game, the share where that game's picture was up within 20 s (the audit's
definition). Big plays: the audit's must-see moments (a score 5, a red-zone trip or turnover 3, others 1), seen when the
picture was on the game from 7 s before the moment; split by how it was seen (near live, parked behind live, a clip of a
play the feed had already reported).

Why: Nick wants many jumps, a yes-rate near 95% and zero mid-play cuts; the pace, the yes-rate and the Rule #1 counters of
every PARK setting have to be measurable in under a second so thousands of settings can be compared before anything is wired
into the live decider.

The core (Setting, Arr, Night, State, Stats, run, summarize and the counters) lives in redzone/noggin/park_core.py, ONE copy
for the app and the scripts; this script keeps the command line, night loading, the simulator brains and the sweep, and
re-exports every name it used to define so `import park_sim` keeps working.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
import time
from dataclasses import asdict, fields, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT.as_posix(), (ROOT / "scripts").as_posix()):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import park_ceiling as pc  # noqa: E402
from redzone.noggin.park_core import (  # noqa: E402,F401  (re-exported: other scripts and tools use them as park_sim.X)
    PARK_PARAMS, TUNE_TICKS, WHY, Arr, Night, ParkParams, Setting, State, Stats, delivered, run, served, summarize, yes_rate)

# the old module constants, kept under their old names for the scripts and tools that read them (values: park_core.ParkParams)
RUN_UP_S = PARK_PARAMS.run_up_s            # veto_audit.RUN_UP_S: how far ahead a brain's value looks
SEEN_LEAD_S = PARK_PARAMS.seen_lead_s      # veto_audit.PLAY_LEN_S: the picture must be up this long before a moment to have seen it
YES_S = PARK_PARAMS.yes_s                  # veto_audit.YES_S
PLAN_OK_S = PARK_PARAMS.plan_ok_s          # a planner's scheduled jump is delivered when it happens within this of the planned second
FEED_LEAD_S = PARK_PARAMS.feed_lead_s      # the feed runs this far ahead of the stream's live edge
LIVE_BEHIND_S = PARK_PARAMS.live_behind_s  # a big play seen this close to the live edge counts as seen live
LONG_STOP_S = PARK_PARAMS.long_stop_s      # a stoppage that still has this long to run may be left without having shown any play
SAME_BEHIND_S = PARK_PARAMS.same_behind_s  # a stick already behind live may skip forward to this close to the live edge
PAD_S = PARK_PARAMS.pad_s


# ---- brains: anything with score(g, c, t, on_screen, back) -> float ------------------------------------------------

class Hindsight:
    """Knows the future: the must-see value about to air on the game from picture position c (the audit's hindsight brain)."""
    name = "hindsight"

    def bind(self, arrs: list[Arr], s: Setting) -> None:
        self.A = arrs

    def score(self, g: int, c: int, t: int, on_screen: bool, back: int = 0) -> float:
        return self.A[g].ahead(c - back + (0 if on_screen else SEEN_LEAD_S), c + RUN_UP_S, c)


class Foresight:
    """No more than the app could know: only moments the feed has already reported (a stick behind live sees the feed ahead of
    its picture), plus a little of the score-and-clock leverage so it has a view when nothing is reported yet."""
    name = "foresight"

    def bind(self, arrs: list[Arr], s: Setting) -> None:
        self.A, self.lead = arrs, int(s.feed_lead)

    def score(self, g: int, c: int, t: int, on_screen: bool, back: int = 0) -> float:
        a = self.A[g]
        known = min(a.n - 1, t + self.lead)
        return a.ahead(c - back + (0 if on_screen else SEEN_LEAD_S), min(c + RUN_UP_S, known), c) + 0.2 * a.lev[min(max(c, 0), known)]


class Heuristic:
    """No future at all: the value2 leverage of the score and the clock, as the feed last reported it."""
    name = "heuristic"

    def bind(self, arrs: list[Arr], s: Setting) -> None:
        self.A, self.lead = arrs, int(s.feed_lead)

    def score(self, g: int, c: int, t: int, on_screen: bool, back: int = 0) -> float:
        a = self.A[g]
        return a.lev[min(a.n - 1, t + self.lead)]


BRAINS = {"hindsight": Hindsight, "foresight": Foresight, "heuristic": Heuristic}


def nights_for(league: str, n_games: int, cap: int, cache: Path | None) -> list[Night]:
    out = []
    for night in pc.load_nights(league, cap, cache):
        sl = pc.slate(night, n_games)
        if sl:
            out.append(Night(sl))
    return out


def slate_nights(name: str, cap: int, cache: Path | None) -> list[Night]:
    """Nick's slates by name: "nba 4", "mnf+3" (park_ceiling.slates_for; the mixed one is synthetic)."""
    return [Night(x) for x in pc.slates_for(name, cap, cache)]


def evaluate(nights: list[Night], brain_name: str, s: Setting) -> dict:
    """One setting under one brain. With a delay error, both signs are run and the worse arm (more Rule #1 breaks, then the
    lower yes-rate) is the row, as the audit does."""
    arms = [s] if not s.delay_error else [s, replace(s, delay_error=-s.delay_error)]
    rows = []
    for arm in arms:
        brain = BRAINS[brain_name]()
        rows.append(summarize([(n, run(n, brain, arm)[0]) for n in nights]))
    return max(rows, key=lambda r: (r["mid_entry"] + r["mid_leave"] + r["hl_leave"], -(r["yes_rate"] or 0.0)))


GRID = {
    "pause_holds": [True], "route": ["tune", "tune", "either", "rewind"], "load_s": [18.5, 25.0, 30.0], "resume_lead_s": [0.0, 1.0, 2.0],
    "pos_sd": [0.3], "delay_sd": [0.2, 0.5, 1.0, 2.0, 3.5], "stamp_sd": [0.3, 0.6], "key_sd": [0.1, 0.3], "pad_k": [3.0, 4.0],
    "feed_lead": [10.0, 20.0, 27.0],
    "min_dead": [4.0, 8.0, 12.0, 20.0], "max_behind": [90.0, 175.0, 300.0, 600.0], "ping_pong": [True, False],
    "min_stay": [0.0, 8.0, 15.0, 30.0], "min_live_s": [3.0, 3.0, 8.0], "land_wait_max": [8.0, 15.0, 30.0, 999.0], "margin": [0.0, 1.0, 3.0], "skip_dead": [True, False], "clips": [True, False],
    "leave_rule": ["dead", "low_leverage"], "park_at": ["end", "start"], "park_target": ["value", "recent"],
    "shots": [False, True], "prepark": [True, False], "delay_error": [0.0], "live_w": [0.0, 0.05, 0.15],
    "repark_margin": [0.0, 1.0, 3.0], "value": ["dense", "dense", "v1"], "lookahead_s": [0.0, 15.0, 30.0, 45.0],
    "bore_w": [0.0, 0.0, 0.02, 0.05, 0.1],
}


def draw(rng: random.Random, league: str) -> Setting:
    """One random setting from the grid (the guard is pad_k x the four error parts together; the any-shot rule needs basketball)."""
    d = {k: rng.choice(v) for k, v in GRID.items()}
    if league in ("nfl", "cfb", "nhl", "mlb"):
        d["shots"] = False
    return Setting(**d)


def parse_sets(items: list[str]) -> dict:
    out, types = {}, {f.name: f.type for f in fields(Setting)}
    for it in items or ():
        k, v = it.split("=", 1)
        ty = types[k]
        out[k] = v.lower() in ("1", "true", "yes", "on") if ty == "bool" else float(v) if ty == "float" else int(v) if ty == "int" else v
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--leagues", nargs="+", default=["nba"])
    ap.add_argument("--games", nargs="+", type=int, default=[4])
    ap.add_argument("--slates", nargs="+", default=None, help='Nick\'s slates by name instead of --leagues/--games: "nba 4" "mnf+3"')
    ap.add_argument("--cap", type=int, default=2, help="dev nights per league")
    ap.add_argument("--brains", nargs="+", default=["hindsight", "foresight"], choices=sorted(BRAINS))
    ap.add_argument("--set", action="append", default=[], help="key=value on Setting (repeatable)")
    ap.add_argument("--sweep", type=int, default=0, help="this many random settings from GRID per league and game count")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--cache", default=None, help="a folder OUTSIDE the repo for the per-game facts")
    ap.add_argument("--csv", default=None)
    a = ap.parse_args()
    fixed = parse_sets(a.set)          # in a sweep these stay fixed and the other knobs are drawn
    base = Setting(**fixed)
    names = [f.name for f in fields(Setting)]
    fh = w = None
    rng = random.Random(a.seed)
    cache = Path(a.cache) if a.cache else None
    jobs = [(x, 0, slate_nights(x, a.cap, cache)) for x in a.slates] if a.slates else \
        [(lg, ng, nights_for(lg, ng, a.cap, cache)) for lg in a.leagues for ng in a.games]
    for lg, ng, nights in jobs:
        for _ in (0,):
            if not nights:
                continue
            ng = ng or len(nights[0].games)
            todo = [("baseline_live", replace(base, mode="live")), ("base", base)] if not a.sweep else \
                [("baseline_live", replace(Setting(mode="live"), **fixed)), ("default", replace(Setting(), **fixed))] \
                + [(f"s{i}", replace(draw(rng, lg), **fixed)) for i in range(a.sweep)]
            for tag, s in todo:
                for bn in a.brains:
                    t_start = time.time()
                    row = {"league": lg, "games": ng, "brain": bn, "tag": tag, **asdict(s), **evaluate(nights, bn, s)}
                    row["run_s"] = round(time.time() - t_start, 3)
                    if a.csv:
                        if w is None:
                            fh = open(a.csv, "a", newline="")
                            w = csv.DictWriter(fh, fieldnames=list(row))
                            if fh.tell() == 0:
                                w.writeheader()
                        w.writerow(row)
                        fh.flush()
                    if not a.sweep:
                        print(json.dumps({k: v for k, v in row.items() if k not in names or k in ("mode",)}))
    if fh:
        fh.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

