"""[T3 tester] Independent checks of the windowed-hindsight oracle teacher (oracle.solve_window), written by the
T3 test thread, not the builder. Four claims, each checked a different way than the builder's own tests:

1. EXACT inside the window: on small random nights the receding-horizon policy makes the same moves as a
   brute-force enumeration of every in-window schedule (same beyond-window value), and with a whole-night
   window it reaches the brute-force optimum of every legal schedule.
2. NO FUTURE LEAK: (a) mechanically, nothing at or beyond the window (any table, not just rewards) changes a
   move already made; (b) on real archived nights, every table column the window reads must be a function of
   the plays the feed has delivered by decision time (wall + feed_lag <= now). Truncating the unrevealed plays
   must not change those columns.
3. RULE #1: every switch leaves on a clean leave and lands on a dead ball, the first pick included; on real
   nights the app's grader counts 0 mid-play cuts and 0 mid-play entries at every buffer.
4. LOCKED NIGHTS: the sweep never reads a test-split (locked) night, and refuses --split test.

Tests marked xfail(strict=True) are KNOWN FINDINGS reported to the builder: they flip to a hard failure the day
the oracle is fixed, so the marker must then be removed.
"""
from __future__ import annotations

import copy
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from redzone.lab import oracle as O

ROOT = Path(__file__).resolve().parents[1]
ARCH = ROOT / "data/archive"


# ------------------------------------------------------------------------------------------------ helpers
def _night(rng, G=3, K=30, tune=5.0, lag=60.0, cont=True):
    """A random night. cont=True draws float rewards so ties (and tie-break differences) never happen."""
    draw = (lambda: rng.random((G, K))) if cont else (lambda: (rng.random((G, K)) < .2) * 1.0)
    tab = dict(big=draw(), crunch=draw(), mine=(rng.random((G, K)) < .05) * 1.0, dead=draw(),
               leave=rng.random((G, K)) < .5, land=rng.random((G, K)) < .6)
    tab["land"][rng.integers(G), 0] = True
    n = O.Night([SimpleNamespace(gid=f"g{g}") for g in range(G)], set(), lag, tune, 0.0, K * O.STEP_S)
    return n, tab


def _R(tab, obj):
    wb, wc, wm, wd, ws = O.OBJECTIVES[obj]
    return wb * tab["big"] + wc * tab["crunch"] + wm * tab["mine"] - wd * tab["dead"], ws


def _blk(n):
    return max(1, int(math.ceil(n.tune_s / O.STEP_S)))


def _W(n, feed_lag):
    lags = [O.lag_of(n.lag, r.gid) for r in n.replays]
    return max(_blk(n) + 1, min(O.window_steps(lg, feed_lag, n.tune_s) for lg in lags))


def _enumerate(R, tab, k0, g0, e, blk, ws, Vn):
    """Brute force, no memo: every schedule from (step k0, on g0) to the window end e. Each step either stays
    (reward) or, on a clean leave, switches to any other game that lands clean (black for blk steps)."""
    G = R.shape[0]
    best = -math.inf

    def walk(k, g, acc):
        nonlocal best
        if k >= e:
            best = max(best, acc + Vn[g])
            return
        walk(k + 1, g, acc + R[g, k])
        if tab["leave"][g, k]:
            for b in range(G):
                if b != g and tab["land"][b, k]:
                    walk(k + blk, b, acc - ws)
    walk(k0, g0, 0.0)
    return best


def _reference_policy(n, tab, feed_lag, obj="combined", horizon_s=120.0):
    """solve_window's contract, re-implemented by enumeration: plan only over the W revealed steps, value the
    window's end with beyond_value (the past), take the first move, replan."""
    R, ws = _R(tab, obj)
    G, K = R.shape
    blk, W, hz = _blk(n), _W(n, feed_lag), int(horizon_s // O.STEP_S)

    def val(k, kk, g):          # best value from step kk on game g, planning at step k
        e = min(K, k + W)
        Vn = O.beyond_value(R, k, hz) if e < K else np.zeros(G)
        return Vn[g] if kk >= e else _enumerate(R, tab, kk, g, e, blk, ws, Vn)

    first = [g for g in range(G) if tab["land"][g, 0]]
    cur = max(first, key=lambda g: val(blk, blk, g))
    out = [("start", None, cur, 0)]
    k = blk
    while k < K:
        stay = R[cur, k] + val(k, k + 1, cur)
        cands = [(val(k, k + blk, b) - ws, b) for b in range(G) if b != cur and tab["land"][b, k]]
        if tab["leave"][cur, k] and k + blk <= K and cands and max(cands)[0] > stay:
            b = max(cands)[1]
            out.append(("switch", cur, b, k))
            cur, k = b, k + blk
        else:
            k += 1
    return out


def _as_tuples(n, sw):
    gi = {r.gid: i for i, r in enumerate(n.replays)}
    out = []
    for s in sw:
        k = int(round((s.t - n.t0) / O.STEP_S))
        out.append(("start", None, gi[s.to], 0) if s.frm is None else ("switch", gi[s.frm], gi[s.to], k))
    return out


# ----------------------------------------------------------------------------------- 1. exact in the window
@pytest.mark.parametrize("tune", [5.0, 10.0])
@pytest.mark.parametrize("obj", ["combined", "big_live", "dead_air"])
def test_window_moves_match_brute_force_receding_horizon(tune, obj):
    """Same moves as enumerating every in-window schedule at every decision step (G=3, W=4..6 steps)."""
    rng = np.random.default_rng(hash((tune, obj)) % 2**32)
    for trial in range(12):
        lag = float(rng.choice([35.0, 40.0, 45.0]))          # W = 4..6 steps with feed 8 s
        n, tab = _night(rng, K=24, tune=tune, lag=lag)
        sw = O.solve_window(n, 8.0, obj, tab)
        assert _as_tuples(n, sw) == _reference_policy(n, tab, 8.0, obj), (trial, tune, obj, lag)


@pytest.mark.parametrize("tune", [5.0, 10.0, 15.0])
def test_whole_night_window_reaches_brute_force_optimum(tune):
    """Window >= night: the oracle's value equals the max over EVERY legal schedule (enumerated, G=3, K=10)."""
    rng = np.random.default_rng(int(tune))
    for trial in range(20):
        n, tab = _night(rng, K=10, tune=tune, lag=10_000.0, cont=bool(trial % 2))
        R, ws = _R(tab, "combined")
        blk = _blk(n)
        zero = np.zeros(R.shape[0])
        brute = max(_enumerate(R, tab, blk, g, R.shape[1], blk, ws, zero)
                    for g in range(R.shape[0]) if tab["land"][g, 0])
        got = O.schedule_value(n, tab, "combined", O.solve_window(n, 0.0, "combined", tab))
        assert abs(got - brute) < 1e-9, (trial, tune, got, brute)


def test_beyond_value_reads_only_the_past():
    rng = np.random.default_rng(0)
    R = rng.random((3, 50))
    for k in range(50):
        R2 = R.copy()
        R2[:, k:] = rng.random((3, 50 - k)) * 100
        assert np.array_equal(O.beyond_value(R, k, 24), O.beyond_value(R2, k, 24))


# --------------------------------------------------------------------------------------- 2a. no future leak
@pytest.mark.parametrize("lag,feed", [(40.0, 8.0), (60.0, 8.0), (86.0, 8.0), (116.0, 70.0)])
def test_no_move_depends_on_anything_at_or_beyond_the_window(lag, feed):
    """Stronger than the builder's check: EVERY table (rewards, dead air, leave, land) is re-drawn from the
    first unrevealed column k + W on, for EVERY decision step k; no move made at or before k may change."""
    rng = np.random.default_rng(int(lag + feed))
    for trial in range(4):
        n, tab = _night(rng, K=60, lag=lag)
        W = _W(n, feed)
        base = O.solve_window(n, feed, tab=tab)
        for k in range(_blk(n), 60 - W):
            tab2 = {key: v.copy() for key, v in tab.items()}
            c = k + W
            for key in ("big", "crunch", "dead"):
                tab2[key][:, c:] = rng.random(tab2[key][:, c:].shape) * 50
            tab2["mine"][:, c:] = rng.random(tab2["mine"][:, c:].shape) < .5
            tab2["leave"][:, c:] = rng.random(tab2["leave"][:, c:].shape) < .5
            tab2["land"][:, c:] = rng.random(tab2["land"][:, c:].shape) < .5
            got = O.solve_window(n, feed, tab=tab2)
            cut = n.t0 + k * O.STEP_S + 1e-6
            assert [s for s in got if s.t <= cut] == [s for s in base if s.t <= cut], (trial, k)


# ------------------------------------------------------------------- 2b. no future leak, against real plays
def _real_nights(league, split="fit", limit=2):
    if not (ARCH / league).exists():
        pytest.skip("needs the pinned eval data (scripts/fetch_eval_data.py)")
    sys.path.insert(0, str(ROOT / "scripts"))
    import eval_sports as ES
    from redzone.models import League
    out = []
    for day, reps, _t in ES.nights(League(league), 10_000, None, split):
        out.append((day, reps))
        if len(out) >= limit:
            break
    if not out:
        pytest.skip(f"no {league} {split} nights in the pinned data")
    return out


def _revealed(reps, now, feed_lag, push_s=900.0):
    """Another night the app cannot tell apart at clock time `now`: the plays the feed has delivered
    (wall + feed_lag <= now) are unchanged, every undelivered play happens push_s later. Nothing ends early, so
    any in-window difference is a real dependency on plays the buffer has not revealed."""
    out = []
    for r in reps:
        r2 = copy.copy(r)
        r2.wall = [w if w + feed_lag <= now else w + push_s for w in r.wall]
        r2._cache = {}
        out.append(r2)
    return out


def _window_leak(league, buffer_s, feed_lag, n_decisions=40):
    """Per table: share of in-window cells (games x W columns, sampled decision steps) whose value changes
    when the plays the feed has NOT yet delivered are removed. 0 everywhere = the window reads only the past."""
    from redzone.lab.backtest import STREAM_DELAY_S
    diffs, cells = {}, 0
    for _day, reps in _real_nights(league):
        t0 = min(r.wall[0] for r in reps) - 600.0
        t1 = max(r.wall[-1] for r in reps) + 600.0
        lag = STREAM_DELAY_S + buffer_s
        n = O.Night(reps, set(), lag, 5.0, t0, t1)
        W = _W(n, feed_lag)
        steps = np.arange(t0, t1, O.STEP_S)
        live = [k for k in range(len(steps) - W)
                if any(r.wall[0] + 300 < steps[k] - lag < r.wall[-1] - 300 for r in reps)]
        for k in np.linspace(0, len(live) - 1, n_decisions).astype(int):
            k = live[k]
            sub = steps[k:k + W]
            full = O.tables(O.Night(reps, set(), lag, 5.0, float(sub[0]), t1), sub)
            seen = O.tables(O.Night(_revealed(reps, float(steps[k]), feed_lag), set(), lag, 5.0, float(sub[0]), t1), sub)
            cells += full["big"].size
            for key in full:
                diffs[key] = diffs.get(key, 0) + int((full[key] != seen[key]).sum())
    return {k: round(v / max(1, cells), 4) for k, v in diffs.items()}


_NBA_LEAK = pytest.mark.xfail(strict=True, reason="T3 finding: NBA leave/land read the next logged action "
                              "(grading.resume_after: RESUME_LEAD 26 s / clock back-solve) beyond what the feed "
                              "has delivered; window_steps does not reserve that look-ahead")


@pytest.mark.parametrize("league,feed", [
    pytest.param("nba", 8.0, marks=_NBA_LEAK), pytest.param("nba", 70.0, marks=_NBA_LEAK),
    ("nfl", 8.0), ("nhl", 8.0), ("nhl", 75.0)])
def test_real_window_reads_only_delivered_plays(league, feed):
    """Every in-window cell of every table is the same on a night that agrees with this one on all the plays
    the feed has delivered and differs only after (60 s buffer)."""
    leak = _window_leak(league, 60.0, feed)
    assert all(v == 0 for v in leak.values()), leak


def test_real_nfl_window_at_measured_feed_lag():
    """NFL at its measured p90 feed lag (62 s): still clean."""
    leak = _window_leak("nfl", 60.0, 62.0)
    assert all(v == 0 for v in leak.values()), leak


@pytest.mark.xfail(strict=True, reason="T3 finding: solve_window floors the window at blk + 1 steps, so when the feed "
                                       "is slower than the buffer (window_steps == 0) it still plans on unrevealed steps")
def test_no_plan_beyond_the_true_window_when_feed_is_slower_than_buffer():
    rng = np.random.default_rng(9)
    lag, feed = 86.0, 85.0
    assert O.window_steps(lag, feed, 5.0) == 0
    for trial in range(10):
        n, tab = _night(rng, K=40, lag=lag)
        base = O.solve_window(n, feed, tab=tab)
        for k in range(_blk(n), 39):
            tab2 = {key: v.copy() for key, v in tab.items()}
            for key in ("big", "crunch", "dead"):
                tab2[key][:, k:] = rng.random(tab2[key][:, k:].shape) * 50      # nothing from k on is known
            got = O.solve_window(n, feed, tab=tab2)
            cut = n.t0 + k * O.STEP_S - 1e-6                                      # moves strictly before k
            assert [s for s in got if s.t < cut] == [s for s in base if s.t < cut], (trial, k)


@pytest.mark.xfail(strict=True, reason="T3 finding: buffer_sweep's window uses --feed-lag 8 s for every league while "
                                       "the measured p90 the adaptive buffer uses is 62-75 s (pick.FEED_LAG_P90)")
def test_sweep_window_uses_the_measured_feed_lag(tmp_path, monkeypatch):
    """Run the real sweep with its defaults and catch the feed lag it hands solve_window: it must be at least
    the measured p90 for that league (the same table the adaptive buffer is built from)."""
    if not (ARCH / "nba").exists():
        pytest.skip("needs the pinned eval data")
    sys.path.insert(0, str(ROOT / "scripts"))
    import buffer_sweep as BS
    from redzone.models import League
    from redzone.noggin import pick as PK
    seen = []
    real = BS.O.solve_window

    def spy(n, feed_lag, *a, **k):
        seen.append(feed_lag)
        return real(n, feed_lag, *a, **k)
    monkeypatch.setattr(BS.O, "solve_window", spy)
    assert BS.main(["--leagues", "nba", "--buffers", "60", "--max-nights", "1", "--out", str(tmp_path)]) == 0
    assert seen and min(seen) >= PK.FEED_LAG_P90[League.NBA], (min(seen), PK.FEED_LAG_P90[League.NBA])


def test_window_is_empty_when_the_feed_is_slower_than_the_buffer():
    """A 70 s feed behind an 86 s screen leaves at most 2 steps: the oracle may not plan 14 steps ahead."""
    assert O.window_steps(86.0, 70.0, 5.0) == 2
    assert O.window_steps(86.0, 85.0, 5.0) == 0


# ------------------------------------------------------------------------------------------------ 3. Rule #1
def _rule1_ok(n, tab, sw):
    blk = _blk(n)
    gi = {r.gid: i for i, r in enumerate(n.replays)}
    assert sw[0].frm is None and tab["land"][gi[sw[0].to], 0], "first pick lands mid-play"
    for s in sw[1:]:
        k = int(round((s.t - n.t0) / O.STEP_S))
        assert tab["leave"][gi[s.frm], k], ("mid-play cut", s)
        assert tab["land"][gi[s.to], k], ("mid-play entry", s)
        assert k + blk <= tab["land"].shape[1]
    ts = [s.t for s in sw]
    assert ts == sorted(ts) and len(set(ts[1:])) == len(ts) - 1


@pytest.mark.parametrize("obj", list(O.OBJECTIVES))
def test_rule1_on_random_nights_every_objective(obj):
    rng = np.random.default_rng(len(obj))
    for trial in range(25):
        n, tab = _night(rng, G=int(rng.integers(2, 6)), K=80, tune=float(rng.choice([5.0, 10.0])),
                        lag=float(rng.choice([45.0, 60.0, 90.0, 116.0])), cont=bool(trial % 2))
        _rule1_ok(n, tab, O.solve_window(n, float(rng.choice([2.0, 8.0, 30.0])), obj, tab))


@pytest.mark.xfail(strict=True, reason="T3 finding: when no game is dead at the first step, solve_window falls back "
                                       "to `or list(range(G))` and starts on a game mid-play")
def test_first_pick_never_lands_mid_play():
    rng = np.random.default_rng(1)
    n, tab = _night(rng, K=30)
    tab["land"][:, 0] = False
    _rule1_ok(n, tab, O.solve_window(n, 8.0, tab=tab))


@pytest.mark.parametrize("league", ["nba", "nfl", "nhl"])
@pytest.mark.parametrize("buffer_s", [45.0, 90.0])
def test_rule1_on_real_nights_graded_by_the_app_grader(league, buffer_s):
    from redzone.lab.backtest import STREAM_DELAY_S
    for _day, reps in _real_nights(league, limit=1):
        t0 = min(r.wall[0] for r in reps) - 600.0
        t1 = max(r.wall[-1] for r in reps) + 600.0
        n = O.Night(reps, {"WSH"}, STREAM_DELAY_S + buffer_s, 5.0, t0, t1)
        tab = O.tables(n, np.arange(t0, t1, O.STEP_S))
        sw = O.solve_window(n, 8.0, "combined", tab)
        _rule1_ok(n, tab, sw)
        card = O.grade(n, sw)
        assert card.cuts == [], card.cuts[:3]
        assert card.cut.summary()["entries_mid_play"] == 0
        full = O.schedule_value(n, tab, "combined", O.solve(n, "combined", tab))
        assert O.schedule_value(n, tab, "combined", sw) <= full + 1e-6     # never above full hindsight


# ------------------------------------------------------------------------------------------ 4. locked nights
def test_sweep_refuses_the_locked_split():
    sys.path.insert(0, str(ROOT / "scripts"))
    import buffer_sweep as BS
    for bad in ("test", "all"):
        with pytest.raises(SystemExit):
            BS.main(["--split", bad, "--out", "/nonexistent"])


@pytest.mark.parametrize("league", ["nba", "nfl", "nhl"])
def test_fit_split_never_contains_a_locked_night(league):
    from redzone.lab import splits
    if not (ARCH / league).exists():
        pytest.skip("needs the pinned eval data")
    test = splits.nights_for(ARCH / league, "test")
    for which in ("fit", "train", "valid"):
        assert not (splits.nights_for(ARCH / league, which) & test), which
    s = splits.league_split(league, splits.archive_nights(ARCH / league))
    assert not splits.leaks(s)
    if s.train + s.valid and s.test:
        assert max(s.train + s.valid) < min(s.test)          # time split: locked nights come last


def test_sweep_run_loads_no_locked_game(tmp_path, monkeypatch):
    """Run the real sweep (1 NBA fit night, 1 buffer) and record every summary file it opens: none may belong
    to a locked night, and every row it writes is a fit night."""
    if not (ARCH / "nba").exists():
        pytest.skip("needs the pinned eval data")
    import json

    sys.path.insert(0, str(ROOT / "scripts"))
    import buffer_sweep as BS
    import eval_sports as ES
    from redzone.lab import splits
    opened = []
    real = ES.Replay.load.__func__

    def spy(cls, league, path):
        opened.append(Path(path))
        return real(cls, league, path)
    monkeypatch.setattr(ES.Replay, "load", classmethod(spy))
    assert BS.main(["--leagues", "nba", "--buffers", "60", "--max-nights", "1", "--out", str(tmp_path)]) == 0
    test_nights = splits.nights_for(ARCH / "nba", "test")
    fit = splits.nights_for(ARCH / "nba", "fit")
    locked_gids = set()
    for d in test_nights:
        sb = ARCH / "nba" / f"scoreboard_{d}.json"
        if sb.exists():
            locked_gids |= {e["id"] for e in json.loads(sb.read_text()).get("events", [])}
    assert opened, "the sweep loaded nothing"
    assert not {p.stem.split("_")[-1] for p in opened} & locked_gids
    rows = json.loads((tmp_path / "60" / "nba.json").read_text())
    assert rows and set(rows) <= fit
