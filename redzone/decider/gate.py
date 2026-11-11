"""One commit gate at the flip for every non-tap switch: an ordered list of named rules, re-read on every poll.

The controller polls `may_cut` until the switch goes or times out. With commit_gate.enabled on, `Engine._act.cut_veto`
asks `commit_gate` here instead of branching by kind, and the answer is the name of the first rule that says no.
Kinds differ only in which landing and leave BAR a rule uses (a jump's picture is judged by jump_lands_dead, an entry
by the plan its decision was made on); no kind has its own path around a rule. Phone taps never come here: a second
tap is Nick's call, and `Engine._act.cut_veto` keeps their path.

Host is the Engine (or a replay stand-in): this module reads it and never imports it. Pure apart from those reads.

Why: Rule #1 kept leaking through paths with their own checks (P1-REVIEW-01, noggin.md 11b); one gate with a name per
veto closes that class and makes every held switch explainable (build plan WP-05, brain-vs-code S4).
"""
from __future__ import annotations

import math
import time
from dataclasses import replace as _replace
from functools import cached_property
from types import SimpleNamespace as _NS
from typing import Any, Callable

from redzone.channels import redzone_on
from redzone.decider import cuts
from redzone.decider.cuts import EntryPlan, Landing, Window
from redzone.decider.decider import ENTRY_FASTEST_S, ENTRY_URGENT_S, PSEUDO, REDZONE, REEL, Candidate
from redzone.decider.screen import break_on_arrival, catchable, leave_window
from redzone.decider.whistle import WHISTLE_LEAGUES
from redzone.models import Phase
from redzone.moments import slate as slate_mod
from redzone.reel import REEL_DIR


class Flip:
    """The facts of one poll. Each is read once, when a rule first needs it, so a poll judges one moment."""

    def __init__(self, host: Any, d: Any, before: str | None, target: str | None, cands: dict,
                 replay_exit: float | None = None, now: float | None = None):
        self.host, self.d, self.before, self.target, self.cands = host, d, before, target, cands
        self.replay_exit = replay_exit
        self.now = time.time() if now is None else now

    @property
    def jump(self) -> bool:
        return self.d.kind == "jump"

    @property
    def is_switch(self) -> bool:
        return self.d.action == "switch"

    @cached_property
    def game(self) -> Any:
        """The game this switch goes to; None for a replay, RedZone, a reel, or a game the merger lost."""
        if not self.is_switch or self.d.target in (REDZONE, REEL):
            return None
        return self.host.merger.games.get(self.d.target or "")

    @property
    def to_game(self) -> bool:
        """This switch needs a real game behind it (a replay and the pseudo channels have their own rules)."""
        return self.is_switch and self.d.target not in (REDZONE, REEL)

    @cached_property
    def timeline(self) -> Any:
        g = self.game
        return self.host.decider.timelines.get(g.game_id) if g is not None else None

    @cached_property
    def cost(self) -> float:
        return self.host.control.deadline_cost(self.target)

    @cached_property
    def delay(self) -> Any:
        return self.host.delays.get(self.target or "unknown")

    @cached_property
    def live(self) -> dict:
        """The decision's candidates on the scores of NOW: a game can turn into a red zone trip during the wait."""
        scored = self.host.scored
        return {gid: _replace(c, scored=scored[gid]) if gid in (self.before, self.d.target) and gid in scored else c
                for gid, c in self.cands.items()}

    @cached_property
    def replan(self) -> tuple[bool, Any]:
        """(this entry is timed, its plan now). Planned on the bar the decision used, and only where the decision
        was timed. [P1-REVIEW-01] Whistle-timed leagues (hold_only) count: their flip can come 25 s after the decision.
        The scrub path doesn't: its picture isn't the live edge a plan judges (landing_whistle gates it)."""
        d, host = self.d, self.host
        if self.jump or self.game is None or d.kind == "user":
            return False, None
        dec = host.decider
        one = {d.target: Candidate(host.scored[d.target], self.target)} if d.target in host.scored else {}
        x = one.get(d.target)
        timed = dec._cut_timed(d.target, one)
        entry_only = not timed and not _own_frame(self) and dec._entry_only(x, d) \
            and not host.control._scrub_wanted(self.target)
        if not (timed or entry_only):
            return False, None
        pos = entry_only and dec._possession_start(x, d)
        urgent = _crunch_earned(x) if d.kind == "crunch" else (entry_only and dec._entry_urgent(d, x, one))   # [WP-15B] the thin margin is earned, not claimed
        return True, dec.entry_plan(d.target, one, self.now, self.cost, urgent=urgent or pos, possession=pos)


# ----- the rules: True means veto --------------------------------------------------------------------------------

def _paused_or_dry_run(f: Flip) -> bool:
    return bool(f.host.decider.user.paused or f.host.cfg.get("mode.dry_run", True))


def _deferred(f: Flip) -> bool:
    return f.now < f.d.at + float(getattr(f.d, "defer_s", 0.0) or 0.0)   # [EXP-803 A1] the break's tail


def _replay_used(f: Flip) -> bool:
    return f.d.action == "replay" and not (f.d.replay and not f.d.replay.replayed)


def _redzone_off(f: Flip) -> bool:
    return f.is_switch and f.d.target == REDZONE and not redzone_on(f.host.merger.games.values())


def _reel_missing(f: Flip) -> bool:
    return f.is_switch and f.d.target == REEL and not (
        bool(f.target and f.target.startswith("reel:")) and (REEL_DIR / "reel.mp4").exists())


def _not_live(f: Flip) -> bool:
    return f.to_game and (f.game is None or f.game.phase != Phase.LIVE)


def _feed_stale(f: Flip) -> bool:
    return f.game is not None and bool(f.host.feeds.stale(f.game.game_id))


def _target_moved(f: Flip) -> bool:
    return f.game is not None and getattr(f.host.where.get(f.game.game_id), "target", None) != f.target


def _no_timeline(f: Flip) -> bool:
    return f.to_game and f.timeline is None


def _chase_gone(f: Flip) -> bool:
    return f.d.kind == "chase" and f.timeline is not None and catchable(f.timeline, f.delay, f.now, f.cost) is None


def _landing(f: Flip) -> bool:
    """Rule #1: the picture must still come up on a dead ball."""
    if f.game is None:
        return False
    if _lands_after_bucket(f):
        return False
    if f.jump:
        if not f.host.decider.jump_lands_dead(f.d.target, f.live.get(f.d.target), f.now, f.cost):
            return True
    else:
        timed, plan = f.replan
        if timed and plan is not None and not plan.ok:
            return True
    return _lands_with_clock_running(f) or _walk_landing_too_late(f)


def _landing_unproven(f: Flip) -> bool:
    """A timed entry with no landing evidence left waits, like one that has it and fails. [D-01] No live fallback."""
    timed, plan = f.replan
    return timed and plan is None and bool(f.host.cfg.get("decider.entry.enabled", True))


def _rewind_changed(f: Flip) -> bool:
    """The hidden stick was cued for the decision's rewind: a different one now means a different frame on screen."""
    timed, plan = f.replan
    return timed and plan is not None and plan.ok and plan.rewind_s != getattr(f.d, "rewind_s", 0.0)


def _arrival_break(f: Flip) -> bool:
    """Not onto a commercial. A planned rewind is exempt: landing before the snap on purpose, the tail is the line-up."""
    if f.game is None or f.jump or f.timeline is None:
        return False
    timed, plan = f.replan
    if timed and plan is not None and plan.rewind_s:
        return False
    return bool(break_on_arrival(f.timeline, f.delay, f.now, f.now + f.cost))


def _hold(f: Flip) -> bool:
    """[P1-REVIEW-06] The screen's game holds the screen on the scores of NOW. Non-jump kinds may leave a held game on
    purpose (crunch to better crunch), so for them it vetoes only a hold the decision didn't know about."""
    if not f.is_switch or f.before is None or f.before not in f.live:
        return False
    dec = f.host.decider
    if not dec._jump_hold(f.live[f.before]):
        return False
    return f.jump or f.before not in f.cands or not dec._jump_hold(f.cands[f.before])


def _ending_airing(f: Flip) -> bool:
    """The game on screen ended in the data but its last play is still airing (noggin.md 11b: a switch off an ended
    game skipped the leave gate)."""
    if not f.is_switch or not f.before or f.before in PSEUDO or f.replay_exit is not None:
        return False
    g = f.host.merger.games.get(f.before)
    if g is not None and g.phase != Phase.POST:
        return False
    return f.host.decider._ending_still_airing(f.before, f.now) > 0


def _replay_exit(f: Flip) -> bool:
    return f.replay_exit is not None and f.now < f.replay_exit


def _leave(f: Flip) -> bool:
    """Leaving the game on screen. A jump uses the rotation's gate (dead windows, low-leverage leaves), anything else the
    'data covers the screen' window. Coming off a replay is _replay_exit's."""
    if f.replay_exit is not None:
        return False
    if f.jump:
        ok = f.host.decider.jump_may_leave(f.before, f.live, f.now, f.host._leave_margin())
    else:
        ok = _plain_leave(f, 8.0)   # the value cut_veto has always used
        if not ok and getattr(f.d, "leave_held", False):       # [legal.leave_low_leverage] a held pick may go at a low-leverage moment
            ok = not _ending_airing(f) and f.host.decider.held_low_leverage_exit(f.before, f.live, f.now)
    return not ok or _leaves_high_leverage_play(f)


def _landing_whistle(f: Flip) -> bool:
    """[EXP-610] Scrub path: the delayed picture at a stopped clock. A jump's own bar already judges its delayed picture."""
    return not f.jump and not f.host._flip_lands_whistle(f.d, f.target)


Rule = tuple[str, Callable[[Flip], bool]]

# The order is the order of the name you read in the log: first what makes the switch meaningless, then the landing,
# then the leave. Is the entry still valid = ENTRY_RULES; may it go now = GATE_RULES.
ENTRY_RULES: tuple[Rule, ...] = (
    ("replay_used", _replay_used),
    ("redzone_off", _redzone_off),
    ("reel_missing", _reel_missing),
    ("not_live", _not_live),
    ("feed_stale", _feed_stale),
    ("target_moved", _target_moved),
    ("no_timeline", _no_timeline),
    ("chase_gone", _chase_gone),
    ("landing", _landing),
    ("landing_unproven", _landing_unproven),
    ("rewind_changed", _rewind_changed),
    ("arrival_break", _arrival_break),
)

# May the game on screen be left: the same rule functions at the flip and for a cut waiting on the leave (leave_veto).
LEAVE_RULES: tuple[Rule, ...] = (
    ("hold", _hold),
    ("ending_airing", _ending_airing),
    ("replay_exit", _replay_exit),
    ("leave", _leave),
)

GATE_RULES: tuple[Rule, ...] = (
    ("paused_or_dry_run", _paused_or_dry_run),
    ("deferred", _deferred),
    *ENTRY_RULES,
    *LEAVE_RULES,
    ("landing_whistle", _landing_whistle),
)


def commit_gate(host: Any, d: Any, before: str | None, target: str | None, cands: dict,
                replay_exit: float | None = None, now: float | None = None) -> str:
    """Why this non-tap switch may not cut right now ("" = it may): the name of the first rule that says no."""
    f = Flip(host, d, before, target, cands, replay_exit, now)
    veto = next((name for name, rule in GATE_RULES if rule(f)), "")
    if f.jump and d.jump is not None:
        d.jump["veto"], d.jump["veto_cost_s"] = veto, round(host.control.deadline_cost(target), 1)
    return veto


def entry_veto(host: Any, d: Any, target: str | None, now: float | None = None) -> str:
    """Why the destination is no longer valid ("" = it is): the entry half of the gate."""
    one = {d.target: Candidate(host.scored[d.target], target)} if d.target in host.scored else {}
    f = Flip(host, d, None, target, one, None, now)
    return next((name for name, rule in ENTRY_RULES if rule(f)), "")


def leave_refusal(dec: Any, gid: str | None, target: str | None, phase: Any, now: float, uncalibrated_sigma: float,
                  required_s: float = 0.0) -> str:
    """Why the screen's game is not safe to leave for the next required_s ("" = it is). The body of Engine.safe_to_leave,
    clock passed in, so a decider waiting on the leave and the flip read one rule. The product reads only empty or not; the
    names are gate:leave's sub-causes in scripts/veto_audit.py: replay, no_timeline, unaired_row (a LIVE game whose newest
    data row has not aired yet), ended_airing (the same check on a game that ended or left the list), window."""
    if not gid or gid in PSEUDO:   # [S-5] a pseudo game has no timeline and no play to cut
        return ""
    if dec.replaying or dec.after_replay:
        return "" if now >= max(dec.replay_until, dec.replay_exit_after) else "replay"
    if gid not in dec.timelines:
        return "no_timeline"  # no screen timing evidence is not an open cutting window
    if dec._ending_still_airing(gid, now) > 0:
        live = phase == Phase.LIVE
        # [gate.leave_unaired_row_only_ended, off] "its last row is still airing" is a rule for a game that ENDED: a live game's newest
        # row has nearly always not aired yet, so on one it refused before the leave window (which guards every play) was asked
        if not (live and dec.cfg.get("gate.leave_unaired_row_only_ended", False)):
            return "unaired_row" if live else "ended_airing"
    if phase == Phase.POST:
        return ""   # its last play has aired (checked above): there's nothing left to cut
    ok = leave_window(dec.timelines[gid], dec.delays.get(target or "unknown"), now, uncalibrated_sigma, required_s).safe
    return "" if ok else "window"


def leave_open(dec: Any, gid: str | None, target: str | None, phase: Any, now: float, uncalibrated_sigma: float,
               required_s: float = 0.0) -> bool:
    """Is the screen's game safe to leave for the next required_s? (leave_refusal says why not.)"""
    return not leave_refusal(dec, gid, target, phase, now, uncalibrated_sigma, required_s)


def leave_cause(f: Flip, usual: float = 8.0) -> str:
    """Which part of the leave rule says no to this flip ("" = none of it): leave_refusal's name for the plain window (asked for
    `usual` seconds, _leave's own number), `jump` for the rotation's own gate, `high_leverage` for the clock proof. Read only by
    scripts/veto_audit.py, to split gate:leave; no decision depends on it."""
    if not _leave(f):
        return ""
    if f.jump:
        return "jump"
    host, before = f.host, f.before or ""
    g = host.merger.games.get(before)
    why = leave_refusal(host.decider, f.before, getattr(host.where.get(before), "target", None), g.phase if g is not None else None,
                        f.now, host.cfg.get("decider.uncalibrated_sigma_s"), _leave_need(f, usual))
    return why or "high_leverage"


class DeciderHost:
    """What the leave rules read of the Engine, from a Decider on its own (no phase for a game outside this tick's list, so
    its leave needs a window). For a cut waiting on the leave, and for the list's landing plan (Flip.replan, legal.facts_urgent):
    the switch still faces the real gate at the flip."""

    def __init__(self, dec: Any, cands: dict, now: float):
        self.decider, self.delays, self.cfg, self._now = dec, dec.delays, dec.cfg, now
        self.scored = {g: x.scored for g, x in cands.items()}
        self.where = {g: _NS(target=x.target) for g, x in dec._known.items()}
        self.merger = _NS(games={g: _NS(game_id=g, phase=Phase.LIVE, league=x.scored.league) for g, x in cands.items() if g not in PSEUDO})
        # [legal.facts_urgent] Flip.replan from the decider's list: the switch cost legal_facts and the entry plan already use
        self.control = _NS(deadline_cost=lambda t: dec.entry_cost(t) if dec.entry_cost is not None else dec._deadline_cost(t),
                           _scrub_wanted=lambda t: False)

    def safe_to_leave(self, gid: str | None, required_s: float = 0.0) -> bool:
        g = self.merger.games.get(gid or "")
        return leave_open(self.decider, gid, getattr(self.where.get(gid or ""), "target", None), g.phase if g is not None else None,
                          self._now, self.cfg.get("decider.uncalibrated_sigma_s"), required_s)

    def _leave_margin(self) -> float:
        flip = float(self.cfg.get("jump_mode.flip_s", 3.2))
        cost = self.decider.entry_cost(None) if self.decider.entry_cost is not None else flip
        return max(flip, cost if math.isfinite(cost) else flip)


def leave_veto(dec: Any, d: Any, cands: dict, now: float) -> str:
    """[LEAVE-AT-FLIP] Why this cut may not leave the game on screen yet ("" = it may): the leave rules of the commit gate,
    run from the decider. A cut the arbiter listed with the leave gate shut goes only when this is "", and the flip asks again."""
    x = cands.get(d.target or "")
    f = Flip(DeciderHost(dec, cands, now), d, dec.current, x.target if x is not None else None, cands, None, now)
    return next((name for name, rule in LEAVE_RULES if rule(f)), "")


# ----- facts a rule reads that aren't one poll's measurements (kept out of Flip so a rule stays a one-liner) -----------

def _leave_need(f: Flip, usual: float) -> float:
    """Seconds the plain leave window must stay open for this flip. `usual` is cut_veto's 8.0, a guess nobody measured. With
    gate.leave_margin_measured (off) it is the flip's own bound, the one a jump's leave already uses (Engine._leave_margin: the
    configured flip, never less than the measured slow end of a flip; 1.9 s median and 2.2 s max on Nick's TV, Nov 9)."""
    if f.host.cfg.get("gate.leave_margin_measured", False):
        return float(f.host._leave_margin())
    return usual


def _plain_leave(f: Flip, usual: float) -> bool:
    """A non-jump leave of the game on screen: the 'data covers the screen' window, open for the whole flip, or (flag) a proven
    dead window on the screen's game."""
    return bool(f.host.safe_to_leave(f.before, required_s=_leave_need(f, usual))) or _dead_window_leave(f)


def _dead_window_leave(f: Flip) -> bool:
    """[gate.leave_dead_window, off] The screen's LIVE game sits inside a play-by-play dead window (cuts.DeadTracker: a foul, free
    throws, a timeout, a huddle) for the whole flip, delay doubt on both sides: the dead-window bar a jump's leave already uses
    (Decider._jump_gate), without its break guess, its walk or its low-leverage exception. The plain window never asked the
    tracker, so basketball and hockey answered "play is live" with the whistle already in the feed. Never when the game clock is
    proven running across the leave, at ANY leverage: an earlier trial of dead-window leaves made 1-2 mid-play NBA cuts
    (decisions.md 2026-11-04), and a window can outlast the restart (_lands_with_clock_running). An ended game is ending_airing's."""
    host, before = f.host, f.before
    if not host.cfg.get("gate.leave_dead_window", False) or not before or before in PSEUDO:
        return False
    g = host.merger.games.get(before)
    if g is None or g.phase != Phase.LIVE:
        return False
    need = float(host._leave_margin())
    target = getattr(host.where.get(before), "target", None)
    return bool(host.decider.dead_window_leave(before, target, f.now, need)) and not _clock_ran_on_leave(f, need)


def _clock_ran_on_leave(f: Flip, need: float) -> bool:
    """Basketball: the game clock is proven running at some moment the screen shows during [now, now + need] on the game being left
    (_leaves_high_leverage_play's proof, asked whatever the leverage). No clock rows, or another sport: False."""
    before = f.before or ""
    tl = f.host.decider.timelines.get(before)
    g = f.host.merger.games.get(before)
    events = getattr(tl, "clock_events", None)
    if g is None or not events or g.league.base.value not in WHISTLE_LEAGUES:
        return False
    mu = f.host.delays.get(getattr(f.host.where.get(before), "target", None) or "unknown").mu
    return _clock_ran(events, f.now - mu, f.now + need - mu)


def _own_frame(f: Flip) -> bool:
    """A real resume or skip lands on a frame its own tune already fixed, so an entry plan doesn't apply. "Real" is read from
    the replay stack's own record, not from the kind: a resume goes back to the game a replay paused (the newest
    resume_log entry, with the rewind that reaches it) and a skip stays on the game on screen. Anything else wearing these
    kinds gets the ordinary landing checks. [WP-15B] these kinds used to be exempt by name (1,208 mid-play entries)."""
    d, dec = f.d, f.host.decider
    if d.kind == "skip":
        return d.target == getattr(dec, "current", None)
    if d.kind != "resume" or not float(getattr(d, "rewind_s", 0.0) or 0.0) > 0:
        return False
    log = getattr(dec, "resume_log", None) or ()
    return bool(log) and log[-1][1] == "replay" and log[-1][2] == d.target


def _crunch_earned(x: Any) -> bool:
    """A "crunch" cut really is into crunch time on the scores of now (see _crunch_pick's pool). No candidate: the kind stands."""
    if x is None:
        return True
    s = x.scored
    return bool(s.crunch or s.crunch_now or getattr(s, "lock", None) == "empty net")


def bucket_on(cfg: Any) -> bool:
    """[NBA-BUCKET] The flag, basketball.join_after_bucket; off unless config says True."""
    return bool(cfg.get("basketball.join_after_bucket", False))


def _post_make_windows(dead: Any, floor_s: float, p: slate_mod.Params) -> list[Window]:
    """Basketball: each made basket's window, in real time. Starts when the ball is through (bucket_after_s), ends at the
    measured moment the next possession can produce a shot (floor_s after the make) or the next logged action less the
    stamp's guard, whichever is sooner. A window needs no next row in the data: with a late feed the floor is all there is.
    A made free throw opens none: Nick's rule is made baskets, and the grader's window (cutgrade.after_bucket) is field goals only."""
    out, ms = [], dead._order()
    for i, m in enumerate(ms):
        if m.shot != "made" or m.ft or m.hi <= dead.frozen_until:
            continue
        nxt = dead._next_action(ms, i)
        end = m.hi + floor_s if nxt is None else min(m.hi + floor_s, nxt.lo - p.bucket_guard_s)
        start = m.hi + p.bucket_after_s
        if end > start:
            out.append(Window(start, end, "bucket", exact=True))
    return out


def bucket_landing(cfg: Any, tl: Any, scored: Any, sigma: float, x0: float, x1: float) -> Landing | None:
    """[NBA-BUCKET] Nick's rule: basketball may land right after a made basket, including the transition after it. The whole
    arrival [x0, x1], widened by the doubt (bucket_sigmas times the stick delay's sigma), must sit inside one made-basket
    window: a landing that may be before the make, or after the next shot, is None (it waits or skips). Never high-leverage
    close-and-late or overtime (the leave rule's bar), never a game outside NBA and men's college.
    x0 and x1 are real time. Made baskets only: _post_make_windows opens on shot == "made".
    [NBA-YES] basketball.join_after_bucket_crunch (off): the same window also takes a join INTO a crunch or high-leverage game.
    The window, its doubt and "made baskets only" are unchanged; the rule about LEAVING a high-leverage moment is not read here."""
    league = tl.dead.league.value
    if not bucket_on(cfg) or league not in ("nba", "cbb"):
        return None
    tight = getattr(scored, "high_leverage", False) or scored.crunch or scored.crunch_now
    if tight and not cfg.get("basketball.join_after_bucket_crunch", False):
        return None
    p = slate_mod.Params.from_config(cfg.get)
    doubt = p.bucket_sigmas * sigma
    for w in reversed(_post_make_windows(tl.dead, slate_mod.bucket_floor_s(league, p), p)):
        if w.start > x1:
            continue
        if x0 - doubt >= w.start and x1 + doubt <= w.end:
            return Landing("dead", "bucket", w.end, "right after a made basket")
        if x0 - doubt > w.end:
            return None
    return None


def bucket_plan(cfg: Any, tl: Any, scored: Any, delay: Any, now: float, cost: float, fastest: float) -> EntryPlan | None:
    """[NBA-BUCKET] The entry plan for a live-edge landing in a made-basket window, or None. The decider's entry_plan takes
    this over a rewind plan or a wait (docs/agents/patches/nba-bucket.patch): Nick's rule replaces the old divert."""
    mu = delay.mu - getattr(delay, "behind", 0.0)
    land = bucket_landing(cfg, tl, scored, delay.current_sigma(now),
                          now + min(cost, fastest) - mu, now + cost - mu)
    return None if land is None else EntryPlan(True, 0.0, land, land.why)


def _lands_after_bucket(f: Flip) -> bool:
    """[NBA-BUCKET] This live-edge join lands in a made-basket window, so the clock-running and walk bars (which forbid
    exactly that) don't apply. A rewind or a buffered picture is not a live-edge landing."""
    g, tl = f.game, f.timeline
    if g is None or tl is None or _own_frame(f) or float(getattr(f.d, "rewind_s", 0.0) or 0.0) > 0:
        return False
    if not bucket_on(f.host.cfg) or f.host.decider._bj_pic(f.d.target, f.live.get(f.d.target), f.now) is not None:
        return False
    x = f.live.get(f.d.target)
    fastest = min(f.cost, float(f.host.cfg.get("decider.entry.fastest_s", ENTRY_FASTEST_S)))
    d, mu = f.delay, f.delay.mu - getattr(f.delay, "behind", 0.0)
    x0, x1 = f.now + fastest - mu, f.now + f.cost - mu
    if f.jump:
        x1 += float(f.host.cfg.get("jump_mode.land_dead_s", 2.0))
    return x is not None and bucket_landing(f.host.cfg, tl, x.scored, d.current_sigma(f.now), x0, x1) is not None


def _clock_ran(events: Any, x0: float, x1: float) -> bool:
    """The game clock was running at some moment of the real-time span [x0, x1], by the play-by-play around it: two
    neighbouring rows of one period whose clock fell, with the span inside the last `drop` seconds before the later row (the
    latest the clock could have started and still fallen that far: the same reading as grading.resume_after). Rows are
    (wall, period, clock left, type), any order. A span the data can't prove running is False: an open window stays open."""
    ev = sorted(events)
    for (w1, p1, c1, _), (w2, p2, c2, _) in zip(ev, ev[1:]):
        drop = c1 - c2
        if p1 == p2 and drop > 0.05 and max(w1, w2 - drop) <= x1 and w2 > x0:
            return True
    return False


def _lands_with_clock_running(f: Flip) -> bool:
    """[WP-15B] Basketball: a dead window can outlast the restart, so the picture still comes up mid-play (a window opened at
    a period's end runs on into the next period, where its clock checks don't apply: 5 of the 7 mid-play NBA jumps). When
    the game clock is proven running across the whole arrival, wait. No margin: this is a proof, not a bar, so a thin-margin
    landing the plans allow stays allowed. A buffered jump's picture isn't the live edge: its own bar (bj.lands) judges it."""
    g, tl = f.game, f.timeline
    events = getattr(tl, "clock_events", None)
    if g is None or not events or g.league.base.value not in WHISTLE_LEAGUES or _own_frame(f):
        return False
    d = f.delay
    if f.jump and f.host.decider._bj_pic(f.d.target, f.live.get(f.d.target), f.now) is not None:
        return False
    mu = d.mu - getattr(d, "behind", 0.0) + float(getattr(f.d, "rewind_s", 0.0) or 0.0)
    fastest = min(f.cost, float(f.host.cfg.get("decider.entry.fastest_s", ENTRY_FASTEST_S)))
    return _clock_ran(events, f.now + fastest - mu, f.now + f.cost - mu)


def _walk_landing_too_late(f: Flip) -> bool:
    """[NBA-JOIN, slate.enabled] A join in the walk up court after a shot must be up before the next live action can start:
    the measured safe time after the shot (slate.walk_safe_s), else wait for a safer dead ball. The walk windows run to
    14 s, past where a make's next action starts (5th percentile 7 s, rebound 5 s, 9 fit nights). A real dead ball is
    untouched; a transition never had a walk (DeadTracker.walks). Only the arrival's late end matters, with the plan's
    thinnest doubt on top. Nothing else about the landing is judged here: _landing's other checks still run."""
    g, tl = f.game, f.timeline
    if g is None or tl is None or _own_frame(f) or not f.host.cfg.get("slate.enabled", False):
        return False
    walk = cuts.walk_params(f.host.cfg, g.league)
    if walk is None or (f.jump and f.host.decider._bj_pic(f.d.target, f.live.get(f.d.target), f.now) is not None):
        return False
    d = f.delay
    mu = d.mu - getattr(d, "behind", 0.0) + float(getattr(f.d, "rewind_s", 0.0) or 0.0)
    fastest = min(f.cost, float(f.host.cfg.get("decider.entry.fastest_s", ENTRY_FASTEST_S)))
    x0, x1 = f.now + fastest - mu, f.now + f.cost - mu
    if f.jump:
        x1 += float(f.host.cfg.get("jump_mode.land_dead_s", 2.0))
    if tl.dead.landing(x0, x1).state == "dead":
        return False
    win = next((w for w in reversed(tl.dead.walks(walk)) if w.start <= x0 and x1 <= w.end), None)
    if win is None:
        return False
    at = win.start - walk[2]
    shot = next((m.shot for m in tl.dead.marks.values() if abs(m.hi - at) < 0.01 and m.shot), "")
    return x1 + ENTRY_URGENT_S > at + slate_mod.walk_safe_s(shot, slate_mod.Params.from_config(f.host.cfg.get))


def _leaves_high_leverage_play(f: Flip) -> bool:
    """[WP-15B] A high-leverage game on screen, its clock proven running across the leave (safe_to_leave's data can read an
    old stoppage as still open: NBA, 3:14 left, leave 1 s before a shooting foul). Leaving at low leverage stays allowed."""
    before = f.before or ""
    tl = f.host.decider.timelines.get(before)
    g = f.host.merger.games.get(before)
    events = getattr(tl, "clock_events", None)
    if g is None or not events or g.league.base.value not in WHISTLE_LEAGUES or before not in f.live:
        return False
    s = f.live[before].scored
    if not (getattr(s, "high_leverage", False) or s.crunch or s.crunch_now):
        return False
    mu = f.host.delays.get(getattr(f.host.where.get(before), "target", None) or "unknown").mu
    return _clock_ran(events, f.now - mu, f.now + f.host._leave_margin() - mu)
