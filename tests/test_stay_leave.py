"""EXP-1712 stay/leave: floors, hysteresis, labels and the no-locked-night manifest."""
import numpy as np

from redzone.lab import splits
from redzone.lab import stay_leave as SL


def test_roll_sum_and_label_are_past_and_future_windows():
    x = np.arange(1, 7, dtype=float)[None]          # 1..6
    assert SL._roll_sum(x, 2)[0].tolist() == [1, 3, 5, 7, 9, 11]
    assert SL.label(x, 2)[0].tolist() == [3, 5, 7, 9, 11, 6]


def test_floor_beats_any_alternative():
    for lg, per, clock in (("nfl", 4, 120), ("nba", 4, 60), ("nhl", 3, 100), ("nba", 5, 200), ("mlb", 8, None)):
        d, _ = SL.stay_or_leave({"league": lg, "period": per, "clock_s": clock, "home_score": 10, "away_score": 9,
                          "value": 0.0, "alts": [{"game_id": "x", "value": 1e9}], "ts": 0})
        assert d.action == "stay" and d.floor, lg


def test_low_leverage_leaves_only_past_the_band():
    ctx = {"league": "nba", "period": 1, "clock_s": 600, "home_score": 10, "away_score": 30, "value": 1.0, "ts": 0}
    st = SL.State(m=0.5)
    d, st = SL.stay_or_leave({**ctx, "alts": [{"game_id": "a", "value": 1.4}]}, st)
    assert d.action == "stay"
    d, st = SL.stay_or_leave({**ctx, "alts": [{"game_id": "a", "value": 1.6}]}, st)
    assert d.action == "leave" and d.alt_game_id == "a"
    assert SL.band(st, 10) == 1.0 and SL.band(st, 10 + 16 * 60) == 0.5


def test_band_non_decreasing_in_recent_switches():
    vals = [SL.band(SL.State(switches=list(range(k)), m=0.5), 100) for k in range(6)]
    assert vals == sorted(vals)


def test_manifest_never_lists_locked_nights():
    nights = [f"202610{d:02d}" for d in range(1, 21)]
    s = splits.split_nights(nights)
    man = SL.manifest({"nba": s})
    assert not (set(man["nba"]["train_nights"]) | set(man["nba"]["valid_nights"])) & set(s.test)
    assert not any(w in src for src in man["label_sources"] for w in ("teacher", "nick", "press", "userstate"))


def test_dwell_backs_off_under_whiplash():
    st = SL.State(m=0.5)
    flips, on = 0, "a"
    for i in range(30):                                  # 10 min, the lead trades every 20 s
        ts = 20.0 * i
        other = "b" if on == "a" else "a"
        ctx = {"league": "nba", "period": 1, "clock_s": 600, "home_score": 10, "away_score": 30, "ts": ts,
               "value": 3.0 if i % 2 else 1.0, "alts": [{"game_id": other, "value": 1.0 if i % 2 else 3.0}]}
        d, st = SL.stay_or_leave(ctx, st)
        if d.action == "leave":
            flips, on = flips + 1, other
    assert flips <= 5
