"""A red-zone trip must not depend on plays the feed has not shown yet.

Why: the Kaggle leak check failed on 2003_02_SEA_ARI row 0 and 2004_05_OAK_IND row 142 (t2_5m 0.0 from the full game, 1.0 from the feed).
The rz_trip detector fires once per drive on the first qualifying snap it walks, and a drive's plays are in play_id order. When a later snap
carries an earlier wall stamp, a cut feed fires on it while the full game has already fired on the earlier snap. These games are the nflverse
adapter's shapes (scripts/holdset_public.nfl_summary), with the stamps the bad rows imply.
"""
import numpy as np
import pytest

from redzone.moments.detect import detect, parse_ts
from redzone.noggin import value_shards as V
from scripts import holdset_public as HP
from tests.test_value_shards_v4 import _iso, _raw


@pytest.fixture(params=["v2", "v3", "v4"])
def version(request):
    was = V.FEATURES_VERSION
    V.select_features(request.param)
    yield request.param
    V.select_features(was)


def _frame():
    """The scripted nflverse game, one drive per kickoff (the shared fixture has them all in drive 1)."""
    frame = _raw()
    frame["drive"] = (frame.play_type == "kickoff").cumsum()
    return frame


def _rz_snaps(frame, n=2):
    """Row positions of the first n red-zone snaps of a mid-game drive: feed order is play_id order."""
    drive = int(frame[frame.yardline_100.le(20) & frame.down.notna()].drive.iloc[8])
    snaps = frame[(frame.drive == drive) & frame.yardline_100.le(20) & frame.down.notna()]
    return list(snaps.index[:n])


def _misstamped_before_kickoff():
    """2003_02_SEA_ARI's shape: the drive's second red-zone snap carries a stamp older than every other play, so it is row 0."""
    frame = _frame()
    _, second = _rz_snaps(frame)
    frame.loc[second, "time_of_day"] = _iso(-120.0)
    return frame


def _two_snaps_swapped():
    """2004_05_OAK_IND's shape: mid-game, the drive's first red-zone snap is stamped after the next one."""
    frame = _frame()
    first, second = _rz_snaps(frame)
    a, b = frame.loc[first, "time_of_day"], frame.loc[second, "time_of_day"]
    frame.loc[first, "time_of_day"], frame.loc[second, "time_of_day"] = b, a
    return frame


def _rows(frame):
    return V.build_rows("nfl", frame.game_id.iloc[0], HP.nfl_summary(frame), night="20231008", season=2023, source="nflverse_pbp",
                        license_line="x", plan="A")


@pytest.mark.parametrize("make", [_misstamped_before_kickoff, _two_snaps_swapped], ids=["row_0", "mid_game"])
def test_every_row_matches_the_cut_feed_when_a_drive_has_stamps_out_of_order(make, version):
    frame = make()
    rows = _rows(frame)
    assert V.prefix_check("nfl", frame.game_id.iloc[0], HP.nfl_summary(frame), rows, n=len(rows))[1] == []


def test_the_misstamped_first_row_counts_the_trip_it_can_see(version):
    frame = _misstamped_before_kickoff()
    rows = _rows(frame).sort_values("feed_cut_t")
    assert rows.t2_5m.iloc[0] == 1.0 and rows.value_10m.iloc[0] == 2.0


def test_the_labels_still_use_the_whole_game():
    frame = _misstamped_before_kickoff()
    gid, summary = frame.game_id.iloc[0], HP.nfl_summary(frame)
    rows = _rows(frame)
    hindsight = detect("nfl", V.strip_published_wp(summary), gid)
    tiers, t, cum = V._moment_arrays(hindsight.moments)
    d = rows.decision_t.to_numpy()
    assert np.allclose(rows.value_next_3m, V._sum_in(t, cum, d, d + V.HORIZON_S))
    assert np.array_equal(rows.p_tier1_60s, (V._count_in(tiers[1], d, d + V.TIER1_S) > 0).astype(np.float32))
    second = frame.loc[_rz_snaps(_frame())[1]]
    assert f"{gid}:{int(second.play_id)}" not in {m.play_id for m in hindsight.moments if m.kind == "rz_trip"}


def _snap(pid, wall, y0):
    return {"id": pid, "type": {"text": "Rush"}, "text": "", "period": {"number": 1}, "clock": {"displayValue": "10:00"}, "wallclock": wall,
            "statYardage": 1, "start": {"down": 1, "distance": 10, "yardsToEndzone": y0}, "end": {"yardsToEndzone": y0 - 1}}


def test_a_snap_with_no_wall_clock_cannot_use_up_the_drive():
    """ESPN's shape: nflverse drops an untimed play, an ESPN summary keeps it, and a cut feed never holds it."""
    summary = {"drives": {"previous": [{"plays": [_snap("a", None, 18), _snap("b", _iso(30), 15)]}]}}
    assert [m.play_id for m in V.feature_detect("nfl", summary, "g").moments if m.kind == "rz_trip"] == ["b"]


def test_a_drive_in_wall_order_keeps_the_same_moments():
    summary = HP.nfl_summary(_frame())
    assert V.feature_detect("nfl", summary, "g").moments == detect("nfl", summary, "g").moments


def test_only_football_is_reordered_here():
    plays = [{"id": str(i), "type": {"text": "Jumper"}, "wallclock": _iso(100 - i), "period": {"number": 4}, "clock": {"displayValue": "1:00"}, "scoringPlay": True,
              "shootingPlay": True, "homeScore": 2 * (i + 1), "awayScore": 0} for i in range(3)]
    summary = {"plays": plays}
    assert V.feature_detect("nba", summary, "g").moments == detect("nba", summary, "g").moments
    assert parse_ts(plays[0]["wallclock"]) > parse_ts(plays[1]["wallclock"])
