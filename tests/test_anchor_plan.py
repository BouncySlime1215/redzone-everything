"""[anchor.enabled] The anchor planner (redzone/noggin/anchor.py): the anchor pick, the gap budget on the real break table, the exact
fill and the late queue.

Why: anchor mode's one new way to break Rule #1 is a late return to the anchor; these pin that a plan never takes more than the
budget, and that the budget is the measured table's 10th percentile less the flips and the margin.
"""
import pytest

from redzone.noggin import anchor as A


class Cfg:
    def __init__(self, **v):
        self.v = {"timing.measured": True, **v}

    def get(self, k, d=None):
        return self.v.get(k, d)


ON = Cfg()
FLIP, PREP, TUNE = 2.2, 19.0, 4.3          # the measured timings (timing.measured on)
NFL_CLIP = 3.0 + 8.0 + 1.0                  # lead-in, the play, a second


def clip(i, value=5.0, seconds=NFL_CLIP, deadline=1e9, big=False):
    return A.Clip(f"c{i}", value, seconds, deadline, big)


# ----- the anchor --------------------------------------------------------------------------------------------------------------

def g(gid, **kw):
    base = dict(league="nfl", margin=3, left_s=100.0, crunch=True)
    return A.Game(gid, **{**base, **kw})


def test_only_crunch_or_overtime_games_are_eligible():
    assert A.pick_anchor([g("a", crunch=False)], None, ON) is None
    assert A.pick_anchor([g("a", crunch=False, overtime=True)], None, ON) == "a"
    assert A.pick_anchor([g("a", ended=True)], "a", ON) is None


def test_the_higher_leverage_game_is_the_anchor():
    close, wider = g("close", margin=3), g("wider", margin=7, left_s=200.0, crunch=True)
    assert A.anchor_score(close) == 3.0
    assert A.pick_anchor([wider, close], None, ON) == "close"


def test_a_challenger_needs_the_hysteresis_margin():
    cur = g("cur", league="nba", margin=8, left_s=100.0)          # hoops leverage 0.9 at 8 points, 2:00 to 1:00
    near = g("near", league="nba", margin=5, left_s=100.0)        # 2.0 / 0.9 = 2.2 x: takes it
    assert A.anchor_score(near) / A.anchor_score(cur) > 1.3
    assert A.pick_anchor([cur, near], "cur", ON) == "near"
    meh = g("meh", league="nba", margin=8, left_s=100.0, stakes=1.2)   # 1.2 x: not enough
    assert A.pick_anchor([cur, meh], "cur", ON) == "cur"
    assert A.pick_anchor([cur, meh], "cur", Cfg(**{"anchor.hysteresis": 1.1})) == "meh"


def test_your_team_first_whatever_the_score():
    yours = g("yours", margin=8, left_s=100.0, mine=True)
    other = g("other", overtime=True, margin=0, left_s=300.0)
    assert A.pick_anchor([yours, other], None, ON) == "yours"
    assert A.pick_anchor([yours, other], "other", ON) == "yours"     # yours takes it outright
    assert A.pick_anchor([yours, other], "yours", ON) == "yours"


def test_the_anchor_ends_when_it_leaves_crunch():
    assert A.pick_anchor([g("a", crunch=False), g("b")], "a", ON) == "b"


def test_a_league_value2_does_not_grade_reads_one_or_the_cap_in_overtime():
    assert A.anchor_score(g("h", league="nhl")) == 1.0
    assert A.anchor_score(g("h", league="nhl", overtime=True)) == 3.0


# ----- the budget, on the real break table ----------------------------------------------------------------------------------------

def test_nfl_timeout_q10_budget_holds_one_short_clip():
    b = A.gap_budget("nfl", "timeout", 0.0, ON)
    assert b == pytest.approx(35.0 - 2 * FLIP - 3.0)
    p = A.plan_gap(b, 0.0, 0.0, [clip(1), clip(2), clip(3)], (), ON)
    assert len(p.items) == 1 and p.away_s == pytest.approx(NFL_CLIP)
    assert p.back_at <= 35.0 - 3.0


def test_nfl_two_minute_warning_chains_clips():
    b = A.gap_budget("nfl", "two_minute_warning", 0.0, ON)
    assert b == pytest.approx(145.0 - 2 * FLIP - 3.0)
    p = A.plan_gap(b, 0.0, 0.0, [clip(i) for i in range(8)], (), ON)
    # first clip preloaded, then flip back while the stick loads the next (19 s): about 31 s a clip
    assert len(p.items) == 5 and p.away_s <= b
    assert all(b2 - e1 >= PREP for e1, b2 in zip(p.ends, p.starts[1:]))


def test_an_nfl_commercial_after_a_score_holds_three_clips():
    b = A.gap_budget("nfl", "score_break", 0.0, ON)
    assert len(A.plan_gap(b, 0.0, 0.0, [clip(i) for i in range(8)], (), ON).items) == 3       # spec section 4: "3 clips in 110 s"


def test_elapsed_time_and_delay_doubt_come_off_the_budget():
    full = A.gap_budget("nfl", "timeout", 0.0, ON)
    assert A.gap_budget("nfl", "timeout", 10.0, ON) == pytest.approx(full - 10.0)
    assert A.gap_budget("nfl", "timeout", 0.0, ON, sigma_s=2.0) == pytest.approx(full - 4.0)
    assert A.gap_budget("nfl", "timeout", 40.0, ON) == 0.0


@pytest.mark.parametrize("league,kind", [("nfl", "none"), ("nfl", "unknown"), ("nba", "whistle"), ("nhl", "whistle"), ("nfl", "halftime_x")])
def test_plain_dead_balls_and_whistles_have_no_budget(league, kind):
    assert A.gap_budget(league, kind, 0.0, ON) == 0.0


def test_flag_off_timing_uses_the_routes_existing_flip():
    off = Cfg(**{"timing.measured": False})
    assert A.costs(off).flip == 3.2 and A.costs(off).prep == 15.0
    assert A.gap_budget("nfl", "timeout", 0.0, off) == pytest.approx(35.0 - 6.4 - 3.0)


# ----- the fill ---------------------------------------------------------------------------------------------------------------

def test_the_knapsack_picks_by_value_under_the_budget():
    small, big = clip(1, value=3.0, seconds=10.0), clip(2, value=9.0, seconds=20.0)
    p = A.plan_gap(25.0, 0.0, 0.0, [small, big], (), ON)
    assert p.items == (big,)
    assert A.plan_gap(15.0, 0.0, 0.0, [small, big], (), ON).items == (small,)


def test_the_hidden_stick_must_be_ready():
    late_ready = A.plan_gap(27.6, 20.0, 0.0, [clip(1)], (), ON)          # still loading until 20: 20 + 12 > 2.2 + 27.6
    assert late_ready.items == ()
    assert A.plan_gap(27.6, 15.0, 0.0, [clip(1)], (), ON).starts == (15.0,)


def test_the_stick_busy_chain():
    """A visit costs a tune, then a clip needs a full load: the preloaded clip is lost if the visit goes first."""
    v = A.Visit("v", 6.0, snap_at=10.0, play_s=8.0)
    c = clip(1, value=5.0)
    p = A.plan_gap(60.0, 0.0, 0.0, [c], [v], ON)
    assert p.items == (v, c)                                              # both fit: the visit (bigger, timed) first
    assert p.starts[0] == pytest.approx(TUNE) and p.ends[0] == 18.0
    assert p.starts[1] == pytest.approx(18.0 + PREP)
    tight = A.plan_gap(30.0, 0.0, 0.0, [c], [v], ON)
    assert tight.items == (v,)                                            # no room left to load the clip after it


def test_a_visit_never_joins_after_its_snap():
    assert A.plan_gap(60.0, 0.0, 0.0, [], [A.Visit("v", 9.0, snap_at=3.0)], ON).items == ()   # the tune alone is 4.3 s


def test_a_clip_past_its_deadline_is_not_planned():
    assert A.plan_gap(60.0, 0.0, 0.0, [clip(1, deadline=1.0)], (), ON).items == ()


@pytest.mark.parametrize("budget", [0.0, 5.0, 11.9, 27.6, 60.0, 137.6, 300.0])
def test_a_plan_never_takes_more_than_the_budget(budget):
    q = [clip(i, value=float(i + 1), seconds=8.0 + i) for i in range(6)]
    p = A.plan_gap(budget, 100.0, 100.0, q, [A.Visit("v", 4.0, snap_at=112.0)], ON)
    assert p.away_s <= budget + 1e-9
    assert p.back_at <= 100.0 + 2 * FLIP + budget + 1e-9          # = now + guaranteed dead time - margin


def test_no_budget_no_plan():
    p = A.plan_gap(0.0, 0.0, 50.0, [clip(1)], (), ON)
    assert p.items == () and p.back_at == 50.0


# ----- the late queue ---------------------------------------------------------------------------------------------------------

def test_late_and_big_goes_last_small_past_180_is_dropped():
    hist = [float(v) for v in range(1, 101)]                              # top 5 percent: 96 and up
    on_time, big, small = clip(1, value=10.0, deadline=200.0), clip(2, value=97.0, deadline=50.0), clip(3, value=40.0, deadline=50.0)
    keep, last, dropped = A.late_queue([on_time, big, small], 100.0, hist, ON)
    assert keep == [on_time] and last == [big] and dropped == [small]


def test_with_no_history_nothing_is_big_unless_marked():
    late = clip(1, value=1e6, deadline=0.0)
    assert A.late_queue([late], 10.0, [], ON)[2] == [late]
    marked = clip(2, deadline=0.0, big=True)
    assert A.late_queue([marked], 10.0, [], ON)[1] == [marked]


def test_the_clip_deadline_is_the_180_s_cap():
    # a play that aired (ended) at t=100 with an 8 s play and a 3 s lead-in: its first second showed at 89
    assert A.clip_deadline(100.0, 8.0, 3.0, 175.0) == pytest.approx(89.0 + 175.0)
    assert A.clip_deadline(100.0, 8.0, 3.0, 500.0) == pytest.approx(89.0 + 179.0)


def test_the_flag_ships_off():
    import yaml
    from pathlib import Path
    cfg = yaml.safe_load((Path(__file__).resolve().parents[1] / "redzone" / "default_config.yaml").read_text())
    assert cfg["anchor"]["enabled"] is False
    assert {k: cfg["anchor"][k] for k in A.PRIORS} == A.PRIORS
