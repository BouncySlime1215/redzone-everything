"""PARK step 5: the training tables, made from the PLANNER's own decisions (scripts/park_plan.py), and the model hook.

    nice -n 19 python scripts/park_table.py --slates "nba 4" "mnf+3" --cap 10 --error spec --every 60 \
        --csv park_table_actions.csv --landings park_table_landings.csv --cache <dir>
    --plan jump_bonus=1      a Plan knob the tables are built under;  --set key=value  a park_sim.Setting knob

No TV, no adb, no engine, no network, no training. Dev (`fit`) nights only.

Table 1, actions (`--csv`). The night is played by the planner. Every `--every` seconds the state is frozen and each legal
action is tried from a copy of it:
    stay            keep the screen game and leave the hidden stick alone for `--hold` seconds
    park:<game>     move the hidden stick to that game now, no jump for `--hold` seconds
    jump            jump to the game the hidden stick holds as soon as it is legal
after which the planner plays on. `q_value` is the dense value the screen then shows over the next `--horizon` seconds
(value2 grades, twice the must-sees, live play), `q_must` the must-see value alone, `q_jumps` the flips, `q_rule1` any
mid-play entry, mid-play leave or high-leverage leave in the horizon (a model must never be rewarded for those).
`planner_pick` marks the action the planner itself took that second: the label to imitate.

Table 2, landings (`--landings`; no rollouts, so it is quick). Per decision second, every landing the planner's search
looked at, one row each: the game, how the stick gets there (hold / roll / skip / tune), `wait_s` to the jump, how far
behind live the picture would come up, what the feed had already reported of the seconds after the landing (`known_s`,
`val_known`, `live_known`), the planner's own gain for it (`plan_gain`) and `picked` = 1 on the one it chose: the label
to imitate. Beside those, what really followed (hindsight, for grading only): the visit to that game's next legal leave
(`visit_s`, `value`, `live_s`, `must`), and the part of it the feed had NOT reported at the decision (`unk_s`,
`unk_value`): that part is what a model can add, so `kaggle_park.py --kind unknown` trains on it.

The features are only what the live app could know at that second. Columns prefixed k_ describe a game at the last second
the feed has reported; s_ is the screen game, h_ the hidden stick's game, a_ the action's game.

`ModelBrain` is the hook: it loads a small file of trees (an .npz in the layout redzone.models_rt.forest reads: arrays
feat, thr, left, right, dleft, miss, value, roots and a meta json with `features`, `depth`, `init`) and gives the planner
the value of one unreported second of a game (`rate`), in place of unk_w x leverage. Nothing is trained here.

Why: with one spare stick the show is decided by which landing is ready before the want arrives, so the model worth
training is the one that knows what a landing's unreported seconds will be worth, graded on what two sticks can really do.
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import random
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT.as_posix(), (ROOT / "scripts").as_posix()):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import park_plan as pp  # noqa: E402
import park_sim as ps  # noqa: E402

FEATURES = ("role", "in_progress", "behind", "lev", "margin", "left_s", "dead_now", "since", "left", "known_s", "val_known", "live_known")
# a game at the last second the feed has reported: all a model may know about the seconds after it
UNKNOWN_FEATURES = ("k_lev", "k_margin", "k_left_s", "k_dead", "k_since", "k_val60", "k_live60")
LIVE_LOOK_S = 30
VISIT_CAP_S = 180        # a visit is followed no further than this for its hindsight labels


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
        role, c = 0, t
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


def unknown_features(x, k: int) -> dict:
    """UNKNOWN_FEATURES of one game, read at k, the last second the feed has reported."""
    k = min(x.n - 1, max(0, k))
    lo = max(0, k - 59)
    return {"k_lev": x.lev[k], "k_margin": float(x.margin[k]), "k_left_s": float(x.left_s[k]), "k_dead": float(x.legal[k]),
            "k_since": float(min(x.since[k], 300)), "k_val60": x.cumv[k + 1] - x.cumv[lo],
            "k_live60": float(sum(1 for i in range(lo, k + 1) if not x.dead[i]))}


def clone(S):
    c = copy.copy(S)
    c.rng = random.Random()
    c.rng.setstate(S.rng.getstate())
    return c


def clone_planner(pl: pp.Planner) -> pp.Planner:
    c = copy.copy(pl)          # its state is a handful of tuples, replaced whole at every search
    c.trace = None
    return c


def actions(A: list, S, s: ps.Setting, t: int) -> list[tuple[str, int]]:
    out = [("stay", S.sg)]
    if S.hg >= 0:
        out.append(("jump", S.hg))
    out += [("park", g) for g in range(len(A)) if g not in (S.sg, S.hg) and A[g].a0 <= t <= A[g].a1]
    return out


def planner_action(night: ps.Night, s: ps.Setting, brain, S, pl: pp.Planner, t: int) -> tuple[str, int]:
    """What the planner does at second t, in the words of the action table: one second is played from a copy."""
    st, S2 = ps.run(night, brain, s, t0=t, t1=t + 1, state=clone(S), stats=ps.Stats(), planner=clone_planner(pl))
    if st.hops:
        return "jump", S2.sg
    if S2.hg >= 0 and S2.hg != S.hg and S2.hg != S.sg:
        return "park", S2.hg
    return "stay", S.sg


def table(night: ps.Night, s: ps.Setting, plan: pp.Plan, *, every: int, hold: int, horizon: int):
    """Yield one dict per (decision second, legal action). The planner plays the night and every rollout."""
    brain = ps.Foresight()
    A = night.arrs(s.shots, s.value, s.live_w)
    a, b = night.span
    pl = pp.Planner(plan)
    st, S = ps.run(night, brain, s, t0=a, t1=a + every, planner=pl)
    t = a + every
    while t + horizon < b:
        sf, hf = game_features(A, S, s, t, S.sg), game_features(A, S, s, t, S.hg)
        acts = actions(A, S, s, t)
        took = planner_action(night, s, brain, S, pl, t)
        rows = []
        for kind, g in acts:
            r, _ = ps.run(night, brain, s, t0=t, t1=t + horizon, state=clone(S), stats=ps.Stats(), force=(kind, g, t + hold),
                          planner=clone_planner(pl))
            rows.append({"t": t - a, "action": kind, "n_actions": len(acts), "on_screen_s": float(t - S.s_land),
                         "games_live": float(sum(1 for x in A if x.a0 <= t <= x.a1)),
                         **{f"s_{k}": v for k, v in sf.items()}, **{f"h_{k}": v for k, v in hf.items()},
                         **{f"a_{k}": v for k, v in game_features(A, S, s, t, g).items()},
                         "planner_pick": int((kind, g) == took or (kind == took[0] == "stay")),
                         "q_value": round(r.brain_value, 3), "q_must": round(sum(r.seen.values()), 3), "q_jumps": len(r.hops),
                         "q_rule1": r.mid_entry + r.mid_leave + r.hl_leave})
        top = max(x["q_value"] for x in rows)
        for x in rows:
            x["q_regret"] = round(top - x["q_value"], 3)
            yield x
        st, S = ps.run(night, brain, s, t0=t, t1=t + every, state=S, stats=st, planner=pl)
        t += every


def landings(night: ps.Night, s: ps.Setting, plan: pp.Plan, *, every: int):
    """Yield one dict per (decision second, landing the planner's search looked at)."""
    brain = ps.Foresight()
    A = night.arrs(s.shots, s.value, s.live_w)
    a, b = night.span
    n = night.n
    pl = pp.Planner(plan)
    pl.trace = []
    st, S = ps.run(night, brain, s, t0=a, t1=a + every, planner=pl)
    t = a + every
    while t + every < b:
        pl.trace.clear()
        pl.next = -1                                   # a search this very second, from the state as it stands
        sg, s_land, s_bel, hg = S.sg, S.s_land, int(t - S.sb + S.se), S.hg
        st, S = ps.run(night, brain, s, t0=t, t1=t + 1, state=S, stats=st, planner=pl)
        if pl.trace:
            _, v0, cs, K, opts, best = pl.trace[0]
            flip, low = pl.flip, s.leave_rule == "low_leverage"
            xs = A[sg]
            keep: dict[tuple, list] = {}               # the best plan through each landing
            for row in opts:
                key = row[7][:3]
                if key not in keep or row[0] > keep[key][0]:
                    keep[key] = row
            rows = []
            for row in keep.values():
                v, tent, tau, i, j, _, _, o, _ = row
                g, route, k = o[0], o[1], o[2]
                x, tb = A[g], pl.tabs[g]
                kj = min(K, n - 2)
                # what really followed: the visit runs to the game's next legal leave once 3 s of play have been shown
                c = min(n - 1, j)
                while c < min(n - 1, j + VISIT_CAP_S) and tb.cl[c] - tb.cl[j] < 3:
                    c += 1
                out = min(n - 1, c + x.leave_in(pl.G, low)[c], j + VISIT_CAP_S)
                m0 = min(out, j + ps.SEEN_LEAD_S)
                u0 = max(m0, kj + 1)                   # the first second of the visit the feed had not reported
                span_end = min(n - 1, s_bel + (tau - t) + flip + (out - j))
                value = x.cumv[out] - x.cumv[m0]
                stay_value = xs.cumv[span_end] - xs.cumv[min(span_end, s_bel)]
                rows.append({
                    "t": t - a, "game": g, "route": pp.ROUTES[route], "held": float(g == hg), "tentative": float(tent),
                    "wait_s": float(tau - t), "behind_s": float(tau + flip - j), "dead_before_play_s": float(tb.le[k] - j),
                    "known_s": float(max(0, kj - j)), "val_known": round(x.cumv[max(m0, min(out, kj + 1))] - x.cumv[m0], 3),
                    "live_known": float(tb.cl[max(j, min(j + LIVE_LOOK_S, kj + 1))] - tb.cl[j]),
                    "plan_gain": round(v - v0, 3), "picked": int(row is best),
                    **{kk: round(vv, 4) for kk, vv in unknown_features(x, kj).items()},
                    "s_lev": xs.lev[min(s_bel, kj)], "s_margin": float(xs.margin[min(s_bel, kj)]), "s_left_s": float(xs.left_s[min(s_bel, kj)]),
                    "s_on_screen_s": float(t - s_land), "s_behind_s": float(t - s_bel),
                    "games_live": float(sum(1 for y in A if y.a0 <= t <= y.a1)),
                    # hindsight, for grading and labels only
                    "visit_s": float(out - j), "value": round(value, 3), "live_s": float(tb.cl[out] - tb.cl[j]),
                    "must": round(sum(mv for mi, mv in x.mom.items() if m0 <= mi < out), 3), "stay_value": round(stay_value, 3),
                    "unk_s": float(max(0, out - u0)), "unk_value": round(x.cumv[out] - x.cumv[min(out, u0)], 3)})
            for r in rows:
                r["gain_per_s"] = round((r["value"] - r["stay_value"]) / max(1.0, r["wait_s"] + flip + r["visit_s"]), 5)
                r["n_landings"] = len(rows)
                yield r
        st, S = ps.run(night, brain, s, t0=t + 1, t1=t + every, state=S, stats=st, planner=pl)
        t += every


class ModelBrain:
    """A model as a park_sim brain and as the planner's value of an unreported second.

    ModelBrain(predict) wraps any function of a feature dict. ModelBrain.load(path) reads a file of trees:
        score(g, c, t, on_screen)   the brain protocol, on FEATURES built at the position scored (role 0 or 1)
        rate(g, k, t)               what one second of game g after k, the last second the feed has reported, is worth,
                                    on UNKNOWN_FEATURES: park_plan.Planner(unknown=model) uses it in place of unk_w x leverage."""
    name = "model"

    def __init__(self, predict, features: tuple = ()):
        self.predict, self.features = predict, tuple(features)

    @classmethod
    def load(cls, path) -> "ModelBrain":
        import numpy as np
        from redzone.models_rt.forest import Nodes, load_npz
        arrays, meta = load_npz(Path(path))
        feats = tuple(meta.get("features") or ())
        if feats not in (UNKNOWN_FEATURES, FEATURES):
            raise ValueError(f"{path}: trained on {feats}, not on the park features")
        nodes, init = Nodes(arrays, int(meta["depth"])), float(meta.get("init", 0.0))

        def predict(row: dict) -> float:
            return init + float(nodes.value[nodes.leaves(np.array([row[f] for f in feats], dtype=np.float32))].sum())
        return cls(predict, feats)

    def bind(self, arrs: list, s: ps.Setting) -> None:
        self.A, self.s = arrs, s

    def rate(self, g: int, k: int, t: int) -> float:
        return max(0.0, float(self.predict(unknown_features(self.A[g], k))))

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
    ap.add_argument("--error", default="spec", choices=sorted(pp.ERRORS), help="the timing scenario the planner plays under")
    ap.add_argument("--landings", default=None, help="also write the landings table to this CSV (no rollouts)")
    ap.add_argument("--no-actions", action="store_true", help="skip the action-value table (the slow one)")
    ap.add_argument("--cap", type=int, default=10, help="dev nights per league (10 = all of them)")
    ap.add_argument("--every", type=int, default=60, help="seconds between decisions in the action table")
    ap.add_argument("--every-landings", type=int, default=20, help="seconds between decisions in the landings table")
    ap.add_argument("--hold", type=int, default=30, help="seconds the tried action is held before the planner takes over")
    ap.add_argument("--horizon", type=int, default=180, help="seconds of screen the label counts")
    ap.add_argument("--plan", action="append", default=[], help="key=value on park_plan.Plan (repeatable)")
    ap.add_argument("--set", action="append", default=[], help="key=value on park_sim.Setting (repeatable)")
    ap.add_argument("--cache", default=None, help="a folder OUTSIDE the repo for the per-game facts")
    ap.add_argument("--csv", required=True)
    a = ap.parse_args()
    s = pp.setting(a.error, True, **ps.parse_sets(a.set))
    plan = pp.Plan(**pp.parse_plan(a.plan))
    cache = Path(a.cache) if a.cache else None
    nights = [(name, k, night) for name in a.slates for k, night in enumerate(ps.slate_nights(name, a.cap, cache))]

    def write(path: str, make) -> int:
        n_rows, w = 0, None
        with open(path, "w", newline="") as fh:
            for name, k, night in nights:
                for row in make(night):
                    row = {"league": name, "games": len(night.games), "night": k, "error": a.error, **row}
                    if w is None:
                        w = csv.DictWriter(fh, fieldnames=list(row))
                        w.writeheader()
                    w.writerow(row)
                    n_rows += 1
        return n_rows

    if not a.no_actions:
        print(f"action rows {write(a.csv, lambda night: table(night, s, plan, every=a.every, hold=a.hold, horizon=a.horizon))} -> {a.csv}")
    if a.landings:
        print(f"landing rows {write(a.landings, lambda night: landings(night, s, plan, every=a.every_landings))} -> {a.landings}")
    print(f"nights {len(nights)} setting {json.dumps(asdict(s))} plan {json.dumps(asdict(plan))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
