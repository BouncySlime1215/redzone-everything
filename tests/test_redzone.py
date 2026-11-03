"""NFL RedZone as a pseudo-game, and the break timing that keeps commercials off your screen."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from redzone.channels import REDZONE_TARGET, in_redzone_window, redzone_on
from redzone.decider.decider import REDZONE, Candidate, Decider, Missed
from redzone.decider.screen import DelayBook, Event, Timeline, break_on_arrival, leave_gate
from redzone.lab import backtest as bt
from redzone.models import GameState, League, Phase, Stoppage, Team
from redzone.scoring.engine import Scored
from tests.test_decider import with_fresh_timing

ET = ZoneInfo("America/New_York")
SUN_1PM = datetime(2025, 11, 2, 13, 0, tzinfo=ET).timestamp()


def sc(gid, score, lock=None, is_break=False, league=League.NFL):
    return Scored(gid, league, score, score, score, 1.0, 0.0, f"reason {gid}", [], is_break,
                  lock, None, 1.0)


def cands(**scores):
    return {g: Candidate(v if isinstance(v, Scored) else sc(g, v), f"youtubetv:{g}") for g, v in scores.items()}


@pytest.fixture
def dec(cfg, clock):
    d = Decider(cfg, DelayBook({"youtubetv": 40.0}, sigma_default=15.0, clock=clock), clock=clock)
    for g in "abc":
        for v in (39, 40, 41):
            d.delays.calibrate(f"youtubetv:{g}", v)
        d.timeline(g, League.NFL)
    with_fresh_timing(d, clock)
    return d


def on(dec, clock, gid, **others):
    d = dec.decide(cands(**{gid: 99, **others}))
    assert d.target == gid
    clock.t += 100


def break_on_screen(dec, clock, gid="a", label="end_period"):
    """A break that's now showing on `gid`'s screen (a quarter break: long enough to visit)."""
    dec.timelines[gid].events.append(Event(clock.t, "stop_start", label))
    clock.t += 50


# ----- the channel ------------------------------------------------------------------------

def game(gid, start, phase=Phase.LIVE, league=League.NFL):
    return GameState(league, gid, Team("A"), Team("B"), phase=phase, start_time=start)


def test_redzone_covers_sunday_afternoon_windows_only():
    assert in_redzone_window(game("1", SUN_1PM))
    assert in_redzone_window(game("1", SUN_1PM + 3 * 3600 + 25 * 60))        # 4:25 ET
    assert not in_redzone_window(game("1", SUN_1PM + 7 * 3600 + 20 * 60))    # Sunday night
    assert not in_redzone_window(game("1", SUN_1PM - 3 * 86400))             # Thursday
    assert not in_redzone_window(game("1", SUN_1PM, league=League.NBA))


def test_redzone_needs_two_live_afternoon_games():
    assert redzone_on([game("1", SUN_1PM), game("2", SUN_1PM)])
    assert not redzone_on([game("1", SUN_1PM), game("2", SUN_1PM, phase=Phase.POST)])


# ----- the decider ------------------------------------------------------------------------

def test_break_goes_to_redzone_when_nothing_else_is_worth_it(dec, clock):
    on(dec, clock, "a", b=10)
    break_on_screen(dec, clock)
    d = dec.decide(cands(a=sc("a", 60, is_break=True), b=10), REDZONE_TARGET)
    assert d.action == "switch" and d.target == REDZONE and d.kind == "break_visit"
    assert dec.home_break_visit == "a"


def test_routine_timeout_is_ridden_out_at_home(dec, clock):
    """[pacing] A routine timeout is over before a RedZone visit pays off (median 20 s in the
    backtest): tuning away and straight back is the frantic flip-flop. Stay put."""
    on(dec, clock, "a", b=10)
    break_on_screen(dec, clock, label="timeout")
    d = dec.decide(cands(a=sc("a", 60, is_break=True), b=10), REDZONE_TARGET)
    assert d.action == "stay" and dec.home_break_visit is None


def test_crunch_timeout_still_visits(dec, clock):
    """In the final two minutes a timeout is still worth a quick look elsewhere."""
    on(dec, clock, "a", b=10)
    break_on_screen(dec, clock, label="timeout")
    d = dec.decide(cands(a=sc("a", 60, is_break=True, lock="final two minutes"), b=10), REDZONE_TARGET)
    assert d.action == "switch" and d.target == REDZONE


def test_without_redzone_on_the_air_you_wait_out_the_ad(dec, clock):
    on(dec, clock, "a", b=10)
    break_on_screen(dec, clock)
    assert dec.decide(cands(a=sc("a", 60, is_break=True), b=10)).action == "stay"


def test_redzone_switched_off_in_settings_is_never_used(dec, clock):
    dec.cfg.update({"decider": {"redzone_channel": False}})
    on(dec, clock, "a", b=10)
    break_on_screen(dec, clock)
    assert dec.decide(cands(a=sc("a", 60, is_break=True), b=10), REDZONE_TARGET).action == "stay"


def test_redzone_is_home_base_until_a_game_beats_it(dec, clock):
    on(dec, clock, "a", b=10)
    d = dec.decide(cands(a=10, b=10), REDZONE_TARGET)                       # nothing close: RedZone
    assert d.target == REDZONE and d.kind == "better"
    clock.t += 50
    d = dec.decide(cands(a=10, b=80), REDZONE_TARGET)                       # no play gate to wait for
    assert d.action == "switch" and d.target == "b"


def test_from_redzone_any_big_play_is_worth_chasing(dec, clock):
    on(dec, clock, "a", b=10)
    dec.decide(cands(a=10, b=10), REDZONE_TARGET)
    clock.t += 25
    dec.timelines["c"].events.append(Event(clock.t - 2, "play", "pick six", big=True))
    d = dec.decide(cands(a=10, b=10, c=5), REDZONE_TARGET)                   # c is far below RedZone's 45
    assert d.action == "switch" and d.target == "c" and d.kind == "chase"


def test_a_missed_play_beats_redzone_as_break_filler(dec, clock):
    on(dec, clock, "a", b=10)
    dec.queue.append(Missed("b", "fg1", "41-yard field goal", "fg", clock.t - 60, value=35))
    # A quarter break has a measured minimum; an ordinary timeout's typical
    # duration cannot guarantee enough time to replay and get back.
    dec.timelines["a"].events.append(Event(clock.t, "stop_start", "end_period"))
    clock.t += 50
    d = dec.decide(cands(a=sc("a", 60, is_break=True), b=10), REDZONE_TARGET)
    assert d.action == "replay" and d.target == "b"


def test_only_live_game_is_found_again_after_its_break(dec, clock):
    # Regression: the "screen shows a break" set went stale once the current game ended, hiding
    # the only game still on for the rest of a slate.
    on(dec, clock, "a", b=50)
    dec.timelines["b"].events.append(Event(clock.t, "stop_start", "timeout"))
    clock.t += 50
    dec.decide(cands(a=50, b=50))                                            # b's ad is on screen
    dec.timelines["b"].events.append(Event(clock.t, "stop_end"))
    clock.t += 300
    d = dec.decide(cands(b=50))                                              # a is final
    assert d.action == "switch" and d.target == "b"


# ----- break timing on screen -------------------------------------------------------------

def tl_with(league, *events):
    tl = Timeline(league)
    tl.events.extend(events)
    return tl


def calibrated(mu=40.0):
    book = DelayBook({"youtubetv": mu}, sigma_default=2.0, clock=lambda: 0.0)
    d = book.get("youtubetv:x")
    d.calibrated_at = 0.0
    return d


def test_leave_once_the_play_before_a_timeout_has_aired():
    # The timeout is logged 20s after the play; it airs at 60. The play is done airing by 47.
    tl = tl_with(League.NFL, Event(0, "play", "run for 2"), Event(20, "stop_start", "timeout"))
    tl.fresh_at = 44.0
    d = calibrated()
    assert not leave_gate(tl, d, 44.0, 8.0).safe
    tl.fresh_at = 48.0
    g = leave_gate(tl, d, 48.0, 8.0)
    assert g.safe and g.on_screen_break


def test_continuous_sports_wait_for_the_stoppage_itself():
    tl = tl_with(League.NBA, Event(0, "play", "layup"), Event(20, "stop_start", "timeout"))
    tl.fresh_at = 48.0
    assert not leave_gate(tl, calibrated(), 48.0, 8.0).on_screen_break


def test_ad_tail_lasts_until_the_snap_that_ends_it_has_aired():
    tl = tl_with(League.NFL, Event(0, "stop_start", "timeout"), Event(90, "stop_end"), Event(102, "play", "run"))
    d = calibrated()
    assert break_on_arrival(tl, d, 0.0, 130.0)                               # ad, then the snap airing
    assert not break_on_arrival(tl, d, 0.0, 143.0)                           # snap has aired (102 + 40)
    assert not break_on_arrival(tl, d, 0.0, 30.0)                            # before the timeout airs


# ----- the backtest grades RedZone honestly -----------------------------------------------

def replay(gid, walls, phases):
    plays = [{"id": f"{gid}{i}", "type": {"text": "Rush"}, "text": "run", "period": {"number": 1},
              "clock": {"displayValue": "10:00"}, "homeScore": 0, "awayScore": 0} for i in range(len(walls))]
    r = bt.Replay(League.NFL, gid, {}, plays, list(walls), matchup=f"X @ {gid}")
    for i, ph in enumerate(phases):
        r._cache[i] = GameState(League.NFL, gid, Team("X"), Team(gid), phase=ph, stoppage=Stoppage.NONE,
                                start_time=SUN_1PM)
    return r


def test_redzone_is_dead_air_once_it_goes_off_the_air():
    t = SUN_1PM + 600
    a = replay("A", [t, t + 2000], [Phase.LIVE, Phase.LIVE])
    b = replay("B", [t, t + 300], [Phase.LIVE, Phase.POST])                  # B ends: RedZone signs off
    rep = bt._grade([a, b], [bt.SwitchLog(t + 50, None, REDZONE, "start", "")], [(t + 50, REDZONE)],
                    set(), 43.0, 7.0, 5.0, t + 100, t + 900, set())
    off = rep.dead_causes.get("RedZone off the air", 0)
    assert rep.watched_s == 810 and off == rep.dead_air_s
    assert 540 <= off <= 570                                                  # from ~t+343 (screen time)


def test_leaving_a_replay_for_the_same_game_is_logged():
    sw, watching, replayed = [], [], set()
    m = Missed("a", "td1", "TD", "td", 100.0, 75)
    bt._record(type("D", (), dict(action="replay", target="a", kind="replay", reason="", replay=m))(),
               "b", 10.0, sw, watching, replayed)
    bt._record(type("D", (), dict(action="switch", target="a", kind="home", reason="", replay=None))(),
               "a", 40.0, sw, watching, replayed)
    assert watching == [(10.0, "replay:a"), (40.0, "a")] and not replayed  # command is not completion


# ----- the catch-up reel -------------------------------------------------------------------

REEL_T = ("reel:1", 45.0)


def test_a_long_break_with_nothing_live_plays_the_catch_up_reel(dec, clock):
    from redzone.decider.decider import REEL
    dec.cfg.update({"reel": {"play": True}})
    on(dec, clock, "a", b=10)
    break_on_screen(dec, clock)
    d = dec.decide(cands(a=sc("a", 60, is_break=True), b=10), reel=REEL_T)
    assert d.action == "switch" and d.target == REEL and d.kind == "break_visit"


def test_reel_is_never_a_reason_to_leave_live_play(dec, clock):
    from redzone.decider.decider import REEL
    dec.cfg.update({"reel": {"play": True}})
    on(dec, clock, "a", b=10)
    assert dec.decide(cands(a=5, b=10), reel=REEL_T).target != REEL


def test_reel_off_in_settings_or_too_long_for_the_break(dec, clock):
    from redzone.decider.decider import REEL
    on(dec, clock, "a", b=10)
    break_on_screen(dec, clock)
    assert dec.decide(cands(a=sc("a", 60, is_break=True), b=10), reel=REEL_T).target != REEL   # play: false
    dec.cfg.update({"reel": {"play": True}})
    assert dec.decide(cands(a=sc("a", 60, is_break=True), b=10), reel=("reel:1", 900.0)).target != REEL


def test_live_game_worth_a_visit_beats_the_reel(dec, clock):
    from redzone.decider.decider import REEL
    dec.cfg.update({"reel": {"play": True}})
    on(dec, clock, "a", b=10)
    break_on_screen(dec, clock)
    d = dec.decide(cands(a=sc("a", 60, is_break=True), b=70), reel=REEL_T)
    assert d.target == "b"
