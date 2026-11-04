"""Clairvoyance (redzone/noggin/lookahead.py, GAME-04): the plays the feed knows that haven't aired yet."""
from types import SimpleNamespace as NS

import pytest

from redzone.decider.cuts import DeadTracker
from redzone.decider.screen import Event
from redzone.models import League
from redzone.noggin.lookahead import ahead

pytestmark = pytest.mark.playbook("GAME-04")


def timeline():
    dt = DeadTracker(League.NFL)
    dt.add("1", 1000, 1000, "rush", "run for 3", "Rush", None, 1)
    dt.add("2", 1040, 1040, "pass", "pass for 8", "Pass Reception", None, 1)
    dt.add("3", 1080, 1080, "pass", "TD pass", "Passing Touchdown", None, 1)
    dt.seen(1100, 10)
    ev = [Event(1000, "play", "run for 3", pid="1"), Event(1040, "play", "pass for 8", pid="2"),
          Event(1080, "play", "TD pass", big=True, pid="3")]
    return NS(events=ev, dead=dt)


def delay(mu, sigma):
    return NS(mu=mu, current_sigma=lambda now: sigma)


def test_window_is_screen_now_to_the_newest_data():
    a = ahead(timeline(), delay(80.0, 3.0), 1100.0)      # the screen shows real time 1020
    assert a.screen_t == 1020.0
    assert [e.pid for e in a.plays] == ["2", "3"]
    assert a.big.pid == "3"
    assert all(w.start > 1020.0 for w in a.dead)


def test_nothing_ahead_once_it_aired():
    a = ahead(timeline(), delay(10.0, 3.0), 1100.0)      # screen at 1090: all three plays aired
    assert a.plays == [] and a.big is None


def test_no_clairvoyance_when_the_screen_position_is_a_guess():
    assert ahead(timeline(), delay(80.0, 11.0), 1100.0) is None          # SAFE-02's threshold
    assert ahead(timeline(), delay(80.0, 11.0), 1100.0, max_sigma_s=12.0) is not None
    assert ahead(None, delay(80.0, 3.0), 1100.0) is None


def test_last_dead_ball_before_the_big_play():
    a = ahead(timeline(), delay(80.0, 3.0), 1100.0)
    w = a.last_dead_before(a.big.lo, a.screen_t)
    assert w is not None and w.end <= 1080 and w.start > 1020
    assert a.next_dead(a.screen_t).start == min(x.start for x in a.dead)


def test_candidate_turns_it_on_with_crunch_wait():
    from redzone.noggin import profile
    args = profile.argv("candidate", "nba")
    assert "noggin.lookahead.enabled=true" in args and "decider.entry.crunch_now=wait" in args


def test_crunch_guard_keeps_a_catch_up_replay_off_a_crunch_game():
    from redzone.decider.decider import Decider, Decision, Missed, State
    from redzone.decider.screen import DelayBook

    class Cfg(dict):
        def get(self, k, default=None):
            return super().get(k, default)
    dec = Decider(Cfg({"noggin.lookahead.enabled": True}), DelayBook({}, 15.0))
    dec.current = "A"
    cands = {"A": NS(target="yt:a", stale=False, scored=NS(crunch_now=True))}
    m = Missed("B", "p1", "goal", "goal", 1000.0, 50.0)
    d = Decision(1100.0, "replay", "B", "catch-up: goal", State.WATCHING, kind="replay", replay=m)
    out = dec._crunch_guard(d, cands, 1100.0, (None, 0.0))
    assert out.action == "stay" and out.target == "A" and dec.lookahead_why["crunch_guard"] == 1
    cands["A"].scored.crunch_now = False
    assert dec._crunch_guard(d, cands, 1100.0, (None, 0.0)) is d
