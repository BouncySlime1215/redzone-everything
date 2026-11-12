"""The seen-state (redzone/noggin/seen.py): what the viewer has seen of each game, what the feed reported since, the
staleness that grows faster close and late, the big plays missed (the catch-up engine's interface) and the templated
recap line. Pure functions on hand-made rows, then the tracker inside a replayed show (fake sticks, no device).

Why: phase 2C's base ("what you have seen"); the director, the catch-up engine and the Show tab all read it, so every
fact it hands on is pinned here.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from redzone.noggin import seen as sn

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
needs_archive = pytest.mark.skipif(not (ROOT / "data/archive/nba").exists(), reason="pinned eval data not fetched (scripts/fetch_eval_data.py)")
T0 = 1_800_000_000


def row(t, away, home, period=1, clock="10:00", text="", scoring=False):
    """One ESPN-shaped play row, `t` seconds after T0."""
    return {"wallclock": datetime.fromtimestamp(T0 + t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "awayScore": away,
            "homeScore": home, "period": {"number": period}, "clock": {"displayValue": clock}, "text": text, "scoringPlay": scoring}


def feed(*rows):
    """(rows, the stamps the show uses): each row's own wallclock."""
    return list(rows), [datetime.strptime(r["wallclock"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() for r in rows]


# ---------------------------------------------------------------------------------------------- the feed's facts
def test_the_situation_then_is_the_last_row_at_or_before_with_the_last_score_reported():
    rows, st = feed(row(0, 0, 0, clock="12:00"), row(10, 2, 0, clock="11:40", scoring=True), row(20, None, None, clock="11:20"))
    assert sn.snap_at(rows, st, T0 - 1) == sn.Snap()
    s = sn.snap_at(rows, st, T0 + 25)
    assert (s.period, s.clock, s.away, s.home, s.stamp) == (1, "11:20", 2, 0, T0 + 20)   # a row with no score keeps the last one


def test_scores_since_start_from_what_the_feed_had_and_list_each_change():
    rows, st = feed(row(0, 0, 0), row(10, 0, 2), row(20, 3, 2), row(30, 3, 2), row(40, 3, 4))
    ch = sn.scores(rows, st, T0 + 10, T0 + 40)
    assert [(c.away, c.home, c.away_pts, c.home_pts) for c in ch] == [(3, 2, 3, 0), (3, 4, 0, 2)]
    assert sn.scores(rows, st, T0 + 40, T0 + 99) == []


def test_lead_changes_count_the_lead_passing_sides_not_ties():
    ch = [sn.Score(0, 2, 0, 2, 0), sn.Score(1, 2, 2, 0, 2), sn.Score(2, 2, 4, 0, 2), sn.Score(3, 5, 4, 3, 0), sn.Score(4, 5, 5, 0, 1)]
    assert sn.lead_changes((0, 0), ch) == 2                    # away leads, a tie, home leads (1), away leads (2), a tie
    assert sn.lead_changes((10, 0), ch[3:]) == 0


def test_the_best_run_is_the_largest_stretch_for_one_side_and_starts_with_its_own_score():
    ch = [sn.Score(0, 0, 2, 0, 2), sn.Score(1, 2, 2, 2, 0), sn.Score(2, 5, 2, 3, 0), sn.Score(3, 5, 4, 0, 2),
          sn.Score(4, 8, 4, 3, 0), sn.Score(5, 10, 4, 2, 0), sn.Score(6, 10, 6, 0, 2)]
    assert sn.best_run(ch) == ("away", 10, 2)                  # 2 + 3 + 3 + 2 for away, 2 for home inside it
    assert sn.best_run([]) is None


def test_a_big_play_is_seen_with_the_replay_judges_own_lead_in():
    from redzone.noggin.park_core import PARK_PARAMS
    assert sn.SEEN.lead_in == PARK_PARAMS.seen_lead_s
    assert sn.seen_at(20, [[10, 30]], 7) and not sn.seen_at(15, [[10, 30]], 7) and not sn.seen_at(31, [[10, 30]], 7)


def test_staleness_grows_with_leverage_a_dead_ball_adds_a_quarter_and_a_break_nothing():
    assert sn.stale_step(True, False, 1.0) == 1.0
    assert sn.stale_step(True, False, 3.0) == 3.0              # close and late: three times as fast
    assert sn.stale_step(False, False, 2.0) == pytest.approx(0.5)
    assert sn.stale_step(False, True, 3.0) == 0.0


# ---------------------------------------------------------------------------------------------- the recap line (a template)
def test_period_and_score_texts():
    assert sn.period_text(2, "5:32") == "2Q 5:32" and sn.period_text(2, "5:32", halves=True) == "2H 5:32"
    assert sn.period_text(5, "1:02") == "OT 1:02" and sn.period_text(6, None) == "2OT" and sn.period_text(3, None, halves=True) == "OT"
    assert sn.period_text(None, "1:00") == ""
    assert sn.score_text("LAC", "LAL", 31, 25) == "LAC 31-25" and sn.score_text("LAC", "LAL", 25, 31) == "LAL 31-25"
    assert sn.score_text("LAC", "LAL", 25, 25) == "tied 25-25"
    assert sn.sides("LAC @ LAL") == ("LAC", "LAL") and sn.sides("game 1") == ("away", "home")


def test_the_recap_line_says_only_the_feeds_facts_in_a_fixed_template():
    then = sn.Snap(2, "5:32", 40, 45)
    run = [sn.Score(0, 42, 45, 2, 0), sn.Score(1, 45, 45, 3, 0), sn.Score(2, 45, 47, 0, 2), sn.Score(3, 48, 47, 3, 0),
           sn.Score(4, 52, 49, 4, 2)]
    assert sn.recap_line("BOS", "NY", then, run) == "Last time: NY 45-40, 2Q 5:32. Since: BOS 12-4 run, 1 lead change"
    assert sn.recap_line("BOS", "NY", then, [sn.Score(0, 42, 45, 2, 0), sn.Score(1, 42, 47, 0, 2)]) == \
        "Last time: NY 45-40, 2Q 5:32. Since: even 2-2"
    assert sn.recap_line("BOS", "NY", then, [sn.Score(0, 40, 48, 0, 3)], missed=2) == \
        "Last time: NY 45-40, 2Q 5:32. Since: NY 3-0, 2 big plays missed"
    assert sn.recap_line("BOS", "NY", then, [], missed=1) == "Last time: NY 45-40, 2Q 5:32. Since: no score, 1 big play missed"
    assert sn.recap_line("BOS", "NY", sn.Snap(), run) == ""


# ---------------------------------------------------------------------------------------------- the tracker
def arr(n=100, a0=0, a1=99, lev=1.0, dead=(), brk=(), mom=None):
    d, b = bytearray(n), bytearray(n)
    for i in dead:
        d[i] = 1
    for i in brk:
        d[i] = b[i] = 1
    return SimpleNamespace(n=n, a0=a0, a1=a1, dead=d, brk=b, lev=[lev] * n if isinstance(lev, float) else list(lev), mom=dict(mom or {}))


def tracker(rows_by_game, n=2, shift=0.0):
    return sn.SeenTracker(n, T0, [shift] * n, lambda g: rows_by_game[g])


def test_a_game_unseen_goes_stale_faster_when_close_and_late_and_resets_when_it_is_on():
    quiet, close = arr(lev=1.0), arr(lev=3.0)
    tr = tracker({0: ([], []), 1: ([], [])})
    for t in range(10):
        tr.tick(t, 0, t, [quiet, close])
    assert tr.staleness(0) == 0.0 and tr.staleness(1) == pytest.approx(30.0) and tr.last_seen_s(1) is None
    tr2 = tracker({0: ([], []), 1: ([], [])})
    for t in range(10):
        tr2.tick(t, 0, t, [quiet, quiet])
    assert tr2.staleness(1) == pytest.approx(10.0)            # the same ten seconds: a third as stale
    tr.tick(10, 1, 10, [quiet, close])
    assert tr.staleness(1) == 0.0 and tr.last_seen_s(1) == 0 and tr.last_seen_s(0) == 1 and tr.staleness(0) == 1.0
    assert tr.worst() == 10 and tr.g[1].shown == [[10, 10]]


def test_a_flip_second_is_neither_seen_nor_unseen_and_a_game_not_in_progress_never_goes_stale():
    later = arr(a0=50)
    tr = tracker({0: ([], []), 1: ([], [])})
    tr.tick(0, 0, 0, [arr(), later])
    tr.tick(1, 0, None, [arr(), later])                       # the picture is changing
    assert tr.g[0].shown == [[0, 0]] and tr.last_seen_s(0) == 1 and tr.staleness(1) == 0.0 and tr.g[1].unseen_s == 0


def test_looks_are_kept_as_the_seconds_shown_and_a_return_behind_starts_a_new_look():
    tr = tracker({0: ([], []), 1: ([], [])})
    for t, p in ((0, 0), (1, 1), (2, 2), (3, 2), (4, 3), (5, 40), (6, 41)):
        tr.tick(t, 0, p, [arr(), arr()])
    assert tr.g[0].shown == [[0, 3], [40, 41]]


def test_missed_since_returns_the_big_plays_not_seen_with_their_feed_stamps_and_leverage():
    """The catch-up engine's interface: big plays (park_core.Arr.mom) after the last second on screen, up to the show's
    edge, not on screen with their lead-in; with the feed stamp, the row's text and the leverage there."""
    rows, st = feed(row(5, 0, 0), row(30, 0, 3, period=2, clock="7:10", text="Tatum 26-foot three point jumper", scoring=True),
                    row(30, 0, 3, text="Timeout"), row(60, 2, 3, period=2, clock="6:20", text="Brown layup", scoring=True))
    g1 = arr(mom={30: 5.0, 60: 5.0, 90: 1.0}, lev=[1.0] * 60 + [2.5] * 40)
    tr = tracker({0: ([], []), 1: (rows, st)}, shift=0.0)
    for t in range(12, 20):                                    # game 1 on screen at 12..19 (the play at 30 is not in it)
        tr.tick(t, 1, t, [arr(), g1])
    for t in range(20, 70):
        tr.tick(t, 0, t, [arr(), g1])
    out = tr.missed_since(1)
    assert [(m.game, m.at, m.stamp, m.value, m.period, m.clock) for m in out] == [(1, 30, T0 + 30.0, 5.0, 2, "7:10"), (1, 60, T0 + 60.0, 5.0, 2, "6:20")]
    assert out[0].text == "Tatum 26-foot three point jumper" and out[1].leverage == 2.5    # the scoring row, not the timeout
    assert tr.missed_since(1, since=40) == out[1:]
    for t in range(70, 100):                                   # back on game 1 from second 70: the play at 90 is seen
        tr.tick(t, 1, t, [arr(), g1])
    assert [m.at for m in tr.missed_since(1, since=0)] == [30, 60]
    assert tr.missed_since(1) == []


def test_since_and_the_recap_come_from_the_rows_between_the_last_second_seen_and_the_show_edge():
    rows, st = feed(row(0, 30, 33, period=2, clock="6:00"), row(20, 31, 33, clock="5:40"), row(40, 35, 33), row(60, 38, 35),
                    row(80, 42, 35))
    tr = tracker({0: ([], []), 1: (rows, st)})
    tr.tick(10, 1, 10, [arr(), arr()])
    for t in range(11, 90):
        tr.tick(t, 0, t, [arr(), arr()])
    s = tr.since(1)
    assert (s.then.away, s.then.home, s.now.away, s.now.home, s.lead_changes, s.run) == (30, 33, 42, 35, 1, ("away", 12, 2))
    assert tr.recap(1, "BOS @ NY") == "Last time: NY 33-30, 2Q 6:00. Since: BOS 12-2 run, 1 lead change"
    v = tr.view(1, "BOS @ NY")
    assert v["last_seen_s"] == 79 and v["missed"] == 0 and v["recap"].startswith("Last time: NY 33-30")
    assert tr.view(0, "A @ B")["recap"] is None and tr.since(0) is not None   # on screen now: no recap line


# ---------------------------------------------------------------------------------------------- inside the show (replay, fake sticks)
@needs_archive
def test_the_tracker_in_a_replayed_show_changes_no_decision_and_fills_the_show_tab(tmp_path):
    import json

    import park_replay as pr

    from redzone.showboard import Board
    from redzone.web import show as tab
    loaded = pr.load_replay("20261023", 3)
    plain = pr.replay("20261023", 3, loaded=loaded, minutes=40)
    board = Board(tmp_path)
    show = pr.replay("20261023", 3, loaded=loaded, minutes=40, seen=True, board=board)
    assert show.director is None and show.seen_state is not None and plain.seen_state is None
    assert show.rig.sent == plain.rig.sent and show.records == plain.records          # park.seen alone decides nothing
    st = show.seen_state
    assert st.worst() > 0 and any(st.last_seen_s(g) is not None for g in range(show.N))
    games = json.loads((tmp_path / "state.json").read_text())["games"]
    off = [g for g in games if not g["on_screen"] and g["last_seen_s"]]
    assert off and all(g["recap"] is None or g["recap"].startswith("Last time: ") for g in off)
    served = tab.build_state(tmp_path)["games"]
    assert all(set(g) >= {"last_seen_s", "stale", "missed", "recap"} for g in served)
    for g in range(show.N):                                    # every missed play is after the last second seen, not in a look
        for m in st.missed_since(g):
            assert not sn.seen_at(m.at, st.g[g].shown, st.p.lead_in) and m.value > 0
