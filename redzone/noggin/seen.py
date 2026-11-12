"""Seen-state: what the viewer has seen of each game in the PARK show, and what the feed has reported since.

Per game, from the show's own seconds and the feed's rows so far (never hindsight):
  - when it was last on screen: the show's second (its wall time is the grid's base plus that second) and the game's
    own second then, with the score, period and game clock the feed reported there (`Snap`, `snap_at`);
  - what the feed has reported since, up to the show's edge: every score (`scores`), the lead changes (`lead_changes`),
    the biggest run (`best_run`) and the big plays missed, each with its leverage and feed stamp (`Missed`);
  - a staleness score: each second a game in progress goes unseen adds the game's leverage then (value2's margin-by-time
    multiplier: about 1 for most of a game, up to 3 when close and late, 0.3 in a blowout), a dead ball a quarter of
    that, a break nothing (`stale_step`). A close, late game goes stale about three times as fast as a quiet one;
  - a recap line built from those facts only (`recap_line`), e.g. "Last time: LAL 31-25, 1Q 0:30. Since: LAC 12-4 run".
    It is a template: no AI writes it, because AI recaps swap facts.

The catch-up engine's interface (branch catchup-engine; kept stable):
    tracker.missed_since(g)            -> [Missed, ...] the big plays game g aired since it was last on screen that were
                                          not seen (oldest first), each with its feed stamp, grid second, value, leverage
    tracker.missed_since(g, since=p)   -> the same from the game's own grid second p on

Pure: no clock, no device, no file, no network; the show hands it every fact. The show builds one only with
park.seen or park.director_v1 on (`redzone/noggin/park_show.py`); off, nothing here runs.

Why: phase 2C ("what you have seen"): nothing in the show knew what the viewer had already seen, so it could not keep
every game fresh, recap a game on return or replay the plays that were missed.
"""
from __future__ import annotations

import bisect
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from redzone.noggin.park_core import PARK_PARAMS
from redzone.sources import espn


@dataclass(frozen=True)
class SeenParams:
    """Seen-state numbers (rule K2: no bare timing literals)."""
    run_min: int = 8              # one side outscoring the other by this many points or more over a stretch is a run
    dead_w: float = 0.25          # an unseen dead-ball second adds this times the game's leverage to its staleness...
    break_w: float = 0.0          # ...an unseen break second (timeout, period break, halftime) this times
    lead_in: int = PARK_PARAMS.seen_lead_s   # a big play counts as seen only with this many seconds before it on screen
    #                                          (the replay judge's own rule, park_core.ParkParams.seen_lead_s)
    rejoin: int = 2               # a picture this close after the last second shown of a game continues the same look
    text_max: int = 80            # a play's text is cut to this many characters


SEEN = SeenParams()


# ---------------------------------------------------------------------------------------------- the feed's facts
@dataclass(frozen=True)
class Snap:
    """A game's situation as the feed reported it at one moment (all None before its first row)."""
    period: int | None = None
    clock: str | None = None      # the game clock as the feed shows it ("5:32", "30.1")
    away: int | None = None
    home: int | None = None
    stamp: float | None = None    # the feed stamp of the row it was read from


@dataclass(frozen=True)
class Score:
    """One change of the score the feed reported."""
    stamp: float
    away: int
    home: int
    away_pts: int                 # points the change added for each side (a correction can be negative)
    home_pts: int


@dataclass(frozen=True)
class Missed:
    """One big play a game aired while it was not on screen: the catch-up engine's input."""
    game: int                     # the game's index in the show
    at: int                       # its second on the show's grid (the game's own time: what a stream at the show's edge
    #                               showed at wall time base + at)
    stamp: float                  # its feed stamp (the feed row's own time, epoch seconds)
    value: float                  # its must-see weight (park_facts.value_of: 5 a score, 3 a turnover or red-zone trip, 1 other)
    leverage: float               # the game's leverage at that second (value2: about 1, up to 3 close and late)
    period: int | None = None
    clock: str | None = None
    text: str = ""                # the feed row's own text, cut short (public play-by-play)


def score_of(p: dict) -> tuple[int, int] | None:
    """(away, home) as one feed row reports it, or None."""
    a, h = espn._int(p.get("awayScore")), espn._int(p.get("homeScore"))
    return None if a is None or h is None else (a, h)


def _score_before(rows: Sequence[dict], i: int) -> tuple[int, int] | None:
    """The last score the feed reported at row i or before it."""
    while i >= 0:
        sc = score_of(rows[i])
        if sc is not None:
            return sc
        i -= 1
    return None


def snap_at(rows: Sequence[dict], stamps: Sequence[float], at: float) -> Snap:
    """The situation the feed reported at feed time `at`: the period and clock of the last row stamped at or before it,
    and the last score reported up to it."""
    i = bisect.bisect_right(stamps, at) - 1
    if i < 0:
        return Snap()
    p, sc = rows[i], _score_before(rows, i)
    return Snap(espn._int(espn._get(p, "period", "number")), espn._get(p, "clock", "displayValue"),
                sc[0] if sc else None, sc[1] if sc else None, float(stamps[i]))


def scores(rows: Sequence[dict], stamps: Sequence[float], lo: float, hi: float) -> list[Score]:
    """Every change of the score the feed reported after feed time lo and up to hi, in feed order. The score the feed
    had reported by lo is where it starts from (a game's first score row starts from itself)."""
    i, j = bisect.bisect_right(stamps, lo), bisect.bisect_right(stamps, hi)
    prev, out = _score_before(rows, i - 1), []
    for k in range(i, j):
        sc = score_of(rows[k])
        if sc is None:
            continue
        if prev is not None and sc != prev:
            out.append(Score(float(stamps[k]), sc[0], sc[1], sc[0] - prev[0], sc[1] - prev[1]))
        prev = sc
    return out


def _sign(x: int) -> int:
    return (x > 0) - (x < 0)


def lead_changes(start: tuple[int, int] | None, changes: Sequence[Score]) -> int:
    """How many times the lead passed from one side to the other (a tie on the way does not count by itself)."""
    lead = _sign(start[1] - start[0]) if start else 0
    n = 0
    for s in changes:
        d = _sign(s.home - s.away)
        if d:
            n += int(lead != 0 and d != lead)
            lead = d
    return n


def best_run(changes: Sequence[Score]) -> tuple[str, int, int] | None:
    """The biggest run: the stretch of consecutive scores over which one side's points less the other's are largest, as
    (side "away" | "home", that side's points, the other's), or None with no score. A run starts with a score of its own."""
    best, top = None, 0
    for side in ("away", "home"):
        cur = mine = theirs = 0
        for s in changes:
            f, a = (s.away_pts, s.home_pts) if side == "away" else (s.home_pts, s.away_pts)
            if cur <= 0:
                cur, mine, theirs = f - a, f, a
            else:
                cur, mine, theirs = cur + f - a, mine + f, theirs + a
            if cur > top:
                best, top = (side, mine, theirs), cur
    return best


def stale_step(live: bool, brk: bool, lev: float, p: SeenParams = SEEN) -> float:
    """What one unseen second of a game in progress adds to its staleness: its leverage for a second of play, dead_w of
    that for a dead ball, break_w of it in a break."""
    return float(lev) * (p.break_w if brk else 1.0 if live else p.dead_w)


def seen_at(m: int, shown: Sequence[Sequence[int]], lead_in: int) -> bool:
    """Was game second m on screen inside one look that began at least lead_in seconds before it?"""
    return any(a <= m - lead_in and m <= b for a, b in shown)


def unseen_big(mom: dict, shown: Sequence[Sequence[int]], lo: int, hi: int, lead_in: int) -> list[tuple[int, float]]:
    """The big plays (game second -> must-see weight) after second lo and up to hi that were not seen, oldest first."""
    return [(m, float(mom[m])) for m in sorted(k for k in mom if lo < k <= hi) if not seen_at(m, shown, lead_in)]


# ---------------------------------------------------------------------------------------------- the recap line (a template)
def sides(matchup: str) -> tuple[str, str]:
    """(away, home) short names from the show's game name ("LAC @ LAL")."""
    a, sep, h = str(matchup).partition(" @ ")
    return (a.strip(), h.strip()) if sep and a.strip() and h.strip() else ("away", "home")


def score_text(away: str, home: str, a: int, h: int) -> str:
    """The leader first: "LAL 31-25"; level: "tied 25-25"."""
    if a == h:
        return f"tied {a}-{h}"
    return f"{away} {a}-{h}" if a > h else f"{home} {h}-{a}"


def period_text(period: int | None, clock: str | None, halves: bool = False) -> str:
    """"2Q 5:32", "2H 5:32" (college basketball plays halves), "OT 1:02", "2OT"."""
    if not period:
        return ""
    regs = 2 if halves else 4
    name = (f"{period}{'H' if halves else 'Q'}" if period <= regs
            else "OT" if period == regs + 1 else f"{period - regs}OT")
    return f"{name} {clock}" if clock else name


def recap_line(away: str, home: str, then: Snap, changes: Sequence[Score], *, missed: int = 0, halves: bool = False,
               run_min: int = SEEN.run_min) -> str:
    """"Last time: LAL 31-25, 1Q 0:30. Since: LAC 12-4 run, 1 lead change, 2 big plays missed". Only the feed's own
    facts, put in a fixed template ("" when the game has not been seen with a score)."""
    if then.away is None or then.home is None:
        return ""
    when = period_text(then.period, then.clock, halves)
    out = f"Last time: {score_text(away, home, then.away, then.home)}" + (f", {when}" if when else "") + "."
    if not changes:
        return out + " Since: no score" + (f", {missed} big play{'s' * (missed != 1)} missed" if missed else "")
    run = best_run(changes)
    pa, ph = sum(s.away_pts for s in changes), sum(s.home_pts for s in changes)
    if run is not None and run[1] - run[2] >= run_min:
        part = f"{away if run[0] == 'away' else home} {run[1]}-{run[2]} run"
    elif pa == ph:
        part = f"even {pa}-{ph}"
    else:
        part = f"{away} {pa}-{ph}" if pa > ph else f"{home} {ph}-{pa}"
    parts = [part]
    lc = lead_changes((then.away, then.home), changes)
    if lc:
        parts.append(f"{lc} lead change{'s' * (lc != 1)}")
    if missed:
        parts.append(f"{missed} big play{'s' * (missed != 1)} missed")
    return out + " Since: " + ", ".join(parts)


# ---------------------------------------------------------------------------------------------- the tracker
@dataclass
class GameSeen:
    """One game's seen-state."""
    last_t: int | None = None     # the show's second it was last on screen (wall time: the grid's base + it)
    last_p: int | None = None     # the game's own second that was on screen then
    shown: list = field(default_factory=list)   # [first, last] game seconds of each look, in the order shown
    stale: float = 0.0            # staleness points since it was last on screen
    unseen_s: int = 0             # show seconds in progress since it was last on screen (or since the show began)
    worst_s: int = 0              # the longest it has gone unseen while in progress


@dataclass(frozen=True)
class Since:
    """What the feed reported of a game between the last second of it on screen and the show's edge."""
    then: Snap
    now: Snap
    changes: tuple
    lead_changes: int
    run: tuple | None
    missed: tuple


class SeenTracker:
    """The show's seen-state, game by game. `tick` each second of the show; the rest are questions about it.

    base: the grid's base (epoch seconds); shifts[g]: game g's grid second = its feed stamp + shifts[g] - base;
    rows(g) -> (rows, stamps): game g's feed rows so far with the stamps the show uses (park_facts.GameFacts plays, wall);
    halves[g]: game g plays halves (college basketball)."""

    def __init__(self, n: int, base: int, shifts: Sequence[float], rows: Callable[[int], tuple], *,
                 halves: Sequence[bool] | None = None, params: SeenParams = SEEN):
        self.g = [GameSeen() for _ in range(n)]
        self.base, self.shifts, self.rows, self.p = int(base), [float(x) for x in shifts], rows, params
        self.halves = list(halves) if halves is not None else [False] * n
        self.t: int | None = None
        self.t0: int | None = None
        self.arrs: list | None = None

    def stamp(self, g: int, p: int) -> float:
        """The feed time of game g's grid second p."""
        return self.base + p - self.shifts[g]

    def tick(self, t: int, on: int, p: int | None, arrs: list) -> None:
        """Show second t: game `on` is on screen at its own second p (None: no picture this second, a flip). arrs = the
        show's per-game arrays (park_core.Arr: the live span a0..a1, dead, brk, lev), as the feed has filled them."""
        self.t, self.arrs = t, arrs
        if self.t0 is None:
            self.t0 = t
        for g, s in enumerate(self.g):
            if g == on:
                if p is None:
                    continue                                     # the picture is changing: neither seen nor unseen
                sh = s.shown
                if sh and sh[-1][0] <= p <= sh[-1][1] + self.p.rejoin:
                    sh[-1][1] = max(sh[-1][1], p)
                else:
                    sh.append([p, p])
                s.last_t, s.last_p, s.stale, s.unseen_s = t, p, 0.0, 0
                continue
            x = arrs[g]
            if not (x.a0 <= t <= x.a1):
                continue                                         # not in progress at the show's edge
            q = min(x.n - 1, max(0, t))
            s.stale += stale_step(not x.dead[q], bool(x.brk[q]), float(x.lev[q]), self.p)
            s.unseen_s += 1
            s.worst_s = max(s.worst_s, s.unseen_s)

    # ---- questions ---------------------------------------------------------------------------------------------------
    def last_seen_s(self, g: int) -> int | None:
        """Show seconds since game g was last on screen (0 while it is), or None when it has not been on yet."""
        s = self.g[g]
        return None if s.last_t is None or self.t is None else self.t - s.last_t

    def staleness(self, g: int) -> float:
        return self.g[g].stale

    def missed_since(self, g: int, since: int | None = None) -> list[Missed]:
        """The big plays game g aired after its own grid second `since` (default: the last second of it on screen, or the
        show's first second when it has not been on) and up to the show's edge, that were not on screen with their
        lead-in; oldest first. The feed stamp, period, clock and text come from the feed row the play is stamped at."""
        if self.t is None or self.arrs is None:
            return []
        s, x = self.g[g], self.arrs[g]
        lo = since if since is not None else s.last_p if s.last_p is not None else (self.t0 or 0) - 1
        hi = min(self.t, x.n - 1)
        rows, stamps = self.rows(g)
        out = []
        for m, v in unseen_big(x.mom, s.shown, lo, hi, self.p.lead_in):
            st = self.stamp(g, m)
            i, j = bisect.bisect_left(stamps, st), bisect.bisect_left(stamps, st + 1.0)
            scoring = [k for k in range(i, j) if rows[k].get("scoringPlay")]
            k = scoring[-1] if scoring else j - 1 if j > i else bisect.bisect_right(stamps, st) - 1
            r = rows[k] if 0 <= k < len(rows) else {}
            out.append(Missed(g, int(m), float(stamps[k]) if 0 <= k < len(stamps) else st, v, float(x.lev[min(x.n - 1, max(0, m))]),
                              espn._int(espn._get(r, "period", "number")), espn._get(r, "clock", "displayValue"),
                              " ".join(str(r.get("text") or "").split())[: self.p.text_max]))
        return out

    def since(self, g: int) -> Since | None:
        """What the feed reported of game g from the last second of it on screen to the show's edge (None before it has
        been on)."""
        s = self.g[g]
        if s.last_p is None or self.t is None:
            return None
        rows, stamps = self.rows(g)
        lo, hi = self.stamp(g, s.last_p), self.stamp(g, self.t)
        ch = scores(rows, stamps, lo, hi)
        then = snap_at(rows, stamps, lo)
        start = (then.away, then.home) if then.away is not None and then.home is not None else None
        return Since(then, snap_at(rows, stamps, hi), tuple(ch), lead_changes(start, ch), best_run(ch), tuple(self.missed_since(g)))

    def recap(self, g: int, matchup: str) -> str:
        """The Show tab's line for game g ("" before it has been on screen with a score)."""
        sn = self.since(g)
        if sn is None:
            return ""
        away, home = sides(matchup)
        return recap_line(away, home, sn.then, sn.changes, missed=len(sn.missed), halves=self.halves[g], run_min=self.p.run_min)

    def view(self, g: int, matchup: str) -> dict:
        """Game g for the Show tab: seconds since it was on screen, its staleness, the big plays missed, the recap line."""
        ago = self.last_seen_s(g)
        missed = len(self.missed_since(g)) if self.t is not None else 0
        return {"last_seen_s": ago, "stale": round(self.g[g].stale, 1), "missed": missed,
                "recap": (self.recap(g, matchup) if ago else "") or None}

    def worst(self) -> int:
        """The longest any game has gone unseen while in progress, in show seconds."""
        return max((s.worst_s for s in self.g), default=0)
