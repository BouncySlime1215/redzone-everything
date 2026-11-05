"""[T9 tester, Nov 5 2026] RC8 "buffer ON" release gate (docs/plans/2026-11-05-rc8-buffer-gate.md).

What this file proves, per Nick's 9:10 AM decision and the coordinator's T9 orders:
1. the RC8 per-league flag ships OFF for every league;
2. the gate blocks any league with a mid-play entry in the sim (either harness) or in shadow;
3. flipping one league leaves every other league untouched, and rollback restores it exactly;
4. the adaptive buffer never goes under its 10-15 s floor nor over the 90 s cap;
5. (engine side, already on integration/next) the hold the engine asks for is per league and capped.

The RC8 module is the builder's (redzone/buffer_rc8.py, thread "Prepare the buffer-on release"). Until it lands,
the contract tests skip; the engine tests run now. The dry-run flip in the replay sim is
tests/test_t9_rc8_dryrun.py.
"""
from __future__ import annotations

import copy
import math
from types import SimpleNamespace

import pytest
import yaml

from redzone import config
from redzone.engine import Engine
from redzone.models import League, Phase

LEAGUES = ("nfl", "cfb", "nba", "cbb", "wcbb", "nhl", "mlb")
RC8_KEY = "timing.behind_live_s.rc8_on"


def _rc8():
    return pytest.importorskip("redzone.buffer_rc8", reason="redzone/buffer_rc8.py (T9 builder) not merged yet")


# --------------------------------------------------------------------------------------------- 1. defaults OFF
def test_rc8_flag_ships_off_for_every_league(cfg):
    """No league runs RC8 out of the box: not globally, not through league_overrides. Nick flips it."""
    assert not cfg.get(RC8_KEY, False)
    for lg in LEAGUES:
        assert config.for_league(cfg, RC8_KEY, lg, False) is False, lg
    assert not config.any_league(cfg, RC8_KEY)


def test_rc8_flag_is_a_real_default_once_the_module_lands(cfg):
    _rc8()
    assert cfg.get(RC8_KEY) is False, "rc8_on must be declared in default_config.yaml (an undeclared key is pruned)"
    assert float(cfg.get("timing.behind_live_s.max_s")) == 90.0


def test_no_override_in_the_shipped_defaults_turns_rc8_on():
    raw = yaml.safe_load((config.Path(config.__file__).parent / "default_config.yaml").read_text())
    for lg, keys in (raw.get("league_overrides") or {}).items():
        for k, v in (keys or {}).items():
            assert not (k == RC8_KEY and v), (lg, k)
            if k in ("timing.behind_live_s", "timing"):   # a whole-group override must not smuggle it in
                assert not (v or {}).get("rc8_on") and not ((v or {}).get("behind_live_s") or {}).get("rc8_on"), lg


# --------------------------------------------------------------------------------------------- 2. gate blocks
def _runs(mid=None, nights=8):
    """A clean sweep: both harnesses, seeds 0-2, >= 8 nights; `mid` = {(harness, seed): n mid-play entries}."""
    mid = mid or {}
    return [{"harness": h, "seed": s, "nights": nights, "entries": 120, "entries_mid_play": mid.get((h, s), 0)}
            for h in ("honest", "resync") for s in (0, 1, 2)]


def _shadow(mid_at=None, n=40, by="label"):
    """Shadow entries as scripts/buffer_shadow_grade.py grades them: label dead|live, clock_truth (NBA/CBB)."""
    rows = [{"league": "nba", "label": "dead", "clock_truth": "dead"} for _ in range(n)]
    if mid_at is not None:
        rows[mid_at][by] = "live"
    return rows


def _all_clean():
    """Every other item at its passing input: sweep ON on >= 8 fit nights, locked clean, sync within 2 s."""
    return dict(sweep={"verdict": "ON", "chosen_b": 75, "fit_nights": 10},
                locked={"entries_mid_play": 0, "nights": 8, "decisions": 120},
                sync={"p95_err_s": 1.2, "n": 300})


@pytest.mark.parametrize("harness", ["honest", "resync"])
@pytest.mark.parametrize("seed", [0, 1, 2])
def test_one_mid_play_entry_in_any_sim_run_fails_the_league(harness, seed):
    m = _rc8()
    state, detail = m.gate_league("nba", _runs({(harness, seed): 1}), _shadow())
    assert state == "FAIL", detail


@pytest.mark.parametrize("by", ["label", "clock_truth"])
def test_one_mid_play_entry_in_shadow_fails_the_league(by):
    m = _rc8()
    state, detail = m.gate_league("nba", _runs(), _shadow(mid_at=17, by=by), **_all_clean())
    assert state == "FAIL", detail


def test_clock_truth_mid_play_in_the_sim_fails_the_league():
    """NBA/CBB need 0 on both graders: a clock-truth live entry vetoes even with cutgrade at 0."""
    m = _rc8()
    runs = _runs()
    runs[4]["clock_truth_live"] = 1
    assert m.gate_league("nba", runs, _shadow(), **_all_clean())[0] == "FAIL"


def test_a_mid_play_locked_read_fails_the_league():
    m = _rc8()
    kw = _all_clean()
    kw["locked"]["entries_mid_play"] = 1
    assert m.gate_league("nba", _runs(), _shadow(), **kw)[0] == "FAIL"


def test_a_clean_league_can_pass():
    """Rule #1 clean on every run + one clean shadow night + sweep ON + locked + sync: the league is ready for
    Nick's flip. Gate doc item 3: a shadow night can only VETO, so a clean one must not block PASS forever."""
    m = _rc8()
    state, detail = m.gate_league("nba", _runs(), _shadow(), **_all_clean())
    assert state == "PASS", detail


def test_mid_play_fails_even_when_the_sample_is_thin():
    """A veto never waits for power: 1 mid-play on 2 nights is FAIL, not PENDING."""
    m = _rc8()
    assert m.gate_league("nhl", _runs({("honest", 0): 1}, nights=2), [])[0] == "FAIL"
    sh = [dict(r, league="nhl") for r in _shadow(mid_at=0)]
    assert m.gate_league("nhl", _runs(nights=2), sh)[0] == "FAIL"


def test_clean_but_thin_is_pending_never_pass():
    m = _rc8()
    assert m.gate_league("nba", _runs(nights=7), _shadow())[0] == "PENDING"
    assert m.gate_league("nba", _runs(), [])[0] == "PENDING"          # no shadow night yet
    assert m.gate_league("nba", [], _shadow())[0] == "PENDING"        # shadow alone never passes
    one_harness = [r for r in _runs() if r["harness"] == "honest"]
    assert m.gate_league("nba", one_harness, _shadow())[0] != "PASS"  # both harnesses, or no PASS


def test_a_run_missing_its_mid_play_count_cannot_pass():
    m = _rc8()
    runs = _runs()
    del runs[3]["entries_mid_play"]
    assert m.gate_league("nba", runs, _shadow())[0] != "PASS"


def test_gate_is_per_league_and_does_not_mutate_its_inputs():
    m = _rc8()
    runs, sh = _runs({("resync", 2): 3}), _shadow()
    snap = copy.deepcopy((runs, sh))
    assert m.gate_league("nba", runs, sh)[0] == "FAIL"
    assert (runs, sh) == snap


# --------------------------------------------------------------------------------------------- 3. flip + rollback
def _shipped_overrides(cfg) -> dict:
    return copy.deepcopy(cfg.get("league_overrides") or {})


def _effective(over: dict) -> dict:
    """league_overrides with the RC8 keys at their OFF values dropped: rollback may write rc8_on: false
    explicitly (it must, to survive Config.update's deep merge) and still equal the shipped set."""
    off = {RC8_KEY: False, "timing.behind_live_s.rc8_fixed_s": 0}
    out = {}
    for lg, keys in over.items():
        kept = {k: v for k, v in (keys or {}).items() if not (k in off and v == off[k])}
        if kept:
            out[lg] = kept
    return out


@pytest.mark.parametrize("league", ["nba", "nhl", "nfl", "cbb"])
def test_flipping_one_league_leaves_every_other_league_untouched(cfg, league):
    m = _rc8()
    before = _shipped_overrides(cfg)
    data = copy.deepcopy(cfg.data)
    after = m.flip(data, league, 75)
    assert data == cfg.data, "flip must be pure"
    for lg in set(before) | set(after):
        if lg != league:
            assert after.get(lg, {}) == before.get(lg, {}), lg
    assert after[league].get(RC8_KEY) is True
    # Everything the league already ran (RC6.2/RC7 graded set) is still there.
    for k, v in (before.get(league) or {}).items():
        assert after[league].get(k) == v, k


@pytest.mark.parametrize("league", ["nba", "nhl", "nfl", "cbb"])
def test_flip_then_rollback_restores_the_shipped_overrides_exactly(cfg, league):
    m = _rc8()
    before = _shipped_overrides(cfg)
    data = copy.deepcopy(cfg.data)
    data["league_overrides"] = m.flip(data, league, 60)
    restored = m.rollback(data, league)
    assert _effective(restored) == _effective(before)
    assert (restored.get(league) or {}).get(RC8_KEY, False) is False


def test_flip_is_validated_and_effective_through_the_real_config(tmp_path):
    """The override flip writes must pass Config.update's validation and read back per league."""
    m = _rc8()
    c = config.Config(user_path=tmp_path / "c.yaml", history_dir=tmp_path / "h")
    c.update({"league_overrides": m.flip(copy.deepcopy(c.data), "nhl", 75)})
    assert config.for_league(c, RC8_KEY, "nhl") is True
    for lg in set(LEAGUES) - {"nhl"}:
        assert config.for_league(c, RC8_KEY, lg, False) is False, lg
    # Config.update deep-MERGES: a rollback that only deletes the key leaves the flip in place. The rollback
    # must write rc8_on: false (found by this test against a delete-only reference, Nov 5).
    c.update({"league_overrides": m.rollback(copy.deepcopy(c.data), "nhl")})
    assert config.for_league(c, RC8_KEY, "nhl", False) is False
    c2 = config.Config(user_path=tmp_path / "c.yaml", history_dir=tmp_path / "h")   # and after a restart
    assert config.for_league(c2, RC8_KEY, "nhl", False) is False


def test_config_undo_rolls_an_rc8_flip_back(tmp_path):
    """Nick's Undo (Config.rollback) takes back a flip like any other edit."""
    m = _rc8()
    c = config.Config(user_path=tmp_path / "c.yaml", history_dir=tmp_path / "h")
    c.update({"decider": {"entry": {"landing_lag_s": c.get("decider.entry.landing_lag_s")}}})   # an earlier edit
    c.update({"league_overrides": m.flip(copy.deepcopy(c.data), "nba", 90)})
    assert config.for_league(c, RC8_KEY, "nba") is True
    assert c.rollback()
    c2 = config.Config(user_path=tmp_path / "c.yaml", history_dir=tmp_path / "h")
    assert config.for_league(c2, RC8_KEY, "nba", False) is False


def test_rollback_of_a_league_never_flipped_is_a_no_op(cfg):
    m = _rc8()
    before = _shipped_overrides(cfg)
    after = m.rollback(copy.deepcopy(cfg.data), "mlb")
    assert _effective(after) == _effective(before)


# --------------------------------------------------------------------------------------------- 4. floor + cap
FLOOR_LO, FLOOR_HI, CAP = 10.0, 15.0, 90.0


@pytest.mark.parametrize("lag,sync,tune,margin", [
    (0, 0, 0, 0),            # an instant feed still holds the floor (never 0)
    (1, 0.5, 0.5, 0.5),
    (5, 2, 6, 5),
    (62, 2, 6, 5),           # NFL-ish
    (70, 2, 6, 5),           # NBA-ish
    (75, 2, 6, 5),           # NHL-ish: 88
    (80, 3, 6, 5),           # 94 -> capped
    (300, 10, 20, 20),       # a dead feed never pushes past 90
])
def test_adaptive_buffer_stays_within_floor_and_cap(lag, sync, tune, margin):
    m = _rc8()
    for floor in (FLOOR_LO, 12.0, FLOOR_HI):
        b = m.adaptive_buffer_s("nba", lag, sync, tune, margin, floor_s=floor)
        assert floor <= b <= CAP, (lag, sync, tune, margin, floor, b)
        raw = lag + sync + tune + margin
        assert b == pytest.approx(min(CAP, max(floor, raw)))


def test_adaptive_buffer_floor_holds_against_a_big_stream_delay_credit():
    m = _rc8()
    b = m.adaptive_buffer_s("nba", 20, 2, 6, 5, floor_s=12.0, stream_delay_credit_s=60.0)
    assert b == pytest.approx(12.0)


def test_approved_min_can_raise_but_never_breaks_the_cap():
    m = _rc8()
    assert m.adaptive_buffer_s("nba", 5, 1, 1, 1, approved_min_s=45) == pytest.approx(45)
    assert m.adaptive_buffer_s("nba", 5, 1, 1, 1, approved_min_s=200) <= CAP


@pytest.mark.parametrize("floor", [0.0, 5.0, 9.99, 15.01, 30.0, 95.0])
def test_a_floor_outside_10_to_15_is_refused(floor):
    m = _rc8()
    with pytest.raises(ValueError):
        m.adaptive_buffer_s("nba", 70, 2, 6, 5, floor_s=floor)


@pytest.mark.parametrize("bad", [math.nan, -1.0, math.inf])
def test_bad_inputs_are_refused_not_clamped(bad):
    m = _rc8()
    with pytest.raises(ValueError):
        m.adaptive_buffer_s("nba", bad, 2, 6, 5)


def test_buffer_for_reads_config_and_respects_floor_and_cap(tmp_path):
    m = _rc8()
    c = config.Config(user_path=tmp_path / "c.yaml", history_dir=tmp_path / "h")
    for lg in ("nba", "nhl", "nfl", "cbb", "mlb"):
        s, parts = m.buffer_for(c, lg)
        assert FLOOR_LO <= s <= CAP, (lg, s, parts)
    c.update({"timing": {"behind_live_s": {"adaptive": {"feed_lag_p95_s": {"nhl": 180}}}}})
    assert m.buffer_for(c, "nhl")[0] == pytest.approx(CAP)
    c.update({"timing": {"behind_live_s": {"adaptive": {"feed_lag_p95_s": {"nhl": 0}, "margin_s": 0,
                                                         "sync_err_s": 0, "tune_seek_s": 0}}}})
    assert FLOOR_LO <= m.buffer_for(c, "nhl")[0] <= FLOOR_HI


def test_config_refuses_a_floor_outside_10_to_15(tmp_path):
    _rc8()
    c = config.Config(user_path=tmp_path / "c.yaml", history_dir=tmp_path / "h")
    for bad in (0, 5, 16, 90):
        with pytest.raises((config.ConfigError, ValueError)):
            c.update({"timing": {"behind_live_s": {"adaptive": {"floor_s": bad}}}})
            from redzone import buffer_rc8
            buffer_rc8.buffer_for(c, "nba")


# --------------------------------------------------------------------------------------------- 5. engine (today)
def _engine(cfg, league: str):
    g = SimpleNamespace(league=League(league), phase=Phase.LIVE)
    return SimpleNamespace(cfg=cfg, merger=SimpleNamespace(games={"g1": g}), _game_on=lambda t, v: "g1")


def _hold(cfg, league):
    return Engine._behind_of(_engine(cfg, league), "youtubetv:x")


def test_engine_hold_is_per_league_and_cbb_does_not_inherit_nba(tmp_path):
    c = config.Config(user_path=tmp_path / "c.yaml", history_dir=tmp_path / "h")
    c.update({"league_overrides": {"nba": {"timing.behind_live_s.enabled": True},
                                   "nhl": {"timing.behind_live_s.enabled": False}}})
    assert _hold(c, "nba") == pytest.approx(90.0)
    for lg in ("nhl", "cbb", "wcbb", "cfb", "mlb"):
        assert _hold(c, lg) == 0.0, lg


def test_flipping_nhl_hold_changes_only_nhl(tmp_path):
    c = config.Config(user_path=tmp_path / "c.yaml", history_dir=tmp_path / "h")
    before = {lg: _hold(c, lg) for lg in LEAGUES}
    c.update({"league_overrides": {"nhl": {"timing.behind_live_s.enabled": True}}})
    after = {lg: _hold(c, lg) for lg in LEAGUES}
    assert after["nhl"] == pytest.approx(90.0)
    assert {k: v for k, v in after.items() if k != "nhl"} == {k: v for k, v in before.items() if k != "nhl"}
    assert c.rollback()
    c2 = config.Config(user_path=tmp_path / "c.yaml", history_dir=tmp_path / "h")
    assert {lg: _hold(c2, lg) for lg in LEAGUES} == before


@pytest.mark.parametrize("asked", [91, 120, 600])
def test_engine_hold_never_exceeds_the_90_s_cap(tmp_path, asked):
    c = config.Config(user_path=tmp_path / "c.yaml", history_dir=tmp_path / "h")
    c.update({"timing": {"behind_live_s": {"by_league": {"nhl": asked}}},
              "league_overrides": {"nhl": {"timing.behind_live_s.enabled": True}}})
    assert _hold(c, "nhl") == pytest.approx(90.0)


def test_engine_hold_is_zero_when_the_game_is_not_live(tmp_path):
    c = config.Config(user_path=tmp_path / "c.yaml", history_dir=tmp_path / "h")
    c.update({"league_overrides": {"nhl": {"timing.behind_live_s.enabled": True}}})
    e = _engine(c, "nhl")
    e.merger.games["g1"].phase = Phase.PRE
    assert Engine._behind_of(e, "youtubetv:x") == 0.0


def test_every_configured_league_hold_sits_inside_floor_and_cap(cfg):
    """The static by_league table (what the hold uses today) never asks for less than the floor or more than
    the cap, so turning any league on can't land a sub-floor buffer."""
    by = cfg.get("timing.behind_live_s.by_league") or {}
    for lg, b in list(by.items()) + [("default", cfg.get("timing.behind_live_s.default"))]:
        assert FLOOR_LO <= float(b) <= CAP, (lg, b)


# --------------------------------------------------------------------------------------------- 6. RC8 in the engine
def test_rc8_flip_moves_only_that_leagues_engine_hold_and_rollback_restores_it(tmp_path):
    m = _rc8()
    c = config.Config(user_path=tmp_path / "c.yaml", history_dir=tmp_path / "h")
    before = {lg: _hold(c, lg) for lg in LEAGUES}
    c.update({"league_overrides": m.flip(copy.deepcopy(c.data), "nhl")})
    after = {lg: _hold(c, lg) for lg in LEAGUES}
    assert after["nhl"] == pytest.approx(m.buffer_for(c, "nhl")[0]) and FLOOR_LO <= after["nhl"] <= CAP
    assert {k: v for k, v in after.items() if k != "nhl"} == {k: v for k, v in before.items() if k != "nhl"}
    c.update({"league_overrides": m.rollback(copy.deepcopy(c.data), "nhl")})
    assert {lg: _hold(c, lg) for lg in LEAGUES} == before


@pytest.mark.parametrize("pin", [1, 5, 9.9])
def test_a_pin_under_the_floor_is_held_at_the_floor(tmp_path, pin):
    m = _rc8()
    c = config.Config(user_path=tmp_path / "c.yaml", history_dir=tmp_path / "h")
    c.update({"league_overrides": m.flip(copy.deepcopy(c.data), "nhl", pin)})
    floor = float(c.get("timing.behind_live_s.adaptive.floor_s"))
    assert FLOOR_LO <= floor <= FLOOR_HI
    assert _hold(c, "nhl") == pytest.approx(floor)


def test_a_global_rc8_on_is_refused_or_ignored(tmp_path):
    """Global keys leak into ungraded leagues (Nick, Nov 4 8:40 PM): RC8 is per league only."""
    m = _rc8()
    c = config.Config(user_path=tmp_path / "c.yaml", history_dir=tmp_path / "h")
    try:
        c.update({"timing": {"behind_live_s": {"rc8_on": True}}})
    except config.ConfigError:
        return
    assert m.validate_scope(c)
    for lg in LEAGUES:
        assert not m.is_on(c, lg), lg


def test_ship_b_stays_inside_floor_and_cap_and_goes_to_cap_after_a_mid_play_night(cfg):
    m = _rc8()
    for lg in ("nba", "nfl", "nhl", "cbb"):
        for sweep in (None, 0, 45, 60, 75, 90, 120):
            b, why = m.ship_b(cfg, lg, sweep)
            assert FLOOR_LO <= b <= CAP, (lg, sweep, b, why)
        assert m.ship_b(cfg, lg, 45, mid_play_night=True)[0] == pytest.approx(CAP)
