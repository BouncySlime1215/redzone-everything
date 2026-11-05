"""[EXP-209] The delay-and-react arms are offline only: off by default, and react / hybrid only change which
games look switchable (never the screen's own game)."""

from redzone.decider.decider import Candidate, Decider
from redzone.decider.screen import DelayBook
from redzone.models import League

def _c(gid, score, **kw):
    base = dict(game_id=gid, league=League.NBA, score=score, crunch=False, crunch_now=False, lock=None,
                pre_moment=None, imminent=False, high_leverage=False)
    base.update(kw)
    from redzone.scoring.engine import Scored
    s = Scored(gid, League.NBA, score, score, 0, 0, 0, "", [], False, base["lock"], base["pre_moment"], 0.5,
               crunch=base["crunch"], crunch_now=base["crunch_now"], high_leverage=base["high_leverage"])
    return Candidate(s, f"yt:{gid}")

def _dec(cfg, mode, chase=False):
    cfg.data.setdefault("decider", {})["react"] = {"mode": mode, "chase": chase}
    d = Decider(cfg, DelayBook({}, 3.0))
    d.current = "cur"
    return d

def test_off_by_default(cfg):
    d = Decider(cfg, DelayBook({}, 3.0))
    assert cfg.get("decider.react.mode") == "off" and cfg.get("decider.react.chase") is False
    cands = {"cur": _c("cur", 40), "o": _c("o", 80)}
    d.current = "cur"
    assert d._react_filter(cands) is cands and d._react_chase()

def test_react_hides_every_other_game_but_never_the_screens(cfg):
    d = _dec(cfg, "react")
    out = d._react_filter({"cur": _c("cur", 40), "o": _c("o", 80, crunch=True), "p": _c("p", 70, lock="red zone")})
    assert out["cur"].scored.score == 40
    assert out["o"].scored.score < 0 and not out["o"].scored.crunch
    assert out["p"].scored.score < 0 and out["p"].scored.lock is None
    assert not d._react_chase()

def test_hybrid_keeps_situational_predictions(cfg):
    d = _dec(cfg, "hybrid")
    out = d._react_filter({"cur": _c("cur", 40), "o": _c("o", 80, crunch=True), "q": _c("q", 85)})
    assert out["o"].scored.score == 80 and out["o"].scored.crunch
    assert out["q"].scored.score < 0
    assert _dec(cfg, "hybrid", chase=True)._react_chase()

def test_an_ended_screen_game_frees_the_pick(cfg):
    d = _dec(cfg, "react")
    cands = {"o": _c("o", 80)}          # the screen's game is gone from the slate
    assert d._react_filter(cands) is cands
