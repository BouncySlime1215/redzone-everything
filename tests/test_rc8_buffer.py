"""[RC8] buffer-ON release: per-league flag (default OFF), adaptive buffer, gate report, flip/rollback."""
from __future__ import annotations

import json
import math
from types import SimpleNamespace

import pytest

from redzone import buffer_rc8 as R
from redzone import config
from redzone.config import Config, ConfigError
from redzone.models import League, Phase


@pytest.fixture
def cfg(tmp_path):
    return Config(tmp_path / "config.yaml", tmp_path / "hist")


# ------------------------------------------------------------------------------------------ shipped defaults
def test_rc8_ships_off_everywhere(cfg):
    assert cfg.get(R.RC8_FLAG) is False
    assert not config.any_league(cfg, R.RC8_FLAG)
    for lg in League:
        assert not R.is_on(cfg, lg.value), lg
        assert not config.for_league(cfg, R.RC8_FLAG, lg.value, False), lg
    assert R.validate_scope(cfg) == []


def test_rc8_leaves_the_rc6_hold_alone(cfg):
    """The hidden-stick hold that RC6.2 already runs for NBA/NFL is not RC8's flag and is unchanged."""
    assert config.for_league(cfg, "timing.behind_live_s.enabled", "nba", False) is True
    assert config.for_league(cfg, "timing.behind_live_s.enabled", "nfl", False) is True
    assert config.for_league(cfg, "timing.behind_live_s.enabled", "nhl", False) is False


# ------------------------------------------------------------------------------------------ the formula
def test_formula_sum_floor_cap():
    assert R.adaptive_buffer_s("nba", 40, 2, 6, 5) == 53.0
    assert R.adaptive_buffer_s("nba", 0, 0, 0, 0) == 15.0                       # never 0: hard floor (v28: 15)
    assert R.adaptive_buffer_s("nba", 0, 0, 0, 0, floor_s=10) == 10.0
    assert R.adaptive_buffer_s("nba", 200, 2, 6, 5) == 90.0                     # Nick's cap
    assert R.adaptive_buffer_s("nba", 200, 2, 6, 5, cap_s=500) == 90.0          # the cap can't be raised
    assert R.adaptive_buffer_s("nba", 70, 2, 6, 5, stream_delay_credit_s=26) == 57.0


def test_ratchet_never_below_approved():
    assert R.adaptive_buffer_s("nba", 40, 2, 6, 5, approved_min_s=75) == 75.0
    assert R.adaptive_buffer_s("nba", 80, 2, 6, 5, approved_min_s=75) == 90.0   # going up is always allowed


@pytest.mark.parametrize("bad", [math.nan, -1, "x", None, math.inf])
def test_formula_rejects_bad_inputs(bad):
    with pytest.raises(ValueError):
        R.adaptive_buffer_s("nba", bad, 2, 6, 5)


@pytest.mark.parametrize("floor", [0, 9.9, 15.1, 30])
def test_floor_outside_10_15_is_refused(floor, cfg):
    with pytest.raises(ValueError):
        R.adaptive_buffer_s("nba", 40, 2, 6, 5, floor_s=floor)
    with pytest.raises(ConfigError):
        cfg.update({"timing": {"behind_live_s": {"adaptive": {"floor_s": floor}}}})


def test_buffer_for_reads_config_and_ratchets_on_by_league(cfg):
    assert cfg.get("timing.behind_live_s.adaptive.floor_s") == 15
    b, p = R.buffer_for(cfg, "nfl")
    assert p["formula_s"] == 75.0 and b == 75.0 and p["ratchet_source"] == "live_hold"
    b, p = R.buffer_for(cfg, "nhl")          # NHL holds nothing today: no ratchet base, the formula
    assert b == 88.0 and p["ratchet_source"] == "none"
    b, p = R.buffer_for(cfg, "nba")          # formula 83 < the live hold 90: no ratchet down without a locked gate
    assert p["formula_s"] == 83.0 and b == 90.0
    cfg.update({"timing": {"behind_live_s": {"adaptive": {"approved_min_s": {"nba": 60},
                                                          "feed_lag_p95_s": {"nba": 40}}}}})
    b, p = R.buffer_for(cfg, "nba")          # approved 60, but one release steps down at most 15 s: 90 -> 75
    assert p["formula_s"] == 53.0 and b == 75.0 and p["ratchet_source"] == "locked_gate"
    for lg in League:
        b, _ = R.buffer_for(cfg, lg.value)
        assert 10.0 <= b <= 90.0, lg


def test_buffer_for_pin(cfg):
    cfg.update({"league_overrides": R.flip(cfg.overrides, "nhl", 45)})
    b, p = R.buffer_for(cfg, "nhl")
    assert b == 45.0 and p["pinned_s"] == 45.0


# ------------------------------------------------------------------------------------------ flip / rollback
def test_flip_touches_one_league_and_rollback_survives_a_merge(cfg):
    before = json.loads(json.dumps(cfg.data["league_overrides"]))
    src = {"league_overrides": {"nba": {"decider.entry.landing_lag_s": 7}}}
    new = R.flip(src, "nba", 60)
    assert src == {"league_overrides": {"nba": {"decider.entry.landing_lag_s": 7}}}   # pure
    assert new["nba"] == {"decider.entry.landing_lag_s": 7, R.RC8_FLAG: True, R.RC8_FIXED: 60.0,
                          "timing.behind_live_s.enabled": True}   # start_timed only via --set, per the verdict
    cfg.update({"league_overrides": R.flip(cfg.overrides, "nba")})
    assert R.is_on(cfg, "nba") and not R.is_on(cfg, "nfl") and not R.is_on(cfg, "nhl")
    assert cfg.get(R.RC8_FLAG) is False                                         # never a global key
    cfg.update({"league_overrides": R.rollback(cfg.overrides, "nba")})      # deep merge: explicit false wins
    assert not R.is_on(cfg, "nba")
    assert config.for_league(cfg, "decider.entry.start_timed", "nba") is False   # restored to shipped
    after = cfg.data["league_overrides"]
    for lg, blk in before.items():
        for k, v in blk.items():
            assert after[lg][k] == v, (lg, k)


def test_flip_refuses_bad_seconds():
    for s in (0, -5, 91, math.nan):
        with pytest.raises(ValueError):
            R.flip({}, "nba", s)


def test_flip_cli_dry_run_sends_only_league_keys(tmp_path, capsys):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("rc8_flip", Path(__file__).parents[1] / "scripts" / "rc8_flip.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.main(["nhl", "on", "--dry-run", "--config", str(tmp_path / "c.yaml")]) == 0
    out = json.loads(capsys.readouterr().out)
    assert set(out["change"]) == {"league_overrides"} and set(out["change"]["league_overrides"]) == {"nhl"}
    assert out["buffer_s"] == 88.0
    assert m.main(["nhl", "on", "--offline", "--config", str(tmp_path / "c.yaml")]) == 0
    capsys.readouterr()
    assert R.is_on(Config(tmp_path / "c.yaml", tmp_path / "h"), "nhl")
    assert m.main(["nhl", "off", "--offline", "--config", str(tmp_path / "c.yaml")]) == 0
    assert not R.is_on(Config(tmp_path / "c.yaml", tmp_path / "h"), "nhl")


# ------------------------------------------------------------------------------------------ the engine
def _engine_stub(cfg, league):
    from redzone.engine import Engine
    g = SimpleNamespace(phase=Phase.LIVE, league=league)
    stub = SimpleNamespace(cfg=cfg, merger=SimpleNamespace(games={"g": g}), _game_on=lambda t, f: "g")
    return lambda: Engine._behind_of(stub, "yttv:x")


def test_engine_behind_of_follows_the_flip(cfg):
    nhl = _engine_stub(cfg, League.NHL)
    nba = _engine_stub(cfg, League.NBA)
    assert nhl() == 0.0                      # NHL: live (no hold) until flipped
    assert nba() == 90.0                     # NBA: RC6.2's fixed hold, untouched
    cfg.update({"league_overrides": R.flip(cfg.overrides, "nhl", 60)})
    assert nhl() == 60.0
    cfg.update({"league_overrides": R.rollback(cfg.overrides, "nhl")})
    assert nhl() == 0.0


def test_controller_gate_opens_for_an_rc8_only_league(cfg):
    """With the RC6 hold off everywhere and only RC8 on for one league, the controller still holds."""
    from redzone.control import Controller
    cfg.update({"league_overrides": {"nba": {"timing.behind_live_s.enabled": False},
                                     "nfl": {"timing.behind_live_s.enabled": False}}})
    assert not config.any_league(cfg, "timing.behind_live_s.enabled")
    cfg.update({"league_overrides": R.flip(cfg.overrides, "nhl", 50)})
    c = SimpleNamespace(cfg=cfg, _ref=lambda t: ("youtubetv", "x"), prime_mv=None, behind_of=lambda t: 50.0)
    stick = SimpleNamespace(host="h")
    assert Controller._buffer_for(c, stick, "yttv:x", SimpleNamespace(path="tune")) == 50.0


# ------------------------------------------------------------------------------------------ the gate
def _runs(mid=0, nights=9, harnesses=("honest", "resync")):
    return [{"b": 75, "arm": "main", "harness": h, "seed": s, "nights": nights, "entries_mid_play": mid,
             "clock_truth_live": 0, "first_pick_mid_play": 0} for h in harnesses for s in range(3)]


def test_gate_pending_without_inputs():
    st, d = R.gate_league("nba", None, None)
    assert st == R.PENDING and all(i["state"] == R.PENDING for i in d["items"].values())


def test_gate_any_mid_play_fails():
    st, _ = R.gate_league("nba", _runs(mid=1), [{"league": "nba", "label": "dead"}], sweep={"verdict": "ON"})
    assert st == R.FAIL
    st, _ = R.gate_league("nba", _runs(), [{"league": "nba", "label": "dead", "clock_truth": "live"}])
    assert st == R.FAIL


def test_gate_shadow_is_veto_only_and_a_clean_league_can_pass():
    sweep = {"verdict": "ON", "chosen_b": 75, "fit_nights": 9}
    st, d = R.gate_league("nba", _runs(), [{"league": "nba", "label": "dead"}], sweep=sweep,
                          sync={"p95_err_s": 1.0, "n": 300},
                          locked={"entries_mid_play": 0, "nights": 8, "decisions": 80})
    assert d["items"]["3_shadow"]["state"] == R.PASS     # "no veto"; the rates come from the sweep (item 2)
    assert st == R.PASS
    st, d = R.gate_league("nba", _runs(), [{"league": "nba", "label": "ambiguous"}], sweep=sweep,
                          sync={"p95_err_s": 1.0, "n": 300},
                          locked={"entries_mid_play": 0, "nights": 8, "decisions": 80})
    assert d["items"]["3_shadow"]["state"] == R.PENDING and st == R.PENDING   # nothing graded: no read


def test_gate_thin_sweep_is_pending_not_pass():
    st, d = R.gate_league("nba", _runs(nights=5), [], sweep={"verdict": "ON", "fit_nights": 5})
    assert d["items"]["2_beats_live"]["state"] == R.PENDING
    st, d = R.gate_league("nba", _runs(harnesses=("honest",)), [{"league": "nba", "label": "dead"}])
    assert d["items"]["1_rule1"]["state"] == R.PENDING


def test_gate_report_runs_with_and_without_inputs(tmp_path, cfg):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("rc8_gate_report",
                                                  Path(__file__).parents[1] / "scripts" / "rc8_gate_report.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    r = m.report(cfg, None, None)
    assert all(d["state"] == R.PENDING for d in r["leagues"].values())
    sweep = {"code_sha": "abc", "leagues": {"nba": {"verdict": "ON", "chosen_b": 75, "needs": ["start_timed"],
                                                    "fit_nights": 9, "runs": [dict(r, arm="st") for r in _runs()] + _runs(mid=1) + [
                                                        dict(_runs(mid=2)[0], b=45)]}}}
    shadow = {"rows": 10, "S1": "PASS", "S2": "PASS",
              "entry_grades": [{"league": "nba", "cutgrade": "dead", "clock_truth": "dead"}]}
    r = m.report(cfg, sweep, shadow)
    nba = r["leagues"]["nba"]
    assert nba["items"]["1_rule1"]["state"] == R.PASS          # 0 at the shipped B (75, st arm it needs)
    assert nba["grid_mid_play_total"] == 6 + 2                    # the whole grid is still shown
    # gate v28: B = max(sweep 75, formula/ratchet/floor 90)
    assert nba["ship_b"] == 90.0
    assert nba["flip_cmd"] == "python scripts/rc8_flip.py nba on --seconds 90 --set decider.entry.start_timed=true"
    assert nba["state"] == R.PENDING                            # locked + one-night shadow still pending
    bad = dict(shadow, S2="FAIL")
    assert m.report(cfg, sweep, bad)["leagues"]["nba"]["state"] == R.FAIL
    assert "| NBA |" in m.to_md(r)


def test_ship_b_max_rule_and_back_up_after_mid_play(cfg):
    assert R.ship_b(cfg, "nfl", 90)[0] == 90.0      # sweep above the formula
    assert R.ship_b(cfg, "nfl", 45)[0] == 75.0      # formula above the sweep
    assert R.ship_b(cfg, "nhl", 60, mid_play_night=True)[0] == 90.0
    assert R.ship_b(cfg, "nhl", None)[0] == 88.0
    with pytest.raises(ConfigError):
        cfg.update({"timing": {"behind_live_s": {"adaptive": {"ratchet_step": 30}}}})


def test_first_picks_are_their_own_rule1_line():
    runs = [dict(r, first_pick_mid_play=1 if i == 0 else 0) for i, r in enumerate(_runs())]
    st, d = R.gate_league("nba", runs, [{"league": "nba", "label": "dead"}])
    assert st == R.FAIL and d["items"]["1_rule1"]["first_pick_mid_play"] == 1


def test_missing_first_pick_field_is_pending_never_pass():
    runs = [{k: v for k, v in r.items() if k != "first_pick_mid_play"} for r in _runs()]
    st, d = R.gate_league("nba", runs, [{"league": "nba", "label": "dead"}])
    assert d["items"]["1_rule1"]["state"] == R.PENDING and d["items"]["1_rule1"]["first_pick_mid_play"] is None


def test_flip_sets_the_hold_and_rollback_restores_shipped(cfg):
    shipped = config.for_league(cfg, "timing.behind_live_s.enabled", "nhl", False)
    cfg.update({"league_overrides": R.flip(cfg.overrides, "nhl")})
    assert config.for_league(cfg, "timing.behind_live_s.enabled", "nhl") is True
    cfg.update({"league_overrides": R.rollback(cfg.overrides, "nhl")})
    assert config.for_league(cfg, "timing.behind_live_s.enabled", "nhl", False) == shipped
