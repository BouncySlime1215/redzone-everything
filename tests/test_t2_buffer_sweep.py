"""[T2 tester] Independent checks of the time-shift buffer replay sim and the "shown from the start" line
(lane 3 prereg /mnt/project-files/lane3/buffer-sweep/PREREG.md, grader proposal #316). Written by the T2
tester, not the builder: every expected value below is derived by hand from the play log, not read back
from the code under test.

What is pinned:
  1. restart_before: the moment the ball went live after the previous dead ball (NBA clock, NHL face-off,
     football snap), on hand-built logs with a known answer.
  2. from_start: tune time, a switch away inside the run, a rewind (cue) to the dead ball, and that it is
     never true when the play was not watched live (from start <= big live).
  3. feed_lag_knobs: the screen runs exactly stream delay + B behind real life for every B in the grid,
     whatever the feed lag; B never exceeds Nick's 90 s cap in the shipped config.
  4. Real nights (pinned eval data, dev split only, never locked): from start <= live on every card line,
     NFL from start == live, and restart_before never after a play's own start nor before the dead ball.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from redzone.config import Config
from redzone.grading import plen, restart_before
from redzone.lab.backtest import STREAM_DELAY_S, feed_lag_knobs, from_start, watched_whole
from redzone.models import League, Stoppage
from redzone.sources import espn

ROOT = Path(__file__).resolve().parent.parent
TUNE = 5.0


def _nba(kind, clock, text="", score=False, per=4):
    return {"type": {"text": kind}, "text": text or kind, "period": {"number": per},
            "clock": {"displayValue": clock}, "scoringPlay": score, "homeScore": 100, "awayScore": 99}


def _timeout_run():
    """Timeout at 2:00 (wall 100). Ball back live with 2:00 on the clock; the first logged event is a missed
    jumper at 1:50 (wall 160), so the clock started at 160 - 10 = 150. Rebound, then the big make at 1:40."""
    plays = [_nba("Full Timeout", "2:00"),
             _nba("Jump Shot", "1:50", "misses 18-foot jumper"),
             _nba("Defensive Rebound", "1:49"),
             _nba("Jump Shot", "1:40", "makes 3-pt jumper", score=True)]
    return SimpleNamespace(league=League.NBA, gid="g1", wall=[100.0, 160.0, 161.0, 170.0], plays=plays)


# ---- 1. restart_before on hand-built logs ------------------------------------------------------------

def test_nba_restart_is_where_the_clock_started_after_the_timeout():
    r = _timeout_run()
    assert espn.stoppage_for(League.NBA, r.plays[0]) == Stoppage.TIMEOUT
    assert restart_before(r.league, r.wall, r.plays, 3) == pytest.approx(150.0)
    # every play of that run shares the same restart
    assert restart_before(r.league, r.wall, r.plays, 1) == pytest.approx(150.0)
    assert restart_before(r.league, r.wall, r.plays, 2) == pytest.approx(150.0)


def test_nba_restart_after_a_make_is_the_make_when_the_clock_runs_on():
    """A made basket is a dead ball (inbound). The clock keeps running: 1:40 at wall 170, next event 1:25 at
    wall 185, so the ball was live again at 170."""
    r = _timeout_run()
    r.plays += [_nba("Jump Shot", "1:25", "misses layup"), _nba("Offensive Rebound", "1:24"),
                _nba("Dunk", "1:22", "makes dunk", score=True)]
    r.wall += [185.0, 186.0, 188.0]
    assert restart_before(r.league, r.wall, r.plays, 6) == pytest.approx(170.0)


def test_nba_restart_never_after_the_play_itself():
    r = _timeout_run()
    for i in range(len(r.plays)):
        assert restart_before(r.league, r.wall, r.plays, i) <= r.wall[i] - plen(r.league)


def test_nhl_restart_is_the_face_off():
    def p(kind, clock="10:00", score=False):
        return {"type": {"text": kind}, "text": kind, "period": {"number": 3}, "clock": {"displayValue": clock},
                "scoringPlay": score}
    plays = [p("Stoppage"), p("Face Off"), p("Shot"), p("Hit"), p("Goal", score=True)]
    wall = [100.0, 131.0, 140.0, 150.0, 160.0]
    assert espn.stoppage_for(League.NHL, plays[0]) == Stoppage.WHISTLE
    assert restart_before(League.NHL, wall, plays, 4) == pytest.approx(131.0)
    assert restart_before(League.NHL, wall, plays, 2) == pytest.approx(131.0)


def test_football_restart_is_the_snap_so_from_start_equals_live():
    plays = [{"type": {"text": "Rush"}, "text": "run for 3"},
             {"type": {"text": "Pass Reception"}, "text": "pass for 40"},
             {"type": {"text": "Passing Touchdown"}, "text": "TD", "scoringPlay": True}]
    wall = [10.0, 50.0, 90.0]
    for i in range(3):
        assert restart_before(League.NFL, wall, plays, i) == wall[i] - plen(League.NFL)


# ---- 2. from_start on hand-built watching logs -------------------------------------------------------

LAG = 26.0 + 60.0    # stream delay + a 60 s buffer: real life -> screen


def test_entry_in_the_dead_ball_counts_from_the_start():
    r = _timeout_run()
    w = [(120.0 + LAG, "g1")]          # tuned during the timeout: picture up at 125 + LAG, restart at 150
    assert from_start(w, r, 3, LAG, TUNE, 1e9)


def test_picture_up_after_the_restart_is_live_but_not_from_start():
    r = _timeout_run()
    w = [(146.0 + LAG, "g1")]          # picture up at 151 + LAG: the inbound at 150 was missed
    assert watched_whole(w, "g1", 170.0 + LAG, 170.0 + LAG, TUNE, 1e9)
    assert not from_start(w, r, 3, LAG, TUNE, 1e9)


def test_tune_exactly_ending_at_the_restart_counts():
    r = _timeout_run()
    w = [(150.0 - TUNE + LAG, "g1")]   # picture up exactly at the restart
    assert from_start(w, r, 3, LAG, TUNE, 1e9)


def test_switch_away_and_back_inside_the_run_breaks_it():
    r = _timeout_run()
    w = [(120.0 + LAG, "g1"), (155.0 + LAG, "g2"), (158.0 + LAG, "g1")]
    assert not from_start(w, r, 3, LAG, TUNE, 1e9)


def test_a_cue_back_to_the_dead_ball_counts_from_the_start():
    """[buffer as editor] Entry at real 172 + LAG that comes up 30 s behind its live edge shows real 142 +
    tune onward: the restart (150) and the big make (170) both aired on screen."""
    r = _timeout_run()
    w = [(100.0 + LAG, "g2"), (165.0 + LAG, "g1")]
    offs = [0.0, 30.0]
    assert from_start(w, r, 3, LAG, TUNE, 1e9, offs)
    # ...but a 10 s cue lands after the restart: live yes, from the start no
    offs = [0.0, 10.0]
    assert watched_whole(w, "g1", 170.0 + LAG, 170.0 + LAG, TUNE, 1e9, offs)
    assert not from_start(w, r, 3, LAG, TUNE, 1e9, offs)


@pytest.mark.parametrize("at", [100.0, 120.0, 140.0, 145.0, 146.0, 150.0, 160.0, 166.0, 171.0])
def test_from_start_implies_live(at):
    r = _timeout_run()
    w = [(at + LAG, "g1")]
    fs = from_start(w, r, 3, LAG, TUNE, 1e9)
    live = watched_whole(w, "g1", 170.0 + LAG, 170.0 + LAG, TUNE, 1e9)
    assert (not fs) or live
    assert fs == (at + TUNE <= 150.0)


def test_horizon_cuts_both_lines():
    r = _timeout_run()
    w = [(120.0 + LAG, "g1")]
    assert not from_start(w, r, 3, LAG, TUNE, 160.0 + LAG)


# ---- 3. the buffer knobs -----------------------------------------------------------------------------

@pytest.mark.parametrize("b", [0.0, 45.0, 60.0, 75.0, 90.0])
@pytest.mark.parametrize("fl", [None, 2.0, 8.0, 20.0])
def test_screen_runs_stream_delay_plus_buffer_whatever_the_feed_lag(b, fl):
    k = feed_lag_knobs(fl, b)
    assert k["feed_lag"] + k["delay"] == pytest.approx(STREAM_DELAY_S + b)
    assert k["calibrate_targets"] is True


def test_buffer_never_above_nicks_cap_in_config():
    cfg = Config(user_path=ROOT / "does-not-exist.yaml")
    tb = cfg.data["timing"]["behind_live_s"]
    assert float(tb["max_s"]) <= 90.0
    assert all(float(v) <= float(tb["max_s"]) for v in (tb.get("by_league") or {}).values())
    assert float(tb.get("default", 0)) <= float(tb["max_s"])


def test_knob_rejects_out_of_range():
    with pytest.raises(ValueError):
        feed_lag_knobs(None, -1.0)
    with pytest.raises(ValueError):
        feed_lag_knobs(None, 181.0)


# ---- 4. real nights (pinned dev data only) -----------------------------------------------------------

def _first_fit_night(league: League):
    import importlib.util
    spec = importlib.util.spec_from_file_location("eval_sports_t2", ROOT / "scripts/eval_sports.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for day, reps, targets in mod.nights(league, 1, None, "train"):   # train only: never valid-tuned, never locked
        return day, reps, targets
    return None


@pytest.mark.parametrize("league", [League.NBA, League.NHL, League.NFL])
def test_restart_before_brackets_every_play_on_a_real_night(league):
    if not (ROOT / "data/archive" / league.value).exists():
        pytest.skip("needs the pinned eval data (scripts/fetch_eval_data.py)")
    got = _first_fit_night(league)
    if got is None:
        pytest.skip("no train night")
    _, reps, _ = got
    for r in reps:
        for i in range(len(r.plays)):
            s = restart_before(r.league, r.wall, r.plays, i)
            assert s <= r.wall[i] - plen(r.league) + 1e-9
            # never before the dead ball it restarts from
            j = next((k for k in range(i - 1, -1, -1)
                      if espn.stoppage_for(r.league, r.plays[k]) != Stoppage.NONE or r.plays[k].get("scoringPlay")),
                     None)
            if j is not None and league != League.NFL:
                # basketball: never before the whistle; hockey: at most RESUME_LEAD (5 s) before it when no
                # face-off is logged (earlier = stricter, never lenient)
                slack = 5.0 + 1e-9 if league == League.NHL else 1e-9
                assert s >= min(r.wall[j], r.wall[i]) - slack


@pytest.mark.parametrize("league", [League.NBA, League.NHL, League.NFL])
def test_from_start_never_exceeds_live_on_a_real_night(league):
    if not (ROOT / "data/archive" / league.value).exists():
        pytest.skip("needs the pinned eval data (scripts/fetch_eval_data.py)")
    from redzone.lab import evaldata, sportsgrade
    from redzone.lab.backtest import run
    got = _first_fit_night(league)
    if got is None:
        pytest.skip("no train night")
    _, reps, targets = got
    pinned = ROOT / "data/eval_config.yaml"
    cfg = Config(user_path=pinned if pinned.exists() else ROOT / "data/config.yaml")
    evaldata.use_shipped_switch_costs(cfg)
    evaldata.flatten_league_overrides(cfg, league.value)
    mine = set(cfg.get(f"teams.{league.value}") or ["WSH"])
    rep = run(reps, cfg, mine, targets=targets, feed_lag_model="measured", **feed_lag_knobs(None, 60.0))
    card = sportsgrade.grade(reps, rep.switches, mine, rep.lag_true, 5.0, rep.t0, rep.t1, rep.replayed)
    assert card.big_from_start <= card.big_live
    assert card.crunch_from_start <= card.crunch_live
    assert card.my_crunch_from_start <= card.my_crunch_live
    if league == League.NFL:
        assert card.big_from_start == card.big_live
        assert card.crunch_from_start == card.crunch_live
