"""Thread-aware arrival: points for a game about to answer its question, how they're capped, and that they only know the past."""
import ast
import dataclasses as dc
from pathlib import Path

import pytest

from redzone.decider.decider import Candidate, Decider
from redzone.models import GameState, League, Phase, Situation, Team
from redzone.moments import arrival as A
from redzone.moments import threads as T
from redzone.noggin import show
from redzone.scoring.engine import Scored

LP = T.Params(enabled=True, reopen_guard_s=0.0)
AP = A.Params(enabled=True)
AIR, TUNE = 35.0, 5.0


def state(lg=League.NFL, *, gid="g", home=10, away=10, period=1, clock=600.0, phase=Phase.LIVE, sit=None, half=None):
    return GameState(lg, gid, Team("HOM", score=home), Team("AWY", score=away), phase=phase, period=period, clock_s=clock,
                     situation=sit or Situation(), half=half)


def fourth(**kw):
    return state(sit=Situation(possession="HOM", down=4, distance=2, yards_to_goal=30), **kw)


def rz(down=1, **kw):
    return state(sit=Situation(possession="HOM", down=down, distance=5, yards_to_goal=8), **kw)


def after_score():
    return state(sit=Situation(possession="AWY", down=1, distance=10, yards_to_goal=75), home=17)


def pp(**kw):
    return state(League.NHL, sit=Situation(power_play="HOM"), **kw)


def run(seq, *, ap=AP, on=False, air=AIR, tune=TUNE):
    """Feed (t, state) through the ledger and the book; the points of each tick in order."""
    led, book, out = T.Ledger(), A.Book(), []
    for now, g in seq:
        before = dict(led.open)
        led.tick({g.game_id: g}, g.game_id if on else None, now, LP, air)
        book.tick(before, led, {g.game_id: g}, now)
        out.append(A.arrival_points(book, led, g, now, air_s=air, tune_s=tune, on_screen=on, params=ap)[0])
    return out


# --- the estimate: how soon, from the state now ----------------------------------------------------------------------

def opened(g, now=0.0):
    led = T.Ledger()
    led.tick({g.game_id: g}, None, now, LP, AIR)
    return led.open[g.game_id]


def test_a_fourth_down_is_one_snap_away_and_runs_down_as_the_state_stands():
    t = opened(fourth())
    assert A.remaining(t, fourth(), 0.0, 0.0, AP) == AP.snap_s
    assert A.remaining(t, fourth(), 30.0, 30.0, AP) == AP.snap_s - 30.0
    assert A.remaining(t, fourth(), 60.0, 60.0, AP) == 0.0


def test_a_red_zone_trip_is_closer_on_a_later_down():
    t = opened(rz())
    assert A.remaining(t, rz(down=1), 0.0, 0.0, AP) > A.remaining(t, rz(down=3), 0.0, 0.0, AP) > 0.0


def test_a_power_play_always_reads_soon_until_it_runs_out():
    t = opened(pp())
    assert A.remaining(t, pp(), 0.0, 0.0, AP) == AP.hazard_s
    assert A.remaining(t, pp(), 110.0, 110.0, AP) == t.eta_s - 110.0
    assert A.remaining(t, pp(), 200.0, 200.0, AP) == 0.0


@pytest.mark.parametrize("lead,want", [(3.9, 0.0), (4.0, 1.0), (50.0, 1.0), (72.5, 0.5), (95.0, 0.0), (500.0, 0.0)])
def test_proximity_wants_a_play_or_two_of_lead(lead, want):
    assert A.proximity(lead, AP.min_lead_s, League.NFL, AP) == pytest.approx(want)


def test_each_sport_has_its_own_full_lead():
    assert AP.full_lead(League.CFB) == AP.full_lead_cfb_s and AP.full_lead(League.CBB) == AP.full_lead_cbb_s
    assert AP.full_lead(League.NHL) == AP.full_lead_nhl_s


# --- the points ----------------------------------------------------------------------------------------------------

def test_a_game_with_a_question_about_to_be_answered_earns_points():
    # a snap due in 8 s, 35 s of picture delay, 5 s to tune: 38 s of lead, inside the full window
    assert run([(0.0, fourth()), (30.0, fourth())])[1] == pytest.approx(AP.bonus_pts * opened(fourth()).stake)


def test_too_little_lead_earns_nothing():
    # with no picture delay, a question due now can't be reached in time
    assert run([(0.0, fourth()), (100.0, fourth())], air=0.0, tune=5.0)[1] == 0.0


def test_a_game_on_screen_has_no_lead_to_lose():
    assert run([(0.0, fourth())], air=0.0, tune=5.0, on=True)[0] > 0.0


def test_too_early_earns_nothing():
    far = state(sit=Situation(possession="HOM", down=1, distance=10, yards_to_goal=60), period=4, clock=400.0, home=7)
    assert run([(0.0, far)]) == [0.0]


def test_stakes_scale_the_points():
    close = run([(0.0, fourth(home=10, away=10))])[0]
    blowout = run([(0.0, fourth(home=40, away=10))])[0]
    assert close > blowout > 0.0


def test_a_run_never_earns():
    t = T.Thread("g:1", "g", League.NBA, "close_run", "q", 0.0, 90.0, 1.0, {})
    led, g = T.Ledger(open={"g": t}), state(League.NBA, period=3, clock=300.0)
    assert A.arrival_points(A.Book(), led, g, 0.0, air_s=AIR, tune_s=TUNE, on_screen=False, params=AP) == (0.0, None)


def test_off_means_no_points():
    assert run([(0.0, fourth())], ap=dc.replace(AP, enabled=False)) == [0.0]


def test_an_answer_the_feed_has_and_the_picture_has_not_earns_while_there_is_lead():
    scored = after_score()
    pts = run([(0.0, fourth()), (40.0, scored), (41.0, scored), (70.0, scored)])
    assert pts[1] > 0.0 and pts[2] > 0.0     # 35 s of air left, 5 s tune: still in front of it
    assert pts[3] == 0.0                     # it has aired


def test_an_answer_is_ignored_with_use_answered_off():
    pts = run([(0.0, fourth()), (40.0, after_score())], ap=dc.replace(AP, use_answered=False))
    assert pts[1] == 0.0


def test_an_answer_with_no_payoff_earns_nothing():
    over = state(League.NHL)
    pts = run([(0.0, pp()), (9.0, over), (20.0, over)])   # the power play ended with no goal
    assert pts[2] == 0.0


# --- the hindsight trap: only what the feed knew at the time --------------------------------------------------------

TIMES = [float(t) for t in range(0, 200, 5)]


def _future_a(t):
    return fourth(home=17) if t >= 100.0 else fourth()


def _future_b(t):
    return rz(down=2, home=10, away=31) if t >= 100.0 else fourth()


@pytest.mark.parametrize("a,b", [(_future_a, _future_b)])
def test_a_later_play_cannot_change_an_earlier_rank(a, b):
    """Two nights identical up to t=100, then different. Every point before that must match, and after it they must differ."""
    pa, pb = run([(t, a(t)) for t in TIMES]), run([(t, b(t)) for t in TIMES])
    cut = TIMES.index(100.0)
    assert pa[:cut] == pb[:cut]
    assert pa[cut:] != pb[cut:]


def test_arrival_imports_nothing_that_knows_the_night():
    """Nothing in this file can read the grader, the lab or a replay."""
    tree = ast.parse(Path("redzone/moments/arrival.py").read_text())
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | \
           {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert mods <= {"__future__", "dataclasses", "collections.abc", "redzone.models", "redzone.moments.threads"}, mods


# --- the guards the show must keep ----------------------------------------------------------------------------------

BANNED = {"bet", "bets", "odds", "spread", "fantasy", "streak", "session", "hour", "tired", "bedtime", "personal", "sweat", "autoplay"}


def test_arrival_cannot_read_betting_or_session_length():
    tree = ast.parse(Path("redzone/moments/arrival.py").read_text())
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    names |= {n.arg for n in ast.walk(tree) if isinstance(n, ast.arg)} | {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    words = {w for name in names for w in name.lower().split("_")}
    assert not BANNED & words, BANNED & words


def test_flag_ships_off_and_every_knob_is_listed():
    from redzone.config import Config
    cfg = Config()
    assert cfg.get("threads.arrival.enabled") is False
    for f in A.Params.__dataclass_fields__:
        assert cfg.get(f"threads.arrival.{f}", "missing") != "missing", f


# --- the chain: caps, and flag off ----------------------------------------------------------------------------------

def scored(score=50.0, **kw):
    s = Scored("g", League.NFL, score, score, score, 1.0, 0.0, "", [], False, None, None, 1.0)
    for k, v in kw.items():
        setattr(s, k, v)
    return s


class Cfg:
    def __init__(self, **v):
        self.v = {"threads.enabled": True, "threads.arrival.enabled": True, "decider.must_see_score": 92, **v}

    def get(self, key, default=None):
        return self.v.get(key, default)


class D:
    mu = 43.0


def host(monkeypatch, cfg=None, musts=(), cur="other"):
    d = show.ShowDecider.__new__(show.ShowDecider)
    d.cfg, d.threads, d.arrival_book, d.arrival_lifts, d.arrival_cuts = cfg or Cfg(), T.Ledger(), A.Book(), {}, show.Counter()
    d.arrival_log = []
    d.thread_holds, d.session_end_at, d.entry_cost = show.Counter(), None, (lambda target: TUNE)
    d.__dict__["_current"] = cur
    d.delays = type("B", (), {"get": staticmethod(lambda t: D)})()
    monkeypatch.setattr(Decider, "_arbiter_params", lambda self: None)
    monkeypatch.setattr(Decider, "_arbiter_musts", lambda self, c, now, p: musts)
    monkeypatch.setattr(Decider, "current", property(lambda self: self.__dict__["_current"]), raising=False)
    return d


def seed(d):
    g = fourth()
    d.show_states = {"g": g}
    d.threads.tick({"g": g}, d.current, 0.0, LP, AIR)
    d.arrival_book.tick({}, d.threads, {"g": g}, 0.0)


def lifted(d, cands, now=0.0):
    return d._with_arrival_scores(cands, now, LP, AP)


def test_a_game_with_a_question_reads_higher_scores_only(monkeypatch):
    d = host(monkeypatch)
    seed(d)
    c = {"g": Candidate(scored(), "t:g"), "other": Candidate(scored(), "t:o")}
    got = lifted(d, c)
    assert got["g"].scored.score > 50.0 and got["g"].target == "t:g" and c["g"].scored.score == 50.0
    assert d.arrival_lifts == {"g": "fourth_down"}


def test_nothing_is_added_with_a_must_show_owed_elsewhere(monkeypatch):
    class M:
        game_id = "other"
    d = host(monkeypatch, musts=(M(),))
    seed(d)
    got = lifted(d, {"g": Candidate(scored(), "t:g"), "other": Candidate(scored(), "t:o")})
    assert got["g"].scored.score == 50.0


@pytest.mark.parametrize("big", [{"crunch": True}, {"crunch_now": True}, {"high_leverage": True}, {"mine": True}, {"lock": "crunch"}])
def test_never_lifted_past_a_crunch_or_your_team_game(monkeypatch, big):
    d = host(monkeypatch)
    seed(d)
    if "lock" in big:
        big = {"lock": next(iter(show.CRUNCH_LOCKS))}
    got = lifted(d, {"g": Candidate(scored(), "t:g"), "other": Candidate(scored(60.0, **big), "t:o")})
    assert got["g"].scored.score <= 59.0


def test_never_lifted_into_a_must_see_score_by_itself(monkeypatch):
    d = host(monkeypatch)
    seed(d)
    assert lifted(d, {"g": Candidate(scored(90.0), "t:g")})["g"].scored.score == 91.0


def test_arrival_flag_needs_the_ledger_flag(monkeypatch):
    d = host(monkeypatch, cfg=Cfg(**{"threads.enabled": False}))
    assert d._arrival_on() is False


def test_flag_off_decide_never_touches_arrival(monkeypatch):
    d = host(monkeypatch, cfg=Cfg(**{"threads.arrival.enabled": False}))
    monkeypatch.setattr(Decider, "decide", lambda self, c, rz=None, commit=True, reel=None: c)
    d.show_states, d._clock, d._known = {}, (lambda: 0.0), {}
    d._air_lag_s = lambda p: 0.0
    d._with_arrival_scores = lambda *a, **k: pytest.fail("arrival ran with the flag off")
    c = {"g": Candidate(scored(), "t:g")}
    assert d.decide(c) is c


def test_past_the_viewers_chosen_end_arrival_stops(monkeypatch):
    d = host(monkeypatch)
    d.session_end_at = -1.0
    monkeypatch.setattr(Decider, "decide", lambda self, c, rz=None, commit=True, reel=None: c)
    d.show_states, d._clock, d._known = {}, (lambda: 0.0), {}
    d._air_lag_s = lambda p: 0.0
    d._with_arrival_scores = lambda *a, **k: pytest.fail("arrival ran past the viewer's end")
    c = {"g": Candidate(scored(), "t:g")}
    assert d.decide(c) is c
