"""Deadline budgets must describe this switch, not the average successful one."""
import pytest

from redzone.decider.decider import Candidate, Decider, Decision, Missed, State
from redzone.decider.screen import DelayBook, Event
from redzone.models import League
from redzone.scoring.engine import Scored


def candidate(gid, score=75):
    return Candidate(Scored(gid, League.NFL, score, score, score, 1.0, 0.0,
                            'touchdown', [], False, None, None, 1.0), f'youtubetv:{gid}')


@pytest.fixture
def dec(cfg, clock):
    cfg.update({'decider': {'switch_cost': {'youtubetv': 3}}})
    return Decider(cfg, DelayBook({'youtubetv': 40}, 1, clock=clock), clock=clock)


def test_cold_target_is_not_catchable_using_other_targets_preload_average(dec, clock):
    cands = {'cold': candidate('cold', 90), 'ready': candidate('ready', 70)}
    for gid in cands:
        dec.timeline(gid, League.NFL).events.append(Event(clock.t - 16, 'play', 'TD', True))
    # Both targets use the same provider, whose recent average is only three seconds.
    assert dec._chase_any(cands, clock.t, None, 0)[0] == 'cold'
    dec.deadline_cost = lambda target: 2.0 if target == 'youtubetv:ready' else 12.0
    assert dec._chase_any(cands, clock.t, None, 0)[0] == 'ready'
    assert dec._cost('youtubetv:cold') == 3.0  # Ranking remains separate.


def test_deadline_rechecks_current_readiness_instead_of_caching_provider_cost(dec, clock):
    cands = {'game': candidate('game')}
    dec.timeline('game', League.NFL).events.append(Event(clock.t - 16, 'play', 'TD', True))
    readiness = {'ready': True}
    dec.deadline_cost = lambda target: 2.0 if readiness['ready'] else 12.0
    assert dec._chase_any(cands, clock.t, None, 0)
    readiness['ready'] = False
    assert dec._chase_any(cands, clock.t, None, 0) is None


def test_replay_budget_includes_full_target_preparation(dec, clock):
    m = Missed('game', 'p1', 'TD', 'td', clock.t - 100, 75)
    cands = {'game': candidate('game')}
    dec.deadline_cost = lambda target: 17.0
    assert dec._replay_cost(m, cands) == 17 + dec.cfg.get('replay.seek_s') + dec.cfg.get('replay.clip_s')


def test_confirmed_replay_does_not_charge_tune_and_seek_twice(dec, clock):
    m = Missed('game', 'p1', 'TD', 'td', clock.t - 100, 75)
    cands = {'game': candidate('game')}
    dec.timeline('game', League.NFL)
    dec.deadline_cost = lambda target: 17.0
    d = Decision(clock.t, 'replay', 'game', 'catch up', State.REPLAY, replay=m)
    expected = dec.cfg.get('replay.clip_s') + dec._replay_overrun(m, cands, clock.t)
    dec.confirm(d, cands)
    assert dec.replay_until == clock.t + expected
    assert not m.replayed and not dec.done_replays


def test_interrupted_replay_stays_missed(dec, clock):
    m = Missed('game', 'p1', 'TD', 'td', clock.t - 100, 75)
    cands = {'game': candidate('game')}
    dec.queue.append(m)
    dec._start_replay(m, cands, clock.t, confirmed=True)
    old_exit = dec.replay_until
    clock.t += 5
    dec.abort_replay()
    assert not m.replayed and m in dec.queue and m not in dec.done_replays
    assert dec.replaying is None and dec.after_replay
    assert dec.replay_until == 0 and dec.replay_exit_after == old_exit
    dec.confirm(Decision(clock.t, 'switch', 'other', 'live', State.WATCHING), {'other': candidate('other')})
    assert dec.replay_exit_after == 0


def test_replay_is_credited_only_after_uninterrupted_duration(dec, clock):
    m = Missed('game', 'p1', 'TD', 'td', clock.t - 100, 75)
    cands = {'game': candidate('game')}
    dec._start_replay(m, cands, clock.t, confirmed=True)
    clock.t = dec.replay_until - 0.1
    dec.decide(cands, commit=False)
    assert not m.replayed
    clock.t += 0.2
    dec.decide(cands, commit=False)
    assert m.replayed and m in dec.done_replays and dec.replaying is None


def test_unknown_picture_does_not_credit_an_elapsed_replay(dec, clock):
    m = Missed('game', 'p1', 'TD', 'td', clock.t - 100, 75)
    cands = {'game': candidate('game')}
    dec._start_replay(m, cands, clock.t, confirmed=True)
    dec._screen_log.append((clock.t + 2, None))
    clock.t = dec.replay_until + 1
    dec.decide(cands, commit=False)
    assert not m.replayed and m in dec.queue


def test_transport_in_flight_blocks_duplicate_decisions_without_a_dwell_quota(dec, clock):
    cands = {'game': candidate('game')}
    dec.deadline_cost = lambda target: 12.0
    first = dec.decide(cands)
    assert first.action == 'switch'
    clock.t += 1
    next_decision = dec.decide({'other': candidate('other', 95), **cands})
    assert next_decision.action == 'stay' and next_decision.reason == 'switch in progress'
    # Real confirmation ends the transport hold immediately.
    dec.confirm(Decision(clock.t, 'switch', 'game', 'ready', State.WATCHING), cands)
    assert dec._arrival_until == 0


def test_typical_timeout_duration_is_not_a_guaranteed_replay_budget(dec, clock):
    tl = dec.timeline('game', League.NFL)
    tl.events.append(Event(clock.t - 45, 'stop_start', 'timeout'))
    # The default says a timeout typically lasts 75s. It may end much sooner.
    assert dec.cfg.get('break_lengths_s.nfl.timeout') == 75
    assert dec._break_remaining('game', {'game': candidate('game')}, clock.t) == 0


def test_guaranteed_break_budget_uses_earliest_uncertain_start(dec, clock):
    tl = dec.timeline('game', League.NFL)
    tl.events.append(Event(clock.t - 50, 'stop_start', 'end_period', spread=40))
    # Start could be90s ago, minimum120s, screen delay40±2s =>68s guaranteed left.
    assert dec._break_remaining('game', {'game': candidate('game')}, clock.t) == 68


def test_agreeing_taps_cannot_shrink_delay_doubt_below_the_tap_floor():
    """Tonight's case: three "I just saw it" taps a second apart. They share a bias the spread
    can't see, so the stated sigma stays >= MIN_TAP_SIGMA_S (2*sigma covers an 8 s real error)."""
    from redzone.decider.screen import MIN_TAP_SIGMA_S, DelayBook
    book = DelayBook({"espn": 40.0}, sigma_default=15.0, clock=lambda: 1000.0)
    for v in (52.0, 53.0, 52.5):
        d = book.calibrate("espn:mnf", v)
    assert d.mu == 52.5
    assert d.sigma == MIN_TAP_SIGMA_S >= 4.0
    # A real disagreement still widens it past the floor.
    for v in (40.0, 64.0):
        d = book.calibrate("espn:mnf", v)
    assert d.sigma == 12.0
