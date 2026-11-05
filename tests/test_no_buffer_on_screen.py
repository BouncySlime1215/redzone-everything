"""[Nick 9:10 AM Nov 5] Tonight's release keeps the screen's buffer exactly as RC6.2 (1bf41504, integration/release,
Nick's Nov 4 10:55 PM OK) has it: NBA and NFL held behind live, every other league live. The time-shift buffer
editor runs in SHADOW only. A league's buffer changes only through RC8 (timing.behind_live_s.rc8_on, set per league
by scripts/rc8_flip.py after its gate passes and Nick flips it), never by a default."""
import pytest

from redzone.config import Config, for_league

# RC6.2's effective timing.behind_live_s per league (global block + league_overrides), pinned from 1bf41504
RC62_ENABLED = {"nba": True, "nfl": True}
RC62_BY_LEAGUE = {"nfl": 60, "cfb": 60, "nba": 90, "cbb": 90, "wcbb": 90, "nhl": 90}
LEAGUES = ["nfl", "cfb", "nba", "cbb", "wcbb", "nhl", "mlb"]


@pytest.fixture(scope="module")
def cfg():
    return Config()


@pytest.mark.parametrize("lg", LEAGUES)
def test_hold_matches_rc62_per_league(cfg, lg):
    assert bool(for_league(cfg, "timing.behind_live_s.enabled", lg, False)) is RC62_ENABLED.get(lg, False)


@pytest.mark.parametrize("lg", LEAGUES)
def test_rc8_buffer_is_off_for_every_league(cfg, lg):
    assert not for_league(cfg, "timing.behind_live_s.rc8_on", lg, False)


def test_global_buffer_settings_match_rc62(cfg):
    assert cfg.get("timing.behind_live_s.enabled") is False
    assert cfg.get("timing.behind_live_s.rc8_on") is False
    assert cfg.get("timing.behind_live_s.by_league") == RC62_BY_LEAGUE
    assert cfg.get("timing.behind_live_s.default") == 60
    assert cfg.get("timing.behind_live_s.max_s") == 90


def test_no_league_override_touches_the_buffer_except_rc62s_hold(cfg):
    over = cfg.get("league_overrides") or {}
    for lg, keys in over.items():
        touched = {k for k in (keys or {}) if k.startswith("timing.behind_live_s")}
        allowed = {"timing.behind_live_s.enabled"} if lg in RC62_ENABLED else set()
        assert touched <= allowed, (lg, touched - allowed)
