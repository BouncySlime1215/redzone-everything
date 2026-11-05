"""[EXP-056] Planner v1's decider interface: shadow only, never moves the TV."""
import pytest

from redzone.decider.decider import Candidate, Decider
from redzone.decider.screen import DelayBook
from redzone.models import League
from redzone.noggin import planner as P
from redzone.scoring.engine import Scored


def _c(gid, score, crunch=False):
    s = Scored(gid, League.NBA, score, score, score, 1.0, 0.0, f"r {gid}", [], False, None, None, 1.0, crunch=crunch)
    return Candidate(s, f"youtubetv:{gid}")


def test_plan_rejects_unknown_actions():
    with pytest.raises(ValueError):
        P.Plan("teleport")


def test_greedy_switches_only_past_its_margin():
    o = lambda g, sc, **k: P.OptionView(g, "nba", f"yt:{g}", sc, **k)   # noqa: E731
    inp = P.PlanInput(0.0, "a", None, (o("a", 50), o("b", 55)))
    assert P.Greedy(10).plan(inp).action == "stay"
    inp = P.PlanInput(0.0, "a", None, (o("a", 50), o("b", 70), o("c", 90, entry_ok=False)))
    p = P.Greedy(10).plan(inp)
    assert p.action == "switch" and p.target == "b"   # never a landing the entry plan rules out


def test_shadow_logs_every_tick_and_never_changes_the_decision(cfg):
    a, b = Decider(cfg, DelayBook({"youtubetv": 40.0}, 3.0)), Decider(cfg, DelayBook({"youtubetv": 40.0}, 3.0))
    cands = {"a": _c("a", 40), "b": _c("b", 90)}
    plain = a.decide(dict(cands))

    class Contrarian:
        def plan(self, inp):
            return P.Plan("switch", "zzz", 99.0, "always somewhere else")
    b.planner = Contrarian()
    cfg.data.setdefault("noggin", {}).setdefault("planner", {})["mode"] = "shadow"
    shadow = b.decide(dict(cands))
    assert (shadow.action, shadow.target) == (plain.action, plain.target)
    assert len(b.planner_log.rows) == 1 and b.planner_log.rows[0]["plan"]["target"] == "zzz"


def test_a_crashing_planner_is_no_opinion(cfg):
    d = Decider(cfg, DelayBook({"youtubetv": 40.0}, 3.0))

    class Broken:
        def plan(self, inp):
            raise RuntimeError("boom")
    d.planner = Broken()
    cfg.data.setdefault("noggin", {}).setdefault("planner", {})["mode"] = "shadow"
    d.decide({"a": _c("a", 40)})
    assert d.planner_log.rows[0]["plan"] is None and d.planner_log.agreement() is None


def test_off_by_default(cfg):
    d = Decider(cfg, DelayBook({"youtubetv": 40.0}, 3.0))
    d.planner = P.Greedy()
    d.decide({"a": _c("a", 40)})
    assert cfg.get("noggin.planner.mode") == "off" and d.planner_log is None
