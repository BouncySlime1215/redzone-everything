"""Scorecards must not reward an interrupted play or a merely requested replay."""
from types import SimpleNamespace

import pytest

from redzone.lab import backtest, sportsgrade
from redzone.models import League, Phase, Stoppage


def replay(league):
    play = {"type": {"text": "Home Run" if league == League.MLB else "Touchdown"},
            "text": "scoring play", "scoringPlay": True, "period": {"number": 8 if league == League.MLB else 4},
            "clock": {"displayValue": "1:00"}, "homeScore": 1, "awayScore": 0}
    return SimpleNamespace(league=league, gid="a", matchup="A @ B", wall=[100.0, 200.0],
                           plays=[play, {"type": {"text": "End Inning"}}],
                           state_at=lambda idx: SimpleNamespace(phase=Phase.LIVE, stoppage=Stoppage.NONE))


@pytest.mark.parametrize("league", [League.MLB, League.NFL])
def test_midplay_cut_cannot_also_earn_full_big_play_credit(league):
    r = replay(league)
    switches = [backtest.SwitchLog(0, None, "a", "live", ""),
                backtest.SwitchLog(95, "a", "other", "live", "")]
    if league == League.MLB:
        card = sportsgrade.grade([r], switches, set(), 0, 0, 0, 90)
        assert card.big_live == card.big_seen == 0
        # The same test including the complete play interval still cannot earn credit.
        card = sportsgrade.grade([r], switches, set(), 0, 0, 0, 200)
        assert len(card.cuts) == 1 and card.big_live == card.big_seen == 0
    else:
        # Only the switching record needs the second game; end before its unknown state is sampled.
        card = backtest._grade([r], switches, [(0, "a"), (95, None)], set(), 0, 7, 0, 0, 100, set())
        assert len(card.mid_play_cuts) == 1 and card.big_plays_live == card.big_plays_caught == 0


def test_complete_uninterrupted_play_earns_credit():
    card = sportsgrade.grade([replay(League.MLB)], [backtest.SwitchLog(0, None, "a", "live", "")],
                             set(), 0, 0, 0, 200)
    assert card.big_live == card.big_seen == 1


def test_leaving_then_returning_inside_play_is_not_full_viewing():
    assert not backtest.watched_whole([(0, "a"), (95, "b"), (97, "a")], "a", 94, 100, 0, 200)
    assert not backtest.watched_whole([(0, "a")], "a", 94, 100, 0, 99)


def test_replay_command_alone_does_not_earn_credit():
    switches, watching, credited = [], [], set()
    d = SimpleNamespace(action="replay", target="a", kind="replay", reason="", replay=SimpleNamespace(t=100))
    backtest._record(d, "b", 200, switches, watching, credited)
    assert watching == [(200, "replay:a")] and not credited


def test_replay_completion_requires_time_and_no_interruption():
    windows, credited = [("a", 100, 200, 240)], set()
    watching = [(200, "replay:a")]
    backtest._credit_completed_replays(windows, watching, 239, credited)
    assert not credited and windows
    backtest._credit_completed_replays(windows, watching + [(240, "b")], 240, credited)
    assert credited == {("a", 100)} and not windows
    windows, credited = [("a", 100, 200, 240)], set()
    backtest._credit_completed_replays(windows, watching + [(230, "b")], 240, credited)
    assert not credited and not windows


def test_tuning_blackout_uses_union_clipped_to_simulation_horizon():
    watching = [(0, "a"), (3, "b"), (20, "a")]
    assert backtest.tuning_blackout_seconds(watching, 10, 0, 25) == 18
    assert backtest.tuning_blackout_seconds(watching, 0, 0, 25) == 0


def test_blackout_is_reported_separately_from_commercials():
    card = sportsgrade.grade([replay(League.MLB)], [backtest.SwitchLog(90, None, "a", "live", "")],
                             set(), 0, 10, 0, 200)
    assert card.tuning_blackout_s == 10 and card.summary()["tuning_blackout_pct"] == 5


def test_saved_switch_limits_are_ignored_without_losing_preferences(tmp_path):
    from redzone.config import Config, RETIRED_SETTINGS
    from redzone.lab.optimize import SPACE, _Overlay
    path = tmp_path / "config.yaml"
    path.write_text("decider:\n  min_dwell_s: 600\n  max_switches_per_5min: 1\n  min_gap_s: 300\n"
                    "  ping_pong_freeze_s: 999\n  chase_min_dwell_s: 300\n  switch_margin: 17\n"
                    "mode:\n  dry_run: true\n")
    cfg = Config(path, tmp_path / "history")
    assert cfg.get("decider.switch_margin") == 17
    assert cfg.get("mode.dry_run") is True
    assert all(cfg.get(key) is None for key in RETIRED_SETTINGS)
    cfg.update({"decider": {"min_dwell_s": 99999, "manual_pause_s": 45}})
    assert cfg.get("decider.min_dwell_s") is None and cfg.get("decider.manual_pause_s") == 45
    assert not (set(SPACE) & RETIRED_SETTINGS)
    overlay = _Overlay(cfg, {"decider.min_gap_s": 500, "decider.switch_margin": 20})
    assert overlay.get("decider.min_gap_s") is None and overlay.get("decider.switch_margin") == 20


def test_rollback_does_not_restore_retired_switch_limits(tmp_path):
    from redzone.config import Config
    history = tmp_path / "history"
    history.mkdir()
    (history / "001.yaml").write_text("decider:\n  min_dwell_s: 600\n  switch_margin: 13\n")
    (history / "002.yaml").write_text("decider:\n  min_dwell_s: 40\n  switch_margin: 15\n")
    cfg = Config(tmp_path / "config.yaml", history)
    assert cfg.rollback()
    assert cfg.get("decider.switch_margin") == 13 and cfg.get("decider.min_dwell_s") is None
