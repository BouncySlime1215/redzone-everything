"""[whistle detection, rule #1] NHL leaves also on the play-by-play's dead windows: a whistle whose faceoff
landed in the same poll never reached the game state, but cuts.DeadTracker still holds it."""
from types import SimpleNamespace as NS

from redzone.decider import decider as D
from redzone.decider.screen import Gate


def _dec(monkeypatch, league="nhl", until=1000.0):
    monkeypatch.setattr(D, "leave_gate", lambda *a, **k: Gate(False, "play is live (only leave at a stoppage)"))
    dec = D.Decider.__new__(D.Decider)
    dec.current = "g"
    dec.cfg = {"decider.leave.dead_windows": ["nhl"]}
    dec.timelines = {"g": NS(dead=NS(marks=[1], dead_until=lambda x0: until if x0 < until else None))}
    dec.delays = NS(get=lambda t: NS(mu=100.0, current_sigma=lambda now: 2.0))
    cur = NS(target="yt:g", scored=NS(league=NS(base=NS(value=league))))
    return dec, cur


def test_dead_window_on_screen_is_a_safe_leave(monkeypatch):
    dec, cur = _dec(monkeypatch)
    # screen shows 946..954 (mu 100, 2 sigma 4); the window runs to 1000 >= 954 + 6
    assert dec._gate(cur, 1050.0).safe
    # too close to the window's end for the tune margin: 1000 < 994 + 6 + ... -> unsafe
    assert not dec._gate(cur, 1095.0).safe
    assert not dec._gate(cur, 1200.0).safe          # past the window: live


def test_only_in_its_leagues(monkeypatch):
    dec, cur = _dec(monkeypatch, league="nba")
    assert not dec._gate(cur, 1050.0).safe
    from redzone.noggin import profile
    assert not any(a.startswith("decider.leave.dead_windows") for a in profile.argv("candidate", "nhl"))  # held out (cycle 06)
