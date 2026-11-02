"""Red team: every way the decider could cut away while a play is on screen.

Each test is named for the bug it pins down or the rule that prevents it. The backtest tests
at the bottom replay real slates under real-world mess (wrong delay, slow / silent / out-of-order
feeds, missing wallclocks) and demand zero mid-play cuts.
"""
from pathlib import Path

import pytest

from redzone.decider.decider import GONE_MAX_HOLD_S, Candidate, Decider, Missed
from redzone.decider.screen import (AFTER_PLAY_BUFFER_S, STALE_DATA_S, Delay, DelayBook, Event, Timeline,
                                    leave_gate)
from redzone.models import League, Phase, Play, Stoppage
from tests.conftest import nfl
from tests.test_decider import calibrate, cands, sc, start_on


@pytest.fixture
def dec(cfg, clock):
    return Decider(cfg, DelayBook({"youtubetv": 40.0}, sigma_default=15.0, clock=clock), clock=clock)


def P(pid, kind="rush", w=None, text=""):
    return Play(str(pid), text or kind, kind, False, w)


def feed(tl, clock, plays, stoppage=Stoppage.NONE, phase=Phase.LIVE):
    """One poll of game 'a' as the app would see it."""
    g = nfl("a", stoppage=stoppage, recent_plays=list(plays), last_play=plays[-1] if plays else None)
    g.phase = phase
    tl.observe(g)


# --- 1. The game ends in the data before your screen shows the last play --------------------------

def test_final_in_data_stays_until_the_last_play_has_aired(dec, clock):
    """Bug: the 'ended' path switched with no gate. A final reaches the data ~35 s before your
    screen shows the last play (BUF @ CLE: cut away during the kneel-downs)."""
    calibrate(dec, "youtubetv:a")
    start_on(dec, clock, "a", {"b": 50})
    tl = dec.timeline("a", League.NFL)
    p1 = P(1, w=clock.t - 60)
    feed(tl, clock, [p1])
    clock.t += 10
    w = clock.t - 8
    feed(tl, clock, [p1, P(2, "rush", w, "kneels"), P(3, "end_of_game", w, "END GAME")], phase=Phase.POST)

    d = dec.decide(cands(b=50))                 # "a" is final: gone from the candidates
    assert d.action == "stay" and "last play" in d.reason
    assert tl.end_t == w
    dl = dec.delays.get("youtubetv:a")
    clock.t = w + dl.mu + 2 * dl.current_sigma(clock.t) + AFTER_PLAY_BUFFER_S - 2
    assert dec.decide(cands(b=50)).action == "stay"
    clock.t += 3
    d = dec.decide(cands(b=50))
    assert d.action == "switch" and d.target == "b" and d.kind == "ended"


def test_final_without_the_last_play_assumes_it_just_happened(dec, clock):
    """A scoreboard can flip to Final before the play-by-play has the last play: assume it was
    as late as possible, i.e. just now."""
    calibrate(dec, "youtubetv:a")
    start_on(dec, clock, "a", {"b": 50})
    tl = dec.timeline("a", League.NFL)
    p1 = P(1, w=clock.t - 60)
    feed(tl, clock, [p1])
    clock.t += 10
    feed(tl, clock, [p1], phase=Phase.POST)
    clock.t += 30                                # the game-winner could still be on screen
    assert dec.decide(cands(b=50)).action == "stay"


def test_vanished_game_is_not_treated_as_final(dec, clock):
    """A game that drops out of the feed may still be live: wait for a provable gap, and only
    give up after GONE_MAX_HOLD_S of flying blind."""
    calibrate(dec, "youtubetv:a")
    start_on(dec, clock, "a", {"b": 50})
    tl = dec.timeline("a", League.NFL)
    p1 = P(1, w=clock.t - 60)
    feed(tl, clock, [p1])
    clock.t += 10
    feed(tl, clock, [p1, P(2, "pass_reception", clock.t - 8)])
    clock.t += 30                                # play 2 is on screen now
    d = dec.decide(cands(b=50))                  # ...and "a" vanished from the candidates
    assert d.action == "stay" and d.target == "a"
    clock.t += 30                                # aired, but the data is stale: can't prove a gap
    assert dec.decide(cands(b=50)).action == "stay"
    clock.t += GONE_MAX_HOLD_S
    d = dec.decide(cands(b=50))
    assert d.action == "switch" and d.target == "b"


def test_muting_the_current_game_still_leaves_right_away(dec, clock):
    calibrate(dec, "youtubetv:a")
    start_on(dec, clock, "a", {})
    dec.timeline("a", League.NFL).events.append(Event(clock.t, "play", "run"))
    clock.t += 35                                # mid-play, but you said so
    dec.user.muted.add("a")
    d = dec.decide(cands(a=30, b=60))
    assert d.action == "switch" and d.target == "b"


def test_live_game_scoring_zero_still_waits_for_the_gate(dec, clock):
    """'Not in live' used to mean 'gone' and skipped the gate even for a live game."""
    calibrate(dec, "youtubetv:a")
    start_on(dec, clock, "a", {})
    dec.timeline("a", League.NFL).events.append(Event(clock.t, "play", "run"))
    clock.t += 35
    zero = sc("a", 0)
    zero.raw = zero.moment = 0.0
    d = dec.decide({"a": Candidate(zero, "youtubetv:a"), **cands(b=60)})
    assert d.action == "stay" and "play is on screen" in d.reason


# --- 2. Breaks that look open after play resumed ---------------------------------------------------

D = Delay(40.0, 2.0)  # calibrated_at None -> sigma stays 2


def test_play_after_a_break_ends_it_even_if_stop_end_is_late(clock):
    """Bug: a stop_end recorded too late (or never) kept the break 'on screen' while the next
    play aired. Any play logged after the stoppage proves it resumed."""
    tl = Timeline(League.NFL, clock)
    T = 1000.0
    tl.events.append(Event(T, "stop_start", "timeout"))
    tl.events.append(Event(T + 100, "stop_end"))          # recorded far too late
    tl.events.append(Event(T + 30, "play", "first snap after the timeout"))
    g = leave_gate(tl, D, T + 30 + 40 - 5, 8.0)            # that play is on screen
    assert not g.safe and not g.on_screen_break


def test_stop_end_never_lands_before_its_stop_start(clock):
    """Bug: next snap logged < lead s after the timeout entry gave stop_end < stop_start, which
    reads as a break that never ended."""
    tl = Timeline(League.NFL, clock)
    T = clock.t - 30
    p1 = P(1, w=T - 40)
    feed(tl, clock, [p1])
    clock.t += 10
    to = P(2, "timeout", T)
    feed(tl, clock, [p1, to], Stoppage.TIMEOUT)
    clock.t += 10
    feed(tl, clock, [p1, to, P(3, "rush", T + 5)])
    assert tl.last("stop_end").seen >= tl.last("stop_start").seen
    g = leave_gate(tl, D, T + 5 + 40 - 3, 8.0)             # that snap is on screen
    assert not g.safe


def test_stop_end_without_a_play_takes_the_earliest_possible_resume(clock):
    """Bug: 'now - 6 - lead' can be later than the real resume when the feed is slow."""
    tl = Timeline(League.NFL, clock)
    p1 = P(1, w=clock.t - 100)
    feed(tl, clock, [p1])
    clock.t += 10
    to = P(2, "timeout", clock.t - 60)
    feed(tl, clock, [p1, to], Stoppage.TIMEOUT)
    clock.t += 10
    feed(tl, clock, [p1, to], Stoppage.NONE)               # resumed, no play yet to time it by
    # was now - 6 - 12; the earliest consistent with "still stopped at the last poll, feed ~10 s behind"
    assert tl.last("stop_end").seen <= clock.t - 10 - 10 - 12


def test_stale_open_break_is_not_trusted(clock):
    """Bug (dropped source): data went silent during a timeout; the game resumed unseen and the
    gate still said 'break on screen'."""
    tl = Timeline(League.NFL, clock)
    p1 = P(1, w=clock.t - 60)
    feed(tl, clock, [p1])
    clock.t += 10
    to = P(2, "timeout", clock.t - 8)
    feed(tl, clock, [p1, to], Stoppage.TIMEOUT)
    for _ in range(4):                                      # fresh polls, still in the timeout
        clock.t += 10
        feed(tl, clock, [p1, to], Stoppage.TIMEOUT)
    assert leave_gate(tl, D, clock.t, 8.0).on_screen_break  # fresh: break on screen is provable
    # Source goes silent. Later the screen may be showing the resume; we can't know.
    assert not leave_gate(tl, D, clock.t + STALE_DATA_S + 5, 8.0).safe


def test_long_official_break_is_trusted_even_when_uncertain(clock):
    """Safe degradation: when we can't see far enough ahead, only breaks that always run long
    (end of quarter, halftime, two-minute warning) still count."""
    tl = Timeline(League.NFL, clock)
    T = clock.t
    big = Delay(40.0, 15.0)                                 # uncalibrated: could be 10..70 s
    tl.events.append(Event(T, "stop_start", Stoppage.TIMEOUT.value))
    assert not leave_gate(tl, big, T + 75, 8.0).safe       # a timeout can be 15 s long
    tl.events.append(Event(T + 1, "stop_start", Stoppage.END_PERIOD.value))
    assert leave_gate(tl, big, T + 75, 8.0).on_screen_break


# --- 3. Plays timed wrong or out of order ----------------------------------------------------------

def test_play_without_wallclock_blocks_its_whole_possible_window(clock):
    """Bug: a play with no wallclock was timed at 'now - 6'. If the feed really ran 25 s behind,
    it aired ~20 s earlier than the gate protected."""
    tl = Timeline(League.NFL, clock)
    p1 = P(1, w=clock.t - 60)
    feed(tl, clock, [p1])
    clock.t += 10
    feed(tl, clock, [p1, P(2, "pass_reception", None)])    # no wallclock
    real = clock.t - 25                                     # what actually happened
    on_screen = real + 40 - 3
    assert not leave_gate(tl, D, on_screen, 8.0).safe


def test_out_of_order_play_does_not_hide_a_newer_one(clock):
    """Bug: the gate stopped at the first fully-aired play, but a late play is appended AFTER
    newer ones, so a newer play still on screen went unchecked."""
    tl = Timeline(League.NFL, clock)
    T = 1000.0
    tl.events.append(Event(T + 10, "play", "B (arrived on time)"))
    tl.events.append(Event(T, "play", "A (arrived a poll late)"))
    assert not leave_gate(tl, D, T + 49, 8.0).safe          # A has aired, B is on screen


def test_slow_feed_means_official_breaks_only(clock):
    """When the feed runs so far behind that a play can reach your screen before our data, 'no
    play in the data' proves nothing."""
    tl = Timeline(League.NFL, clock)
    feed(tl, clock, [P(0, w=clock.t - 200)])
    plays = []
    for i in range(1, 6):                                   # each play shows up 35 s late
        clock.t += 10
        plays.append(P(i, w=clock.t - 35))
        feed(tl, clock, plays)
    for _ in range(2):                                      # fresh polls, no new plays
        clock.t += 10
        feed(tl, clock, plays)
    g = leave_gate(tl, Delay(40.0, 3.0), clock.t, 8.0)      # every known play has aired...
    assert not g.safe and "behind" in g.why                 # ...but an unknown one may be on screen
    assert tl.lag_est() >= 24


# --- 4. The end of a catch-up replay ---------------------------------------------------------------

def test_replay_runs_until_the_replayed_play_has_ended(dec, clock, cfg):
    """The rewind is whole 10 s presses (rounded up) and the delay is ±2σ, so a 15 s clip can end
    before the play does."""
    calibrate(dec, "youtubetv:a")
    start_on(dec, clock, "a", {})
    m = Missed("a", "p1", "TD", "td", clock.t - 30, 80)
    cs = cands(a=40)
    dec._start_replay(m, cs, clock.t)
    sig = dec.delays.get("youtubetv:a").current_sigma(clock.t)
    worst_start = 10 + 3 + cfg.get("replay.seek_step_s") + 2 * sig  # before the end of the play
    playback = dec.replay_until - clock.t - dec._cost("youtubetv:a") - cfg.get("replay.seek_s")
    assert playback >= worst_start + AFTER_PLAY_BUFFER_S - 1e-6


# --- 5. Backtests under real-world mess -----------------------------------------------------------

ARCHIVE = Path(__file__).resolve().parent.parent / "data/archive/nfl"
needs_archive = pytest.mark.skipif(not ARCHIVE.exists(), reason="no archived slates")


@pytest.fixture(scope="module")
def slates():
    from redzone.lab.optimize import load_slates
    return {s[0].matchup: s for s in load_slates(ARCHIVE)}


@pytest.fixture(scope="module")
def real_cfg():
    from redzone.config import Config
    return Config(user_path=ARCHIVE.parent.parent / "config.yaml")


def _cuts(slates, real_cfg, name, **kw):
    from redzone.lab.backtest import run
    rep = run(slates[name], real_cfg, set(real_cfg.get("teams.nfl")), **kw)
    return rep


@needs_archive
def test_backtest_regression_buf_cle_kneel_downs(slates, real_cfg):
    rep = _cuts(slates, real_cfg, "BUF @ CLE")
    assert rep.mid_play_cuts == []


@needs_archive
@pytest.mark.parametrize("name,kw", [
    ("BUF @ CLE", dict(true_delay=29.0)),               # screen 2σ ahead of the estimate
    ("SEA @ ATL", dict(true_delay=41.0)),               # ...and 2σ behind
    ("CLE @ CHI", dict(sigma=5.0, true_delay=25.0)),
    ("SEA @ ATL", dict(lag_jitter=(3.0, 25.0), seed=1)),
    ("CAR @ ATL", dict(drop=0.1, seed=2)),
    ("CLE @ CHI", dict(late_plays=0.15, seed=0)),
    ("BUF @ CLE", dict(no_wallclock=0.5)),
    ("NYG @ CHI", dict(true_delay=29.0, lag_jitter=(3.0, 25.0), drop=0.1, late_plays=0.15,
                       no_wallclock=0.5, seed=1)),
    ("CLE @ LV", dict(true_delay=29.0, lag_jitter=(3.0, 25.0), drop=0.1, late_plays=0.15,
                      no_wallclock=0.5, seed=1)),
])
def test_backtest_zero_cuts_under_real_world_mess(slates, real_cfg, name, kw):
    rep = _cuts(slates, real_cfg, name, **kw)
    assert rep.mid_play_cuts == [], rep.mid_play_cuts


@needs_archive
def test_backtest_logs_every_tune_after_a_replay(slates, real_cfg):
    """A return to the same game after its replay (kind home/break_visit) went unlogged: the grader
    thought the replay never ended and skipped the mid-play check on the next cut."""
    rep = _cuts(slates, real_cfg, "SF @ CLE")
    tunes = sum(1 for d in rep.decisions if d.action == "switch" and d.target)
    assert tunes == sum(1 for s in rep.switches if s.kind != "replay")


@needs_archive
def test_backtest_knobs_are_seeded(slates, real_cfg):
    kw = dict(lag_jitter=(3.0, 25.0), drop=0.1, late_plays=0.15, no_wallclock=0.5, seed=3)
    a = _cuts(slates, real_cfg, "ARI @ SEA", **kw).summary
    b = _cuts(slates, real_cfg, "ARI @ SEA", **kw).summary
    assert a == b


def test_baseball_foul_ball_is_a_live_pitch_not_a_dead_ball():
    from redzone.decider.screen import _is_marker
    assert not _is_marker("foul_ball", League.MLB)        # a pitch: you can't cut during it
    assert _is_marker("personal_foul", League.NBA)        # a whistle in basketball
    assert not _is_marker("foul_ball", None) and _is_marker("full_timeout", League.MLB)
