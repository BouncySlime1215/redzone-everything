"""[WP-15] Mutate every commit-gate rule (and the Flip facts they read); a named check must notice each change.

Beyond test_commit_gate's rule removal this goes inside each rule: a dropped guard, a flipped comparison, a changed
bar. A mutant nothing catches is a rule no test really holds, so a survivor fails here. Equivalent mutants are listed
with why; a listed one that gets killed fails too, so the list can't rot.

Why: Rule #1 rests on these rules; each must be proven to matter (build plan WP-15, brain-vs-code "mutation test").
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from test_commit_gate import AT, CASES, T, _airing, _Cand, _d, _held, _host, _set, _timed
from test_rule1_review import _plan

from redzone.decider import gate
from redzone.decider.decider import REDZONE, REEL, Candidate
from redzone.models import Phase
from tests.adversarial import mutate as M

SOURCE = Path(gate.__file__)
RULES = {fn.__name__ for _, fn in gate.GATE_RULES}
GATE_ORDER = ["paused_or_dry_run", "deferred", "replay_used", "redzone_off", "reel_missing", "not_live", "feed_stale",
              "target_moved", "no_timeline", "chase_gone", "landing", "landing_unproven", "rewind_changed", "arrival_break",
              "hold", "ending_airing", "replay_exit", "leave", "landing_whistle"]


def _ready(m):
    """The screen checks say yes unless a check says otherwise, as in test_commit_gate."""
    m.break_on_arrival, m.catchable = (lambda *a: False), (lambda *a: object())
    m.redzone_on = lambda games: True
    return m


def _cands():
    return {g: _Cand(NS(g=g), f"youtubetv:{g}") for g in ("a", "b")}


def V(m, h=None, *, before="a", tuner=T, cands=None, rx=None, now=AT, **kw):
    """The veto for one flip, on a host where everything says yes unless `h` was tweaked. `target` in kw is the game."""
    return m.commit_gate(h or _host(), _d(**kw), before, tuner, _cands() if cands is None else cands, rx, now=now)


def h_with(fn):
    h = _host()
    fn(h)
    return h


def k_order(m):
    assert [n for n, _ in m.GATE_RULES] == GATE_ORDER
    assert [n for n, _ in m.ENTRY_RULES] == GATE_ORDER[2:14]


def k_every_rule_names_itself(m):
    _ready(m)
    for name, (tweak, kw, target, rx) in CASES.items():
        h = _host()
        _ready(m)
        if tweak == "catchable":
            m.catchable = lambda *a: None
        elif tweak == "break":
            m.break_on_arrival = lambda *a: True
        elif name == "redzone_off":
            m.redzone_on = lambda games: False
        elif tweak is not None:
            tweak(h)
        assert V(m, h, tuner=target, rx=rx, **kw) == name, name
    _ready(m)


def k_clean(m):
    _ready(m)
    for kw in ({}, {"kind": "jump"}, {"kind": "chase"}):
        assert V(m, **kw) == ""
    assert V(m, before=None) == "" and V(m, before=REDZONE) == ""


def k_paused_or_dry_run(m):
    _ready(m)
    assert V(m, _host(**{"mode.dry_run": True})) == "paused_or_dry_run"
    assert V(m, h_with(lambda h: _set(h.decider.user, paused=True))) == "paused_or_dry_run"
    h = _host()
    h.cfg = NS(get=lambda k, default=None: default)   # nothing configured: the safe answer is dry run
    assert V(m, h) == "paused_or_dry_run"


def k_deferred(m):
    _ready(m)
    assert V(m, defer_s=5.0, now=AT + 4.9) == "deferred"
    assert V(m, defer_s=5.0, now=AT + 5.0) == ""
    assert V(m, defer_s=0.0) == "" and V(m, defer_s=None) == ""
    bare = NS(at=AT, action="switch", target="b", kind="better", jump=None, rewind_s=0.0)   # an object with no defer_s
    assert m.commit_gate(_host(), bare, "a", T, _cands(), None, now=AT) == ""


def k_replay_used(m):
    _ready(m)
    assert V(m, action="replay", replay=None) == "replay_used"
    assert V(m, action="replay", replay=NS(replayed=True)) == "replay_used"
    assert V(m, action="replay", replay=NS(replayed=False)) == ""
    assert V(m, action="switch", replay=NS(replayed=True)) == ""


def k_redzone_off(m):
    _ready(m)
    m.redzone_on = lambda games: False
    assert V(m, target=REDZONE) == "redzone_off"
    assert V(m, action="replay", replay=NS(replayed=False), target=REDZONE) == ""
    assert V(m) == ""
    m.redzone_on = lambda games: True
    assert V(m, target=REDZONE) == ""


def k_reel(m, tmp=Path("/nonexistent-rz")):
    _ready(m)
    m.REEL_DIR = _reel_dir(with_file=True)
    assert V(m, target=REEL, **{}) == "reel_missing"   # target "youtubetv:b" isn't a reel target
    assert m.commit_gate(_host(), _d(target=REEL), "a", "reel:x", _cands(), None, now=AT) == ""
    assert m.commit_gate(_host(), _d(target=REEL), "a", None, _cands(), None, now=AT) == "reel_missing"
    m.REEL_DIR = _reel_dir(with_file=False)
    assert m.commit_gate(_host(), _d(target=REEL), "a", "reel:x", _cands(), None, now=AT) == "reel_missing"
    assert m.commit_gate(_host(), _d(action="replay", replay=NS(replayed=False), target=REEL), "a", "nope", _cands(), None, now=AT) == ""


class _ReelFile:
    def __init__(self, exists):
        self._e = exists

    def exists(self):
        return self._e


class _ReelDir:
    def __init__(self, exists):
        self._e = exists

    def __truediv__(self, name):
        assert name == "reel.mp4"
        return _ReelFile(self._e)


def _reel_dir(with_file):
    return _ReelDir(with_file)


def k_not_live(m):
    _ready(m)
    assert V(m, h_with(lambda h: h.merger.games.pop("b"))) == "not_live"
    assert V(m, h_with(lambda h: _set(h.merger.games["b"], phase=Phase.PRE))) == "not_live"
    assert V(m, h_with(lambda h: _set(h.merger.games["b"], phase=Phase.POST))) == "not_live"
    # a replay has its own rule: the game behind it may be over
    assert V(m, h_with(lambda h: _set(h.merger.games["b"], phase=Phase.POST)), action="replay", replay=NS(replayed=False)) == ""
    assert V(m, h_with(lambda h: h.merger.games.pop("b")), target=REDZONE) == ""


def k_feed_stale(m):
    _ready(m)
    seen = []
    h = _host()
    h.feeds.stale = lambda g: seen.append(g) or False
    assert V(m, h) == "" and seen == ["b"]
    seen.clear()
    assert V(m, h, target=REDZONE) == "" and seen == []


def k_target_moved(m):
    _ready(m)
    assert V(m, h_with(lambda h: h.where.pop("b"))) == "target_moved"
    assert V(m, h_with(lambda h: _set(h.where["b"], target="prime:b"))) == "target_moved"
    assert V(m, h_with(lambda h: h.where.pop("b")), target=REDZONE) == ""


def k_no_timeline(m):
    _ready(m)
    assert V(m, h_with(lambda h: h.decider.timelines.pop("b"))) == "no_timeline"
    assert V(m, h_with(lambda h: h.decider.timelines.pop("b")), target=REDZONE) == ""
    assert V(m, h_with(lambda h: h.decider.timelines.pop("b")), action="replay", replay=NS(replayed=False)) == ""


def k_chase(m):
    _ready(m)
    args = []
    m.catchable = lambda tl, delay, now, cost: args.append((tl, delay, now, cost)) or object()
    h = _host()
    assert V(m, h, kind="chase") == ""
    assert args[0][0] is h.decider.timelines["b"] and args[0][2:] == (AT, 3.0)
    m.catchable = lambda *a: None
    assert V(m, kind="chase") == "chase_gone"
    assert V(m, kind="better") == ""


def k_landing_jump(m):
    _ready(m)
    args = []
    h = _host()
    h.decider.jump_lands_dead = lambda gid, x, now, cost: args.append((gid, x, now, cost)) or True
    cs = _cands()
    assert V(m, h, cands=cs, kind="jump") == ""
    assert args == [("b", cs["b"], AT, 3.0)]
    assert V(m, h_with(lambda h: _set(h.decider, jump_lands_dead=lambda *a: False)), kind="jump") == "landing"
    assert V(m, h_with(lambda h: _set(h.decider, jump_lands_dead=lambda *a: False)), kind="jump", target=REDZONE) == ""
    assert V(m, h_with(lambda h: _timed(h, None)), kind="jump") == ""     # no plan asked for, so none can be missing
    # a jump's bar is jump_lands_dead, never the entry plan
    assert V(m, h_with(lambda h: _timed(h, _plan(False))), kind="jump") == ""


def k_landing_timed(m):
    _ready(m)
    assert V(m, h_with(lambda h: _timed(h, _plan(False)))) == "landing"
    assert V(m, h_with(lambda h: _timed(h, _plan(True)))) == ""
    assert V(m, h_with(lambda h: _timed(h, None))) == "landing_unproven"
    h = _host(**{"decider.entry.enabled": False})
    _timed(h, None)
    assert V(m, h) == ""
    h = _host()
    h.cfg = NS(get=lambda k, default=None: False if k == "mode.dry_run" else default)   # unset: on, the safe answer
    _timed(h, None)
    assert V(m, h) == "landing_unproven"
    assert V(m, h_with(lambda h: _set(h.decider, entry_plan=lambda *a, **k: _plan(False)))) == ""   # not timed: no plan asked


def _entry_only(h, plan=None):
    h.decider._entry_only = lambda *a: True
    h.decider.entry_plan = lambda *a, **k: _plan(False) if plan is None else plan


def k_entry_only(m):
    _ready(m)
    assert V(m, h_with(_entry_only)) == "landing"
    for exempt in ("resume", "skip"):
        assert V(m, h_with(_entry_only), kind=exempt) == ""
    assert V(m, h_with(lambda h: (_entry_only(h), _set(h.control, _scrub_wanted=lambda t: True))[0])) == ""
    h = _host()
    _entry_only(h)
    timed_called = []
    h.decider._cut_timed = lambda gid, c: timed_called.append(gid) or False
    assert V(m, h, kind="user") == "" and timed_called == []
    assert V(m, h_with(_entry_only), target=REDZONE) == ""


def _capture_plan(h, plan):
    args = {}
    h.decider.entry_plan = lambda gid, one, now, cost, urgent=False, possession=False: args.update(
        gid=gid, one=one, now=now, cost=cost, urgent=urgent, possession=possession) or plan
    return args


def k_plan_args(m):
    _ready(m)
    h = _host()
    h.scored = {"b": NS(g="now")}
    h.decider._cut_timed = lambda *a: True
    args = _capture_plan(h, _plan(True))
    assert V(m, h) == ""
    assert (args["gid"], args["now"], args["cost"], args["urgent"], args["possession"]) == ("b", AT, 3.0, False, False)
    assert isinstance(args["one"]["b"], Candidate) and args["one"]["b"].scored.g == "now" and args["one"]["b"].target == T
    h = _host()
    h.decider._cut_timed = lambda *a: True
    args = _capture_plan(h, _plan(True))
    assert V(m, h, kind="crunch") == "" and args["urgent"] is True
    h = _host()
    h.decider._entry_only = lambda *a: True
    args = _capture_plan(h, _plan(True))
    assert V(m, h) == "" and (args["urgent"], args["possession"]) == (False, False)
    h = _host()
    h.decider._entry_only = lambda *a: True
    h.decider._entry_urgent = lambda *a: True
    args = _capture_plan(h, _plan(True))
    assert V(m, h) == "" and args["urgent"] is True
    h = _host()
    h.decider._entry_only = lambda *a: True
    h.decider._possession_start = lambda *a: True
    args = _capture_plan(h, _plan(True))
    assert V(m, h) == "" and args["urgent"] is True and args["possession"] is True
    h = _host()   # a timed league: possession start is the entry-only path's idea
    h.decider._cut_timed = lambda *a: True
    h.decider._entry_only = lambda *a: True
    h.decider._possession_start = lambda *a: True
    h.decider._entry_urgent = lambda *a: True
    args = _capture_plan(h, _plan(True))
    assert V(m, h) == "" and (args["urgent"], args["possession"]) == (False, False)


def k_rewind(m):
    _ready(m)
    plan30 = lambda: h_with(lambda h: _timed(h, _plan(True, 30.0)))  # noqa: E731
    assert V(m, plan30(), rewind_s=30.0) == ""
    assert V(m, plan30(), rewind_s=0.0) == "rewind_changed"
    bare = NS(at=AT, action="switch", target="b", kind="better", jump=None, defer_s=0.0)   # no rewind_s: means 0
    assert m.commit_gate(h_with(lambda h: _timed(h, _plan(True, 0.0))), bare, "a", T, _cands(), None, now=AT) == ""
    assert m.commit_gate(plan30(), bare, "a", T, _cands(), None, now=AT) == "rewind_changed"
    h = h_with(lambda h: _timed(h, _plan(False, 30.0)))
    assert V(m, h, rewind_s=0.0) == "landing"   # a failed plan is the landing's veto


def k_arrival_break(m):
    _ready(m)
    args = []
    m.break_on_arrival = lambda tl, d, a, b: args.append((tl, d, a, b)) or True
    h = _host()
    assert V(m, h) == "arrival_break"
    assert args[0][0] is h.decider.timelines["b"] and args[0][2:] == (AT, AT + 3.0)
    assert V(m, kind="jump") == ""
    assert V(m, target=REDZONE) == ""
    assert V(m, h_with(lambda h: _timed(h, _plan(True, 30.0))), rewind_s=30.0) == ""    # a planned rewind is exempt
    assert V(m, h_with(lambda h: _timed(h, _plan(True, 0.0))), rewind_s=0.0) == "arrival_break"


def k_hold(m):
    _ready(m)
    assert V(m, h_with(_held), kind="jump") == "hold"
    assert V(m, h_with(_held), kind="better") == ""     # held at the decision too: it decided that on purpose
    assert V(m, h_with(_held), kind="jump", before=None) == ""
    assert V(m, h_with(_held), kind="jump", before="ghost") == ""     # not a candidate: nothing to hold
    assert V(m, h_with(_held), kind="jump", action="replay", replay=NS(replayed=False)) == ""
    h = _host()
    h.decider._jump_hold = lambda x: "crunch" if x.scored.g == "fresh" else None
    h.scored = {"a": NS(g="fresh")}
    assert V(m, h) == "hold"            # turned crunch during the wait
    h2 = _host()
    h2.decider._jump_hold = lambda x: "crunch" if x.scored.g == "old" else None
    h2.scored = {"a": NS(g="fresh")}
    assert V(m, h2) == ""               # judged on the scores of now


def k_ending_airing(m):
    _ready(m)
    assert V(m, h_with(_airing)) == "ending_airing" and V(m, h_with(_airing), kind="jump") == "ending_airing"
    h = h_with(lambda h: (_airing(h), h.merger.games.pop("a"))[0])
    assert V(m, h) == "ending_airing"       # the game left the merger: its last play can still be airing
    assert V(m, h_with(_airing), before=None) == "" and V(m, h_with(_airing), before=REDZONE) == ""
    assert V(m, h_with(_airing), action="replay", replay=NS(replayed=False)) == ""
    assert V(m, h_with(_airing), rx=AT - 1.0) == ""     # coming off a replay: that rule's
    h = _host()
    h.decider._ending_still_airing = lambda *a: 12.0
    assert V(m, h) == ""                    # a live game isn't held for its last event
    h = h_with(_airing)
    h.decider._ending_still_airing = lambda *a: 0.0
    assert V(m, h) == ""
    got = {}
    h = h_with(_airing)
    h.decider._ending_still_airing = lambda g, now: got.update(g=g, now=now) or 1.0
    V(m, h)
    assert got == {"g": "a", "now": AT}


def k_replay_exit(m):
    _ready(m)
    assert V(m, rx=AT + 0.1) == "replay_exit"
    assert V(m, rx=AT) == "" and V(m, rx=AT - 5.0) == ""


def k_leave(m):
    _ready(m)
    seen = {}
    h = _host()
    h.safe_to_leave = lambda gid, required_s=0.0: seen.update(gid=gid, req=required_s) or True
    assert V(m, h) == "" and seen == {"gid": "a", "req": 8.0}
    never = lambda h: _set(h, safe_to_leave=lambda gid, required_s=0.0: False)  # noqa: E731
    assert V(m, h_with(never)) == "leave"
    assert V(m, h_with(never), rx=AT - 1.0) == ""     # off a replay: replay_exit's call
    seen = {}
    h = _host()
    h.scored = {"a": NS(g="fresh")}
    cs = _cands()
    h.decider.jump_may_leave = lambda gid, live, now, req: seen.update(gid=gid, live=live, now=now, req=req) or True
    assert V(m, h, cands=cs, kind="jump") == ""
    assert seen["gid"] == "a" and seen["now"] == AT and seen["req"] == 3.2 and seen["live"]["a"].scored.g == "fresh"
    no_jump = lambda h: _set(h.decider, jump_may_leave=lambda *a: False)  # noqa: E731
    assert V(m, h_with(no_jump), kind="jump") == "leave"
    assert V(m, h_with(no_jump), kind="jump", rx=AT - 1.0) == ""
    assert V(m, h_with(no_jump)) == ""                # not a jump: the other bar


def k_landing_whistle(m):
    _ready(m)
    no = lambda h: _set(h, _flip_lands_whistle=lambda d, t: False)  # noqa: E731
    assert V(m, h_with(no)) == "landing_whistle"
    assert V(m, h_with(no), kind="jump") == ""
    got = []
    h = _host()
    h._flip_lands_whistle = lambda d, t: got.append((d.target, t)) or True
    assert V(m, h) == "" and got == [("b", T)]


def k_commit_gate_records(m):
    _ready(m)
    h = _host()
    h.control.deadline_cost = lambda t: 3.14
    h.feeds.stale = lambda g: True
    d = _d(kind="jump")
    d.jump = {}
    assert m.commit_gate(h, d, "a", T, {}, None, now=AT) == "feed_stale"
    assert d.jump == {"veto": "feed_stale", "veto_cost_s": 3.1}
    d = _d(kind="jump")
    assert m.commit_gate(_host(), d, "a", T, {}, None, now=AT) == "" and d.jump is None   # no log asked for: no crash
    d = _d(kind="better")
    d.jump = {}
    m.commit_gate(_host(), d, "a", T, {}, None, now=AT)
    assert d.jump == {}                                                                   # only a jump logs here


def k_entry_veto(m):
    _ready(m)
    h = _host()
    h.decider.user.paused = True
    assert m.entry_veto(h, _d(), T, now=AT) == ""         # paused is the gate's, not the destination's
    assert m.entry_veto(_host(), _d(), T, now=AT) == ""
    assert m.entry_veto(h_with(lambda h: _set(h.feeds, stale=lambda g: True)), _d(), T, now=AT) == "feed_stale"
    h = _host()
    h.scored = {"b": NS(g="now")}
    got = {}
    h.decider._cut_timed = lambda gid, one: got.update(one=one) or False
    assert m.entry_veto(h, _d(), T, now=AT) == ""
    assert got["one"]["b"].scored.g == "now" and got["one"]["b"].target == T
    assert m.entry_veto(h_with(lambda h: _set(h, safe_to_leave=lambda gid, required_s=0.0: False)), _d(), T, now=AT) == ""


def k_flip_now(m):
    m.time = NS(time=lambda: 555.0)
    assert m.Flip(_host(), _d(), None, T, {}, None, None).now == 555.0
    assert m.Flip(_host(), _d(), None, T, {}, None, now=0.0).now == 0.0


def k_flip_facts(m):
    h = _host()
    h.merger.games[REDZONE] = NS(game_id=REDZONE, phase=Phase.LIVE, league=None)   # a pseudo target is never a game
    assert m.Flip(h, _d(target=REDZONE), "a", T, {}, None, AT).game is None
    h = _host()
    f = m.Flip(h, _d(), "a", T, {}, None, AT)
    assert f.game is h.merger.games["b"] and f.to_game and f.is_switch and not f.jump
    assert m.Flip(h, _d(kind="jump"), "a", T, {}, None, AT).jump
    assert m.Flip(h, _d(target=REDZONE), "a", T, {}, None, AT).game is None
    assert m.Flip(h, _d(target=REEL), "a", T, {}, None, AT).game is None
    assert not m.Flip(h, _d(target=REDZONE), "a", T, {}, None, AT).to_game
    assert not m.Flip(h, _d(target=REEL), "a", T, {}, None, AT).to_game
    rep = m.Flip(h, _d(action="replay"), "a", T, {}, None, AT)
    assert rep.game is None and not rep.to_game and not rep.is_switch
    assert m.Flip(h, _d(target="ghost"), "a", T, {}, None, AT).game is None
    assert m.Flip(h, _d(target=None), "a", T, {}, None, AT).game is None
    assert m.Flip(h, _d(target="ghost"), "a", T, {}, None, AT).timeline is None
    got = []
    h.delays = NS(get=lambda t: got.append(t) or NS(mu=1.0))
    m.Flip(h, _d(), "a", None, {}, None, AT).delay
    m.Flip(h, _d(), "a", T, {}, None, AT).delay
    assert got == ["unknown", T]
    cost = []
    h.control.deadline_cost = lambda t: cost.append(t) or 7.0
    assert m.Flip(h, _d(), "a", T, {}, None, AT).cost == 7.0 and cost == [T]


def k_rules_read_alone(m):
    """Later rules cover for these guards inside commit_gate; each rule must still be right on its own."""
    _ready(m)
    m.catchable = lambda *a: None
    assert m._chase_gone(m.Flip(h_with(lambda h: h.decider.timelines.pop("b")), _d(kind="chase"), "a", T, {}, None, AT)) is False
    m.break_on_arrival = lambda *a: True
    assert m._arrival_break(m.Flip(h_with(lambda h: h.decider.timelines.pop("b")), _d(), "a", T, {}, None, AT)) is False
    h = _host()
    _timed(h, _plan(False, 30.0))
    assert m._rewind_changed(m.Flip(h, _d(rewind_s=0.0), "a", T, {}, None, AT)) is False
    _ready(m)
    assert V(m, h_with(lambda h: _timed(h, None)), target=REDZONE) == ""   # a pseudo target has no plan to miss


def k_a_short_tail_still_holds(m):
    _ready(m)
    h = h_with(_airing)
    h.decider._ending_still_airing = lambda *a: 0.5
    assert V(m, h) == "ending_airing"


def k_flip_live_scores(m):
    h = _host()
    h.scored = {"a": NS(g="new-a"), "b": NS(g="new-b"), "c": NS(g="new-c")}
    cs = {g: _Cand(NS(g="old"), f"youtubetv:{g}") for g in ("a", "b", "c", "d")}
    live = m.Flip(h, _d(), "a", T, cs, None, AT).live
    assert (live["a"].scored.g, live["b"].scored.g, live["c"].scored.g, live["d"].scored.g) == ("new-a", "new-b", "old", "old")
    assert all(live[g].target == f"youtubetv:{g}" for g in cs)
    assert m.Flip(h, _d(), "a", T, {"d": cs["d"]}, None, AT).live["d"] is cs["d"]


CHECKS = [v for k, v in sorted(globals().items()) if k.startswith("k_") and callable(v)]

# Equivalent mutants: no flip can tell them apart. id -> why.
REPLAN = "replan returns a plan only when timed is true, so `plan is not None` already implies it"
EQUIVALENT = {
    "Flip.game:50:drop_operand_1": "merger.games.get(None) and .get('') are both None: no game has either id",
    "Flip.game:50:change_str": "merger.games.get('_mutated') is None like .get(''): only reached with a falsy target",
    "_landing:148:drop_operand_0": REPLAN,
    "_rewind_changed:160:drop_operand_0": REPLAN,
    "_arrival_break:168:drop_operand_0": REPLAN,
    "_arrival_break:165:drop_operand_0": "timeline is None whenever game is None (Flip.timeline), the same guard's other half",
    "_hold:176:drop_operand_1": "None is never a key of f.live, so `before not in live` already covers it",
    "_hold:181:drop_operand_1": "f.live is built from f.cands: a key of live is always a key of cands",
}


def _all():
    return M.mutants(SOURCE, {"Flip", "commit_gate", "entry_veto", *RULES}, rules=RULES, tables=("ENTRY_RULES", "GATE_RULES"))


MUTANTS = _all()


@pytest.mark.parametrize("check", CHECKS, ids=lambda c: c.__name__)
def test_the_checks_pass_on_the_real_gate(check):
    fresh = M.load(M.Mutant("real", SOURCE.read_text()))
    check(fresh)


@pytest.mark.parametrize("mutant", [x for x in MUTANTS if x.id not in EQUIVALENT], ids=lambda x: x.id)
def test_every_gate_mutant_is_killed(mutant):
    assert M.killed_by(M.load(mutant), CHECKS) is not None, f"survived: {mutant.id}"


@pytest.mark.parametrize("mid", sorted(EQUIVALENT))
def test_listed_equivalent_mutants_still_survive(mid):
    mutant = next(x for x in MUTANTS if x.id == mid)
    assert M.killed_by(M.load(mutant), CHECKS) is None, f"{mid} is killable now: remove it from EQUIVALENT"


def test_mutation_score_is_reported_and_full():
    killed = sum(M.killed_by(M.load(x), CHECKS) is not None for x in MUTANTS)
    total = len(MUTANTS)
    print(f"\nGATE MUTATION SCORE: {killed}/{total} = {100 * killed / total:.1f}% ({len(EQUIVALENT)} listed equivalent)")
    assert killed == total - len(EQUIVALENT)


def test_every_rule_function_has_mutants():
    for name in RULES:
        assert any(x.id.startswith(f"{name}:") for x in MUTANTS), name
