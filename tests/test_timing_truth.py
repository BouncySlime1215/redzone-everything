"""[timing.measured, off] One source for the TV's measured stick timings; the catch-up route reads them only with the flag on.

Why: the route planned a 3.2 s flip and a 15 s clip load; Nick's TV measured 1.9 s (max 2.2) and 18-25 s on Nov 9 (small samples,
busy Mac). Flag off, every cost is exactly what it was.
"""
from pathlib import Path

import yaml

from redzone import measured_timing as mt
from redzone.decider import buffered as bj
from redzone.noggin import arbiter as A
from redzone.noggin import catchup as C
from tests.test_exits_catchup import CANDS, miss, route_decider

CONFIG = yaml.safe_load((Path(mt.__file__).parent / "default_config.yaml").read_text())
P = A.Params(catchup_route=True, catchup_route_reach_s=170.0)


def test_the_flag_ships_off_with_the_measured_values():
    t = CONFIG["timing"]
    assert t["measured"] is False
    assert {k: float(t[k]) for k in mt.MEASURED} == mt.MEASURED == {
        "flip_s": 1.9, "flip_max_s": 2.2, "tune_s": 3.0, "tune_max_s": 4.3, "clip_prep_s": 19.0, "clip_prep_max_s": 25.0,
        "seek_base_s": 28.7, "seek_per_press_s": 16.5, "reach_s": 175.0}


def test_flag_off_the_route_costs_are_todays():
    d = route_decider()
    old = C.Route(reach_s=min(P.catchup_route_reach_s, C.HARD_CAP_S), clip_s=P.plan_clip_s, flip_s=float(bj.cfg_get(d.cfg, "flip_s")),
                  short=P.catchup_route_short)       # what _catchup_route_params built before TIMING-TRUTH
    assert d._catchup_route_params(P) == old
    assert old.prep_s == mt.ASSUMED_CLIP_PREP_S == 15.0 and old.flip_s == 3.2
    assert mt.route_costs(d.cfg) is None and route_decider(**{"timing.measured": False})._catchup_route_params(P) == old


def test_flag_on_the_route_reads_the_measured_timings():
    r = route_decider(**{"timing.measured": True})._catchup_route_params(P)
    assert (r.flip_s, r.prep_s, r.reach_s, r.clip_s) == (2.2, 19.0, 175.0, P.plan_clip_s)


def test_flag_on_a_config_value_wins_and_the_cap_still_holds():
    d = route_decider(**{"timing.measured": True, "timing.clip_prep_s": 25.0, "timing.reach_s": 400, "timing.flip_max_s": 3.0})
    r = d._catchup_route_params(P)
    assert (r.flip_s, r.prep_s, r.reach_s) == (3.0, 25.0, C.HARD_CAP_S)


def test_flag_on_a_slower_clip_load_holds_a_fresh_miss_back():
    d, m = route_decider(), miss()
    d.queue = [m]
    assert d._catchup_route(CANDS, 1016.0, P) is m                  # 15 s after it aired: loaded with today's 15 s
    d, m = route_decider(**{"timing.measured": True}), miss()
    d.queue = [m]
    assert d._catchup_route(CANDS, 1016.0, P) is None               # the measured 19 s load is not done yet
    assert d._catchup_route(CANDS, 1019.0, P) is m


def test_flag_on_the_parkable_reach_is_the_measured_one():
    from types import SimpleNamespace as NS
    base = {"replay.scrub_max_play_age_s": 95, "replay.yttv_seek": "scrub", "arbiter.catchup_route_reach_s": 95}
    for kw, ok in (({}, False), ({"timing.measured": True}, True)):
        d = route_decider(**base, **kw)
        d.target_of = lambda gid: "youtubetv:x"
        assert d._parkable(NS(game_id="b", t=1000.0), 1150.0) is ok           # 150 s old: past 95, inside the measured 175
