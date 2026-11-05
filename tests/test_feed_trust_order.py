"""[FEED-01, FEED-03] sources.trust_order: the fastest measured source owns a game's stoppage while it
is fresh and healthy; when it errors or goes quiet the next source in the order takes over (failover),
and when a source disagrees with the owner the owner wins and the disagreement is logged."""
import pytest

from redzone.models import GameState, League, Phase, Stoppage, Team
from redzone.sources.merge import OWNER_ERROR_HOLD_S, STOPPAGE_OWNER_FRESH_S, Merger


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def _g(stoppage, clock_s=600.0):
    return GameState(league=League.NHL, game_id="7", home=Team("WSH", score=1), away=Team("NYR", score=0),
                     phase=Phase.LIVE, period=2, clock_s=clock_s, stoppage=stoppage)


ORDER = {League.NHL: ["nhl", "espn_fastcast", "espn_summary"]}


@pytest.mark.playbook("FEED-01")
def test_failover_walks_the_order_when_the_first_source_errors_or_goes_quiet():
    clk = Clock()
    m = Merger(clock=clk, stoppage_owner=ORDER)
    m.update("nhl", _g(Stoppage.WHISTLE))
    m.update("espn_fastcast", _g(Stoppage.WHISTLE))
    assert m.owner(League.NHL, "7") == "nhl"
    m.record_error("nhl")                                       # 5xx on the fastest source
    clk.t += 1
    m.update("espn_fastcast", _g(Stoppage.WHISTLE))
    assert m.owner(League.NHL, "7") == "espn_fastcast"          # next in the order, not the slow poll
    clk.t += OWNER_ERROR_HOLD_S + 1
    m.update("nhl", _g(Stoppage.WHISTLE))
    assert m.owner(League.NHL, "7") == "nhl"                    # healthy again: back to the fastest
    clk.t += STOPPAGE_OWNER_FRESH_S + 1                         # everyone quiet
    assert m.owner(League.NHL, "7") is None
    assert [o for _, _, o in m.owner_log] == ["nhl", "espn_fastcast", "nhl"]


@pytest.mark.playbook("FEED-03")
def test_a_disagreement_is_decided_for_the_faster_source_and_logged():
    clk = Clock()
    m = Merger(clock=clk, stoppage_owner=ORDER)
    m.update("nhl", _g(Stoppage.WHISTLE))
    clk.t += 3
    got = m.update("espn_summary", _g(Stoppage.NONE))           # the slower poll says play is on
    assert got.stoppage == Stoppage.WHISTLE
    assert m.disagreements[("nhl", "espn_summary")] == 1


@pytest.mark.playbook("FEED-01", "FEED-03")
def test_off_by_default_and_a_single_owner_string_still_works():
    m = Merger(clock=Clock())
    m.update("nhl", _g(Stoppage.WHISTLE))
    assert m.update("espn_summary", _g(Stoppage.NONE)).stoppage == Stoppage.NONE   # no order: progress decides
    m2 = Merger(clock=Clock(), stoppage_owner={League.NHL: "nhl"})
    m2.update("nhl", _g(Stoppage.WHISTLE))
    assert m2.update("espn_summary", _g(Stoppage.NONE)).stoppage == Stoppage.WHISTLE


def test_engine_reads_the_trust_order_flag(tmp_path):
    from redzone.config import Config
    from redzone.engine import Engine
    from redzone.store import Store
    cfg = Config(user_path=tmp_path / "c.yaml", history_dir=tmp_path / "h")
    cfg.defaults["league_overrides"] = cfg.data["league_overrides"] = {}   # live behavior: no league runs a promoted set
    # [Nick 9:05 AM Nov 5] nba_cdn is primary by default: it owns NBA stoppages
    assert Engine(cfg, Store(tmp_path / "a.db")).merger.stoppage_owner == {League.NBA: ["nba"]}
    cfg.update({"sources": {"trust_order": {"nhl": ["nhl", "espn_summary"]}}})
    assert Engine(cfg, Store(tmp_path / "b.db")).merger.stoppage_owner == {League.NBA: ["nba"],
                                                                            League.NHL: ["nhl", "espn_summary"]}
    cfg.update({"sources": {"nba_primary": False}})
    assert Engine(cfg, Store(tmp_path / "c.db")).merger.stoppage_owner == {League.NHL: ["nhl", "espn_summary"]}
