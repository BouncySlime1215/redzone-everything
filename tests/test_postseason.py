"""Postseason stakes (redzone/scoring/postseason.py): parsed from real ESPN scoreboards, scored only when on."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from redzone.models import League, Phase
from redzone.noggin import priority as P
from redzone.scoring import postseason as ps
from redzone.sources import espn
from redzone.sources.merge import _fill_gaps

FIX = Path(__file__).parent / "fixtures"


class Cfg(dict):
    def get(self, key, default=None):
        return super().get(key, default)


ON = Cfg({"score.postseason.enabled": True})


def _board(name):
    return json.loads((FIX / name).read_text())


def test_world_series_game_7_is_a_decider():
    g = espn.parse_scoreboard(League.MLB, _board("espn_mlb_scoreboard_2025-11-01.json"))[0]
    st = g.stakes
    assert st.postseason and st.label == "World Series - Game 7"
    assert st.rounds_left == 0 and st.best_of == 7 and st.game_no == 7
    # ESPN's final wins are 4-3; going into the game it was 3-3
    assert (st.wins_home, st.wins_away) == (3, 3)
    assert st.elimination and st.decider
    assert ps.reason(g) == "World Series - Game 7 · winner takes it"


def test_game_5_at_3_1_is_elimination_not_decider():
    g = espn.parse_scoreboard(League.MLB, _board("espn_mlb_scoreboard_2026-11-02.json"))[0]
    st = g.stakes
    assert st.game_no == 5 and sorted([st.wins_home, st.wins_away]) == [1, 3]
    assert st.elimination and not st.decider


def test_live_series_game_counts_wins_as_given():
    data = _board("espn_mlb_scoreboard_2025-11-01.json")
    ev = copy.deepcopy(data["events"][0])
    comp = ev["competitions"][0]
    comp["series"]["completed"] = False
    for c, w in zip(comp["series"]["competitors"], (2, 1)):
        c["wins"] = w
    comp["notes"] = [{"type": "event", "headline": "NLCS - Game 4"}]
    g = espn.parse_scoreboard(League.MLB, {"events": [ev]})[0]
    assert g.stakes.rounds_left == 1 and g.stakes.game_no == 4
    assert not g.stakes.elimination and not g.stakes.decider


def test_regular_season_unranked_has_no_stakes():
    for name, lg in (("espn_nfl_scoreboard_2026-11-02.json", League.NFL),
                     ("espn_nba_scoreboard_2026-11-02.json", League.NBA)):
        for g in espn.parse_scoreboard(lg, _board(name)):
            if not (g.home.rank or g.away.rank):
                assert g.stakes is None


def test_single_elimination_without_series():
    ev = {"season": {"type": 3}, "competitions": [{"notes": [{"headline": "Super Bowl LXI"}]}]}
    st = ps.parse(League.NFL, ev)
    assert st.elimination and st.decider and st.rounds_left == 0 and st.best_of is None
    bowl = {"season": {"type": 3}, "competitions": [{"notes": [{"headline": "Duke's Mayo Bowl"}]}]}
    assert not ps.parse(League.CFB, bowl).elimination
    cfp = {"season": {"type": 3}, "competitions": [{"notes": [{"headline": "College Football Playoff Quarterfinal"}]}]}
    assert ps.parse(League.CFB, cfp).rounds_left == 2 and ps.parse(League.CFB, cfp).elimination


def test_ranked_matchup_regular_season():
    st = ps.parse(League.CBB, {"season": {"type": 2}}, rank_home=4, rank_away=9)
    assert st.ranked_matchup and not st.postseason
    assert ps.bonus(st) == ps.WEIGHTS["ranked_both"] + ps.WEIGHTS["ranked_top10_both"]
    assert ps.parse(League.CBB, {"season": {"type": 2}}, rank_home=99) is None   # ESPN's "unranked"


def test_round_ladder_and_cap():
    title = ps.Stakes(postseason=True, rounds_left=0, elimination=True, decider=True)
    first = ps.Stakes(postseason=True, rounds_left=3)
    assert ps.bonus(title) == ps.WEIGHTS["cap"]
    assert 0 < ps.bonus(first) < ps.bonus(ps.Stakes(postseason=True, rounds_left=1)) < ps.bonus(title)
    assert ps.bonus(None) == 0.0


def test_hooks_are_off_by_default():
    g = espn.parse_scoreboard(League.MLB, _board("espn_mlb_scoreboard_2025-11-01.json"))[0]
    assert ps.stakes_bonus(g, Cfg()) == 0.0 and ps.mind_terms(g, Cfg()) == {}
    assert ps.stakes_bonus(g, ON) == ps.WEIGHTS["cap"]
    terms = ps.mind_terms(g, ON)
    assert terms == {"playoff_race": 1.0}
    # The Noggin L3 term lands as bounded playoff-race points
    pts = P.mind_points(P.MindRead(terms, at=0.0), now=0.0)
    assert pts == {"mind:playoff_race": P.MIND_TERM_POINTS["playoff_race"]}
    assert ps.playoff_flag(g) == 1


def test_weights_read_from_config():
    st = ps.Stakes(postseason=True)
    assert ps.bonus(st, Cfg({"score.postseason.base": 0.1})) == 0.1
    assert ps.bonus(st, Cfg({"score.postseason.base": "junk"})) == ps.WEIGHTS["base"]


def test_bad_payloads_never_raise():
    for ev in ({}, {"season": "x"}, {"season": {"type": 3}, "competitions": ["x"]},
               {"season": {"type": 3}, "competitions": [{"series": {"competitors": [None, {"wins": "2"}]},
                                                          "notes": "x"}]}):
        ps.parse(League.NBA, ev)


def test_merge_fills_stakes_from_espn():
    g = espn.parse_scoreboard(League.MLB, _board("espn_mlb_scoreboard_2025-11-01.json"))[0]
    other = copy.deepcopy(g)
    other.stakes = None
    other.phase = Phase.LIVE
    assert _fill_gaps(other, g).stakes is g.stakes
