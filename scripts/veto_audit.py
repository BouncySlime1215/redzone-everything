"""The brain-first audit: what refuses a strong brain's top pick, per rule, and what each refusal costs.

    nice -n 15 python scripts/veto_audit.py --split fit --leagues nba --brains hindsight heuristic --cap 3 --json out.json
    --set arbiter.visit_budget=0   any config value for this run (repeatable), for a before/after
    --set audit.assume_gate_ok=true   what-if: legal_options stops asking for a leave gap at list time (the flip still does)
    --prep-s 60   what-if: the hidden stick needs this long to tune and seek to a clip (default 15; 60 is a re-tuned stick, buffered.prep_s)
    --timing measured   the catch-up route's flip, clip load and reach as the TV measured them Nov 9 (timing.* via redzone/measured_timing.py:
                  flip 2.2 s, load 19 s, reach capped at 175 s, so the 180 s column reads 175). Default `assumed`: today's 3.2 / 15 / column reach
    --dead floor  size every catch-up piece with the dead time the live code could count on in advance (scoring/dead_left.guaranteed_left,
                  q10 of the stoppage's measured length less what already ran), not the real length known afterwards. Default `hindsight`
    --delay-error 3   the TRUE picture delay is 3 s more, then 3 s less, than the app believes (two whole runs); the table is the worse arm's

`yes-rate` (new lines after the tables): of the ticks the brain wanted another game, the share where that game's picture was up
within 20 s, and the share that instead got a finished catch-up clip of that game within 60 s (the route at the 180 s reach, SIMULATED).
`gate:leave by sub-cause`: which part of the leave rule said no (gate.leave_cause): unaired_row is "the newest data row of a LIVE
game has not aired yet", window is the leave window itself.

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

`residual` (with arbiter.catchup_route on): every value point still lost after catch-up, by WHY the route did not air it. A
moment's cause is the furthest step any tick got inside its window (set up 15 s after it aired, up before the reach and the
180 s cap), see CAUSES. `past_cap` is the part of it that had an open, long-enough dead ball in the 10 minutes after the cap.

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
from redzone.decider.decider import Decider, Decision, FocusMode, LeaveHold, _holds_replays  # noqa: E402
from redzone.engine import Engine  # noqa: E402
from redzone import measured_timing  # noqa: E402
from redzone import config as rz_config  # noqa: E402
from redzone.decider.screen import CONTINUOUS  # noqa: E402
from redzone.grading import dead_at, held_stop, idx, is_action, lag_of  # noqa: E402
from redzone.lab import backtest, leverage, splits, sportsgrade  # noqa: E402
from redzone.models import League, Phase, Stoppage  # noqa: E402
from redzone.moments.detect import detect as detect_moments  # noqa: E402
from redzone.noggin import arbiter, catchup, legal  # noqa: E402
from redzone.scoring.dead_left import guaranteed_left  # noqa: E402
from redzone.sources import espn  # noqa: E402

SCRIPTS = {"nfl": ("scripts/eval.py", []), "nba": ("scripts/eval_sports.py", ["nba"]),
           "nhl": ("scripts/eval_sports.py", ["nhl"]), "mlb": ("scripts/eval_sports.py", ["mlb"])}
PLAY_LEN_S = 7.0    # backtest.run's play_len: the picture must be up this long before the play's end to have seen it
RUN_UP_S = 75.0     # how far ahead the hindsight brain looks, and how far back a refusal is charged to a moment
SCALE = 10.0        # one value point is this many brain points, so a single must-see clears the arbiter's margin
REACHES = (95, 150, 180)  # catch-up rewind reach, s: the measured floor (YouTube TV), the measured ceiling, and Nick's never-3-minutes-late cap
CLIP_S = arbiter.DEFAULT_PARAMS.plan_clip_s   # one catch-up clip holds the screen this long
DEFAULT_PREP_S = 15.0   # buffered_jump_sim.MR.prep_s: a free stick takes this long to set a clip up
PREP_S = DEFAULT_PREP_S  # this run's load time (--prep-s): how long after it aired a moment is ready, and the stick's wait after a cut or a piece
FLIP_S = 3.2         # buffered.py flip_s: an input flip between the sticks (the slowest usual)
DEFAULT_FLIP_S = FLIP_S
REACH_CAP_S = float("inf")   # --timing measured: no reach column goes deeper than timing.reach_s (175 s measured)
TIMINGS, DEADS = ("assumed", "measured"), ("hindsight", "floor")
TIMING, DEAD = TIMINGS[0], DEADS[0]   # this run's --timing and --dead (defaults: today's numbers)
PAST_CAP_S = 600.0   # `past_cap`: how long after the 180 s cap a later opening is still looked for (info only)
YES_S, CLIP_YES_S = 20.0, 60.0   # yes-rate: the wanted game's picture up this soon after the tick; else a catch-up clip of it this soon
DELAY_ERROR_KEY = "audit.delay_error_s"   # --delay-error: rides on the job's sets (no product flag); the true delay is the believed one plus this

# Why the catch-up route did not air a lost moment: the furthest step any tick got inside the moment's window, in this order.
CAUSES = ("no_stay_tick",        # no tick in the window at all: the replay was cutting, or the night ended
          "route_off",           # every tick had the route shut by Nick's controls, a home visit or buffered jump (catchup_gates)
          "own_game_on_screen",  # the moment's own game was on screen: the route never rewinds the game it is on
          "screen_mid_play",     # the game on screen was in play on every tick: Rule #1, a clip can't leave it
          "screen_not_started",  # the game on screen had not started or had ended: the replay gives it no dead time to use
          "dead_too_short",      # dead balls only too short for the whole play plus two flips and the margin
          "route_closed",        # a long-enough plain dead ball, on a held, crunch or high-leverage game (commercials only)
          "screen_on_clip",      # hidden stick busy: another catch-up clip was on screen
          "stick_prep",          # hidden stick busy: still setting up after a cut or the last piece
          "queued",              # hidden stick busy: an opening came, another clip took it
          "cant_finish",         # an opening came, but the clip could not have finished before the cap
          "abandoned")           # it started, and the rest never aired in time
STICK_BUSY = ("screen_on_clip", "stick_prep", "queued")

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
FLIP_LEAVE = {n for n, _ in gate.LEAVE_RULES}   # the flip's leave rules by bare name: what a held pick waits on
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
    route_on: bool = False                                # arbiter.catchup_route: the new route (route2), not the old one
    route_short: bool = False                             # arbiter.catchup_route_short: a clip is just the play while another waits
    route_clip_s: float = 0.0                             # audit.route_clip_s, what-if: a shorter clip (0 = the plan's CLIP_S)
    route2: list = field(default_factory=list)            # [catchup_route] (t, game on screen, a plain dead ball is open too) on a stay tick
    route_off: list = field(default_factory=list)         # [catchup_route] (t, game on screen) on a stay tick the gates shut the route (info only)
    route_off_gate: list = field(default_factory=list)    # ...and, tick for tick, which gate shut it (GATES; info only)
    held: int = 0            # [legal.leave_at_flip] picks remembered to leave later
    released: int = 0        # ...that went
    held_ticks: int = 0      # ticks a remembered pick waited
    over: Counter = field(default_factory=Counter)        # why a remembered pick was let go
    wants: list = field(default_factory=list)             # (t, game) for every tick the top pick was a cut: the yes-rate's denominator
    leave_sub: dict = field(default_factory=lambda: {k: Counter() for k in LEAVE_SUB_KEYS})   # gate:leave split by gate.leave_cause


LEAVE_SUB_KEYS = ("any", "first", "real", "lost")   # per sub-cause: ticks it said no, was the first refuser, a play really was on screen; value lost


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
    # [audit fix H] with legal.leave_at_flip the product's cut is a held pick (leave_held), which may leave at a low-leverage moment:
    # judged without it, gate:leave was charged on ticks where the cut went anyway
    d = Decision(now, "switch", pick, f"brain {host.brain.name}", host.base_state, kind="better", owner=host.base_owner,
                 leave_held=host._leave_at_flip())
    x = cands[pick]
    flip = gate.Flip(ReplayHost(host, cands), d, host.current, x.target, cands, None, now)
    timed, plan = flip.replan
    if timed and plan is not None and plan.ok:
        d.rewind_s = plan.rewind_s
    vetoes = [f"gate:{n}" for n, rule in gate.GATE_RULES if rule(flip)]
    host.leave_sub = gate.leave_cause(flip) if "gate:leave" in vetoes else ""
    return named, arb, vetoes, d


class AuditDecider(Decider):
    """decide() throws the decider's own proposal away and walks the brain's top pick down the real chain."""
    brain, tally, truth, sets, base_state, base_owner, pick_scores = None, None, None, {}, None, None, {}
    leave_sub = ""      # gate.leave_cause of the last flip judged, when its leave rule said no

    def __init__(self, cfg, delays, clock=None):
        super().__init__(Overlay(cfg, self.sets), delays, clock=clock)

    # ----- [legal.leave_at_flip] the wait the product runs in Decider._leave_held, driven from the audit's own tick. A wait tick
    # is charged to what still refuses the pick, so each rule's lost value stays comparable with the flag on and off.

    def _arbiter(self, d, cands, now, home):
        return d if self._leave_at_flip() else super()._arbiter(d, cands, now, home)

    def _arbiter_think(self, inp):
        if not self._leave_at_flip():
            return super()._arbiter_think(inp)
        ids = {o.id for o in inp.options}
        return contract.BrainOutput(tick=inp.tick, model_id=self.brain.name, made_at=inp.now, ttl_s=5.0,
                                    scores={k: v for k, v in self.pick_scores.items() if k in ids})

    def _leave_settle(self, d, now) -> None:
        """The audit ends its own holds (_held_tick): nothing goes through the product's settle."""

    def _landable(self, gid: str, cands: dict, now: float) -> bool:
        row = next((g for g in self.legal_facts(cands, now).games if g.game_id == gid), None)
        return row is not None and row.landable

    def _flip_vetoes(self, d: Decision, target: str | None, cands: dict, now: float) -> list[str]:
        f = gate.Flip(ReplayHost(self, cands), d, self.current, target, cands, None, now)
        timed, plan = f.replan
        if timed and plan is not None and plan.ok:
            d.rewind_s = plan.rewind_s
        names = [n for n, rule in gate.GATE_RULES if rule(f)]
        self.leave_sub = gate.leave_cause(f) if "leave" in names else ""
        return names

    def _go(self, d: Decision, now: float) -> Decision:
        self.tally.went += 1
        self._commit(d.target, kind=d.kind)
        self._land_behind(d.target, d.rewind_s)
        self.tally.screen.append((now + self.truth.tune_s, d.target))
        return d

    def _held_tick(self, cands: dict, now: float, stay: Decision) -> Decision | None:
        """A pick is waiting: stay and charge what still refuses it, or go. None: it is over, the tick goes on."""
        h, t = self._leave_pick, self.tally
        status, veto = self._leave_status(cands, now)
        if status == "over":
            t.over[veto.split(":")[0]] += 1
            return None
        t.wanted += 1
        t.wants.append((now, h.target))
        t.held_ticks += 1
        d = Decision(now, "switch", h.target, f"brain {self.brain.name}: held", self.base_state, kind=h.kind, owner=self.base_owner, leave_held=True)
        vetoes = self._flip_vetoes(d, cands[h.target].target, cands, now)
        if veto == "leave" and "leave" not in vetoes:
            self.leave_sub = "decider_only"     # the decider's own view of the leave (gate.DeciderHost) said no, the flip's did not
        blockers = ([f"legal:{veto}"] if veto == "no_landing" else [f"gate:{veto}"] if veto else []) + [f"gate:{v}" for v in vetoes if f"gate:{v}" != f"gate:{veto}"]
        if status == "wait" or vetoes:
            self._charge(h.target, blockers, now)
            self._note_route(cands, now)
            return stay
        self._leave_end("released", now)
        t.released += 1
        return self._go(d, now)

    def _decide_held(self, base: Decision, cands: dict, now: float) -> Decision:
        t = self.tally
        stay = Decision(now, "stay", self.current, f"brain {self.brain.name}: stay", base.state, owner=base.owner)
        if self._leave_pick is not None and (r := self._held_tick(cands, now, stay)) is not None:
            return r
        top = max(self.pick_scores, key=lambda o: (self.pick_scores[o], o))
        if top == "stay" or self.pick_scores[top] <= self.pick_scores.get("stay", 0.0):
            self._note_route(cands, now)
            return stay
        pick = top.removeprefix("cut:")
        t.wanted += 1
        t.wants.append((now, pick))
        named, ch, vetoes, d = judge(self, pick, cands, now)
        blockers = named + vetoes
        if not self._landable(pick, cands, now):
            blockers = ["legal:no_landing"] + blockers
        chosen = ch.option if ch is not None else None
        if blockers:
            self._charge(pick, blockers, now)
        if chosen is None or chosen.verb == "stay":
            self._note_route(cands, now)
            return stay
        d = Decision(now, "switch", chosen.game_id, f"arbiter over {self.brain.name}", base.state, kind="better", owner=base.owner, leave_held=True)
        names = self._flip_vetoes(d, cands[chosen.game_id].target, cands, now)
        land = self._landable(chosen.game_id, cands, now)
        if not land or set(names) & FLIP_LEAVE and set(names) <= FLIP_LEAVE:
            first = "no_landing" if not land else names[0]
            self._leave_begin(LeaveHold(chosen.id, chosen.game_id, legal.kind_of(chosen.verb), ch.why, self.current, now), first, now)
            t.held += 1
            self._note_route(cands, now)
            return stay
        if names:
            self._note_route(cands, now)
            return stay
        return self._go(d, now)

    def decide(self, cands, redzone=None, *, commit=True, reel=None):
        base = super().decide(cands, redzone, commit=False, reel=reel)
        now, t = self._clock(), self.tally
        t.ticks += 1
        if base.action in ("switch", "replay"):
            self.reject(base, backoff=False)
        self.base_state, self.base_owner = base.state, base.owner
        self.pick_scores = self.brain.scores(cands, self.current, now)
        if self._leave_at_flip():
            return self._decide_held(base, cands, now)
        top = max(self.pick_scores, key=lambda o: (self.pick_scores[o], o))
        stay = Decision(now, "stay", self.current, f"brain {self.brain.name}: stay", base.state, owner=base.owner)
        if top == "stay" or self.pick_scores[top] <= self.pick_scores.get("stay", 0.0):
            self._note_route(cands, now)
            return stay
        pick = top.removeprefix("cut:")
        t.wanted += 1
        t.wants.append((now, pick))
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
        if self.cfg.get("arbiter.catchup_route", False):
            self.tally.route_on = True
            self.tally.route_clip_s = float(self.cfg.get("audit.route_clip_s", 0.0) or 0.0)
            self.tally.route_short = bool(self.cfg.get("arbiter.catchup_route_short", False))
            ok, plain = self.catchup_gates(cur, cands, now)
            if ok:
                self.tally.route2.append((now, self.current, plain))
            else:
                self.tally.route_off.append((now, self.current))
                self.tally.route_off_gate.append(self._route_gate(cur, now))
            return
        if cur is None or not self.cfg.get("replay.enabled", True) or _holds_replays(cur.scored) or cur.scored.crunch_now \
                or cur.scored.high_leverage or self._crunch_on(cands):
            return
        p = self._arbiter_params()
        opts = legal.legal_options(self.legal_facts(cands, now))
        if arbiter.catchup_pick([("clip", 0, now)], opts, self._arbiter_show(cands, now, p), now, p) is not None:
            self.tally.route.append((now, self.current))

    def _route_gate(self, cur, now: float) -> str:
        """Which check of Decider.catchup_gates shut the route, in its order (info only: the real gate decides, this names it)."""
        u = self.user
        checks = (("screen_game_not_live", cur is None), ("replays_off", not self.cfg.get("replay.enabled", True)),
                  ("home_visit", self._home is not None), ("safe_mode", bool(self.safe_mode)), ("paused", bool(u.paused)),
                  ("locked_game", bool(u.locked_game)), ("manual_pause", (u.manual_until or 0.0) > now),
                  ("focus_mode", u.focus_mode != FocusMode.AUTOPILOT), ("buffered_jump", self.buffered_on()))
        return next((name for name, shut in checks if shut), "unnamed")

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
        sub = f"gate:leave_{self.leave_sub or 'unnamed'}" if "gate:leave" in blockers else ""
        if sub:
            t.leave_sub["any"][sub] += 1
            t.leave_sub["first"][sub] += int(first == "gate:leave")
            t.leave_sub["real"][sub] += int(mid)
        t.log[pick].append((now, first, len(blockers) == 1, bool(mid if first in LEAVE_RULES else lands), sub))


def seen(air: Air, screen: list) -> bool:
    """On screen for the whole play: the last cut before it started was to this game."""
    i = bisect.bisect_right([s[0] for s in screen], air.at - PLAY_LEN_S) - 1
    return i >= 0 and screen[i][1] == air.game_id


def floor_left(rep, st: float) -> float:
    """[--dead floor] The dead time still to come that the live code could count on at screen time st: guaranteed_left (q10 of the
    measured length, less what already ran) of the stoppage that began this dead ball. A plain dead ball, with no measured kind,
    guarantees nothing."""
    i = idx(rep.wall, st)
    if i < 0 or st > rep.wall[-1]:
        return 0.0
    for k in range(i, max(-1, i - 40), -1):
        stop = espn.stoppage_for(rep.league, rep.plays[k])
        if stop == Stoppage.NONE and k == i and (rep.league in CONTINUOUS or rep.league.base == League.NBA):
            stop = held_stop(rep.league, rep.wall, rep.plays, i)
        if stop not in (Stoppage.NONE, Stoppage.UNKNOWN):
            return guaranteed_left(rep.league, stop.value, st - rep.wall[k])
        if is_action(rep.league, rep.plays[k]):
            break
    return 0.0


def dead_left(rep, st: float, stats: Counter | None = None) -> tuple[bool, bool, float]:
    """dead_at, with --dead floor: the seconds left are what the live code could know in advance, never more than the truth (a floor
    above the truth would have run a clip into live play: counted in `floor_over_truth` ticks, info only)."""
    dead, brk, left = dead_at(rep.league, rep.wall, rep.plays, st)
    if DEAD != "floor" or not dead:
        return dead, brk, left
    fl = floor_left(rep, st)
    if fl > left and stats is not None:
        stats["floor_over_truth"] += 1
    return dead, brk, min(left, fl)


def use_timing(timing: str = TIMINGS[0], dead: str = DEADS[0], prep_s: float | None = None, sets: dict | None = None) -> None:
    """This process's knobs (a worker has its own module globals). `measured`: timing.* from the config defaults and --set."""
    global TIMING, DEAD, PREP_S, FLIP_S, REACH_CAP_S
    if timing not in TIMINGS or dead not in DEADS:
        raise ValueError(f"--timing {timing!r} / --dead {dead!r}")
    TIMING, DEAD = timing, dead
    m = measured_timing.route_costs(measured_cfg(sets)) if timing == "measured" else None
    FLIP_S, REACH_CAP_S = (DEFAULT_FLIP_S, float("inf")) if m is None else (m["flip_s"], m["reach_s"])
    PREP_S = prep_s if prep_s is not None else DEFAULT_PREP_S if m is None else m["prep_s"]


def measured_cfg(sets: dict | None) -> NS:
    """Config-like over default_config.yaml and this run's --set values, with timing.measured on."""
    over = {**(sets or {}), measured_timing.TIMING_FLAG: True}
    return NS(get=lambda k, d=None: over[k] if k in over else rz_config.default(k, d))


def last_flip(m: Air, reach: float, tune_s: float) -> float:
    """The last second a catch-up of this play can start: its picture must be up within `reach` of the play's start on air,
    and never 180 s or more after it (Nick's rule)."""
    return m.at - PLAY_LEN_S + min(reach, 180.0, REACH_CAP_S) - tune_s


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
        dead, _, left = dead_left(r, now - lag_of(truth.lag, gid))
        i = bisect.bisect_left(cuts, now)
        if not dead or left < need or (i < len(cuts) and cuts[i] < now + need):
            continue
        cands = [(k, m.value, m.at, last_flip(m, reach, tune), CLIP_S, m.at + PREP_S) for k, m in enumerate(pool)]
        first = arbiter.plan_clips(cands, now, tune, PREP_S)
        if first is not None:
            shown.add(pool[first])
            free_at = now + need
    return shown


def route_shown(unseen: list[Air], t: Tally, truth: Truth, reach: float, why: dict | None = None,
                aired: list | None = None) -> tuple[dict[Air, bool], Counter]:
    """SIMULATED arbiter.catchup_route (redzone/noggin/catchup.py) on the replay's own screen, at this rewind reach: which unseen moments
    air before the hard cap, each piece inside dead time on the game on screen (truth, with a margin) on a tick the route was open.
    Causal: a clip starts without knowing the next dead ball is coming. {moment: airs marked as a replay}, and what became of the rest.
    Clips can't move the replay's own cuts; a cut on the way ends the dead time and takes the hidden stick for its prep.
    `why` (info only, it changes no choice): filled with {moment not shown: (its cause from CAUSES, an opening came within PAST_CAP_S
    after the cap)}. `aired` (info only): gets (when its last piece came up, game) for every clip that aired whole."""
    r = catchup.Route(reach_s=min(reach, catchup.HARD_CAP_S, REACH_CAP_S), clip_s=t.route_clip_s or CLIP_S, flip_s=FLIP_S, prep_s=PREP_S, lead_s=3.0, margin_s=2.0,
                      short=t.route_short)
    moments = {f"{m.game_id}:{m.at}:{m.kind}:{i}": m for i, m in enumerate(unseen)}
    pool = [catchup.Moment(k, m.game_id, float(m.value), m.at, PLAY_LEN_S) for k, m in moments.items()]
    tune, cuts, ups = truth.tune_s, sorted(s[0] - truth.tune_s for s in t.screen), [s[0] for s in t.screen]
    shown: dict[Air, bool] = {}
    marked_ids: set[str] = set()
    stats: Counter = Counter()
    doing, stick_end, busy_to, aired_of_game, aired_late = None, float("-inf"), float("-inf"), defaultdict(list), {}
    open_ticks, closed_ticks = defaultdict(int), defaultdict(int)     # per moment: a dead tick in its window, route open / closed by a held game
    step: dict[str, int] = {}           # per moment: the furthest CAUSES step any tick in its window got (info only)
    openings: list[tuple[float, str]] = []     # ticks any waiting clip could have started on: dead, long enough, route open
    by_aired = sorted(pool, key=lambda x: x.aired)
    aired_at = [x.aired for x in by_aired]
    span_s = catchup.HARD_CAP_S         # no window reaches further past the airing than the cap

    def waiting(now: float) -> list[catchup.Moment]:
        """The moments whose window holds this tick: set up, and a first piece now would still be on time."""
        lo, hi = bisect.bisect_left(aired_at, now - span_s), bisect.bisect_right(aired_at, now - PREP_S)
        return [x for x in by_aired[lo:hi] if moments[x.id] not in shown and catchup.on_time(x, now + r.flip_s, 0.0, r)]

    def got(x: catchup.Moment, cause: str) -> None:
        step[x.id] = max(step.get(x.id, 0), CAUSES.index(cause))

    def note(now: float, gid: str, cause: str) -> None:
        for x in waiting(now):
            got(x, cause if x.game_id != gid else "own_game_on_screen")

    def known(x: catchup.Moment, now: float) -> bool:
        i = bisect.bisect_right(ups, x.aired) - 1
        for k in range(max(i, 0), len(t.screen)):
            at, g = t.screen[k]
            if at > now:
                break
            if g == x.game_id and (k > i or at <= x.aired):
                return True
        return any(a > x.aired for a in aired_of_game[x.game_id])

    def account(x: catchup.Moment, now: float, gid: str, ok: bool) -> None:
        if x.game_id != gid and x.aired + PREP_S <= now and catchup.on_time(x, now + r.flip_s, 0.0, r):
            (open_ticks if ok else closed_ticks)[x.id] += 1

    if why is not None:
        for now, gid in t.route_off:
            note(now, gid, "route_off")
    for now, gid, plain in t.route2:
        rep = truth.replays.get(gid)
        if rep is None or now < busy_to:
            if why is not None:
                note(now, gid, "screen_on_clip" if rep is not None else "screen_not_started")
            continue
        st = now - lag_of(truth.lag, gid)
        dead, brk, left = dead_left(rep, st, stats)
        i = bisect.bisect_left(cuts, now)
        end = min(now + left, cuts[i] if i < len(cuts) else float("inf"))
        if why is not None:
            fit, off_air = catchup.room(now, end, r), not rep.wall or st < rep.wall[0] or st > rep.wall[-1]
            if dead and not off_air and (brk or plain) and fit >= r.lead_s + PLAY_LEN_S + 1.0:
                openings.append((now, gid))
            for x in waiting(now):
                got(x, "own_game_on_screen" if x.game_id == gid else "screen_not_started" if off_air else "screen_mid_play" if not dead
                    else "dead_too_short" if fit < catchup.first_piece_s(x, r) else "route_closed" if not (brk or plain) else "stick_prep")
        if dead:
            for x in pool:
                if catchup.room(now, end, r) >= catchup.first_piece_s(x, r):
                    account(x, now, gid, brk or plain)
        if doing is not None and catchup.overdue(doing[0], doing[1], now, r):
            stats["abandoned"] += 1
            doing = None
        if not dead or not (brk or plain):
            continue
        ready = max(stick_end + PREP_S, (cuts[i - 1] if i > 0 else float("-inf")) + PREP_S)
        rest = [x for x in pool if moments[x.id] not in shown and x.game_id != gid]
        p = catchup.next_piece(rest if doing is None else (), now, end, doing=doing, ready_at=ready, known=lambda x: known(x, now), r=r)
        if why is not None and now >= ready:      # the stick was free: whoever had room and did not go was queued, or could not have finished
            took = p.moment.id if p is not None else doing[0].id if doing is not None else None
            for x in waiting(now):
                if x.game_id != gid and x.id != took and catchup.room(now, end, r) >= catchup.first_piece_s(x, r):
                    got(x, "queued" if took is not None else "cant_finish")
            if p is not None and not p.last:
                got(p.moment, "abandoned")       # stays its cause only if the rest never airs
        if p is None:
            continue
        stick_end = busy_to = p.until
        stats["pieces"] += 1
        aired_of_game[p.moment.game_id].append(p.moment.aired)
        aired_late[p.moment.id] = catchup.lateness(p.moment, now + r.flip_s, p.offset_s, r)
        if p.marked:
            marked_ids.add(p.moment.id)
        if p.last:
            marked = p.moment.id in marked_ids or p.marked
            shown[moments[p.moment.id]] = marked
            if aired is not None:
                aired.append((now + r.flip_s, p.moment.game_id))
            stats["clips"] += 1
            stats["value"] += moments[p.moment.id].value
            stats["marked_value"] += moments[p.moment.id].value if marked else 0.0
            stats["pieced_clips"] += int(p.offset_s > 0.0)
            doing = None
        else:
            doing = (p.moment, p.offset_s + p.seconds)
    stats["max_late_s"] = round(max(aired_late.values(), default=0.0), 1)
    if doing is not None:
        stats["unfinished"] += 1
    for x in pool:
        if moments[x.id] not in shown:
            stats["left_" + ("no_dead_time" if not open_ticks[x.id] and not closed_ticks[x.id] else "route_closed" if not open_ticks[x.id] else "one_stick_queue")] += moments[x.id].value
            if why is not None:
                cap_at = x.aired - x.play_s - r.lead_s + min(r.reach_s, catchup.HARD_CAP_S) - r.flip_s      # the last flip that is on time
                k = bisect.bisect_left(openings, (cap_at, ""))
                later = any(g != x.game_id for at, g in openings[k:bisect.bisect_right(openings, (cap_at + PAST_CAP_S, "\uffff"))])
                why[moments[x.id]] = (CAUSES[step.get(x.id, 0)], later)
    return shown, stats


def yes_counts(wants: list, screen: list, clips: list = ()) -> tuple[int, int]:
    """Of the ticks the brain wanted another game (t, game): how many had that game's picture up within YES_S, and how many of the
    rest had a catch-up clip of that game come up within CLIP_YES_S. `screen` is (picture up at, game), `clips` (clip up at, game)."""
    ups, cls = defaultdict(list), defaultdict(list)
    for at, g in sorted(screen):
        ups[g].append(at)
    for at, g in sorted(clips):
        cls[g].append(at)
    live = clip = 0
    for t, g in wants:
        i = bisect.bisect_right(ups[g], t)
        if i < len(ups[g]) and ups[g][i] <= t + YES_S:
            live += 1
            continue
        j = bisect.bisect_right(cls[g], t)
        clip += int(j < len(cls[g]) and cls[g][j] <= t + CLIP_YES_S)
    return live, clip


def charge_night(t: Tally, truth: Truth, league_row: dict) -> None:
    """Charge every unseen must-see moment to the last refusal of its game in the run-up. Seen and uncharged are counted."""
    unseen = [m for ms in truth.moments.values() for m in ms if not seen(m, t.screen)]
    why: dict = {r: {} for r in REACHES}
    clips: list = []
    if t.route_on:
        results = {r: route_shown(unseen, t, truth, r, why[r], clips if r == REACHES[-1] else None) for r in REACHES}
        shown = {r: set(results[r][0]) for r in REACHES}
        for r in REACHES:
            late = max(league_row["route"][str(r)].get("max_late_s", 0.0), results[r][1].pop("max_late_s", 0.0))
            league_row["route"][str(r)].update(results[r][1])
            league_row["route"][str(r)]["max_late_s"] = late
    else:
        shown = {r: catchup_shown(unseen, t, truth, r) for r in REACHES}
    live, clip = yes_counts(t.wants, t.screen, clips)
    for k, n in (("wanted", len(t.wants)), ("live", live), ("clip", clip)):
        league_row["yes"][k] += n
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
                if rule == "gate:leave" and len(real) > 1 and real[1]:
                    league_row["leave_sub"]["lost"][real[1]] += m.value
                league_row["moments"][rule] += 1
                if only:
                    league_row["lost_alone"][rule] += m.value
                for r in REACHES:
                    if m not in shown[r]:
                        league_row["lost_after_catchup"][str(r)][rule] += m.value
                        league_row["avoidable_lost"][str(r)] += 0.0 if (real and real[0]) else m.value
                        if m in why[r]:
                            cause, later = why[r][m]
                            league_row["residual"][str(r)][cause] += m.value
                            league_row["residual_by_rule"][str(r)][f"{rule}|{cause}"] += m.value
                            league_row["past_cap"][str(r)][cause] += m.value if later else 0.0
            else:
                league_row["not_asked"] += m.value


def new_row() -> dict:
    return {"must_total": 0.0, "must_seen": 0.0, "not_asked": 0.0, "lost": Counter(), "lost_alone": Counter(), "moments": Counter(),
            "ticks": 0, "wanted": 0, "went": 0, "held": 0, "released": 0, "held_ticks": 0, "over": Counter(), "refused": Counter(), "alone": Counter(), "any_rule": Counter(), "protected": Counter(), "high": Counter(),
            "lost_after_catchup": {str(r): Counter() for r in REACHES}, "route": {str(r): Counter() for r in REACHES}, "avoidable_lost": {str(r): 0.0 for r in REACHES},
            "residual": {str(r): Counter() for r in REACHES}, "residual_by_rule": {str(r): Counter() for r in REACHES},
            "past_cap": {str(r): Counter() for r in REACHES},
            "yes": Counter(), "leave_sub": {k: Counter() for k in LEAVE_SUB_KEYS}}


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
        err = float(sets.get(DELAY_ERROR_KEY, 0.0) or 0.0)
        if err:     # [--delay-error] the picture really runs this much later (or earlier) than the app believes; the app is not told
            kw["true_delay"] = (kw.get("delay", 35.0) if kw.get("true_delay") is None else kw["true_delay"]) + err
        lag = kw.get("feed_lag", 8.0) + (kw.get("delay", 35.0) if kw.get("true_delay") is None else kw["true_delay"])
        truth.bind(replays, lag, kw.get("tune_s", 5.0))
        t = Tally()
        current.append(t)
        rep = real_run(replays, cfg, my_teams, **kw)
        charge_night(t, truth, row)
        for k in ("ticks", "wanted", "went", "held", "released", "held_ticks"):
            row[k] += getattr(t, k)
        row["over"].update(t.over)
        for k in ("refused", "alone", "any_rule", "protected", "high"):
            row[k].update(getattr(t, k))
        for k in ("any", "first", "real"):
            row["leave_sub"][k].update(t.leave_sub[k])
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
    """One league under one brain: the row for the table. (league, brain, sets, cap, prep_s[, timing, dead]): a worker process has its
    own module, so the knobs ride in on the job."""
    league, brain_name, sets, cap, prep_s, *knobs = job
    use_timing(*knobs, prep_s=prep_s, sets=sets)
    row = new_row()
    script, args = SCRIPTS[league]
    extra = ["--split", "fit"]
    with replay(brain_name, sets, row, cap) as high:
        c = card(script, *args, *extra)
    nights = c.get("per_night", [])
    row["card"] = {"nights": len(nights), "entries": sum(n["entries"] for n in nights),
                   "entries_mid_play": sum(n["entries_mid_play"] for n in nights),
                   "cuts_high": len(high), "mid_play_leaves": sum(n.get("cuts", 0) for n in nights), "dead_air_pct": c.get("dead_air_pct"),
                   "plays_missed_entry": c.get("plays_missed_entry", sum(n.get("plays_missed_entry", 0) for n in nights)),
                   "switches_per_hour": c.get("switches_per_hour"), "hours": c.get("hours", sum(n.get("hours", 0.0) for n in nights))}
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
    return "\n".join(lines + catchup_lines(rows) + residual_lines(rows) + leave_lines(rows) + yes_lines(rows))


def leave_lines(rows: list[dict]) -> list[str]:
    """gate:leave split by which part of the leave rule said no (gate.leave_cause), most ticks first."""
    tot = {k: Counter() for k in LEAVE_SUB_KEYS}
    for r in rows:
        for k in LEAVE_SUB_KEYS:
            tot[k].update(r.get("leave_sub", {}).get(k, {}))
    if not tot["any"]:
        return []
    lines = ["", "gate:leave by sub-cause (said no = any tick; first = it was the first refuser; real% = a play really was on screen)",
             f"{'sub-cause':<28}{'said no':>8}{'first':>8}{'real%':>7}{'lost':>8}"]
    for sub, n in sorted(tot["any"].items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"{sub:<28}{n:>8}{tot['first'][sub]:>8}{100.0 * tot['real'][sub] / n:>6.0f}%{tot['lost'][sub]:>8.1f}")
    return lines


def yes_rate(rows: list[dict]) -> dict:
    """{wanted, live, clip, yes_pct, clip_pct} over these rows: see yes_counts. Percentages are of `wanted` (0 when nothing was)."""
    y = Counter()
    for r in rows:
        y.update(r.get("yes", {}))
    n = y["wanted"]
    return {"wanted": n, "live": y["live"], "clip": y["clip"], "yes_pct": round(100.0 * y["live"] / n, 1) if n else 0.0,
            "clip_pct": round(100.0 * y["clip"] / n, 1) if n else 0.0}


def yes_lines(rows: list[dict]) -> list[str]:
    """The yes-rate per league and brain, per brain, and in total."""
    if not yes_rate(rows)["wanted"]:
        return []
    lines = ["", f"yes-rate: of the ticks the brain wanted another game, its picture was up within {YES_S:g} s; else a catch-up clip of it "
                 f"within {CLIP_YES_S:g} s (SIMULATED, reach {REACHES[-1]} s)",
             f"{'league/brain':<22}{'wanted':>8}{'yes':>8}{'yes%':>7}{'clip':>8}{'clip%':>7}{'either%':>9}"]
    brains = list(dict.fromkeys(r["brain"] for r in rows))
    groups = [(f"{r['league']}/{r['brain']}", [r]) for r in rows] + [(f"TOTAL {b}", [r for r in rows if r["brain"] == b]) for b in brains]
    for name, part in groups + ([("TOTAL", rows)] if len(brains) > 1 else []):
        y = yes_rate(part)
        lines.append(f"{name:<22}{y['wanted']:>8}{y['live']:>8}{y['yes_pct']:>6.1f}%{y['clip']:>8}{y['clip_pct']:>6.1f}%{y['yes_pct'] + y['clip_pct']:>8.1f}%")
    return lines


def rule1_counters(rows: list[dict]) -> dict:
    """The Rule #1 counters of a run, summed over its rows."""
    return {k: sum(r["card"].get(k, 0) or 0 for r in rows) for k in ("entries_mid_play", "mid_play_leaves", "cuts_high")}


def residual_lines(rows: list[dict], reach: int = REACHES[-1]) -> list[str]:
    """Why the catch-up route did not air what is still lost, at Nick's cap: per cause and league, biggest first. Simulated."""
    leagues = list(dict.fromkeys(r["league"] for r in rows))
    by = {lg: Counter() for lg in leagues}
    past, rules = Counter(), defaultdict(Counter)
    for r in rows:
        by[r["league"]].update(r.get("residual", {}).get(str(reach), {}))
        past.update(r.get("past_cap", {}).get(str(reach), {}))
        for key, v in r.get("residual_by_rule", {}).get(str(reach), {}).items():
            rule, _, cause = key.partition("|")
            rules[cause][rule] += v
    total = Counter()
    for c in by.values():
        total.update(c)
    if not sum(total.values()):
        return []
    lines = ["", f"still lost after catch-up at reach {reach} s, by why the route did not air it (SIMULATED)",
             f"{'cause':<22}{'lost':>8}" + "".join(f"{lg:>8}" for lg in leagues) + f"{'past cap':>10}  top rules"]
    for cause, v in sorted(total.items(), key=lambda kv: -kv[1]):
        top = ", ".join(f"{k} {n:.0f}" for k, n in rules[cause].most_common(3))
        lines.append(f"{cause:<22}{v:>8.1f}" + "".join(f"{by[lg][cause]:>8.1f}" for lg in leagues) + f"{past[cause]:>10.1f}  {top}")
    lines.append(f"{'TOTAL':<22}{sum(total.values()):>8.1f}" + "".join(f"{sum(by[lg].values()):>8.1f}" for lg in leagues) + f"{sum(past.values()):>10.1f}")
    lines.append(f"hidden stick busy ({' + '.join(STICK_BUSY)}): {sum(total[c] for c in STICK_BUSY):.1f}")
    return lines


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
    for reach in REACHES:
        r = Counter()
        for row in rows:
            r.update(row["route"][str(reach)])
        r["max_late_s"] = max((row["route"][str(reach)].get("max_late_s", 0.0) for row in rows), default=0.0)     # the latest, not a sum of rows
        if r["pieces"]:
            lines.append(f"route at reach {reach} s (SIMULATED): clips {r['clips']} of value {r['value']:.0f} (marked as replays {r['marked_value']:.0f}), "
                         f"pieces {r['pieces']}, clips in 2+ pieces {r['pieced_clips']}, started but not finished {r['abandoned'] + r['unfinished']}, "
                         f"left: no dead time {r['left_no_dead_time']:.0f}, route closed {r['left_route_closed']:.0f}, one stick queue {r['left_one_stick_queue']:.0f}, latest {r['max_late_s']:.1f} s")
    return lines


def avoidable(rows: list[dict], reach: int) -> float:
    return sum(row["avoidable_lost"][str(reach)] for row in rows)


def worse_key(rows: list[dict]) -> tuple:
    """Orders the arms of --delay-error: more Rule #1 harm is worse (mid-play entries and high-leverage leaves, then every mid-play
    leave), then the lower yes-rate."""
    c = rule1_counters(rows)
    return (c["entries_mid_play"] + c["cuts_high"], c["mid_play_leaves"], -yes_rate(rows)["yes_pct"])


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
    ap.add_argument("--prep-s", type=float, default=None,
                    help="what-if: seconds the hidden stick needs to tune and seek to a catch-up clip (default 15, or timing.clip_prep_s with "
                         "--timing measured; 60 models a re-tuned stick)")
    ap.add_argument("--timing", default=TIMINGS[0], choices=TIMINGS, help="catch-up flip, clip load and reach: assumed (today) or measured (timing.*)")
    ap.add_argument("--dead", default=DEADS[0], choices=DEADS, help="catch-up pieces sized by the real dead time (hindsight, today) or the floor "
                                                                   "the live code could know (floor)")
    ap.add_argument("--delay-error", type=float, default=0.0, metavar="S",
                    help="run twice, with the TRUE picture delay S s more and S s less than the app believes; print the worse arm (default 0: one run)")
    ap.add_argument("--json", type=Path, default=None)
    a = ap.parse_args()
    if a.prep_s is not None and a.prep_s < 0:
        ap.error("--prep-s must be 0 or more")
    if a.delay_error < 0:
        ap.error("--delay-error must be 0 or more")
    sets = parse_sets(a.set)
    if a.timing == "measured":
        sets.setdefault(measured_timing.TIMING_FLAG, True)      # the replayed decider reads the same timings
    use_timing(a.timing, a.dead, a.prep_s, sets)
    a.prep_s = PREP_S
    jobs = [(lg, b, sets, a.cap, a.prep_s) + ((a.timing, a.dead) if (a.timing, a.dead) != (TIMINGS[0], DEADS[0]) else ())
            for lg in a.leagues for b in a.brains]
    def run_all(todo: list[tuple]) -> list[dict]:
        if a.workers == 1 or len(todo) == 1:
            return [run_one(j) for j in todo]
        with ProcessPoolExecutor(max_workers=a.workers) as ex:
            return list(ex.map(run_one, todo))

    arms: dict[str, list[dict]] = {}
    if a.delay_error:
        for err in (a.delay_error, -a.delay_error):
            arms[f"{err:+g}"] = run_all([(j[0], j[1], {**sets, DELAY_ERROR_KEY: err}, *j[3:]) for j in jobs])
        worse = max(arms, key=lambda k: worse_key(arms[k]))
        rows = arms[worse]
    else:
        rows = run_all(jobs)
    print(table(rows))
    if arms:
        print(f"\ndelay error {a.delay_error:g} s: the TRUE picture delay shifted from what the app believes; the table above is the worse arm ({worse} s)")
        for name, arm in arms.items():
            c, y = rule1_counters(arm), yes_rate(arm)
            print(f"  true delay {name} s: entries_mid_play {c['entries_mid_play']}, mid_play_leaves {c['mid_play_leaves']}, high-leverage leaves {c['cuts_high']}, "
                  f"yes {y['yes_pct']:.1f}%, clip {y['clip_pct']:.1f}%, went {sum(r['went'] for r in arm)} of {sum(r['wanted'] for r in arm)}")
    if a.prep_s != DEFAULT_PREP_S:
        print(f"what-if: hidden stick load time {a.prep_s:g} s (default {DEFAULT_PREP_S:g} s)")
    if a.timing != TIMINGS[0]:
        print(f"timing {a.timing}: flip {FLIP_S:g} s, clip load {PREP_S:g} s, reach capped at {REACH_CAP_S:g} s (SMALL SAMPLES, Nov 9, busy Mac)")
    if a.dead != DEADS[0]:
        print(f"dead {a.dead}: catch-up pieces sized by the q10 dead time the live code could know in advance, not the real length")
    if a.json:
        out = {"sets": sets, "rows": rows, "rules": merge(rows), "avoidable_lost": {str(r): avoidable(rows, r) for r in REACHES}}
        if a.prep_s != DEFAULT_PREP_S:      # the default run's file stays byte-identical
            out["prep_s"] = a.prep_s
        if a.timing != TIMINGS[0]:
            out["timing"] = a.timing
        if a.dead != DEADS[0]:
            out["dead"] = a.dead
        if arms:
            out["delay_error_s"], out["worse_arm"] = a.delay_error, worse
            out["arms"] = {name: {"rows": arm, "rule1": rule1_counters(arm), "yes": yes_rate(arm)} for name, arm in arms.items()}
        a.json.write_text(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
