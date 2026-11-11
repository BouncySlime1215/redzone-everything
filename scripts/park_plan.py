"""PARK-BRAIN: a planning controller for the two-stick show, run inside park_sim (its nights, physics, padding and counters).

    nice -n 19 python scripts/park_plan.py --slates "nba 4" "mnf+3" --errors today middle spec --bonus 0 0.5 1 2 --csv curve.csv
    --plan horizon=120 --plan depth=3       one Plan knob for this run (repeatable); --set key=value sets a park_sim.Setting knob
    --model park_unknown.npz                value the seconds the feed has not reported with a trained model (park_table.ModelBrain)
    --baseline                              also print live-edge physics under the same padding
    --report curve.csv                      print the trade-off curve of a CSV written by --csv, one table per slate and error

No TV, no adb, no engine, no network, no training. Dev (`fit`) nights only. A night runs in well under a second.

What it does. park_sim's rule set parks the hidden stick on the game the brain scores highest and jumps when it can. This
controller PLANS instead. Every `replan_s` seconds (and whenever a jump is due or the hidden stick comes up) it searches the
next `horizon` seconds of what the two sticks can do and takes the first step of the best plan:
    stay                            keep the screen game
    jump                            to the hidden stick, at a legal leave of the screen game the feed has reported
    hold                            the stick just left stays paused where the viewer left it (a later jump returns there)
    roll / skip                     play that stick on, or skip it forward in 15 s steps, and pause it before a later play
    tune                            put the hidden stick on another game: tune (3 s), play to a spot, one pause key
A plan is up to `depth` jumps (the best `beam` first jumps are searched one level deeper). It reads ONLY what the feed has
reported: nothing past the live edge + `feed_lead`. A second the feed has not reported is worth `unk_w` x the game's
score-and-clock leverage (the foresight brain's no-future view), or what a trained model says (`--model`).
When no jump in the reports is worth making it BANKS a landing: it assumes the next leave comes `exp_wait` past the reports
and puts the hidden stick where the first `bank_s` seconds after such a jump are worth most. That is never scheduled.

What a plan is worth: the simulator's dense value of every second it puts on screen (value2 grades, twice the must-sees,
live play; a graded play counts only when the picture was up 7 s before it, as the simulator counts it), plus `jump_bonus`
for each visit that shows live play (paid when it has, so a hop through dead air earns nothing), less `late_w` per second
on screen per second behind the stream's live edge. A plan holds only `depth` jumps, so the seconds after its last known
leave are worth `jump_bonus` per hoped-for cycle (`cycle_k` x the average spacing of landing spots per game in the feed so
far): that is what makes a jump now better than the same jump later, and a landing with a quick next leave better.
Never traded, whatever the bonus:
    the Rule #1 padding and no leave inside a high-leverage span: the planner only ASKS for a jump, park_sim's own tests
        (pad_k x the summed error from every edge, both ends reported by the feed) decide, and it asks only where they pass;
    the must-see guarantee: a must-see the feed has reported is worth MUST_W, more than any bonus or grade can add up to, so a
        plan that shows it always beats one that does not (it also overrides a committed jump);
    the lateness cap: no landing further behind the stream's live edge than Setting.late_cap (park_sim refuses it too);
    a visit that has shown no play is left only for a play that starts at least HOP_GAIN_S sooner (no dead-air ping-pong).
One thing it reads the way park_sim's own leave test does: whether a second is inside a high-leverage span, which is marked
from 7 s before the play's stamp. A game being close and late is known before the play, so this is not a look at the result.

The yes-rate for a planner. The audit's yes-rate (the brain wanted a game and its picture was up within 20 s) is kept: the
brain passed in is only asked what it wants. Added, "planned jump delivered": each time the plan's next jump is first
scheduled, or moves to another game or by more than 2 s, it is logged; it is delivered when a flip onto that game happens
within 2 s of the planned second with no Rule #1 break on it. `firm` is the same count over the jumps as they stood when
they came inside the commit window (`commit_s` ahead), which is the promise the app could show on screen.

Why: the rule-set controller found by random search delivered about a third of the jump pace the two sticks can supply,
because with one spare stick the result is decided by which game is parked BEFORE the want arrives; that is a planning
problem, and Nick wants many jumps, a yes-rate near 95% and zero mid-play cuts at the same time.

The planner itself (Plan, Tab, Planner) lives in redzone/noggin/park_plan.py, ONE copy for the app and the scripts; this
script keeps the command line, the three timing scenarios, the evaluation loop and the report, and re-exports every
name it used to define so `import park_plan` keeps working.
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from dataclasses import asdict, fields, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT.as_posix(), (ROOT / "scripts").as_posix()):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import park_ceiling as pc  # noqa: E402
import park_sim as ps  # noqa: E402
from redzone.noggin import park_plan as core  # noqa: E402
from redzone.noggin.park_plan import (  # noqa: E402,F401  (re-exported: other scripts and tools use them as park_plan.X)
    FAR, FRESH_BEHIND, HOLD, JOIN_SLACK, MOVE, MUST_W, ROLL, ROUTES, TUNE, Plan, Tab, tabs_for)

HOP_GAIN_S = ps.PARK_PARAMS.hop_gain_s     # a visit that has shown no play is left only for a play that starts this much sooner
# the three timing scenarios of the brief: how well the channel delay is known (sd, seconds); the other three parts are fixed
ERRORS = {"today": 3.5, "middle": 1.0, "spec": 0.2}
ERR_FIXED = {"pos_sd": 0.3, "stamp_sd": 0.3, "key_sd": 0.1, "pad_k": 4.0}


class Planner(core.Planner):
    """redzone.noggin.park_plan.Planner, unchanged, except that each search reads the skip step and the key-press time from
    park_ceiling's SKIP_STEP_S and PRESS_S as the script's planner used to. A caller that changes those two for a search (the
    local show runner's NoSkipPlanner sets SKIP_STEP_S to infinity to take the skip route away) still gets what it asked
    for. With them at their measured values this is exactly the package's planner."""

    def _search(self, t: int, S) -> None:
        prm = self.params
        if (prm.skip_step_s, prm.press_s) != (pc.SKIP_STEP_S, pc.PRESS_S):
            self.params = replace(prm, skip_step_s=pc.SKIP_STEP_S, press_s=pc.PRESS_S)
        super()._search(t, S)


# ---- running it ------------------------------------------------------------------------------------------------------

def setting(error: str, league_shots: bool, **kw) -> ps.Setting:
    """The planner's park_sim setting for one of the three timing scenarios. A landing may wait any time for its play (the
    plan prices the dead air), and the any-shot rule is on wherever the padding fits inside the 4 s window."""
    base = {"route": "tune", "pause_holds": True, "ping_pong": True, "land_wait_max": 999.0, "max_behind": pc.PAUSE_REACH_S,
            "delay_sd": ERRORS[error], "shots": league_shots, **ERR_FIXED}
    base.update(kw)
    return ps.Setting(**base)


def evaluate(nights: list, s: ps.Setting, plan: Plan, brain: str = "hindsight", unknown=None) -> dict:
    """One planner setting on these nights. The brain is only asked what it wants (the audit's yes-rate)."""
    runs, searches, t0 = [], 0, time.time()
    for night in nights:
        pl = Planner(plan, unknown)
        runs.append((night, ps.run(night, ps.BRAINS[brain](), s, planner=pl)[0]))
        searches += pl.searches
    row = ps.summarize(runs)
    row["searches"] = searches
    row["s_per_night"] = round((time.time() - t0) / max(1, len(nights)), 2)
    return row


DROP = 0.15          # the curve is read up to the point where graded value seen has fallen this far below its best


def report(path: str) -> None:
    """The trade-off curve as markdown, one table per slate and error. A row past the 15% fall in graded value is marked."""
    rows = list(csv.DictReader(open(path)))

    def f(r: dict, k: str) -> float:
        try:
            return float(r[k])
        except (KeyError, ValueError):
            return float("nan")

    def pc_(r: dict, k: str) -> str:
        v = f(r, k)
        return f"{100 * v:.0f}%" if v == v else "-"
    for slate, err in dict.fromkeys((r["slate"], r["error"]) for r in rows):
        part = [r for r in rows if (r["slate"], r["error"]) == (slate, err)]
        top = max(f(r, "v2_seen") for r in part if r["tag"].startswith("planner"))
        print(f"\n**{slate}, {err}: {part[0]['nights']} dev nights, {f(part[0], 'minutes'):.0f} minutes"
              f"{', SYNTHETIC slate' if '+' in slate else ''}. Simulated.**\n")
        print("| row | jumps/min | 5 / 10 / 20 / 30 / 45 / 60 min (median) | graded value seen | big plays seen | yes-rate (audit) | planned jump "
              "delivered (all / firm) | behind live s (median / p90) | mid-play joins / leaves / high-leverage leaves | flips | live share |")
        print("|---|---|---|---|---|---|---|---|---|---|---|")
        for r in part:
            past = r["tag"].startswith("planner") and f(r, "v2_seen") < (1.0 - DROP) * top
            win = " / ".join(r[f"per_{w}m_med"] for w in pc.WINDOWS_MIN)
            deliv = f"{pc_(r, 'planned_delivered')} / {pc_(r, 'firm_delivered')}" if r["tag"].startswith("planner") else "-"
            print(f"| {r['tag']}{' (past the 15% fall)' if past else ''} | {f(r, 'jumps_per_min'):.2f} | {win} | {pc_(r, 'v2_seen')} | "
                  f"{pc_(r, 'big_seen')} | {pc_(r, 'yes_rate')} | {deliv} | {f(r, 'behind_med_s'):.0f} / {f(r, 'behind_p90_s'):.0f} | "
                  f"{r['mid_entry']} / {r['mid_leave']} / {r['hl_leave']} | "
                  f"{round(f(r, 'hops_per_min') * f(r, 'minutes'))} | {pc_(r, 'live_share')} |")


def parse_plan(items: list[str]) -> dict:
    out, types = {}, {f.name: f.type for f in fields(Plan)}
    for it in items or ():
        k, v = it.split("=", 1)
        out[k] = int(v) if types[k] == "int" else float(v)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--slates", nargs="+", default=["nba 4"], help='Nick\'s slates by name: "nba 4" "mnf+3" (the mixed one is synthetic)')
    ap.add_argument("--errors", nargs="+", default=["spec"], choices=sorted(ERRORS))
    ap.add_argument("--bonus", nargs="+", type=float, default=[0.5], help="jump_bonus values (the trade-off curve)")
    ap.add_argument("--cap", type=int, default=10, help="dev nights per league (10 = all of them)")
    ap.add_argument("--brain", default="hindsight", choices=sorted(ps.BRAINS), help="whose wants the audit's yes-rate is counted on")
    ap.add_argument("--plan", action="append", default=[], help="key=value on Plan (repeatable)")
    ap.add_argument("--set", action="append", default=[], help="key=value on park_sim.Setting (repeatable)")
    ap.add_argument("--model", default=None, help="an .npz of trees (kaggle_park.py) that values the seconds the feed has not reported")
    ap.add_argument("--baseline", action="store_true", help="also run live-edge physics under the same padding")
    ap.add_argument("--cache", default=None, help="a folder OUTSIDE the repo for the per-game facts")
    ap.add_argument("--csv", default=None)
    ap.add_argument("--report", default=None, help="print the curve tables of a CSV written by --csv, and stop")
    a = ap.parse_args()
    if a.report:
        report(a.report)
        return 0
    cache = Path(a.cache) if a.cache else None
    base_plan = Plan(**parse_plan(a.plan))
    sets = ps.parse_sets(a.set)
    unknown = None
    if a.model:
        import park_table  # the model hook lives beside the tables it is trained on
        unknown = park_table.ModelBrain.load(a.model)
    w = fh = None
    for name in a.slates:
        nights = ps.slate_nights(name, a.cap, cache)
        if not nights:
            continue
        for err in a.errors:
            s = setting(err, True, **sets)
            todo = [("live-edge physics", None)] if a.baseline else []
            todo += [(f"planner bonus {b:g}", replace(base_plan, jump_bonus=b)) for b in a.bonus]
            for tag, plan in todo:
                if plan is None:
                    r = ps.evaluate(nights, a.brain, replace(s, mode="live"))
                else:
                    r = evaluate(nights, s, plan, a.brain, unknown)
                row = {"slate": name, "error": err, "tag": tag, "brain": a.brain,
                       **{k: getattr(s, k) for k in ("delay_sd", "pos_sd", "stamp_sd", "key_sd", "pad_k", "late_cap", "max_behind", "feed_lead", "shots")},
                       **(asdict(plan) if plan is not None else {f.name: "" for f in fields(Plan)}), **r}
                print(f"{name:6s} {err:6s} {tag:22s} {r['jumps_per_min']:5.2f}/min  yes {_pc(r['yes_rate'])}  delivered {_pc(r['planned_delivered'])}"
                      f"  value {_pc(r['v2_seen'])}  big {_pc(r['big_seen'])}  behind {r['behind_med_s']}/{r['behind_p90_s']}/{r['behind_max_s']}"
                      f"  breaks {r['mid_entry']}/{r['mid_leave']}/{r['hl_leave']}  nights {r['nights']}  {r.get('s_per_night', '')} s/night", flush=True)
                if a.csv:
                    if w is None:
                        fh = open(a.csv, "a", newline="")
                        w = csv.DictWriter(fh, fieldnames=list(row), extrasaction="ignore", restval="")
                        if fh.tell() == 0:
                            w.writeheader()
                    w.writerow(row)
                    fh.flush()
    if fh:
        fh.close()
    return 0


def _pc(v) -> str:
    return f"{100 * v:.0f}%" if isinstance(v, (int, float)) else "-"


if __name__ == "__main__":
    raise SystemExit(main())
