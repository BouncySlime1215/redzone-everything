"""Feature factory for GameValue v5: 100,000+ named candidate columns from the plays at the feed head, and the kept few, computed online.

Why: Nick asked for far more grading variables behind the 0-100 value (decisions.md, 2026-11-09). The honest shape of that is many generated
candidates and few shipped ones: a Kaggle run generates every column here and scripts/kaggle_gamevalue_select.py keeps only those that raise the
score on training nights. Generated is not used: the model reads only the kept list, whose length is the number to quote.

    names = candidate_names("nfl")                         # every candidate, the same list every run
    series = track("nfl", timed_plays)                     # per-play numbers, oldest first, in wall order
    X = run_plan(series, head, compile_plan("nfl", kept))  # kept columns at each head index, float32, no pandas
    game = WideGame("nfl", kept); game.update(timed_plays); game.values()      # the live call: bounded state per game

A column name says exactly how it is made: gv5__<stat>__<side>__<window>__<agg>__<norm>__<cross>, for example
gv5__pts__trail__p10__sum__raw__late is the points the trailing team (trailing at the head play) scored in the last 10 plays, kept only when the game is late.

    stat    per-play number. Team-split stats (home, away, and lead / trail at the head) come from score changes alone, because the public adapters
            carry no reliable team on other plays; turnovers, fouls and the rest are per game. What the nflverse adapter does not carry the same way as
            ESPN is a known risk the fair test (ESPN feeds) measures.
    window  pN = last N plays, sT = last T seconds of wall clock before the head play; game = whole-game registers (cum, per, since_s, since_p, streak).
    agg     sum, mean, rate (per minute), nz (plays where it was nonzero), max, std, slope (second half minus first half of the window).
    norm    raw, lg (divided by a hand-set league unit), log (signed log1p). Only under the numeric crosses (margin, left): under a 0/1/-1 cross all three order every row the same, so a tree sees one column.
            lg is a constant multiple of raw inside one league (the two differ only across leagues); drop_duplicates removes it before boosting.
    cross   none, or a game-state number multiplied in: close, late, crunch, last (last regulation period or later), lead, margin, left; football adds
            down3, rz, short.

Football scoring: nflverse rows (extra points and two-point tries dropped, holdset_public.nfl_summary) show a touchdown as +6 and leave the conversion to the next row, while ESPN's touchdown row is usually +7
(checked on six dev-night archive games, 2026-11-09). Tracker therefore counts every touchdown as 7 and a lone +1 or +2 without a safety as nothing, so `pts` and `scoring` read the same from either source.

Causal by construction: every number is computed from plays at or before the head (never the decision time, the cut, the label or a later play), by one
incremental Tracker, whether offline or live, and windows are bounded by RING plays either way, so offline equals online. League units are constants
fit on no rows, so they cannot leak. No pandas here: the live process imports this. Default OFF: RZ_GV_FEATURES=v5 and a kept list turn it on.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Mapping, NamedTuple, Sequence

import numpy as np

from redzone.moments.detect import clock_s
from redzone.noggin import gamevalue as G

PREFIX = "gv5"
SEP = "__"
RING = 256                               # most recent plays a window may ever reach, offline and live: the per-game bound
PLAY_WINDOWS = (2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30, 40, 50, 75, 100, 150, 200)
SEC_WINDOWS = (15, 30, 45, 60, 90, 120, 180, 240, 300, 420, 600, 900, 1200, 1800, 2700, 3600)
WINDOWS = tuple(f"p{n}" for n in PLAY_WINDOWS) + tuple(f"s{t}" for t in SEC_WINDOWS)
GAME_WINDOW = "game"
WINDOW_AGGS = ("sum", "mean", "rate", "nz", "max", "std", "slope")
GAME_AGGS = ("cum", "per", "since_s", "since_p", "streak")
NORMS = ("raw", "lg", "log")
_CROSS_ALL = ("none", "close", "late", "crunch", "last", "margin", "left", "lead")
_CROSS_FOOTBALL = _CROSS_ALL + ("down3", "rz", "short")
NUMERIC_CROSSES = ("margin", "left")      # the only crosses where the norm changes the order of a column's rows
TEAM_SIDES = ("all", "home", "away", "lead", "trail")
_SCORE_HOOPS = ("pts", "scoring", "made2", "made3", "ft")
_OTHER_HOOPS = ("plays", "turnover", "foul", "timeout", "miss", "reb_off", "reb_def", "dunk", "layup", "jump", "three_att", "ft_att", "lead_change", "tie")
_SCORE_FOOTBALL = ("pts", "scoring", "td", "fg")
_OTHER_FOOTBALL = ("plays", "turnover", "penalty", "timeout", "pass", "rush", "sack", "punt", "kickoff", "explosive", "negative", "first_down",
                   "third_down", "fourth_down", "redzone", "yards", "lead_change", "tie")
FOOTBALL = ("nfl", "cfb")
HOOPS = ("nba", "cbb", "wnba")
LEAGUES = FOOTBALL + HOOPS
REGULATION = {"nfl": (4, 900.0), "cfb": (4, 900.0), "nba": (4, 720.0), "wnba": (4, 600.0), "cbb": (2, 1200.0)}   # value_shards.REGULATION_LEN: a test keeps them equal
# Hand-set from the sports' scoring and pace, fit on no rows: per-league plays and yards in 5 minutes, so one number means one thing across sports.
_PLAYS_5M = {"nfl": 8.0, "cfb": 10.0, "nba": 60.0, "cbb": 50.0, "wnba": 55.0}
_YARDS_5M = 50.0
BOUND = 1e9                              # gamevalue.valid_rows' physical range for a wide column: finite, nothing more

_WNBA_SCALE = (2.5, 12.0, 20.0, 2400.0)    # wnba has no row in gamevalue.GV_LEAGUE_SCALE, which v4 keeps as it is

KEPT_ENV = "RZ_GV_V5_KEPT"
DEFAULT_KEPT = Path(__file__).resolve().parent / "gv_v5_kept.json"
KEPT_SCHEMA = 1


def _logic(source: str, only: frozenset | None = None) -> str:
    """The code of `source` without docstrings or comments, or just the top-level names in `only`. A reworded comment is the same generator; a changed line is not."""
    tree = ast.parse(source)
    nodes = tree.body if only is None else [n for n in tree.body if getattr(n, "name", None) in only or (isinstance(n, ast.Assign) and any(getattr(t, "id", None) in only for t in n.targets))]
    kept = ast.Module(body=nodes, type_ignores=[])
    for node in ast.walk(kept):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) and isinstance(body[0].value.value, str):
            node.body = body[1:] or [ast.Pass()]
    return ast.dump(kept)


def logic_digest(wide_source: str, gamevalue_source: str, detect_source: str) -> str:
    """Sha of this module's logic and constants and of what it borrows: the play kinds, the feed-row rule, the league scales, the clock parser."""
    parts = (_logic(wide_source), _logic(gamevalue_source, frozenset({"play_kind", "feed_row", "_num", "GV_LEAGUE_SCALE", "_HOOPS_VIOLATIONS", "_NOT_A_SNAP", "_FOOTBALL"})),
             _logic(detect_source, frozenset({"clock_s"})))
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


GENERATOR = f"gv5-{logic_digest(Path(__file__).read_text(), Path(G.__file__).read_text(), Path(clock_s.__code__.co_filename).read_text())}"     # a kept list from other logic will not load


def _check_league(league: str) -> None:
    if league not in LEAGUES:
        raise ValueError(f"no wide features for league {league!r}: known {LEAGUES}")


def stats_of(league: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(team-split stats, per-game stats) for this league."""
    _check_league(league)
    return (_SCORE_FOOTBALL, _OTHER_FOOTBALL) if league in FOOTBALL else (_SCORE_HOOPS, _OTHER_HOOPS)


def crosses_of(league: str) -> tuple[str, ...]:
    return _CROSS_FOOTBALL if league in FOOTBALL else _CROSS_ALL


@lru_cache(maxsize=16)
def series_names(league: str) -> tuple[str, ...]:
    """Every per-play series, in matrix order: 'stat@side', team stats split all/home/away, others 'all' only."""
    team, other = stats_of(league)
    return tuple(f"{s}@{side}" for s in team for side in ("all", "home", "away")) + tuple(f"{s}@all" for s in other)


@lru_cache(maxsize=16)
def _series_index(league: str) -> dict[str, int]:
    return {n: i for i, n in enumerate(series_names(league))}


class Block(NamedTuple):
    """One stat in one window with every side, agg, norm and cross wanted: the unit the generator computes at once."""
    stat: str
    window: str
    sides: tuple[str, ...]
    aggs: tuple[str, ...]
    norms: tuple[str, ...]
    crosses: tuple[str, ...]


def blocks(league: str) -> tuple[Block, ...]:
    """Every candidate, grouped, in the one fixed order."""
    team, other = stats_of(league)
    plain = tuple(c for c in crosses_of(league) if c not in NUMERIC_CROSSES)
    numeric = tuple(c for c in crosses_of(league) if c in NUMERIC_CROSSES)
    out = []
    for stat in tuple(team) + tuple(other):
        sides = TEAM_SIDES if stat in team else ("all",)
        for w, aggs in tuple((x, WINDOW_AGGS) for x in WINDOWS) + ((GAME_WINDOW, GAME_AGGS),):
            out.append(Block(stat, w, sides, aggs, ("raw",), plain))
            out.append(Block(stat, w, sides, aggs, NORMS, numeric))
    return tuple(out)


def name_of(stat: str, side: str, window: str, agg: str, norm: str, cross: str) -> str:
    return SEP.join((PREFIX, stat, side, window, agg, norm, cross))


def block_names(b: Block) -> list[str]:
    return [name_of(b.stat, s, b.window, a, n, c) for s in b.sides for a in b.aggs for n in b.norms for c in b.crosses]


def candidate_names(league: str) -> list[str]:
    """Every generated column for this league, deterministic. Quote len() of this as candidates, never as what the model uses."""
    return [name for b in blocks(league) for name in block_names(b)]


def candidate_count(league: str) -> int:
    return sum(len(b.sides) * len(b.aggs) * len(b.norms) * len(b.crosses) for b in blocks(league))


class Spec(NamedTuple):
    stat: str
    side: str
    window: str
    agg: str
    norm: str
    cross: str


def parse(name: str) -> Spec:
    """A column name -> its parts, or ValueError: a name that is not exactly this grammar is not a wide column."""
    parts = name.split(SEP)
    if len(parts) != 7 or parts[0] != PREFIX:
        raise ValueError(f"not a wide column: {name!r}")
    spec = Spec(*parts[1:])
    known_stats = _SCORE_HOOPS + _OTHER_HOOPS + _SCORE_FOOTBALL + _OTHER_FOOTBALL
    aggs = GAME_AGGS if spec.window == GAME_WINDOW else WINDOW_AGGS
    if (spec.stat not in known_stats or spec.side not in TEAM_SIDES or (spec.window not in WINDOWS and spec.window != GAME_WINDOW) or spec.agg not in aggs
            or spec.norm not in NORMS or spec.cross not in _CROSS_FOOTBALL):
        raise ValueError(f"not a wide column: {name!r}")
    if spec.side != "all" and spec.stat not in _SCORE_HOOPS + _SCORE_FOOTBALL:
        raise ValueError(f"not a wide column (no team split for {spec.stat}): {name!r}")
    if spec.norm != "raw" and spec.cross not in NUMERIC_CROSSES:
        raise ValueError(f"not a wide column (norm {spec.norm} under cross {spec.cross} is a clone of raw): {name!r}")
    return spec


def _scale(league: str) -> tuple[float, float, float, float]:
    """(points in one score, final-margin sd, points per 5 min, regulation seconds): hand-set, fit on no rows."""
    return G.GV_LEAGUE_SCALE.get(league) or _WNBA_SCALE


def _unit(league: str, stat: str) -> float:
    """The league's natural size for this stat (lg norm)."""
    if stat in ("pts", "td", "fg", "made2", "made3", "ft", "scoring"):
        return _scale(league)[0]
    if stat == "yards":
        return _YARDS_5M
    return _PLAYS_5M[league]


def _play_stats(league: str, kind: str, text: str, p: Mapping, dh: float, da: float, change: bool, tie: bool) -> dict[str, tuple[float, float]]:
    """{stat: (home, away)} for a team stat, {stat: (value, 0)} for a per-game one. Read only from this play and the score change it caused."""
    def team(cond):
        return float(cond and dh > 0), float(cond and da > 0)

    d = dh + da
    kinded = G.play_kind(league, p)
    out = {"pts": (dh, da), "scoring": (float(dh > 0), float(da > 0)), "plays": (1.0, 0.0), "turnover": (float(kinded == "turnover"), 0.0),
           "timeout": (float(kinded == "timeout"), 0.0), "lead_change": (float(change), 0.0), "tie": (float(tie), 0.0)}
    if league in FOOTBALL:
        st = p.get("start") or {}
        down, dist, y0, gain = G._num(st.get("down"), 0.0), G._num(st.get("distance"), 0.0), G._num(st.get("yardsToEndzone")), G._num(p.get("statYardage"), 0.0)
        snap = bool(down) and "no play" not in text
        out.update({"td": team(d >= 6), "fg": team(d == 3), "penalty": (float(kinded == "foul"), 0.0), "pass": (float("pass" in kind or "reception" in kind), 0.0),
                    "rush": (float("rush" in kind), 0.0), "sack": (float("sack" in kind), 0.0), "punt": (float("punt" in kind), 0.0), "kickoff": (float("kickoff" in kind), 0.0),
                    "explosive": (float(snap and gain >= 20.0), 0.0), "negative": (float(snap and gain < 0.0), 0.0),
                    "first_down": (float(snap and dist > 0.0 and gain >= dist), 0.0), "third_down": (float(down == 3.0), 0.0), "fourth_down": (float(down == 4.0), 0.0),
                    "redzone": (float(snap and 0.0 < y0 <= 20.0), 0.0), "yards": (gain if snap else 0.0, 0.0)})
    else:
        shot = bool(p.get("shootingPlay"))
        free = "free throw" in kind
        out.update({"made2": team(d == 2), "made3": team(d == 3), "ft": team(d == 1), "foul": (float(kinded == "foul"), 0.0), "miss": (float(shot and not d and not free), 0.0),
                    "reb_off": (float("offensive rebound" in kind), 0.0), "reb_def": (float("defensive rebound" in kind), 0.0), "dunk": (float("dunk" in kind), 0.0),
                    "layup": (float("layup" in kind), 0.0), "jump": (float("jump" in kind), 0.0), "three_att": (float(shot and "three" in text), 0.0), "ft_att": (float(free), 0.0)})
    return out


def _football_points(points: float, kind: str) -> float:
    """One team's points on one football play, the same from either source (see the module docstring): a touchdown is 7, a stray +1 or +2 (a conversion arriving a row late) is 0, a safety stays 2."""
    if points >= 6.0:
        return 7.0
    if 0.0 < points < 3.0 and "safety" not in kind:
        return 0.0
    return points


class Tracker:
    """Walks one game play by play, oldest first, with O(1) state: the one definition of every per-play number, offline and live."""

    def __init__(self, league: str):
        team, _ = stats_of(league)
        self.league, self.team = league, frozenset(team)
        self.names = series_names(league)
        self.index = {n: i for i, n in enumerate(self.names)}
        d = len(self.names)
        self.cum, self.period_sum = np.zeros(d), np.zeros(d)
        self.last_t, self.since_p, self.streak = np.full(d, -1.0), np.zeros(d), np.zeros(d)
        self.home = self.away = 0.0
        self.sign, self.period = 0, None
        self.unit, self.scale = _scale(league)[0], _scale(league)[3]
        self.regs, self.length = REGULATION[league]

    def push(self, wall: float, play: Mapping) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
        """One play -> (per-play values, registers (5, d), crosses, sign of the margin). Reads only this play and earlier state."""
        h1, a1 = play.get("homeScore"), play.get("awayScore")
        nh = self.home if h1 is None else float(h1)
        na = self.away if a1 is None else float(a1)
        dh, da = max(nh - self.home, 0.0), max(na - self.away, 0.0)       # a downward correction is not a score
        kind, text = ((play.get("type") or {}).get("text") or "").lower(), (play.get("text") or "").lower()
        if self.league in FOOTBALL:
            dh, da = _football_points(dh, kind), _football_points(da, kind)
        self.home, self.away = nh, na
        margin = self.home - self.away
        sign = (margin > 0) - (margin < 0)
        change = bool(sign and self.sign and sign != self.sign)
        tie = bool(not sign and (self.home + self.away) > 0 and (dh + da) > 0)
        self.sign = sign or self.sign
        vec = np.zeros(len(self.names))
        for stat, (h, a) in _play_stats(self.league, kind, text, play, dh, da, change, tie).items():
            if stat in self.team:
                vec[self.index[f"{stat}@all"]] = h + a
                vec[self.index[f"{stat}@home"]] = h
                vec[self.index[f"{stat}@away"]] = a
            else:
                vec[self.index[f"{stat}@all"]] = h
        period = (play.get("period") or {}).get("number")
        if period != self.period:
            self.period, self.period_sum = period, np.zeros(len(self.names))
        self.cum += vec
        self.period_sum += vec
        hit = vec != 0.0
        self.last_t[hit] = wall
        self.since_p = np.where(hit, 0.0, self.since_p + 1.0)
        self.streak = np.where(hit, self.streak + 1.0, 0.0)
        seen = self.last_t >= 0.0
        regs = np.stack((self.cum, self.period_sum, np.where(seen, wall - self.last_t, -1.0), np.where(seen, self.since_p, -1.0), self.streak))
        return vec, regs, self._crosses(margin, play, sign), float(sign)

    def _crosses(self, margin: float, play: Mapping, sign: int) -> np.ndarray:
        """Game state at this play, in crosses_of(league) order. Clock from redzone.moments.detect.clock_s: the one clock parser, so rounding cannot differ."""
        clock = clock_s(play)
        period = (play.get("period") or {}).get("number")
        per = G._num(period)
        left = None
        if clock is not None and per >= 1:
            left = float(clock) if per > self.regs else (self.regs - per) * self.length + float(clock)
        frac = min(max(left, 0.0) / self.scale, 1.0) if left is not None else None
        close = abs(margin) <= self.unit
        late = frac is not None and frac <= 0.25
        row = [1.0, float(close), float(late), float(close and late), float(per >= self.regs), abs(margin) / self.unit, frac if frac is not None else 0.0, float(sign)]
        if self.league in FOOTBALL:
            st = play.get("start") or {}
            down, dist, y0 = G._num(st.get("down"), 0.0), G._num(st.get("distance"), 0.0), G._num(st.get("yardsToEndzone"))
            row += [float(down >= 3.0), float(0.0 < y0 <= 20.0), float(1.0 <= dist <= 3.0)]
        return np.array(row)


@dataclass
class Series:
    """Per-play arrays of one game, oldest first. `regs`, `cross`, `sign` are one row per head live (full False), one per play offline."""
    league: str
    walls: np.ndarray
    x: np.ndarray                 # (n, d): per-play values
    regs: np.ndarray              # (rows, 5, d)
    cross: np.ndarray             # (rows, c)
    sign: np.ndarray              # (rows,)
    full: bool = True
    _prefix: dict = field(default_factory=dict)
    _rmq: dict = field(default_factory=dict)

    def reg(self, head: np.ndarray) -> np.ndarray:
        """The row of regs/cross/sign for each head."""
        return head if self.full else np.zeros(len(head), dtype=int)

    def prefix(self, col: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Running sums of the value, its square and its nonzero count, computed once per column."""
        got = self._prefix.get(col)
        if got is None:
            v = self.x[:, col]
            n = len(v)
            p, p2, nz = np.zeros(n + 1), np.zeros(n + 1), np.zeros(n + 1)
            p[1:], p2[1:], nz[1:] = np.cumsum(v), np.cumsum(v * v), np.cumsum(v != 0.0)
            got = self._prefix[col] = (p, p2, nz)
        return got

    def range_max(self, col: int, lo: np.ndarray, head: np.ndarray) -> np.ndarray:
        """max(x[lo..head]) per head: a sparse table, built once per column."""
        table = self._rmq.get(col)
        if table is None:
            level = self.x[:, col].copy()
            table = [level]
            k = 1
            while (1 << k) <= len(level):
                prev = table[-1]
                half = 1 << (k - 1)
                table.append(np.maximum(prev[:-half], prev[half:]))
                k += 1
            self._rmq[col] = table
        length = head - lo + 1
        logs = np.floor(np.log2(length)).astype(int)
        out = np.empty(len(head))
        for k in np.unique(logs):
            mask = logs == k
            t = table[k]
            out[mask] = np.maximum(t[lo[mask]], t[head[mask] - (1 << k) + 1])
        return out


def track(league: str, timed_plays: Sequence[tuple[float, Mapping]]) -> Series:
    """(wall, play) oldest first, football through gamevalue.feed_row -> the whole game's Series. Same Tracker the live path pushes."""
    t = Tracker(league)
    n, d = len(timed_plays), len(t.names)
    walls, x, regs = np.empty(n), np.zeros((n, d)), np.zeros((n, 5, d))
    cross, sign = np.zeros((n, len(crosses_of(league)))), np.zeros(n)
    for i, (w, p) in enumerate(timed_plays):
        walls[i] = w
        x[i], regs[i], cross[i], sign[i] = t.push(w, p)
    return Series(league, walls, x, regs, cross, sign)


def _bounds(s: Series, head: np.ndarray, window: str) -> tuple[np.ndarray, np.ndarray]:
    """(first play in the window, first play of its second half) per head. Inclusive of the head, at most RING plays: the bound live state can honour."""
    span = int(window[1:])
    earliest = np.maximum(head - RING + 1, 0)
    if window[0] == "p":
        lo = head - span + 1
        mid = head - span // 2 + 1
    else:
        now = s.walls[head]
        lo = np.searchsorted(s.walls, now - float(span), "right")
        mid = np.searchsorted(s.walls, now - span / 2.0, "right")
    lo = np.maximum(lo, earliest)
    return lo, np.maximum(np.maximum(mid, lo), earliest)


def _window_agg(s: Series, head: np.ndarray, col: int, window: str, aggs: tuple[str, ...]) -> np.ndarray:
    """(len(head), len(aggs)) values of one series in one window."""
    lo, mid = _bounds(s, head, window)
    p, p2, nz = s.prefix(col)
    total = p[head + 1] - p[lo]
    count = (head + 1 - lo).astype(float)
    out = np.empty((len(head), len(aggs)))
    for k, agg in enumerate(aggs):
        if agg == "sum":
            out[:, k] = total
        elif agg == "mean":
            out[:, k] = total / count
        elif agg == "rate":
            span = np.maximum(s.walls[head] - s.walls[lo], 30.0) if window[0] == "p" else float(window[1:])
            out[:, k] = total / span * 60.0
        elif agg == "nz":
            out[:, k] = nz[head + 1] - nz[lo]
        elif agg == "max":
            out[:, k] = s.range_max(col, lo, head)
        elif agg == "std":
            out[:, k] = np.sqrt(np.maximum((p2[head + 1] - p2[lo]) / count - (total / count) ** 2, 0.0))
        elif agg == "slope":
            out[:, k] = (p[head + 1] - p[mid]) - (p[mid] - p[lo])
    return out


def _game_agg(s: Series, head: np.ndarray, col: int, aggs: tuple[str, ...]) -> np.ndarray:
    reg = s.reg(head)
    return np.stack([s.regs[reg, GAME_AGGS.index(a), col] for a in aggs], axis=1)


def _agg(s: Series, head: np.ndarray, col: int, window: str, aggs: tuple[str, ...]) -> np.ndarray:
    return _game_agg(s, head, col, aggs) if window == GAME_WINDOW else _window_agg(s, head, col, window, aggs)


def block_values(s: Series, head: np.ndarray, b: Block) -> np.ndarray:
    """The block's columns at each head, (len(head), m) float32. The only place a value is computed, offline, in selection and live."""
    index = _series_index(s.league)
    sides = []
    home_away: tuple[np.ndarray, np.ndarray] | None = None
    for side in b.sides:
        if side in ("lead", "trail"):
            if home_away is None:
                home_away = (_agg(s, head, index[f"{b.stat}@home"], b.window, b.aggs), _agg(s, head, index[f"{b.stat}@away"], b.window, b.aggs))
            home, away = home_away
            sgn = s.sign[s.reg(head)]
            flip = 1.0 if side == "lead" else -1.0
            tied = (home + away) / 2.0
            pick = np.where((sgn * flip > 0)[:, None], home, away)
            sides.append(np.where((sgn == 0)[:, None], tied, pick))
        else:
            sides.append(_agg(s, head, index[f"{b.stat}@{side}"], b.window, b.aggs))
    base = np.stack(sides, axis=1)                                             # (h, sides, aggs)
    unit = _unit(s.league, b.stat)
    norms = [base if n == "raw" else base / unit if n == "lg" else np.sign(base) * np.log1p(np.abs(base)) for n in b.norms]
    norm = np.stack(norms, axis=3)                                             # (h, sides, aggs, norms)
    positions = [crosses_of(s.league).index(c) for c in b.crosses]
    cross = s.cross[s.reg(head)][:, positions]
    return (norm[..., None] * cross[:, None, None, None, :]).reshape(len(head), -1).astype(np.float32)


# --- a kept list, computed online and for the shards ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Plan:
    """The columns wanted, grouped by block, with each column's place in the block's output."""
    league: str
    names: tuple[str, ...]
    groups: tuple[tuple[Block, np.ndarray, np.ndarray], ...]    # (block, positions in out, positions in the block's output)


@lru_cache(maxsize=32)
def compile_plan(league: str, names: tuple[str, ...], own: frozenset | None = None) -> Plan:
    """`names` may hold columns this league does not have (a kept list spans leagues): they read 0 here. With `own`, so does every column outside this league's kept list,
    so a league's model can only ever split on the columns its own forward check validated."""
    team, other = stats_of(league)
    crosses = set(crosses_of(league))
    grouped: dict[tuple[str, str], list[tuple[int, Spec]]] = {}
    for pos, name in enumerate(names):
        spec = parse(name)
        if own is not None and name not in own:
            continue
        if spec.stat not in team and spec.stat not in other:
            continue
        if spec.cross not in crosses or (spec.side != "all" and spec.stat not in team):
            continue
        grouped.setdefault((spec.stat, spec.window), []).append((pos, spec))
    groups = []
    for (stat, window), members in sorted(grouped.items()):
        specs = [s for _, s in members]
        wanted = lambda field, order: tuple(x for x in order if any(getattr(s, field) == x for s in specs))  # noqa: E731
        aggs_order = GAME_AGGS if window == GAME_WINDOW else WINDOW_AGGS
        b = Block(stat, window, wanted("side", TEAM_SIDES), wanted("agg", aggs_order), wanted("norm", NORMS), wanted("cross", crosses_of(league)))
        shape = (len(b.sides), len(b.aggs), len(b.norms), len(b.crosses))
        idx = np.ravel_multi_index(np.array([[b.sides.index(s.side), b.aggs.index(s.agg), b.norms.index(s.norm), b.crosses.index(s.cross)] for s in specs]).T, shape)
        groups.append((b, np.array([p for p, _ in members]), idx))
    return Plan(league, tuple(names), tuple(groups))


def run_plan(s: Series, head: np.ndarray, plan: Plan) -> np.ndarray:
    """(len(head), len(plan.names)) float32 of the kept columns. Columns the league lacks read 0."""
    out = np.zeros((len(head), len(plan.names)), dtype=np.float32)
    if not len(head):
        return out
    for b, pos, idx in plan.groups:
        out[:, pos] = block_values(s, head, b)[:, idx]
    return out


def features(s: Series, head: np.ndarray, names: tuple[str, ...], own: frozenset | None = None) -> dict[str, np.ndarray]:
    """{name: value per head}: value_shards.features' entry. `own` is this league's kept list."""
    if not names:
        return {}
    X = run_plan(s, head, compile_plan(s.league, names, own))
    return {n: X[:, j] for j, n in enumerate(names)}


# --- the kept list ---------------------------------------------------------------------------------------------------------------


def kept_path() -> Path:
    return Path(os.environ.get(KEPT_ENV) or DEFAULT_KEPT)


def read_kept(path: Path | str | None = None) -> dict:
    """The kept-list file, checked. ValueError on anything it cannot vouch for: no list means v5 does not start."""
    target = Path(path) if path else kept_path()
    try:
        data = json.loads(target.read_text())
    except (OSError, ValueError) as e:
        raise ValueError(f"{G.GV_FEATURES_ENV}=v5 needs a kept-column list at {target} ({type(e).__name__}); "
                         "run scripts/kaggle_gamevalue_select.py and point RZ_GV_V5_KEPT at its output") from e
    if not isinstance(data, dict) or data.get("schema") != KEPT_SCHEMA or data.get("complete") is not True:
        raise ValueError(f"{target}: not a finished kept-column list")
    if data.get("generator") != GENERATOR:
        raise ValueError(f"{target}: built by {data.get('generator')!r}, this code generates {GENERATOR!r}")
    names = data.get("columns")
    if not isinstance(names, list) or names != sorted(set(names)):
        raise ValueError(f"{target}: columns must be a sorted list without repeats")
    for n in names:
        parse(n)
    leagues = data.get("leagues")
    if not isinstance(leagues, dict) or not all(isinstance(v, dict) and isinstance(v.get("kept_columns"), list) for v in leagues.values()):
        raise ValueError(f"{target}: every league needs its own kept_columns")
    if sorted({c for v in leagues.values() for c in v["kept_columns"]}) != names:
        raise ValueError(f"{target}: columns is not the union of the leagues' kept_columns")
    return data


def load_kept(path: Path | str | None = None) -> tuple[str, ...]:
    return tuple(read_kept(path)["columns"])


def kept_sha(path: Path | str | None = None) -> str:
    """Hash of the kept columns: a shard or model built on another list is another feature set."""
    return hashlib.sha256(json.dumps(sorted(load_kept(path))).encode()).hexdigest()


def write_kept(path: Path, leagues: Mapping[str, Mapping], *, complete: bool, shard_id: str) -> dict:
    """Atomic, no timestamps: the same run gives the same bytes. Only a finished run sets complete, and only a complete file loads; a partial one stays readable for the record."""
    columns = sorted({c for v in leagues.values() for c in v.get("kept_columns", [])})
    data = {"schema": KEPT_SCHEMA, "generator": GENERATOR, "complete": bool(complete), "shard_id": shard_id, "leagues": leagues, "columns": columns}
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
    os.replace(tmp, target)
    return data


# --- the live call ----------------------------------------------------------------------------------------------------------------


def game_for(league: str) -> "WideGame":
    """A WideGame for the active v5 feature set, this league's own columns live and the rest 0, exactly as the shards were built."""
    names = tuple(f for f in G.GV_FEATURES if f.startswith(PREFIX + SEP))
    return WideGame(league, names, G.GV_V5_OWN.get(league, frozenset()))


class WideGame:
    """One live game: fixed-size history (RING plays), kept columns on demand. Everything it holds is bounded, whatever the game's length."""

    def __init__(self, league: str, names: Sequence[str], own: frozenset | None = None):
        self.league, self.names = league, tuple(names)
        self.plan = compile_plan(league, self.names, own)
        self._reset()

    def _reset(self) -> None:
        self.tracker = Tracker(self.league)
        d = len(self.tracker.names)
        self._walls, self._x = np.zeros(RING), np.zeros((RING, d))
        self._regs, self._cross, self._sign = None, None, 0.0
        self._kept, self._seen, self._last_id = 0, 0, None

    def state_bytes(self) -> int:
        """What this game holds in numpy arrays: the bound the test measures."""
        t = self.tracker
        arrays = (self._walls, self._x, t.cum, t.period_sum, t.last_t, t.since_p, t.streak)
        return sum(a.nbytes for a in arrays) + (self._regs.nbytes + self._cross.nbytes if self._regs is not None else 0)

    def _push(self, wall: float, play: Mapping) -> None:
        if not G.feed_row(self.league, play):
            return
        vec, regs, cross, sign = self.tracker.push(wall, play)
        at = self._kept % RING
        self._walls[at], self._x[at] = wall, vec
        self._regs, self._cross, self._sign = regs, cross, sign
        self._kept += 1

    def update(self, timed_plays: Sequence[tuple[float, Mapping]]) -> None:
        """The feed's plays so far, oldest first. New plays are pushed; a feed that rewrote what we saw starts over, so a correction cannot strand old state."""
        n = len(timed_plays)
        if n < self._seen or (self._seen and timed_plays[self._seen - 1][1].get("id") != self._last_id):
            self._reset()
        for wall, play in timed_plays[self._seen:n]:
            self._push(wall, play)
        self._seen = n
        if n:
            self._last_id = timed_plays[n - 1][1].get("id")

    def _series(self) -> Series:
        n = min(self._kept, RING)
        order = np.arange(self._kept - n, self._kept) % RING
        return Series(self.league, self._walls[order], self._x[order], self._regs[None], self._cross[None], np.array([self._sign]), full=False)

    def matrix(self) -> np.ndarray | None:
        """(1, kept) float32 at the feed head; None before the first play."""
        if not self._kept:
            return None
        s = self._series()
        return run_plan(s, np.array([len(s.walls) - 1]), self.plan)

    def values(self) -> dict[str, float] | None:
        """{kept column: value} at the feed head; None before the first play."""
        X = self.matrix()
        return None if X is None else {n: float(X[0, j]) for j, n in enumerate(self.names)}
