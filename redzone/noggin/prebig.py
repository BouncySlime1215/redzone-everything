"""[EXP-025] Arrive at the dead ball BEFORE the big play (flag `noggin.prebig.enabled`, OFF by default).

The decider ranks games by how good they are right now; the scorer already says when a big moment is
about to start (``Scored.pre_moment``: 4th down, 3rd & goal, a power play, an empty net, the final minute
of a one-score game) but nothing acts on it. This stage does: while the app would stay, it looks for
another game whose next snap is high-leverage (a fixed, hand-set hazard below, NOT fitted to outcomes),
and asks to switch there so the picture lands on the stoppage before that snap. Once there it holds
through the build-up (the next plays) instead of drifting back.

Every guard the decider already has stays in force: this runs where Reflex PICK runs (``Decider._pick``),
before the hold-for-a-big-play, entry-timing and safety stages, so the leave gate (Rule #1), the whistle
wait on entry (hold_only / cut timing) and your team's L1 rules decide WHEN and WHETHER. It never leaves
your team's game, a crunch window, or a locked moment (red zone drive, power play, final two minutes).

Live wiring: none. Nothing in the live path constructs ``with_prebig``; the experiment harness
(``redzone/lab/buildup.py``) swaps it in for scorecards only, and even then it is a no-op until
``noggin.prebig.enabled`` is true. Inputs are what the decider already saw (candidates, timelines): no
future plays, no outcomes.
"""
from __future__ import annotations

from dataclasses import dataclass

from redzone.models import League

FLAG = "noggin.prebig.enabled"

# Hazard: a hand-set prior that the next stretch of play holds a graded big play (NFL score/turnover,
# NHL goal, NBA clutch bucket). Fixed in docs/prereg/exp-025.md BEFORE the first run; never tuned.
HAZARD_PRE = {
    "1st & goal": 0.55, "2nd & goal": 0.55, "3rd & goal": 0.55, "4th & goal": 0.6,
    "4th down": 0.4,
    "two-minute warning, one score": 0.45, "final minute, one score": 0.5,
    "final minute, one possession": 0.6,
    "power play": 0.3, "empty net": 0.45,
}
HAZARD_LOCK = {"red zone drive": 0.5, "empty net": 0.45, "power play": 0.3}
# Basketball's big play is a clutch bucket (Q4/OT, last 2:00, within 5): the run-up to that window is the
# dead ball before it. (period >= this, seconds left in (lo, hi], margin <= m) -> hazard
RUNUP = {League.NBA: (4, 120.0, 240.0, 6, 0.5), League.NHL: (3, 120.0, 240.0, 1, 0.3),
         League.NFL: (4, 120.0, 240.0, 8, 0.35)}

MIN_HAZARD = 0.3        # a target needs at least this
MARGIN = 0.15           # ...and this much more than what's on screen
PREBIG_MIN_GAP = 60.0        # never a pre-big switch within this long of the last switch
PREBIG_HOLD = {League.NFL: 240.0, League.NBA: 240.0, League.NHL: 180.0}   # hold the build-up at most this long
KEEP_KINDS = {"user", "crunch", "must_see", "ended", "home", "return", "resume", "skip", "replay_resume", "start"}
PREBIG_KIND = "prebig"


@dataclass
class Signal:
    hazard: float
    label: str


def _runup(league: League, tl) -> Signal | None:
    rule = RUNUP.get(league)
    if rule is None or tl is None or not getattr(tl, "clocks", None):
        return None
    last_per, lo, hi, margin, hz = rule
    _, per, clk = tl.clocks[-1]
    sc = getattr(tl, "_last_score", None)
    if per is None or clk is None or not sc or None in sc:
        return None
    if per == last_per and lo < float(clk) <= hi and abs(sc[0] - sc[1]) <= margin:
        return Signal(hz, "crunch run-up")
    return None


def signal(scored, tl=None) -> Signal:
    """How likely the next stretch of this game holds a big play, from what the decider already knows."""
    if scored is None or getattr(scored, "garbage", False):
        return Signal(0.0, "")
    best = Signal(0.0, "")
    pre, lock = getattr(scored, "pre_moment", None), getattr(scored, "lock", None)
    if pre:
        hz = HAZARD_PRE.get(pre, 0.45 if pre.endswith("& goal") else 0.0)
        if hz > best.hazard:
            best = Signal(hz, pre)
    if lock and HAZARD_LOCK.get(lock, 0.0) > best.hazard:
        best = Signal(HAZARD_LOCK[lock], lock)
    lg = scored.league.base if hasattr(scored.league, "base") else scored.league
    ru = _runup(lg, tl)
    if ru is not None and ru.hazard > best.hazard:
        best = ru
    return best


def enabled(cfg) -> bool:
    return bool(cfg.get(FLAG, False))


def apply(dec, d, cands: dict, now: float):
    """The pre-big stage on one decision. Returns `d` unchanged when the flag is off."""
    from redzone.decider.decider import PSEUDO, Decision, State

    if not enabled(dec.cfg):
        return d
    if dec.replaying or dec.after_replay or dec.user.paused or dec.safe_mode or d.action == "replay" \
            or d.kind == "user":
        return d
    st = getattr(dec, "_prebig", None)
    cur = dec.current
    on = cands.get(cur) if cur else None

    # 1. Holding the build-up: stay on the game we came for while its signal lasts (and PREBIG_HOLD at most).
    if st and cur == st[0] and on is not None:
        lg = on.scored.league.base
        if now - max(st[1], dec.since) <= PREBIG_HOLD.get(lg, 180.0) and signal(on.scored, dec.timelines.get(cur)).hazard >= MIN_HAZARD:
            if d.action == "switch" and d.target != cur and d.kind not in KEEP_KINDS \
                    and d.target not in dec._yours:
                x = cands.get(d.target)
                if x is None or not (x.scored.crunch or x.scored.crunch_now):
                    return Decision(now, "stay", cur, f"prebig: holding the build-up ({st[2]})", d.state, d.gate)
            return d
        dec._prebig = None
    elif st and cur != st[0]:
        dec._prebig = None

    # 2. Pre-switch: only where the app would stay, never off your team / crunch / a locked moment.
    if d.action != "stay" or d.want or cur in PSEUDO:
        return d    # the app is moving, or already waiting to move somewhere: its call
    if cur is not None:
        if on is None or cur in dec._yours or on.scored.crunch or on.scored.crunch_now or on.scored.lock:
            return d
    if dec.history and now - dec.history[-1][0] < PREBIG_MIN_GAP:
        return d
    here = signal(on.scored, dec.timelines.get(cur)).hazard if on is not None else 0.0
    best, why = None, None
    for gid, x in cands.items():
        if gid == cur or gid in PSEUDO or not x.target or x.stale or gid in dec._screen_break:
            continue
        s = signal(x.scored, dec.timelines.get(gid))
        if s.hazard >= MIN_HAZARD and s.hazard >= here + MARGIN and (why is None or s.hazard > why.hazard):
            best, why = gid, s
    if best is None:
        return d
    if on is not None:
        gate = dec._gate(on, now)
        if not (gate and gate.safe):
            return Decision(now, "stay", cur, f"prebig: {why.label} — waiting: {gate.why if gate else 'no timeline'}",
                            d.state, gate.why if gate else "", want=best)
    dec._prebig = (best, now, why.label)
    return Decision(now, "switch", best, f"prebig: {why.label} ({why.hazard:.2f} vs {here:.2f})",
                    d.state if isinstance(d.state, State) else State.WATCHING, kind=PREBIG_KIND)


def with_prebig(decider_cls):
    """A Decider subclass with the pre-big stage after Reflex PICK (lab harness only; see module doc)."""

    class PreBigDecider(decider_cls):
        _prebig = None

        def _pick(self, d, cands, now):
            return apply(self, super()._pick(d, cands, now), cands, now)

    PreBigDecider.__name__ = f"PreBig{decider_cls.__name__}"
    return PreBigDecider
