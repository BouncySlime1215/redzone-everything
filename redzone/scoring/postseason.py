"""Postseason stakes: elimination games, deciders and how close a game is to the title (default OFF).

The scorer's stakes today know records, the spread and national TV. They can't tell a Game 7 from a
Tuesday in May. ESPN's scoreboard already says so, on the payload we poll anyway:

    event.season.type == 3                    postseason
    competitions[0].series                    best-of length (totalCompetitions), wins per team id
    competitions[0].notes[].headline          round and game number, e.g. "World Series - Game 7"

`parse` reads those into a `Stakes` (stored on `GameState.stakes` by the ESPN parser, always, since it is
only data). Nothing scores it unless `score.postseason.enabled` is true. The hooks for the lanes that own
scoring (engine.py) and the Noggin are:

    stakes_bonus(g, cfg)     -> float  add to the engine's stakes multiplier (bounded, 0 when off)
    mind_terms(g, cfg)       -> dict   a Noggin L3 term {"playoff_race": 0..1} (priority.MIND_TERM_POINTS)
    playoff_flag(g)          -> int    the Noggin Timing/Predict `playoff` feature (factory.py's definition)

Idea from ArenaSwap (github.com/hiteacheryouare/arenaswap): grade a playoff game by distance from the
trophy, and add series state. Ideas only; this code, the round table and the weights are our own.

Why: recorded in docs/memory/decisions.md 2026-11-04 (score.postseason ships OFF); first added in 66a4f113 (2026-11-04) as "Postseason stakes hooks (off) + ArenaSwap comparison".
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from redzone.models import GameState, League

# How many rounds are left after this one, from the round's name in ESPN's note headline. 0 = the title
# game or series. Checked in order, first match wins, so the specific names sit above the generic ones.
ROUNDS_LEFT: dict[League, tuple[tuple[str, int], ...]] = {
    League.MLB: (("world series", 0), ("championship series", 1), ("alcs", 1), ("nlcs", 1),
                 ("division series", 2), ("alds", 2), ("nlds", 2), ("wild card", 3)),
    League.NBA: (("nba finals", 0), ("conference finals", 1), ("east finals", 1), ("west finals", 1),
                 ("semifinals", 2), ("first round", 3), ("1st round", 3), ("play-in", 4)),
    League.NHL: (("stanley cup final", 0), ("conference final", 1), ("second round", 2), ("2nd round", 2),
                 ("first round", 3), ("1st round", 3)),
    League.NFL: (("super bowl", 0), ("conference championship", 1), ("afc championship", 1),
                 ("nfc championship", 1), ("divisional", 2), ("wild card", 3)),
    League.CFB: (("national championship", 0), ("semifinal", 1), ("quarterfinal", 2), ("first round", 3)),
    League.CBB: (("national championship", 0), ("final four", 1), ("elite 8", 2), ("elite eight", 2),
                 ("sweet 16", 3), ("sweet sixteen", 3), ("2nd round", 4), ("second round", 4),
                 ("1st round", 5), ("first round", 5), ("first four", 6)),
}
ROUNDS_LEFT[League.WCBB] = ROUNDS_LEFT[League.CBB]

# Postseason games that are one-and-done when ESPN sends no series (a CFB bowl outside the playoff isn't).
SINGLE_ELIM = {League.NFL, League.CBB, League.WCBB}
CFB_PLAYOFF_WORDS = ("playoff", "national championship", "cfp")

# Default weights (stakes multiplier deltas). Overridable under score.postseason.*; the sum is capped.
WEIGHTS = dict(base=0.02, elimination=0.05, decider=0.05, round_step=0.02, max_round_bonus=0.08,
                ranked_both=0.03, ranked_top10_both=0.02, cap=0.15)


@dataclass(frozen=True)
class Stakes:
    postseason: bool = False
    rounds_left: int | None = None      # 0 = title game / series; None = unknown round
    best_of: int | None = None          # series length; None = single game
    wins_home: int | None = None        # series wins before this game
    wins_away: int | None = None
    game_no: int | None = None          # game number in the series
    elimination: bool = False           # a loss ends someone's season
    decider: bool = False               # the winner takes it (Game 7, or a one-game final)
    rank_home: int | None = None        # college poll rank, None when unranked
    rank_away: int | None = None
    label: str = ""                     # ESPN's headline, e.g. "World Series - Game 7"

    @property
    def ranked_matchup(self) -> bool:
        return self.rank_home is not None and self.rank_away is not None


def _poll_rank(r) -> int | None:
    return r if isinstance(r, int) and 0 < r <= 25 else None


def round_of(league: League, headline: str) -> int | None:
    h = (headline or "").lower()
    for words, left in ROUNDS_LEFT.get(league, ()):
        if words in h:
            return left
    return None


def parse(league: League, ev: dict, home_id: str = "", away_id: str = "",
          rank_home: int | None = None, rank_away: int | None = None) -> Stakes | None:
    """Stakes from one ESPN scoreboard event, or None when there's nothing to say (regular season and
    unranked). Never raises on a strange payload: missing pieces read as unknown."""
    comps = ev.get("competitions") if isinstance(ev, dict) else None
    comp = comps[0] if isinstance(comps, list) and comps and isinstance(comps[0], dict) else {}
    season = ev.get("season") if isinstance(ev.get("season"), dict) else {}
    post = season.get("type") == 3
    notes = comp.get("notes") if isinstance(comp.get("notes"), list) else []
    label = next((n.get("headline") for n in notes if isinstance(n, dict) and n.get("headline")), "") or ""
    rh, ra = _poll_rank(rank_home), _poll_rank(rank_away)
    if not post:
        return Stakes(rank_home=rh, rank_away=ra) if (rh or ra) else None

    series = comp.get("series") if isinstance(comp.get("series"), dict) else {}
    best_of = series.get("totalCompetitions") if isinstance(series.get("totalCompetitions"), int) else None
    wins = {}
    for c in series.get("competitors") or []:
        if isinstance(c, dict) and isinstance(c.get("wins"), int):
            wins[str(c.get("id"))] = c["wins"]
    wh, wa = wins.get(str(home_id)), wins.get(str(away_id))
    m = re.search(r"game\s+(\d+)", label, re.I)
    game_no = int(m.group(1)) if m else None

    elimination = decider = False
    if best_of and best_of > 1 and wh is not None and wa is not None:
        if series.get("completed") is True and game_no:
            # ESPN counts the finished game in the wins: take it back off the winner of the series
            # so this reads as the state going into the game (a Game 7 was 3-3, not 4-3).
            need = best_of // 2 + 1
            if wh == need and wh + wa == game_no:
                wh -= 1
            elif wa == need and wh + wa == game_no:
                wa -= 1
        need = best_of // 2 + 1
        elimination = max(wh, wa) == need - 1
        decider = wh == wa == need - 1
        if game_no is None:
            game_no = wh + wa + 1
    elif league in SINGLE_ELIM or (league == League.CFB and any(w in label.lower() for w in CFB_PLAYOFF_WORDS)):
        elimination = True
        best_of = None
    left = round_of(league, label)
    if not best_of and elimination and left == 0:
        decider = True          # a one-game final: the winner takes the title
    return Stakes(True, left, best_of if best_of and best_of > 1 else None, wh, wa, game_no, elimination,
                  decider, rh, ra, label)


def enabled(cfg) -> bool:
    return bool(cfg is not None and cfg.get("score.postseason.enabled", False))


def _w(cfg, name: str) -> float:
    try:
        return float(cfg.get(f"score.postseason.{name}", WEIGHTS[name]))
    except (TypeError, ValueError):
        return WEIGHTS[name]


def bonus(st: Stakes | None, cfg=None) -> float:
    """Stakes multiplier delta for these stakes (no flag check: the caller decides)."""
    if st is None:
        return 0.0
    w = (lambda n: WEIGHTS[n]) if cfg is None else (lambda n: _w(cfg, n))
    b = 0.0
    if st.postseason:
        b += w("base")
        if st.rounds_left is not None:
            b += max(0.0, w("max_round_bonus") - w("round_step") * st.rounds_left)
        if st.elimination:
            b += w("elimination")
        if st.decider:
            b += w("decider")
    if st.ranked_matchup:
        b += w("ranked_both")
        if st.rank_home <= 10 and st.rank_away <= 10:
            b += w("ranked_top10_both")
    return round(max(0.0, min(w("cap"), b)), 4)


def stakes_bonus(g: GameState, cfg) -> float:
    """The hook for the scorer's stakes multiplier: 0 unless score.postseason.enabled."""
    if not enabled(cfg):
        return 0.0
    return bonus(getattr(g, "stakes", None), cfg)


def mind_terms(g: GameState, cfg) -> dict[str, float]:
    """A Noggin L3 term for these stakes, scaled 0..1 against the cap (priority.MindRead.terms)."""
    if not enabled(cfg):
        return {}
    b = bonus(getattr(g, "stakes", None), cfg)
    cap = _w(cfg, "cap") or 1.0
    return {"playoff_race": round(b / cap, 4)} if b > 0 else {}


def playoff_flag(g: GameState) -> int:
    """1 for a postseason game: the Noggin features' `playoff` column (factory.py), which live reads have
    no source for today."""
    st = getattr(g, "stakes", None)
    return int(bool(st is not None and st.postseason))


def reason(g: GameState) -> str:
    """Short words for the phone, e.g. 'World Series - Game 7 · winner takes it'."""
    st = getattr(g, "stakes", None)
    if st is None:
        return ""
    bits = [st.label] if st.label else []
    if st.decider:
        bits.append("winner takes it")
    elif st.elimination:
        bits.append("elimination game")
    if st.ranked_matchup:
        bits.append(f"#{st.rank_away} vs #{st.rank_home}")
    return " · ".join(bits)
