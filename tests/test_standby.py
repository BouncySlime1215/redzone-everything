"""Hot standby policy: what the hidden stick should hold between switches, and when to re-tune it."""
from conftest import FakeClock, nfl

from redzone import standby
from redzone.decider.decider import Candidate, Missed
from redzone.models import GameState, League, Phase, Situation, Team
from redzone.scoring.engine import Scored
from redzone.standby import BEST, HOME, MOMENT, REPLAY, Option


def sc(gid, score, *, pre=None, is_break=False, garbage=False, league=League.NFL):
    return Scored(gid, league, score, score, score, 1.0, 0.0, f"reason {gid}", [], is_break, None, pre, 1.0,
                  garbage)


def cand(gid, score, target=None, stale=False, **kw):
    return Candidate(sc(gid, score, **kw), target or f"youtubetv:{gid}", stale=stale)


def pool(*xs):
    return {x.scored.game_id: x for x in xs}


def game(league, gid, hs, as_, period, clock=None, home="WSH", away="DAL", **sit):
    return GameState(league=league, game_id=gid, home=Team(home, score=hs), away=Team(away, score=as_),
                     phase=Phase.LIVE, period=period, clock_s=clock, situation=Situation(**sit))


# --- ranking: the four classes --------------------------------------------------------------

def test_home_game_takes_the_slot_even_during_its_break():
    cs = pool(cand("home", 40, is_break=True), cand("hot", 95, pre="4th & goal"), cand("b", 80))
    opts = standby.options(cs, current="visit", home="home")
    assert opts[0].game_id == "home" and opts[0].kind == "home"


def test_replay_beats_moments_but_not_your_game():
    cs = pool(cand("home", 40, is_break=True), cand("hot", 95, pre="4th & goal"))
    rp = Option("x", "youtubetv:x", REPLAY, 99, "replay ready", back_s=60, replay_id="p1")
    assert standby.options(cs, current="cur", replay=rp)[0].replay_id == "p1"
    assert standby.options(cs, current="cur", home="home", replay=rp)[0].kind == "home"


def test_moments_crunch_time_and_your_team_outrank_a_better_plain_game():
    games = {"crunch": nfl("crunch", home="KC", away="BUF", hs=20, as_=17, q=4, clock=300),
             "mine": nfl("mine", home="WSH", away="NYG", hs=7, as_=3, q=2, clock=600),
             "plain": nfl("plain", home="SF", away="SEA", hs=14, as_=10, q=3, clock=600)}
    mine = {League.NFL: {"WSH"}}
    for gid, score, why in (("pre", 30, "about to happen: 4th & goal"), ("crunch", 30, "crunch time coming"),
                            ("mine", 70, "your team")):
        cs = pool(cand(gid, score, pre="4th & goal" if gid == "pre" else None), cand("plain", 85))
        opts = standby.options(cs, current="cur", games=games, my_teams=mine)
        assert (opts[0].game_id, opts[0].kind, opts[0].why) == (gid, "moment", why)


def test_your_team_in_a_lull_far_behind_the_best_game_is_just_another_game():
    games = {"mine": nfl("mine", home="WSH", away="NYG", hs=7, as_=3, q=1, clock=600)}
    cs = pool(cand("mine", 40), cand("thriller", 85))
    opts = standby.options(cs, current="cur", games=games, my_teams={League.NFL: {"WSH"}})
    assert opts[0].game_id == "thriller" and [o.kind for o in opts] == ["best", "best"]


def test_garbage_time_gets_no_priority_even_for_your_team():
    games = {"mine": nfl("mine", home="WSH", away="NYG", hs=38, as_=3, q=4, clock=300)}
    cs = pool(cand("mine", 30, garbage=True), cand("plain", 60))
    opts = standby.options(cs, current="cur", games=games, my_teams={League.NFL: {"WSH"}})
    assert opts[0].game_id == "plain" and opts[1].kind == "best"


def test_never_what_is_on_screen_muted_stale_untunable_or_on_a_break():
    cs = pool(cand("cur", 99), cand("muted", 90), cand("stale", 90, stale=True), cand("dead", 90),
              cand("brk", 90, is_break=True), cand("ok", 20))
    cs["nolink"] = Candidate(sc("nolink", 95), None)
    opts = standby.options(cs, current="cur", muted={"muted"}, tunable=lambda t: t != "youtubetv:dead")
    assert [o.game_id for o in opts] == ["ok"]


def test_value_counts_seconds_saved_and_the_game_we_just_left():
    cs = pool(cand("yt", 71), cand("pr", 68, target="prime:amzn1.x"))
    opts = standby.options(cs, current="cur", save_s=lambda t: 9.0 if t.startswith("prime") else 4.0)
    assert opts[0].game_id == "pr"          # 68 + 9s saved beats 71 + 4s
    cs = pool(cand("prev", 62), cand("c", 70))
    opts = standby.options(cs, current="cur", back_to="prev")
    assert opts[0].game_id == "prev" and "just left" in opts[0].why


# --- the planner: hysteresis ------------------------------------------------------------------

def opt(gid, value, cls=BEST, replay_id=None):
    return Option(gid, f"youtubetv:{gid}", cls, value, "", replay_id=replay_id)


def test_holds_a_game_then_moves_only_for_a_clear_gain(cfg):
    clock = FakeClock()
    p = standby.Planner(cfg, clock=clock)
    held = ("youtubetv:a", None)
    assert p.plan([opt("b", 72), opt("a", 60)], held) is None      # just loaded: hold it
    clock.t += 10
    assert p.plan([opt("b", 72), opt("a", 60)], held) is None      # still inside hold_s (20)
    much = opt("b", 90)
    assert p.plan([much, opt("a", 60)], held) is much              # ...unless much better (2x min_gain)
    clock.t += 15
    assert p.plan([opt("b", 65), opt("a", 60)], held) is None      # +5 is noise (min_gain 8)
    b = opt("b", 70)
    assert p.plan([b, opt("a", 60)], held) is b                     # +10 after the hold: move


def test_a_higher_class_moves_after_the_hold_even_if_it_scores_lower(cfg):
    clock = FakeClock()
    p = standby.Planner(cfg, clock=clock)
    m = opt("m", 40, MOMENT)
    assert p.plan([m, opt("a", 80)], ("youtubetv:a", None)) is None
    clock.t += 36
    assert p.plan([m, opt("a", 80)], ("youtubetv:a", None)) is m


def test_your_game_and_replays_dont_wait_for_the_hold(cfg):
    p = standby.Planner(cfg, clock=FakeClock())
    home = opt("home", 10, HOME)
    assert p.plan([home, opt("a", 80)], ("youtubetv:a", None)) is home
    rp = opt("x", 70, REPLAY, replay_id="p1")
    assert p.plan([rp, opt("a", 80)], ("youtubetv:a", None)) is rp


def test_empty_or_useless_standby_is_filled_at_once(cfg):
    p = standby.Planner(cfg, clock=FakeClock())
    a = opt("a", 50)
    assert p.plan([a], (None, None)) is a                           # nothing loaded
    assert p.plan([a], ("youtubetv:gone", None)) is a               # its game ended / got muted
    assert p.plan([a], ("youtubetv:a", "p1")) is a                  # parked replay footage isn't live


def test_already_there_means_leave_it_alone(cfg):
    p = standby.Planner(cfg, clock=FakeClock())
    assert p.plan([opt("a", 50)], ("youtubetv:a", None)) is None
    assert p.plan([opt("x", 70, REPLAY, replay_id="p1")], ("youtubetv:x", "p1")) is None
    assert p.plan([], (None, None)) is None


# --- game-state helpers -----------------------------------------------------------------------

def test_crunch_soon_by_league():
    assert standby.crunch_soon(game(League.NFL, "a", 20, 17, 4, 300))
    assert not standby.crunch_soon(game(League.NFL, "a", 20, 17, 4, 600))       # 10:00 left
    assert not standby.crunch_soon(game(League.NFL, "a", 31, 17, 4, 300))       # two scores
    assert standby.crunch_soon(game(League.NFL, "a", 17, 17, 5, 500))           # overtime
    assert standby.crunch_soon(game(League.NBA, "a", 99, 94, 4, 240))
    assert not standby.crunch_soon(game(League.NBA, "a", 99, 94, 3, 240))
    assert standby.crunch_soon(game(League.NHL, "a", 2, 2, 3, 360))
    assert not standby.crunch_soon(game(League.NHL, "a", 3, 1, 3, 360))
    assert standby.crunch_soon(game(League.MLB, "a", 3, 2, 8))
    assert not standby.crunch_soon(game(League.MLB, "a", 3, 2, 7))


def test_break_soon_reads_the_data_ahead_of_your_screen():
    assert standby.break_soon(None, sc("a", 50, is_break=True))                 # data already says break
    assert standby.break_soon(game(League.NFL, "a", 7, 3, 1, 45))               # end of the quarter
    assert standby.break_soon(game(League.NFL, "a", 7, 3, 2, 150))              # two-minute warning ahead
    assert not standby.break_soon(game(League.NFL, "a", 7, 3, 2, 300))
    assert not standby.break_soon(game(League.NFL, "a", 7, 3, 4, 45))           # end of GAME, not a break
    assert standby.break_soon(game(League.NBA, "a", 70, 66, 3, 40))
    assert not standby.break_soon(game(League.NBA, "a", 70, 66, 4, 40))
    assert standby.break_soon(game(League.NHL, "a", 1, 1, 2, 60))
    assert standby.break_soon(game(League.MLB, "a", 1, 1, 6, outs=2))
    assert not standby.break_soon(game(League.MLB, "a", 1, 1, 6, outs=1))


def test_likely_replay_is_the_most_valuable_fresh_one():
    now = 10_000.0
    old_big = Missed("g1", "p1", "TD", "td", now - 1700, 80)
    fresh = Missed("g2", "p2", "TD", "td", now - 60, 70)
    done = Missed("g3", "p3", "TD", "td", now - 10, 99, replayed=True)
    assert standby.likely_replay([old_big, fresh, done], now, 1800).pid == "p2"
    assert standby.likely_replay([done], now, 1800) is None


# --- the decider's own next pick, and the replay it would really play --------------------------

def test_the_switch_the_decider_already_wants_takes_the_slot_over_everything():
    cs = pool(cand("home", 40, is_break=True), cand("hot", 95, pre="4th & goal"), cand("next", 30))
    rp = Option("x", "youtubetv:x", REPLAY, 99, "replay ready", back_s=60, replay_id="p1")
    opts = standby.options(cs, current="visit", home="home", replay=rp, want="next")
    assert (opts[0].game_id, opts[0].kind) == ("next", "next")


def test_a_wanted_switch_never_waits_for_the_hold(cfg):
    p = standby.Planner(cfg, clock=FakeClock())
    nxt = opt("n", 10, standby.NEXT)
    assert p.plan([nxt, opt("a", 80)], ("youtubetv:a", None)) is nxt


def test_a_replay_the_decider_would_skip_for_a_better_live_game_is_not_cued():
    cs = pool(cand("live", 80))
    rp = Option("x", "youtubetv:x", REPLAY, 60, "replay ready", back_s=60, replay_id="p1")
    assert standby.options(cs, current="cur", replay=rp)[0].game_id == "live"
    rp.value = 90
    assert standby.options(cs, current="cur", replay=rp)[0].replay_id == "p1"


def test_shadow_stick_counts_flips_to_a_ready_preload_as_hits():
    s = standby.ShadowStick(tune_s=7)
    s.preload("youtubetv:a", None, 100)
    assert not s.switch("youtubetv:a", None, "youtubetv:cur", 103)   # still tuning
    assert s.standby == ("youtubetv:cur", None)                       # the stick we left reloads it
    assert s.switch("youtubetv:cur", None, "youtubetv:a", 120)        # going back: a flip
    s.preload("youtubetv:x", "p1", 130)
    assert not s.switch("youtubetv:x", None, "youtubetv:a", 140)      # parked replay != live
    card = s.card()
    assert card.pop("seconds_to_picture")["youtubetv"]["switches"] == 3
    assert card == {"switches": 3, "hits": 1, "hit_rate": 33.3, "still_loading": 1, "preloads": 2,
                    "breaks_filled": "0/0", "break_fill_rate": 0.0}


def test_shadow_stick_knows_prime_is_slow_to_tune():
    s = standby.ShadowStick(app_s={"prime": 30.0, "youtubetv": 4.5})
    s.preload("prime:a", None, 100)
    assert not s.switch("prime:a", None, "youtubetv:x", 110)     # 10 s in: still loading
    s.preload("prime:b", None, 200)
    assert s.switch("prime:b", None, "youtubetv:x", 231)         # 31 s in: ready, a flip
    pic = s.card()["seconds_to_picture"]["prime"]
    assert pic["switches"] == 2 and pic["instant"] == 1


def test_shadow_stick_prime_game_left_keeps_playing():
    """Prime keeps its player when its stick loses the TV input: going back is a flip within seconds,
    while YouTube TV pauses and has to be reloaded at the live edge."""
    s = standby.ShadowStick(tune_s=7, keep_s=2.5)
    s.preload("youtubetv:a", None, 100)
    assert s.switch("youtubetv:a", None, "prime:p", 110)
    assert s.switch("prime:p", None, "youtubetv:a", 113)        # 3 s later: Prime is still live there
    assert not s.switch("youtubetv:a", None, "prime:p", 116)    # 3 s later: YouTube TV is still reloading
