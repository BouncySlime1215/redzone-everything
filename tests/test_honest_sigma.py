"""[auto-anchors] Honest sync doubt in the sims: ad-break anchors, per-tune landing error, honesty grade."""
import random
from types import SimpleNamespace as NS

from redzone.lab import backtest as B
from redzone.models import League


class Cfg(dict):
    def get(self, k, default=None):
        return super().get(k, default)


def play(t, per=1, clk="10:00"):
    return {"type": {"text": t}, "period": {"number": per}, "clock": {"displayValue": clk}}


def test_ad_breaks_follow_official_stoppages_and_nhl_tv_timeouts():
    nfl = B.Replay(League.NFL, "f", {}, [play("Rush"), play("Official Timeout"), play("Pass")], [100.0, 200.0, 300.0])
    nhl = B.Replay(League.NHL, "h", {}, [play("Stoppage", 1, "15:00"), play("Stoppage", 1, "13:30"),
                                         play("Stoppage", 1, "13:00"), play("Stoppage", 1, "9:00")],
                   [10.0, 20.0, 30.0, 40.0])
    cfg = Cfg({"sync.break_anchor.true_gap_s": 15.0, "sync.break_anchor.true_gap_sd_s": 0.0})
    out = B.ad_breaks([nfl, nhl], 50.0, cfg, random.Random(0))
    assert out["f"] == [(200.0, 265.0)]                          # whistle + lag + the gap to the ad
    assert [w for w, _ in out["h"]] == [20.0, 40.0]              # first stoppage past 14:00 and past 10:00


def test_honesty_summary_counts_misses_and_small_doubt():
    h = B.honesty_summary([(1.0, 3.0), (7.0, 3.0), (0.5, 9.0), (2.0, 2.0)])
    assert h["n"] == 4 and h["within_2sigma"] == 0.75 and h["sigma_le_8"] == 0.75 and h["sigma_le_3"] == 0.75
    assert B.honesty_summary([]) == {"n": 0}


def test_anchor_tick_feeds_one_break_matched_like_live():
    got = []
    sync = None
    d = NS(behind=0.0)
    delays = NS(get=lambda t: d, tuned=lambda t: got.append(("tuned", t)),
                anchor=lambda target, z, kind, t, sd: got.append((target, round(z, 1), kind, t, round(sd, 2))))
    sw = [B.SwitchLog(1000.0, None, "g", "chase", "", 2.0, 2.0)]
    cfg = Cfg({"sync.break_anchor.start_bias_s": 15.0, "sync.break_anchor.bias_sd_s": 10.0})
    dec = NS(action="stay", target="g")
    B._anchor_tick(delays, sync, dec, sw, {"g": "yt:g"}, {"g": [(990.0, 1060.0)]}, cfg, 5.0, 1070.0, 10.0)
    # the ad comes up 2 s late (this tune's unknown landing): z = 1062 - 990 - 15
    assert got == [("yt:g", 57.0, "break", 1062.0, 10.05)]
    got.clear()
    B._anchor_tick(delays, sync, dec, sw, {"g": "yt:g"}, {"g": [(990.0, 1060.0)]}, cfg, 5.0, 1080.0, 10.0)
    assert got == []                                             # one anchor per break


def test_noise_cal_learns_the_real_error_and_never_widens():
    from redzone.decider.screen import Delay, NoiseCal
    rng = random.Random(1)
    cal = NoiseCal(min_n=20, min_sigma_s=2.0)
    assert cal.sigma("yt") is None
    for _ in range(400):                          # true error U(0, 6), anchor noise sd 10
        cal.add("yt", rng.uniform(0, 6) + rng.gauss(0, 10), 10.0)
    s = cal.sigma("yt")
    assert 3.0 <= s <= 9.0                         # an upper bound on ~3.5 s rms, honest side
    d = Delay(100.0, 3.0, calibrated_at=0.0)
    d.cal_sigma = s
    assert d.current_sigma(5400.0) == min(9.0, s)  # aging model 3 + 6 = 9, the learned doubt below it
    d.cal_sigma = 50.0
    assert d.current_sigma(5400.0) == 9.0          # never wider than the model


def test_first_anchor_after_a_tune_feeds_the_calibration():
    from redzone.decider.screen import DelayBook, NoiseCal
    book = DelayBook({"youtubetv": 40.0}, 9.0, clock=lambda: 0.0)
    cal = book.attach_cal(NoiseCal(min_n=1, min_sigma_s=2.0))
    book.anchor("youtubetv:g", 45.0, "break", 0.0, 1.0)     # not tuned yet: no residual
    assert not cal.pool
    book.tuned("youtubetv:g")
    book.anchor("youtubetv:g", 45.0, "break", 0.0, 1.0)
    book.anchor("youtubetv:g", 47.0, "break", 1.0, 1.0)     # second one after the same tune: ignored
    assert [round(r) for r, _ in cal.pool["youtubetv"]] == [5]
    assert book.get("youtubetv:g").cal_sigma is not None


def _tracker():
    from redzone.decider.cuts import DeadTracker
    dt = DeadTracker(League.NFL)
    dt.add("1", 1000, 1000, "rush", "run for 3", "Rush", None, 1)
    dt.add("2", 1040, 1040, "pass", "pass for 8", "Pass Reception", None, 1)
    dt.seen(1100, 10)
    return dt                                   # dead 1003-1030, then from 1043


def test_landing_lag_widens_only_a_thin_margin():
    from redzone.decider.cuts import plan_entry
    dt = _tracker()
    kw = dict(can_rewind=False, relaxed=8.0, urgent=2.0)
    # the picture would come up at 1006: an urgent (2 s) landing is fine on time, not 6 s late
    a = plan_entry(dt, 100.0, 20.0, 1101.0, 5.0, **kw)
    b = plan_entry(dt, 100.0, 20.0, 1101.0, 5.0, lag=6.0, **kw)
    assert a.ok and "(urgent)" in a.why and not b.ok
    # a full margin already covers the lag: same answer either way
    for x in range(990, 1050, 2):
        a = plan_entry(dt, 100.0, 20.0, x + 95.0, 5.0, can_rewind=False)
        b = plan_entry(dt, 100.0, 20.0, x + 95.0, 5.0, can_rewind=False, lag=6.0)
        assert (a.ok, a.why) == (b.ok, b.why)


def test_candidate_takes_the_landing_lag_and_strict_ended_entries():
    from redzone.noggin import profile
    args = profile.argv("candidate", "nhl")
    assert "decider.entry.landing_lag_s=6" in args and "decider.entry.ended_strict=true" in args


def test_possession_start_cues_to_the_last_dead_ball_instead_of_waiting():
    from redzone.decider.cuts import plan_entry
    dt = _tracker()
    # the picture would come up at 1036, mid-play; the full 20 s doubt fits no rewind in 1003-1030
    kw = dict(can_rewind=True, step=10.0, max_back=120.0)
    a = plan_entry(dt, 100.0, 20.0, 1131.0, 5.0, **kw)
    b = plan_entry(dt, 100.0, 20.0, 1131.0, 5.0, possession=True, **kw)
    assert not a.ok
    assert b.ok and b.rewind_s > 0 and "(possession start)" in b.why
    assert 1003 <= 1036 - b.rewind_s <= 1030           # lands on the dead ball before the play
    # without a DVR there is nothing to cue: still waits (rule #1)
    c = plan_entry(dt, 100.0, 20.0, 1131.0, 5.0, can_rewind=False, possession=True)
    assert not c.ok


def test_possession_start_is_off_by_default_and_reads_its_scope():
    from types import SimpleNamespace as NS
    from redzone.decider.decider import Decider
    dec = Decider.__new__(Decider)
    x = NS(scored=NS(crunch_now=False))
    d = NS(kind="score")
    for mode, want in (("off", False), (False, False), ("crunch", False), ("all", True)):
        dec.cfg = {"decider.entry.possession_start": mode}
        assert dec._possession_start(x, d) is want
    dec.cfg = {"decider.entry.possession_start": "crunch"}
    assert dec._possession_start(NS(scored=NS(crunch_now=True)), d) is True


def test_candidate_uses_possession_start_for_crunch():
    from redzone.noggin import profile
    assert "decider.entry.possession_start=crunch" in profile.argv("candidate", "nba")


def test_rule1_no_rewind_into_a_data_gap():
    """[data gap] NBA GS @ DEN (Nov 4): a substitution with no stoppage logged before it, then 4 minutes
    with no data. With decider.entry.confirmed_dead_only the gap is no dead window, so nothing lands in it."""
    from redzone.decider.cuts import DeadTracker, plan_entry
    dt = DeadTracker(League.NBA)
    dt.add("1", 1000, 1000, "jump_shot", "makes 2", "Jump Shot", 377.0, 2)
    dt.add("2", 1000, 1000, "substitution", "enters the game", "Substitution", 377.0, 2)
    dt.add("3", 1238, 1238, "jump_shot", "makes 3", "Jump Shot", 353.0, 2)
    dt.seen(1300, 10)
    kw = dict(can_rewind=True, step=10.0, max_back=120.0, possession=True)
    # the picture would come up at 1250: a rewind would have landed in the gap (1000 .. 1212)
    assert plan_entry(dt, 100.0, 4.0, 1345.0, 5.0, **kw).ok
    dt.confirmed_only, dt._windows = True, None
    assert all(w.kind != "substitution" for w in dt.windows())
    assert not plan_entry(dt, 100.0, 4.0, 1345.0, 5.0, **kw).ok


def test_candidate_takes_confirmed_dead_only():
    from redzone.noggin import profile
    assert "decider.entry.confirmed_dead_only=true" in profile.argv("candidate", "nba")


def test_candidate_takes_the_position_floor():
    from redzone.noggin import profile
    args = profile.argv("candidate", "nfl")
    assert "delay.sync.min_sigma_s=3" in args and "sync.break_anchor.enabled=true" in args


def test_position_reads_pin_the_delay_after_a_tune():
    """[yttv position] once an anchor has fixed the origin, a position read after a new tune gives the
    delay at once (sync.py: d = wall - origin - position)."""
    from redzone.decider.sync import SyncBook
    sb = SyncBook(lambda t: (40.0, 8.0), clock=lambda: 0.0)
    for k in range(20):                               # anchors with 5 s doubt, positions every 5 s
        t = 1000.0 + 5 * k
        sb.position("youtubetv:espn", t - 1.7e9 - 45.0, t=t)
        if k % 5 == 0:
            sb.measure("youtubetv:espn", 45.0 + (2 if k % 10 else -2), "flip", t=t, sd=3.0)
    sb.tuned("youtubetv:espn", 1200.0)
    sb.position("youtubetv:espn", 1210.0 - 1.7e9 - 51.0, t=1210.0)   # the new tune landed 6 s further back
    mu, sd = sb.get("youtubetv:espn").mu(1210.0), sb.get("youtubetv:espn").sd(1210.0)
    assert abs(mu - 51.0) < 2.5 and sd < 3.0


def test_replay_window_skips_stale_single_replays_in_its_leagues():
    """[replay window] Nick, Nov 4: a missed moment airs within 3 min or not singly (montage only)."""
    from types import SimpleNamespace as NS
    from redzone.decider.decider import Decider, Missed
    dec = Decider.__new__(Decider)
    dec._known = {"g1": NS(scored=NS(league=NS(base=NS(value="nfl")))),
                  "g2": NS(scored=NS(league=NS(base=NS(value="nhl"))))}
    m1 = Missed("g1", "p", "TD", "td", 1000.0, 90.0)
    m2 = Missed("g2", "p", "goal", "goal", 1000.0, 90.0)
    dec.cfg = {"replay.window_s": 180, "replay.window_leagues": ["nfl"]}
    assert dec._window(m1) == 180 and dec._window(m2) == 0
    dec.cfg = {"replay.window_s": 0}
    assert dec._window(m1) == 0
    dec.cfg = {"replay.window_s": 180}
    assert dec._window(m2) == 180 and dec._due(m2) == 180


def test_lookahead_scopes_to_its_leagues():
    """[GAME-04] noggin.lookahead.leagues: the candidate chases only NFL plays (NHL locked crunch -0.9)."""
    from types import SimpleNamespace as NS
    from redzone.decider.decider import Decider
    from redzone.noggin import profile
    dec = Decider.__new__(Decider)
    nfl, nhl = (NS(scored=NS(league=NS(base=NS(value=v)))) for v in ("nfl", "nhl"))
    dec.cfg = {"noggin.lookahead.enabled": True, "noggin.lookahead.leagues": ["nfl"]}
    assert dec._lookahead_on() and dec._lookahead_on(nfl) and not dec._lookahead_on(nhl)
    dec.cfg = {"noggin.lookahead.enabled": True}
    assert dec._lookahead_on(nhl)
    dec.cfg = {"noggin.lookahead.leagues": ["nfl"]}
    assert not dec._lookahead_on(nfl)
    assert "noggin.lookahead.leagues=[nfl]" in profile.argv("candidate", "nhl")
