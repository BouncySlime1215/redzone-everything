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


def test_set_under_league_overrides_keeps_the_rest_of_the_group():
    """[grader v24] `--set league_overrides.nba.delay.sync.min_sigma_s=1` (EXP-803 A2) changes that one setting; it
    used to nest and then replace the whole delay group for the run."""
    from redzone.config import Config, for_league
    cfg = SimpleNamespace(data={"delay": {"sync": {"min_sigma_s": 3.0, "other": 7}, "keep": True}})
    evaldata.apply_set(cfg, "league_overrides.nba.delay.sync.min_sigma_s=1")
    assert cfg.data["league_overrides"] == {"nba": {"delay.sync.min_sigma_s": 1}}
    evaldata.flatten_league_overrides(cfg, "nba")
    assert cfg.data["delay"] == {"sync": {"min_sigma_s": 1, "other": 7}, "keep": True}
    app = Config.__new__(Config)                                  # the app reads the same flat key the same way
    app.data = {"delay": {"sync": {"min_sigma_s": 3.0, "other": 7}},
                "league_overrides": {"nba": {"delay.sync.min_sigma_s": 1}}}
    assert for_league(app, "delay.sync.min_sigma_s", "nba") == 1
    assert for_league(app, "delay.sync.other", "nba") == 7


def test_plain_set_still_nests():
    cfg = SimpleNamespace(data={"decider": {"entry": {"cue": "seek", "x": 1}}})
    evaldata.apply_set(cfg, "decider.entry.cue=hidden")
    assert cfg.data == {"decider": {"entry": {"cue": "hidden", "x": 1}}}
    evaldata.apply_set(cfg, "league_overrides.nba={}")          # a whole-league value is still allowed
    assert cfg.data["league_overrides"] == {"nba": {}}
