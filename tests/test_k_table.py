"""One reader for the per-game offset K, in all three file shapes (STACK-CLEAN, integration plan S6/S7).

K = YouTube-clock time of a frame minus the feed time of the game-clock second on it, one game, one night. The calibration
tools and the watcher write a table keyed by channel, timing.game_offset_file is keyed by game id, and the watcher's
K map is keyed "<channel>|<league>|<game id>". `noggin/k_table.k_rows` reads all three; `park_timing.k_for` and
`decider/tune_offset.GameOffsets` both read K through it. All rows here are made up.
"""
from __future__ import annotations

import json

import pytest

from redzone.decider import tune_offset as TO
from redzone.noggin import park_timing as pt

NIGHT = "2026-11-10"
GAME = "900000001"          # made-up feed ids
OTHER = "900000002"


def watcher_table(**extra):
    """The watcher's shape: keyed by channel, the row names its game."""
    return {"nbc": {"channel": "nbc", "league": "nba", "game": GAME, "K": 24.30, "spread": 0.40, "n": 6,
                    "source": "eyes", "date": NIGHT, "pm": 0.2, "wide": False, **extra}}


def games_file():
    return {"games": {GAME: {"night": NIGHT, "k_s": 24.30, "sd_s": 0.28, "spread_s": 0.50, "n": 5}}}


def eyes_map(date=NIGHT):
    row = {"k_s": 24.30, "spread_s": 0.60, "n": 5}
    return {f"nbc|nba|{GAME}": {**row, "date": date} if date else row}


def write(tmp_path, data, name="k.json"):
    p = tmp_path / name
    p.write_text(json.dumps(data))
    return p


def k_for(table, game=GAME, channel="nbc", league="nba", today=NIGHT, **kw):
    return pt.k_for(table, channel=channel, league=league, game_id=game, today=today, **kw)


# ------------------------------------------------------------------------------------------ the sign
def test_the_sign_of_k_a_picture_24_30_s_after_the_feed_stamp_is_plus_24_30(tmp_path):
    """A game whose picture shows each feed stamp 24.30 s later on YouTube's clock has K = +24.30 s, in both readers."""
    p = write(tmp_path, watcher_table())
    g = pt.GameTiming("nba", k_for(pt.read_k_table(p)))
    F = 1_000_000.0
    assert g.k_value == pytest.approx(24.30) and g.content_of_feed(F) == pytest.approx(F + 24.30)
    k = TO.GameOffsets.load(p).k(GAME, NIGHT)
    assert k.k_s == pytest.approx(24.30)
    # a stick 20 s behind YouTube's clock (U) shows feed second F at wall F + 20 + 24.30: 44.30 s behind the feed
    read = TO.TuneRead(20.0, 0.29, "label", 0.0)
    delay, _sd = TO.delay_sample(read, k)
    assert delay == pytest.approx(44.30)
    tune = pt.Tune.from_read(content_at=F + 24.30, position=500.0)   # the frame of feed F, read at position 500
    assert g.position_of_feed(F, tune) == pytest.approx(500.0)


# ------------------------------------------------------------------------------------------ one reader, three shapes
@pytest.mark.parametrize("data,shape", [(watcher_table(), "table"), (games_file(), "games"), (eyes_map(), "eyes")])
def test_each_shape_reads_to_the_same_k_in_both_readers(tmp_path, data, shape):
    rows = pt.k_rows(pt.read_k_table(write(tmp_path, data)))
    assert [(r.game, r.date, r.k, r.shape) for r in rows] == [(GAME, NIGHT, 24.30, shape)]
    assert k_for(data).k == pytest.approx(24.30)                          # park_timing (games shape: no channel, by id)
    assert TO.GameOffsets(data).k(GAME, NIGHT).k_s == pytest.approx(24.30)


def test_the_per_game_file_keeps_its_own_doubt_and_the_tables_spread_is_floored():
    assert TO.GameOffsets(games_file()).k(GAME, NIGHT).sd_s == pytest.approx(0.375, abs=0.001)   # hypot(0.28, 0.50 / 2)
    assert TO.GameOffsets(watcher_table(spread=0.2)).k(GAME, NIGHT).sd_s == 0.5                   # never tighter than 0.5
    assert k_for(watcher_table(spread=0.2)).spread == 0.5 and k_for(eyes_map()).spread == 0.6


def test_a_row_that_names_a_game_applies_to_that_game_only():
    for data in (watcher_table(), games_file(), eyes_map()):
        assert k_for(data, game=OTHER) is None                            # a later game on the same channel: unknown
        assert TO.GameOffsets(data).k(OTHER, NIGHT) is None
    assert k_for(games_file(), channel="espn").k == pytest.approx(24.30)  # the per-game file names no channel: by id only


def test_a_row_with_no_game_is_never_borrowed():
    table = {"nbc": {"channel": "nbc", "league": "nba", "K": 24.30, "spread": 0.4, "n": 6, "date": NIGHT}}
    assert TO.GameOffsets(table).rows == {} and TO.GameOffsets(table).k(GAME, NIGHT) is None
    assert k_for(table) is None and k_for(table, game=OTHER) is None
    assert k_for(table, unnamed_ok=True).k == pytest.approx(24.30)        # only on the operator's word that it is this game


def test_unknown_stays_wide_never_zero(tmp_path):
    for data in ({}, watcher_table(date="2026-11-09"), watcher_table(wide=True), watcher_table(K="x"),
                 {"games": {GAME: {"night": NIGHT, "k_s": None}}}, eyes_map(date=None)):
        assert k_for(data) is None and TO.GameOffsets(data).k(GAME, NIGHT) is None
        g = pt.GameTiming("nba", k_for(data))
        assert not g.known and g.k_value == pt.TIMING.prior_k != 0 and g.grid_padding() == 48
    assert pt.read_k_table(tmp_path / "missing.json") == {} and TO.GameOffsets.load(tmp_path / "missing.json").rows == {}
    assert TO.GameOffsets.load("").k(GAME, NIGHT) is None
    assert TO.delay_sample(TO.TuneRead(20.0, 0.29, "label", 0.0), None) is None     # no K: unknown, not U + 0


def test_the_live_eyes_map_needs_no_date_but_a_file_row_does():
    assert k_for({}, eyes_k=eyes_map(date=None)) == pt.GameK(24.30, 0.6, 5, "eyes")   # in memory, from the watcher now
    assert k_for(eyes_map(date=None)) is None                                         # in a file with no night: unknown


def test_a_file_with_all_three_shapes_at_once():
    data = {**watcher_table(), **games_file(), **{f"espn|cbb|{OTHER}": {"k_s": -2.80, "spread_s": 0.5, "date": NIGHT}}}
    assert {r.shape for r in pt.k_rows(data)} == {"table", "games", "eyes"}
    go = TO.GameOffsets(data)
    assert go.k(GAME, NIGHT).k_s == pytest.approx(24.30) and go.k(OTHER, NIGHT).k_s == pytest.approx(-2.80)
    assert k_for(data, game=OTHER, channel="espn", league="cbb").k == pytest.approx(-2.80)
