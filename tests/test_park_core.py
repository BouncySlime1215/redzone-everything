"""The PARK core and planner in the package (redzone/noggin/park_core.py, park_plan.py) are the scripts' code, unchanged.

Pinned here: on one small hand-made night, the planner's flips second for second, its first scheduled jumps and its counters,
and the rule-set controller's flips and end state under a seeded timing error. The numbers were taken from the scripts BEFORE
the move (branch park at 57fdca52), so this fails on any change of behaviour. Also: the scripts still expose every name other
code imports from them (the local show runner does `import park_sim, park_plan, park_ceiling`), the timing numbers are the
old constants, and nothing in the app imports the new modules yet (so with `park.enabled` off the app cannot change).

Why: PARK-CORE moved the planner and the simulator core out of scripts/ so the app and the scripts run one copy; a move
must change nothing, and this is where that is proved without the archive.
"""
import math
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT.as_posix(), (ROOT / "scripts").as_posix()):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from redzone.noggin import park_core as core  # noqa: E402
from redzone.noggin import park_plan as plan_core  # noqa: E402

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


def night():
    """Three ten-minute games: shots, graded plays, one must-see (game 1 at 1240) and one high-leverage span (game 0)."""
    g0 = game("a", 1600, [(1000, 1060), (1090, 1180), (1220, 1330), (1370, 1480), (1510, 1600)], shots=[1050, 1170, 1320],
              v2=[(1170, 2.0), (1320, 1.0)], hl=[(1313, 1320)])
    g1 = game("b", 1600, [(1000, 1040), (1075, 1130), (1160, 1250), (1300, 1400), (1440, 1560)], shots=[1120, 1240, 1390],
              v2=[(1120, 3.0), (1390, 2.0)], big=[(1240, 5.0)])
    g2 = game("c", 1600, [(1000, 1100), (1125, 1210), (1245, 1350), (1380, 1450), (1490, 1600)], shots=[1205, 1345],
              v2=[(1205, 1.0), (1345, 3.0)], lev=1.5)
    return core.Night([g0, g1, g2])


class Ahead:
    """A brain that reads only what the feed has reported: the value about to air from picture position c."""
    name = "ahead"

    def bind(self, A, s):
        self.A, self.lead = A, int(s.feed_lead)

    def score(self, g, c, t, on_screen, back=0):
        return self.A[g].ahead(c - back + (0 if on_screen else 7), min(c + 75, t + self.lead), c)


def exact(**kw):
    """No timing error: what the app believes is what is true."""
    base = dict(route="tune", ping_pong=True, land_wait_max=999.0, max_behind=315.0, pos_sd=0.0, delay_sd=0.0, stamp_sd=0.0, key_sd=0.0,
                pad_k=0.0, guard=2.0, shots=True)
    base.update(kw)
    return core.Setting(**base)


# ---- pinned behaviour (numbers from the scripts before the move) ------------------------------------------------------

PINNED_PLAN = {
    0.0: {"flips": [(462, 1, 0), (532, 2, 0), (618, 1, 0), (704, 2, 0), (810, 1, 0), (822, 2, 0), (898, 1, 0)],
          "sched": [(449, 462, 1), (498, 522, 0), (508, 532, 2), (585, 618, 1), (619, 704, 2), (725, 810, 1)], "n_sched": 11,
          "searches": 307, "parked_big": 5.0, "live_s": 557, "dead_s": 29, "wants": 182},
    1.0: {"flips": [(452, 1, 0), (454, 0, 0), (466, 1, 0), (536, 2, 0), (622, 0, 0), (634, 2, 0), (640, 1, 0), (652, 2, 0), (779, 0, 0),
                    (781, 2, 0), (790, 1, 0), (802, 2, 0), (902, 0, 0)],
          "sched": [(449, 452, 1), (453, 454, 0), (455, 466, 1), (498, 536, 2), (582, 622, 0), (623, 634, 2)], "n_sched": 14,
          "searches": 308, "parked_big": 0.0, "live_s": 478, "dead_s": 96, "wants": 165},
}


def test_planner_flips_second_for_second_as_before_the_move():
    for bonus, want in PINNED_PLAN.items():
        pl = plan_core.Planner(plan_core.Plan(jump_bonus=bonus))
        st, _ = core.run(night(), Ahead(), exact(), planner=pl)
        assert st.flips == want["flips"], bonus
        assert st.sched[:6] == want["sched"] and len(st.sched) == want["n_sched"], bonus
        assert pl.searches == want["searches"], bonus
        assert (st.mid_entry, st.mid_leave, st.hl_leave) == (0, 0, 0)
        assert st.v2_seen == 7.0 and st.seen["parked"] == want["parked_big"]
        assert (st.live_s, st.dead_s, len(st.wants)) == (want["live_s"], want["dead_s"], want["wants"]), bonus


def test_rule_set_controller_and_physics_as_before_the_move():
    """No planner, timing error on and seeded: the random draws, the padding and every counter land where they did."""
    s = core.Setting(pos_err=3.0, delay_sd=0.5, stamp_sd=0.3, key_sd=0.1, pad_k=3.0, shots=True, seed=11)
    st, S = core.run(night(), Ahead(), s)
    assert st.flips == [(466, 1, 0), (536, 0, 0), (641, 1, 0), (746, 0, 0)]
    assert (st.mid_entry, st.mid_leave, st.hl_leave, st.walk_entry, st.walk_leave, st.parks, st.rolls) == (0, 0, 0, 0, 0, 1, 3)
    assert st.v2_seen == 6.0 and len(st.wants) == 187
    assert st.why == {"other_game_parked": 33, "screen_mid_play": 105, "loading": 2, "screen_high_leverage": 20, "landing_not_dead": 23}
    assert (S.sg, round(S.sb, 6), S.hg, round(S.hb, 6), S.h_paused) == (0, 136.15639, 1, 109.77591, False)


# ---- the scripts still expose the old names -----------------------------------------------------------------------------

def test_scripts_still_expose_the_old_names():
    import park_ceiling as pc
    import park_plan as pp
    import park_sim as ps
    for name in ("Setting", "Arr", "Night", "State", "Stats", "run", "served", "yes_rate", "delivered", "summarize", "WHY", "TUNE_TICKS"):
        assert getattr(ps, name) is getattr(core, name), name
    for name in ("Hindsight", "Foresight", "Heuristic", "BRAINS", "nights_for", "slate_nights", "evaluate", "GRID", "draw", "parse_sets", "main"):
        assert hasattr(ps, name), name
    for name in ("Plan", "Tab", "tabs_for", "MUST_W", "FAR", "HOLD", "ROLL", "MOVE", "TUNE", "ROUTES", "FRESH_BEHIND", "JOIN_SLACK"):
        assert getattr(pp, name) is getattr(plan_core, name), name
    assert issubclass(pp.Planner, plan_core.Planner)
    for name in ("ERRORS", "ERR_FIXED", "setting", "evaluate", "report", "parse_plan", "main", "HOP_GAIN_S"):
        assert hasattr(pp, name), name
    P = core.PARK_PARAMS
    old = {"RUN_UP_S": (ps, 75), "SEEN_LEAD_S": (ps, 7), "YES_S": (ps, 20), "PLAN_OK_S": (ps, 2), "FEED_LEAD_S": (ps, 27),
           "LIVE_BEHIND_S": (ps, 60), "LONG_STOP_S": (ps, 20), "SAME_BEHIND_S": (ps, 10), "PAD_S": (ps, 400), "HOP_GAIN_S": (pp, 10)}
    for name, (mod, v) in old.items():
        got = getattr(mod, name)
        assert got == v and type(got) is int and getattr(P, name.lower()) == v, name
    for name in ("SHOT_WINDOW_S", "SKIP_STEP_S", "PRESS_S", "REACH_S", "RESCRUB_1_S"):
        assert getattr(P, name.lower()) == getattr(pc, name) and type(getattr(P, name.lower())) is float, name
    assert core.WINDOWS_MIN == pc.WINDOWS_MIN
    assert core.Setting().feed_lead == 27 and type(core.Setting().feed_lead) is int     # the CSVs write "27", as before


def test_a_caller_that_sets_park_ceiling_skip_step_still_gets_it():
    """The local show runner's NoSkipPlanner takes the skip route away by setting park_ceiling.SKIP_STEP_S during a search.
    Through the script's Planner that still works, and it equals the package planner given the same number."""
    import park_ceiling as pc
    import park_plan as pp

    class NoSkip(pp.Planner):
        def _search(self, t, S):
            keep, pc.SKIP_STEP_S = pc.SKIP_STEP_S, float("inf")
            try:
                super()._search(t, S)
            finally:
                pc.SKIP_STEP_S = keep

    s = exact(guard=3.0)
    a, _ = core.run(night(), Ahead(), s, planner=NoSkip(plan_core.Plan(jump_bonus=1.0)))
    pl = plan_core.Planner(plan_core.Plan(jump_bonus=1.0))
    pl.params = replace(core.PARK_PARAMS, skip_step_s=math.inf)
    b, _ = core.run(night(), Ahead(), s, planner=pl)
    c, _ = core.run(night(), Ahead(), s, planner=pp.Planner(plan_core.Plan(jump_bonus=1.0)))
    d, _ = core.run(night(), Ahead(), s, planner=plan_core.Planner(plan_core.Plan(jump_bonus=1.0)))
    assert a.flips == b.flips and a.sched == b.sched
    assert c.flips == d.flips and c.sched == d.sched                 # with the measured step the script's planner IS the package's
    assert pc.SKIP_STEP_S == 15.0


def test_params_from_config():
    assert core.ParkParams.from_config(lambda k, d: d) == core.PARK_PARAMS
    got = core.ParkParams.from_config(lambda k, d: {"park.skip_step_s": 10, "park.seen_lead_s": 5.0, "park.yes_s": "x"}.get(k, d))
    assert got.skip_step_s == 10.0 and type(got.skip_step_s) is float
    assert got.seen_lead_s == 5 and type(got.seen_lead_s) is int
    assert got.yes_s == 20                                           # a bad value keeps the default


def test_only_the_park_shows_own_modules_import_them():
    """Wired in behind `park.enabled` by unit 8 (PARK-CONTROLLER): only the show's own modules import the core and the
    planner, and the engine imports the show only inside `_park_loop`, once the flag is on
    (tests/test_park_flag_off.py), so with the flag off the app's decisions cannot change."""
    hits = []
    for p in (ROOT / "redzone").rglob("*.py"):
        if p.name in ("park_core.py", "park_plan.py") and p.parent.name == "noggin":
            continue
        text = p.read_text(encoding="utf-8")
        if "park_core" in text or "noggin.park_plan" in text or "noggin import park_plan" in text:
            hits.append(p.relative_to(ROOT).as_posix())
    # redzone/lab/gym2/qdata.py: research only (A-02 decisive-Q dataset); nothing outside lab/gym2 imports gym2
    # (tests/test_gym2_parity.py::test_the_show_never_imports_gym2), so it cannot change the app's decisions
    assert sorted(hits) == ["redzone/lab/gym2/qdata.py", "redzone/noggin/park_facts.py", "redzone/noggin/park_show.py",
                            "redzone/park_run.py", "redzone/showboard.py"]
    engine = (ROOT / "redzone" / "engine.py").read_text(encoding="utf-8")
    assert engine.count("import ParkRun") == 1 and "\n        from redzone.park_run import ParkRun\n" in engine   # inside the loop, never at the top
