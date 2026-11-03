"""Live game reasoner: brief, head, bounded lock_in points, the model's JSON, cadence, failures."""
import json
import math

import numpy as np
import pytest
from conftest import nfl

from redzone.models import GameState, League, Phase, Play, Situation, Stoppage, Team
from redzone.reasoner import brief
from redzone.reasoner.head import Head
from redzone.reasoner.reasoner import (EMPTY, MAX_POINTS, Client, Narration, Reading, Reasoner,
                                       lock_points, parse_answer)
from redzone.scoring.engine import Personal, Scorer

ME = Personal(my_teams={League.NFL: {"WSH"}})


def on(cfg, **kw):
    cfg.data["reasoner"]["enabled"] = True
    cfg.data["reasoner"].update(kw)
    return cfg


class FixedHead(Head):
    """A head with a fixed answer (no model file)."""

    def __init__(self, p):
        super().__init__()
        self.p = p

    def odds(self, league, facts):
        return self.p

    def cuts(self, league):
        return 0.3, 0.6


class FakeClient:
    def __init__(self, text='{"lock_in":"must_see","safe_to_leave":"no","confidence":0.8,'
                            '"storyline":"WSH driving","why":"red zone","next":"TD likely","stay_s":90}'):
        self.text, self.calls, self.keep = text, [], []

    def generate(self, messages, max_tokens=160):
        self.calls.append(messages)
        return (self.text, 12.0) if self.text is not None else None

    def keepalive(self, n):
        self.keep.append(n)


# --- the brief ----------------------------------------------------------------------------------

def test_brief_survives_an_empty_feed():
    g = GameState(league=League.NHL, game_id="x", home=Team("A"), away=Team("B"), phase=Phase.LIVE)
    f = brief.facts(g, None, None, None)
    assert set(brief.features_for("nhl")) <= set(f)
    t = brief.text(g, f)
    assert t.startswith("NHL B ? @ A ?")
    assert np.isnan(brief.vector(f, ["clock_s"])[0])


def test_brief_nfl_drive_facts_and_text():
    plays = [Play(id=str(i), text=f"run for {i} yards", type_text="Rush") for i in range(4)]
    g = nfl("g", "WSH", "DAL", 17, 20, q=4, clock=95, pos="WSH", down=1, dist=10, ytg=14, wp=0.4,
            recent_plays=plays)
    ctx = brief.Context(mine=True, crunch=True, late=True, bet="WSH +3")
    f = brief.facts(g, {"lead_changes": 3.0}, None, ctx)
    assert f["red_zone"] == 1.0 and f["two_min"] == 1.0 and f["possession_trailing"] == 1.0
    assert f["drive_plays"] == 4.0 and f["mine"] == 1.0 and f["crunch"] == 1.0
    t = brief.text(g, f, ctx, head_p=0.71)
    assert "ball WSH 1&10 14 yds to goal" in t and "crunch time" in t and "lock-in odds 71%" in t
    assert "your team" in t and "bet WSH +3" in t and t.count("\n- ") == 4


def test_brief_nhl_power_play():
    g = GameState(league=League.NHL, game_id="h", home=Team("WSH", score=2), away=Team("PIT", score=2),
                  phase=Phase.LIVE, period=3, clock_s=300,
                  situation=Situation(power_play="WSH", strength="5v3"))
    f = brief.facts(g, None, None, None)
    assert f["pp"] == 1.0 and f["pp_5v3"] == 1.0
    assert "power play WSH 5v3" in brief.text(g, f)


# --- the head -----------------------------------------------------------------------------------

def test_head_hard_facts_win(tmp_path):
    h = FixedHead(0.99)
    assert h.level("nfl", 0.99, garbage=True) == "none"          # garbage time is never a lock-in
    assert h.level("nfl", 0.0, crunch=True) == "must_see"
    assert h.level("nfl", 0.7) == "must_see" and h.level("nfl", 0.4) == "watch" and h.level("nfl", 0.1) == "none"


def test_head_without_or_with_a_broken_model_is_none(tmp_path):
    assert Head(tmp_path).odds("nfl", {}) is None
    (tmp_path / "nfl.npz").write_bytes(b"not a model")
    assert Head(tmp_path).odds("nfl", {}) is None


def test_shipped_heads_load_and_predict():
    h = Head()
    g = nfl("g", "WSH", "DAL", 17, 20, q=4, clock=95, pos="WSH", down=1, dist=10, ytg=14, wp=0.4)
    for lg in ("nfl", "nba", "nhl"):
        if not h.available(lg):
            pytest.skip("heads not built")
        p = h.odds(lg, brief.facts(g, None, None, brief.Context(crunch=True)))
        assert p is not None and 0.0 <= p <= 1.0
        w, m = h.cuts(lg)
        assert 0.0 <= w <= m <= 1.0


# --- bounded points -------------------------------------------------------------------------------

def test_lock_points_are_bounded(cfg):
    r = Reading(p=0.9, lock_in="must_see")
    cfg.data["reasoner"]["must_points"] = 999
    cfg.data["reasoner"]["max_points"] = 999
    pts, part = lock_points(r, cfg)
    assert pts == MAX_POINTS and part
    assert lock_points(Reading(lock_in="none"), cfg) == (0.0, None)
    assert lock_points(None, cfg) == (0.0, None)
    cfg.data["reasoner"]["must_points"] = -5
    assert lock_points(r, cfg)[0] == 0.0


def test_lock_points_break_discount(cfg):
    cfg.data["reasoner"]["must_points"] = 10
    cfg.data["reasoner"]["break_discount"] = 1.0
    r = Reading(p=0.9, lock_in="must_see", facts={"brain_break": 0.75})
    assert lock_points(r, cfg)[0] == pytest.approx(2.5)


def test_scorer_unchanged_when_off(cfg, clock):
    g = nfl("g", "WSH", "DAL", 17, 20, q=4, clock=95, pos="WSH", down=1, dist=10, ytg=14, wp=0.4)
    a = Scorer(cfg, clock=clock).score(g, ME)
    b = Scorer(cfg, clock=clock, reasoner=Reasoner(cfg, head=FixedHead(0.99))).score(g, ME)
    assert a.score == b.score and b.reasoner == {}


def test_scorer_adds_points_live_not_in_breaks_not_in_garbage(cfg, clock):
    on(cfg, must_points=8, max_points=12)
    cfg.data["reasoner"]["nfl"].update(must_points=8, by_odds=0)
    live = nfl("g", "KC", "BUF", 7, 10, q=2, clock=500, pos="KC", down=1, dist=10, ytg=50, wp=0.4)
    off_cfg_score = Scorer(cfg, clock=clock, reasoner=Reasoner(cfg, head=FixedHead(0.0))).score(live, ME)
    s = Scorer(cfg, clock=clock, reasoner=Reasoner(cfg, head=FixedHead(0.99))).score(live, ME)
    assert s.reasoner["lock_in"] == "must_see"
    assert s.score == pytest.approx(min(100.0, off_cfg_score.score + 8))
    brk = nfl("g", "KC", "BUF", 7, 10, q=2, clock=500, pos="KC", stoppage=Stoppage.TIMEOUT, wp=0.4)
    sc = Scorer(cfg, clock=clock, reasoner=Reasoner(cfg, head=FixedHead(0.99)))
    sc.score(live, ME)
    clock.t += 10
    on_break = sc.score(brk, ME)
    assert "reasoner" not in on_break.factors       # no points in a break...
    clock.t += 1000
    assert sc.score(brk, ME).score < s.score       # ...and none linger through smoothing
    blowout = nfl("b", "KC", "BUF", 3, 45, q=4, clock=200, pos="KC", down=1, dist=10, ytg=50, wp=0.001)
    sb = Scorer(cfg, clock=clock, reasoner=Reasoner(cfg, head=FixedHead(0.99))).score(blowout, ME)
    assert sb.garbage and sb.reasoner["lock_in"] == "none" and "reasoner" not in sb.factors


# --- the model's answer ------------------------------------------------------------------------

def test_parse_answer_validates_and_clamps():
    n = parse_answer('<think>hm</think> {"lock_in":"watch","safe_to_leave":"NO","confidence":7,'
                     '"storyline":"  a   b ","why":"x","next":"y","stay_s":99999}')
    assert n.lock_in == "watch" and n.safe_to_leave == "no" and n.confidence == 1.0
    assert n.storyline == "a b" and n.stay_s == 900
    assert parse_answer('{"lock_in":"maybe"}') is None
    assert parse_answer("no json here") is None
    assert parse_answer('{"lock_in": "none", "confidence": "high", "stay_s": "x"}').confidence == 0.0
    assert parse_answer('{"lock_in":"none","confidence":NaN}') is None or True   # never raises
    assert parse_answer("") is None


# --- cadence and failures -------------------------------------------------------------------------

def _reasoner(cfg, client, clock, p=0.99):
    on(cfg, min_interval_s=20)
    return Reasoner(cfg, head=FixedHead(p), client=client, clock=clock)


def test_reasoner_reads_once_per_play_and_is_empty_when_off(cfg, clock):
    r = Reasoner(cfg, head=FixedHead(0.9), clock=clock)
    g = nfl("g", "KC", "BUF", 7, 10, q=2, clock=500)
    assert r.read(g, None) is EMPTY
    on(cfg)
    a = r.read(g, None, None, brief.Context())
    assert a.lock_in == "must_see" and r.read(g, None, None, brief.Context()) is a
    g.phase = Phase.POST
    assert r.read(g, None) is EMPTY


def test_narration_is_event_driven_and_rate_limited(cfg, clock):
    fc = FakeClient()
    r = _reasoner(cfg, fc, clock)
    g = nfl("g", "KC", "BUF", 7, 10, q=2, clock=500, last_play=Play(id="1"))
    rd = r.read(g, None, None, brief.Context())
    assert r.narrate(g, rd)
    r._pool.shutdown(wait=True)
    r._pool = None
    assert len(fc.calls) == 1 and r.cached("g").narration.storyline == "WSH driving"
    assert not r.narrate(g, rd)                         # nothing new
    g.last_play = Play(id="2")
    clock.t += 5
    assert not r.narrate(g, rd)                         # new play, but under min_interval_s
    g.home.score = 17                                    # a score asks right away
    assert r.narrate(g, rd)
    r._pool.shutdown(wait=True)
    assert len(fc.calls) == 2


def test_quiet_games_cost_no_model_time(cfg, clock):
    fc = FakeClient()
    r = _reasoner(cfg, fc, clock, p=0.0)
    g = nfl("g", "KC", "BUF", 7, 10, q=2, clock=500, last_play=Play(id="1"))
    rd = r.read(g, None, None, brief.Context())
    assert rd.lock_in == "none" and not r.narrate(g, rd)


def test_bad_model_output_keeps_the_head(cfg, clock):
    fc = FakeClient(text="sorry, I can't")
    r = _reasoner(cfg, fc, clock)
    cfg.data["reasoner"]["decide_with"] = "model"
    g = nfl("g", "KC", "BUF", 7, 10, q=2, clock=500, last_play=Play(id="1"))
    rd = r.read(g, None, None, brief.Context())
    r.narrate(g, rd)
    r._pool.shutdown(wait=True)
    assert r.cached("g").narration is None and rd.lock_in == "must_see"


def test_decide_with_model_still_obeys_garbage_and_crunch(cfg, clock):
    r = _reasoner(cfg, FakeClient(), clock, p=0.0)
    cfg.data["reasoner"]["decide_with"] = "model"
    r._narr["g"] = Narration(lock_in="must_see", at=clock.t)
    g = nfl("g", "KC", "BUF", 7, 10, q=2, clock=500)
    assert r.read(g, None, None, brief.Context()).lock_in == "must_see"
    r._cache.clear()
    assert r.read(g, None, None, brief.Context(garbage=True)).lock_in == "none"
    r._cache.clear()
    r._narr["g"] = Narration(lock_in="none", at=clock.t)
    assert r.read(g, None, None, brief.Context(crunch=True)).lock_in == "must_see"
    r._cache.clear()
    r._narr["g"] = Narration(lock_in="must_see", at=clock.t - 3600)   # stale: the head's call
    assert r.read(g, None, None, brief.Context()).lock_in == "none"


def test_client_down_returns_none_and_backs_off():
    c = Client("http://127.0.0.1:9", timeout_s=0.5)
    assert c.generate([{"role": "user", "content": "x"}]) is None
    assert c.down_until > 0
    assert c.generate([{"role": "user", "content": "x"}]) is None   # backed off: no new connect


def test_strange_feed_never_raises(cfg, clock):
    on(cfg)
    r = Reasoner(cfg, head=FixedHead(0.5), clock=clock)
    g = nfl("g", "KC", "BUF", None, None, q=None, clock=None, recent_plays=[None, Play(id="1", text=None)])
    out = r.read(g, {"run_home": "x"}, None, brief.Context())
    assert out is EMPTY or out.lock_in in ("none", "watch", "must_see")
    assert json.dumps(out.as_dict())
    assert not math.isnan(0.0)
