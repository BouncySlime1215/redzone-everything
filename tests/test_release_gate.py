"""The release gate: pass/fail/pending logic (scripts/release_gate.py)."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("release_gate", Path(__file__).parent.parent / "scripts/release_gate.py")
rg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rg)


def _card(**kw):
    m = {"eval_data": "eval-data-v1", "split": "test", "mid_play_cuts": 0, "entries": 100, "entries_mid_play": 0,
         "big_seen_pct": 99.0, "your_team_crunch_live_pct": 100.0, "crunch_live_pct": 96.0}
    m.update(kw)
    return {"metrics": m}


def _status(rows, gate, scope="nhl"):
    return next(r[2] for r in rows if r[0] == gate and r[1] == scope)


def test_clean_card_passes_and_system_gates_stay_pending():
    rows = rg.gates({"nhl": _card()})
    assert all(r[2] == rg.PASS for r in rows if r[1] == "nhl")
    assert all(r[2] == rg.PENDING for r in rows if r[1] == "system")
    # every other league of Nick's six reads PENDING ("not measured"), never absent
    for lg in ("nfl", "cfb", "nba", "cbb", "mlb"):
        assert _status(rows, "league scorecard present", lg) == rg.PENDING
        assert _status(rows, "entries mid-play = 0", lg) == rg.PENDING


def test_cuts_dead_ball_and_rise_above_today_fail():
    base = {"cards": {"nhl": _card(entries_mid_play=2)}}
    rows = rg.gates({"nhl": _card(mid_play_cuts=1, entries_mid_play=6)}, base)
    assert _status(rows, "mid-play cuts = 0") == rg.FAIL
    assert _status(rows, "entries at a dead ball >= 95%") == rg.FAIL
    assert _status(rows, "entries mid-play <= 5%") == rg.FAIL
    assert _status(rows, "entries mid-play never above today") == rg.FAIL


def test_crunch_measured_under_the_bar_fails_and_only_zero_plays_is_pending():
    """F5/R9: a measured 90% (or a real 0% of your-team crunch) FAILS; PENDING only when n == 0."""
    rows = rg.gates({"nhl": _card(crunch_live_pct=90.0, crunch_plays=40, your_team_crunch_live_pct=0.0,
                                  your_team_crunch_live="0/4")})
    assert _status(rows, "crunch live >= 95% or feasible ceiling") == rg.FAIL
    assert _status(rows, "your-team crunch live = 100%") == rg.FAIL          # a REAL 0/4
    rows = rg.gates({"nhl": _card(crunch_live_pct=0.0, crunch_plays=0, your_team_crunch_live_pct=0.0,
                                  your_team_crunch_live="0/0")})
    assert _status(rows, "crunch live >= 95% or feasible ceiling") == rg.PENDING
    assert _status(rows, "your-team crunch live = 100%") == rg.PENDING      # 0/0: nothing to grade
    assert _status(rg.gates({"nhl": _card(your_team_crunch_live_pct=93.0)}), "your-team crunch live = 100%") == rg.FAIL
    # a computed feasible ceiling can explain a miss
    ok = rg.gates({"nhl": _card(crunch_live_pct=90.0, crunch_plays=40, crunch_live_ceiling_pct=90.0)})
    assert _status(ok, "crunch live >= 95% or feasible ceiling") == rg.PASS


def test_per_night_card_with_one_mid_play_entry_fails_rule_one():
    """R1/F3: the per-night path used to let up to 5% mid-play through (1 in 300 PASSed on the 95% row)."""
    per = [{"night": str(i), "entries": 30, "entries_mid_play": int(i == 0)} for i in range(10)]
    m = {"per_night": per, "entries": 300, "entries_mid_play": 1}
    rows = rg.gates({"nfl": _card(**m)})
    assert _status(rows, "entries mid-play = 0", "nfl") == rg.FAIL
    assert _status(rg.gates({"nfl": _card(per_night=[{**x, "entries_mid_play": 0} for x in per], entries=300,
                                          entries_mid_play=0)}), "entries mid-play = 0", "nfl") == rg.PASS


def test_a_card_not_graded_on_the_locked_split_fails():
    """F4: `--split all` (train nights included) must not be able to PASS the gate."""
    assert _status(rg.gates({"nhl": _card(split="all")}), "graded on the locked split") == rg.FAIL
    assert _status(rg.gates({"nhl": _card(split=None)}), "graded on the locked split") == rg.FAIL
    assert _status(rg.gates({"nhl": _card()}), "graded on the locked split") == rg.PASS
    # the slice header of ci_scorecard names the split when the card doesn't
    nosplit = {"metrics": {k: v for k, v in _card()["metrics"].items() if k != "split"}}
    assert _status(rg.gates({"nhl": nosplit}, None, {"split": "test"}), "graded on the locked split") == rg.PASS
    assert _status(rg.gates({"nhl": nosplit}, None, {"split": "all"}), "graded on the locked split") == rg.FAIL


def test_commercial_and_dead_air_rows():
    """R9: mid-commercial entries and dead air are gated (Nick's pass bar: 0 commercial seconds)."""
    per = [{"night": str(i), "entries": 30, "entries_mid_play": 0, "entries_mid_break": 1, "dead_s": 50.0,
            "watched_s": 1000.0} for i in range(10)]
    rows = rg.gates({"nfl": _card(per_night=per, entries=300)})
    assert _status(rows, "entries mid-commercial = 0", "nfl") == rg.FAIL
    assert _status(rows, "dead air (commercials on screen) = 0", "nfl") == rg.FAIL
    clean = [{**x, "entries_mid_break": 0, "dead_s": 0.0} for x in per]
    rows = rg.gates({"nfl": _card(per_night=clean, entries=300)})
    assert _status(rows, "entries mid-commercial = 0", "nfl") == rg.PASS
    assert _status(rows, "dead air (commercials on screen) = 0", "nfl") == rg.PASS
    old = [{k: v for k, v in x.items() if k not in ("entries_mid_break", "dead_s", "watched_s")} for x in per]
    rows = rg.gates({"nfl": _card(per_night=old, entries=300)})
    assert _status(rows, "entries mid-commercial = 0", "nfl") == rg.PENDING


def test_promotion_checks_leaves_and_missing_lines():
    """R10: leaves count, and a line the candidate dropped is PENDING, not skipped."""
    live = {"nfl": _card(big_live_pct=30.0, dead_air_pct=10.0, mid_play_cuts=0)}
    def pro(**kw):
        c = {"nfl": _card(big_live_pct=40.0, dead_air_pct=9.0, **kw)}
        return rg.league_promotion({"dev": live, "locked": live}, {"dev": c, "locked": c}, "nfl")
    assert pro(mid_play_cuts=0)[0] == rg.PASS
    assert pro(mid_play_cuts=1)[0] == rg.FAIL
    c = {"nfl": _card(big_live_pct=40.0, mid_play_cuts=0)}          # dead_air_pct dropped
    assert rg.league_promotion({"dev": live, "locked": live}, {"dev": c, "locked": c}, "nfl")[0] == rg.PENDING
    c = {"nfl": {"metrics": {k: v for k, v in _card(big_live_pct=40.0, dead_air_pct=9.0)["metrics"].items()
                             if k != "mid_play_cuts"}}}
    assert rg.league_promotion({"dev": live, "locked": live}, {"dev": c, "locked": c}, "nfl")[0] == rg.PENDING


def test_the_baseline_ceiling_is_rule_one_not_the_old_nba_10_of_20():
    import json
    base = json.loads((Path(__file__).parent.parent / "docs/gate_baseline.json").read_text())
    for name, card in base["cards"].items():
        assert _entries_mid(card) == 0, name
    rows = rg.gates({"nba": _card(entries=20, entries_mid_play=10, mid_play_cuts=0)}, base)
    assert _status(rows, "entries mid-play never above today", "nba") == rg.FAIL


def _entries_mid(card):
    return rg._entries(rg._m(card))[1]


def test_unpinned_data_and_crashed_scorecard_fail():
    rows = rg.gates({"nhl": _card(eval_data="UNPINNED: x"), "nba": {"error": "exit 1"}})
    assert _status(rows, "pinned data") == rg.FAIL
    assert _status(rows, "scorecard ran", "nba") == rg.FAIL


def test_demo_card_reads_its_own_shapes():
    demo = {"metrics": {"eval_data": "eval-data-v1", "mid_play_cuts": 0,
                        "cut_timing": {"entries": 200, "entries_mid_play": 4},
                        "nfl_big_plays": {"seen": "137/137 (100%)"}, "nhl_goals": {"seen": "35/36 (97%)"},
                        "crunch_live": "104/137 (76%)"}}
    rows = rg.gates({"demo_20261101": demo})
    assert _status(rows, "entries at a dead ball >= 95%", "demo_20261101") == rg.PASS
    assert _status(rows, "big plays seen >= 95% (NHL)", "demo_20261101") == rg.PASS
    assert _status(rows, "crunch live >= 95% or feasible ceiling", "demo_20261101") == rg.FAIL   # 76% measured
