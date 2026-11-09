"""The brain-first audit: what refuses a strong brain's top pick, per rule, and what each refusal costs.

    nice -n 15 python scripts/veto_audit.py --split fit --leagues nba --brains hindsight heuristic --cap 3 --json out.json
    --set arbiter.visit_budget=0   any config value for this run (repeatable), for a before/after
    --set audit.assume_gate_ok=true   what-if: legal_options stops asking for a leave gap at list time (the flip still does)

Dev nights only (`--split` takes fit and nothing else; a locked night is never opened). No TV, no adb, no engine.

The goal: code stops being the bottleneck, so what the brain wants goes. Each replayed tick a stand-in brain names its
top pick (hindsight: the game with the most must-see value about to air, knowing the future; heuristic: the highest live
scorer score). The pick then walks the real chain, in live order: `legal_options`, `arbiter.choose`, the commit gate.
Every rule that would say no is recorded, and the replay goes on with what the chain allows, so a refusal has its real
cost downstream. Value: every must-see moment (the arbiter's floor) weighs 5 for a score, 3 for a red-zone trip or a
turnover, 1 otherwise. A moment that aired unseen is charged to the LAST refusal of its game inside the run-up.
`lost` charges the first rule named, `lost_alone` only ticks where that rule alone said no, so loosening it alone
would have freed the cut.

Each rule carries its class:  rule1 (protects never-join-mid-play or the leave rule, never loosened)  user (Nick's own
controls)  policy (Nick's stated priorities)  caution (an old limit with no measured Rule #1 reason)  stick_prep (a slow
stick or a landing in progress)  missing_data. `protected` is how many of a rule's refusals really would have landed
mid-play or left a high-leverage play, by the grader's own facts: it checks the class, it never sets it.

`lost_after_catchup` (SIMULATED, per rule, at two reaches): of `lost`, what the catch-up route still would not have shown.
After the night, the unseen must-sees go to the route on the ticks it was open (arbiter.catchup_pick and _arbiter_catchup's
own checks, on a tick the replay stayed), one clip at a time in arbiter.plan_clips order. A clip goes only when the game on
screen sits at a dead ball long enough to leave, air the clip and come back before play restarts, so it can't join or leave
mid-play. Reach 95 s is the measured floor, 180 s is Nick's never-3-minutes-late cap; real rewind reach is unproven on the
TV. Clips can't move the replay's own cuts. `avoidable lost` is lost_after_catchup without the moments whose refusal was
`protected`: what no rule protected and no replay made up.

This is a replay: seek, prep and feed lag are model constants, so stick_prep and missing_data rules can only be judged
on the TV. It is info only: not a gate, nothing frozen is imported. Stdout: the ranked table.

Why: Nick's goal is that what the brain wants goes. Before loosening anything we need to know which rule costs what, and
which refusals are Rule #1 and which are just habit.
"""
from __future__ import annotations

import argparse
import bisect
import contextlib
import dataclasses
import io
import json
import runpy
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parent.parent
if ROOT.as_posix() not in sys.path:
    sys.path.insert(0, ROOT.as_posix())

from redzone import engine as engine_mod  # noqa: E402
from redzone.contracts import brain as contract  # noqa: E402
from redzone.decider import gate  # noqa: E402
from redzone.decider.decider import Decider, Decision, _holds_replays  # noqa: E402
from redzone.engine import Engine  # noqa: E402
from redzone.grading import dead_at, lag_of  # noqa: E402
from redzone.lab import backtest, leverage, splits, sportsgrade  # noqa: E402
from redzone.models import Phase  # noqa: E402
from redzone.moments.detect import detect as detect_moments  # noqa: E402
from redzone.noggin import arbiter, legal  # noqa: E402

SCRIPTS = {"nfl": ("scripts/eval.py", []), "nba": ("scripts/eval_sports.py", ["nba"]),
           "nhl": ("scripts/eval_sports.py", ["nhl"]), "mlb": ("scripts/eval_sports.py", ["mlb"])}
PLAY_LEN_S = 7.0    # backtest.run's play_len: the picture must be up this long before the play's end to have seen it
RUN_UP_S = 75.0     # how far ahead the hindsight brain looks, and how far back a refusal is charged to a moment
SCALE = 10.0        # one value point is this many brain points, so a single must-see clears the arbiter's margin
REACHES = (95, 180)  # catch-up rewind reach, s: the measured floor (YouTube TV) and Nick's never-3-minutes-late cap
CLIP_S = arbiter.DEFAULT_PARAMS.plan_clip_s   # one catch-up clip holds the screen this long
PREP_S = 15.0        # buffered_jump_sim.MR.prep_s: a free stick takes this long to set a clip up

RULE1, USER, POLICY, CAUTION, STICK, DATA = "rule1", "user", "policy", "caution", "stick_prep", "missing_data"
CLASSES: dict[str, str] = {
    "legal:user:safe_mode": USER, "legal:user:paused": USER, "legal:user:locked_game": USER, "legal:user:manual_pause": USER,
    "legal:arriving": STICK, "legal:dwell": CAUTION, "legal:too_soon": CAUTION, "legal:leave_gate": RULE1,
    "legal:moment_lock": RULE1, "legal:on_screen": POLICY, "legal:not_tunable": DATA, "legal:dark": RULE1,
    "legal:no_landing": RULE1, "legal:banned_app": USER, "legal:focus": USER, "legal:unknown_game": DATA,
    "arbiter:no_cuts": POLICY, "arbiter:ceiling": CAUTION, "arbiter:last_game": CAUTION, "arbiter:floor": POLICY,
    "arbiter:stay_replay": CAUTION, "arbiter:thinned": CAUTION, "arbiter:baseline": POLICY, "arbiter:margin": CAUTION,
    "arbiter:decide_last": CAUTION, "arbiter:late_narrow": CAUTION, "arbiter:visit_budget": CAUTION,
    "arbiter:l1_class": POLICY, "arbiter:outscored": POLICY, "arbiter:not_offered": POLICY,
    "gate:paused_or_dry_run": USER, "gate:deferred": STICK, "gate:replay_used": POLICY, "gate:redzone_off": DATA,
    "gate:reel_missing": DATA, "gate:not_live": DATA, "gate:feed_stale": DATA, "gate:target_moved": DATA,
    "gate:no_timeline": DATA, "gate:chase_gone": POLICY, "gate:landing": RULE1, "gate:landing_unproven": RULE1,
    "gate:rewind_changed": STICK, "gate:arrival_break": POLICY, "gate:hold": RULE1, "gate:ending_airing": RULE1,
    "gate:replay_exit": RULE1, "gate:leave": RULE1, "gate:landing_whistle": RULE1,
}


# Rules about leaving the game on screen; every other rule's harm is the landing on the game it would go to.
LEAVE_RULES = frozenset({"legal:leave_gate", "legal:moment_lock", "gate:leave", "gate:hold", "gate:ending_airing", "gate:replay_exit"})


def value_of(kind: str) -> float:
    """What one must-see moment is worth. N5: every score, and 95% of red-zone trips."""
    return 5.0 if kind in arbiter.SCORE_KINDS else 3.0 if kind in ("rz_trip", "turnover") else 1.0


@dataclass(frozen=True)
class Air:
    """One must-see moment as the screen sees it."""
    at: float          # epoch s it finishes airing on the game's picture
    game_id: str
    kind: str
    value: float


def must_see(replays, lag) -> dict[str, list[Air]]:
    """{game: its must-see moments in air order}. Facts from the grader's own detector; the floor says what counts."""
    out: dict[str, list[Air]] = {}
    for r in replays:
        ms = [Air(m.t + lag_of(lag, r.gid), r.gid, m.kind, value_of(m.kind)) for m in detect_moments(r.league.value, r.data, r.gid).moments
              if arbiter.trigger_rank(r.league.value, m.kind) != arbiter.NOT_FLOOR]
        out[r.gid] = sorted(ms, key=lambda a: a.at)
    return out


class Truth:
    """What really happens, for a brain that knows the future and for the `protected` check."""

    def __init__(self):
        self.replays, self.lag, self.tune_s, self.moments = {}, 43.0, 5.0, {}

    def bind(self, replays, lag, tune_s) -> None:
        self.replays, self.lag, self.tune_s = {r.gid: r for r in replays}, lag, tune_s
        self.moments = must_see(replays, lag)

    def ahead(self, gid: str | None, now: float, lead: float) -> float:
        """Must-see value finishing on this game's picture in [now + lead, now + RUN_UP_S]; sooner counts a bit more."""
        ms = self.moments.get(gid or "", ())
        i = bisect.bisect_left([m.at for m in ms], now + lead)
        return sum(m.value * (1.0 - 0.5 * (m.at - now) / RUN_UP_S) for m in ms[i:] if m.at <= now + RUN_UP_S)

    def lands_mid_play(self, gid: str, now: float) -> bool:
        r = self.replays.get(gid)
        return r is not None and not dead_at(r.league, r.wall, r.plays, now + self.tune_s - lag_of(self.lag, gid))[0]

    def mid_play_on_screen(self, gid: str | None, now: float) -> bool:
        r = self.replays.get(gid or "")
        return r is not None and not dead_at(r.league, r.wall, r.plays, now - lag_of(self.lag, r.gid))[0]

    def high_leverage_on_screen(self, gid: str | None, now: float) -> bool:
        r = self.replays.get(gid or "")
        if r is None:
            return False
        at = now - lag_of(self.lag, r.gid)
        return any(w - PLAY_LEN_S <= at <= w and leverage.high_leverage(r.league, p) for w, p in zip(r.wall, r.plays))


class Hindsight:
    """Knows the future: the highest score goes to the game with the most must-see value about to air."""
    name = "hindsight"

    def __init__(self, truth: Truth):
        self.truth = truth

    def scores(self, cands, current, now) -> dict[str, float]:
        lead = self.truth.tune_s + PLAY_LEN_S
        out = {"stay": min(100.0, SCALE * self.truth.ahead(current, now, 0.0))}
        for g in cands:
            if g != current:
                out[f"cut:{g}"] = min(100.0, SCALE * self.truth.ahead(g, now, lead))
        return out


class Heuristic:
    """No future: a good live ranker, the scorer's own score, with crunch, red zone and your teams already in it."""
    name = "heuristic"

    def __init__(self, truth: Truth):
        self.truth = truth

    def scores(self, cands, current, now) -> dict[str, float]:
        cur = cands.get(current or "")
        out = {"stay": min(100.0, float(cur.scored.score)) if cur is not None else 0.0}
        for g, x in cands.items():
            if g != current:
                out[f"cut:{g}"] = min(100.0, float(x.scored.score))
        return out


BRAINS = {"hindsight": Hindsight, "heuristic": Heuristic}


class Overlay:
    """A Config with this run's --set values on top."""

    def __init__(self, cfg, sets: dict):
        self._cfg, self._sets = cfg, sets

    def __getattr__(self, name):
        return getattr(self._cfg, name)

    def get(self, key, default=None):
        return self._sets[key] if key in self._sets else self._cfg.get(key, default)


class ReplayHost:
    """What the commit gate reads of the Engine, on a replay's Decider."""
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


@dataclass
class Tally:
    """One run: every refused top pick, and the screen, for the charge after the night."""
    ticks: int = 0
    wanted: int = 0          # ticks the top pick was a cut
    went: int = 0            # ...and it went
    refused: Counter = field(default_factory=Counter)     # rule -> ticks it was the first refuser
    alone: Counter = field(default_factory=Counter)       # rule -> ticks it was the only refuser
    any_rule: Counter = field(default_factory=Counter)    # rule -> ticks it said no at all
    protected: Counter = field(default_factory=Counter)   # rule -> of any_rule, ticks the harm it names was real (see LEAVE_RULES)
    high: Counter = field(default_factory=Counter)        # leave rules: of any_rule, ticks a high-leverage play was on screen
    log: dict = field(default_factory=lambda: defaultdict(list))    # game -> [(t, first, alone?)]
    screen: list = field(default_factory=list)            # (picture up at, game)
    route: list = field(default_factory=list)             # (t, game on screen) when the catch-up route was open on a stay tick


def judge(host: Decider, pick: str, cands: dict, now: float) -> tuple[list[str], arbiter.Choice | None, list[str], Decision | None]:
    """Run the top pick through the real chain: (legal refusals, the arbiter's choice, gate vetoes, the decision)."""
    facts = host.legal_facts(cands, now)
    if host.cfg.get("audit.assume_gate_ok", False):     # what-if only, no product flag: the flip's own leave rules still run
        facts = dataclasses.replace(facts, gate_ok=True)
    refused = list(legal.refusals(facts, pick))
    named = [f"legal:{r}" for r in refused]
    opts = legal.legal_options(facts)
    p = host._arbiter_params()
    inp = contract.BrainInput(tick=host.tally.ticks, now=now, options=opts)
    ids = {o.id for o in opts}          # the contract refuses a score for an option code did not offer
    out = contract.BrainOutput(tick=inp.tick, model_id=host.brain.name, made_at=now, ttl_s=5.0,
                               scores={k: v for k, v in host.pick_scores.items() if k in ids})
    show, musts = host._arbiter_show(cands, now, p), host._arbiter_musts(cands, now, p)
    arb = arbiter.choose(inp, out, musts=musts, show=show, p=p)
    why = arbiter.refused_by(inp, out, f"cut:{pick}", musts=musts, show=show, p=p) if not refused else ""
    if why:
        named.append(f"arbiter:{why}")
    d = Decision(now, "switch", pick, f"brain {host.brain.name}", host.base_state, kind="better", owner=host.base_owner)
    x = cands[pick]
    flip = gate.Flip(ReplayHost(host, cands), d, host.current, x.target, cands, None, now)
    timed, plan = flip.replan
    if timed and plan is not None and plan.ok:
        d.rewind_s = plan.rewind_s
    vetoes = [f"gate:{n}" for n, rule in gate.GATE_RULES if rule(flip)]
    return named, arb, vetoes, d


class AuditDecider(Decider):
    """decide() throws the decider's own proposal away and walks the brain's top pick down the real chain."""
    brain, tally, truth, sets, base_state, base_owner, pick_scores = None, None, None, {}, None, None, {}

    def __init__(self, cfg, delays, clock=None):
        super().__init__(Overlay(cfg, self.sets), delays, clock=clock)

    def decide(self, cands, redzone=None, *, commit=True, reel=None):
        base = super().decide(cands, redzone, commit=False, reel=reel)
        now, t = self._clock(), self.tally
        t.ticks += 1
        if base.action in ("switch", "replay"):
            self.reject(base, backoff=False)
        self.base_state, self.base_owner = base.state, base.owner
        self.pick_scores = self.brain.scores(cands, self.current, now)
        top = max(self.pick_scores, key=lambda o: (self.pick_scores[o], o))
        stay = Decision(now, "stay", self.current, f"brain {self.brain.name}: stay", base.state, owner=base.owner)
        if top == "stay" or self.pick_scores[top] <= self.pick_scores.get("stay", 0.0):
            self._note_route(cands, now)
            return stay
        pick = top.removeprefix("cut:")
        t.wanted += 1
        named, ch, vetoes, d = judge(self, pick, cands, now)
        blockers = named + vetoes
        chosen = ch.option if ch is not None else None
        if blockers:
            self._charge(pick, blockers, now)
        if chosen is None or chosen.verb == "stay":
            self._note_route(cands, now)
            return stay
        if chosen.game_id != pick:      # the arbiter's floor picked another legal game: it still faces the gate
            d = Decision(now, "switch", chosen.game_id, f"arbiter over {self.brain.name}", base.state, kind="better", owner=base.owner)
            f = gate.Flip(ReplayHost(self, cands), d, self.current, cands[chosen.game_id].target, cands, None, now)
            timed, plan = f.replan
            if timed and plan is not None and plan.ok:
                d.rewind_s = plan.rewind_s
            vetoes = [n for n, rule in gate.GATE_RULES if rule(f)]
        if vetoes:
            self._note_route(cands, now)
            return stay
        t.went += 1
        self._commit(d.target, kind=d.kind)
        self._land_behind(d.target, d.rewind_s)
        t.screen.append((now + self.truth.tune_s, d.target))
        return d

    def _note_route(self, cands: dict, now: float) -> None:
        """Would the live catch-up route be open on this stay tick? The same checks as Decider._arbiter_catchup, then catchup_pick."""
        cur = cands.get(self.current or "")
        if cur is None or not self.cfg.get("replay.enabled", True) or _holds_replays(cur.scored) or cur.scored.crunch_now \
                or cur.scored.high_leverage or self._crunch_on(cands):
            return
        p = self._arbiter_params()
        opts = legal.legal_options(self.legal_facts(cands, now))
        if arbiter.catchup_pick([("clip", 0, now)], opts, self._arbiter_show(cands, now, p), now, p) is not None:
            self.tally.route.append((now, self.current))

    def _charge(self, pick: str, blockers: list[str], now: float) -> None:
        t, first = self.tally, blockers[0]
        t.refused[first] += 1
        if len(blockers) == 1:
            t.alone[first] += 1
        lands, mid, high = self.truth.lands_mid_play(pick, now), self.truth.mid_play_on_screen(self.current, now), \
            self.truth.high_leverage_on_screen(self.current, now)
        for b in blockers:
            t.any_rule[b] += 1
            t.protected[b] += int(mid if b in LEAVE_RULES else lands)
            t.high[b] += int(high and b in LEAVE_RULES)
        t.log[pick].append((now, first, len(blockers) == 1, bool(mid if first in LEAVE_RULES else lands)))


def seen(air: Air, screen: list) -> bool:
    """On screen for the whole play: the last cut before it started was to this game."""
    i = bisect.bisect_right([s[0] for s in screen], air.at - PLAY_LEN_S) - 1
    return i >= 0 and screen[i][1] == air.game_id


def last_flip(m: Air, reach: float, tune_s: float) -> float:
    """The last second a catch-up of this play can start: its picture must be up within `reach` of the play's start on air,
    and never 180 s or more after it (Nick's rule)."""
    return m.at - PLAY_LEN_S + min(reach, 180.0) - tune_s


def catchup_shown(unseen: list[Air], t: Tally, truth: Truth, reach: float) -> set[Air]:
    """SIMULATED: the unseen moments the catch-up route would have shown at this rewind reach. See the module docstring."""
    tune, shown, free_at = truth.tune_s, set(), float("-inf")
    need = 2.0 * tune + CLIP_S          # leave, the clip, come back: all inside one dead ball on the game on screen
    cuts = sorted(s[0] - tune for s in t.screen)
    for now, gid in t.route:
        if now < free_at:
            continue
        pool = [m for m in unseen if m not in shown and m.at + PREP_S <= now <= last_flip(m, reach, tune)]
        r = truth.replays.get(gid)
        if not pool or r is None:
            continue
        dead, _, left = dead_at(r.league, r.wall, r.plays, now - lag_of(truth.lag, gid))
        i = bisect.bisect_left(cuts, now)
        if not dead or left < need or (i < len(cuts) and cuts[i] < now + need):
            continue
        cands = [(k, m.value, m.at, last_flip(m, reach, tune), CLIP_S, m.at + PREP_S) for k, m in enumerate(pool)]
        first = arbiter.plan_clips(cands, now, tune, PREP_S)
        if first is not None:
            shown.add(pool[first])
            free_at = now + need
    return shown


def charge_night(t: Tally, truth: Truth, league_row: dict) -> None:
    """Charge every unseen must-see moment to the last refusal of its game in the run-up. Seen and uncharged are counted."""
    unseen = [m for ms in truth.moments.values() for m in ms if not seen(m, t.screen)]
    shown = {r: catchup_shown(unseen, t, truth, r) for r in REACHES}
    for gid, ms in truth.moments.items():
        log = t.log.get(gid, [])
        times = [e[0] for e in log]
        for m in ms:
            league_row["must_total"] += m.value
            if seen(m, t.screen):
                league_row["must_seen"] += m.value
                continue
            i = bisect.bisect_right(times, m.at - truth.tune_s - PLAY_LEN_S) - 1
            if i >= 0 and times[i] >= m.at - RUN_UP_S:
                _, rule, only, *real = log[i]
                league_row["lost"][rule] += m.value
                league_row["moments"][rule] += 1
                if only:
                    league_row["lost_alone"][rule] += m.value
                for r in REACHES:
                    if m not in shown[r]:
                        league_row["lost_after_catchup"][str(r)][rule] += m.value
                        league_row["avoidable_lost"][str(r)] += 0.0 if (real and real[0]) else m.value
            else:
                league_row["not_asked"] += m.value


def new_row() -> dict:
    return {"must_total": 0.0, "must_seen": 0.0, "not_asked": 0.0, "lost": Counter(), "lost_alone": Counter(), "moments": Counter(),
            "ticks": 0, "wanted": 0, "went": 0, "refused": Counter(), "alone": Counter(), "any_rule": Counter(), "protected": Counter(), "high": Counter(),
            "lost_after_catchup": {str(r): Counter() for r in REACHES}, "avoidable_lost": {str(r): 0.0 for r in REACHES}}


def card(script: str, *args: str) -> dict:
    """One scorecard run in this process, so the patches reach it; its last JSON object."""
    argv, sys.argv = sys.argv, [script, *args]
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            runpy.run_path(str(ROOT / script), run_name="__main__")
    finally:
        sys.argv = argv
    text = "\n" + out.getvalue()
    return json.loads(text[text.rindex("\n{\n") + 1:])


@contextlib.contextmanager
def replay(brain_name: str, sets: dict, row: dict, cap: int | None):
    """Scorecards with the stand-in brain in charge of every cut. Everything patched here is restored."""
    saved = (backtest.Decider, backtest.run, engine_mod.time, gate.redzone_on, engine_mod.redzone_on, splits.nights_for, sportsgrade.grade)
    box, truth, real_run, real_nights, real_grade = {}, Truth(), backtest.run, splits.nights_for, sportsgrade.grade
    agent = BRAINS[brain_name](truth)
    high = []
    current: list[Tally] = []

    class Host(AuditDecider):
        def __init__(self, cfg, delays, clock=None):
            self.sets = sets
            super().__init__(cfg, delays, clock=clock)
            box["clock"] = clock
            self.brain, self.truth = agent, truth
            self.tally = current[-1]

    def run(replays, cfg, my_teams, **kw):
        lag = kw.get("feed_lag", 8.0) + (kw.get("delay", 35.0) if kw.get("true_delay") is None else kw["true_delay"])
        truth.bind(replays, lag, kw.get("tune_s", 5.0))
        t = Tally()
        current.append(t)
        rep = real_run(replays, cfg, my_teams, **kw)
        charge_night(t, truth, row)
        for k in ("ticks", "wanted", "went"):
            row[k] += getattr(t, k)
        for k in ("refused", "alone", "any_rule", "protected", "high"):
            row[k].update(getattr(t, k))
        return rep

    def nights_for(league_dir, which, end=None):
        if which != "fit":
            raise AssertionError(f"veto audit asked for split {which!r}: dev nights only")
        keep = real_nights(league_dir, which, end)
        if cap is None:
            return keep
        games = splits.archive_nights(league_dir, end)
        return set(sorted(keep, key=lambda n: (-len(games.get(n, ())), n))[:cap])

    def grade(*a, **kw):
        c = real_grade(*a, **kw)
        high.extend(c.cuts_high)
        return c

    backtest.Decider, backtest.run, splits.nights_for, sportsgrade.grade = Host, run, nights_for, grade
    engine_mod.time = NS(time=lambda: box["clock"](), monotonic=time.monotonic)
    gate.redzone_on = engine_mod.redzone_on = lambda games: True
    try:
        yield high
    finally:
        (backtest.Decider, backtest.run, engine_mod.time, gate.redzone_on, engine_mod.redzone_on, splits.nights_for, sportsgrade.grade) = saved
        backtest._CALS.clear()


def run_one(job: tuple) -> dict:
    """One league under one brain: the row for the table."""
    league, brain_name, sets, cap = job
    row = new_row()
    script, args = SCRIPTS[league]
    extra = ["--split", "fit"]
    with replay(brain_name, sets, row, cap) as high:
        c = card(script, *args, *extra)
    nights = c.get("per_night", [])
    row["card"] = {"nights": len(nights), "entries": sum(n["entries"] for n in nights),
                   "entries_mid_play": sum(n["entries_mid_play"] for n in nights),
                   "cuts_high": len(high), "mid_play_leaves": sum(n.get("cuts", 0) for n in nights), "dead_air_pct": c.get("dead_air_pct"),
                   "plays_missed_entry": c.get("plays_missed_entry", sum(n.get("plays_missed_entry", 0) for n in nights))}
    row["league"], row["brain"] = league, brain_name
    return json.loads(json.dumps(row))


def merge(rows: list[dict]) -> dict:
    """Per rule, over the rows: refusals, must-see value lost, class. Biggest loss first."""
    out: dict[str, dict] = {}
    for r in rows:
        for rule, n in r["refused"].items():
            out.setdefault(rule, _blank(rule))["refused"] += n
        for key in ("alone", "any_rule", "protected", "high", "lost", "lost_alone", "moments"):
            for rule, v in r[key].items():
                out.setdefault(rule, _blank(rule))[key] += v
        for reach, by_rule in r["lost_after_catchup"].items():
            for rule, v in by_rule.items():
                out.setdefault(rule, _blank(rule))[f"after_{reach}"] += v
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["lost"]))


def _blank(rule: str) -> dict:
    return {"class": CLASSES.get(rule, "unclassified"), "refused": 0, "alone": 0, "any_rule": 0, "protected": 0, "high": 0, "lost": 0.0,
            "lost_alone": 0.0, "moments": 0, **{f"after_{r}": 0.0 for r in REACHES}}


def table(rows: list[dict], *, top: int = 25) -> str:
    """The ranked table of rules by must-see value lost, then per-league coverage."""
    lines = [f"{'rule':<28}{'class':<14}{'refused':>8}{'alone':>7}{'lost':>8}{'alone$':>8}{'moments':>8}{'real%':>7}{'high%':>7}"]
    for rule, v in list(merge(rows).items())[:top]:
        real = 100.0 * v["protected"] / v["any_rule"] if v["any_rule"] else 0.0
        high = 100.0 * v["high"] / v["any_rule"] if v["any_rule"] else 0.0
        lines.append(f"{rule:<28}{v['class']:<14}{v['refused']:>8}{v['alone']:>7}{v['lost']:>8.1f}{v['lost_alone']:>8.1f}{v['moments']:>8}{real:>6.0f}%{high:>6.0f}%")
    for r in rows:
        c, pct = r["card"], 100.0 * r["must_seen"] / r["must_total"] if r["must_total"] else 0.0
        lines.append(f"{r['league']}/{r['brain']}: must-see seen {pct:.1f}% of {r['must_total']:.0f}, not asked {r['not_asked']:.0f}, "
                     f"wanted {r['wanted']} went {r['went']}, mid-play {c['entries_mid_play']}/{c['entries']}, high-lev leaves {c['cuts_high']}, all mid-play leaves {c['mid_play_leaves']}")
    return "\n".join(lines + catchup_lines(rows))


def catchup_lines(rows: list[dict]) -> list[str]:
    """lost_after_catchup per rule at each reach, its totals, and the one avoidable-lost number per reach. Simulated."""
    merged = merge(rows)
    lines = ["", "lost after catch-up (SIMULATED: real rewind reach is unproven on the TV)",
             f"{'rule':<28}{'lost':>8}" + "".join(f"{'reach ' + str(r) + 's':>13}" for r in REACHES)]
    for rule, v in merged.items():
        if v["lost"]:
            lines.append(f"{rule:<28}{v['lost']:>8.1f}" + "".join(f"{v[f'after_{r}']:>13.1f}" for r in REACHES))
    lines.append(f"{'TOTAL':<28}{sum(v['lost'] for v in merged.values()):>8.1f}"
                 + "".join(f"{sum(v[f'after_{r}'] for v in merged.values()):>13.1f}" for r in REACHES))
    for r in REACHES:
        lines.append(f"avoidable lost, simulated reach {r} s: {avoidable(rows, r):.1f}  (no catch-up route, refusal not protected)")
    return lines


def avoidable(rows: list[dict], reach: int) -> float:
    return sum(row["avoidable_lost"][str(reach)] for row in rows)


def parse_sets(items) -> dict:
    out = {}
    for s in items or ():
        k, _, v = s.partition("=")
        try:
            out[k] = json.loads(v)
        except json.JSONDecodeError:
            out[k] = v
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--split", default="fit", choices=("fit",), help="dev nights only; fit is the only choice")
    ap.add_argument("--leagues", nargs="+", default=list(SCRIPTS), choices=list(SCRIPTS))
    ap.add_argument("--brains", nargs="+", default=list(BRAINS), choices=list(BRAINS))
    ap.add_argument("--cap", type=int, default=None, help="busiest N fit nights per league (default every fit night)")
    ap.add_argument("--workers", type=int, default=2, choices=(1, 2))
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="override one config value for this run")
    ap.add_argument("--json", type=Path, default=None)
    a = ap.parse_args()
    sets = parse_sets(a.set)
    jobs = [(lg, b, sets, a.cap) for lg in a.leagues for b in a.brains]
    if a.workers == 1 or len(jobs) == 1:
        rows = [run_one(j) for j in jobs]
    else:
        with ProcessPoolExecutor(max_workers=a.workers) as ex:
            rows = list(ex.map(run_one, jobs))
    print(table(rows))
    if a.json:
        a.json.write_text(json.dumps({"sets": sets, "rows": rows, "rules": merge(rows), "avoidable_lost": {str(r): avoidable(rows, r) for r in REACHES}}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
