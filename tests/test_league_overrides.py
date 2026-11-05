"""[league overrides] Per-league promotion (Nick, Nov 4 8:20/8:40 PM): a league runs its graded set,
every other league keeps live behavior (redzone/config.py for_league)."""
from types import SimpleNamespace

import pytest

from redzone import config
from redzone.config import Config, ConfigError
from redzone.decider.decider import Decider
from redzone.decider.screen import DelayBook
from redzone.models import League


def _x(league, crunch=False):
    lg = League(league)   # the real enum: CBB.base is NBA, so a stub can't hide a base-league lookup
    return SimpleNamespace(scored=SimpleNamespace(league=lg, crunch=crunch, crunch_now=False, garbage=False),
                           target=f"yt:{league}")


def test_override_wins_only_for_its_league(cfg):
    assert config.for_league(cfg, "decider.entry.cue", "nba") == "hidden"
    assert config.for_league(cfg, "decider.entry.cue", "cbb") == cfg.get("decider.entry.cue")
    assert config.for_league(cfg, "decider.entry.cue", None) == cfg.get("decider.entry.cue")


def test_a_group_override_replaces_the_whole_group(cfg):
    assert config.for_league(cfg, "decider.entry.max_wait_s.nba", "nba") is None   # {}: no cap at all
    assert config.for_league(cfg, "decider.entry.max_wait_s.nba", "cbb") == cfg.get("decider.entry.max_wait_s.nba")


def test_rc7_scopes_nba_nhl_nfl_and_leaves_the_rest_live(cfg):
    over = cfg.get("league_overrides")
    assert set(over) == {"nba", "nhl", "nfl"}
    for lg in ("cbb", "cfb", "wcbb", "mlb"):
        assert config.for_league(cfg, "noggin.priority.enabled", lg) is False
        assert config.for_league(cfg, "timing.behind_live_s.enabled", lg) is False
    assert config.any_league(cfg, "timing.behind_live_s.enabled")
    assert not cfg.get("timing.behind_live_s.enabled")   # the global toggle stays off


def test_unknown_override_key_is_refused_and_pruned_alone(tmp_path):
    c = Config(user_path=tmp_path / "c.yaml", history_dir=tmp_path / "h")
    with pytest.raises(ConfigError):
        c.update({"league_overrides": {"nba": {"decider.entry.nope": 1}}})
    with pytest.raises(ConfigError):
        c.update({"league_overrides": {"nba": {"decider.entry.landing_lag_s": "six"}}})
    (tmp_path / "u.yaml").write_text("league_overrides:\n  cbb:\n    decider.entry.nope: 1\n    decider.entry.cue: hidden\n")
    u = Config(user_path=tmp_path / "u.yaml", history_dir=tmp_path / "h2")
    assert config.for_league(u, "decider.entry.cue", "cbb") == "hidden"
    assert any("decider.entry.nope" in r for r in u.rejected)


def test_crunch_block_follows_the_crunch_games_league(cfg):
    dec = Decider(cfg, DelayBook(40.0, 3.0))
    assert dec._crunch_on({"a": _x("nba", crunch=True)})
    assert not dec._crunch_on({"b": _x("cbb", crunch=True)})   # CBB keeps live behavior: no block


def test_entry_keys_follow_the_destination(cfg):
    dec = Decider(cfg, DelayBook(40.0, 3.0))
    assert dec._crunch_entry_mode(_x("nhl")) == "wait"
    assert dec._crunch_entry_mode(_x("cbb")) == cfg.get("decider.entry.crunch_now")
    assert dec._lc("decider.entry.landing_lag_s", _x("nfl")) == 6
    assert dec._lc("decider.entry.landing_lag_s", _x("mlb"), 0.0) == cfg.get("decider.entry.landing_lag_s", 0.0)


def test_redzone_way_per_game(cfg):
    dec = Decider(cfg, DelayBook(40.0, 3.0))
    dec._known.update({"n": _x("nba"), "c": _x("cbb")})
    assert dec._rw("n") and not dec._rw("c")


def test_college_never_inherits_the_pro_leagues_overrides(cfg):
    """CBB's base league is NBA and CFB's is NFL, but RC7 ships CBB/CFB on live behavior."""
    dec = Decider(cfg, DelayBook(40.0, 3.0))
    assert League.CBB.base == League.NBA and League.CFB.base == League.NFL
    assert dec._lg(_x("cbb")) == "cbb" and dec._lg(_x("cfb")) == "cfb"
    assert dec._lc("decider.entry.cue", _x("cfb")) == cfg.get("decider.entry.cue")
    assert dec._lc("decider.entry.cue", _x("nfl")) == "hidden"
