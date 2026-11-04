"""Noggin Timing v1 + data factory: labels match the live tracker, splits are whole nights, bounds are safe."""
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from redzone.config import Config
from redzone.decider import cuts
from redzone.models import League
from redzone.noggin import features as F
from redzone.noggin import live as L
from redzone.noggin import timing as T

T0 = datetime(2025, 1, 10, 0, 30, tzinfo=timezone.utc)   # Jan 9 evening ET


def _p(i, sec, ttype, text, clock, period=1, home=0, away=0):
    return {"id": str(i), "sequenceNumber": i, "type": {"text": ttype}, "text": text, "period": {"number": period},
            "clock": {"displayValue": clock}, "wallclock": (T0 + timedelta(seconds=sec)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "homeScore": home, "awayScore": away}


GAME = [
    _p(1, 0, "Jumpball", "jump ball", "12:00"),
    _p(2, 20, "Jump Shot", "A makes jumper", "11:40", home=2),
    _p(3, 40, "Full Timeout", "Team full timeout", "11:20", home=2),
    # 150 s of dead time, then 10 s of game clock ran before the next logged shot
    _p(4, 200, "Jump Shot", "B misses jumper", "11:10", home=2),
    _p(5, 202, "Defensive Rebound", "A rebound", "11:08", home=2),
    _p(6, 230, "Shooting Foul", "B shooting foul", "10:40", home=2),
    _p(7, 260, "Free Throw - 1 of 2", "A makes free throw 1 of 2", "10:40", home=3),
    _p(8, 280, "Free Throw - 2 of 2", "A makes free throw 2 of 2", "10:40", home=4),
    _p(9, 300, "Jump Shot", "B makes three point jumper", "10:20", home=4, away=3),
]


def test_factory_labels_come_from_the_live_tracker():
    rows = F.windows_for_game(League.NBA, GAME)
    to = [r for r in rows if r["kind"].startswith("timeout")]
    assert len(to) == 1
    # restart worked back from the game clock: 200 - (11:20 - 11:10 = 10 s) - 3 s margin = 187 -> 147 s dead
    assert to[0]["length_s"] == pytest.approx(147.0)
    assert to[0]["exact"] and to[0]["timeout_index"] == 1 and to[0]["kind"] == "timeout_long"
    foul = [r for r in rows if r["kind"] == "shooting_foul"]
    assert foul and foul[0]["prev_kind_code"] == F.KIND_CODE["timeout_long"]


def test_factory_context_matches_live_hook():
    """The features the live hook computes for an open window equal the factory's for that window."""
    rows = F.windows_for_game(League.NBA, GAME)
    dt = cuts.DeadTracker(League.NBA)
    from redzone.sources import espn
    for k, p in enumerate(GAME[:3]):     # stop right after the timeout: it's open
        w = espn._ts(p["wallclock"])
        pc = espn._play_clock(League.NBA, p)
        dt.add(p["id"], w, w, espn._kind(League.NBA, p), p["text"], pc["type_text"], pc["clock"], pc["period"])
    open_w = dt.windows()[-1]
    assert open_w.end is None
    hook = L.TimingHook(model=None, league=League.NBA)
    live_x = hook._features(dt, open_w)
    fac = next(r for r in rows if r["kind"].startswith("timeout"))
    assert np.allclose(live_x, F.vector(fac), equal_nan=True)


def _table(pd, nights):
    return pd.DataFrame({"night": nights, "game_id": [f"g{n}" for n in nights], "league": "nba",
                         "length_s": 30.0, "kind": "foul", **{f: 0.0 for f in F.FEATURES}})


def test_splits_are_whole_nights_time_ordered_and_skip_the_eval_window():
    pd = pytest.importorskip("pandas")
    from redzone.noggin import factory as FA
    nights = [f"2024{m:02d}{d:02d}" for m in range(1, 13) for d in (1, 15)] + ["20261020", "20261101"]
    t = FA.drop_eval_nights(_table(pd, nights * 3), "nba")
    assert t.night.max() < FA.EVAL_FIRST_NIGHT["nba"]
    t = FA.assign_splits(t)
    assert t.groupby("night").split.nunique().max() == 1
    last = {s: t[t.split == s].night for s in ("train", "calib", "test")}
    assert last["train"].max() < last["calib"].min() <= last["calib"].max() < last["test"].min()


def test_flag_off_by_default_and_hook_when_on():
    cfg = Config(user_path=Path("/nonexistent.yaml"))
    assert cfg.get("noggin.timing.leagues") == []
    assert L.hook_for(cfg, League.NBA) is None

    class C:
        def get(self, k, d=None):
            return ["nba"] if k == "noggin.timing.leagues" else d
    h = L.hook_for(C(), League.NBA)
    assert h is not None and h.model.league == "nba"
    assert L.hook_for(C(), League.NHL) is None


@pytest.fixture(scope="module")
def model():
    m = T.model_for("nba")
    assert m is not None, "redzone/noggin/models/timing_nba.v1.npz ships with the repo"
    return m


def test_model_reading_is_sane_and_fast(model):
    rows = F.windows_for_game(League.NBA, GAME)
    x = F.vector(next(r for r in rows if r["kind"].startswith("timeout")))
    lo, med = model.reading(x, 0.0, "timeout_long")
    assert 0.0 <= lo <= med <= T.HORIZON_BINS * T.BIN_S
    assert lo > 60.0           # a quarter's first timeout is a commercial: over a minute is certain
    later_lo, _ = model.reading(x, 150.0, "timeout_long")
    assert later_lo < lo        # 150 s in, less of it is left
    ts = []
    for _ in range(30):
        a = time.perf_counter()
        model.reading(x, 20.0, "timeout_long")
        ts.append(time.perf_counter() - a)
    assert np.median(ts) < 0.005    # < 5 ms per reading (one per open window per 5 s)


def test_numpy_forest_matches_meta(model):
    assert model.info["features"] == len(T.MODEL_FEATURES)
    assert model.info["numpy_vs_lgb_max_abs"] < 1e-5
    assert len(model.offsets) == len(T.E_BUCKETS) - 1 and all(o <= 0 for o in model.offsets)


def test_dead_tracker_uses_the_hook_and_never_goes_below_coverage(model):
    from redzone.sources import espn
    dt = cuts.DeadTracker(League.NBA)
    for p in GAME[:3]:
        w = espn._ts(p["wallclock"])
        pc = espn._play_clock(League.NBA, p)
        dt.add(p["id"], w, w, espn._kind(League.NBA, p), p["text"], pc["type_text"], pc["clock"], pc["period"])
    w = dt.windows()[-1]
    base = dt._open_until(w)
    dt.noggin = L.TimingHook(model, League.NBA)
    with_model = dt._open_until(w)
    assert with_model >= w.start
    assert with_model != base          # the calibrated bound replaces the table floor
    dt.seen(fresh_at=w.start + 400, lag=10)   # the data proves it lasted past where the model would stop
    assert dt._open_until(w) >= dt._covered()


def _nfl_rows():
    pd = pytest.importorskip("pandas")
    base = dict(game_id="2023_01_A_B", timeout=0.0, touchdown=0.0, field_goal_result=None, extra_point_result=None,
                interception=0.0, fumble_lost=0.0, sack=0.0, complete_pass=0.0, safety=0.0, sp=0.0,
                total_home_score=0.0, total_away_score=0.0, season_type="REG", two_point_attempt=0.0)
    t = lambda s: (T0 + timedelta(seconds=s)).strftime("%Y-%m-%dT%H:%M:%S.000Z")  # noqa: E731
    rows = [
        dict(play_id=1, time_of_day=t(0), time="02:40", qtr=2, play_type="run", desc="A runs"),
        dict(play_id=2, time_of_day=t(30), time="02:10", qtr=2, play_type="pass", desc="A pass", complete_pass=1.0),
        # the clock crosses 2:00 here: an unseen two-minute warning
        dict(play_id=3, time_of_day=t(200), time="01:55", qtr=2, play_type="pass", desc="A pass TOUCHDOWN",
             touchdown=1.0, sp=1.0, total_home_score=6.0),
        dict(play_id=4, time_of_day=t(205), time="01:55", qtr=2, play_type="extra_point", desc="kick GOOD",
             extra_point_result="good", total_home_score=7.0),
        dict(play_id=5, time_of_day=None, time="00:00", qtr=2, play_type=None, desc="END QUARTER 2"),
    ]
    return pd.DataFrame([{**base, **r} for r in rows])


def test_nfl_loader_looks_like_the_espn_feed():
    from redzone.noggin import factory as FA
    plays, skip = FA._plays_football(_nfl_rows())
    types = [p["type"]["text"] for p in plays]
    assert types == ["Rush", "Pass Reception", "Passing Touchdown", "End of Half"]   # XP folded into the TD
    assert plays[-1]["wallclock"] == plays[-2]["wallclock"]        # ESPN stamps END QUARTER at the last play
    assert len(skip) == 1 and skip[0][1] - skip[0][0] == pytest.approx(170.0)


def test_hook_falls_back_for_kinds_the_model_never_saw():
    class M:
        info = {"kinds": ["huddle"]}

        def reading(self, x, e, kind):
            raise AssertionError("must not be asked")
    hook = L.TimingHook(M(), League.NFL)
    w = cuts.Window(start=100.0, end=None, kind="tv_timeout")
    assert hook.reading(None, w, 10.0) is None
