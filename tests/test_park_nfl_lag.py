"""PARK-NFL-LAG on fake sticks: a late football feed's game is parked deeper (`park.feed_lag.*`, off), the hidden Prime
stick's own play clock gates a parked football frame (`park.prime.bug_gate`, off), and the play-clock parser.

The play clock's behaviour is the watcher's (Nov 11, a Thursday-night replay on Prime, small sample): 40 from the snap
through the play, counting down from the dead ball, readable on 76% of frames. Prime's live TNF bug layout is UNKNOWN
until live. Fake sticks: not measured on a TV.

Why: with football's real feed lag (median 44 s) the show almost never joined the football game.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT.as_posix(), (ROOT / "scripts").as_posix()):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import park_replay as pr  # noqa: E402
from redzone import config  # noqa: E402
from redzone.noggin import park_prime  # noqa: E402
from redzone.park_run import feed_lags  # noqa: E402
from redzone.screen.scorebug import Bug  # noqa: E402
from test_park_prime import E_PRIME, GTI, pworld  # noqa: E402,F401

needs_archive = pytest.mark.skipif(not (ROOT / "data/archive/nfl").exists(), reason="pinned eval data not fetched (scripts/fetch_eval_data.py)")


def box(text, x, y=0.85, h=0.03, conf=0.9):
    return SimpleNamespace(text=text, x=x, y=y, width=0.03, height=h, confidence=conf)


BUG = Bug(21, 17, "3RD", "12:34", "3&7")


def test_the_play_clock_is_read_and_scores_clock_and_distance_are_not_taken_for_it():
    obs = [box("NYG", 0.1), box("21", 0.15, h=0.05), box("WSH", 0.3), box("17", 0.35, h=0.05), box("3RD", 0.5),
           box("12:34", 0.55), box("3RD &", 0.62), box("7", 0.655), box(":25", 0.75)]
    assert park_prime.play_clock(obs, BUG) == 25
    obs40 = [o for o in obs if o.text != ":25"] + [box("40", 0.75)]
    assert park_prime.play_clock(obs40, BUG) == 40
    assert park_prime.play_clock([o for o in obs if o.text != ":25"], BUG) is None      # none shown
    assert park_prime.play_clock(obs + [box("9", 0.8)], BUG) is None                    # two candidates: ambiguous
    assert park_prime.play_clock(obs, None) is None
    assert park_prime.play_over(25) is True and park_prime.play_over(40) is False and park_prime.play_over(None) is None


def test_feed_lag_off_keeps_every_game_and_on_moves_only_listed_leagues():
    off = SimpleNamespace(get=lambda k, d=None: config.default(k) if d is None else (config.default(k) if config.default(k) is not None else d))
    assert config.default("park.feed_lag.enabled") is False
    assert feed_lags(off, ["nba", "nfl"], 8.0) == [8.0, 8.0]
    vals = {"park.feed_lag.enabled": True}
    on = SimpleNamespace(get=lambda k, d=None: vals.get(k, config.default(k) if config.default(k) is not None else d))
    lags = feed_lags(on, ["nba", "nfl", "cbb"], 8.0)
    assert lags[0] == 8.0 and lags[2] == 8.0                                  # basketball is not listed: unchanged
    assert lags[1] == pytest.approx(62.49)                                    # nfl q90 of the Nov 2-3 profile (n=150)


async def test_the_bug_gate_refuses_a_parked_football_frame_whose_play_clock_says_the_play_runs(pworld):  # noqa: F811
    w = pworld
    target = f"prime:{GTI}"
    reads = []

    async def pc(stick, home, away):
        reads.append(1)
        return w.pc_value
    w.hands.play_clock_reader = pc
    w.hands.football.add(target)
    assert await w.tv.sound_guard.check("session start") == "ok"
    assert (await w.c.park_tune(target)).ok
    w.pc_value = 40
    out = await w.c.park_pause(target, None)
    assert reads == []                                                        # the gate is off: never read
    assert out.ok
    assert (await w.c.park_resume(target)).ok
    w.c.cfg.update({"park": {"enabled": True, "prime": {"enabled": True, "bug_gate": True}}})
    out = await w.c.park_pause(target, None)
    assert not out.ok and "play clock says the play runs" in out.why and out.numbers["play_over"] is False
    assert w.b.ps == "playing" and not w.hands.place_of(w.b).paused           # never left frozen on a live frame
    w.pc_value = 23
    out = await w.c.park_pause(target, None)
    assert out.ok and out.numbers["play_clock"] == 23 and out.numbers["play_over"] is True and w.b.ps == "paused"
    w.hands.football.discard(target)                                          # basketball on Prime: no gate
    assert (await w.c.park_resume(target)).ok
    n = len(reads)
    assert (await w.c.park_pause(target, None)).ok and len(reads) == n


async def test_a_deep_prime_game_is_held_longer_by_its_depth(pworld):  # noqa: F811
    w = pworld
    target = f"prime:{GTI}"
    w.hands.depth_s[target] = 1.2
    assert await w.tv.sound_guard.check("session start") == "ok"
    out = await w.c.park_tune(target)
    assert out.ok and out.numbers["hold_s"] == pytest.approx(2.4 + 1.2, abs=0.6)
    assert w.b.behind() == pytest.approx(E_PRIME + 2.4 + 1.2, abs=0.6)


@needs_archive
def test_a_late_football_feed_is_parked_deeper_and_keeps_both_judges_at_zero():
    show = pr.replay("20261023", 1, minutes=30, walk="none", nfl=0, prime=1, nfl_lag=44.0, deep=True)
    g = len(show.replays) - 1
    assert show.depth == {g: pytest.approx(36.0)} and show.rig.depth is show.depth
    assert show.tunes[g][0] == pytest.approx(pr.prime_cost(show.P, show.rig.steps) + 36.0)
    fg, pic = pr.graded(show), pr.picture_grade(show)
    assert (fg["grader_entries_mid_play"], fg["grader_entries_mid_play_strict"], fg["grader_leaves_mid_play"]) == (0, 0, 0)
    assert (pic["football_joins_mid"], pic["football_leaves_mid"]) == (0, 0)


@needs_archive
def test_with_no_football_the_basketball_show_is_unchanged_by_the_lag_settings():
    a = pr.replay("20261023", 2, minutes=20, walk="none")
    b = pr.replay("20261023", 2, minutes=20, walk="none", deep=True, nfl_lag=44.0)      # no football game: no depth
    assert b.depth == {} or not b.depth
    assert a.records == b.records and a.jump_at == b.jump_at


# ---------------------------------------------------------------------------------------------- the football slider
def test_the_slider_curve_and_where_each_end_takes_the_anchor():
    from redzone.noggin.park_show import football_weight, slider_anchor
    assert [round(football_weight(s), 3) for s in (0, 25, 50, 75, 100)] == [0.25, 0.5, 1.0, 2.0, 4.0]
    fb, live, lev = [False, True, False], [True, True, True], [0.5, 0.2, 0.9]
    assert slider_anchor(50, -1, fb, live, lev) == -1 and slider_anchor(50, 2, fb, live, lev) == 2      # as before
    assert slider_anchor(89, -1, fb, live, lev) == -1 and slider_anchor(90, -1, fb, live, lev) == 1      # more football
    assert slider_anchor(100, 2, fb, live, lev) == 1                          # over a basketball crunch anchor too
    assert slider_anchor(10, 1, fb, live, lev) == 2 and slider_anchor(0, -1, fb, live, lev) == 2         # more everything else
    assert slider_anchor(0, 0, fb, live, lev) == 0                            # a basketball crunch anchor stands
    assert slider_anchor(95, -1, fb, [True, False, True], lev) == -1          # no live football: nothing to anchor


def test_the_slider_ships_at_50_and_50_means_off():
    from redzone.park_run import football_slider
    assert config.default("park.football_slider") == 50
    assert football_slider(SimpleNamespace(get=lambda k, d=None: 50)) is None
    assert football_slider(SimpleNamespace(get=lambda k, d=None: 80)) == 80.0
    assert football_slider(SimpleNamespace(get=lambda k, d=None: 140)) == 100.0


@needs_archive
def test_at_50_or_with_no_football_the_show_decides_exactly_as_without_the_slider():
    a = pr.replay("20261023", 1, minutes=20, walk="none", nfl=0)
    b = pr.replay("20261023", 1, minutes=20, walk="none", nfl=0, slider=50)
    assert a.records == b.records and a.jump_at == b.jump_at and b.slider == 50.0
    c = pr.replay("20261023", 2, minutes=20, walk="none")
    d = pr.replay("20261023", 2, minutes=20, walk="none", slider=100)
    assert d.slider is None and c.records == d.records                        # basketball only: the slider is not there


@needs_archive
@pytest.mark.parametrize("s", [0, 100])
def test_at_either_end_rule_1_and_warrants_hold(s):
    show = pr.replay("20261023", 1, minutes=30, walk="none", nfl=0, slider=s)
    fg, pic = pr.graded(show), pr.picture_grade(show)
    assert (fg["grader_entries_mid_play"], fg["grader_entries_mid_play_strict"], fg["grader_leaves_mid_play"]) == (0, 0, 0)
    assert (pic["football_joins_mid"], pic["football_leaves_mid"]) == (0, 0)
    row = pr.result(show)
    assert row["warranted"] == row["warrant_n"]


@needs_archive
def test_the_feed_lag_is_measured_from_each_rows_first_sight():
    """In the replay a row is handed over once the show's edge is `lead` short of its stamp, read every 3 s: its measured
    lag (first sight - stamp, on the show's grid) is -lead, less up to one poll."""
    show = pr.replay("20261023", 2, minutes=10)
    for g in range(2):
        lag, lead = show.feed.lag_s(g), show.feed.leads[g]
        assert lag is not None and -lead - 0.5 <= lag <= -lead + 3.5
