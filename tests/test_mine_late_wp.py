"""[EXP-204] Your team "late and close" needs the leader's win probability under a cap (Nick, Nov 4:
ATL up 12 with 9:09 left in Q4 held WSH all night; close is ~6 points, ~6 minutes)."""
from types import SimpleNamespace

from redzone.decider.decider import Decider
from redzone.decider.screen import DelayBook
from redzone.models import GameState, League, Phase, Team
from redzone.scoring.engine import lead_wp


def _g(league, period, clock_s, home, away, wp=None):
    return GameState(league, "g", Team("WSH", score=home), Team("ATL", score=away), Phase.LIVE,
                     period=period, clock_s=clock_s, home_win_prob=wp)


def test_lead_wp_reads_nicks_examples():
    assert lead_wp(_g(League.NFL, 4, 549, 10, 22)) > 0.97          # ATL +12, 9:09 left: not close
    assert 0.85 < lead_wp(_g(League.NFL, 4, 360, 20, 14)) < 0.93   # 6 points, 6:00 left: close
    assert lead_wp(_g(League.NFL, 4, 549, 14, 14)) == 0.5
    assert lead_wp(_g(League.NFL, 4, 549, 10, 22, wp=0.08)) == 0.92   # ESPN's WP wins when there
    assert lead_wp(_g(League.MLB, 8, None, 1, 2)) is None
    assert 0.5 < lead_wp(_g(League.NBA, 4, 300, 100, 94)) < 0.95
    assert 0.5 < lead_wp(_g(League.NHL, 3, 600, 2, 1)) < 0.9


def _s(league, wp, crunch=False):
    return SimpleNamespace(late=True, garbage=False, personal=18.0, closeness=0.6, crunch=crunch,
                           league=League(league), lead_wp=wp)


def test_cap_is_off_by_default_and_league_scoped(cfg):
    dec = Decider(cfg, DelayBook(40.0, 3.0))
    cfg.defaults["league_overrides"] = cfg.data["league_overrides"] = {}
    assert dec._mine_late(_s("nfl", 0.99))                       # live: any non-garbage Q4 holds
    cfg.data["league_overrides"] = {"nfl": {"decider.mine_late_max_lead_wp": 0.93}}
    assert not dec._mine_late(_s("nfl", 0.99))                   # ATL +12 at 9:09: let others in
    assert dec._mine_late(_s("nfl", 0.90))                       # one score, 6 min: hold
    assert dec._mine_late(_s("nfl", 0.999, crunch=True))         # crunch time always holds
    assert dec._mine_late(_s("nfl", None))                       # unknown WP: live behavior
    assert dec._mine_late(_s("cfb", 0.99))                       # CFB keeps live behavior


def test_rc7_defaults_release_atl_up_12_but_hold_one_score(cfg):
    """RC7 ships the cap for NFL and NBA (EXP-204, 0.95); NHL and college stay on live behavior."""
    dec = Decider(cfg, DelayBook(40.0, 3.0))
    atl = lead_wp(_g(League.NFL, 4, 549, 10, 22))
    assert not dec._mine_late(_s("nfl", atl))
    assert dec._mine_late(_s("nfl", lead_wp(_g(League.NFL, 4, 360, 20, 14))))
    assert dec._mine_late(_s("nhl", 0.99)) and dec._mine_late(_s("cbb", 0.99))
