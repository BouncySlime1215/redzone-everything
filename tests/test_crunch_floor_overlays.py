"""[SAFE-09] Crunch = stay sits above every model (Nick, Nov 4 3:30 PM).

The Mind's 100% crunch unsafe-leaves are a training-data failure. The live app's rule floor keeps them off
the TV: whatever any overlay or model proposes (scenario, clairvoyance, PICK, and later the Mind and watch
value), the screen stays on a game inside its crunch window unless the app's own crunch rules, another
crunch or your hand move it. No candidate profile may turn the floor off."""
from types import SimpleNamespace as NS

import pytest

from redzone.decider.decider import Decider, Decision, Missed, State
from redzone.decider.screen import DelayBook
from redzone.noggin import profile

OVERLAYS = ["_scenario", "_clairvoyant", "_crunch_guard", "_pick", "_hold_for_big", "_time_entry"]


class Cfg(dict):
    def get(self, k, default=None):
        return super().get(k, default)


def _sc(**kw):
    base = dict(crunch=False, crunch_now=False, garbage=False, lock=None, personal=0.0, score=50.0)
    base.update(kw)
    return NS(**base)


def _dec(cfg=None):
    dec = Decider(Cfg(cfg or {}), DelayBook({}, 15.0))
    dec.current = "A"
    return dec


def _cands(a_crunch=True, b_crunch=False):
    return {"A": NS(target="yt:a", stale=False, scored=_sc(crunch=a_crunch, crunch_now=a_crunch)),
            "B": NS(target="yt:b", stale=False, scored=_sc(crunch=b_crunch, crunch_now=b_crunch, score=99.0))}


def _rogue(now, kind="better", replay=False):
    if replay:
        m = Missed("B", "p1", "touchdown", "touchdown", now - 60.0, 90.0)
        return Decision(now, "replay", "B", "rogue model: catch-up", State.WATCHING, kind="replay", replay=m)
    return Decision(now, "switch", "B", "rogue model: B scores 0.99", State.WATCHING, kind=kind)


def _stay(now):
    return Decision(now, "stay", "A", "app: crunch time here", State.WATCHING)


@pytest.mark.playbook("SAFE-09")
@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.parametrize("overlay", OVERLAYS)
def test_no_overlay_or_model_takes_the_screen_off_crunch(monkeypatch, overlay, replay):
    """Every overlay in decide()'s chain is replaced by a rogue model that leaves the crunch game for a
    non-crunch game B (a switch or a catch-up of B). The floor puts the screen back on A, every time."""
    dec = _dec()
    now = 1000.0
    dec._clock = lambda: now
    cands = _cands()
    monkeypatch.setattr(dec, "_decide", lambda c, t: _stay(t))
    for name in ("_with_redzone", "_with_reel", "_nfl_priority"):
        monkeypatch.setattr(dec, name, lambda c, *a, **k: c)
    for name in ("_learn_entries", "_scan_missed"):
        monkeypatch.setattr(dec, name, lambda *a, **k: None)
    for name in OVERLAYS:
        if name == overlay:
            monkeypatch.setattr(dec, name, lambda d, *a, **k: _rogue(now, replay=replay))
        else:
            monkeypatch.setattr(dec, name, lambda d, *a, **k: d)
    monkeypatch.setattr(dec, "_safe", lambda d, *a, **k: d)
    out = dec.decide(cands)
    assert (out.action, out.target) == ("stay", "A"), out
    assert dec.current == "A" and dec.replaying is None
    assert dec.lookahead_why["crunch_floor"] == 1


@pytest.mark.playbook("SAFE-09")
def test_floor_lets_the_apps_own_crunch_rules_another_crunch_and_your_hand_through():
    now = 1000.0
    dec = _dec()
    # the app's own call (e.g. a hop to another live crunch at a long stoppage): untouched
    d0 = _rogue(now, kind="crunch")
    assert dec._crunch_floor(d0, d0, _cands(), now, (None, 0.0)) is d0
    # an overlay leaving for another game that is in crunch time: league order already chose
    d = _rogue(now)
    assert dec._crunch_floor(d, _stay(now), _cands(b_crunch=True), now, (None, 0.0)) is d
    # your hand
    du = _rogue(now, kind="user")
    assert dec._crunch_floor(du, _stay(now), _cands(), now, (None, 0.0)) is du
    # not crunch on screen, or garbage time: the floor doesn't apply
    assert dec._crunch_floor(d, _stay(now), _cands(a_crunch=False), now, (None, 0.0)) is d
    c = _cands()
    c["A"].scored.garbage = True
    assert dec._crunch_floor(d, _stay(now), c, now, (None, 0.0)) is d
    # a catch-up of the crunch game itself is the app's crunch-replay rule, not a leave
    m = Missed("A", "p1", "touchdown", "touchdown", now - 60.0, 90.0)
    dr = Decision(now, "replay", "A", "instant replay", State.WATCHING, kind="replay", replay=m)
    assert dec._crunch_floor(dr, _stay(now), _cands(), now, (None, 0.0)) is dr
    assert "crunch_floor" not in dec.lookahead_why


@pytest.mark.playbook("SAFE-09")
def test_floor_is_on_by_default_and_no_candidate_profile_turns_it_off():
    from redzone.config import Config
    assert Config().get("decider.crunch.floor") is True
    for name in profile.names():
        for lg in ("nfl", "cfb", "nba", "cbb", "nhl", "mlb"):
            args = profile.argv(name, lg)
            assert not any(a.startswith("decider.crunch.floor") for a in args), (name, lg)
