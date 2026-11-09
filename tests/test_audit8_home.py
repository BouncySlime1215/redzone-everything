"""AUDIT-8: what a break visit remembers. The return time and the home game die with the visit, and a return the flip
refused because the home game is still in its break isn't re-proposed every 12 s."""
from redzone.decider.decider import Candidate, Decider, Decision, State
from redzone.decider.screen import DelayBook, Event
from redzone.models import League
from redzone.scoring.engine import Scored

MU = 35.0


def sc(gid, score):
    return Scored(gid, League.NFL, score, score, score, 1.0, 0.0, f"reason {gid}", [], False, None, None, 1.0)


def cand(gid, score=60):
    return Candidate(sc(gid, score), f"youtubetv:{gid}")


def setup(cfg, clock):
    delays = DelayBook({"youtubetv": MU}, sigma_default=15.0, clock=clock)
    dec = Decider(cfg, delays, clock=clock)
    dec.deadline_cost = lambda target: 4.0
    for gid in ("a", "b", "c"):
        for v in (MU - 0.5, MU, MU + 0.5):
            delays.calibrate(f"youtubetv:{gid}", v)
        dec.timeline(gid, League.NFL)
        dec._known[gid] = cand(gid)
        dec._target_of[gid] = f"youtubetv:{gid}"
    dec.current = "b"
    return dec


def test_a_cancelled_visits_return_time_is_not_kept(cfg, clock):
    """Stale _home_at: 'what we're on is clearly better' cleared the home game but kept the time, so the next visit
    from here started already overdue: home_due at once, back to a game on a break."""
    dec = setup(cfg, clock)
    dec._home_set("a", clock.t + 100, "long-break visit")
    dec._home_set(None, dec._home_at, "what we're on is clearly better")
    assert dec.home_return_at is None
    clock.t += 500
    dec._home_set("b", dec._home_at, "a break visit")
    assert not dec._home_due("b", cand("b"), clock.t) or dec.home_m.is_("visit")
    assert dec.home_return_at is None and dec.home_m.is_("visit")


def test_the_way_home_only_forgets_a_time_with_no_visit_left(cfg, clock):
    dec = setup(cfg, clock)
    dec._home_set("a", clock.t + 100, "long-break visit")
    dec._home_set("a", dec._home_at, "a chained visit keeps its way back")
    assert dec.home_return_at == clock.t + 100 and dec.home_m.is_("visit_timed")


def test_landing_on_the_home_game_by_any_switch_ends_the_visit(cfg, clock):
    """Stale _home: only kind 'home' cleared it. Reaching the home game as a 'better' switch left it set, and the next
    better game was pulled straight back as 'back to your game' (A -> B -> A)."""
    dec = setup(cfg, clock)
    dec._home_set("a", None, "a break visit")
    dec._commit("a", kind="better")
    assert dec.home_break_visit is None and dec.home_m.is_("none")


def test_landing_elsewhere_keeps_the_visit(cfg, clock):
    dec = setup(cfg, clock)
    dec._home_set("a", None, "a break visit")
    dec._commit("c", kind="better")
    assert dec.home_break_visit == "a"


def _halftime(dec, clock):
    tl = dec.timelines["a"]
    tl.events.append(Event(clock.t - 300, "stop_start", "halftime"))
    tl.final_at = None
    return tl


def test_a_refused_trip_home_stops_being_proposed_while_the_break_airs(cfg, clock):
    """Seed-2 night, 17:56: halftime visit, home_return_at due, the flip vetoed 'home' (entry_invalid: break_on_arrival) 16
    times in a row, 12 s each, holding the control lock for 3 minutes. Refused once, the visit goes back on the data."""
    dec = setup(cfg, clock)
    _halftime(dec, clock)
    dec._home_set("a", clock.t - 1, "long-break visit")
    home = cand("a")
    home.scored.is_break = True
    assert dec._home_due("a", home, clock.t)                         # the planned time
    dec.reject(Decision(clock.t, "switch", "a", "back to your game", State.WATCHING, kind="home"))
    assert dec.home_break_visit == "a" and dec.home_return_at is None
    assert not dec._home_due("a", home, clock.t)                     # waits for the break to end in the data
    assert dec.home_m.is_("visit")


def test_a_refusal_with_the_break_over_keeps_the_time(cfg, clock):
    dec = setup(cfg, clock)                                          # no stoppage in the data: nothing to wait out
    dec._home_set("a", clock.t - 1, "long-break visit")
    dec.reject(Decision(clock.t, "switch", "a", "back to your game", State.WATCHING, kind="home"))
    assert dec.home_return_at == clock.t - 1


def test_a_superseded_trip_home_keeps_the_time(cfg, clock):
    dec = setup(cfg, clock)
    _halftime(dec, clock)
    dec._home_set("a", clock.t - 1, "long-break visit")
    dec.reject(Decision(clock.t, "switch", "a", "back to your game", State.WATCHING, kind="home"), backoff=False)
    assert dec.home_return_at == clock.t - 1
