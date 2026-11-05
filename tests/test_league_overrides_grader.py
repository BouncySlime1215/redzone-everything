"""[grader v7] evaldata.flatten_league_overrides: a scorecard grades the league-scoped defaults the app runs."""
from types import SimpleNamespace

from redzone.lab import evaldata


def test_flattens_only_the_graded_league():
    cfg = SimpleNamespace(data={"league_overrides": {"nba": {"decider.entry.cue": "hidden", "replay.crunch_block": True},
                                                     "nfl": {"decider.entry.cue": "seek"}},
                                "decider": {"entry": {"cue": "seek"}}})
    got = evaldata.flatten_league_overrides(cfg, "nba")
    assert got == {"decider.entry.cue": "hidden", "replay.crunch_block": True}
    assert cfg.data["decider"]["entry"]["cue"] == "hidden" and cfg.data["replay"]["crunch_block"] is True


def test_no_overrides_changes_nothing():
    cfg = SimpleNamespace(data={"decider": {"entry": {"cue": "seek"}}})
    assert evaldata.flatten_league_overrides(cfg, "nhl") == {}
    assert cfg.data == {"decider": {"entry": {"cue": "seek"}}}


def test_buffer_comes_from_the_config_when_enabled():
    on = SimpleNamespace(data={"timing": {"behind_live_s": {"enabled": True, "by_league": {"nba": 90, "nfl": 60},
                                                            "default": 60}}})
    assert evaldata.config_behind_live(on, "nba") == 90.0
    assert evaldata.config_behind_live(on, "mlb") == 60.0
    off = SimpleNamespace(data={"timing": {"behind_live_s": {"enabled": False, "by_league": {"nba": 90}}}})
    assert evaldata.config_behind_live(off, "nba") is None
