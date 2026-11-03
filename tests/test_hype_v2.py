"""Hype v2: the learned score, its variables, and the hard rules kept on top of it."""
from conftest import nfl

from redzone.models import GameState, League, Phase, Play, Situation, Stoppage, Team
from redzone.scoring.engine import Personal, Scorer
from redzone.scoring.features import PHRASES, Tracker, features, time_left_frac
from redzone.scoring.hype import HypeModel

ME = Personal(my_teams={lg: {"WSH"} for lg in League})


def _g(lg, hs, as_, per, clk=None, wp=None, spread=None, gid="g", home="H", away="A", **kw):
    return GameState(league=lg, game_id=gid, home=Team(home, score=hs), away=Team(away, score=as_),
                     phase=Phase.LIVE, period=per, clock_s=clk, home_win_prob=wp, spread=spread,
                     stoppage=kw.pop("stoppage", Stoppage.NONE), **kw)


def test_model_ships_and_covers_every_league():
    m = HypeModel.load()
    assert m is not None and set(m.leagues) >= {"nfl", "nba", "nhl", "mlb"}
    # Monotone map: a likelier key moment never scores lower.
    xs = [i / 200 for i in range(201)]
    ys = [m.to_score(x) for x in xs]
    assert all(b >= a - 1e-9 for a, b in zip(ys, ys[1:])) and 0 <= ys[0] and ys[-1] <= 100


def test_features_are_bounded_and_never_crash_on_missing_data():
    for lg in (League.NFL, League.NBA, League.NHL, League.MLB, League.CBB, League.WCBB):
        g = GameState(league=lg, game_id="x", home=Team("H"), away=Team("A"), phase=Phase.LIVE)
        f = features(g, Tracker(), 0.0)
        assert f and all(-1.0 <= v <= 1.0 for v in f.values()), (lg, f)
        g2 = _g(lg, 50, 48, 2, 100, wp=0.6, spread=4.0)
        t = Tracker()
        t.observe(g2, 0.0)
        assert all(-1.0 <= v <= 1.0 for v in features(g2, t, 10.0).values())


def test_every_feature_the_model_uses_is_computed():
    m = HypeModel.load()
    for lg, md in m.leagues.items():
        g = _g(League(lg), 3, 2, 2, 300)
        names = set(features(g, Tracker(), 0.0))
        used = {n for n, keep in zip(md["names"], md["keep"]) if keep}
        assert used <= names, (lg, used - names)


def test_time_left_runs_down_and_overtime_is_short():
    assert time_left_frac(_g(League.NBA, 0, 0, 1, 720)) == 1.0
    assert time_left_frac(_g(League.NBA, 0, 0, 4, 60)) < 0.03
    assert time_left_frac(_g(League.NHL, 0, 0, 4, 300)) < 0.1
    assert time_left_frac(_g(League.CBB, 0, 0, 2, 600)) == 0.25


def test_tracker_counts_lead_changes_runs_and_stars():
    t = Tracker()
    plays = []
    for i, (hs, as_) in enumerate([(0, 0), (2, 0), (2, 3), (5, 3), (7, 3), (9, 3)]):
        plays.append(Play(str(i), "Star Player makes 18-foot jumper", scoring=True, score=(hs, as_)))
        g = _g(League.NBA, hs, as_, 4, 300, recent_plays=list(plays[-8:]), last_play=plays[-1])
        t.observe(g, float(i * 30))
    assert t.lead_changes == 2 and t.run_side == 1 and t.run_pts == 7   # 5-3, 7-3, 9-3 unanswered
    assert t.star and t.star[0] == "Star Player"


def test_tonights_miss_a_tightening_nba_finish_beats_an_nfl_q3_drive(cfg, clock):
    cfg.data["score"]["v2"]["leagues"] = ["nfl", "nba"]   # off by default (pending unseen nights)
    # Nov 2: NO's 9-point lead on MEM shrank to 2 in the last 4 minutes while the old score ranked an
    # NFL Q3 drive at the 20 above it until 0:35 was left. v2 sees the lead shrinking.
    sc = Scorer(cfg, clock=clock)
    drive = nfl("nfl", "SEA", "CHI", 10, 21, q=3, clock=400, pos="SEA", down=1, dist=10, ytg=20, wp=0.2)
    for clk, mem, no in [(264, 111, 120), (220, 113, 120), (180, 115, 121), (150, 118, 122)]:
        clock.t += 30
        a = sc.score(_g(League.NBA, mem, no, 4, clk, wp=0.3, gid="nba", home="MEM", away="NO"), Personal())
        b = sc.score(drive, Personal())
    assert a.hype_p is not None and a.score > b.score, (a.score, b.score)


def test_college_halftime_is_never_crunch_and_scores_low(cfg, clock):
    # OSU-BYU, Nov 2: halftime arrives as period 2, 0:00 and was scored 90 "last-shot possession".
    g = _g(League.CBB, 40, 38, 2, 0, stoppage=Stoppage.HALFTIME)
    s = Scorer(cfg, clock=clock).score(g, Personal())
    assert not s.crunch and s.score < 40, s


def test_your_team_live_and_close_late_beats_any_neutral_non_crunch(cfg, clock):
    cfg.data["score"]["v2"]["leagues"] = ["nfl", "nba"]   # off by default (pending unseen nights)
    sc = Scorer(cfg, clock=clock)
    mine = _g(League.NBA, 80, 88, 4, 600, wp=0.25, gid="w", home="WSH", away="CHA")
    neutral = _g(League.NBA, 90, 90, 3, 30, wp=0.5, gid="n")
    s_mine, s_neutral = sc.score(mine, ME), sc.score(neutral, ME)
    assert s_mine.score >= cfg.get("score.personal.my_team_late_floor") > s_neutral.score


def test_your_team_in_garbage_time_gets_no_floor(cfg, clock):
    s = Scorer(cfg, clock=clock).score(_g(League.NBA, 70, 100, 4, 300, wp=0.01, home="WSH"), ME)
    assert s.garbage and s.score < 25


def test_crunch_time_never_scores_below_the_old_crunch_value(cfg, clock):
    cfg.data["score"]["v2"]["leagues"] = ["nfl", "nba", "nhl"]
    g = _g(League.NHL, 2, 2, 3, 100, gid="c")
    s = Scorer(cfg, clock=clock).score(g, Personal())
    assert s.crunch and s.legacy is not None and s.score >= 0.9 * s.legacy - 1e-6


def test_a_stuck_feed_fades_out(cfg, clock):
    cfg.data["score"]["v2"]["leagues"] = ["nfl", "nba", "nhl"]
    sc = Scorer(cfg, clock=clock)
    g = _g(League.NHL, 2, 1, 2, 400, wp=0.5, last_play=Play("p1", "x"))
    fresh = sc.score(g, Personal()).score
    clock.t += 600   # ten minutes, same last play, not in a break
    stuck = sc.score(g, Personal()).score
    assert stuck < 0.5 * fresh, (fresh, stuck)


def test_reason_names_model_drivers_in_plain_english(cfg, clock):
    sc = Scorer(cfg, clock=clock)
    g = _g(League.NBA, 100, 100, 4, 40, wp=0.5)
    s = sc.score(g, Personal())
    assert s.reason and "·" in s.reason
    assert all(v == "" or "_" not in v for v in PHRASES.values())


def test_v2_off_is_the_old_score(cfg, clock):
    cfg.data["score"]["v2"]["enabled"] = False
    s = Scorer(cfg, clock=clock).score(_g(League.NBA, 98, 95, 4, 200), Personal())
    assert s.hype_p is None and s.legacy is None


def test_blend_and_quiet_fade_can_be_set_per_league(cfg, clock):
    from redzone.scoring.engine import _per_league
    assert _per_league({"nba": 0.5}, League.NBA, 1.0) == 0.5
    assert _per_league({"nba": 0.5}, League.NFL, 1.0) == 1.0      # not listed: the default
    assert _per_league({"nba": None}, League.NBA, 240.0) is None  # explicit null: off
    assert _per_league({"nba": "x"}, League.NBA, 1.0) == 1.0      # junk never breaks scoring
    assert _per_league(0.25, League.CBB, 1.0) == 0.25             # one number for every league
    # NBA ships with the fade off: a quiet feed outside a break keeps its score.
    sc = Scorer(cfg, clock=clock)
    g = _g(League.NBA, 50, 49, 3, 400, wp=0.5, last_play=Play("p1", "x"))
    fresh = sc.score(g, Personal()).score
    clock.t += 600
    assert sc.score(g, Personal()).score > 0.5 * fresh


def test_per_league_score_map_is_used_only_for_its_league():
    m = HypeModel.load()
    data = dict(m.data, score_maps={"nhl": {"p": [0.0, 1.0], "s": [0.0, 100.0]}})
    m2 = HypeModel(data)
    assert abs(m2.to_score(0.5, League.NHL) - 50.0) < 1e-6
    assert m2.to_score(0.05, League.NFL) == m.to_score(0.05, League.NFL)


def test_a_league_left_out_of_v2_gets_the_old_score_and_no_floor(cfg, clock):
    cfg.data["score"]["v2"]["leagues"] = ["nfl"]
    s = Scorer(cfg, clock=clock).score(_g(League.NHL, 0, 3, 1, 900, home="WSH"), ME)
    assert s.hype_p is None and s.legacy is None and s.score < cfg.get("score.personal.my_team_floor")


def test_hype_moment_meets_the_moment_model_contract(cfg, clock):
    cfg.data["score"]["v2"]["leagues"] = ["nfl", "nba"]   # off by default (pending unseen nights)
    from redzone.contracts.moment import MomentModel
    from redzone.scoring.hype import HypeMoment
    sc = Scorer(cfg, clock=clock)
    hm = HypeMoment(sc)
    assert isinstance(hm, MomentModel)
    g = _g(League.NBA, 100, 98, 4, 90, wp=0.6, gid="m")
    assert not hm.predict(g, clock.t).available            # not scored yet
    s = sc.score(g, Personal())
    p = hm.predict(g, clock.t)
    assert p.available and p.game_id == "m" and 0 <= p.hotness <= 100 and 0 < p.key_soon_p < 1
    assert p.key_soon_p == s.hype_p and p.horizon_s > 0
    assert not hm.predict(g, clock.t + 60).available      # stale
    hm.forget("m")
    assert not hm.predict(g, clock.t).available
    nhl = _g(League.NHL, 1, 1, 3, 300, gid="h")             # NHL isn't scored by v2 by default
    sc.score(nhl, Personal())
    assert not hm.predict(nhl, clock.t).available
