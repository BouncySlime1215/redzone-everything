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
