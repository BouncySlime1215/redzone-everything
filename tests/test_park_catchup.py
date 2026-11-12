"""The catch-up move in the PARK show (redzone/noggin/park_catchup.py, park.catchup.enabled off), replayed on recorded
dev nights with fake sticks (scripts/catchup_replay.py): off it is the show, decision for decision; on, every live join
keeps Rule #1 under the frozen grader, every clip starts at its play's start, nothing airs 3 minutes late, and a pinned
stick is never used.

Replayed with fake sticks is NOT measured on the TV.

Why: a catch-up takes the screen and both sticks for a minute or two; before the flag is turned on it has to prove it
changes nothing when off and never trades Rule #1 for a replay when on.
"""
from __future__ import annotations

import sys
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT.as_posix(), (ROOT / "scripts").as_posix()):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import catchup_replay as cr  # noqa: E402
import park_replay as pr  # noqa: E402
from redzone.noggin import catchup_pack as cp  # noqa: E402
from redzone.noggin.park_catchup import CatchupParams, CatchupShow, ShowSeen, plays_of  # noqa: E402

needs_archive = pytest.mark.skipif(not (ROOT / "data/archive/nba").exists(), reason="pinned eval data not fetched (scripts/fetch_eval_data.py)")


# ---------------------------------------------------------------------------------------------- what the package reads
def _fake_show(rows, walls, league="nba", n=400):
    x = SimpleNamespace(n=n, mom={}, v2={}, lev=[1.0] * n, dead=bytearray(n))
    fx = SimpleNamespace(plays=rows, wall=walls)
    return SimpleNamespace(feed=SimpleNamespace(fx=[fx], shifts=[0.0]), night=SimpleNamespace(base=0), A=[x], leagues=[league],
                           seen_to=[-1])


def test_plays_of_reads_scores_lead_changes_ties_runs_and_each_plays_start():
    rows = [{"homeScore": 0, "awayScore": 0, "text": "jump ball"},
            {"homeScore": 2, "awayScore": 0, "text": "home 2"},
            {"homeScore": 2, "awayScore": 0, "text": "rebound"},
            {"homeScore": 2, "awayScore": 3, "text": "away 3"},
            {"homeScore": 2, "awayScore": 3, "text": "assist row, same second"},
            {"homeScore": 5, "awayScore": 3, "text": "home 3"},
            {"homeScore": 8, "awayScore": 3, "text": "home 3 again"},
            {"homeScore": 8, "awayScore": 8, "text": "away and-one"}]
    walls = [100.0, 110.0, 120.0, 125.0, 125.0, 140.0, 190.0, 200.0]
    got = plays_of(_fake_show(rows, walls), 0, cp.PackParams())
    by = {p.text: p for p in got}
    assert list(by) == ["home 2", "away 3", "home 3", "home 3 again", "away and-one"]
    assert by["home 2"].lead_change and by["away 3"].lead_change and by["home 3"].lead_change
    assert not by["home 3 again"].lead_change and by["away and-one"].tie
    assert by["home 3 again"].run == 6 and by["away and-one"].run == 5
    assert by["away 3"].start == 120.0                                     # the rebound before it: where the play began
    assert by["home 3"].start == 125.0
    assert by["home 3 again"].start == 190.0 - 24.0                        # at most a shot clock back
    assert all(p.start <= p.at for p in got)


def test_the_seen_stub_answers_from_the_shows_own_seen_to():
    show = SimpleNamespace(seen_to=[-1, 812])
    seen = ShowSeen(show)
    assert seen.missed_since(0) is None and seen.missed_since(1) == 812.0


def test_params_come_from_park_catchup_keys_and_ship_off():
    assert CatchupParams().enabled is False
    conf = {"park.catchup.enabled": True, "park.catchup.min_gap_s": 200, "park.catchup.cap_s": 60}
    got = CatchupParams.from_config(lambda k, d=None: conf.get(k, d))
    assert got.enabled and got.min_gap_s == 200 and got.pack.cap_s == 60.0


# ---------------------------------------------------------------------------------------------- the replay
@needs_archive
def test_off_it_is_the_show_decision_for_decision():
    plain = pr.replay("20261023", 2, minutes=30)
    with cr.wired(CatchupParams(enabled=False)):
        off = pr.replay("20261023", 2, minutes=30)
    assert isinstance(off, CatchupShow) and not off.cu_ok
    assert off.rig.sent == plain.rig.sent
    assert pr.result(off) == pr.result(plain)


@needs_archive
def test_on_a_rig_without_the_cue_step_nothing_starts():
    """The app's ControllerRig has no `cu_cue` yet: the flag on changes nothing there (fail closed)."""
    plain = pr.replay("20261023", 2, minutes=30)
    old = pr.Show
    pr.Show = partial(CatchupShow, catchup=CatchupParams(enabled=True))
    try:
        on = pr.replay("20261023", 2, minutes=30)
    finally:
        pr.Show = old
    assert not on.cu_ok and on.cu_log == [] and on.rig.sent == plain.rig.sent


@needs_archive
def test_catchups_keep_rule_1_and_queue_every_clip_to_its_plays_start():
    show, c = cr.run("20261028", 4, "proven", minutes=60)
    m = cr.metrics(show, c)
    assert m["done"] >= 1
    assert (m["grader_entries_mid_play"], m["grader_leaves_mid_play"]) == (0, 0)          # every cut, frozen grader
    assert (m["mid_entry"], m["mid_leave"], m["hl_leave"]) == (0, 0, 0)                   # the replay's own judge
    assert (m["cu_join_mid"], m["cu_join_mid_strict"], m["cu_leave_mid"]) == (0, 0, 0)  # the catch-up cuts alone
    assert m["clip_starts"]["mid"] == 0 and m["clips_late"] == 0
    assert m["behind_max_s"] <= 145 + 1
    recs = [r for r in show.records if r["reason"] == "catch-up"]
    assert len(recs) == m["done"] and all(r["warrant"]["ok"] for r in recs)
    for r in show.cu_log:
        assert 1 <= len(r["plays"]) <= cp.PackParams().max_plays
        assert all(cp.age_ok(cl.lo, cl.air, c.pack, r["n_planned"]) for cl in r["clips"])   # its age cap, as aired
        assert all(r["n_planned"] >= 2 or cp.age_s(cl.lo, cl.air, c.pack) < 180.0 for cl in r["clips"])


@needs_archive
def test_a_pinned_stick_is_never_sent_back_or_re_tuned_by_a_catchup():
    """The pinned Prime football stream keeps its game through a catch-up (it plays on unseen, as when a jump leaves
    it); the clips and the live join are all on the other stick ("h_join"); a catch-up never cues the pinned one."""
    show, c = cr.run("20261023", 1, "proven", minutes=150, pin=True, **cr.NFL_PRIME)
    off = pr.replay("20261023", 1, minutes=150, pin=True, **cr.NFL_PRIME)
    assert show.keep == 0
    assert not any(x[0] == "cue" and x[1] == 0 for x in show.rig.sent)
    assert sum(1 for x in show.rig.sent if x[0] == "tune" and x[1] == 0) <= sum(1 for x in off.rig.sent if x[0] == "tune" and x[1] == 0)
    assert all(r["h"] == 1 and r["rejoined"] for r in show.cu_log)
    m = cr.metrics(show, c)
    assert (m["grader_entries_mid_play"], m["grader_leaves_mid_play"], m["cu_join_mid"]) == (0, 0, 0)
    assert m["behind_max_s"] <= 145 + 1


@needs_archive
def test_football_is_left_for_a_catchup_only_in_a_break_that_covers_it():
    """Football first: the NBA game's catch-up never takes the screen from football between the plays of a drive."""
    show, c = cr.run("20261023", 1, "library", minutes=80, **cr.NFL_PRIME)
    off = pr.replay("20261023", 1, minutes=80, **cr.NFL_PRIME)
    fb = next(g for g, f in enumerate(show.football) if f)
    left_fb = [r for r in show.cu_log if r["from"] == fb]
    assert left_fb
    for r in left_fb:
        assert show.A[fb].brk[r["i"]]
    m, m0 = cr.metrics(show, c), cr.metrics(off, None)
    assert m["big_n"] - m["big_live"] - m["big_via"] <= m0["big_n"] - m0["big_live"]
    assert (m["grader_entries_mid_play"], m["grader_leaves_mid_play"], m["cu_join_mid"]) == (0, 0, 0)
