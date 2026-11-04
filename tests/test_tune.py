"""The decider auto-tuner (redzone/lab/tune.py) and the dead-ball landing scorecard line."""
import numpy as np

from redzone.lab import cutgrade, tune


def test_landed_dead_pct_counts_entries_not_mid_play():
    c = cutgrade.CutCard(entries=40, entries_mid_play=10)
    assert c.summary()["landed_dead_pct"] == 75.0
    assert cutgrade.CutCard().summary()["landed_dead_pct"] == 100.0   # no entries: nothing landed badly


def test_split_is_by_time_and_never_straddles_a_night():
    items = [f"202610{d:02d}{x}" for d in range(10, 22) for x in "ab"]
    h = tune.split(items, lambda s: s[:8])
    nights = [sorted({i[:8] for i in b}) for b in h["blocks"]]
    assert len(h["blocks"]) == tune.FOLDS + 1
    flat = [n for b in nights for n in b]
    assert flat == sorted(flat) and len(flat) == len(set(flat))   # oldest first, each night once
    assert sorted(i for b in h["blocks"] for i in b) == sorted(items)


def test_forward_chaining_folds_validate_on_newer_nights():
    items = [f"202610{d:02d}" for d in range(10, 22)]
    halves = {"nba": tune.split(items, lambda s: s)}
    for k in range(1, tune.FOLDS + 1):
        train, valid = tune.fold(halves, k)
        assert train["nba"] and valid["nba"]
        assert max(train["nba"]) < min(valid["nba"])
        assert not set(train["nba"]) & set(valid["nba"])


def test_encode_decode_round_trip_and_entry_modes():
    p = {k.key: (k.lo + k.hi) / 2 for k in tune.SPACE}
    p = tune.decode(tune.encode(p))
    assert tune.decode(tune.encode(p)) == p
    for mode, lg_full, lg_hold in ((0, [], []), (1, [], ["nba"]), (2, ["nba"], [])):
        o = tune.to_overrides({"nba": mode})
        assert o["decider.entry.leagues"] == ["nfl"] + lg_full
        assert o["decider.entry.hold_only"] == lg_hold
    # Out-of-box samples are clipped, never outside the knob's range.
    q = tune.decode(np.full(len(tune.SPACE), 3.0))
    for k in tune.SPACE:
        assert k.lo <= q[k.key] <= k.hi


def _m(**kw):
    m = dict(mid_play_cuts=0, entries_mid_play=5, plays_missed_entry=1, dead_air_pct=10.0, big_seen_pct=90.0,
             your_team_crunch_live_pct=100.0, your_team_big_seen_pct=100.0, crunch_live_pct=80.0,
             big_live_pct=40.0, landed_dead_pct=95.0, entry_wait_s=20.0, nfl_drive_live_pct=15.0)
    return m | kw


def test_hard_constraints_beat_any_reward():
    base = {"nba": _m()}
    better_but_cuts = {"nba": _m(crunch_live_pct=100.0, mid_play_cuts=1)}
    feasible = {"nba": _m(crunch_live_pct=81.0)}
    assert tune.fitness(feasible, base) < tune.fitness(better_but_cuts, base)
    for bad in (dict(entries_mid_play=6), dict(plays_missed_entry=2), dict(dead_air_pct=10.5),
                dict(big_seen_pct=89.0), dict(your_team_crunch_live_pct=99.0)):
        assert tune.violation(_m(**bad), _m()) > 0, bad
    assert tune.violation(_m(), _m()) == 0
    assert tune.fitness({"nba": _m(landed_dead_pct=100.0)}, base)[1] < 0   # more dead-ball landings = gain


def test_cma_finds_a_quadratic_minimum():
    es = tune.CMA(np.full(4, 0.5), 0.2, 10, seed=1)
    target = np.array([0.2, 0.8, 0.3, 0.6])
    for _ in range(60):
        xs = es.ask()
        f = [float(((np.clip(x, 0, 1) - target) ** 2).sum()) for x in xs]
        es.tell([np.clip(x, 0, 1) for x in xs], sorted(range(len(xs)), key=f.__getitem__))
    assert np.abs(es.mean - target).max() < 0.02


def test_cma_state_round_trips():
    import json
    a = tune.CMA(np.full(3, 0.5), 0.2, 6, seed=3)
    xs = a.ask()
    a.tell(xs, list(range(6)))
    b = tune.CMA(np.zeros(3), 0.5, 6, seed=99)
    b.load(json.loads(json.dumps(a.state())))
    assert np.allclose(a.ask(), b.ask())


def test_locked_nights_never_in_any_block():
    items = [f"202610{d:02d}" for d in range(10, 32)] + ["20261101"]
    h = tune.split(items, lambda s: s, lambda s: s >= "20261029")
    assert h["locked"] == ["20261029", "20261030", "20261031", "20261101"]
    tuned = {i for b in h["blocks"] for i in b}
    assert not tuned & set(h["locked"])
    assert sorted(tuned | set(h["locked"])) == sorted(items)


def test_a_held_out_slate_takes_its_whole_night_out_of_tuning():
    items = ["20251102a", "20251102b", "20251109a", "20251116a"]
    h = tune.split(items, lambda s: s[:8], lambda s: s == "20251102b")
    assert h["locked"] == ["20251102b"]
    assert "20251102a" not in [i for b in h["blocks"] for i in b]


def test_tuning_holds_out_the_pinned_locked_split():
    """Salvaged from #16: the hard-coded section-7 slice predates eval-data-v1, so the pinned
    LOCKED nights (splits.nights_for(..., "test")) are held out too."""
    from redzone.lab import splits, tune
    for lg in ("nfl", "nba", "nhl"):
        d = tune.ROOT / f"data/archive/{lg}"
        if not d.exists():
            continue
        assert tune.locked_nights(lg) == set(splits.nights_for(d, "test") or ())
