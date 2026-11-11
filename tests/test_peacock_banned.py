"""Peacock is never a source (CLAUDE.md "Never use Peacock"; audit DEV-01 / D-07, discovery side).

Repro: before the fix `peacock` sat in TUNER_APPS / LINK_APPS / LABEL_ONLY and LAST_RESORT, so a game whose
only way to watch was a Peacock event link got a tunable `peacock:<id>` target from `best_source`, with no
Noggin flag involved.  Root cause: the ban lived only in noggin.priority (off by default).
"""
from redzone.channels import ChannelMap
from redzone.discovery import providers as prov
from redzone.models import GameState, League, Phase, Team


def _game(broadcasts=("Peacock",)):
    return GameState(league=League.NBA, game_id="g1", home=Team("CLE"), away=Team("OKC"),
                     phase=Phase.LIVE, broadcasts=list(broadcasts))


def test_peacock_is_not_a_tuner_or_link_app():
    assert "peacock" not in prov.TUNER_APPS
    assert "peacock" not in prov.LINK_APPS and "peacock" not in prov.LABEL_ONLY
    assert "peacock" in prov.BANNED_APPS


def test_peacock_only_game_has_no_target():
    g = _game()
    links = {"peacock": [{"ref": "fb28161e-3214", "feed": "home"}]}
    cands = prov.gather_sources(g, None, links)
    assert not any(c.provider == "peacock" or (c.target or "").startswith("peacock:") for c in cands)
    pick = prov.best_source(g, cands)
    assert pick.target is None and not pick.tunable


def test_best_source_drops_peacock_even_when_set_on_the_phone():
    for conf in ("sure", "you set it"):
        src = prov.Source("peacock", "peacock:x", "Peacock", True, confidence=conf)
        assert prov.best_source(_game(), [src]).target is None
    other = prov.Source("espn", "espn:y", "ESPN", True)
    pick = prov.best_source(_game(), [prov.Source("peacock", "peacock:x", "Peacock", True), other])
    assert pick.target == "espn:y"


def test_peacock_game_still_maps_to_nbc_on_youtube_tv(tmp_path):
    cm = ChannelMap(tmp_path / "channels.json")
    cm.codes = {"nbcsn": "NBCSN123", "nbc": "NBC123"}
    cands = prov.gather_sources(_game(), None, {}, channel_map=cm)
    targets = [c.target for c in cands]
    assert "youtubetv:nbcsn" in targets and all(t.startswith("youtubetv:") for t in targets)
    assert prov.best_source(_game(), cands).target.startswith("youtubetv:")
