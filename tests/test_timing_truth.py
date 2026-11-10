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
    assert old.prep_s == C.Route.prep_s == 15.0 and old.flip_s == 3.2
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


# ----- the hidden stick is busy while it loads (ready_at) ----------------------------------------------------------------------------

def _one_piece_ended(d, m, at):
    """Clip m aired whole as one piece and just ended on screen at `at`: the hidden stick now re-seeks for whatever is next."""
    from collections import Counter
    d.arbiter_log, d.cfg.v["arbiter.plan_clip_s"] = Counter(), 30.0
    m.piece_s, m.offset_s = 30.0, 0.0
    d._clip, d._current = m, "b"
    d.screen_m.to("replaying", "t", now=at - 30.0)
    d.catchup_m.to("airing", "t", now=at - 30.0)
    assert not d._route_piece_ended(at)
    d._current = "on"                                              # the picture is back on the live game


def test_the_route_waits_for_the_hidden_stick_after_a_piece():
    d, done, nxt = route_decider(), miss(), miss(1050.0, "p2")
    _one_piece_ended(d, done, 1080.0)
    d.queue = [nxt]
    assert d._catchup_route(CANDS, 1085.0, P) is None             # 5 s into a 15 s load: the stick is not on the clip yet
    assert d._catchup_route(CANDS, 1095.0, P) is nxt


def test_the_route_waits_for_the_hidden_stick_after_a_cut():
    d, m = route_decider(), miss()
    d.queue, d._route_stick_at = [m], 1030.0                       # a cut at 1030 took the hidden stick
    assert d._catchup_route(CANDS, 1040.0, P) is None
    assert d._catchup_route(CANDS, 1045.0, P) is m
    d, m = route_decider(**{"timing.measured": True}), miss()
    d.queue, d._route_stick_at = [m], 1030.0
    assert d._catchup_route(CANDS, 1045.0, P) is None and d._catchup_route(CANDS, 1049.0, P) is m     # the measured 19 s load


def test_an_interrupted_piece_also_takes_the_stick():
    d, m = route_decider(), miss()
    d._clock = lambda: 1060.0
    d.replaying, d._clip, m.piece_s = True, m, 12.0
    d.screen_m.to("replaying", "t", now=1050.0)
    d.catchup_m.to("airing", "t", now=1050.0)
    d.abort_replay()
    assert d._route_stick_at == 1060.0


# ----- flag off: a whole small night decides exactly as before ----------------------------------------------------------------------

def _night_decisions(**sets):
    import json

    from redzone.config import Config, _deep_merge
    from redzone.lab import backtest
    from redzone.lab.backtest import Replay
    from redzone.models import League
    fix = Path(__file__).parent / "fixtures" / "your_team_crunch"         # the NBA night tests/test_hidden_stick.py replays
    evs = json.loads((fix / "espn_nba_scoreboard_20251212.json").read_text())["events"]
    reps = [r for r in (Replay.load(League.NBA, fix / f"espn_nba_summary_{e['id']}.json") for e in evs) if r]
    targets = {r.gid: ("prime:" if i % 3 == 0 else "youtubetv:") + r.gid for i, r in enumerate(reps)}
    cfg = Config(user_path=fix / "eval_config.yaml")
    for k, v in sets.items():
        node = v
        for part in reversed(k.split(".")):
            node = {part: node}
        cfg.data = _deep_merge(cfg.data, node)
    rep = backtest.run(reps, cfg, {"WSH"}, targets=targets, seed=1, **backtest.feed_lag_knobs(70.0, 90.0))
    return [repr(d) for d in rep.decisions], [vars(s) for s in rep.switches]


def test_flag_off_a_night_decides_as_before_the_change(monkeypatch):
    """After: as shipped. Before: the new bookkeeping inert (the stick time is never written) and the measured reader never called."""
    from redzone.decider.decider import Decider
    after = _night_decisions()
    assert len(after[0]) > 500 and after[1]
    assert _night_decisions(**{"timing.measured": True}) == after           # without the route, the measured timings change nothing
    monkeypatch.setattr(mt, "route_costs", lambda cfg: (_ for _ in ()).throw(AssertionError("read with the route off")))
    monkeypatch.setattr(Decider, "_route_stick_at", property(lambda self: float("-inf"), lambda self, v: None), raising=False)
    assert _night_decisions() == after
