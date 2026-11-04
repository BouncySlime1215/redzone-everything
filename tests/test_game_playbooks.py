"""GAME-05/06/09/10/21/22 (docs/playbooks.md): crunch overlaps, empty net, OT, review/injury as breaks."""
from types import SimpleNamespace as NS

import pytest

from redzone.decider import decider as D
from redzone.decider import scenarios as sc
from redzone.decider.decider import Decider, Decision, State
from redzone.decider.screen import DelayBook
from redzone.models import League


class Cfg(dict):
    def get(self, k, default=None):
        return super().get(k, default)


def dec(**flags):
    return Decider(Cfg(flags), DelayBook({}, 15.0))


def sc_(lg=League.NHL, crunch=True, crunch_now=True, lock=None, closeness=0.8, personal=0, score=50.0,
        is_break=False):
    return NS(league=lg, crunch=crunch, crunch_now=crunch_now, lock=lock, closeness=closeness, personal=personal,
              score=score, is_break=is_break, garbage=False)


def cand(s, gid):
    return NS(target=f"yt:{gid}", stale=False, scored=s)


@pytest.mark.playbook("GAME-05")
def test_game05_higher_stakes_crunch_stays_live():
    d = dec(**{"playbooks.game.crunch_overlap": True})
    d.current = "A"
    reg, ot = sc_(), sc_(lock="overtime")
    cands = {"A": cand(reg, "A"), "B": cand(ot, "B")}
    assert d._crunch_go(cands, "B", reg, False, 0.0)            # sudden death elsewhere: go (at a safe gap)
    cands = {"A": cand(ot, "A"), "B": cand(reg, "B")}
    assert not d._crunch_go(cands, "B", ot, True, 0.0)          # never leave sudden death for regulation
    tied, up1 = sc_(closeness=1.0), sc_(closeness=0.7)
    cands = {"A": cand(up1, "A"), "B": cand(tied, "B")}
    assert d._crunch_go(cands, "B", up1, False, 0.0)            # the closer game wins
    assert d.overlap_why["higher"] == 2 and d.overlap_why["stay"] == 1


@pytest.mark.playbook("GAME-05")
def test_game05_off_keeps_the_hop_at_stoppages():
    d = dec()
    d.current = "A"
    reg, ot = sc_(), sc_(lock="overtime")
    cands = {"A": cand(reg, "A"), "B": cand(ot, "B")}
    assert not d._crunch_go(cands, "B", reg, False, 0.0)        # off: same tier hops only on a stoppage here
    assert not d.overlap_why


@pytest.mark.playbook("GAME-06")
def test_game06_league_order_decides_across_leagues():
    d = dec(**{"playbooks.game.crunch_overlap": True})
    d.current = "A"
    nhl_ot, nba = sc_(lock="overtime"), sc_(League.NBA, closeness=0.5)
    cands = {"A": cand(nhl_ot, "A"), "B": cand(nba, "B")}
    assert d._crunch_go(cands, "B", nhl_ot, False, 0.0)         # NBA > NHL even against sudden death
    cands = {"A": cand(nba, "A"), "B": cand(nhl_ot, "B")}
    assert not d._crunch_go(cands, "B", nba, True, 0.0)
    assert not d.overlap_why                                    # GAME-05 stakes never cross leagues


@pytest.mark.playbook("GAME-09")
def test_game09_pulled_goalie_counts_as_crunch():
    en = cand(sc_(crunch=False, crunch_now=False, lock="empty net"), "E")
    quiet = cand(sc_(crunch=False, crunch_now=False), "Q")
    assert dec()._crunch_pick({"E": en, "Q": quiet}) is None
    assert dec(**{"playbooks.game.empty_net": True})._crunch_pick({"E": en, "Q": quiet}) == "E"


@pytest.mark.playbook("GAME-10")
@pytest.mark.parametrize("league,period", [(League.NFL, 5), (League.NBA, 5), (League.NHL, 4)])
def test_game10_overtime_is_crunch_time_in_every_league(league, period):
    from redzone.models import GameState, Phase, Team
    from redzone.scoring.engine import crunch_time
    g = GameState(game_id="g", league=league, home=Team("H", score=3), away=Team("A", score=3))
    g.phase, g.period, g.clock_s = Phase.LIVE, period, 200.0
    assert crunch_time(g)


@pytest.mark.playbook("GAME-21", "GAME-22")
@pytest.mark.parametrize("stop", ["review", "injury"])
def test_game21_22_review_and_injury_are_breaks_to_leave(monkeypatch, stop):
    monkeypatch.setattr(sc, "state_of", lambda dec, gid, x, now: dec._states[gid])
    s = sc.Scenarios(Cfg({"playbooks.game.enabled": True}))
    nf = lambda score=50.0, **kw: NS(target="yt", stale=False, scored=NS(crunch=False, personal=0, score=score,  # noqa: E731
                                                                        league=NS(base=NS(value="nfl")), **kw))
    d = NS(current="A", cfg=Cfg(), user=NS(muted=set()), _states={"A": stop, "B": "redzone", "C": "live"})
    p = s.step(d, {"A": nf(), "B": nf(60), "C": nf()}, 0.0)
    assert p.branch["id"] == ("GAME-21" if stop == "review" else "GAME-22") and p.b == "B"
    assert p.branch["do"] == "visit" and p.branch["back"] == "restart"
    s.plan = None
    d._states["C"] = "crunch"                                   # crunch first: no visit while one is on
    cands = {"A": nf(), "B": nf(60), "C": nf()}
    cands["C"].scored.crunch = True
    assert s.step(d, cands, 0.0) is None


@pytest.mark.playbook("GAME-04")
def test_clairvoyance_never_steals_the_pretune_from_crunch_time(monkeypatch):
    monkeypatch.setattr(D, "ahead", lambda tl, dl, now, m: NS(big=NS(lo=1.0, label="TD")) if tl == "C" else None)
    d = dec(**{"noggin.lookahead.enabled": True})
    d.current = "A"
    d.timelines["C"] = "C"
    cands = {"A": cand(sc_(League.NFL, crunch=False, crunch_now=False), "A"),
             "B": cand(sc_(League.NFL), "B"), "C": cand(sc_(League.NFL, crunch=False, crunch_now=False), "C")}
    want = Decision(0.0, "stay", "A", "want crunch time", State.WATCHING, want="B")
    assert d._clairvoyant(want, cands, 0.0, (None, 0.0)) is want
