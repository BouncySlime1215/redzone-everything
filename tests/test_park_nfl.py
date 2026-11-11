"""PARK on football (PARK-NFL): football facts are the grader's own NFL dead time (each action row covers the 7 s before its
stamp, everything else dead, no shot window), never trusted past the play clock with no row to explain the wait, and the
show takes a football game only when `park.leagues` lists it (it ships [nba, cbb]: decisions identical).

Recorded NFL dev nights (2025 season); the mixed slate is an NFL night game time-shifted onto a recorded NBA night, so
it is NOT a recorded night. Fake sticks: not measured on a TV.

Why: Nick's scope is basketball plus Monday and Thursday Night Football, and PARK had no football at all.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT.as_posix(), (ROOT / "scripts").as_posix()):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import park_ceiling as pc  # noqa: E402
import park_replay as pr  # noqa: E402
from redzone import config  # noqa: E402
from redzone.grading import dead_at, is_action  # noqa: E402
from redzone.models import League  # noqa: E402
from redzone.noggin.park_facts import FACTS, GameFacts, is_football  # noqa: E402
from redzone.noggin.park_timing import GameK, GameTiming  # noqa: E402

needs_archive = pytest.mark.skipif(not (ROOT / "data/archive/nfl").exists(), reason="pinned eval data not fetched (scripts/fetch_eval_data.py)")


def _load(i: int):
    from redzone.lab import splits
    from redzone.lab.backtest import Replay
    d = ROOT / "data/archive/nfl"
    night = pr.nfl_nights()[i]
    return Replay.load(League.NFL, d / f"summary_{splits.archive_nights(d)[night][0]}.json")


def test_football_ships_off_and_basketball_is_unchanged():
    assert config.default("park.leagues") == ["nba", "cbb"]
    assert is_football("nfl") and is_football("cfb") and not is_football("nba")
    with pytest.raises(ValueError):
        GameFacts("nhl")
    assert FACTS.lead(League.NBA) == 26.0 and FACTS.lead(League.NFL) == 15.0
    tm = GameTiming("nfl", GameK(0.0, 0.5))
    assert tm.grid_padding(True) == 3                                         # Eyes: stamp vs snap sd 0.40 s (n=41, one replay)
    assert GameTiming("nba", GameK(0.0, 0.5)).grid_padding(True) == 3          # basketball as before


def _row(t, typ="Rush", text="run for 3", **kw):
    from datetime import datetime, timezone
    return {"wallclock": datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "type": {"text": typ},
            "text": text, "period": {"number": 2}, "clock": {"displayValue": "8:00"}, "homeScore": 7, "awayScore": 3, **kw}


def test_a_dead_ball_past_the_play_clock_with_nothing_to_explain_it_is_not_trusted():
    """The Nov 10 feed trap: a row stamped minutes late drags the next one forward and the gap reads as dead."""
    t0 = 1_800_000_000
    fx = GameFacts("nfl", "g")
    for t in (t0, t0 + 35, t0 + 70, t0 + 370, t0 + 405):                     # the 70 -> 370 gap: no row says why
        fx.add(_row(t))
    fx.set_final()
    f = fx.facts()
    dead = {s for a, b in f.dead for s in range(int(a), int(b))}
    assert t0 + 13 in dead and t0 + 27 in dead and t0 + 28 not in dead      # the grader: 7 s before each stamp is play
    assert t0 + 1 not in dead and t0 + 11 not in dead                         # picture model: the 12 s after a snap stamp
    assert t0 + 70 + 45 in dead and t0 + 70 + 46 not in dead and t0 + 300 not in dead   # past the play clock: not trusted
    ws, ps = fx.wall, fx.plays
    assert dead_at(League.NFL, ws, ps, t0 + 300)[0]                         # (the grader alone would call it dead)
    fx2 = GameFacts("nfl", "g")
    for t in (t0, t0 + 35, t0 + 70):
        fx2.add(_row(t))
    fx2.add(_row(t0 + 75, typ="Timeout", text="Timeout #1 by DAL"))          # a timeout explains the long wait
    for t in (t0 + 370, t0 + 405):
        fx2.add(_row(t))
    fx2.set_final()
    dead2 = {s for a, b in fx2.facts().dead for s in range(int(a), int(b))}
    assert t0 + 300 in dead2


def test_an_open_football_stoppage_is_trusted_to_the_edge_less_a_play_and_never_past_the_play_clock():
    t0 = 1_800_000_000
    fx = GameFacts("nfl", "g")
    for t in (t0, t0 + 35):
        fx.add(_row(t))
    o = fx._open(t0 + 60)
    assert o is not None and o.since == t0 + 35 + 12 and o.earliest_restart == t0 + 47    # dead only once its play is over
    o = fx._open(t0 + 80)
    assert o.earliest_restart == t0 + 80 - 15
    assert fx.clock_cap_at() == t0 + 35 + 45
    o = fx._open(t0 + 200)
    assert o.earliest_restart == t0 + 35 + 45                                 # capped by the play clock
    fx.add(_row(t0 + 40, typ="Timeout", text="Timeout #2"))
    assert fx.clock_cap_at() is None                                          # a timeout: no cap
    fx.add(_row(t0 + 160))
    assert fx._open(t0 + 190) is not None and fx._open(t0 + 190).since == t0 + 172
    assert fx.shots == []                                                     # no shot window in football


@needs_archive
@pytest.mark.parametrize("i", [0, 1])
def test_football_facts_are_the_graders_dead_time_less_only_the_play_clock_guard(i):
    from redzone.noggin.park_facts import FactsParams
    r = _load(i)
    fx = GameFacts("nfl", r.gid, FactsParams(play_after_s=0.0))           # the grader's rule alone (picture model off)
    cut = 9 ** 9
    for k, p in enumerate(r.plays):
        fx.add(p)
        if k == len(r.plays) // 2:                                            # half way: what is final now stays final
            mid = fx.facts()
            cut = mid.settled
    fx.set_final()
    f = fx.facts()
    truth = pc.truth_of(r)
    tdead = {s for a, b in truth["dead"] for s in range(int(a), int(b))}
    fdead = {s for a, b in f.dead for s in range(int(a), int(b))}
    assert fdead <= tdead                                                     # never dead where the grader says play
    lost = tdead - fdead
    assert len(lost) <= 0.05 * len(tdead)                                     # the guard trims a few percent only
    middead = {s for a, b in mid.dead for s in range(int(a), int(b)) if s < cut}
    assert middead <= fdead | {s for s in middead if s >= cut - 1}            # final then, final now
    acts = sum(1 for p in r.plays if is_action(League.NFL, p))
    assert acts > 100 and not f.shots
    pic = GameFacts("nfl", r.gid)                                             # picture model: 12 s (scrimmage) after each snap stamp is play
    for p in r.plays:
        pic.add(p)
    pic.set_final()
    pdead = {s for a, b in pic.facts().dead for s in range(int(a), int(b))}
    assert pdead <= fdead and len(fdead - pdead) >= 0.08 * len(fdead)       # it only removes, and it costs: 12% on game 0


@needs_archive
def test_a_football_game_on_a_basketball_night_keeps_rule_1_on_a_slice_and_reports_its_snaps():
    show = pr.replay("20261023", 1, minutes=30, walk="none", nfl=0)
    g = len(show.replays) - 1
    assert show.replays[g].league.base == League.NFL and show.tm.g[g].league == "nfl" and show.pad[g] >= 3
    fg = pr.graded(show)
    assert (fg["grader_entries_mid_play"], fg["grader_entries_mid_play_strict"], fg["grader_leaves_mid_play"]) == (0, 0, 0)
    row = pr.result(show)
    assert row["warranted"] == row["warrant_n"]
    snap = pr.snap_share(show, g)
    assert snap["plays"] > 0 and 0.0 <= snap["share"] <= 1.0


def test_special_teams_plays_keep_the_cautious_window_scrimmage_plays_the_measured_one():
    fx = GameFacts("nfl", "g")
    assert fx.play_after(_row(0, typ="Kickoff")) == 15.0 and fx.play_after(_row(0, typ="Field Goal Good")) == 15.0
    assert fx.play_after(_row(0, typ="Punt")) == 15.0 and fx.play_after(_row(0, typ="Rush")) == 12.0
    assert fx.play_after(_row(0, typ="Pass Reception")) == 12.0
