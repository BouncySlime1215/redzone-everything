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


def test_crunch_block_starts_no_catch_up_while_any_game_is_in_crunch():
    """[crunch block] Cycle 04 review: on locked, NHL crunch plays aired under a catch-up 2 -> 10. With
    replay.crunch_block no catch-up starts while ANY live game is in crunch time or its run-up."""
    from redzone.decider.decider import Decider, Missed
    from redzone.decider.screen import DelayBook

    class Cfg(dict):
        def get(self, k, default=None):
            return super().get(k, default)
    dec = Decider(Cfg({"replay.crunch_block": True}), DelayBook({}, 15.0))
    dec.current = "A"
    dec.queue = [Missed("B", "p1", "goal", "goal", 1000.0, 50.0)]
    calm = NS(crunch=False, crunch_now=False, garbage=False)
    cands = {"A": NS(target="youtubetv:a", stale=False, scored=calm), "B": NS(target="youtubetv:b", stale=False, scored=calm),
             "C": NS(target="youtubetv:c", stale=False, scored=calm)}
    assert not dec._crunch_on(cands)
    assert dec._best_replay(cands, 1100.0, None, False) is dec.queue[0]
    cands["C"] = NS(target="youtubetv:c", stale=False, scored=NS(crunch=True, crunch_now=False, garbage=False))
    assert dec._crunch_on(cands)                       # the run-up elsewhere is enough
    assert dec._best_replay(cands, 1100.0, None, False) is None
    mine = Missed("B", "p2", "TD", "td", 1010.0, 50.0, mine=True)
    dec.queue.append(mine)
    dec._known["B"] = NS(scored=NS(league=NS(base=NS(value="nfl"))))
    assert dec._best_replay(cands, 1050.0, None, False) is None     # your team's waits out crunch...
    assert dec._best_replay(cands, 1400.0, None, False) is None     # ...all of it outside the listed leagues
    dec.cfg["replay.crunch_block_mine_leagues"] = ["nfl"]
    assert dec._best_replay(cands, 1050.0, None, False) is None     # NFL: until 90 s after it aired
    assert dec._best_replay(cands, 1110.0, None, False) is mine
    dec.queue.remove(mine)
    cands["C"].scored.garbage = True                   # a decided game's last minutes don't hold replays
    assert not dec._crunch_on(cands)
    dec.cfg = Cfg({})                                  # off by default
    cands["C"].scored.garbage = False
    assert not dec._crunch_on(cands)


def test_low_leverage_leave_rules_and_sticky_floors():
    """[cut rules, Nick Nov 4 3:15 PM] Never JOIN mid-play; LEAVING mid-play is OK at low leverage only.
    Down the stretch (close and late), crunch, locked moments and your team's game are sticky."""
    from redzone.decider.decider import Decider
    from redzone.decider.screen import DelayBook

    class Cfg(dict):
        def get(self, k, default=None):
            return super().get(k, default)

    def cand(**kw):
        base = dict(league=NS(base=NS(value="nba")), crunch=False, crunch_now=False, lock=None, imminent=False,
                    pre_moment=None, personal=0.0, late=False, garbage=False, closeness=0.9)
        base.update(kw)
        return NS(target="youtubetv:a", stale=False, scored=NS(**base))
    dec = Decider(Cfg({"decider.leave.low_leverage": ["nfl", "nba"]}), DelayBook({}, 15.0))
    assert dec._leavable(cand())                                       # Q2 of a close game: leavable
    assert not dec._leavable(cand(late=True))                          # close and late: sticky
    assert dec._leavable(cand(late=True, closeness=0.3))               # late but not close: leavable
    assert not dec._leavable(cand(late=True, closeness=0.9, garbage=False, crunch=True))
    assert not dec._leavable(cand(lock="red zone"))
    assert not dec._leavable(cand(personal=5.0))                       # your team's game
    assert not dec._leavable(cand(league=NS(base=NS(value="nhl"))))    # hockey: off (dev: 1-3 mid-play entries)
    dec.cfg = Cfg({})
    assert not dec._leavable(cand())                                   # off by default
