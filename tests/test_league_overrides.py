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


# A fixture set, injected: the mechanism tests must not depend on which leagues ship a set today
# (RC6.3 set NBA back to live behavior and broke tests that read the shipped NBA block).
SET = {"decider.entry.cue": "hidden", "decider.entry.max_wait_s": {}, "decider.entry.crunch_now": "wait",
       "decider.entry.landing_lag_s": 6, "replay.crunch_block": True, "replay.redzone_way": True}


@pytest.fixture
def scoped(cfg):
    """cfg with SET under nba and nfl, every other league live."""
    over = {"nba": dict(SET), "nfl": dict(SET)}
    cfg.defaults["league_overrides"] = over
    cfg.data["league_overrides"] = {k: dict(v) for k, v in over.items()}
    return cfg


def test_override_wins_only_for_its_league(scoped):
    cfg = scoped
    assert config.for_league(cfg, "decider.entry.cue", "nba") == "hidden"
    assert config.for_league(cfg, "decider.entry.cue", "cbb") == cfg.get("decider.entry.cue")
    assert config.for_league(cfg, "decider.entry.cue", None) == cfg.get("decider.entry.cue")


def test_a_group_override_replaces_the_whole_group(scoped):
    cfg = scoped
    assert config.for_league(cfg, "decider.entry.max_wait_s.nba", "nba") is None   # {}: no cap at all
    assert config.for_league(cfg, "decider.entry.max_wait_s.nba", "cbb") == cfg.get("decider.entry.max_wait_s.nba")


def test_shipped_scopes_nfl_and_leaves_the_rest_live(cfg):
    """RC6.3: NFL runs its graded set; NBA runs live behavior plus EXP-204 only (its set cost your-team
    crunch under the live-resync harness); every other league is live."""
    over = cfg.get("league_overrides")
    assert {k for k, v in over.items() if v} == {"nba", "nfl"}
    assert set(over["nba"]) == {"decider.mine_late_max_lead_wp"}
    for lg in ("nba", "nhl", "cbb", "cfb", "wcbb", "mlb"):
        assert config.for_league(cfg, "noggin.priority.enabled", lg) is False
        assert config.for_league(cfg, "timing.behind_live_s.enabled", lg) is False
    assert config.for_league(cfg, "timing.behind_live_s.enabled", "nfl") is True
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


def test_crunch_block_follows_the_crunch_games_league(scoped):
    cfg = scoped
    dec = Decider(cfg, DelayBook(40.0, 3.0))
    assert dec._crunch_on({"a": _x("nba", crunch=True)})
    assert not dec._crunch_on({"b": _x("cbb", crunch=True)})   # CBB keeps live behavior: no block


def test_entry_keys_follow_the_destination(scoped):
    cfg = scoped
    dec = Decider(cfg, DelayBook(40.0, 3.0))
    assert dec._crunch_entry_mode(_x("nba")) == "wait"
    assert dec._crunch_entry_mode(_x("nhl")) == cfg.get("decider.entry.crunch_now")
    assert dec._crunch_entry_mode(_x("cbb")) == cfg.get("decider.entry.crunch_now")
    assert dec._lc("decider.entry.landing_lag_s", _x("nfl")) == 6
    assert dec._lc("decider.entry.landing_lag_s", _x("mlb"), 0.0) == cfg.get("decider.entry.landing_lag_s", 0.0)


def test_redzone_way_per_game(scoped):
    cfg = scoped
    dec = Decider(cfg, DelayBook(40.0, 3.0))
    dec._known.update({"n": _x("nba"), "c": _x("cbb")})
    assert dec._rw("n") and not dec._rw("c")


def test_college_never_inherits_the_pro_leagues_overrides(scoped):
    """CBB's base league is NBA and CFB's is NFL, but RC7 ships CBB/CFB on live behavior."""
    cfg = scoped
    dec = Decider(cfg, DelayBook(40.0, 3.0))
    assert League.CBB.base == League.NBA and League.CFB.base == League.NFL
    assert dec._lg(_x("cbb")) == "cbb" and dec._lg(_x("cfb")) == "cfb"
    assert dec._lc("decider.entry.cue", _x("cfb")) == cfg.get("decider.entry.cue")
    assert dec._lc("decider.entry.cue", _x("nfl")) == "hidden"
