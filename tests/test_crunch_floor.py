"""The crunch = stay rule floor holds under every candidate (Nick, Nov 4 3:30 PM ET).

The live app's L1 floor sits above the Noggin: while the game on screen is in crunch time and its play is
live, nothing leaves it for a higher-scored game that isn't in crunch. Every named profile, and every
override layer that can change a decision (Pick in replace mode with no keep kinds, the scenario tree,
lookahead), must keep that floor. A promotion candidate that bypasses it fails this test.
"""
import pytest
import yaml

from redzone.decider.decider import Decider
from redzone.decider.screen import DelayBook
from redzone.models import League
from redzone.noggin import profile
from redzone.scoring.engine import Scored
from tests.test_decider import calibrate, cands, start_on, with_fresh_timing

# Layers that get to overrule the generic pick, all at their most aggressive setting
EVERYTHING_ON = [
    "noggin.pick.enabled=true", "noggin.pick.mode=replace", "noggin.pick.keep_kinds=[]",
    "noggin.pick.leagues=[nfl, nba, nhl]", "playbooks.game.enabled=true", "noggin.lookahead.enabled=true",
    "noggin.priority.enabled=true",
]


def _apply(cfg, sets):
    for kv in sets:
        key, _, val = kv.partition("=")
        node = cfg.data
        *path, last = key.split(".")
        for part in path:
            node = node.setdefault(part, {})
        node[last] = yaml.safe_load(val)




def crunch_live(gid, score, personal=0.0):
    """What the engine scores for a one-score game inside its last two minutes, play live."""
    return Scored(gid, League.NFL, score, score, score, 1.0, personal, f"reason {gid}", [], False, "crunch time", None,
                  1.0, crunch=True, crunch_now=True)


LAYERS = {"live default": []}
LAYERS.update({f"profile:{n}": profile.PROFILES[n]["set"] for n in profile.names()})
LAYERS["profile:candidate+everything_on"] = profile.PROFILES["candidate"]["set"] + EVERYTHING_ON


def _run(cfg, clock, sets, scenario):
    _apply(cfg, sets)
    d = Decider(cfg, DelayBook({"youtubetv": 40.0}, sigma_default=15.0, clock=clock), clock=clock)
    with_fresh_timing(d, clock)
    for g in ("a", "b"):
        calibrate(d, f"youtubetv:{g}")
    start_on(d, clock, "a", {})
    d.timeline("b", League.NFL)
    out = []
    for _ in range(5):
        clock.t += 15
        out.append(d.decide(scenario()))
    return out


# On screen: crunch time, play live. Elsewhere: a strong game that isn't in crunch.
SCENARIOS = {
    "neutral crunch vs a strong game": lambda: cands(a=crunch_live("a", 90), b=85),
    "your-team crunch vs a better-scored game": lambda: cands(a=crunch_live("a", 70, personal=22.5), b=89),
}


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
@pytest.mark.parametrize("name", sorted(LAYERS))
def test_no_candidate_leaves_live_crunch_where_the_live_default_stays(tmp_path, clock, name, scenario):
    from redzone.config import Config

    base = _run(Config(user_path=tmp_path / "a.yaml", history_dir=tmp_path / "ha"), clock, [], SCENARIOS[scenario])
    assert all(x.action == "stay" for x in base), "the live default itself must hold this crunch"
    got = _run(Config(user_path=tmp_path / "b.yaml", history_dir=tmp_path / "hb"), clock, LAYERS[name],
               SCENARIOS[scenario])
    left = [x for x in got if x.action == "switch" and x.target == "b"]
    assert not left, f"{name} bypassed the crunch floor: {left[0].reason}"


def test_pick_in_replace_mode_never_overrules_a_crunch_window(cfg, clock, monkeypatch):
    """Pick is the Noggin layer that can overrule the app. Even forced to want the other game, with no keep
    kinds, it hands back the app's decision while the game on screen is in its crunch window."""
    from types import SimpleNamespace

    from redzone.noggin import pick as PK

    _apply(cfg, EVERYTHING_ON + ["noggin.pick.tune_s=0"])
    d = Decider(cfg, DelayBook({"youtubetv": 40.0}, sigma_default=15.0, clock=clock), clock=clock)
    with_fresh_timing(d, clock)
    for g in ("a", "b"):
        calibrate(d, f"youtubetv:{g}")
    start_on(d, clock, "a", {})
    monkeypatch.setattr(PK, "model_for", lambda lg: object())
    monkeypatch.setattr(PK, "FEED_LAG_P90", {League.NFL: -999.0})
    monkeypatch.setattr(PK, "live_choice", lambda *a, **k: ("b", {"a": 0.0, "b": 1.0}))
    for g in ("a", "b"):
        d.timeline(g, League.NFL).live = SimpleNamespace(league=League.NFL, gid=g, matchup="X @ Y")
    c = cands(a=crunch_live("a", 90), b=85)
    stay = d.decide(c)
    assert stay.action == "stay"
    assert d._pick(stay, c, clock.t) is stay
    # the guard is what holds it: off crunch, the same forced Pick does act
    calls = []
    monkeypatch.setattr(PK, "live_choice", lambda *a, **k: calls.append(1) or ("b", {"a": 0.0, "b": 1.0}))
    out = d._pick(stay, cands(a=85, b=85), clock.t)
    assert calls and (out.target == "b" or out.want == "b")
