"""PARK step 5: the training table. What each legal action is worth, by the hindsight planner, on what is really achievable.

    nice -n 19 python scripts/park_table.py --leagues nba --games 4 --cap 2 --every 20 --csv park_table_nba.csv
    --set pos_err=5 --set guard=10      the PARK setting the table is built under (park_sim.Setting keys)

No TV, no adb, no engine, no network, no training. Dev (`fit`) nights only.

How a row is made. The night is played by park_sim under the hindsight brain (the behaviour policy). Every `--every`
seconds the state is frozen and each legal action is tried from a copy of it:
    stay            keep the screen game and leave the hidden stick alone for `--hold` seconds
    park:<game>     move the hidden stick to that game now (costs the load), no jump for `--hold` seconds
    jump            jump to the game the hidden stick holds as soon as it is legal
after which the hindsight brain plays on. The action's label `q_value` is the brain value the screen then shows over the
next `--horizon` seconds (value2 grades, twice the must-sees, live play), `q_must` the must-see value alone, `q_jumps` the
jumps made, and `q_rule1` any mid-play entry, mid-play leave or high-leverage leave in the horizon (a model must never be
rewarded for those). So the label is what was achievable with the measured load, flip and position error, not what an
all-seeing viewer with instant sticks would get.

The features are only what the live app could know at that second: each game's score-and-clock leverage, margin and time
left as the feed last reported them, whether its picture is at a dead ball, how long that dead ball has run and how long
the feed says it still runs, the brain value the feed has already reported ahead of the picture, seconds of live play the
feed has reported ahead, and how far behind live the picture is. A stick that sits behind live gets more of these for free,
which is the point of parking. Columns are prefixed s_ (screen game), h_ (hidden stick's game), a_ (the action's game).

`--landings out.csv` writes the second table (no rollouts, so it is quick): per decision second, every LEGAL LANDING the two
sticks could make next, one row each. A landing is a dead stretch (or, with the any-shot rule, the walk after a shot) of
another game that a stick can be parked on inside the reach. Per row: `wait_s` until the jump could go (the later of the
stick being set up there and the screen game's next legal leave), `visit_s` from the landing to that game's next legal
leave, the `value`, must-see value and live seconds that visit shows, the value the screen game shows over the same span
if the viewer stays (`stay_value`), and what of it the feed had already reported at the decision (`value_known`,
`visit_known`). `best` marks the landing with the highest value gained per second of screen time: the pick to learn.

`ModelBrain` wraps any function of those features as a park_sim brain, so a trained model is scored by the same simulator.

Why: the AI has to learn "which game to park next" and "jump now or wait" on what the two sticks can really do; a label
from an unconstrained hindsight view would teach it to ask for things the TV cannot deliver.
"""
from __future__ import annotations

import argparse
import bisect
import copy
import csv
import math
import random
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT.as_posix(), (ROOT / "scripts").as_posix()):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import park_sim as ps  # noqa: E402

FEATURES = ("role", "in_progress", "behind", "lev", "margin", "left_s", "dead_now", "since", "left", "known_s", "val_known", "live_known")
LIVE_LOOK_S = 30


def game_features(A: list, S, s: ps.Setting, t: int, g: int) -> dict:
    """What the app could know about game g at second t (g < 0: no such game, every feature -1)."""
    if g < 0:
        return {k: -1.0 for k in FEATURES}
    x = A[g]
    ready = S.hg == g and t >= S.h_ready
    if g == S.sg:
        role, c = 1, t - S.sb + S.se
    elif g == S.hg:
        role, c = (2 if ready else 3), t - S.hb + S.he
    else:
        role, c = 0, t - s.min_behind
    known = min(x.n - 1, t + int(s.feed_lead))
    ci = min(x.n - 1, max(0, int(c)))
    ck = min(ci, known)
    seen = ci <= known
    return {
        "role": float(role), "in_progress": float(x.a0 <= t <= x.a1), "behind": float(t - ci),
        "lev": x.lev[ck], "margin": float(x.margin[ck]), "left_s": float(x.left_s[ck]),
        "dead_now": float(x.legal[ci]) if seen else -1.0,
        "since": float(min(x.since[ci], 300)) if seen else -1.0,
        "left": float(min(x.left[ci], known - ci)) if seen else -1.0,
        "known_s": float(known - ci),
        "val_known": x.ahead(ci, min(ci + ps.RUN_UP_S, known), ci),
        "live_known": float(sum(1 for i in range(ci + 1, min(ci + LIVE_LOOK_S, known) + 1) if not x.legal[i])),
    }


def clone(S):
    c = copy.copy(S)
    c.rng = random.Random()
    c.rng.setstate(S.rng.getstate())
    return c


def actions(A: list, S, s: ps.Setting, t: int) -> list[tuple[str, int]]:
    out = [("stay", S.sg)]
    if S.hg >= 0:
        out.append(("jump", S.hg))
    out += [("park", g) for g in range(len(A)) if g not in (S.sg, S.hg) and A[g].a0 <= t <= A[g].a1]
    return out


def table(night: ps.Night, s: ps.Setting, *, every: int, hold: int, horizon: int):
    """Yield one dict per (decision second, legal action)."""
    brain = ps.Hindsight()
    A = night.arrs(s.shots, s.value, s.live_w)
    a, b = night.span
    st, S = ps.run(night, brain, s, t0=a, t1=a + every)
    t = a + every
    while t + horizon < b:
        sf, hf = game_features(A, S, s, t, S.sg), game_features(A, S, s, t, S.hg)
        acts = actions(A, S, s, t)
        rows = []
        for kind, g in acts:
            r, _ = ps.run(night, brain, s, t0=t, t1=t + horizon, state=clone(S), stats=ps.Stats(), force=(kind, g, t + hold))
            rows.append({"t": t - a, "action": kind, "n_actions": len(acts), "on_screen_s": float(t - S.s_land),
                         "games_live": float(sum(1 for x in A if x.a0 <= t <= x.a1)),
                         **{f"s_{k}": v for k, v in sf.items()}, **{f"h_{k}": v for k, v in hf.items()},
                         **{f"a_{k}": v for k, v in game_features(A, S, s, t, g).items()},
                         "q_value": round(r.brain_value, 3), "q_must": round(sum(r.seen.values()), 3), "q_jumps": len(r.jumps),
                         "q_rule1": r.mid_entry + r.mid_leave + r.hl_leave})
        top = max(x["q_value"] for x in rows)
        for x in rows:
            x["q_regret"] = round(top - x["q_value"], 3)
            yield x
        st, S = ps.run(night, brain, s, t0=t, t1=t + every, state=S, stats=st)
        t += every


def guard_of(s: ps.Setting) -> int:
    """The padding park_sim.run keeps from every edge under this setting (the same arithmetic)."""
    sd = 0.6 * s.pos_err if s.pos_sd < 0 else s.pos_sd
    if s.pad_k > 0:
        return max(1, int(round(s.pad_k * math.sqrt(sd ** 2 + s.delay_sd ** 2 + s.stamp_sd ** 2 + s.key_sd ** 2))))
    return int(round(s.pos_err if s.guard < 0 else s.guard))


def landings(night: ps.Night, s: ps.Setting, *, every: int, per_game: int = 4):
    """Yield one dict per (decision second, legal landing). The night is played by the hindsight brain; nothing is tried."""
    brain = ps.Hindsight()
    A = night.arrs(s.shots, s.value, s.live_w)
    a, b = night.span
    guard, flip = guard_of(s), max(1, int(round(s.flip)))
    lattice = 0 if s.pause_holds else int(ps.pc.SKIP_STEP_S)
    need = max(int(s.min_dead), 2 * guard + lattice)
    low = s.leave_rule == "low_leverage"
    st, S = ps.run(night, brain, s, t0=a, t1=a + every)
    t = a + every
    while t + every < b:
        xs = A[S.sg]
        known = min(night.n - 1, t + int(s.feed_lead))
        s_bel = min(night.n - 1, max(0, int(t - S.sb + S.se)))
        leave_wait = xs.leave_in(guard, low)[s_bel]
        rows = []
        for g, x in enumerate(A):
            if g == S.sg or not (x.a0 <= t <= x.a1):
                continue
            held = g == S.hg
            cur = int(t - S.hb + S.he) if held else None
            load = s.load_s + s.load_slow_s + (0 if lattice else ps.pc.SKIP_STEP_S / 2.0)
            lo, hi = (max(cur, t - int(s.max_behind)), t - ps.SAME_BEHIND_S) if held else (int(t + load - s.max_behind), int(t + load - s.min_behind))
            j = bisect.bisect_right(x.ends, hi + guard)
            n_out = 0
            for k in range(j - 1, -1, -1):
                p = x.ends[k] - guard if s.park_at == "end" else x.starts[k] + guard
                if p < lo or n_out >= per_game:
                    break
                if x.ends[k] - x.starts[k] < need or x.ends[k] > known or p > hi:
                    continue
                if held:
                    d = max(0, p - cur)
                    ready = 0.0 if (d == 0 and t >= S.h_ready) else max(0.0, S.h_ready - t) + 2.0 + ps.pc.PRESS_S * math.floor(d / ps.pc.SKIP_STEP_S) + d % ps.pc.SKIP_STEP_S
                else:
                    ready = load
                wait = max(ready, float(leave_wait))
                end = x.ends[k]                                   # the play starts here
                out = end + x.leave_in(guard, low)[min(night.n - 1, end)]   # and this is the next legal leave after it
                out = min(out, night.n - 1)
                visit = out - p
                span_end = min(night.n - 1, s_bel + int(wait) + flip + visit)
                value = x.cumv[out + 1] - x.cumv[p + 1]
                rows.append({"t": t - a, "game": g, "held": float(held), "behind_s": float(t - p), "ready_s": round(ready, 1),
                             "leave_wait_s": float(leave_wait), "wait_s": round(wait, 1), "visit_s": float(visit),
                             "dead_before_play_s": float(end - p), "value": round(value, 3),
                             "must": round(sum(v for i, v in x.mom.items() if p < i <= out), 3),
                             "live_s": float(sum(1 for i in range(p, out) if not x.dead[i])),
                             "stay_value": round(xs.cumv[span_end + 1] - xs.cumv[s_bel + 1], 3),
                             "value_known": round(x.cumv[min(out, known) + 1] - x.cumv[p + 1], 3), "visit_known": float(out <= known),
                             "lev": x.lev[min(p, known)], "margin": float(x.margin[min(p, known)]), "left_s": float(x.left_s[min(p, known)]),
                             "s_lev": xs.lev[min(s_bel, known)], "s_margin": float(xs.margin[min(s_bel, known)]),
                             "s_left_s": float(xs.left_s[min(s_bel, known)]), "s_on_screen_s": float(t - S.s_land),
                             "s_behind_s": float(t - s_bel), "games_live": float(sum(1 for y in A if y.a0 <= t <= y.a1))})
                n_out += 1
        for r in rows:
            r["gain_per_s"] = round((r["value"] - r["stay_value"]) / max(1.0, r["wait_s"] + flip + r["visit_s"]), 5)
        top = max((r["gain_per_s"] for r in rows), default=0.0)
        for r in rows:
            r["n_landings"] = len(rows)
            r["best"] = int(r["gain_per_s"] == top and top > 0)
            yield r
        st, S = ps.run(night, brain, s, t0=t, t1=t + every, state=S, stats=st)
        t += every


class ModelBrain:
    """Any function of the feature dict as a park_sim brain: predict(features) -> the game's score. The features are built
    from the position being scored alone (role 0), so the same model scores the screen game, the parked game and the rest."""
    name = "model"

    def __init__(self, predict):
        self.predict = predict

    def bind(self, arrs: list, s: ps.Setting) -> None:
        self.A, self.s = arrs, s

    def score(self, g: int, c: int, t: int, on_screen: bool, back: int = 0) -> float:
        x = self.A[g]
        known = min(x.n - 1, t + int(self.s.feed_lead))
        ci = min(x.n - 1, max(0, int(c)))
        ck = min(ci, known)
        seen = ci <= known
        return float(self.predict({
            "role": 1.0 if on_screen else 0.0, "in_progress": float(x.a0 <= t <= x.a1), "behind": float(t - ci), "lev": x.lev[ck],
            "margin": float(x.margin[ck]), "left_s": float(x.left_s[ck]), "dead_now": float(x.legal[ci]) if seen else -1.0,
            "since": float(min(x.since[ci], 300)) if seen else -1.0, "left": float(min(x.left[ci], known - ci)) if seen else -1.0,
            "known_s": float(known - ci), "val_known": x.ahead(ci, min(ci + ps.RUN_UP_S, known), ci),
            "live_known": float(sum(1 for i in range(ci + 1, min(ci + LIVE_LOOK_S, known) + 1) if not x.legal[i]))}))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--slates", nargs="+", default=["nba 4"], help='Nick\'s slates by name: "nba 4" "mnf+3" (the mixed one is synthetic)')
    ap.add_argument("--landings", default=None, help="also write the legal-landings table to this CSV (no rollouts)")
    ap.add_argument("--no-actions", action="store_true", help="skip the action-value table (the slow one)")
    ap.add_argument("--cap", type=int, default=2, help="dev nights per league")
    ap.add_argument("--every", type=int, default=20, help="seconds between decisions")
    ap.add_argument("--hold", type=int, default=30, help="seconds the tried action is held before the brain takes over")
    ap.add_argument("--horizon", type=int, default=180, help="seconds of screen the label counts")
    ap.add_argument("--set", action="append", default=[], help="key=value on park_sim.Setting (repeatable)")
    ap.add_argument("--cache", default=None, help="a folder OUTSIDE the repo for the per-game facts")
    ap.add_argument("--csv", required=True)
    a = ap.parse_args()
    s = ps.Setting(**{"guard": 10.0, **ps.parse_sets(a.set)})
    cache = Path(a.cache) if a.cache else None
    nights = [(name, k, night) for name in a.slates for k, night in enumerate(ps.slate_nights(name, a.cap, cache))]

    def write(path: str, make) -> int:
        n_rows, w = 0, None
        with open(path, "w", newline="") as fh:
            for name, k, night in nights:
                for row in make(night):
                    row = {"league": name, "games": len(night.games), "night": k, **row}
                    if w is None:
                        w = csv.DictWriter(fh, fieldnames=list(row))
                        w.writeheader()
                    w.writerow(row)
                    n_rows += 1
        return n_rows

    if not a.no_actions:
        print(f"action rows {write(a.csv, lambda night: table(night, s, every=a.every, hold=a.hold, horizon=a.horizon))} -> {a.csv}")
    if a.landings:
        print(f"landing rows {write(a.landings, lambda night: landings(night, s, every=a.every))} -> {a.landings}")
    print(f"setting {asdict(s)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
