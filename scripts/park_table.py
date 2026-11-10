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

`ModelBrain` wraps any function of those features as a park_sim brain, so a trained model is scored by the same simulator.

Why: the AI has to learn "which game to park next" and "jump now or wait" on what the two sticks can really do; a label
from an unconstrained hindsight view would teach it to ask for things the TV cannot deliver.
"""
from __future__ import annotations

import argparse
import copy
import csv
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
    ap.add_argument("--leagues", nargs="+", default=["nba"])
    ap.add_argument("--games", nargs="+", type=int, default=[4])
    ap.add_argument("--cap", type=int, default=2, help="dev nights per league")
    ap.add_argument("--every", type=int, default=20, help="seconds between decisions")
    ap.add_argument("--hold", type=int, default=30, help="seconds the tried action is held before the brain takes over")
    ap.add_argument("--horizon", type=int, default=180, help="seconds of screen the label counts")
    ap.add_argument("--set", action="append", default=[], help="key=value on park_sim.Setting (repeatable)")
    ap.add_argument("--cache", default=None, help="a folder OUTSIDE the repo for the per-game facts")
    ap.add_argument("--csv", required=True)
    a = ap.parse_args()
    s = ps.Setting(**{"guard": 10.0, **ps.parse_sets(a.set)})
    n_rows, w = 0, None
    with open(a.csv, "w", newline="") as fh:
        for lg in a.leagues:
            for ng in a.games:
                for k, night in enumerate(ps.nights_for(lg, ng, a.cap, Path(a.cache) if a.cache else None)):
                    for row in table(night, s, every=a.every, hold=a.hold, horizon=a.horizon):
                        row = {"league": lg, "games": ng, "night": k, **row}
                        if w is None:
                            w = csv.DictWriter(fh, fieldnames=list(row))
                            w.writeheader()
                        w.writerow(row)
                        n_rows += 1
    print(f"rows {n_rows} -> {a.csv}; setting {asdict(s)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
