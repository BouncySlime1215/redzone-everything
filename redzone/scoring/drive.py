"""RedZone mode, part 1: is this NFL drive alive? (drive heat, 0..1)

The base scorer (scoring/engine.py) knows the down, distance and field position of one poll.
RedZone producers watch the DRIVE: a team that has moved 50 yards in six plays, just hit a 30-yard
pass and sits at the opponent's 30 is worth tuning to; a third straight three-and-out is not. This
tracks each NFL game's current drive from the stream of GameStates (no extra feeds) and turns it
into heat, then adjusts the scorer's output through its public API only (a subclass that calls
Scorer.score and edits the result): the hype-score internals stay the other builder's.

    heat = 0.45·EP(field position)/5 + 0.30·moved + 0.25·explosive  (+ red-zone/4th-down/drill bumps)

EP (expected points at the line of scrimmage) is the rough nflfastR/Burke curve: ~0.4 at your own
20, ~2.0 at midfield, ~3.7 at the opponent's 20, ~4.9 at the 5. moved = yards gained on the drive
(40+ = 1). explosive = a 20+ yard gain on this drive in the last two minutes.

Punt-fest signs lower an NFL game's score instead ("dead"): a punt coming (4th down deep in its own
half), 3rd & 7+ in its own half, and a run of drives that ended without points.

The decider reads Scored.factors["drive_heat"] (decider.nfl_priority): while an NFL drive is hot,
other sports' non-crunch moments rank below it. Crunch time, your teams and garbage time are
untouched (garbage time keeps the scorer's verdict: a decided game isn't worth a whip-around).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, replace

from redzone import crunch
from redzone.models import GameState, League, Phase
from redzone.scoring import breaks
from redzone.scoring.engine import Personal, Scored, Scorer

# Expected points by yards to the goal line (piecewise linear). Rough nflfastR/Burke values.
_EP = [(1, 5.6), (5, 4.9), (10, 4.4), (20, 3.7), (35, 2.8), (50, 2.0), (65, 1.2), (80, 0.4), (99, -0.4)]
EXPLOSIVE_YDS = 20
EXPLOSIVE_FADE_S = 150.0
HEAT_RELEASE_S = 40.0     # heat rises at once and decays with this half-life (a drive doesn't go cold mid-huddle)
HOT = 0.5                 # heat at or above this: "a drive is alive" (decider.nfl_priority)


def expected_points(ytg: int | None) -> float:
    if ytg is None:
        return 0.0
    y = max(1, min(99, ytg))
    for (x0, e0), (x1, e1) in zip(_EP, _EP[1:]):
        if x0 <= y <= x1:
            return e0 + (e1 - e0) * (y - x0) / (x1 - x0)
    return 0.0


def _punt(text: str) -> bool:
    t = (text or "").lower()
    return " punts " in f" {t} " or t.startswith("punt") or "punt is blocked" in t


@dataclass
class Drive:
    team: str | None = None
    start_ytg: int | None = None
    ytg: int | None = None
    plays: int = 0
    explosive_at: float | None = None
    explosive_yds: int = 0


@dataclass
class DriveState:
    drive: Drive = field(default_factory=Drive)
    seen: set = field(default_factory=set)
    empty_drives: int = 0          # drives in a row that ended with no points (punts, downs, picks)
    heat: float = 0.0
    heat_at: float | None = None
    last_score: tuple | None = None


@dataclass
class Heat:
    heat: float          # smoothed 0..1
    raw: float           # this poll's value
    dead: float          # punt-fest signs 0..1
    why: str             # short phrase for the reason line ("" if nothing to say)


class DriveBook:
    """Per-game drive tracking from successive GameStates."""

    def __init__(self, clock=time.time):
        self._clock = clock
        self.games: dict[str, DriveState] = {}

    def observe(self, g: GameState) -> Heat:
        now = self._clock()
        st = self.games.setdefault(g.game_id, DriveState())
        sit = g.situation
        score = (g.home.score, g.away.score)
        scored = st.last_score is not None and score != st.last_score and None not in score
        st.last_score = score
        dr = st.drive
        changed = bool(sit.possession and sit.possession != dr.team)
        if changed:
            # New drive: did the last one come away empty?
            if dr.team is not None:
                st.empty_drives = 0 if scored else st.empty_drives + 1
            st.drive = dr = Drive(sit.possession, sit.yards_to_goal, sit.yards_to_goal)
        elif scored:
            st.empty_drives = 0
        plays = list(g.recent_plays) or ([g.last_play] if g.last_play else [])
        for p in plays:
            if not p.id or p.id in st.seen:
                continue
            st.seen.add(p.id)
            if _punt(p.text):
                continue
            dr.plays += 1
        if sit.yards_to_goal is not None and sit.possession == dr.team:
            if dr.ytg is not None and dr.ytg - sit.yards_to_goal >= EXPLOSIVE_YDS:
                dr.explosive_at, dr.explosive_yds = now, dr.ytg - sit.yards_to_goal
            dr.ytg = sit.yards_to_goal
            if dr.start_ytg is None:
                dr.start_ytg = sit.yards_to_goal
        raw, dead, why = _heat(g, dr, st.empty_drives, now)
        # Fast attack, slow release.
        decayed = st.heat * 0.5 ** ((now - st.heat_at) / HEAT_RELEASE_S) if st.heat_at is not None else 0.0
        if changed or scored:
            decayed = 0.0     # the ball changed hands or the drive scored: that drive is over
        st.heat, st.heat_at = max(raw, decayed), now
        return Heat(st.heat, raw, dead, why)


def _heat(g: GameState, dr: Drive, empty: int, now: float) -> tuple[float, float, str]:
    sit = g.situation
    ytg = sit.yards_to_goal
    if g.phase != Phase.LIVE or not sit.possession or ytg is None:
        return 0.0, 0.0, ""
    ep = max(0.0, min(1.0, expected_points(ytg) / 5.0))
    gained = (dr.start_ytg - ytg) if dr.start_ytg is not None else 0
    moved = max(0.0, min(1.0, gained / 40.0))
    boom = 0.0
    if dr.explosive_at is not None:
        boom = max(0.0, 1.0 - (now - dr.explosive_at) / EXPLOSIVE_FADE_S)
    h = 0.45 * ep + 0.30 * moved + 0.25 * boom
    q, clk = g.period or 0, g.clock_s
    off_diff = None
    if g.score_diff is not None:
        off_diff = g.score_diff if sit.possession == g.home.abbr else -g.score_diff
    if ytg <= 20:
        h += 0.10
    if sit.goal_to_go:
        h += 0.10
    if sit.down == 4 and ytg < 40:
        h += 0.15     # going for it, or a long field goal: either way, watch the snap
    drill = (q == 2 and clk is not None and clk <= 120) or crunch.trailing_drill(q, clk, off_diff)
    if drill and ytg <= 75:
        h += 0.15
    h = max(0.0, min(1.0, h))
    dead = 0.0
    if sit.down == 4 and ytg > 40 and not drill:
        dead = max(dead, 1.0)    # punt coming
    elif sit.down == 3 and (sit.distance or 0) >= 7 and ytg > 50 and not drill:
        dead = max(dead, 0.5)    # 3rd & long in its own half: probably a punt next
    if empty >= 3:
        dead = max(dead, min(1.0, (empty - 2) / 3))   # punt-fest
    why = ""
    if h >= HOT:
        if dr.explosive_at is not None and boom > 0.3:
            why = f"{sit.possession} drive: {dr.explosive_yds}-yard gain"
        elif gained >= 30:
            why = f"{sit.possession} driving: {gained} yards in {dr.plays} plays"
    elif empty >= 3:
        why = f"{empty} empty drives in a row"
    return h, dead, why


class DriveScorer(Scorer):
    """The scorer, plus NFL drive heat. Only Scorer.score's output is touched (public API)."""

    def __init__(self, cfg, clock=time.time):
        super().__init__(cfg, clock=clock)
        self.drives = DriveBook(clock=clock)
        # The break forecast for each NFL game (scoring/breaks.py), so the decider can tell a drive
        # it's about to tune to from one about to go to commercial. Its hold keeps a break that has
        # left the data (the kickoff after the TD's ads) forecast until it has reached the screen.
        self.breaks = breaks.BreakBook(clock=clock, table=_one_table(cfg))

    def use_breaks(self, book: breaks.BreakBook) -> None:
        """[one brain, step 5] read the engine's BreakBook (the one instance) instead of a private one."""
        self.breaks = book

    def score(self, g: GameState, me: Personal | None = None) -> Scored:
        s = super().score(g, me)
        if g.league.base != League.NFL or g.phase != Phase.LIVE or not self.cfg.get("redzone_mode.enabled", True):
            return s
        h = self.drives.observe(g)
        self.breaks.observe(g)
        f = self.breaks.forecast(g)
        s = apply_heat(s, h, self.cfg)
        return replace(s, factors={**s.factors, "break_p": round(f.p, 2), "break_s": round(f.expect_s)})


def _one_table(cfg):
    """The measured break table when ``breaks.one_model`` is on, else None (breaks.LEN)."""
    if not cfg.get("breaks.one_model", False):
        return None
    from redzone.contracts.breaks import load_table
    return load_table()


def apply_heat(s: Scored, h: Heat, cfg) -> Scored:
    if s.garbage:
        return replace(s, factors={**s.factors, "drive_heat": 0.0})
    boost = float(cfg.get("redzone_mode.heat_points", 8)) * h.heat
    damp = 1.0 - float(cfg.get("redzone_mode.dead_damp", 0.25)) * h.dead * (1.0 - h.heat)
    score = max(0.0, min(100.0, s.score * damp + boost))
    raw = max(0.0, min(100.0, s.raw * damp + boost))
    tags = list(s.tags)
    if h.why and h.why not in tags:
        tags.append(h.why)
    factors = {**s.factors, "drive_heat": round(h.heat, 2)}
    if h.dead:
        factors["punt_fest"] = round(h.dead, 2)
    return replace(s, score=score, raw=raw, tags=tags, factors=factors)
