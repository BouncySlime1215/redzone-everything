"""Noggin Priority -> decider wiring (flag `noggin.priority.enabled`, off by default).

The decider's candidates carry a Scored, not team names: "your team" is the decider's own verdict
(`Decider._yours`: the scorer's personal points, from the same `teams` / bets config L1 reads), passed
through as a YOURS token so `HardRules.is_yours` sees it. L2-L4 enter as one live term, the rate the
decider already ranks by (core.rate), until the Mind and Reflex terms are wired (docs/noggin.md Phase 2).
"""
from __future__ import annotations

from redzone.noggin import priority as P

YOURS = "__YOURS__"
# Locks that are big moments (red zone, power play, close-late). Other locks hold the TV but are not tier 0.
BIG_LOCKS = {"red zone drive", "power play", "final two minutes", "crunch time", "overtime", "empty net", "high-leverage at-bat"}


def rules(cfg, leagues) -> P.HardRules:
    base = P.HardRules.from_config(cfg)
    mine = {lg: frozenset({YOURS}) for lg in leagues}
    return P.HardRules(mine, base.league_order, base.banned_apps)


def game_input(gid: str, cand, yours: bool, value: float | None = None) -> P.GameInput:
    s = cand.scored
    app = (cand.target or "").split(":", 1)[0]
    big = bool(s.lock in BIG_LOCKS or s.pre_moment or s.crunch)
    return P.GameInput(gid, s.league.base.value if s.league.base.value in P.DEFAULT_LEAGUE_ORDER else s.league.value,
                       (YOURS,) if yours else (), app=app, crunch=bool(s.crunch), big_moment=big,
                       live={"score": float(s.score if value is None else value)}, label=gid)


def pick(cfg, cands: dict, ids, yours: set[str], values: dict | None = None) -> str | None:
    """The best of `ids` by the Noggin Priority order (L1 tiers first, then `values`: the decider's own
    rate, so inside a tier the pick is today's), or None."""
    ins = [game_input(g, cands[g], g in yours, (values or {}).get(g)) for g in ids]
    if not ins:
        return None
    ranked = P.rank(ins, rules(cfg, {i.league for i in ins}), P.Preferences.from_config(cfg))
    return ranked[0].game_id if ranked else None
