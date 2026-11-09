"""Features v3: recent history at the feed head, planted futures per feature family, one definition for rows and the live feed.

Why: the first honest NBA model sits at K4 86.3 against hindsight 97.3, and the recent-history signals that could close that gap are
also the easiest place to smuggle the future into a row. Each family gets a planted-future test: a future event must not move any
earlier row, and a builder that peeks must be caught by the prefix rebuild.
"""
import datetime as dt
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from redzone.noggin import gamevalue as G
from redzone.noggin import value_shards as V

T0 = dt.datetime(2024, 1, 10, 0, 30, tzinfo=dt.timezone.utc)


def _iso(sec):
    return (T0 + dt.timedelta(seconds=sec)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _play(i, kind="Jump Shot", text="", home=0, away=0, period=None):
    return {"id": str(i), "type": {"text": kind}, "text": text, "period": {"number": period or 1 + i // 60},
            "clock": {"displayValue": f"{11 - i % 12}:00"}, "wallclock": _iso(i * 60), "scoringPlay": False, "shootingPlay": True,
            "scoreValue": 0, "homeScore": home, "awayScore": away}


def _game(n=240):
    """Quiet basketball every 60 s: a home basket every 7th play, a foul every 11th, a turnover every 13th, a timeout every 17th."""
    plays, home = [], 0
    for i in range(n):
        home += 2 if i % 7 == 0 else 0
        kind = "Timeout" if i % 17 == 0 else "Lost Ball Turnover" if i % 13 == 0 else "Personal Foul" if i % 11 == 0 else "Jump Shot"
        plays.append(_play(i, kind, home=home))
    return {"plays": plays}


def _football(n=180):
    plays, home = [], 0
    for i in range(n):
        home += 7 if i % 37 == 36 else 0
        kind = "Timeout" if i % 23 == 0 else "Pass Interception Return" if i % 19 == 0 else "Penalty" if i % 29 == 0 else "Rush"
        plays.append({**_play(i, kind, home=home, period=1 + i // 45), "start": {"down": 1 + i % 4, "distance": 10, "yardsToEndzone": 50},
                      "end": {"yardsToEndzone": 45}})
    return {"drives": {"previous": [{"plays": plays[i:i + 9]} for i in range(0, n, 9)]}}


@pytest.fixture
def v3():
    V.select_features("v3")
    yield
    V.select_features("v2")


def _rows(league, summary, **kw):
    return V.build_rows(league, "g1", summary, night="20240109", season=2024, source="hoopr_espn_nba", license_line="x", plan="B", **kw)


def test_v3_is_off_unless_asked_for():
    assert V.FEATURES_VERSION == G.GV_FEATURES_VERSION == "v2" and len(V.VALUE_FEATURES) == 23
    assert not set(G.HISTORY_FEATURES) & set(V.VALUE_COLUMNS)


def test_v3_only_appends_so_the_old_columns_keep_their_place(v3):
    assert V.FEATURES_VERSION == G.GV_FEATURES_VERSION == "v3"
    assert V.VALUE_FEATURES[:23] == G.GV_FEATURE_SETS["v2"] and V.VALUE_FEATURES[23:] == G.HISTORY_FEATURES


def test_an_unknown_version_stops_rather_than_guessing():
    with pytest.raises(ValueError, match="known versions"):
        V.select_features("v9")


def test_a_v3_row_has_the_same_v2_features_as_a_v2_row(v3):
    summary = _game()
    new = _rows("nba", summary)
    V.select_features("v2")
    old = _rows("nba", summary)
    V.select_features("v3")
    assert len(new) == len(old)
    for f in G.GV_FEATURE_SETS["v2"]:
        assert np.array_equal(new[f].to_numpy(), old[f].to_numpy(), equal_nan=True), f


def _plain(events):
    """[(kind, home, away), ...] one play a minute -> (timed plays for history_now)."""
    return [(float(i * 60), _play(i, k, home=h, away=a)) for i, (k, h, a) in enumerate(events)]


def test_runs_points_and_who_is_coming_back():
    got = G.history_now("nba", _plain([("Jump Shot", 10, 20), ("Jump Shot", 12, 20), ("Jump Shot", 15, 20), ("Jump Shot", 15, 20), ("Jump Shot", 17, 20), ("Jump Shot", 17, 20)]))
    assert got["run_pts"] == 7.0 and got["run_by_trailing"] == 1.0        # 2 + 3 + 2 unanswered, still behind
    assert got["pts_last3"] == 2.0 and got["swing_last5"] == 7.0          # -10 five plays ago, -3 now
    assert G.history_now("nba", _plain([("Jump Shot", 10, 0), ("Jump Shot", 10, 3)]))["run_pts"] == 3.0   # the other team answered


def test_a_score_correction_clears_the_run():
    got = G.history_now("nba", _plain([("Jump Shot", 10, 0), ("Jump Shot", 8, 0)]))
    assert got["run_pts"] == 0.0 and got["run_by_trailing"] == 0.0


def test_turnovers_fouls_and_timeouts_come_from_the_play_type():
    plays = _plain([("Timeout", 0, 0), ("Bad Pass Turnover", 0, 0), ("Personal Foul", 0, 0), ("Traveling", 0, 0), ("Jump Shot", 0, 0)])
    got = G.history_now("nba", plays)
    assert got["to_5m"] == 2.0 and got["fouls_5m"] == 1.0 and got["timeouts_period"] == 1.0 and got["since_timeout_s"] == 240.0
    assert got["timeout_recent"] == 0.0 and G.history_now("nba", plays[:2])["timeout_recent"] == 1.0
    assert G.play_kind("nfl", {"type": {"text": "Pass Interception Return"}}) == "turnover" and G.play_kind("nfl", {"type": {"text": "Penalty"}}) == "foul"


def test_old_events_age_out_and_a_new_period_resets_timeouts():
    plays = [(0.0, _play(0, "Personal Foul")), (290.0, _play(1, "Timeout", period=1)), (400.0, _play(2, "Timeout", period=2)), (401.0, _play(3, period=2))]
    got = G.history_now("nba", plays)
    assert got["fouls_5m"] == 0.0 and got["timeouts_period"] == 1.0 and got["since_timeout_s"] == 1.0


def test_pace_counts_recent_plays_and_says_when_it_cannot_know():
    plays = [(float(i * 30), _play(i)) for i in range(12)]
    got = G.history_now("nba", plays)
    assert got["plays_2m"] == 4.0 and got["sec_per_play_10"] == 30.0
    assert G.history_now("nba", plays[:5])["sec_per_play_10"] == -1.0


@pytest.mark.parametrize("league,make", [("nba", _game), ("nfl", _football)])
def test_every_row_rebuilds_from_a_cut_feed(v3, league, make):
    summary = make()
    rows = _rows(league, summary)
    checked, bad = V.prefix_check(league, "g1", summary, rows, n=60)
    assert checked == 60 and bad == []


def test_a_cut_feed_matches_even_with_a_play_stamped_out_of_order(v3):
    summary = _game()
    summary["plays"][50]["wallclock"] = _iso(50 * 60 + 200)      # a later stamp than the two plays after it
    _, bad = V.prefix_check("nba", "g1", summary, _rows("nba", summary), n=60)
    assert bad == []


def test_live_call_matches_the_training_row_at_the_feed_head(v3):
    """The row a shard stores is exactly what a live caller gets from the plays the feed has shown so far."""
    summary = _game()
    rows = _rows("nba", summary)
    timed = V.flat_plays("nba", V.wall_ordered("nba", summary))
    for i in (15, 60, 137, len(rows) - 1):
        r = rows.iloc[i]
        got = G.history_now("nba", [(t, p) for t, p in timed if t <= float(r.feed_cut_t)])
        for f in G.HISTORY_FEATURES:
            assert float(np.float32(got[f])) == pytest.approx(float(r[f]), abs=1e-3), f


def test_live_call_needs_neither_pandas_nor_a_future_play():
    code = "import sys, redzone.noggin.gamevalue as G; print(G.history_now('nba', [(0.0, {'type': {'text': 'Timeout'}})])['timeouts_period'], 'pandas' in sys.modules)"
    got = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=Path(__file__).resolve().parent.parent, timeout=60)
    assert got.returncode == 0 and got.stdout.split() == ["1.0", "False"], got.stderr


# Planted futures, one per feature family: (family, what to plant at play 200 on, the features the family owns)
FAMILIES = {
    "scoring": (lambda p: p.update(homeScore=p["homeScore"] + 30), ("pts_last3", "pts_last5", "swing_last5", "run_pts", "run_by_trailing")),
    "turnovers_fouls": (lambda p: p.update(type={"text": "Lost Ball Turnover"}), ("to_5m", "fouls_5m")),
    "pace": (lambda p: p.update(wallclock=_iso(float(p["id"]) * 60 + 0.5)), ("plays_2m", "sec_per_play_10")),
    "timeouts": (lambda p: p.update(type={"text": "Timeout"}), ("timeouts_period", "since_timeout_s", "timeout_recent")),
}


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_a_future_event_never_moves_an_earlier_row(v3, family):
    plant, owned = FAMILIES[family]
    clean, future = _game(), _game()
    for p in future["plays"][200:]:
        plant(p)
    before, after = _rows("nba", clean), _rows("nba", future)
    cut = T0.timestamp() + 200 * 60
    a, b = before[before.feed_head_t < cut], after[after.feed_head_t < cut]
    assert len(a) == len(b) and len(a) > 50
    for f in owned:
        assert np.array_equal(a[f].to_numpy(), b[f].to_numpy()), f


@pytest.mark.parametrize("family", sorted(FAMILIES))
def test_a_builder_that_peeks_is_caught_by_the_cut_feed_rebuild(v3, family):
    _, owned = FAMILIES[family]
    summary = _game()
    def cheating(view, head, decision):
        got = V.features(view, head, decision)
        for f in owned:                       # the value at the last play of the game, whatever the row's head was
            got[f] = np.full(len(head), float(G.history_features(view.history, np.array([len(view.walls) - 1]))[f][0]) + 1.0)
        return got
    rows = _rows("nba", summary, feature_fn=cheating)
    _, bad = V.prefix_check("nba", "g1", summary, rows, n=40, feature_fn=cheating)
    assert bad, f"{family}: a future-reading builder passed the rebuild"
    assert any(any(f in line for f in owned) for line in bad)
