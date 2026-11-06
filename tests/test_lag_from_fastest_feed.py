"""Nov 4-5 (Mac plays.jsonl): nba_cdn lag p50 12 s, ESPN's copies of the same plays 37-170 s. Every
copy was a new play to the timeline, so lag_est (max of the last 12 samples) followed ESPN and the
NBA leave gate's 'covered' path was dead even while nba_cdn owned the game.
decider.lag_from_fastest_feed: the fastest FRESH source's own worst case."""
from redzone.decider.decider import Decider
from redzone.decider.screen import DelayBook, Timeline
from redzone.models import GameState, League, Phase, Play, Stoppage, Team


def g(plays):
    return GameState(league=League.NBA, game_id="g", home=Team("GS", score=0), away=Team("MIA", score=0),
                     phase=Phase.LIVE, period=1, clock_s=600.0, stoppage=Stoppage.NONE,
                     recent_plays=list(plays), last_play=plays[-1])


def feed(tl, clock, n=8, fast=12.0, slow=150.0, slow_too=True):
    for i in range(n):
        clock.t += 15
        ps = [Play(f"n{clock.t}", "jump_shot", "jump_shot", False, clock.t - fast, source="nba_cdn")]
        if slow_too:
            ps.append(Play(f"e{clock.t}", "jump_shot", "jump_shot", False, clock.t - slow, source="espn_summary"))
        tl.observe(g(ps))


def tl_(clock, on):
    tl = Timeline(League.NBA, clock)
    tl.lag_by_source, tl.lag_source_fresh_s = on, 120.0
    tl.observe(g([Play("first", "jump_shot", "jump_shot", False, clock.t - 5, source="nba_cdn")]))
    return tl


def test_off_keeps_the_old_worst_case(clock):
    tl = tl_(clock, False)
    feed(tl, clock)
    assert tl.lag_est() >= 120


def test_on_uses_the_fast_feed(clock):
    tl = tl_(clock, True)
    feed(tl, clock)
    assert tl.lag_est() <= 30


def test_fast_feed_gone_quiet_falls_back_to_the_slow_one(clock):
    tl = tl_(clock, True)
    feed(tl, clock)
    for i in range(10):          # nba_cdn silent for 150 s, ESPN keeps coming
        clock.t += 15
        tl.observe(g([Play(f"x{i}", "jump_shot", "jump_shot", False, clock.t - 150, source="espn_summary")]))
    assert tl.lag_est() >= 120


def test_too_few_samples_never_lower_the_estimate(clock):
    tl = tl_(clock, True)
    feed(tl, clock, n=3)         # 3 fast samples: not enough to trust
    assert tl.lag_est() >= 120


def test_decider_sets_it_per_league(cfg, clock):
    cfg.data.setdefault("league_overrides", {}).setdefault("nba", {})["decider.lag_from_fastest_feed"] = True
    dec = Decider(cfg, DelayBook({"youtubetv": 42.0}, sigma_default=15.0, clock=clock), clock=clock)
    assert dec.timeline("a", League.NBA).lag_by_source and dec.timeline("a", League.NBA).lag_source_fresh_s == 120
    assert not dec.timeline("b", League.NHL).lag_by_source
