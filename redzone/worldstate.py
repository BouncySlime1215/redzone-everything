"""[one brain, step 7] The per-tick WorldState builder, run in SHADOW (docs/onebrain-migration.md). Serves:
decision provenance (every decision traced with the world it saw) and steps 8-10 (one ranker, one
decision entry), which will read this instead of the engine's dicts. Owner: INTEGRATION.

With `onebrain.world_shadow` on (default OFF), Engine.decide builds one immutable
contracts.world.WorldState per tick, beside the legacy path: the feeds (merger state + freshness),
the scored games as contracts.scored.Scored, each live game's core MomentView (contracts.views,
the one translation), the screen, RedZone and the flags. Nothing reads it to decide yet. `summary`
is what gets logged next to the legacy decision (data/world_shadow.jsonl, at most once a minute plus
every changed decision), so a later step can diff "what the world said" against "what we did".

Filled now: clock, games (feed, scored, target), screen, redzone_target, flags, tick. Left empty
until their step lands: moment / brk / reasoner per game, sync, paths, reel, health.
"""
from __future__ import annotations

from typing import Callable, Mapping

from redzone.contracts import scored as S
from redzone.contracts.clock import Clock
from redzone.contracts.feeds import GameFeed
from redzone.contracts.views import moment_view
from redzone.contracts.world import GameView, Screen, WorldState, freeze

FLAG = "onebrain.world_shadow"


def build(now: float, games: Mapping, cands: Mapping, scored: Mapping, *, fresh_at: Callable[[str], float | None],
          stale: Callable[[str], bool], current: str | None, current_target: str | None,
          redzone_target: str | None = None, flags: Mapping[str, bool] | None = None, tick: int = 0) -> WorldState:
    """games: game_id -> GameState (merger); cands: game_id -> decider Candidate (live, this tick);
    scored: game_id -> scoring Scored (every game scored this tick)."""
    views = {}
    for gid, g in games.items():
        sc = scored.get(gid)
        c = cands.get(gid)
        t = fresh_at(gid)
        views[gid] = GameView(feed=GameFeed(g, float(t) if t is not None else 0.0, "merged", bool(stale(gid))),
                              scored=S.from_scored(sc) if sc is not None else None,
                              target=c.target if c is not None else None)
    return WorldState(clock=Clock(now), games=freeze(views),
                      screen=Screen(current, current_target, since=now, confirmed=False),
                      redzone_target=redzone_target, flags=freeze(dict(flags or {})), tick=tick)


def summary(w: WorldState, decision=None, top: int = 3) -> dict:
    """One log line: the live games the world ranks highest (core MomentView value, which is the
    scorer's score until the reasoner bonus is wired) next to the legacy decision."""
    live = []
    for gid, v in w.live().items():
        if v.scored is None:
            continue
        mv = moment_view(v.scored)
        live.append((mv.value, gid, v.target, mv.crunch, mv.lock, v.feed.stale))
    live.sort(key=lambda x: (-x[0], x[1]))
    out = {"ts": round(w.now, 2), "tick": w.tick, "games": len(w.games), "live": len(live),
           "screen": w.screen.game_id, "redzone": w.redzone_target,
           "top": [{"game": gid, "value": round(val, 2), "target": tgt, "crunch": cr, "lock": lk, "stale": st}
                   for val, gid, tgt, cr, lk, st in live[:top]]}
    if decision is not None:
        d = S.from_decision(decision)
        out["legacy"] = {"action": d.action, "target": d.target, "kind": d.kind, "reason": d.reason[:120]}
        out["agrees"] = bool(live) and d.action == "stay" and live[0][1] == w.screen.game_id \
            or (d.action == "switch" and bool(live) and live[0][1] == d.target)
    return out
