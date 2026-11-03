"""Producer outputs -> the decision core's views (redzone/decider/core.py).

Owner: INTEGRATION. The core's view types (MomentView, BreakView, SyncView, ReelView, Option) are
canonical and defined ONLY in core.py; the models produce the richer contract types in this
package, and these pure functions are the one place they are translated. The Decider calls them
while building CoreState from the tick's WorldState.
"""
from __future__ import annotations

from redzone.contracts.breaks import BreakForecast
from redzone.contracts.clock import Clock
from redzone.contracts.moment import MomentPrediction
from redzone.contracts.reasoner import ReasonerView
from redzone.contracts.reel import ReelJump, ReelOffer
from redzone.contracts.sync import SyncEstimate
from redzone.decider.core import BreakView, MomentView, Option, ReelView, SyncView

LOCK_IN_LEVEL = 2   # a reasoner lock_in maps to this core lock_in level (0 free .. 3 never leave)


def moment_view(scored, pred: MomentPrediction | None = None, rv: ReasonerView | None = None,
                now: float | None = None, bonus_cap: float = 20.0) -> MomentView:
    """Scored (scorer) + MomentPrediction (Brain v3) + ReasonerView (advisory) -> MomentView."""
    fresh = rv is not None and (now is None or rv.fresh(now))
    bonus = min(bonus_cap, max(0.0, rv.value_bonus)) if fresh else 0.0
    return MomentView(value=float(scored.score) + bonus,
                      p_big_soon=float(pred.key_soon_p or 0.0) if pred is not None else 0.0,
                      lock=scored.lock, crunch=bool(scored.crunch), late=bool(scored.late),
                      garbage=bool(scored.garbage), personal=float(scored.personal),
                      must_see=False, lock_in=LOCK_IN_LEVEL if fresh and rv.lock_in else 0)


def break_view(f: BreakForecast | None, clock: Clock, target: str | None,
               started_at: float | None = None, on_break: bool | None = None) -> BreakView:
    """BreakForecast (feed time) -> BreakView (screen time). started_at: feed-time start of the
    current break if known, so the remaining lengths shrink as it runs."""
    if f is None:
        return BreakView()
    brk = f.in_data_now if on_break is None else on_break
    if not brk:
        return BreakView(break_soon_p=f.p_start, break_soon_s=f.length_q50_s)
    elapsed = 0.0
    if started_at is not None and target:
        elapsed = max(0.0, clock.on_screen_now(target) - started_at)
    restart = clock.to_screen(f.next_restart_at, target) if (f.next_restart_at and target) else None
    return BreakView(True, max(0.0, f.length_q10_s - elapsed), max(0.0, f.length_q50_s - elapsed), restart)


def sync_view(e: SyncEstimate) -> SyncView:
    return SyncView(e.delay_s, e.sigma_s, e.behind_s)


def reel_option(offer: ReelOffer, jump: ReelJump | None = None, min_length_s: float = 0.0) -> Option | None:
    """The ready reel as a core Option. With a highlight-jump verdict, its ev_reel (points per second
    over the window, the core's units) is the option's value; a jump that isn't ok offers nothing."""
    if jump is not None and not jump.ok:
        return None
    rv = ReelView(offer.seconds, min_length_s, offer.value, len(offer.clip_ids))
    return Option(offer.target, kind="reel", target=offer.target, reel=rv,
                  value_override=jump.ev_reel if jump is not None else offer.value)
