"""hardening.feeds: a live game whose ESPN polling went dark takes FastCast pushes until polling is
back (fakes only, no network):
- FeedBook.polled_stale ignores pushes, so a push can't switch its own fallback off;
- with the live check over (no FastCast running), a dark game starts FastCast in "fallback" mode on
  just the dark games, and it stops once polling is fresh;
- in shadow mode a dark game's push is merged instead of only graded;
- off (the default): nothing changes."""
import json

import pytest

from redzone.models import GameState, League, Phase, Team
from redzone.sources import fastcast as fc
from test_fastcast import FIX, FakeClient, _normalize, engine  # noqa: F401  (fixture)

GID = "401909906"
TOPIC = f"gp-basketball-nba-{GID}"


@pytest.fixture
def dark(engine, monkeypatch):
    """One live game whose polled feed is dark (polled_stale) until `state['dark']` goes False."""
    FakeClient.made = []
    monkeypatch.setattr(fc, "FastcastClient", FakeClient)
    g0 = GameState(league=League.NBA, game_id=GID, home=Team("LAL"), away=Team("MEM"), phase=Phase.LIVE)
    engine.merger.update("espn_scoreboard", g0)
    for g in engine.merger.games.values():
        if g.game_id != GID:
            g.phase = Phase.POST
    state = {"dark": True}
    monkeypatch.setattr(engine.feeds, "polled_stale", lambda gid, now=None: state["dark"] and gid == GID)
    return engine, state


def doc():
    d = _normalize(League.NBA, json.loads((FIX / "espn_nba_summary_401909906.json").read_text()))
    d["header"]["competitions"][0]["status"]["type"]["state"] = "in"
    return d


async def test_dark_game_starts_fastcast_fallback_and_merges_until_polling_is_back(dark):
    engine, state = dark
    engine.cfg.update({"sources": {"fastcast_check": False}, "hardening": {"feeds": True}})
    engine.sync_fastcast()
    assert engine._fastcast_mode == "fallback" and FakeClient.made[0].topics == {TOPIC}
    engine._on_fastcast_fallback(TOPIC, doc(), 1000.0, None)
    assert "espn_fastcast" in engine.merger.games[GID].sources          # merged: the game is visible again
    state["dark"] = False                                                # polling recovered
    before = engine.merger.health["espn_fastcast"].updates
    engine._on_fastcast_fallback(TOPIC, doc(), 1001.0, None)
    assert engine.merger.health["espn_fastcast"].updates == before        # ignored now
    engine.sync_fastcast()
    assert engine.fastcast is None and engine._fastcast_mode is None and FakeClient.made[0].stopped


async def test_shadow_merges_a_dark_game_instead_of_grading_it(dark):
    engine, state = dark
    engine.cfg.update({"hardening": {"feeds": True}})
    engine.sync_fastcast()
    assert engine._fastcast_mode == "shadow" and TOPIC in FakeClient.made[0].topics   # dark game first
    engine._on_fastcast_shadow(TOPIC, doc(), 1000.0, None)
    assert "espn_fastcast" in engine.merger.games[GID].sources and engine.fc_check.updates == 0


async def test_off_nothing_changes(dark):
    engine, state = dark
    engine.cfg.update({"sources": {"fastcast_check": False}})
    engine.sync_fastcast()
    assert engine.fastcast is None and FakeClient.made == []
    engine.cfg.update({"sources": {"fastcast_check": True}})
    engine.sync_fastcast()
    engine._on_fastcast_shadow(TOPIC, doc(), 1000.0, None)
    assert "espn_fastcast" not in engine.merger.games[GID].sources


def test_polled_stale_ignores_pushes():
    from redzone.sources.feeds import FeedBook
    from redzone.sources.merge import Merger

    class F:
        def fetched_at(self, u):
            return 100.0

        def backing_off(self, u):
            return False
    m = Merger()
    g = GameState(league=League.NBA, game_id="g", home=Team("A"), away=Team("B"), phase=Phase.LIVE)
    m.update("espn_scoreboard", g)
    m.games["g"].sources["espn_fastcast"] = 200.0                       # a fresh push
    fb = FeedBook(m, F(), lambda g: ["u"], lambda gid: 3.0, clock=lambda: 200.0)
    assert fb.polled_stale("g")                                          # pushes do not count
    assert not FeedBook(m, F(), lambda g: ["u"], lambda gid: 3.0, clock=lambda: 105.0).polled_stale("g")
