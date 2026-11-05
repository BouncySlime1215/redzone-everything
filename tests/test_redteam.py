"""[EXP-032] The red-team search: planted bad stress configs must be recovered within the budget.

Pre-registered bar (a), docs/prereg/exp-032.md: on the PLANTED set below, search() with
BUDGET evaluations and search seed 0 recovers 100 % of them. No archive needed.
"""
from __future__ import annotations

import math

import pytest

from redzone.lab import redteam as R

BUDGET = 120   # pre-registered evaluation budget for bar (a)

# Planted known-bad regions, as boxes in unit knob space: {axis: (lo, hi)}. "soft" plants also give a
# near-miss signal (plays missed on entry grow as the mix gets closer), the way a real backtest drifts
# toward a failure; "hard" plants give nothing until the mix is inside.
PLANTED = [
    ("screen_far_behind", {"true_delay": (0.9, 1.0)}, False),
    ("tune_jitter_max", {"tune_jitter": (0.85, 1.0)}, False),
    ("drop_and_late", {"drop": (0.7, 1.0), "late_plays": (0.7, 1.0)}, False),
    ("ahead_and_jittery", {"true_delay": (0.1, 0.35), "lag_span": (0.5, 0.8)}, False),
    ("tight_sigma_no_wall", {"sigma": (0.0, 0.25), "no_wallclock": (0.4, 0.65)}, False),
    ("narrow_three_axis", {"true_delay": (0.55, 0.75), "drop": (0.3, 0.5), "lag_lo": (0.6, 0.8)}, True),
    ("narrow_late_wall", {"late_plays": (0.2, 0.35), "no_wallclock": (0.75, 0.9), "sigma": (0.5, 0.7)}, True),
]
NAMES = [n for n, _, _ in PLANTED]
IDX = {n: i for i, (n, _, _, _) in enumerate(R.AXES)}


def planted(box: dict, soft: bool, calls: list | None = None):
    def evaluate(k: R.Knobs) -> R.Outcome:
        if calls is not None:
            calls.append(k)
        u = k.unit()
        inside = all(lo <= u[IDX[a]] <= hi for a, (lo, hi) in box.items())
        if inside:
            return R.Outcome(entries_mid_play=1, entries=10)
        if not soft:
            return R.Outcome(entries=10)
        gap = math.sqrt(sum(max(0.0, lo - u[IDX[a]], u[IDX[a]] - hi) ** 2 for a, (lo, hi) in box.items()))
        return R.Outcome(plays_missed_entry=int(round(20 * max(0.0, 1.0 - gap))), entries=10)
    return evaluate


@pytest.mark.parametrize("name,box,soft", PLANTED, ids=NAMES)
def test_search_recovers_every_planted_config_within_budget(name, box, soft):
    calls: list = []
    res = R.search(planted(box, soft, calls), BUDGET, seed=0)
    assert len(calls) == BUDGET and len(res.trials) == BUDGET      # the budget is spent exactly
    assert res.first_hit is not None, f"{name} not recovered in {BUDGET} evaluations"


def test_recovery_rate_is_100_pct():
    found = sum(R.search(planted(b, s), BUDGET, seed=0).first_hit is not None for _, b, s in PLANTED)
    assert found == len(PLANTED)


def test_null_when_nothing_breaks_spends_the_whole_budget():
    res = R.search(lambda k: R.Outcome(entries=5), 40, seed=3)
    assert res.first_hit is None and res.hits == [] and len(res.trials) == 40


def test_search_is_deterministic_given_seed():
    ev = planted({"drop": (0.5, 0.6)}, True)
    a = [t.knobs for t in R.search(ev, 50, seed=7).trials]
    b = [t.knobs for t in R.search(ev, 50, seed=7).trials]
    assert a == b


def test_edges_phase_tries_base_each_axis_max_and_corner():
    res = R.search(lambda k: R.Outcome(), 30, seed=0)
    edges = [t.knobs for t in res.trials if t.phase == "edges"]
    assert len(edges) == len(R.AXES) + 2
    assert edges[0].unit() == R.Knobs.base().unit()
    assert edges[-1].unit() == [1.0] * len(R.AXES)


def test_knobs_map_to_backtest_kwargs():
    assert R.Knobs().run_kwargs() == dict(true_delay=35.0, sigma=3.0, lag_jitter=None, drop=0.0, late_plays=0.0,
                                          no_wallclock=0.0, tune_jitter_s=0.0, seed=0)
    kw = R.FIXED_GRID["combined_faults_s1"].run_kwargs()
    assert kw["lag_jitter"] == (3.0, 25.0) and kw["true_delay"] == 41.0 and kw["drop"] == 0.1 and kw["seed"] == 1
    k = R.Knobs(true_delay=41.0, drop=0.2, seed=5)
    assert R.Knobs.from_unit(k.unit(), 5) == k


def test_reproduce_needs_the_same_result_every_rerun():
    assert R.reproduce(lambda k: R.Outcome(entries_mid_play=1), R.Knobs(), 3)
    flip = iter([R.Outcome(entries_mid_play=1), R.Outcome(entries_mid_play=1), R.Outcome()])
    assert not R.reproduce(lambda k: next(flip), R.Knobs(), 3)
    assert not R.reproduce(lambda k: R.Outcome(), R.Knobs(), 3)   # not a failure at all


def test_novel_only_when_the_grid_left_that_line_clean():
    clean = {"a": R.Outcome(), "b": R.Outcome(cuts=2)}
    entry_grid = {"a": R.Outcome(entries_mid_play=1)}
    o = R.Outcome(entries_mid_play=1)
    assert R.novel(o, clean) and not R.novel(o, entry_grid)
    assert R.novel(R.Outcome(cuts_high=1), entry_grid)


def test_hunt_night_counts_every_evaluation_and_writes_golden_candidates():
    calls: list = []
    ev = planted({"true_delay": (0.9, 1.0)}, False, calls)
    out = R.hunt_night(ev, 30, seed=0, reruns=3, max_repro=2)
    assert out["evals_spent"] == len(calls)
    assert out["grid_breaks"] == []
    assert out["findings"] and all(f["reproducible"] and f["novel_vs_grid"] for f in out["findings"])
    g = R.golden_candidate("nba", "20261020", out["findings"][0])
    assert g["breaks"] == ["mid-play entry"] and g["must_reach"] == {"entries_mid_play": 0, "cuts_high": 0}


def test_dev_nights_refuse_the_locked_split(tmp_path):
    for night in ("20261001", "20261002", "20261003", "20261004", "20261005", "20261006", "20261007", "20261008"):
        (tmp_path / f"scoreboard_{night}.json").write_text('{"events": [{"id": "%s"}]}' % night)
    with pytest.raises(ValueError):
        R.dev_nights(tmp_path, "test", None)
    fit = R.dev_nights(tmp_path, "fit", None)
    assert not set(fit) & R.locked_nights(tmp_path, None) and len(fit) == 6


def test_bootstrap_ci_brackets_the_mean():
    lo, hi = R.bootstrap_ci([0, 1, 0, 1, 1, 0, 0, 1], seed=1)
    assert 0 <= lo <= 0.5 <= hi <= 1


def test_flag_is_off_by_default():
    assert R.ENABLED is False


def test_script_does_nothing_without_enable(capsys):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("rt_script", Path(__file__).parent.parent / "scripts/redteam.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main([]) == 0 and "OFF" in capsys.readouterr().out
    s = mod.summarize([{"evals_spent": 5, "grid_breaks": [], "search_hits": 1,
                        "findings": [{"reproducible": True, "novel_vs_grid": True}]}])
    assert s["novel_reproducible"]["status"] == "PENDING" and s["verdict_b"].startswith("found")
