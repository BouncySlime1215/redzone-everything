"""[Nov 8, card S] Timing accuracy on recorded / fake data: honest frame times, the running-clock fit, relative
meters, change points, bug-gone evidence, feed bias, the per-app floor and the live error log. No TV, no adb."""
from __future__ import annotations

import json
import random
from types import SimpleNamespace as NS

import pytest

from redzone.decider.delaymodel import FeedBias, replay_allows_floor
from redzone.decider.screen import DelayBook, Timeline
from redzone.decider.sync import ChannelSync, SyncBook, fit_clock_window, fit_delay, summarize_errors
from redzone.models import GameState, League, Phase, Play, Team
from redzone.screen.scorebug import Bug, ScoreBugWatcher, frame_kind, frame_time, read_frame

T0 = 1_762_500_000.0
TGT = "prime:game1"


# ------------------------------------------------------------------ item 1: honest frame times
def test_frame_time_is_the_midpoint_with_its_own_doubt():
    t, sd = frame_time(100.0, 106.0)
    assert t == 103.0 and sd == pytest.approx((36.0 / 12 + 0.01) ** 0.5)
    assert frame_time(100.0, 100.5)[1] < frame_time(100.0, 107.0)[1]     # the slow stick says so itself
    assert frame_time(100.0, 90.0)[0] == 100.0                           # a clock step can't go negative


def _scored_timeline(wall):
    e = NS(seen=wall, spread=0.0, score=(11, 10), label="made shot")
    return NS(scores=[NS(seen=wall - 500, spread=0.0, score=(10, 10), label=""), e], clocks=[])


def test_midpoint_moves_the_onset_and_uses_the_frames_doubt_only_with_the_flag():
    def run(honest):
        got = []
        w = ScoreBugWatcher(on_sample=got.append, honest_frames=honest)
        tl, tune = _scored_timeline(T0), object()
        for i, score in enumerate([(10, 10), (11, 10), (11, 10)]):
            start = T0 + 30 + 2 * i
            w.observe("s", TGT, tune, start, Bug(*score), tl, t_done=start + 1.5)
        return got[0]
    off, on = run(False), run(True)
    assert off.onset == T0 + 30 + 2 - 1.0 and off.error_s == 1.0     # today: capture start, half the gap
    assert on.onset == pytest.approx(off.onset + 0.75)               # the capture took 1.5 s
    assert on.error_s == pytest.approx(((4 / 12) + (1.5 ** 2 / 12 + 0.01)) ** 0.5, rel=0.4) and on.error_s != 1.0


# ------------------------------------------------------------------ item 2: the running-clock window fit
def _run(start_c, n=12, dt=2.0, jitter=0.25, seed=3):
    r = random.Random(seed)
    return [(T0 + i * dt + r.gauss(0, jitter), float(int(start_c - i * dt))) for i in range(n)]


def test_fit_recovers_a_running_clock_and_a_misread_does_not_move_it():
    pts = _run(600)
    clean = fit_clock_window(pts)
    assert clean is not None and abs(clean.slope + 1.0) <= 0.05 and clean.n == 12
    bad = list(pts)
    bad[5] = (bad[5][0], bad[5][1] + 60.0)                           # OCR read a 4 as a 5
    fit = fit_clock_window(bad)
    assert fit is not None and fit.n == 11
    assert abs(fit.wall_at(590) - clean.wall_at(590)) < 0.5
    assert fit.sd >= 0.4                                              # never claims less than the display allows


@pytest.mark.parametrize("pts", [
    [(T0 + i * 2.0, 600.0) for i in range(12)],                       # stopped
    [(T0 + i * 2.0, 600.0 - 2.0 * i * 0.8) for i in range(12)],       # slope 0.8: not a game clock
    [(T0 + i * 2.0, 600.0 - i * 2.0) for i in range(3)],              # too few frames
])
def test_fit_refuses_what_is_not_a_running_clock(pts):
    assert fit_clock_window(pts) is None


def test_fit_delay_is_timed_against_the_feed_bracket():
    true_delay = 20.0
    # the clock read 600 at real wall T0 and the screen shows it true_delay later
    frames = [(T0 + true_delay + i * 2.0, 600.0 - i * 2.0) for i in range(12)]
    fit = fit_clock_window(frames)
    plays = [(T0 - 5, 605.0), (T0 + 30, 570.0)]                       # the clock ran all 35 s between them
    assert fit_delay(fit, plays) == pytest.approx(true_delay, abs=0.01)
    assert fit_delay(fit, [(T0 - 5, 605.0), (T0 + 60, 570.0)]) is None  # the wall gap isn't the clock gap: it stopped


def test_watcher_emits_one_fitted_sample_with_its_sd_and_falls_back_off():
    got, singles = [], []
    w = ScoreBugWatcher(clock_fit=True, on_clock_fit=lambda t, wall, d, sd: got.append((d, sd)),
                        on_clock=lambda t, wall, d: singles.append(d))
    tl = NS(scores=[], clocks=[(T0 - 5, 2, 605.0), (T0 + 60, 2, 540.0)])
    tune = object()
    for i in range(14):
        c = 600 - 2 * i
        w.observe("s", TGT, tune, T0 + 18 + 2.0 * i, Bug(1, 1, "2ND", f"{c // 60}:{c % 60:02d}"), tl)
    assert got and got[0][0] == pytest.approx(18.0, abs=0.5) and 0.4 <= got[0][1] <= 0.7
    assert w.status()["fits"] >= 1


def test_clock_fit_off_is_todays_behaviour():
    got = []
    w = ScoreBugWatcher(on_clock_fit=lambda *a: got.append(a))
    tl = NS(scores=[], clocks=[(T0 - 5, 2, 605.0), (T0 + 60, 2, 540.0)])
    tune = object()
    for i in range(14):
        c = 600 - 2 * i
        w.observe("s", TGT, tune, T0 + 18 + 2.0 * i, Bug(1, 1, "2ND", f"{c // 60}:{c % 60:02d}"), tl)
    assert got == []


# ------------------------------------------------------------------ items 3-4: meters and change points
def _synced(robust=True, mu=30.0):
    f = ChannelSync("youtubetv:espn", T0, robust=robust)
    f.P[2][2] = 1e8
    f.reset(T0, mu, 6.0)
    return f


def _meter(f, t, e):
    return f.position(t, t - e, 0.3)


def test_meter_holds_the_delay_and_anchors_only_set_the_constant():
    f = _synced()
    e, t = 46_800.0, T0
    for i in range(30):                                     # E flat at e, no anchor yet: delay unknown still
        t = T0 + 2.0 * i
        assert _meter(f, t, e)
    assert f.sd(t) > 4.0
    for i in range(9):                                      # anchors at sd 1.5 around 35 s
        t += 20
        _meter(f, t, e)
        f.delay(t, 35.0 + (-1) ** i * 0.5, "flip", 1.5)
    assert f.mu(t) == pytest.approx(35.0, abs=0.7) and f.sd(t) < 1.0
    before = f.mu(t)
    for i in range(300):                                    # 10 minutes of meter: the delay stays put
        t += 2.0
        _meter(f, t, e)
    assert abs(f.mu(t) - before) < 0.2 and f.sd(t) < 1.1


def test_regression_a_single_bad_ocr_read_does_not_flag_the_channel():
    """Repro: the 3-sigma gate flags a channel uncertain for 300 s on one outlier (sync.py GATE, UNCERTAIN_HOLD_S)."""
    for robust, flagged in [(False, True), (True, False)]:
        f = _synced(robust=robust)
        for i in range(6):
            f.delay(T0 + 10.0 * i, 30.0, "clock", 1.0)
        t = T0 + 70.0
        assert f.delay(t, 90.0, "clock", 1.0) is False
        assert f.uncertain(t) is flagged
        assert f.mu(t) == pytest.approx(30.0, abs=0.5)


def test_two_outliers_restart_only_when_they_agree():
    f = _synced()
    for i in range(6):
        f.delay(T0 + 10.0 * i, 30.0, "clock", 1.0)
    f.delay(T0 + 70, 90.0, "clock", 1.0)
    assert f.delay(T0 + 80, 150.0, "clock", 1.0) is False            # two different misreads: still noise
    assert not f.uncertain(T0 + 80) and f.mu(T0 + 80) == pytest.approx(30.0, abs=0.5)
    assert f.delay(T0 + 90, 150.5, "clock", 1.0) is True             # the same answer twice: the stream moved
    assert f.uncertain(T0 + 90) and f.mu(T0 + 90) == pytest.approx(150.5, abs=1.0)


def test_student_t_downweights_a_modest_miss():
    def after(robust):
        f = _synced(robust=robust)
        for i in range(6):
            f.delay(T0 + 10.0 * i, 30.0, "clock", 1.0)
        f.delay(T0 + 70, 32.0, "clock", 1.0)
        return f.mu(T0 + 70)
    assert 30.0 < after(True) < after(False)


def test_change_point_books_a_pause_and_keeps_the_origin():
    f = _synced()
    e, t = 46_800.0, T0
    for i in range(10):
        t = T0 + 2.0 * i
        _meter(f, t, e)
    f.delay(t, 30.0, "flip", 1.0)
    o = f.x[2]
    t += 40.0                                               # paused 30 s, not fed meanwhile
    e += 30.0
    assert _meter(f, t, e) is False and f.changepoints == 0  # one read off its level waits for a second
    assert not f.uncertain(t)
    t += 2.0
    assert _meter(f, t, e) is True and f.changepoints == 1
    assert f.mu(t) == pytest.approx(60.0, abs=1.0) and f.x[2] == pytest.approx(o, abs=1.0)
    assert not f.uncertain(t)


def test_a_lone_meter_glitch_is_ignored():
    f = _synced()
    e, t = 46_800.0, T0
    for i in range(10):
        t = T0 + 2.0 * i
        _meter(f, t, e)
    f.delay(t, 30.0, "flip", 1.0)
    assert _meter(f, t + 2, e + 8.0) is False
    assert _meter(f, t + 4, e) is True and f.changepoints == 0
    assert f.mu(t + 4) == pytest.approx(30.0, abs=0.5)


def test_a_new_clock_forgets_the_origin_rather_than_book_a_huge_delay():
    f = _synced()
    e, t = 46_800.0, T0
    for i in range(10):
        t = T0 + 2.0 * i
        _meter(f, t, e)
    f.delay(t, 30.0, "flip", 1.0)
    _meter(f, t + 2, e - 86_400.0)
    _meter(f, t + 4, e - 86_400.0)
    assert f.mu(t + 4) == pytest.approx(30.0, abs=1.0)


def test_robust_off_keeps_todays_gate():
    f = ChannelSync("x:y", T0)
    assert f.robust is False and f._q(T0) == pytest.approx(0.02 ** 2)


# ------------------------------------------------------------------ item 3: the probe feeds the meter
def _probe(meter=True, every=2.0):
    from redzone import sync_edge as se
    from redzone.sync_probe import SyncProbe
    cfg = NS(get=lambda k, d=None: {"sync.edge.meter": meter, "sync.edge.meter_every_s": every,
                                    "sync.edge.settle_s": 3.0, "sync.edge.enabled": True,
                                    "mode.dry_run": False}.get(k, d))
    p = SyncProbe(cfg, NS(), root=None)
    got = []
    p.on_position = lambda *a: got.append(a)
    return p, got, se


def _row(state="playing", played=10.0):
    return {"pos_now_s": 1234.5, "ms": {"state": state}, "played_s": played, "rtt_ms": 120}


def test_probe_meter_feeds_only_while_playing_settled_and_flag_on():
    p, got, _ = _probe()
    p._meter("youtubetv:espn", _row(state="paused"), T0)
    p._meter("youtubetv:espn", _row(played=1.0), T0)
    assert got == []
    p._meter("youtubetv:espn", _row(), T0)
    p._meter("youtubetv:espn", _row(), T0 + 1.0)            # within meter_every_s
    p._meter("youtubetv:espn", _row(), T0 + 2.5)
    assert len(got) == 2 and got[0][0] == "youtubetv:espn" and got[0][2] == 1234.5 and 0.3 < got[0][3] < 0.32
    q, got2, _ = _probe(meter=False)
    q._meter("youtubetv:espn", _row(), T0)
    assert got2 == []


# ------------------------------------------------------------------ item 5: bug gone, black vs DRM blank
def test_black_splits_by_app():
    png = b"\x89PNG\r\n\x1a\n" + b"\0" * 4 + b"IHDR" + (1920).to_bytes(4, "big") + (1080).to_bytes(4, "big") + b"\0" * 50
    assert frame_kind(png, "prime") == "black"
    assert frame_kind(png, "youtubetv") == "drm_blank"
    assert read_frame(png, Team("A", "A"), Team("B", "B"), None, "youtubetv") == (None, "drm_blank")


def test_bug_gone_counts_real_absence_only():
    w = ScoreBugWatcher()
    tune = object()
    for i, kind in enumerate(["no_bug", "black", "no_bug"]):
        w.observe("s", TGT, tune, T0 + 4.0 * i, None, None, kind=kind)
    ev = w.evidence(TGT, T0 + 8.0)
    assert ev["break"] is True and ev["absent_frames"] == 3 and ev["black_frames"] == 1 and ev["bug_absent_s"] == 8.0
    w.observe("s", TGT, tune, T0 + 12, Bug(1, 1), None, kind="picture")
    assert w.evidence(TGT, T0 + 12)["bug_absent_s"] == 0.0 and w.evidence(TGT, T0 + 12)["break"] is False
    # blindness is never absence
    v = ScoreBugWatcher()
    for i in range(5):
        v.observe("s", TGT, tune, T0 + 4.0 * i, None, None, kind="drm_blank")
        v.observe("s", TGT, tune, T0 + 4.0 * i, None, None, kind="ocr_failed")
        v.observe("s", TGT, tune, T0 + 4.0 * i, None, None)            # today's caller: says nothing
    assert v.evidence(TGT, T0 + 20)["break"] is False


def test_clock_jumping_back_is_replay_evidence_and_a_stop_is_end_of_play():
    stops = []
    w = ScoreBugWatcher(on_stop=lambda t, wall, d, sd: stops.append((wall, d, sd)))
    tl = NS(scores=[], clocks=[])
    tune = object()
    for i, c in enumerate(["5:00", "4:58", "4:56", "4:56", "4:56"]):
        w.observe("s", TGT, tune, T0 + 30 + 2.0 * i, Bug(1, 1, "2ND", c), tl)
    assert w.end_of_play(TGT)["wall"] == T0 + 34 and w.end_of_play(TGT)["clock"] == 296.0
    tl.clocks.append((T0 + 15, 2, 296.0))                             # the feed's whistle at that clock arrives
    w.observe("s", TGT, tune, T0 + 42, Bug(1, 1, "2ND", "4:56"), tl)
    assert stops and stops[0][1] == pytest.approx(19.0)
    w.observe("s", TGT, tune, T0 + 44, Bug(1, 1, "2ND", "6:30"), tl)
    assert w.evidence(TGT, T0 + 45)["replay"] is True


# ------------------------------------------------------------------ item 6: feed bias
def _game(plays):
    g = GameState(league=League.NBA, game_id="g1", home=Team("A", "A"), away=Team("B", "B"), phase=Phase.LIVE)
    g.recent_plays = plays
    g.updated_at = T0
    return g


def test_feed_bias_learns_espn_vs_timeactual_from_scoring_plays():
    b = FeedBias(min_n=3)
    for i in range(5):
        b.add("g", "nba", (i, 0), "nba_cdn", T0 + 10.0 * i)
        b.add("g", "nba", (i, 0), "espn", T0 + 10.0 * i + 4.0 + (i % 2) * 0.2)
    mu, sd, n = b.bias("nba", "espn")
    assert n == 5 and mu == pytest.approx(4.0, abs=0.25) and sd < 0.5
    assert b.bias("nba", "nba_cdn") is None
    assert b.adjust("nba", "espn", T0) == T0                          # shadow by default
    b.apply = True
    assert b.adjust("nba", "espn", T0) == pytest.approx(T0 - mu)


def test_timeline_only_touches_clocks_when_bias_is_attached():
    plays = [Play("1", "x", "two", True, T0 - 100, T0, "espn", score=(2, 0), clock=500.0, period=1),
             Play("2", "y", "two", True, T0 - 50, T0, "espn", score=(4, 0), clock=470.0, period=1)]
    tl = Timeline(League.NBA, clock=lambda: T0)
    tl.observe(_game([]))
    tl.observe(_game(plays))
    assert list(tl.clocks) == [(T0 - 100, 1, 500.0), (T0 - 50, 1, 470.0)]
    Timeline.feed_bias = FeedBias(min_n=1, apply=True)
    try:
        Timeline.feed_bias._diff("nba", "espn", 5.0)
        tb = Timeline(League.NBA, clock=lambda: T0)
        tb.observe(_game([]))
        tb.observe(_game(plays))
        assert list(tb.clocks)[0][0] == T0 - 105
    finally:
        Timeline.feed_bias = None


# ------------------------------------------------------------------ the floor mechanism
REPLAY = {"method": "forward_chaining", "folds": [{"base_mid_play": 1, "new_mid_play": 1},
                                                  {"base_mid_play": 0, "new_mid_play": 0},
                                                  {"base_mid_play": 2, "new_mid_play": 1}]}


def test_floor_stays_four_without_a_clearing_replay():
    assert replay_allows_floor(REPLAY)
    assert not replay_allows_floor(None)
    assert not replay_allows_floor({**REPLAY, "method": "train_test"})
    assert not replay_allows_floor({"method": "forward_chaining", "folds": REPLAY["folds"][:2]})
    risen = {"method": "forward_chaining", "folds": REPLAY["folds"] + [{"base_mid_play": 0, "new_mid_play": 1}]}
    assert not replay_allows_floor(risen)
    b = DelayBook({}, 8.0)
    b.attach_sync(min_sigma_s=4.0)
    assert b.floor_for("youtubetv:espn") == 4.0
    assert b.set_app_floor("youtubetv", 1.5, None) is False and b.floor_for("youtubetv:espn") == 4.0


def test_cleared_floor_applies_to_that_app_only_and_respects_noisecal():
    b = DelayBook({}, 8.0)
    b.attach_sync(min_sigma_s=4.0)
    assert b.set_app_floor("youtubetv", 1.5, REPLAY) is True
    assert b.floor_for("youtubetv:espn") == 1.5 and b.floor_for("prime:x") == 4.0
    b.attach_cal(NS(sigma=lambda app: 2.5))
    assert b.floor_for("youtubetv:espn") == 2.5
    b.app_floor["youtubetv"] = 9.0                                     # an app floor can only lower
    assert b.floor_for("youtubetv:espn") == 4.0


# ------------------------------------------------------------------ the live error log
def test_live_error_log_is_numbers_only_per_app_and_hdmi(tmp_path):
    clock = [T0]
    s = SyncBook(lambda t: (30.0, 6.0), clock=lambda: clock[0], log_path=tmp_path / "sync.jsonl")
    s.measure("prime:abc", 31.0, "clock_fit", t=T0, sd=0.5, stick="HDMI1")
    s.measure("youtubetv:espn", 34.0, "flip", t=T0, sd=3.0, stick="HDMI2")
    s.note("prime:abc", "clock_stop", T0, 33.0, sd=1.0, stick="HDMI1")
    d = tmp_path / "sync_truth" / "delay_error"
    rows = [json.loads(x) for f in d.glob("*.jsonl") for x in f.read_text().splitlines()]
    assert len(rows) == 3 and {r["hdmi"] for r in rows} == {"HDMI1", "HDMI2"}
    assert all(isinstance(v, (int, float, bool, type(None))) or k in ("night", "app", "hdmi", "kind")
               for r in rows for k, v in r.items())
    assert not any("abc" in json.dumps(r) or "espn" in json.dumps(r) for r in rows)  # no channel, no address
    out = summarize_errors(rows)
    fit = next(x for x in out if x["kind"] == "clock_fit")
    assert fit["app"] == "prime" and fit["hdmi"] == "HDMI1" and fit["n"] == 1 and fit["mean_s"] == 1.0
