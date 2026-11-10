"""The PARK planner on tiny hand-made nights: it jumps where it should, never inside the padding, never past what the feed
has reported, never over the lateness cap, never out of a high-leverage span, and a reported must-see beats any bonus.

Why: scripts/park_plan.py only ASKS park_sim for jumps, so every Rule #1 promise it makes has to be checked where it is
made: on nights small enough to read by eye, with the timing error at zero so a believed position IS the true one.
No archive, no TV, no training: every game here is a few typed lists.
"""
import random
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT.as_posix(), (ROOT / "scripts").as_posix()):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import park_plan as pp  # noqa: E402
import park_sim as ps  # noqa: E402
import park_table as pt  # noqa: E402

T0 = 1000          # every hand-made game tips off here


def game(gid, end, live, *, shots=(), v2=(), big=(), hl=(), lev=1.0):
    """One game's facts as park_ceiling.truth_of gives them. `live` = [(start, end), ...]; the rest of T0..end is dead ball."""
    dead, prev = [], T0
    for a, b in live:
        if a > prev:
            dead.append((prev, a))
        prev = b
    if end > prev:
        dead.append((prev, end))
    return {"gid": gid, "t0": T0, "t1": end, "dead": dead, "brk": [False] * len(dead), "shots": list(shots), "big": sorted(big),
            "hl": list(hl), "lev": [(T0, lev, 5, 600.0)], "v2": sorted((t, v, 0.0) for t, v in v2)}


def exact(guard=2, **kw):
    """A planner setting with no timing error at all: what the app believes is what is true."""
    base = dict(route="tune", ping_pong=True, land_wait_max=999.0, max_behind=315.0, pos_sd=0.0, delay_sd=0.0, stamp_sd=0.0, key_sd=0.0,
                pad_k=0.0, guard=float(guard), shots=True)
    base.update(kw)
    return ps.Setting(**base)


def play(night, s, plan, brain=None, until=None):
    """Run the planner one second at a time and keep every flip with where it left and where it landed: true positions,
    as clock seconds (`leave`, `land`) and as places on the night's grid (`leave_i`, `land_i`). `until` is a clock second."""
    pl = pp.Planner(plan)
    A = night.arrs(s.shots, s.value, s.live_w)
    a, b = night.span
    base = night.base
    b = b if until is None else min(b, until - base)
    brain = brain or ps.Foresight()
    st, S = ps.run(night, brain, s, t0=a, t1=a + 1, planner=pl)
    cuts = []
    for t in range(a + 1, b):
        sg, sb = S.sg, S.sb
        n_before = len(st.hops)
        st, S = ps.run(night, brain, s, t0=t, t1=t + 1, state=S, stats=st, planner=pl)
        if len(st.hops) > n_before:
            cuts.append({"t": t + base, "from": sg, "leave": int(t - sb) + base, "leave_i": int(t - sb), "to": S.sg,
                         "land": int(S.s_from) + base, "land_i": int(S.s_from), "behind": S.sb})
    return st, S, cuts, A, pl


def padded(x, i, guard):
    """Is position i at least `guard` inside a legal stretch, with no high-leverage second within `guard` of it."""
    calm = x.hlc[min(x.n, i + guard + 1)] - x.hlc[max(0, i - guard)] == 0
    return x.since[i] > guard and x.left[i] >= guard and calm


def two_games():
    """Game 0: a 40 s dead ball at 1100. Game 1: a dead ball 1090-1110, then a play with a graded shot at 1122."""
    g0 = game("a", 1400, [(1000, 1100), (1140, 1300), (1330, 1400)])
    g1 = game("b", 1400, [(1000, 1090), (1110, 1125), (1150, 1290), (1320, 1400)], shots=[1122], v2=[(1122, 3.0)])
    return ps.Night([g0, g1])


def test_it_jumps_at_the_known_dead_ball_to_the_known_shot():
    night = two_games()
    s = exact()
    st, S, cuts, A, _ = play(night, s, pp.Plan(jump_bonus=0.0), until=1200)
    first = cuts[0]
    assert first["from"] == 0 and first["to"] == 1
    assert 1102 <= first["leave"] <= 1110 and first["leave"] == first["t"]                # at the live edge, inside game 0's dead ball
    assert 1092 <= first["land"] <= 1108                                                  # inside game 1's dead ball, before its play
    assert first["land"] + ps.SEEN_LEAD_S <= 1122                                         # early enough for the shot to count as seen
    assert st.v2_seen == pytest.approx(3.0)
    assert st.mid_entry == st.mid_leave == st.hl_leave == 0


def random_night(seed, games=3, minutes=40):
    """Games with dead balls of every length, shots, graded plays, a must-see or two and high-leverage spans."""
    rng = random.Random(seed)
    out = []
    for g in range(games):
        t, live, shots, v2, big, hl = T0 + rng.randrange(0, 20), [], [], [], [], []
        end = T0 + minutes * 60
        while t < end - 60:
            d = rng.choice([6, 9, 14, 20, 24, 35])
            a, b = t, min(end - 30, t + d)
            live.append((a, b))
            if d >= 14:
                sh = a + d - 6
                shots.append(sh)
                if rng.random() < 0.4:
                    v2.append((sh, rng.choice([1.0, 2.0, 3.0])))
                if rng.random() < 0.04:
                    big.append((sh, 5.0))
                if rng.random() < 0.08:
                    hl.append((sh - 7, sh))
            t = b + rng.choice([3, 5, 8, 12, 30, 45, 70])
        out.append(game(f"g{g}", end, live, shots=shots, v2=v2, big=big, hl=hl, lev=rng.choice([0.7, 1.0, 1.6])))
    return ps.Night(out)


@pytest.mark.parametrize("guard, bonus", [(2, 0.0), (2, 2.0), (6, 0.5), (6, 2.0), (14, 2.0)])
def test_never_inside_the_padding_and_never_out_of_high_leverage(guard, bonus):
    flips = 0
    for seed in (1, 2, 3):
        night = random_night(seed)
        st, S, cuts, A, _ = play(night, exact(guard), pp.Plan(jump_bonus=bonus))
        for c in cuts:
            xs, xh = A[c["from"]], A[c["to"]]
            assert padded(xs, c["leave_i"], guard) or c["leave_i"] > xs.a1 + guard, c     # the leave (or the game is over, by the padding)
            assert xh.since[c["land_i"]] > guard and xh.left[c["land_i"]] >= guard, c      # the landing
            assert not xs.hl[c["leave_i"]], c
        assert st.mid_entry == st.mid_leave == st.hl_leave == 0
        flips += len(cuts)
    assert flips >= 10          # it did jump: the test is not passing on an idle planner


def test_no_leave_inside_a_high_leverage_span():
    # game 0's dead ball 1100-1140 starts inside a high-leverage play (1095-1125); game 1 has a graded shot worth going to
    g0 = game("a", 1400, [(1000, 1100), (1140, 1300), (1330, 1400)], hl=[(1095, 1125)])
    g1 = game("b", 1400, [(1000, 1090), (1136, 1150), (1180, 1290), (1320, 1400)], shots=[1147], v2=[(1147, 3.0)])
    night = ps.Night([g0, g1])
    st, S, cuts, A, _ = play(night, exact(), pp.Plan(jump_bonus=2.0), until=1200)
    first = next(c for c in cuts if c["from"] == 0)
    assert first["leave"] >= 1125 + 2 + 1          # not until the whole span, and the padding after it, has gone by
    assert st.hl_leave == 0


@pytest.mark.parametrize("cap", [30.0, 60.0, 145.0])
def test_never_over_the_lateness_cap(cap):
    worst, flips = 0.0, 0
    for seed in (4, 5):
        night = random_night(seed)
        st, S, cuts, A, _ = play(night, exact(2, late_cap=cap), pp.Plan(jump_bonus=2.0, late_w=0.0))
        worst = max(worst, max(st.behind))
        flips += len(cuts)
        assert all(c["behind"] <= cap for c in cuts)
    assert worst <= cap and flips >= 10
    if cap == 30.0:
        assert worst > 5.0          # the cap was really in play: sticks do sit behind live


def test_the_rule_set_controller_keeps_the_cap_too():
    night = random_night(6)
    s = ps.Setting(pos_sd=0.0, delay_sd=0.0, stamp_sd=0.0, key_sd=0.0, pad_k=0.0, guard=6.0, late_cap=60.0, max_behind=300.0, bore_w=0.05)
    st, _ = ps.run(night, ps.Hindsight(), s)
    assert max(st.behind) <= 60.0 and len(st.hops) >= 3


def test_nothing_past_what_the_feed_has_reported():
    """Two nights that are the same up to second X and different after it: until the feed could have reported second X the
    planner must do exactly the same thing on both. (No high-leverage spans here: park_sim's own calm test reads their 7 s.)"""
    base = random_night(7, games=3, minutes=30)
    s = exact(2)
    lead = int(s.feed_lead)
    for x in (T0 + 400, T0 + 700, T0 + 1000):
        twin = []
        for g in base.games:
            cut = dict(g, hl=[])
            # after X the twin is another game: nothing but one long dead ball
            cut["dead"] = [(a, min(b, x)) for a, b in g["dead"] if a < x] + [(x, g["t1"])]
            cut["brk"] = [False] * len(cut["dead"])
            cut["shots"] = [v for v in g["shots"] if v < x]
            cut["v2"] = [v for v in g["v2"] if v[0] < x]
            cut["big"] = [v for v in g["big"] if v[0] < x]
            twin.append(cut)
        one = ps.Night([dict(g, hl=[]) for g in base.games])
        two = ps.Night(twin)
        until = x - lead - 1          # the last second whose feed (now + feed_lead) holds nothing from X on
        r1 = play(one, s, pp.Plan(jump_bonus=1.0), until=until)
        r2 = play(two, s, pp.Plan(jump_bonus=1.0), until=until)
        assert r1[2] == r2[2] and len(r1[2]) >= 3                              # the same flips at the same seconds and places
        assert r1[0].sched == r2[0].sched and r1[0].firm == r2[0].firm          # the same plans
        assert r1[4].cmd == r2[4].cmd and r1[4].go == r2[4].go                  # and the same next step
        assert (r1[1].sg, r1[1].hg, r1[1].sb, r1[1].hb, r1[1].h_paused) == (r2[1].sg, r2[1].hg, r2[1].sb, r2[1].hb, r2[1].h_paused)


def test_a_reported_must_see_beats_any_grade():
    # game 0 shows three graded plays worth 6 each while game 1 shows one must-see (5): the dense value says stay
    g0 = game("a", 1500, [(1000, 1100), (1120, 1200), (1230, 1500)], shots=[1140, 1150, 1160], v2=[(1140, 6.0), (1150, 6.0), (1160, 6.0)])
    g1 = game("b", 1500, [(1000, 1095), (1125, 1170), (1200, 1500)], big=[(1150, 5.0)])
    night = ps.Night([g0, g1])
    st, S, cuts, A, _ = play(night, exact(), pp.Plan(jump_bonus=0.0), until=1260)
    assert sum(st.seen.values()) == pytest.approx(5.0)
    assert any(c["to"] == 1 and c["land"] + ps.SEEN_LEAD_S <= 1150 for c in cuts)


def test_planned_jump_delivered_is_counted_per_scheduled_jump():
    st = ps.Stats()
    st.sched = [(90, 100, 1), (150, 200, 2), (290, 300, 1), (390, 400, 1)]
    st.flips = [(101, 1, 0), (200, 1, 0), (299, 1, 1), (403, 1, 0)]      # on time; wrong game; on time with a break; 3 s late
    assert ps.delivered(st) == 1
    st.firm = [(95, 100, 1)]
    assert ps.delivered(st, st.firm) == 1


def test_most_scheduled_jumps_are_delivered_and_logged():
    night = random_night(8)
    st, S, cuts, A, pl = play(night, exact(2), pp.Plan(jump_bonus=1.0))
    assert len(st.sched) >= len(st.firm) >= 5
    assert ps.delivered(st, st.firm) / len(st.firm) >= 0.8
    row = ps.summarize([(night, st)])
    assert 0.0 < row["planned_delivered"] <= 1.0 and row["firm_delivered"] >= 0.8 and row["behind_max_s"] <= 145.0


def test_the_bonus_buys_jumps():
    night = random_night(9)
    calm, _, _, _, _ = play(night, exact(2), pp.Plan(jump_bonus=0.0))
    keen, _, _, _, _ = play(night, exact(2), pp.Plan(jump_bonus=2.0))
    assert len(keen.jumps) > len(calm.jumps)


def tiny_model(path, features, feat, thr, left_value, right_value, init=0.0):
    """An .npz of one tree in the layout redzone.models_rt.forest reads (the layout kaggle_park.py writes)."""
    import json
    meta = {"features": list(features), "depth": 1, "init": init, "kind": "unknown"}
    np.savez_compressed(path, feat=np.array([features.index(feat), 0, 0], np.int16), thr=np.array([thr, 0.0, 0.0]),
                        left=np.array([1, 1, 2], np.int32), right=np.array([2, 1, 2], np.int32), dleft=np.array([True, False, False]),
                        miss=np.zeros(3, np.int8), value=np.array([0.0, left_value, right_value], np.float32), roots=np.array([0], np.int32),
                        meta=np.frombuffer(json.dumps(meta).encode(), dtype=np.uint8))


def test_model_brain_loads_a_tree_file_and_values_the_unreported_seconds(tmp_path):
    f = tmp_path / "park_unknown.npz"
    tiny_model(f, pt.UNKNOWN_FEATURES, "k_lev", 1.2, 0.01, 0.09, init=0.005)
    model = pt.ModelBrain.load(f)
    lo = ps.Night([game("a", 1400, [(1000, 1100), (1140, 1400)], lev=0.8), game("b", 1400, [(1000, 1090), (1110, 1400)], lev=2.0)])
    s = exact()
    model.bind(lo.arrs(s.shots, s.value, s.live_w), s)
    k = 1050 - lo.base
    assert model.rate(0, k, k - 27) == pytest.approx(0.015)     # leverage 0.8: the left leaf
    assert model.rate(1, k, k - 27) == pytest.approx(0.095)     # leverage 2.0: the right leaf
    # and the planner runs with it in place of unk_w x leverage
    pl = pp.Planner(pp.Plan(jump_bonus=0.5), unknown=model)
    st, _ = ps.run(lo, ps.Foresight(), s, planner=pl)
    assert pl.searches > 100 and st.mid_entry == st.mid_leave == st.hl_leave == 0


def test_model_brain_refuses_a_file_trained_on_other_features(tmp_path):
    f = tmp_path / "bad.npz"
    tiny_model(f, ("k_lev", "something_else"), "k_lev", 1.0, 0.0, 1.0)
    with pytest.raises(ValueError):
        pt.ModelBrain.load(f)


def test_the_tables_come_from_the_planners_own_decisions():
    night = random_night(10, games=3, minutes=25)
    s = exact(2)
    rows = list(pt.landings(night, s, pp.Plan(jump_bonus=0.5), every=20))
    assert rows and all(set(pt.UNKNOWN_FEATURES) <= set(r) for r in rows)
    by = {}
    for r in rows:
        by.setdefault(r["t"], []).append(r)
    assert all(sum(r["picked"] for r in rs) <= 1 for rs in by.values()) and any(r["picked"] for r in rows)
    assert all(r["unk_s"] >= 0 and r["known_s"] >= 0 for r in rows)
    acts = list(pt.table(night, s, pp.Plan(jump_bonus=0.5), every=120, hold=20, horizon=90))
    assert acts and {"stay"} <= {r["action"] for r in acts}
    for t in {r["t"] for r in acts}:
        assert sum(r["planner_pick"] for r in acts if r["t"] == t) == 1
    assert all(r["q_rule1"] == 0 for r in acts if r["planner_pick"])


def test_old_sweep_rows_load_with_the_cap_off():
    import park_pick
    row = {f: str(getattr(ps.Setting(), f)) for f in ps.Setting.__dataclass_fields__ if f != "late_cap"}
    assert park_pick.setting_of(row).late_cap == 0.0
    assert park_pick.setting_of(dict(row, late_cap="145.0")).late_cap == 145.0


def test_setting_for_each_error_gives_the_padding_of_the_brief():
    night = two_games()
    for err, pad in (("today", 14), ("middle", 4), ("spec", 2)):
        pl = pp.Planner(pp.Plan())
        ps.run(night, ps.Foresight(), pp.setting(err, True), t0=night.span[0], t1=night.span[0] + 2, planner=pl)
        assert pl.G == pad
    assert replace(pp.setting("spec", True), late_cap=0.0).late_cap == 0.0 and pp.setting("spec", True).late_cap == 145.0


# ---- scripts/kaggle_park.py: everything but the training itself (nothing is fitted on the Mac) -----------------------

class FakeBooster:
    """Stands in for a LightGBM booster: one tree, split on the first feature at 1.2."""

    def dump_model(self):
        return {"tree_info": [{"tree_structure": {"split_feature": 0, "threshold": 1.2, "decision_type": "<=", "default_left": True,
                                                  "missing_type": "None", "left_child": {"leaf_value": 0.01}, "right_child": {"leaf_value": 0.09}}}]}

    def predict(self, x):
        return np.where(np.asarray(x)[:, 0] <= 1.2, 0.01, 0.09)


class FakeModel:
    booster_ = FakeBooster()


def test_the_trainer_writes_the_file_the_planner_reads(tmp_path):
    import kaggle_park as kp
    assert tuple(kp.UNKNOWN_FEATURES) == tuple(pt.UNKNOWN_FEATURES)
    x = np.array([[0.8, 0, 0, 0, 0, 0, 0], [2.0, 0, 0, 0, 0, 0, 0]], dtype=np.float32)
    info = kp.export(FakeModel(), list(kp.UNKNOWN_FEATURES), "unknown", x, tmp_path / "park_unknown.npz")
    assert info["trees"] == 1 and info["max_abs_diff_vs_booster"] < 1e-6
    model = pt.ModelBrain.load(tmp_path / "park_unknown.npz")
    row = dict.fromkeys(pt.UNKNOWN_FEATURES, 0.0)
    assert model.predict(dict(row, k_lev=0.8)) == pytest.approx(0.01) and model.predict(dict(row, k_lev=2.0)) == pytest.approx(0.09)


def test_the_trainer_reads_the_planners_tables(tmp_path):
    import csv

    import kaggle_park as kp
    s, plan = exact(2), pp.Plan(jump_bonus=0.5)
    paths = {}
    for kind, make in (("landings", lambda n: pt.landings(n, s, plan, every=20)),
                       ("actions", lambda n: pt.table(n, s, plan, every=150, hold=20, horizon=90))):
        paths[kind] = tmp_path / f"{kind}.csv"
        with open(paths[kind], "w", newline="") as fh:
            w = None
            for k in range(2):
                for row in make(random_night(20 + k, games=3, minutes=20)):
                    row = {"league": "tiny 3", "games": 3, "night": k, "night_id": f"2026010{k + 1}", "error": "none", **row}
                    w = w or csv.DictWriter(fh, fieldnames=list(row))
                    if fh.tell() == 0:
                        w.writeheader()
                    w.writerow(row)
    land = kp.load([str(paths["landings"])])
    train, test = kp.split(land, 0.5)
    assert set(train["night"]) == {0} and set(test["night"]) == {1}          # the newest recorded night is the test night
    assert kp.nights_of(test) == ["20260102"] and kp.nights_of(train) == ["20260101"]
    twice = pd.concat([land, land.assign(league="tiny 2")])                  # the same nights under a second slate
    tr2, te2 = kp.split(twice, 0.5)
    assert set(tr2["night_id"].astype(str)).isdisjoint(set(te2["night_id"].astype(str))) and len(te2) == 2 * len(test)
    assert pt.night_id(random_night(20)) == "19691231"                        # hand-made games tip off at second 1000 (12 h are taken off)
    assert set(kp.LANDING_FEATURES) - {f"route_{r}" for r in kp.ROUTES} <= set(land.columns)
    unk = kp.unknown_rows(land)
    assert len(unk) and (unk["unk_s"] >= kp.UNKNOWN_MIN_S).all() and not unk.duplicated(["league", "night", "error", "t", "game"]).any()
    assert kp.judge_unknown(unk, unk["unk_rate"])["weighted_mae"] == 0.0
    assert kp.judge_landings(land, land["gain_per_s"])["mean_gain_lost"] == 0.0
    assert 0.0 <= kp.judge_landings(land, land["picked"])["picked_best"] <= 1.0
    acts = kp.load([str(paths["actions"])])
    assert kp.judge(acts, acts["q_value"])["mean_regret"] == 0.0
    assert kp.judge(acts, acts["planner_pick"])["same_as_planner"] == 1.0
    y = kp.target(acts, 5.0)
    assert abs(float(y.groupby(acts["decision"]).sum().abs().max())) < 1e-6 or (acts["q_rule1"] > 0).any()
