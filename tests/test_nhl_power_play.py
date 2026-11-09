"""[nhl pp] Power plays, goals and overtime: what the app knows, and what it does with it."""
import json
from pathlib import Path

from redzone.decider.decider import Candidate, _holds_replays
from redzone.lab import sportsgrade
from redzone.models import GameState, League, Phase, Situation, Stoppage, Team
from redzone.scoring.engine import Scored
from redzone.sources import espn, leagues
from tests.test_decider import calibrate, cands, dec, on_break_now, start_on  # noqa: F401  (fixture)
from tests.test_scorer import _score

FIX = Path(__file__).parent / "fixtures"
IDS = {"1": "WSH", "2": "PIT"}


def play(typ, team=None, strength="even-strength", text=""):
    p = {"type": {"id": typ, "text": text or typ}, "strength": {"abbreviation": strength}}
    if team:
        p["team"] = {"id": {"WSH": "1", "PIT": "2"}[team]}
    return p


FACEOFF, GOAL, STOP = "502", "505", "516"


def pp(*plays):
    return espn.nhl_power_play(list(plays), IDS, "WSH", "PIT")


# --- who's on the power play, from ESPN's play-by-play ----------------------------------

def test_strength_labels_say_who_is_up_a_man():
    assert pp(play(FACEOFF, "WSH", "power-play")) == ("WSH", "5v4")
    assert pp(play(FACEOFF, "WSH", "short-handed")) == ("PIT", "5v4")
    assert pp(play(FACEOFF, "WSH")) == (None, None)


def test_a_penalty_starts_the_power_play_before_the_faceoff_labels_it():
    # The penalty and the whistle after it are still labelled even strength.
    assert pp(play(FACEOFF, "WSH"), play("55", "PIT", text="Tripping"), play(STOP)) == ("WSH", "5v4")


def test_coincidental_minors_cancel_and_a_second_minor_is_a_5_on_3():
    assert pp(play(FACEOFF, "WSH"), play("55", "PIT", text="Tripping"), play("33", "WSH", text="Hooking")) == \
        (None, None)
    assert pp(play(FACEOFF, "WSH", "power-play"), play("55", "PIT", text="Tripping")) == ("WSH", "5v3")
    assert pp(play(FACEOFF, "WSH"), play("55", "PIT", text="Tripping"), play("33", "PIT", text="Hooking")) == \
        ("WSH", "5v3")


def test_a_power_play_goal_ends_it_and_fights_or_misconducts_do_not_start_one():
    assert pp(play(FACEOFF, "WSH", "power-play"), play(GOAL, "WSH", "power-play")) == (None, None)
    assert pp(play(FACEOFF, "WSH", "short-handed"), play(GOAL, "WSH", "short-handed")) == ("PIT", "5v4")
    assert pp(play(FACEOFF, "WSH"), play("80", "PIT", text="Fighting (maj)")) == (None, None)
    assert pp(play(FACEOFF, "WSH"), play("91", "PIT", text="Misconduct (10 min)")) == (None, None)


def test_recorded_game_shows_power_plays_and_the_nhl_feed_still_wins():
    data = json.loads((FIX / "espn_nhl_summary_401892626.json").read_text())
    plays = espn.flatten_plays(League.NHL, data)
    states = [espn.parse_summary(League.NHL, data, upto=i, plays=plays) for i in range(len(plays))]
    on = [g for g in states if g.situation.power_play]
    assert on and all(g.situation.power_play in (g.home.abbr, g.away.abbr) for g in on)
    # Most of the game is at even strength.
    assert len(on) < len(states) / 3
    g = on[0]
    # The NHL feed's on-ice code is the truth when there is one...
    out = leagues.enrich_nhl(g, {"gameState": "LIVE", "situationCode": "1551"})
    assert out.situation.power_play is None
    # ...but no code means "don't know", not "even strength".
    out = leagues.enrich_nhl(g, {"gameState": "LIVE"})
    assert out.situation.power_play == g.situation.power_play


# --- scoring: a power play within 2 goals holds the TV; overtime always does ---------------

def _nhl(hs, as_, period, clock_s, pp_team=None):
    return GameState(league=League.NHL, game_id="h", home=Team("BOS", score=hs), away=Team("PIT", score=as_),
                     phase=Phase.LIVE, period=period, clock_s=clock_s, stoppage=Stoppage.NONE,
                     situation=Situation(power_play=pp_team, strength="5v4" if pp_team else None))


def test_power_play_locks_within_two_goals_only(cfg, clock):
    assert _score(cfg, clock, _nhl(3, 1, 3, 600, "PIT")).lock == "power play"   # late, 2 goals
    assert _score(cfg, clock, _nhl(1, 1, 1, 900, "PIT")).lock == "power play"
    assert _score(cfg, clock, _nhl(4, 1, 1, 900, "PIT")).lock is None           # early 3-goal game


def test_overtime_always_locks(cfg, clock):
    s = _score(cfg, clock, _nhl(2, 2, 4, 240, "PIT"))
    assert s.lock == "overtime" and s.crunch


# --- the decider ----------------------------------------------------------------------

def nsc(gid, score, lock=None, crunch=False, personal=0.0, late=False, closeness=1.0, is_break=False):
    return Scored(gid, League.NHL, score, score, score, 1.0, personal, f"reason {gid}", [], is_break,
                  lock, None, closeness, crunch=crunch, late=late)


def test_neutral_overtime_is_held_through_its_stoppages(dec, clock):  # noqa: F811
    calibrate(dec, "youtubetv:a")
    dec.timeline("b", League.NFL)
    start_on(dec, clock, "a", {})
    on_break_now(dec, clock, "a", "timeout")
    d = dec.decide({"a": Candidate(nsc("a", 60, "overtime", crunch=True, is_break=True), "youtubetv:a"),
                    "b": Candidate(nsc("b", 80, "power play"), "youtubetv:b")})
    assert d.action == "stay" and d.reason.startswith("crunch time")


def test_a_power_play_never_beats_your_team_late(dec):  # noqa: F811
    w = {"pp": Candidate(nsc("pp", 80, "power play"), "youtubetv:pp"),
         "mine": Candidate(nsc("mine", 60, personal=18.0, late=True), "youtubetv:mine"),
         "c": Candidate(nsc("c", 70), "youtubetv:c")}
    assert dec._best(w, None) == "mine"
    w["mine"] = Candidate(nsc("mine", 60, personal=18.0, late=False), "youtubetv:mine")
    assert dec._best(w, None) == "pp"   # your team early: the power play's worth a look


def test_a_power_play_does_not_cut_a_catch_up_short_but_crunch_does():
    assert not _holds_replays(nsc("a", 80, "power play", closeness=0.9))
    assert _holds_replays(nsc("a", 80, "overtime"))
    assert _holds_replays(nsc("a", 80, "empty net"))


def test_only_an_imminent_chance_lets_a_chase_skip_the_whistle_hold(dec):  # noqa: F811
    from redzone.decider.decider import Decision, State
    chase = Decision(0, "switch", "b", "catch it live", State.WATCHING, kind="chase")
    better = Decision(0, "switch", "b", "better game", State.WATCHING, kind="better")
    hot = nsc("b", 50)
    hot.imminent = True
    assert not dec._entry_only(Candidate(hot, "youtubetv:b"), chase)
    assert dec._entry_only(Candidate(hot, "youtubetv:b"), better)
    assert dec._entry_only(Candidate(nsc("b", 50), "youtubetv:b"), chase)   # an ordinary chase waits


def test_crunch_run_up_waits_for_a_whistle_but_the_last_two_minutes_do_not(dec):  # noqa: F811
    dec.cfg.defaults["league_overrides"] = dec.cfg.data["league_overrides"] = {}   # live behavior (decider.entry.crunch_now live)
    from redzone.decider.decider import Decision, State
    d = Decision(0, "switch", "b", "crunch time", State.WATCHING, kind="crunch")
    run_up = nsc("b", 70, crunch=True)
    now = nsc("b", 70, crunch=True)
    now.crunch_now = True
    assert dec._entry_only(Candidate(run_up, "youtubetv:b"), d)
    dec.cfg.update({"decider": {"entry": {"crunch_now": "live"}}}, record=False)
    dec.cfg.data["decider"]["entry"]["allow_live_fallback"] = True   # D-01: going in live now needs the explicit opt-in
    assert not dec._entry_only(Candidate(now, "youtubetv:b"), d)


def test_imminent_goal_chances(cfg, clock):
    from redzone.scoring.engine import History, Scorer, crunch_window, nhl_goal_imminent
    h = History()
    g = _nhl(1, 1, 2, 900, "PIT")
    assert nhl_goal_imminent(g, h, cfg)                   # the power play just started
    g.clock_s = 880
    assert nhl_goal_imminent(g, h, cfg)
    g.clock_s = 860
    assert not nhl_goal_imminent(g, h, cfg)               # 40 s in: an ordinary power play
    g.situation.strength = "5v3"
    assert nhl_goal_imminent(g, h, cfg)
    g = _nhl(2, 1, 3, 100)
    g.situation.empty_net = "PIT"
    assert nhl_goal_imminent(g, History(), cfg)
    g.clock_s = 300
    assert not nhl_goal_imminent(g, History(), cfg)
    # Off by default (0 mid-play entries on every measure): the skip never fires.
    sc = Scorer(cfg, clock=clock)
    assert not sc.score(_nhl(1, 1, 2, 900, "PIT")).imminent
    # Turned on, it stays "imminent" on the scored game for a while after (the goal kills the power play)
    cfg.update({"score": {"nhl": {"imminent_hold_s": 30}}}, record=False)
    sc = Scorer(cfg, clock=clock)
    assert sc.score(_nhl(1, 1, 2, 900, "PIT")).imminent
    clock.t += 20
    assert sc.score(_nhl(2, 1, 2, 890)).imminent
    clock.t += 20
    assert not sc.score(_nhl(2, 1, 2, 870)).imminent
    assert not crunch_window(_nhl(2, 1, 3, 170)) and crunch_window(_nhl(2, 1, 3, 110))
    assert crunch_window(_nhl(2, 2, 4, 290))


# --- the scorecard lines ----------------------------------------------------------------

def test_scorecard_counts_power_play_time_and_goals():
    plays = [play(FACEOFF, "WSH"), play("55", "PIT", text="Tripping"), play(STOP),
             play(FACEOFF, "WSH", "power-play"), play(GOAL, "WSH", "power-play"), play(FACEOFF, "PIT")]
    for p in plays:
        p["type"]["text"] = "Goal" if p["type"]["id"] == GOAL else p["type"]["text"]
    assert sportsgrade.power_play_after(League.NHL, plays) == [False, True, True, True, False, False]
    assert sportsgrade.power_play_after(League.NBA, plays) == [False] * 6
    assert [sportsgrade.is_pp_goal(League.NHL, p) for p in plays] == [False] * 4 + [True, False]
    assert {"pp_minutes_on_screen", "pp_goals_live_pct", "pp_goals"} <= set(sportsgrade.Card("nhl").summary())
    assert "pp_goals" not in sportsgrade.Card("nba").summary()


def test_crunch_last_two_minutes_plans_a_rewind_landing_but_never_waits(dec):  # noqa: F811
    dec.cfg.defaults["league_overrides"] = dec.cfg.data["league_overrides"] = {}   # live behavior (decider.entry.crunch_now live)
    # [D-01] the go-in-live exits need the fallback key (default false); this pins what that key brings back
    dec.cfg.update({"decider": {"entry": {"allow_live_fallback": True, "crunch_now": "rewind"}}}, record=False)
    from redzone.decider.decider import Decision, State
    d = Decision(0, "switch", "b", "crunch time", State.WATCHING, kind="crunch")
    now = nsc("b", 70, crunch=True)
    now.crunch_now = True
    assert dec._crunch_entry_mode() == "rewind"
    assert dec._entry_only(Candidate(now, "youtubetv:b"), d)        # plan the landing...
    dec.cfg.update({"decider": {"entry": {"crunch_now": "live"}}}, record=False)
    assert not dec._entry_only(Candidate(now, "youtubetv:b"), d)    # ...or, set to live, go now


def test_nba_entries_plan_a_rewind_landing(dec):  # noqa: F811
    from redzone.decider.decider import Decision, State
    d = Decision(0, "switch", "b", "better game", State.WATCHING, kind="better")
    x = Candidate(Scored("b", League.NBA, 50, 50, 50, 1.0, 0.0, "r", [], False, None, None, 1.0), "youtubetv:b")
    assert dec._entry_only(x, d)          # default: NBA waits for a whistle (hold_only, capped by max_wait_s)
    dec.cfg.update({"decider": {"entry": {"hold_only": ["nhl"], "rewind_only": ["nba"]}}}, record=False)
    assert dec._entry_only(x, d)          # rewind-only still plans a dead-ball landing
    dec.cfg.update({"decider": {"entry": {"rewind_only": []}}}, record=False)
    assert not dec._entry_only(x, d)
