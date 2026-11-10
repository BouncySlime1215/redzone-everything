"""[legal.leave_low_leverage] [arbiter.catchup_route] More exits for a held pick, and the catch-up route. Both off by default.

Why: Nick's goal is value lost to code refusals at 0, with Rule #1 hard. These pin the three things that may never slip: a high-leverage moment is
never left, nothing airs 180 s or more late, and a clip that spoils a result already seen airs marked as a replay.
"""
from collections import Counter, deque
from types import SimpleNamespace as NS

import pytest

from redzone.decider import gate
from redzone.decider.decider import Candidate, Decider, Decision, FocusMode, Missed, State
from redzone.decider.screen import Delay, Event, Timeline
from redzone.models import League
from redzone.noggin import arbiter as A
from redzone.noggin import catchup as C
from redzone.scoring.engine import Scored
from scripts import veto_audit as va


def scored(league=League.NFL, **kw):
    s = Scored.__new__(Scored)
    s.__dict__.update({"personal": 0, "crunch": False, "crunch_now": False, "high_leverage": False, "late": False, "lock": None, "league": league,
                       "garbage": False, "score": 50.0, "game_id": "on", "mine": False, "imminent": False, "pre_moment": False, "closeness": 0.0, "is_break": False, **kw})
    return s


def cand(league=League.NFL, **kw):
    x = Candidate.__new__(Candidate)
    x.__dict__.update(scored=scored(league, **kw), target="yt:on")
    return x


class Cfg:
    def __init__(self, **v):
        self.v = v

    def get(self, k, d=None):
        return self.v.get(k, d)


def host(leagues=("nfl",), **kw):
    """A decider whose last play has aired while the feed went quiet (no data covers the screen, so the plain leave says no): the one
    state the held exit can open since it refuses while anything is still airing. Only the new rule can open the leave."""
    d = Decider.__new__(Decider)
    d.cfg = Cfg(**{"legal.leave_low_leverage": list(leagues), "legal.leave_at_flip": True, "decider.uncalibrated_sigma_s": 8.0,
                   "decider.crunch.hold_personal": 5, "decider.leave.sticky_closeness": 0.5, **kw})
    tl = Timeline(League.NFL)
    tl.fresh_at = 150.0
    tl.events.append(Event(150.0, "play"))        # aired on screen around 185 at a 35 s delay; nothing from the feed since
    d.timelines, d.delays, d.current, d.replaying, d.after_replay, d.safe_mode = {"on": tl}, NS(get=lambda t: Delay(35.0, 1.0), held={}), "on", False, False, False
    d._yours, d._high_lev_at, d._known, d._target_of, d._crunch_tail = set(), {}, {}, {}, {}
    d.entry_cost, d.user = None, NS(paused=False, locked_game=None, manual_until=0.0, focus_mode=FocusMode.AUTOPILOT)
    d.history, d.log, d._home = deque(), [], None
    return d


def flip(d, cands, *, held=True, now=200.0):
    dec = Decision(now, "switch", "a", "", State.WATCHING, kind="better", leave_held=held)
    h = gate.DeciderHost(d, {**cands, "a": cands["a"]}, now)
    return gate._leave(gate.Flip(h, dec, "on", "yt:a", cands, None, now))


# ----- Part A: a held pick's exit at low leverage ---------------------------------------------------------------------------------

def test_a_held_pick_may_leave_a_low_leverage_play_only_with_the_flag():
    cands = {"on": cand(), "a": cand()}
    assert flip(host(()), cands) is True                     # flag off: the mid-play leave stays refused
    assert flip(host(("nfl",)), cands) is False
    assert flip(host(("nba",)), cands) is True               # per league


@pytest.mark.parametrize("break_it", [lambda d: d.timelines.clear(), lambda d: d.timelines["on"].events.clear()])
def test_a_held_pick_never_leaves_without_screen_timing(break_it):
    """Rule #1 side: leave_open refuses a game it has no timing for, and the exit must not overrule that."""
    d = host(("nfl",))
    cands = {"on": cand(), "a": cand()}
    assert d.held_low_leverage_exit("on", cands, 200.0) is True
    break_it(d)
    assert d.held_low_leverage_exit("on", cands, 200.0) is False


def test_a_held_pick_never_leaves_while_the_last_play_is_still_airing():
    """Rule #1 side: seen at 165, airing until about 207 at a 35 s delay (leave_open's _ending_still_airing)."""
    d = host(("nfl",))
    d.timelines["on"].events.append(Event(165.0, "play"))
    assert d._ending_still_airing("on", 200.0) > 0
    assert d.held_low_leverage_exit("on", {"on": cand(), "a": cand()}, 200.0) is False


def test_a_pick_that_was_not_held_never_reads_the_new_rule():
    assert flip(host(("nfl",)), {"on": cand(), "a": cand()}, held=False) is True


@pytest.mark.parametrize("kw", [{"high_leverage": True}, {"crunch": True}, {"crunch_now": True}, {"lock": "red zone"}, {"mine": True}, {"personal": 9},
                                {"imminent": True}, {"pre_moment": True}, {"late": True, "closeness": 0.9}])
def test_high_leverage_or_a_moment_of_yours_is_never_left(kw):
    assert flip(host(("nfl",)), {"on": cand(**kw), "a": cand()}) is True


def test_a_high_leverage_moment_still_airing_on_screen_is_never_left():
    d = host(("nfl",))
    d._high_lev_at["on"] = 190.0        # the feed was close and late 10 s ago: at a 35 s delay that is what's on screen now
    assert flip(d, {"on": cand(), "a": cand()}) is True
    d._high_lev_at["on"] = 100.0        # long off the screen
    assert flip(d, {"on": cand(), "a": cand()}) is False


def test_your_teams_game_is_never_left():
    d = host(("nfl",))
    d._yours = {"on"}
    assert flip(d, {"on": cand(), "a": cand()}) is True


def test_the_wait_reads_per_league_and_the_slower_game_wins():
    d = host()
    d.cfg = Cfg(**{"legal.leave_wait_s": 45.0, "league_overrides.nfl": {"legal.leave_wait_s": 70.0}})
    d._lg = lambda x: {"on": "nba", "a": "nfl"}[x]
    assert d._leave_wait_s(NS(current="on", target="a")) == 70.0
    d.cfg = Cfg(**{"legal.leave_wait_s": 45.0})
    assert d._leave_wait_s(NS(current="on", target="a")) == 45.0


# ----- Part B: the pure route ------------------------------------------------------------------------------------------------------

M = C.Moment("td", "b", 5.0, aired=1000.0, play_s=7.0)
R = C.Route(reach_s=200.0)       # past the cap on purpose: the cap has to hold on its own


def test_the_hard_cap_is_180_seconds_strictly():
    start = M.aired - M.play_s - R.lead_s
    assert C.on_time(M, start + 179.9, 0.0, R)
    assert not C.on_time(M, start + 180.0, 0.0, R)
    assert not C.on_time(M, start + 300.0, 0.0, R)


def test_a_clip_past_the_cap_is_never_offered_whatever_the_reach():
    start = M.aired - M.play_s - R.lead_s
    now = start + 180.0 - R.flip_s      # the picture would be up at exactly 180 s
    assert C.next_piece([M], now, now + 200.0, r=R) is None
    now -= 1.0
    p = C.next_piece([M], now, now + 200.0, r=R)
    assert p is not None and p.seconds == R.clip_s


def test_a_later_piece_can_not_break_the_cap():
    start = M.aired - M.play_s - R.lead_s
    now = start + 170.0                 # the later piece airs behind where the first left off
    assert C.next_piece((), now, now + 200.0, doing=(M, 12.0), ready_at=now, r=R) is not None
    assert C.overdue(M, 12.0, start + 12.0 + 180.0 - R.flip_s + 1.0, R)
    late = start + 12.0 + 180.0
    assert C.next_piece((), late, late + 200.0, doing=(M, 12.0), ready_at=late, r=R) is None


def test_reach_limits_a_piece_inside_the_cap():
    r = C.Route(reach_s=95.0)
    start = M.aired - M.play_s - r.lead_s
    assert C.next_piece([M], start + 95.0 - r.flip_s, start + 300.0, r=r) is not None
    assert C.next_piece([M], start + 95.0 - r.flip_s + 1.0, start + 300.0, r=r) is None


def test_a_piece_fits_the_dead_time_and_returns_before_the_restart():
    now = 1100.0
    p = C.next_piece([M], now, now + 21.0, r=R)
    assert p is not None and not p.last and p.until <= now + 21.0 - R.margin_s
    assert p.seconds >= C.first_piece_s(M, R)


def test_the_first_piece_holds_the_whole_play():
    now = 1100.0
    short = 2.0 * R.flip_s + R.margin_s + C.first_piece_s(M, R) - 0.5
    assert C.next_piece([M], now, now + short, r=R) is None
    assert C.next_piece([M], now, now + short + 0.6, r=R) is not None


def test_a_clip_that_could_never_finish_is_not_started():
    now = M.aired - M.play_s - R.lead_s + 180.0 - R.flip_s - 8.0      # opening piece on time, but a later piece never could be
    assert C.next_piece([M], now, now + 25.0, r=R) is None


def test_the_stick_must_be_ready_first():
    assert C.next_piece([M], 1000.0 + R.prep_s - 1.0, 1100.0, r=R) is None
    assert C.next_piece([M], 1030.0, 1100.0, ready_at=1031.0, r=R) is None


def test_a_result_already_seen_is_marked_as_a_replay():
    p = C.next_piece([M], 1100.0, 1200.0, known=lambda m: True, r=R)
    assert p is not None and p.marked
    assert not C.next_piece([M], 1100.0, 1200.0, r=R).marked


# ----- the audit's route simulation --------------------------------------------------------------------------------------------------

def truth(monkeypatch, moments, dead=(True, False, 100.0), screen_game="a"):
    t = va.Truth()
    t.lag, t.tune_s, t.moments = 0.0, 5.0, moments
    t.replays = {screen_game: NS(league=League.NFL, wall=[0.0], plays=[{}], gid=screen_game)}
    monkeypatch.setattr(va, "dead_at", lambda *a: dead)
    return t


def test_route_shows_a_clip_within_the_cap_only(monkeypatch):
    air = va.Air(1000.0, "b", "td", 5.0)
    t = truth(monkeypatch, {"b": [air]})
    shown, stats = va.route_shown([air], va.Tally(route_on=True, route2=[(1040.0, "a", True)]), t, 95)
    assert shown == {air: False} and stats["value"] == 5.0
    shown, _ = va.route_shown([air], va.Tally(route_on=True, route2=[(1170.0, "a", True)]), t, 180)
    assert shown == {}          # 180 s or later is too late, at any reach
    shown, _ = va.route_shown([air], va.Tally(route_on=True, route2=[(1040.0 + 70.0, "a", True)]), t, 95)
    assert shown == {}          # past the 95 s reach


def test_route_spans_two_dead_balls_when_one_is_too_short(monkeypatch):
    air = va.Air(1000.0, "b", "td", 5.0)
    calls = iter([(True, False, 25.0), (True, False, 40.0)])
    t = truth(monkeypatch, {"b": [air]})
    monkeypatch.setattr(va, "dead_at", lambda *a: next(calls))
    shown, stats = va.route_shown([air], va.Tally(route_on=True, route2=[(1040.0, "a", True), (1090.0, "a", True)]), t, 180)
    assert shown == {air: False} and stats["pieces"] == 2 and stats["pieced_clips"] == 1


def test_route_a_plain_dead_ball_stays_closed_on_a_held_game_but_a_commercial_opens(monkeypatch):
    air = va.Air(1000.0, "b", "td", 5.0)
    t = truth(monkeypatch, {"b": [air]})
    assert va.route_shown([air], va.Tally(route_on=True, route2=[(1040.0, "a", False)]), t, 180)[0] == {}
    monkeypatch.setattr(va, "dead_at", lambda *a: (True, True, 100.0))
    assert va.route_shown([air], va.Tally(route_on=True, route2=[(1040.0, "a", False)]), t, 180)[0] == {air: False}


def test_route_never_airs_anything_at_180_seconds_or_later(monkeypatch):
    air = va.Air(1000.0, "b", "td", 5.0)
    t = truth(monkeypatch, {"b": [air]}, dead=(True, True, 60.0))
    tally = va.Tally(route_on=True, route2=[(float(n), "a", True) for n in range(1020, 1180, 5)])
    shown, stats = va.route_shown([air], tally, t, 400)
    assert shown and 0.0 < stats["max_late_s"] < 180.0
    row = va.new_row()
    va.charge_night(tally, t, row)
    assert row["route"]["180"]["max_late_s"] < 180.0


def test_route_never_runs_mid_play_or_into_the_replays_own_cut(monkeypatch):
    air = va.Air(1000.0, "b", "td", 5.0)
    t = truth(monkeypatch, {"b": [air]}, dead=(False, False, 0.0))
    assert va.route_shown([air], va.Tally(route_on=True, route2=[(1040.0, "a", True)]), t, 180)[0] == {}
    t = truth(monkeypatch, {"b": [air]})
    assert va.route_shown([air], va.Tally(route_on=True, route2=[(1040.0, "a", True)], screen=[(1050.0, "c")]), t, 180)[0] == {}


def test_route_marks_a_clip_whose_result_the_screen_already_showed(monkeypatch):
    air = va.Air(1000.0, "b", "td", 5.0)
    t = truth(monkeypatch, {"b": [air]})
    shown, stats = va.route_shown([air], va.Tally(route_on=True, route2=[(1040.0, "a", True)], screen=[(1020.0, "b"), (1030.0, "a")]), t, 180)
    assert shown == {air: True} and stats["marked_value"] == 5.0
    shown, _ = va.route_shown([air], va.Tally(route_on=True, route2=[(1040.0, "a", True)]), t, 180)
    assert shown == {air: False}


def test_route_a_later_clip_of_the_same_game_spoils_an_earlier_one(monkeypatch):
    old, new = va.Air(1000.0, "b", "fg", 1.0), va.Air(1010.0, "b", "td", 5.0)
    t = truth(monkeypatch, {"b": [old, new]})
    monkeypatch.setattr(C.arbiter, "plan_clips", lambda cands, *a, **k: max(cands, key=lambda c: c[2])[0])     # the newest goes first
    shown, _ = va.route_shown([old, new], va.Tally(route_on=True, route2=[(1040.0, "a", True), (1095.0, "a", True)]), t, 180)
    assert shown.get(new) is False and shown.get(old) is True    # the newer one went first (bigger value), so the older one knows the result


def test_the_old_route_stays_without_the_flag(monkeypatch):
    air = va.Air(1000.0, "b", "td", 5.0)
    t = truth(monkeypatch, {"b": [air]})
    row = va.new_row()
    va.charge_night(va.Tally(route=[(1050.0, "a")], route2=[(1040.0, "a", True)]), t, row)
    assert row["route"]["95"] == {} and sum(row["lost_after_catchup"]["95"].values()) == 0.0


# ----- the decider's route ----------------------------------------------------------------------------------------------------------

P = A.Params(catchup_route=True, catchup_route_reach_s=170.0)


def route_decider(**kw):
    d = Decider.__new__(Decider)
    d.cfg, d.current, d.safe_mode = Cfg(**{"replay.enabled": True, "arbiter.catchup_route": True, "arbiter.catchup_route_reach_s": 170.0, **kw}), "on", False
    d.user = NS(paused=False, locked_game=None, manual_until=0.0, focus_mode=FocusMode.AUTOPILOT, muted=set())
    d._home, d.history, d.log, d.timelines, d.delays = None, deque(), [], {}, NS(get=lambda t: Delay(35.0, 1.0))
    d._screen_log, d.queue, d.done_replays, d._high_lev_at, d._yours, d._known = [], [], [], {}, set(), {}
    d.buffered_on = lambda: False
    d._crunch_on = lambda c: False
    d._replay_pool = lambda c: c
    d._replayable = lambda x: True
    d._arbiter_missed_rank = lambda m, p: 0
    d._catchup_span = lambda cur, now: (now + 60.0, False)
    d._clock = lambda: 0.0
    return d


def miss(aired=1000.0, pid="p1"):
    return Missed("b", pid, "TD", "td", aired, 90.0, aired=aired)


CANDS = {"on": cand(), "b": cand()}


def test_the_decider_route_gives_a_piece_of_a_missed_play():
    d, m = route_decider(), miss()
    d.queue = [m]
    got = d._catchup_route(CANDS, 1040.0, P)
    assert got is m and m.piece_s == P.plan_clip_s and m.offset_s == 0.0 and not m.marked_replay
    assert d._replay_decision(got, State.REPLAY, "", 1040.0).reason.startswith("catch-up:")


def test_the_decider_route_marks_a_result_the_screen_showed():
    d, m = route_decider(), miss()
    d.queue, d._screen_log = [m], [(1010.0, "b"), (1030.0, "on")]
    got = d._catchup_route(CANDS, 1040.0, P)
    assert got is m and m.marked_replay and d._replay_decision(got, State.REPLAY, "", 1040.0).reason.startswith("instant replay:")


def test_the_decider_route_never_goes_past_the_cap():
    d, m = route_decider(), miss()
    d.queue = [m]
    assert d._catchup_route(CANDS, 1000.0 - 7.0 - 3.0 + 180.0, P) is None


@pytest.mark.parametrize("kw", [{"high_leverage": True}, {"crunch_now": True}, {"lock": "red zone"}])
def test_a_held_game_keeps_a_plain_dead_ball_but_not_a_commercial(kw):
    d, m = route_decider(), miss()
    d.queue = [m]
    cands = {"on": cand(**kw), "b": cand()}
    assert d._catchup_route(cands, 1040.0, P) is None
    d._catchup_span = lambda cur, now: (now + 60.0, True)
    assert d._catchup_route(cands, 1040.0, P) is m


def test_nick_keeps_the_screen_from_the_route():
    d, m = route_decider(), miss()
    d.queue, d.user.locked_game = [m], "on"
    assert d._catchup_route(CANDS, 1040.0, P) is None


def test_a_piece_seeks_past_what_already_aired():
    m = miss()
    full = m.back_s(1100.0, 35.0, 7.0)
    m.offset_s = 15.0
    assert m.back_s(1100.0, 35.0, 7.0) == full - 15.0


def test_a_clip_stays_owed_between_its_pieces_and_ends_on_the_last():
    d, m = route_decider(), miss()
    d.arbiter_log, d.cfg.v["arbiter.plan_clip_s"] = Counter(), 30.0
    m.piece_s, m.offset_s = 12.0, 0.0
    d._clip = m
    d._current = "b"
    d.screen_m.to("replaying", "t", now=1050.0)
    d.catchup_m.to("airing", "t", now=1050.0)
    assert d._route_piece_ended(1062.0) and not m.replayed and m in d.queue and d._route_doing == (m, 12.0)
    assert d.catchup_m.is_("between")
    m.piece_s, m.offset_s = 18.0, 12.0
    d._clip = m
    d.catchup_m.to("airing", "t", now=1080.0)
    assert not d._route_piece_ended(1098.0) and d.catchup_m.is_("idle")


def test_a_later_piece_with_the_cap_gone_is_dropped():
    d, m = route_decider(), miss()
    m.offset_s = 12.0
    d.queue, d._route_doing = [m], (m, 12.0)
    d.catchup_m.to("airing", "t", now=1050.0)
    d.catchup_m.to("between", "t", now=1060.0)
    assert d._catchup_route(CANDS, 1000.0 - 7.0 - 3.0 + 12.0 + 180.0, P) is None
    assert d._route_doing is None and m not in d.queue and d.catchup_m.is_("idle")


def test_the_shows_league_order_reaches_the_class_bonus():
    p = A.Params(class_bonus=True)
    d = route_decider()
    cands = {"on": cand(), "b": cand(League.NHL)}
    d.timelines = {}
    show = d._arbiter_show(cands, 1000.0, p)
    assert show.league_rank["on"] < show.league_rank["b"]
