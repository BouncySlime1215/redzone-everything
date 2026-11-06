"""Nick, Nov 5: alert when auto-switching is left paused during games, and when nothing switches for
20+ min with several live games (that night: paused until 10:12 PM, then 0 switches for 70+ min)."""
import time
from types import SimpleNamespace as NS

from redzone.models import Phase
from redzone.platform.health import SWITCH_ALERTS, PlatformHealth


class Cfg(dict):
    def get(self, k, d=None):
        return super().get(k, d)


def engine(paused=False, since_ago=1500.0, live=3, pending=None, live_control=True, cfg=None):
    games = {f"g{i}": NS(phase=Phase.LIVE) for i in range(live)}
    games["done"] = NS(phase=Phase.POST)
    where = {gid: NS(target=f"youtubetv:c{gid}") for gid in games}
    where["g0"] = NS(target=None)   # live but no channel we can tune: doesn't count
    ctl = NS(live_control=live_control, _task=None, _flipping=False, input_unknown=False, _lock=None,
             sticks=[], tv=None)
    dec = NS(user=NS(paused=paused), since=time.time() - since_ago, current="g1")
    return NS(control=ctl, decider=dec, merger=NS(games=games), where=where, feeds=None,
              _pending_decision=pending, last_decision=NS(reason="play is live (only leave at a stoppage)"),
              cfg=cfg if cfg is not None else Cfg())


def codes(e):
    return [c for c, _, _ in PlatformHealth(e).conditions() if c in SWITCH_ALERTS]


def test_paused_with_live_games_is_flagged():
    assert codes(engine(paused=True, since_ago=10)) == ["paused_in_games"]


def test_no_switch_for_20_minutes_with_several_live_games_is_flagged():
    (c, comp, detail), = [x for x in PlatformHealth(engine()).conditions() if x[0] in SWITCH_ALERTS]
    assert c == "no_switch" and "25 min" in detail and "play is live" in detail


def test_quiet_cases_are_not_flagged():
    assert codes(engine(since_ago=600)) == []              # switched 10 min ago
    assert codes(engine(live=2)) == []                     # one tunable live game: nowhere to go
    assert codes(engine(pending=object())) == []           # a switch is running
    assert codes(engine(live_control=False)) == []         # dry run
    assert codes(engine(paused=True, live=1)) == []        # no tunable live game


def test_thresholds_come_from_config():
    cfg = Cfg({"health.no_switch_s": 300, "health.no_switch_min_games": 3})
    assert codes(engine(since_ago=400, live=4, cfg=cfg)) == ["no_switch"]
    assert codes(engine(since_ago=400, live=3, cfg=cfg)) == []


def test_paused_alert_has_a_grace_period():
    h = PlatformHealth(engine(paused=True), mono=lambda: 0.0)
    assert not [f for f in h.faults(time.time()) if f.code == "paused_in_games"]
    h.mono = lambda: 121.0
    assert [f for f in h.faults(time.time()) if f.code == "paused_in_games"]
