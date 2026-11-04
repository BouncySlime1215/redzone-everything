"""[switch timing] Detection lag from the flight recorder, the measured-lag backtest, and the gate's
feed-lag estimate (redzone/lab/timing.py, redzone/decider/screen.py)."""
import json
import random

import pytest

from redzone.decider import screen
from redzone.decider.screen import Timeline
from redzone.lab import backtest, timing
from redzone.models import League


def _rows(tmp_path, rows):
    p = tmp_path / "2026-11-02" / "plays.jsonl"
    p.parent.mkdir(parents=True)
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return p


def _row(t, wall, game="g1", typ="Shot", league="nhl", source="espn_summary"):
    return {"t": t, "wall": wall, "lag": t - wall, "league": league, "game": game, "type": typ, "source": source}


def test_first_batch_and_gaps_are_not_feed_lag(tmp_path):
    p = _rows(tmp_path, [
        _row(1000, 100),                    # first batch: a backlog from before we tracked the game
        _row(1030, 990, typ="Stoppage"),     # tracked: 40 s
        _row(1060, 1010),                    # tracked: 50 s
        _row(2000, 1990, typ="Timeout"),     # after a 940 s silence (Mac asleep): not feed lag
    ])
    kept = timing.tracked(timing.load_rows([p]))
    assert sorted(r.lag for r in kept) == [40, 50]


def test_dead_ball_report_and_lead(tmp_path):
    rows = [_row(1000, 900)] + [_row(1000 + 20 * i, 1000 + 20 * i - 40 - i, typ="Stoppage") for i in range(1, 11)]
    rep = timing.detection_report(timing.load_rows([_rows(tmp_path, rows)]), ["2026-11-02"])
    d = rep.by_league["nhl"]["espn_summary"]["dead_ball"]
    assert d["n"] == 10 and d["within_3s_pct"] == 0.0 and 41 <= d["p50"] <= 50
    lead = timing.lead_table(rep, {"youtubetv:espn": 26.4})
    assert lead["youtubetv:espn / nhl"]["lead_median_lag"] < 0   # it airs before we hear of it


def test_quantile_interpolates():
    assert timing.quantile([0, 10], 0.5) == 5
    assert timing.quantile([], 0.5) != timing.quantile([], 0.5)   # nan


def test_sampler_is_seeded_and_stays_inside_the_measured_range():
    prof = {"leagues": {"nhl": {"n": 100, "q": [30.0 + 2 * i for i in range(21)]}}}
    s = timing.LagSampler(prof)
    a = [s.draw("nhl", random.Random(7)) for _ in range(3)]
    b = [s.draw("nhl", random.Random(7)) for _ in range(3)]
    assert a == b
    xs = [s.draw("nhl", random.Random(i)) for i in range(500)]
    assert min(xs) >= 30.0 and max(xs) <= 68.0       # never past the p95 (back-fills)
    assert s.has("cbb") is False and timing.LagSampler({"leagues": {"nba": prof["leagues"]["nhl"]}}).has("cbb")
    with pytest.raises(KeyError):
        s.draw("mlb", random.Random(1))


def test_shipped_profile_covers_the_scorecard_leagues():
    s = timing.LagSampler()
    for lg in ("nfl", "nba", "nhl"):
        assert s.has(lg)
        med = timing.quantile([s.draw(lg, random.Random(i)) for i in range(400)], 0.5)
        assert 25 <= med <= 70, (lg, med)


def test_build_profile_prefers_dead_balls(tmp_path):
    rows = [_row(1000, 900)]
    rows += [_row(1000 + i, 1000 + i - 40, typ="Stoppage") for i in range(1, 260)]
    rows += [_row(1300 + i, 1300 + i - 150, typ="Hit") for i in range(1, 100)]   # back-filled bookkeeping
    prof = timing.build_profile(timing.load_rows([_rows(tmp_path, rows)]), ["2026-11-02"])
    row = prof["leagues"]["nhl"]
    assert row["basis"] == "dead_ball" and row["q"][-1] == 40


def test_lag_samples_are_not_capped_under_the_measured_median():
    """NHL median 50 s: a 45 s cap made a slow feed look fast enough to cover the screen."""
    from redzone.models import GameState, Phase, Play, Team
    assert screen.LAG_SAMPLE_CAP_S >= 120
    now = [1000.0]
    tl = Timeline(League.NHL, clock=lambda: now[0])

    def poll(plays):
        g = GameState(league=League.NHL, game_id="g", home=Team("H", "Home", 0), away=Team("A", "Away", 0),
                      phase=Phase.LIVE, period=1, clock_s=600, recent_plays=plays,
                      last_play=plays[-1] if plays else None, updated_at=now[0])
        tl.observe(g)
    poll([Play(id="p0", kind="shot", text="shot", wallclock=990.0)])
    now[0] = 1010.0
    poll([Play(id="p0", kind="shot", text="shot", wallclock=990.0),
          Play(id="p1", kind="shot", text="shot", wallclock=930.0)])   # 80 s behind the last fresh poll
    assert tl.lag_est() == 70.0


def test_backtest_rejects_an_unknown_feed_lag_model(cfg):
    with pytest.raises(ValueError):
        backtest.run([], cfg, set(), start=0, end=1, feed_lag_model="guess")


def test_fixed_feed_lag_keeps_the_screen_where_it_was():
    assert backtest.feed_lag_knobs(None) == {}
    k = backtest.feed_lag_knobs(45)
    assert k["feed_lag"] + k["delay"] == backtest.SCREEN_LAG_S
    with pytest.raises(ValueError):
        backtest.feed_lag_knobs(-1)


def test_time_shift_buffer_moves_the_screen_and_calibrates_every_app():
    k = backtest.feed_lag_knobs(None, 60)
    assert k["feed_lag"] + k["delay"] == backtest.STREAM_DELAY_S + 60 and k["calibrate_targets"]
    with pytest.raises(ValueError):
        backtest.feed_lag_knobs(None, 500)


def test_the_night_tv_flip_matches_the_live_lab_distribution():
    """Live lab Nov 4: 64/64 SmartThings flips, command -> confirmed p50 1.89 s, p95 2.18 s, max 2.58 s."""
    from redzone.lab.night import smartthings as st
    rng = random.Random(3)
    xs = sorted(st.draw(rng) + rng.uniform(0, 0.3) for _ in range(4000))
    assert abs(xs[len(xs) // 2] - 1.89) < 0.06 and abs(xs[int(0.95 * len(xs))] - 2.18) < 0.08
    assert xs[-1] <= 2.58 + 0.15
