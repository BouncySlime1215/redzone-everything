"""How much is a bet sweating right now? 0 = decided or calm, 1 = on the knife's edge.

    margin m  = how far the bet is from flipping, in points
    unit u    = one typical score (NFL 7, NBA 4, NHL 1, MLB 1.5)
    sweat     = exp(-|m| / u) x lateness, and ~0 once it's out of reach

Kinds: moneyline, spread, total (over/under), prop (no live stat: flat medium sweat).

Why: first added in 9faa350e (2026-11-02) as "RedZone Everything: core engine, scorer, decider, replays, backtest lab, server"; no separate decision is recorded for this module.
"""
from __future__ import annotations

import math

from redzone.models import PERIOD_S, REGULATION, ByLeague, GameState, League, Phase

UNIT = ByLeague({League.NFL: 7.0, League.NBA: 4.0, League.NHL: 1.0, League.MLB: 1.5})


def progress(g: GameState) -> float:
    """0 at kickoff, 1 at the end of regulation."""
    regs = REGULATION[g.league]
    p = g.period or 1
    if g.league == League.MLB:
        return min(1.0, (p - 1 + (0.5 if g.half == "bottom" else 0)) / regs)
    length = PERIOD_S[g.league]
    left = g.clock_s if g.clock_s is not None else length / 2
    return min(1.0, ((min(p, regs) - 1) * length + (length - left)) / (regs * length))


def margin(g: GameState, bet: dict) -> float | None:
    """Points by which the bet is currently winning (+) or losing (-)."""
    hs, as_ = g.home.score, g.away.score
    if hs is None or as_ is None:
        return None
    kind = bet.get("kind")
    team = (bet.get("team") or "").upper()
    line = float(bet.get("line") or 0.0)
    if kind in ("moneyline", "spread"):
        if team == g.home.abbr:
            diff = hs - as_
        elif team == g.away.abbr:
            diff = as_ - hs
        else:
            return None
        return diff + (line if kind == "spread" else 0.0)
    if kind == "total":
        over = (bet.get("side") or "over").lower() == "over"
        return (hs + as_ - line) * (1 if over else -1)
    return None


def sweat(g: GameState, bet: dict) -> float:
    if g.phase != Phase.LIVE:
        return 0.0
    if bet.get("kind") == "prop":
        return 0.5
    m = margin(g, bet)
    if m is None:
        return 0.0
    u = UNIT[g.league]
    prog = progress(g)
    # Late in the game a 3-score gap is effectively decided.
    reachable = u * max(1.0, 6.0 * (1.0 - prog))
    if abs(m) > reachable:
        return 0.0
    if bet.get("kind") == "total" and m > 0:
        return 0.0  # an over that's already cashed can't lose
    return math.exp(-abs(m) / u) * (0.35 + 0.65 * prog)


def label(g: GameState, bet: dict) -> str:
    kind = bet.get("kind")
    team = bet.get("team") or ""
    line = bet.get("line")
    m = margin(g, bet)
    status = "" if m is None else (" · covering" if m > 0 else " · push" if m == 0 else f" · needs {abs(m):g}")
    if kind == "spread":
        return f"your bet: {team} {float(line):+g}{status}"
    if kind == "moneyline":
        return f"your bet: {team} ML{status}"
    if kind == "total":
        return f"your bet: {(bet.get('side') or 'over').title()} {line:g}{status}"
    return f"your bet: {bet.get('note') or 'prop'}"
