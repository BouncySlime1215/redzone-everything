"""[highlight jump] When to leave the game on screen for the catch-up reel, and when to stay."""
from redzone.decider.decider import REEL, Missed
from redzone.decider.screen import Event
from redzone.lab import reelsim
from redzone.lab.backtest import SwitchLog
from redzone.models import League
from redzone.reel import JumpPolicy, JumpWindow, ReelOffer, StayView, fit_reel, jump_value, reel_rate
from tests.test_redzone import break_on_screen, cands, dec, on, sc  # noqa: F401  (fixtures)

SIX = ReelOffer("reel:1", ((9.0, 70.0), (9.0, 65.0), (8.0, 60.0), (8.0, 60.0), (8.0, 55.0), (8.0, 50.0)),
                5, 30.0, (("x", 100.0), ("x", 200.0), ("y", 300.0), ("y", 400.0), ("z", 500.0), ("z", 600.0)))
CALM = StayView(live_value=60.0)
ROOMY = JumpWindow(dead_s=120.0, out_s=5.0, back_s=5.0, margin_s=8.0)


# ----- the pure function ------------------------------------------------------------------------

def test_a_reel_that_fits_the_dead_ball_is_played_whole():
    j = jump_value(CALM, SIX, ROOMY)
    assert j.ok and j.n_clips == 6 and j.reel_s == 50.0 and j.cost_s == 10.0
    assert j.ev_reel > j.ev_stay


def test_the_reel_is_cut_to_fit_and_never_below_five_clips_or_thirty_seconds():
    tight = JumpWindow(dead_s=60.0, out_s=5.0, back_s=5.0, margin_s=8.0)     # 42 s of room
    j = jump_value(CALM, SIX, tight)
    assert j.ok and j.n_clips == 5 and j.reel_s == 42.0
    assert not jump_value(CALM, SIX, JumpWindow(45.0, 5.0, 5.0, 8.0)).ok     # 27 s: under 30 s
    assert fit_reel(SIX, 41.9) == (0, 0.0)                                    # 4 clips: not a reel


def test_hard_stays_come_first_whatever_the_reel_is_worth():
    for world in (StayView(60.0, crunch=True), StayView(60.0, mine_late=True), StayView(60.0, must_see=True)):
        j = jump_value(world, SIX, ROOMY)
        assert not j.ok and "staying" in j.reason


def test_a_big_moment_likely_soon_means_stay_measured_against_the_leagues_usual_rate():
    nfl_usual = StayView(60.0, key_soon_p=0.55, key_rate=0.52)     # NFL breaks run ~0.5: normal
    assert jump_value(nfl_usual, SIX, ROOMY).ok
    hot = StayView(60.0, key_soon_p=0.85, key_rate=0.52)
    assert not jump_value(hot, SIX, ROOMY).ok
    nba_hot = StayView(60.0, key_soon_p=0.30, key_rate=0.15)       # twice the NBA's usual rate
    assert not jump_value(nba_hot, SIX, ROOMY).ok


def test_a_better_live_visit_or_a_thin_reel_loses():
    assert not jump_value(StayView(60.0, best_visit=90.0), SIX, ROOMY).ok
    dull = ReelOffer("reel:1", tuple((8.0, 3.0) for _ in range(6)), 5, 30.0)
    assert not jump_value(CALM, dull, ROOMY).ok              # under staying's risk + min_gain
    assert not jump_value(CALM, None, ROOMY).ok


def test_no_timing_means_no_jump():
    assert not jump_value(CALM, SIX, JumpWindow(float("inf"), 5.0, float("inf"))).ok
    assert not jump_value(CALM, SIX, JumpWindow(0.0, 5.0, 5.0)).ok


def test_the_reel_is_worth_more_per_second_than_one_replay():
    j = jump_value(CALM, SIX, ROOMY)
    per_s = reel_rate(j, SIX)
    one_replay = 75.0          # a missed TD
    assert per_s * (5 + 6 + 15) > one_replay   # tune + seek + clip: the reel wins the break
    assert reel_rate(jump_value(StayView(60.0, crunch=True), SIX, ROOMY), SIX) == 0.0


def test_policy_knobs_are_respected():
    strict = JumpPolicy(key_ratio=1.01)
    assert not jump_value(StayView(60.0, key_soon_p=0.53, key_rate=0.52), SIX, ROOMY, strict).ok


# ----- in the decider ---------------------------------------------------------------------------

def reel_on(dec):
    dec.cfg.data["reel"]["play"] = True


def test_jumps_to_the_reel_at_a_dead_ball_and_comes_back_for_the_restart(dec, clock):
    reel_on(dec)
    on(dec, clock, "a", b=10)
    break_on_screen(dec, clock)                       # a quarter break: 120 s floor
    d = dec.decide(cands(a=sc("a", 60, is_break=True), b=10), reel=SIX)
    assert d.action == "switch" and d.target == REEL
    assert dec.reel_plan is not None and dec.reel_plan.n_clips >= 5
    assert dec.home_break_visit == "a" and dec.home_return_at == dec.reel_end_at
    clock.t = dec.reel_end_at + 0.1
    d = dec.decide(cands(a=sc("a", 60, is_break=True), b=10))
    assert d.action == "switch" and d.target == "a" and d.kind == "home"


def test_restart_in_the_data_cuts_the_reel_short_to_be_home_first(dec, clock):
    reel_on(dec)
    on(dec, clock, "a", b=10)
    break_on_screen(dec, clock)
    dec.decide(cands(a=sc("a", 60, is_break=True), b=10), reel=SIX)
    clock.t += 10
    restart = clock.t
    dec.timelines["a"].events.append(Event(restart, "stop_end", "end_period"))   # the restart, in the data
    airs = restart + dec.delays.get("youtubetv:a").mu                             # ...on screen ~40 s later
    end = dec.reel_end_at
    while True:
        clock.t += 1
        d = dec.decide(cands(a=sc("a", 60), b=10))
        if d.action == "switch":
            break
    # home before the restart airs (with the tune), cutting the reel short: never after it
    assert d.target == "a" and clock.t + 5 < airs and clock.t < end


def test_never_jumps_in_crunch_time_or_from_live_play(dec, clock):
    reel_on(dec)
    on(dec, clock, "a", b=10)
    assert dec.decide(cands(a=5, b=10), reel=SIX).target != REEL          # live play: no
    break_on_screen(dec, clock)
    hot = sc("a", 60, is_break=True)
    hot.crunch = True
    assert dec.decide(cands(a=hot, b=10), reel=SIX).target != REEL


def test_a_reel_that_wont_fit_the_break_is_not_played(dec, clock):
    reel_on(dec)
    on(dec, clock, "a", b=10)
    break_on_screen(dec, clock, label="timeout")      # no floor, no fitted length: no window
    assert dec.decide(cands(a=sc("a", 60, is_break=True), b=10), reel=SIX).target != REEL


def test_fitted_break_length_opens_the_window(dec, clock):
    reel_on(dec)
    dec.cfg.data["reel"]["jump"]["p10_s"] = {"nfl": {"timeout": 150.0}}
    on(dec, clock, "a", b=10)
    break_on_screen(dec, clock, label="timeout")
    assert dec._p10_left("a", clock.t) > 60
    d = dec.decide(cands(a=sc("a", 60, is_break=True), b=10), reel=SIX)
    assert d.target == REEL


def test_reel_plays_are_not_replayed_again(dec, clock):
    reel_on(dec)
    on(dec, clock, "a", b=10)
    dec.queue.append(Missed("b", "p1", "TD", "td", clock.t - 200, 75.0))
    dec.cfg.data["replay"]["enabled"] = False      # (keep _scan_missed from re-judging the fake queue)
    offer = ReelOffer(SIX.target, SIX.clips, 5, 30.0, (("b", clock.t - 190),) + SIX.plays[1:])
    break_on_screen(dec, clock)
    d = dec.decide(cands(a=sc("a", 60, is_break=True), b=10), reel=offer)
    assert d.target == REEL
    assert not dec.queue and dec.done_replays[-1].pid == "p1"


def test_trace_sees_every_answer_and_never_breaks_a_decision(dec, clock):
    reel_on(dec)
    seen = []
    dec.reel_trace = lambda *a: seen.append(a) or 1 / 0
    on(dec, clock, "a", b=10)
    break_on_screen(dec, clock)
    assert dec.decide(cands(a=sc("a", 60, is_break=True), b=10), reel=SIX).target == REEL
    assert seen and seen[-1][1] == "a" and seen[-1][4].ok


def test_off_means_off(dec, clock):
    on(dec, clock, "a", b=10)
    break_on_screen(dec, clock)
    assert dec.decide(cands(a=sc("a", 60, is_break=True), b=10), reel=SIX).target != REEL   # play: false
    reel_on(dec)
    dec.cfg.data["reel"]["jump"]["enabled"] = False
    assert dec.decide(cands(a=sc("a", 60, is_break=True), b=10), reel=SIX).target != REEL


# ----- the scorecard ------------------------------------------------------------------------------

class _R:
    def __init__(self, gid, league, wall, plays):
        self.gid, self.league, self.wall, self.plays, self.matchup = gid, league, wall, plays, "A @ B"


def test_reel_card_counts_dead_time_and_big_plays_missed_while_on_it():
    plays = [{"id": "1", "type": {"text": "Pass"}, "text": "pass"},
             {"id": "2", "type": {"text": "Timeout"}, "text": "timeout"},
             {"id": "3", "type": {"text": "Pass"}, "text": "pass", "scoringPlay": True}]
    r = _R("g", League.NFL, [0.0, 10.0, 200.0], plays)
    lag = 40.0
    sw = [SwitchLog(55.0, "g", "reel", "reel", ""), SwitchLog(120.0, "reel", "g", "home", "")]
    card = reelsim.grade([r], sw, lag, 5.0, 1000.0)
    s = card.summary()
    assert s["reels"] == 1 and s["reel_s"] == 60 and s["reels_in_dead_pct"] == 100.0
    assert s["reel_home_big_missed"] == 0
    late = [SwitchLog(55.0, "g", "reel", "reel", ""), SwitchLog(260.0, "reel", "g", "home", "")]
    s = reelsim.grade([r], late, lag, 5.0, 1000.0).summary()
    assert s["reel_home_big_missed"] == 1 and s["reel_home_late_s"] > 0
