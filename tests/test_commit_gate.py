"""[WP-05] The commit gate: one named-veto gate at the flip for every non-tap kind (redzone/decider/gate.py).

Three layers: a table with one case per rule and a mutation test over it (remove a rule, its case must fail); the
real Engine behind the flag, with P1-REVIEW-01..03 and -06 re-run through the gate; and a replay of dev nights with the
gate standing where the flip stands (the scorecards don't run the Engine, so the flag alone can't move them).
"""
import contextlib
import dataclasses
import io
import json
import runpy
import sys
import time
from collections import Counter
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from test_app import engine  # noqa: F401  (fixture)
from test_p1_rootcause import _engine
from test_rule1_review import _plan, test_01_resume_and_skip_land_where_they_planned as _resume_check
from test_rule1_review import test_01_the_flip_rechecks_on_the_bar_the_decision_used as _bar_check
from test_rule1_review import test_01_whistle_timed_league_is_replanned_at_the_flip as _replan_check
from test_rule1_review import test_02_one_prime_tap_mid_play_waits as _tap_check
from test_rule1_review import test_02_one_tap_with_no_play_by_play_waits as _tap_blind_check
from test_rule1_review import test_02_second_tap_forces as _tap_second_check
from test_switch_path_audit import _jump, _two_live

from redzone import engine as engine_mod
from redzone.decider import gate
from redzone.decider.decider import REDZONE, REEL, Decider, Decision, State
from redzone.engine import Engine
from redzone.lab import backtest
from redzone.models import League, Phase

ROOT = Path(__file__).resolve().parent.parent
T = "youtubetv:b"
AT = 1000.0
_Cand = dataclasses.make_dataclass("_Cand", ["scored", "target"])


# ----- a host where every rule says yes ----------------------------------------------------------------------------

def _host(**cfg):
    games = {g: NS(game_id=g, phase=Phase.LIVE, league=League.NBA) for g in ("a", "b")}
    dec = NS(user=NS(paused=False), timelines={"a": object(), "b": object()},
             jump_lands_dead=lambda *a: True, jump_may_leave=lambda *a: True, _jump_hold=lambda x: None,
             _ending_still_airing=lambda *a: 0.0, _cut_timed=lambda *a: False, _entry_only=lambda *a: False,
             _possession_start=lambda *a: False, _entry_urgent=lambda *a: False,
             entry_plan=lambda *a, **k: _plan(True))
    vals = {"mode.dry_run": False, "decider.entry.enabled": True, **cfg}
    return NS(cfg=NS(get=lambda k, default=None: vals.get(k, default)), decider=dec,
              merger=NS(games=games), feeds=NS(stale=lambda g: False), where={"b": NS(target=T), "a": NS(target="youtubetv:a")},
              delays=NS(get=lambda t: NS(mu=30.0)), scored={},
              control=NS(deadline_cost=lambda t: 3.0, _scrub_wanted=lambda t: False),
              safe_to_leave=lambda gid, required_s=0.0: True, _leave_margin=lambda: 3.2,
              _flip_lands_whistle=lambda d, t: True)


def _d(kind="better", action="switch", target="b", **kw):
    return Decision(AT, action, target, kind, State.WATCHING, kind=kind, **kw)


@pytest.fixture(autouse=True)
def _screen(request, monkeypatch):
    if request.node.name.startswith("test_replay"):
        return   # the replay runs the real screen checks
    monkeypatch.setattr(gate, "break_on_arrival", lambda *a: False)
    monkeypatch.setattr(gate, "catchable", lambda *a: object())


def _timed(h, plan):
    h.decider._cut_timed = lambda *a: True
    h.decider.entry_plan = lambda *a, **k: plan


def _set(obj, **kw):
    for k, v in kw.items():
        setattr(obj, k, v)


def _airing(h):
    h.merger.games["a"].phase = Phase.POST
    h.decider._ending_still_airing = lambda *a: 12.0


def _held(h):
    h.decider._jump_hold = lambda x: "crunch"


# rule -> (tweak(host), decision kwargs, target, replay_exit, now-offset)
CASES = {
    "paused_or_dry_run": (lambda h: _set(h.decider.user, paused=True), {}, T, None),
    "deferred": (None, {"defer_s": 5.0}, T, None),
    "replay_used": (None, {"action": "replay", "replay": NS(replayed=True)}, T, None),
    "redzone_off": (None, {"target": REDZONE}, "x:rz", None),
    "reel_missing": (None, {"target": REEL}, "nope", None),
    "not_live": (lambda h: _set(h.merger.games["b"], phase=Phase.POST), {}, T, None),
    "feed_stale": (lambda h: _set(h.feeds, stale=lambda g: True), {}, T, None),
    "target_moved": (lambda h: _set(h.where["b"], target="prime:b"), {}, T, None),
    "no_timeline": (lambda h: h.decider.timelines.pop("b"), {}, T, None),
    "chase_gone": ("catchable", {"kind": "chase"}, T, None),
    "landing": (lambda h: _timed(h, _plan(False)), {}, T, None),
    "landing_unproven": (lambda h: _timed(h, None), {}, T, None),
    "rewind_changed": (lambda h: _timed(h, _plan(True, 30.0)), {}, T, None),
    "arrival_break": ("break", {}, T, None),
    "hold": (_held, {"kind": "jump"}, T, None),
    "ending_airing": (_airing, {"kind": "jump"}, T, None),
    "replay_exit": (None, {}, T, AT + 50.0),
    "leave": (lambda h: _set(h, safe_to_leave=lambda gid, required_s=0.0: False), {}, T, None),
    "landing_whistle": (lambda h: _set(h, _flip_lands_whistle=lambda d, t: False), {}, T, None),
}


def _fire(name, monkeypatch):
    tweak, kw, target, rx = CASES[name]
    h = _host()
    if tweak == "catchable":
        monkeypatch.setattr(gate, "catchable", lambda *a: None)
    elif tweak == "break":
        monkeypatch.setattr(gate, "break_on_arrival", lambda *a: True)
    elif tweak is not None:
        tweak(h)
    d = _d(**kw)
    cands = {g: _Cand(NS(g=g), f"youtubetv:{g}") for g in ("a", "b")}
    return gate.commit_gate(h, d, "a", target, cands, rx, now=AT)


def test_every_rule_has_a_case():
    assert {n for n, _ in gate.GATE_RULES} == set(CASES)


def test_a_clean_flip_passes(monkeypatch):
    h = _host()
    cands = {g: _Cand(NS(g=g), f"youtubetv:{g}") for g in ("a", "b")}
    assert gate.commit_gate(h, _d(), "a", T, cands, None, now=AT) == ""


@pytest.mark.parametrize("name", sorted(CASES))
def test_each_rule_names_its_veto(name, monkeypatch):
    assert _fire(name, monkeypatch) == name


@pytest.mark.parametrize("name", sorted(CASES))
def test_mutation_removing_a_rule_fails_its_case(name, monkeypatch):
    """Per-rule mutation: with the rule gone the same flip must no longer be stopped by it, so its case fails."""
    monkeypatch.setattr(gate, "GATE_RULES", tuple(r for r in gate.GATE_RULES if r[0] != name))
    monkeypatch.setattr(gate, "ENTRY_RULES", tuple(r for r in gate.ENTRY_RULES if r[0] != name))
    with pytest.raises(AssertionError):
        assert _fire(name, monkeypatch) == name


@pytest.mark.parametrize("kind", ["better", "crunch", "chase", "drive", "must_see", "home", "return",
                                  "break_visit", "start", "adopt", "live", "resume", "skip", "jump"])
def test_every_non_tap_kind_meets_the_same_rules(kind, monkeypatch):
    """Paths with their own check are how Rule #1 leaked: no kind gets a way around a rule."""
    for rule, tweak in (("not_live", lambda h: _set(h.merger.games["b"], phase=Phase.POST)),
                        ("feed_stale", lambda h: _set(h.feeds, stale=lambda g: True)),
                        ("target_moved", lambda h: _set(h.where["b"], target="prime:b")),
                        ("no_timeline", lambda h: h.decider.timelines.pop("b"))):
        h = _host()
        tweak(h)
        assert gate.commit_gate(h, _d(kind=kind), "a", T, {}, None, now=AT) == rule, rule


def test_ending_game_holds_every_kind(monkeypatch):
    """noggin.md 11b: a switch off an ended game skipped the leave gate. Non-jump used to be caught only in safe_to_leave."""
    for kind in ("jump", "better"):
        h = _host()
        _airing(h)
        cands = {"b": _Cand(NS(g="b"), T)}
        assert gate.commit_gate(h, _d(kind=kind), "a", T, cands, None, now=AT) == "ending_airing"


def test_a_live_game_is_not_held_for_its_last_event_airing():
    """safe_to_leave's 'still airing' is true of nearly every live game: only a game that ended may wait on it."""
    h = _host()
    h.decider._ending_still_airing = lambda *a: 12.0
    assert gate.commit_gate(h, _d(kind="jump"), "a", T, {}, None, now=AT) == ""


def test_hold_stops_a_non_jump_only_when_the_decision_did_not_know(monkeypatch):
    """A crunch-to-better-crunch switch is a decision to leave a held game; a game turning crunch during the wait isn't."""
    h = _host()
    h.decider._jump_hold = lambda x: "crunch" if x.scored.g == "fresh" else None
    h.scored = {"a": NS(g="fresh")}
    cands = {"a": _Cand(NS(g="old"), "youtubetv:a"), "b": _Cand(NS(g="b"), T)}
    assert gate.commit_gate(h, _d(), "a", T, cands, None, now=AT) == "hold"
    h.decider._jump_hold = lambda x: "crunch"   # held at the decision too: it decided that on purpose
    assert gate.commit_gate(h, _d(), "a", T, cands, None, now=AT) == ""


def test_a_planned_rewind_is_exempt_from_the_commercial_check(monkeypatch):
    """Landing before the snap on purpose: the break's tail is the line-up (kept from _entry_valid)."""
    h = _host()
    _timed(h, _plan(True, 30.0))
    monkeypatch.setattr(gate, "break_on_arrival", lambda *a: True)
    assert gate.commit_gate(h, _d(rewind_s=30.0), "a", T, {}, None, now=AT) == ""


def test_a_jump_is_judged_on_the_scores_of_now(monkeypatch):
    h = _host()
    seen = {}
    h.scored = {"a": NS(g="fresh-a"), "b": NS(g="fresh-b")}
    h.decider.jump_lands_dead = lambda gid, x, now, cost: seen.update(land=x.scored.g) or True
    h.decider.jump_may_leave = lambda gid, live, now, req: seen.update(leave=live[gid].scored.g, req=req) or True
    cands = {g: _Cand(NS(g="old"), f"youtubetv:{g}") for g in ("a", "b")}
    assert gate.commit_gate(h, _d(kind="jump"), "a", T, cands, None, now=AT) == ""
    assert seen == {"land": "fresh-b", "leave": "fresh-a", "req": 3.2}


def test_a_jump_logs_the_veto_and_its_cost():
    h = _host()
    h.feeds.stale = lambda g: True
    d = _d(kind="jump")
    d.jump = {}
    assert gate.commit_gate(h, d, "a", T, {}, None, now=AT) == "feed_stale"
    assert d.jump == {"veto": "feed_stale", "veto_cost_s": 3.0}


# ----- the real Engine, behind the flag --------------------------------------------------------------------------------

def _on(e):
    e.cfg.update({"commit_gate": {"enabled": True}, "mode": {"dry_run": False}})


def _may_cut(e, monkeypatch, d, before=None, cands=None):
    got = {}
    monkeypatch.setattr(e.control, "submit", lambda target, back, done, **kw: got.update(done=done, **kw))
    e._act(d, before, cands or {})
    return got["may_cut"]


def test_flag_off_never_reaches_the_gate(tmp_path, monkeypatch):
    e = _engine(tmp_path)
    monkeypatch.setattr(gate, "commit_gate", lambda *a, **k: pytest.fail("gate called with the flag off"))
    monkeypatch.setattr(gate, "entry_veto", lambda *a, **k: pytest.fail("gate called with the flag off"))
    d = Decision(time.time(), "switch", "g2", "start", State.WATCHING, kind="start")
    may_cut = _may_cut(e, monkeypatch, d)
    assert may_cut() is False and may_cut.veto == "entry_invalid"   # today's name, today's path
    assert e._entry_valid(d, T) is False


def test_flag_on_the_gate_answers_for_a_non_tap(tmp_path, monkeypatch):
    e = _engine(tmp_path)
    _on(e)
    monkeypatch.setattr(gate, "commit_gate", lambda *a, **k: "landing")
    d = Decision(time.time(), "switch", "g2", "start", State.WATCHING, kind="start")
    may_cut = _may_cut(e, monkeypatch, d)
    assert may_cut() is False and may_cut.veto == "landing"


def test_flag_on_the_real_gate_names_the_veto_on_the_real_engine(tmp_path, monkeypatch):
    e = _engine(tmp_path)
    _on(e)
    d = Decision(time.time(), "switch", "g2", "start", State.WATCHING, kind="start")
    may_cut = _may_cut(e, monkeypatch, d)
    assert may_cut() is False and may_cut.veto == "not_live"   # no game g2 in the merger
    assert e._entry_valid(d, T) is False


def test_a_tap_keeps_todays_path_with_the_flag_on(tmp_path, monkeypatch):
    """N4: a tap is Nick's call, the gate never sees it; the second tap still goes."""
    e = _engine(tmp_path)
    _on(e)
    monkeypatch.setattr(gate, "commit_gate", lambda *a, **k: pytest.fail("a tap reached the gate"))
    d = Decision(time.time(), "switch", "g2", "tap", State.WATCHING, kind="user")
    monkeypatch.setattr(e, "safe_to_leave", lambda gid, required_s=0.0: True)
    monkeypatch.setattr(e, "_tap_lands_dead", lambda *a: False)
    may_cut = _may_cut(e, monkeypatch, d, before="a")
    assert may_cut() is False and may_cut.veto == "tap_landing" and may_cut.tap is True
    monkeypatch.setattr(e, "_forced_go", lambda dd: True)
    assert may_cut() is True


@pytest.mark.parametrize("check,args", [(_bar_check, ()), (_resume_check, ()), (_tap_check, ()), (_tap_blind_check, ()),
                                        (_tap_second_check, ()),
                                        *[(_replan_check, (lg,)) for lg in (League.NBA, League.NHL, League.MLB)]])
def test_p1_review_behaviours_hold_through_the_gate(engine, monkeypatch, check, args):  # noqa: F811
    """P1-REVIEW-01 and -02: the same regression tests, with the flag on."""
    _on(engine)
    check(engine, monkeypatch, *args)


def test_p1_review_06_a_game_turning_crunch_vetoes_the_jump(engine, monkeypatch):  # noqa: F811
    """06, through the gate. The gate wants a timeline for every kind, so this scene has one."""
    a, b = _two_live(engine, monkeypatch)
    _on(engine)
    engine.decider.timeline(b, League.NFL)
    monkeypatch.setattr(engine.decider, "jump_may_leave", lambda *x: True)
    got, _ = _jump(engine, monkeypatch, a, b)
    assert got["may_cut"]() is True
    engine.scored[a] = dataclasses.replace(engine.scored[a], crunch=True, is_break=False)
    assert got["may_cut"]() is False and got["may_cut"].veto == "hold"


def test_p1_review_01_flip_rechecks_nba_by_name(engine, monkeypatch):  # noqa: F811
    """01: a whistle-timed league's plan has failed by the flip: it waits, and says which rule held it."""
    a, b = _two_live(engine, monkeypatch)
    _on(engine)
    engine.where[b] = NS(target=T)
    monkeypatch.setattr(engine.feeds, "stale", lambda g: False)
    monkeypatch.setattr(engine.control, "deadline_cost", lambda t: 3.0)
    engine.scored[b] = dataclasses.replace(engine.scored[b], league=League.NBA, crunch=False, crunch_now=False)
    engine.decider.timeline(b, League.NBA)
    monkeypatch.setattr(engine.decider, "entry_plan", lambda *x, **k: _plan(False))
    d = Decision(time.time(), "switch", b, "better", State.WATCHING, kind="better")
    assert gate.commit_gate(engine, d, a, T, {}) == "landing"


def test_p1_review_03_a_rewind_stays_the_decisions_and_stays_hidden(tmp_path, monkeypatch):
    """03: the gate never rewrites a switch. The planned rewind and cue reach the controller as decided, so the
    controller's 'never rewind the visible stick' keeps its input; a plan that now needs a different rewind waits."""
    e = _engine(tmp_path)
    _on(e)
    d = Decision(time.time(), "switch", "g2", "better", State.WATCHING, kind="better", rewind_s=45.0, cue="hidden")
    got = {}
    monkeypatch.setattr(e.control, "submit", lambda target, back, done, **kw: got.update(kw))
    e._act(d, None, {})
    assert got["entry_rewind_s"] == 45.0 and got["entry_cue"] == "hidden"
    h = _host()
    _timed(h, _plan(True, 30.0))
    assert gate.commit_gate(h, _d(rewind_s=45.0), None, T, {}, None, now=AT) == "rewind_changed"


# ----- replay on the touched leagues: the gate where the flip is ---------------------------------------------------------

class _ReplayHost:
    """What the gate reads of the Engine, on a backtest's Decider."""
    safe_to_leave = Engine.safe_to_leave
    _flip_lands_whistle = Engine._flip_lands_whistle
    _leave_margin = Engine._leave_margin
    _entry_valid = Engine._entry_valid

    def __init__(self, dec, cands):
        tune = dec.entry_cost(None) if dec.entry_cost else 5.0
        self.cfg = NS(get=lambda k, default=None: False if k == "mode.dry_run" else dec.cfg.get(k, default))
        self.decider, self.delays = dec, dec.delays
        self.scored = {g: x.scored for g, x in cands.items()}
        self.where = {g: NS(target=x.target) for g, x in dec._known.items()}
        self.merger = NS(games={g: NS(game_id=g, phase=Phase.LIVE if g in cands else Phase.POST, league=x.scored.league)
                                for g, x in dec._known.items()})
        self.feeds = NS(stale=lambda g: bool(g in cands and cands[g].stale))
        self.control = NS(deadline_cost=lambda t: tune, flip_upper=lambda: tune, _scrub_wanted=lambda t: False)


def _todays_veto(h, d, before, target, cands, replay_exit, now):
    """engine._act.cut_veto with the flag off, for a non-tap kind: the 'before' of the replay's before/after."""
    if h.decider.user.paused or h.cfg.get("mode.dry_run", True):
        return "paused_or_dry_run"
    if d.kind == "jump":
        cost, g = h.control.deadline_cost(target), h.merger.games.get(d.target or "")
        live = {gid: dataclasses.replace(c, scored=h.scored[gid]) if gid in (before, d.target) and gid in h.scored else c
                for gid, c in cands.items()}
        return ("not_live" if not g or g.phase != Phase.LIVE else "feed_stale" if h.feeds.stale(g.game_id)
                else "target_moved" if getattr(h.where.get(g.game_id), "target", None) != target
                else "landing" if not h.decider.jump_lands_dead(d.target, live.get(d.target), now, cost)
                else "hold" if before in live and h.decider._jump_hold(live[before])
                else "leave" if not h.decider.jump_may_leave(before, live, now, h._leave_margin()) else "")
    if not h._entry_valid(d, target):
        return "entry_invalid"
    if not (now >= replay_exit if replay_exit is not None else h.safe_to_leave(before, required_s=8.0)):
        return "replay_exit" if replay_exit is not None else "leave"
    return "" if h._flip_lands_whistle(d, target) else "landing_whistle"


VETOES: Counter = Counter()


class _Gated(Decider):
    """decide() proposes (commit=False, as live); the gate stands at the flip; a veto is a stay and the next tick
    proposes again, the replay's stand-in for may_cut's polling. A pass commits the way decide() itself does."""
    judge = staticmethod(gate.commit_gate)

    def decide(self, cands, redzone=None, *, commit=True, reel=None):
        d = super().decide(cands, redzone, commit=False, reel=reel)
        now = self._clock()
        if d.action not in ("switch", "replay"):
            return d
        target = cands[d.target].target if d.target in cands else self.target_of(d.target)
        rx = max(self.replay_until, self.replay_exit_after) if (self.replaying or self.after_replay) else None
        veto = self.judge(_ReplayHost(self, cands), d, self.current, target, cands, rx,
                          now=now + float(getattr(d, "defer_s", 0.0) or 0.0))   # a deferred cut is judged at its moment
        if veto:
            VETOES[veto] += 1
            self.reject(d, backoff=False)
            return Decision(now, "stay", self.current, f"commit gate: {veto}", d.state, d.gate, want=d.target, owner=d.owner)
        if d.action == "switch" and d.target and d.target == self.current and not self.after_replay and d.kind != "skip":
            self._proposal_home = None
            return Decision(now, "stay", self.current, "already here", d.state, owner=d.owner)
        if self._proposal_home and self._proposal_home[0] is d:
            _, home, at = self._proposal_home
            self._home_set(home, at, "the commit gate passed it")
        self._proposal_home = None
        if d.action == "switch" and d.target:
            if d.kind in ("resume", "skip"):
                self.resume_log.append((now, d.kind, d.target, d.rewind_s))
            self._commit(d.target, kind=d.kind)
            self._land_behind(d.target, d.rewind_s)
        elif d.action == "replay" and d.replay:
            self._pause_for(d, now)
            self._start_replay(d.replay, cands, now)
        return d


@contextlib.contextmanager
def gated(rules=None, judge=None):
    """Scorecards with a veto at the flip: rules=() runs the replay plumbing with no rule (the control run);
    judge=_todays_veto runs the flag-off path; default, the commit gate."""
    saved = backtest.Decider, engine_mod.time, gate.GATE_RULES, gate.redzone_on, engine_mod.redzone_on
    clock = {}

    class Host(_Gated):
        def __init__(self, cfg, delays, clock=None):
            super().__init__(cfg, delays, clock=clock)
            box["clock"] = clock
    if judge is not None:
        Host.judge = staticmethod(judge)
    box = clock
    backtest.Decider = Host
    engine_mod.time = NS(time=lambda: box["clock"](), monotonic=time.monotonic)
    gate.redzone_on = engine_mod.redzone_on = lambda games: True   # the replay only offers RedZone while it's on
    if rules is not None:
        gate.GATE_RULES = rules
    VETOES.clear()
    try:
        yield
    finally:
        backtest.Decider, engine_mod.time, gate.GATE_RULES, gate.redzone_on, engine_mod.redzone_on = saved
        backtest._CALS.clear()


def card(script, *args):
    """One scorecard run, in-process so the patches above reach it; its last JSON object."""
    argv, sys.argv = sys.argv, [script, *args]
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            runpy.run_path(str(ROOT / script), run_name="__main__")
    finally:
        sys.argv = argv
    text = "\n" + out.getvalue()
    c = json.loads(text[text.rindex("\n{\n") + 1:])
    c.pop("seconds", None)   # wall clock: the one field that differs between two identical runs
    return c


DEAD_AIR_NOISE_PTS = 0.3   # a tick either way moves a two-night card this much
LEAGUES = {"nfl": ("scripts/eval.py", ["--split", "valid"]),
           "nba": ("scripts/eval_sports.py", ["nba", "--split", "valid"]),
           "nhl": ("scripts/eval_sports.py", ["nhl", "--split", "valid"])}


@pytest.mark.parametrize("league", sorted(LEAGUES))
def test_replay_on_dev_nights_the_gate_keeps_rule_1(league):
    """Valid split only (dev data): the plumbing with no rule equals today's card; with every rule, no Rule #1 metric
    gets worse and mid-play entries and high-leverage leaves stay 0."""
    if not (ROOT / "data/archive" / league).exists():
        pytest.skip("pinned eval data not fetched (scripts/fetch_eval_data.py)")
    script, args = LEAGUES[league]
    base = card(script, *args)
    with gated(rules=()):
        plumbing = card(script, *args)
    assert plumbing == base
    with gated(judge=_todays_veto):
        off = card(script, *args)
    with gated():
        on = card(script, *args)
    assert on["entries_mid_play"] == 0 and on.get("mid_play_cuts_high", 0) == 0
    assert on["plays_missed_entry"] <= off["plays_missed_entry"]
    assert on["dead_air_pct"] <= off["dead_air_pct"] + DEAD_AIR_NOISE_PTS
