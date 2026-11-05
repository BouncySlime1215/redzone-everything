"""[leave floor, EXP-208] Rule #1's leave half: leaving mid-play is allowed only at LOW leverage, and never
out of your team's game or a close-and-late / overtime moment. The decider's gate
(decider.leave.low_leverage) used to miss your team in Q1 (personal under hold_personal) and keyed on
league.base, so "nba" switched it on for college basketball too. Golden suite: these fail on 1bf4150."""
import pytest

from redzone.decider.decider import Candidate, Decider
from redzone.decider.screen import DelayBook
from redzone.lab import leverage
from redzone.models import GameState, League, Phase, Stoppage, Team
from redzone.scoring import engine
from redzone.scoring.engine import Personal, Scorer

ME = Personal(my_teams={League.NBA: {"WSH"}, League.CBB: {"UNC"}})


def nba(gid, home, away, hs, as_, q, clock_s, league=League.NBA):
    return GameState(league=league, game_id=gid, home=Team(home, score=hs), away=Team(away, score=as_),
                     phase=Phase.LIVE, period=q, clock_s=clock_s, stoppage=Stoppage.NONE)


def _gate(cfg, clock, g, lgs=("nba",)):
    cfg.data.setdefault("decider", {}).setdefault("leave", {})["low_leverage"] = list(lgs)
    dec = Decider(cfg, DelayBook({}, 3.0))
    return dec._leavable(Candidate(Scorer(cfg, clock=clock).score(g, ME), "yt:x"))


def test_an_ordinary_q1_game_may_be_left(cfg, clock):
    assert _gate(cfg, clock, nba("o", "GSW", "PHX", 15, 14, 1, 323))


@pytest.mark.parametrize("q,clock_s,hs,as_", [(1, 323, 20, 31), (2, 600, 40, 51), (3, 100, 60, 95), (4, 400, 80, 112)])
def test_your_team_is_never_left_mid_play(cfg, clock, q, clock_s, hs, as_):
    # WSH ahead, including a decided game (garbage time drops its personal boost to 0, which the old
    # personal >= hold_personal test let through)
    g = nba("w", "CHI", "WSH", hs, as_, q, clock_s)
    assert not _gate(cfg, clock, g)


@pytest.mark.parametrize("q,clock_s,hs,as_", [(4, 300, 100, 96), (4, 30, 99, 99), (5, 200, 110, 101)])
def test_close_and_late_or_overtime_is_never_left_mid_play(cfg, clock, q, clock_s, hs, as_):
    assert not _gate(cfg, clock, nba("o", "GSW", "PHX", hs, as_, q, clock_s))


def test_nba_does_not_switch_it_on_for_college(cfg, clock):
    g = nba("c", "DUKE", "UVA", 20, 18, 1, 900, league=League.CBB)
    assert not _gate(cfg, clock, g, lgs=("nba",))
    assert _gate(cfg, clock, g, lgs=("cbb",))


def test_shipped_gate_is_nba_only(cfg, clock):
    """RC6.3: the gate ships for NBA only (full league: never CBB, NFL or NHL)."""
    dec = Decider(cfg, DelayBook({}, 3.0))
    sc = Scorer(cfg, clock=clock)
    assert cfg.get("decider.leave.low_leverage") == ["nba"]
    assert dec._leavable(Candidate(sc.score(nba("o", "GSW", "PHX", 15, 14, 1, 323), ME), "yt:x"))
    assert not dec._leavable(Candidate(sc.score(nba("c", "DUKE", "UVA", 20, 18, 1, 900, league=League.CBB), ME), "yt:x"))


def test_the_decider_floor_matches_the_graders():
    assert engine.LEAVE_CLOSE_LATE == leverage.CLOSE_LATE


def test_close_and_late_still_on_screen_is_never_left(cfg, clock):
    """The feed runs ~a delay ahead of the screen: a 6-point game with 54 s left (on screen) can read 8 points
    in the feed. The floor holds while the screen can still be showing the close-and-late moment."""
    cfg.data.setdefault("decider", {}).setdefault("leave", {})["low_leverage"] = ["nba"]
    dec = Decider(cfg, DelayBook({}, 3.0))
    sc = Scorer(cfg, clock=clock)
    close = Candidate(sc.score(nba("o", "BKN", "CHA", 100, 106, 4, 54), ME), "yt:x")
    later = Candidate(sc.score(nba("o", "BKN", "CHA", 101, 109, 4, 33), ME), "yt:x")
    assert close.scored.high_leverage and not later.scored.high_leverage
    dec._high_lev_at["o"] = 1000.0
    d = dec.delays.get("yt:x")
    assert not dec._leavable(later, 1000.0 + d.mu, d)                      # still on screen
    assert dec._leavable(later, 1000.0 + d.mu + 2 * d.current_sigma(1000.0) + 300.0, d)   # long gone
