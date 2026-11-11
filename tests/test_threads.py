"""The thread ledger: what opens a question, what answers it, and when a cut may wait for the answer."""
import ast
from pathlib import Path

import pytest

from redzone.decider.decider import Candidate, Decider, Decision, State
from redzone.models import GameState, League, Phase, Situation, Stoppage, Team
from redzone.moments import threads as T
from redzone.noggin import show
from redzone.scoring.engine import Scored

P = T.Params(enabled=True, closure_first=True, reopen_guard_s=0.0, flicker_s=8.0)
AIR = 35.0


def state(lg, *, home=10, away=10, period=1, clock=600.0, phase=Phase.LIVE, stoppage=Stoppage.NONE, sit=None, half=None, gid="g"):
    return GameState(lg, gid, Team("HOM", score=home), Team("AWY", score=away), phase=phase, period=period, clock_s=clock,
                     stoppage=stoppage, situation=sit or Situation(), half=half)


def tick(led, g, now, *, on=None, p=P, air=AIR):
    led.tick({g.game_id: g}, on, now, p, air)


# --- what opens ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("sit,period,clock,home,away,kind", [
    (Situation(possession="HOM", down=4, distance=2, yards_to_goal=30), 2, 600.0, 7, 7, "fourth_down"),
    (Situation(possession="HOM", down=2, distance=6, yards_to_goal=12), 1, 600.0, 7, 7, "rz_trip"),
    (Situation(possession="HOM", down=1, distance=10, yards_to_goal=60), 4, 100.0, 7, 10, "two_minute"),
    (Situation(possession="HOM", down=1, distance=10, yards_to_goal=60), 4, 400.0, 7, 10, "go_ahead"),
])
def test_nfl_opens_the_right_question(sit, period, clock, home, away, kind):
    assert T.detect(state(League.NFL, sit=sit, period=period, clock=clock, home=home, away=away), params=P).kind == kind


def test_nfl_nothing_open_on_an_early_midfield_first_down():
    sit = Situation(possession="HOM", down=1, distance=10, yards_to_goal=55)
    assert T.detect(state(League.NFL, sit=sit, period=1, clock=600.0), params=P) is None


def test_a_real_break_opens_nothing():
    sit = Situation(possession="HOM", down=4, distance=2, yards_to_goal=30)
    assert T.detect(state(League.NFL, sit=sit, stoppage=Stoppage.TIMEOUT), params=P) is None


def test_college_football_reads_football_rules():
    sit = Situation(possession="HOM", down=4, distance=2, yards_to_goal=30)
    assert T.detect(state(League.CFB, sit=sit), params=P).kind == "fourth_down"


def test_basketball_last_possessions_and_tying_shot():
    assert T.detect(state(League.NBA, period=4, clock=100.0, home=100, away=96), params=P).kind == "last_possessions"
    assert T.detect(state(League.NBA, period=4, clock=40.0, home=100, away=98), params=P).kind == "tying_shot"
    assert T.detect(state(League.NBA, period=4, clock=300.0, home=100, away=96), params=P) is None


def test_college_basketball_uses_its_halves():
    assert T.detect(state(League.CBB, period=2, clock=100.0, home=70, away=66), params=P).kind == "last_possessions"
    assert T.detect(state(League.CBB, period=1, clock=100.0, home=70, away=66), params=P) is None


def test_a_run_is_a_fact_that_never_holds_or_ranks():
    g = state(League.NBA, period=3, clock=300.0, home=70, away=64)
    spec = T.detect(g, run=("AWY", 8), params=P)
    assert spec.kind == "close_run" and "close_run" not in T.HOLDABLE
    led = T.Ledger()
    led._open("g", g, spec, 0.0, "g")
    assert T.closure_bonus(led, "g", 1.0, P) == 0.0
    facts = T.HoldFacts("better", False, False, False)
    assert T.hold_for(led, "g", League.NBA, 1.0, facts, P) is None


def test_nhl_power_play_and_extra_attacker():
    assert T.detect(state(League.NHL, sit=Situation(power_play="HOM", strength="5v4")), params=P).kind == "power_play"
    assert T.detect(state(League.NHL, sit=Situation(empty_net="AWY")), params=P).kind == "extra_attacker"


def test_mlb_risp_late_and_save_situation():
    sit = Situation(outs=1, runners=(False, True, False))
    assert T.detect(state(League.MLB, sit=sit, period=8, home=3, away=2, half="top"), params=P).kind == "risp_late"
    assert T.detect(state(League.MLB, sit=sit, period=3, home=3, away=2, half="top"), params=P) is None
    empty = Situation(outs=0, runners=(False, False, False))
    assert T.detect(state(League.MLB, sit=empty, period=9, home=3, away=1, half="top"), params=P).kind == "save_situation"


# --- what answers it ----------------------------------------------------------------------------------------------

def fourth(poss="HOM", **kw):
    return state(League.NFL, sit=Situation(possession=poss, down=4, distance=2, yards_to_goal=30), **kw)


def test_fourth_down_closes_on_the_conversion():
    led = T.Ledger()
    tick(led, fourth(), 0.0)
    assert led.open["g"].kind == "fourth_down"
    tick(led, state(League.NFL, sit=Situation(possession="HOM", down=1, distance=10, yards_to_goal=25)), 40.0)
    assert not led.open
    assert led.closed[-1].closes_on == "first_down"


def test_fourth_down_closes_on_a_turnover_or_a_score():
    led = T.Ledger()
    tick(led, fourth(), 0.0)
    tick(led, state(League.NFL, sit=Situation(possession="AWY", down=1, distance=10, yards_to_goal=70)), 40.0)
    assert led.closed[-1].closes_on == "possession"
    led2 = T.Ledger()
    tick(led2, fourth(), 0.0)
    tick(led2, fourth(home=13), 40.0)
    assert led2.closed[-1].closes_on == "score"


def test_one_question_per_game_until_it_is_answered():
    led = T.Ledger()
    tick(led, fourth(), 0.0)
    tick(led, state(League.NFL, sit=Situation(possession="HOM", down=4, distance=1, yards_to_goal=29)), 5.0)
    assert sum(led.opened.values()) == 1 and "g" in led.open


def test_an_answer_reaches_the_picture_after_the_feed():
    led = T.Ledger()
    tick(led, fourth(), 0.0, air=35.0)
    tick(led, fourth(home=13), 40.0, air=35.0)
    t = led.closed[-1]
    assert t.aired_at == pytest.approx(75.0) and led.unaired("g", 60.0) is t and led.unaired("g", 76.0) is None


def test_power_play_needs_to_stay_gone():
    led = T.Ledger()
    pp = state(League.NHL, sit=Situation(power_play="HOM", strength="5v4"))
    even = state(League.NHL, sit=Situation())
    tick(led, pp, 0.0)
    tick(led, even, 10.0)        # flicker: the label lags
    assert "g" in led.open
    tick(led, pp, 15.0)
    tick(led, even, 20.0)
    tick(led, even, 29.0)
    assert led.closed[-1].closes_on == "pp_over"


def test_power_play_goal_closes_it():
    led = T.Ledger()
    tick(led, state(League.NHL, sit=Situation(power_play="HOM", strength="5v4"), home=1), 0.0)
    tick(led, state(League.NHL, sit=Situation(), home=2), 10.0)
    assert led.closed[-1].closes_on == "score"


def test_mlb_stranded_closes_it():
    led = T.Ledger()
    tick(led, state(League.MLB, sit=Situation(outs=1, runners=(False, True, False)), period=8, home=3, away=2, half="top"), 0.0)
    tick(led, state(League.MLB, sit=Situation(outs=2, runners=(True, False, False)), period=8, home=3, away=2, half="top"), 30.0)
    assert led.closed[-1].closes_on == "stranded"


def test_reopen_guard_stops_a_score_state_opening_a_phantom():
    p = T.Params(enabled=True, closure_first=True, reopen_guard_s=25.0)
    led = T.Ledger()
    tick(led, fourth(home=7), 0.0, p=p)
    tick(led, fourth(home=13), 10.0, p=p)            # the score, and the next state still reads like a 4th down
    assert led.closed[-1].closes_on == "score" and "g" not in led.open
    tick(led, fourth(home=13), 40.0, p=p)
    assert led.open["g"].kind == "fourth_down"


def test_a_finished_game_closes_its_thread():
    led = T.Ledger()
    tick(led, state(League.NBA, period=4, clock=30.0, home=100, away=98), 0.0)
    tick(led, state(League.NBA, period=4, clock=0.0, home=100, away=98, phase=Phase.POST), 40.0)
    assert led.closed[-1].closes_on == "game_end" and not led.open


# --- the hold ------------------------------------------------------------------------------------------------------

def opened(kind="fourth_down", lg=League.NFL, eta=45.0, now=0.0, stake=0.5):
    led = T.Ledger()
    led._open("g", state(lg), T.Spec(kind, "q", eta, stake), now, "g")
    return led


def facts(cut="better", on_break=False, must=False, big=False):
    return T.HoldFacts(cut, on_break, must, big)


def test_an_optional_cut_waits_for_the_open_question():
    led = opened()
    assert T.hold_for(led, "g", League.NFL, 10.0, facts(), P)[0] == "fourth_down"


@pytest.mark.parametrize("cut", ["user", "crunch", "must_see", "chase", "home", "return", "start", "drive", "replay", ""])
def test_a_hold_never_stops_a_cut_that_is_not_optional(cut):
    assert T.hold_for(opened(), "g", League.NFL, 10.0, facts(cut=cut), P) is None


def test_a_must_show_elsewhere_beats_the_hold():
    assert T.hold_for(opened(), "g", League.NFL, 10.0, facts(must=True), P) is None


def test_a_crunch_or_big_target_beats_the_hold():
    assert T.hold_for(opened(), "g", League.NFL, 10.0, facts(big=True), P) is None


def test_no_hold_through_a_break():
    assert T.hold_for(opened(), "g", League.NFL, 10.0, facts(on_break=True), P) is None


def test_a_thread_cannot_hold_past_its_sports_reach():
    led = opened(lg=League.NBA)
    now = 5.0
    t = T.hold_for(led, "g", League.NBA, now, facts(), P)[1]
    led.note_hold("g", now, t, P)
    assert T.hold_for(led, "g", League.NBA, 79.0, facts(), P) is not None
    assert T.hold_for(led, "g", League.NBA, 81.0, facts(), P) is None        # 75 s reach from the first hold


def test_a_chain_of_threads_cannot_hold_past_twice_the_reach():
    led = opened(lg=League.NHL)
    for now in range(0, 221, 20):                    # one hold every 20 s, each on a fresh thread so only the chain can stop it
        led.open["g"].first_hold = None
        t = T.hold_for(led, "g", League.NHL, float(now), facts(), P)[1]
        led.note_hold("g", float(now), t, P)
    led.open["g"].first_hold = None
    assert T.hold_for(led, "g", League.NHL, 235.0, facts(), P) is not None
    assert T.hold_for(led, "g", League.NHL, 241.0, facts(), P) is None       # 2 x 120 s, from the first hold


def test_a_gap_starts_a_new_chain():
    led = opened(lg=League.NHL)
    led.note_hold("g", 0.0, led.open["g"], P)
    led.note_hold("g", 20.0, led.open["g"], P)
    assert led.chain_s("g", 40.0, P) == 40.0
    assert led.chain_s("g", 200.0, P) == 0.0


def test_the_hold_waits_for_the_answer_to_air_then_lets_go():
    led = T.Ledger()
    tick(led, fourth(), 0.0, on="g", air=35.0)
    tick(led, state(League.NFL, home=13), 40.0, on="g", air=35.0)
    assert not led.open
    assert T.hold_for(led, "g", League.NFL, 60.0, facts(), P)[0] == "fourth_down"
    assert T.hold_for(led, "g", League.NFL, 80.0, facts(), P) is None


def test_flag_off_means_no_hold_and_no_bonus():
    off = T.Params(enabled=True, closure_first=False)
    led = opened()
    assert T.hold_for(led, "g", League.NFL, 10.0, facts(), off) is None
    assert T.closure_bonus(led, "g", 10.0, off) == 0.0


# --- the bonus -----------------------------------------------------------------------------------------------------

def test_the_thread_about_to_close_earns_the_most():
    near = opened(eta=45.0, now=0.0, stake=1.0)
    assert T.closure_bonus(near, "g", 44.0, P) > T.closure_bonus(near, "g", 5.0, P) > 0.0
    far = opened(eta=300.0, now=0.0, stake=1.0)
    assert T.closure_bonus(far, "g", 10.0, P) == 0.0


def test_bonus_is_capped_at_its_points():
    assert T.closure_bonus(opened(eta=1.0, stake=1.0), "g", 50.0, P) <= P.bonus_pts


def test_fade_after_the_answer_airs():
    led = T.Ledger()
    tick(led, fourth(), 0.0, air=35.0)
    tick(led, fourth(home=13), 40.0, air=35.0)
    assert T.fade_after_answer(led, "g", 76.0, P) == P.cut_penalty_pts
    assert T.fade_after_answer(led, "g", 200.0, P) == 0.0


# --- the chain ------------------------------------------------------------------------------------------------------

def scored(**kw):
    s = Scored("g", League.NFL, 50.0, 50.0, 50.0, 1.0, 0.0, "", [], False, None, None, 1.0)
    for k, v in kw.items():
        setattr(s, k, v)
    return s


class Cfg:
    def __init__(self, **v):
        self.v = {"threads.enabled": True, "threads.closure_first": True, **v}

    def get(self, key, default=None):
        return self.v.get(key, default)


def host(monkeypatch, cfg=None, musts=()):
    d = show.ShowDecider.__new__(show.ShowDecider)
    d.cfg, d.threads = cfg or Cfg(), T.Ledger()
    d.thread_holds, d.session_end_at = show.Counter(), None
    d.__dict__["_current"] = "g"
    monkeypatch.setattr(Decider, "_safe", lambda self, dec, c, now, hb: dec)
    monkeypatch.setattr(Decider, "_home_restore", lambda self, hb, why: None)
    monkeypatch.setattr(Decider, "_arbiter_params", lambda self: None)
    monkeypatch.setattr(Decider, "_arbiter_musts", lambda self, c, now, p: musts)
    monkeypatch.setattr(Decider, "current", property(lambda self: self.__dict__["_current"]), raising=False)
    return d


def cut(kind="better", target="other"):
    return Decision(10.0, "switch", target, "a better game", State.WATCHING, kind=kind)


def test_chain_turns_an_optional_cut_into_a_stay(monkeypatch):
    d = host(monkeypatch)
    d.threads._open("g", state(League.NFL), T.Spec("fourth_down", "q", 45.0, 0.5), 0.0, "g")
    cands = {"g": Candidate(scored(), "t:g"), "other": Candidate(scored(), "t:o")}
    got = d._safe(cut(), cands, 10.0, None)
    assert got.action == "stay" and got.want == "other" and d.thread_holds["fourth_down"] == 1


def test_chain_lets_a_chase_go(monkeypatch):
    d = host(monkeypatch)
    d.threads._open("g", state(League.NFL), T.Spec("fourth_down", "q", 45.0, 0.5), 0.0, "g")
    cands = {"g": Candidate(scored(), "t:g"), "other": Candidate(scored(), "t:o")}
    assert d._safe(cut(kind="chase"), cands, 10.0, None).action == "switch"


def test_chain_never_holds_over_a_must_show_elsewhere(monkeypatch):
    class M:
        game_id = "other"
    d = host(monkeypatch, musts=(M(),))
    d.threads._open("g", state(League.NFL), T.Spec("fourth_down", "q", 45.0, 0.5), 0.0, "g")
    cands = {"g": Candidate(scored(), "t:g"), "other": Candidate(scored(), "t:o")}
    assert d._safe(cut(), cands, 10.0, None).action == "switch"


def test_chain_flag_off_is_the_parents_call(monkeypatch):
    d = host(monkeypatch, cfg=Cfg(**{"threads.closure_first": False}))
    d.threads._open("g", state(League.NFL), T.Spec("fourth_down", "q", 45.0, 0.5), 0.0, "g")
    cands = {"g": Candidate(scored(), "t:g"), "other": Candidate(scored(), "t:o")}
    assert d._safe(cut(), cands, 10.0, None).action == "switch" and not d.thread_holds


def test_past_the_viewers_chosen_end_nothing_acts(monkeypatch):
    d = host(monkeypatch)
    d.session_end_at = 5.0
    d.threads._open("g", state(League.NFL), T.Spec("fourth_down", "q", 45.0, 0.5), 0.0, "g")
    cands = {"g": Candidate(scored(), "t:g"), "other": Candidate(scored(), "t:o")}
    assert d._safe(cut(), cands, 10.0, None).action == "switch"


def test_chain_ledger_only_means_no_score_change_no_hold(monkeypatch):
    d = host(monkeypatch, cfg=Cfg(**{"threads.closure_first": False}))
    monkeypatch.setattr(Decider, "decide", lambda self, c, rz=None, commit=True, reel=None: c)
    d.show_states, d._clock, d.delays, d._known = {}, (lambda: 0.0), None, {}
    d._air_lag_s = lambda p: 0.0
    c = {"g": Candidate(scored(), "t:g")}
    assert d.decide(c) is c


def test_flag_off_ledger_never_ticks(monkeypatch):
    d = host(monkeypatch, cfg=Cfg(**{"threads.enabled": False}))
    monkeypatch.setattr(Decider, "decide", lambda self, c, rz=None, commit=True, reel=None: c)
    d.threads.tick = lambda *a, **k: pytest.fail("ledger ticked with the flag off")
    c = {"g": Candidate(scored(), "t:g")}
    assert d.decide(c) is c


def test_thread_bonus_changes_only_the_score(monkeypatch):
    d = host(monkeypatch)
    d.__dict__["_current"] = "other"
    d.threads._open("g", state(League.NFL), T.Spec("fourth_down", "q", 45.0, 1.0), 0.0, "g")
    c = {"g": Candidate(scored(), "t:g")}
    got = d._with_thread_scores(c, 40.0, P)
    assert got["g"].scored.score > 50.0 and got["g"].target == "t:g" and c["g"].scored.score == 50.0


def test_fade_stays_off_crunch(monkeypatch):
    d = host(monkeypatch)
    tick(d.threads, fourth(), 0.0, air=35.0)
    tick(d.threads, fourth(home=13), 40.0, air=35.0)
    plain, crunch = {"g": Candidate(scored(), "t:g")}, {"g": Candidate(scored(crunch=True), "t:g")}
    assert d._with_thread_scores(plain, 80.0, P)["g"].scored.score == 42.0
    assert d._with_thread_scores(crunch, 80.0, P)["g"].scored.score == 50.0


# --- the guards the show must keep -----------------------------------------------------------------------------------

BANNED = {"bet", "bets", "odds", "spread", "fantasy", "streak", "session", "hour", "tired", "bedtime", "personal", "sweat"}


@pytest.mark.parametrize("path", ["redzone/moments/threads.py"])
def test_the_ledger_cannot_read_betting_or_session_length(path):
    """Report section 8: no betting tie-in, no ranking on time of night or session length, runs never predict."""
    tree = ast.parse(Path(path).read_text())
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    names |= {n.arg for n in ast.walk(tree) if isinstance(n, ast.arg)} | {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    words = {w for name in names for w in name.lower().split("_")}
    assert not BANNED & words, BANNED & words


def test_flags_ship_off_and_every_knob_is_listed():
    from redzone.config import Config
    cfg = Config()
    assert cfg.get("threads.enabled") is False and cfg.get("threads.closure_first") is False
    for f in T.Params.__dataclass_fields__:
        assert cfg.get(f"threads.{f}", "missing") != "missing", f
