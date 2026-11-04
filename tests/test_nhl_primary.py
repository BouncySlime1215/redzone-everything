"""NHL api-web as the primary NHL source: it owns stoppages while fresh, and its plays reach the
timeline bounded by the NHL feed's lag window (no wallclock)."""
import json

from redzone.decider.screen import SOURCE_LAG_S, _bounds
from redzone.models import GameState, League, Phase, Stoppage, Team
from redzone.sources import leagues
from redzone.sources.merge import STOPPAGE_OWNER_FRESH_S, Merger

PBP = json.load(open("tests/fixtures/nhl_api_gamecenter_2026020229_play-by-play.json"))


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def _g(stoppage, clock_s=600.0):
    return GameState(league=League.NHL, game_id="7", home=Team("WSH", score=1), away=Team("NYR", score=0),
                     phase=Phase.LIVE, period=2, clock_s=clock_s, stoppage=stoppage)


def test_fresh_owner_keeps_the_whistle_against_a_stale_espn_copy():
    clk = Clock()
    m = Merger(clock=clk, stoppage_owner={League.NHL: "nhl"})
    m.update("nhl", _g(Stoppage.WHISTLE))
    clk.t += 4
    assert m.update("espn_summary", _g(Stoppage.NONE)).stoppage == Stoppage.WHISTLE
    clk.t += STOPPAGE_OWNER_FRESH_S + 1          # owner went quiet: ESPN decides again
    assert m.update("espn_summary", _g(Stoppage.NONE, 590)).stoppage == Stoppage.NONE


def test_no_owner_keeps_old_behaviour():
    m = Merger(clock=Clock())
    m.update("nhl", _g(Stoppage.WHISTLE))
    assert m.update("espn_summary", _g(Stoppage.NONE)).stoppage == Stoppage.NONE


def test_first_look_is_history_then_new_plays_carry_seen_time():
    seen = {}
    assert leagues.nhl_plays(PBP, seen, now=50.0) == []
    newer = dict(PBP, plays=PBP["plays"] + [{"eventId": 9999, "typeDescKey": "stoppage",
                                             "timeRemaining": "05:00", "periodDescriptor": {"number": 3},
                                             "details": {"reason": "icing"}}])
    out = leagues.nhl_plays(newer, seen, now=80.0)
    assert [p.id for p in out] == ["nhl9999"]
    p = out[0]
    assert p.kind == "stoppage" and p.wallclock is None and p.seen_at == 80.0 and p.clock == 300
    lo, hi = _bounds(p, 85.0)
    assert (lo, hi) == (80.0 - SOURCE_LAG_S["nhl"][1], 80.0 - SOURCE_LAG_S["nhl"][0])


def test_delayed_penalty_is_live_and_a_whistle_penalty_is_dead():
    from redzone.decider.screen import _is_marker
    assert not _is_marker(leagues.NHL_KIND["delayed-penalty"], League.NHL)
    assert _is_marker(leagues.NHL_KIND["penalty"], League.NHL)
    assert not _is_marker(leagues.NHL_KIND["faceoff"], League.NHL)
