"""Director v1: the rule-based show director, the baseline a later AI director must beat (phase 2C, item 2).

Each time the PARK planner weighs its options (every search, about every 2 s), the director weighs each game by three
goals, as plain multipliers on what the planner already values (`Show.pref`, and where the idle hidden stick waits,
`Show.prospect`):
  (a) never miss a big play: a game with a high-leverage moment pending (a close-and-late or overtime play in the feed's
      last `big_look` seconds: the grader's own close-and-late rule, as `Show.anchor` reads it) is weighted `big_w`;
  (b) keep every game fresh: a game's staleness (`seen.SeenTracker`: its unseen seconds weighted by leverage) weighs it
      up to 1 + fresh_w, reached at `stale_ref` points (about 10 minutes of ordinary play unseen);
  (c) Nick's taste: his league weights (`score.league_weight`, the scorer's own value scale) relative to the best league
      on the slate, so a one-league night is unchanged. His teams (park.show.my_pref) and the football slider stay as
      they were; the frozen grader is read and changed nowhere.
It never cuts by itself: the planner still plans, every jump still needs its warrant, and Rule #1 stays the hard guard
(the show's `refusal`, the padding, the lateness cap), all unchanged. Each jump's warrant names the goal that drove it
(`driver`): "a", "b" or "c".

Behind `park.director_v1` (default OFF). Off, the show never builds a Director: every decision is as before.
Pure: no clock, no device, no file.

What the replay found (fake sticks, scripts/park_director_ab.py; for whoever builds the AI director): the numbers were
picked among five on tuning nights 20261024-26. Pushing freshness harder (fresh_w 2 or 3) made the games STALER there
(mean age of what is known of a game 4.3 -> 5.0 min): a multiplier on value cannot buy freshness that the hidden stick's
tunes and the dead-ball windows do not offer. The longest unseen stretches are mostly a halftime plus another game's crunch
time, which the crunch anchor holds on purpose.

Why: Nick, Nov 11: the show's goal becomes three things at once (never miss a big play, keep every game fresh, his
taste), and a rule-based director is built first as the baseline the AI director must beat on nights it never saw.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, fields

GOALS = {"a": "never miss a big play", "b": "keep every game fresh", "c": "Nick's taste"}


@dataclass(frozen=True)
class DirectorParams:
    """Director v1's numbers (`park.director.<name>`); the league weights are `score.league_weight`."""
    big_w: float = 3.0            # (a) a game with a high-leverage moment pending is worth this many times more
    big_look: int = 90            # ...pending: a high-leverage play in the feed's last this-many seconds (park.show.crunch_look)
    fresh_w: float = 1.0          # (b) a stale game is worth up to 1 + this...
    stale_ref: float = 600.0      # ...reached at this many staleness points (seen.stale_step: a second of play adds its leverage)
    taste: bool = True            # (c) weigh each game by its league's weight, relative to the best league on the slate
    league_weight: tuple = ()     # ((league, weight), ...): score.league_weight

    @classmethod
    def from_config(cls, get: Callable[..., object]) -> "DirectorParams":
        """Each number from `park.director.<name>`, or its default (a value of the wrong kind keeps the default); the
        league weights from `score.league_weight`."""
        kw: dict = {}
        for f in fields(cls):
            if f.name == "league_weight":
                continue
            d, v = f.default, get(f"park.director.{f.name}", f.default)
            if isinstance(d, bool):
                kw[f.name] = v if isinstance(v, bool) else d
            else:
                kw[f.name] = type(d)(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else d
        lw = get("score.league_weight", None)
        if isinstance(lw, dict):
            kw["league_weight"] = tuple(sorted((str(k).lower(), float(v)) for k, v in lw.items()
                                               if isinstance(v, (int, float)) and not isinstance(v, bool)))
        return cls(**kw)


DIRECTOR = DirectorParams()


@dataclass(frozen=True)
class Weigh:
    """One game's weights at one choice."""
    a: float = 1.0                # (a) big_w with a high-leverage moment pending, else 1
    b: float = 1.0                # (b) 1 .. 1 + fresh_w by staleness
    c: float = 1.0                # (c) the league's weight relative to the slate's best
    stale: float = 0.0            # its staleness points then
    unseen_s: int | None = None   # show seconds since it was on screen (None: not yet)

    @property
    def w(self) -> float:
        return self.a * self.b * self.c


def taste_weights(leagues: Sequence[str], table: Sequence[tuple]) -> list[float]:
    """Each game's league weight over the best league's on the slate (the scorer's clamp, 0.5-1.5; unknown = 1)."""
    tab = {str(k).lower(): v for k, v in table}

    def one(lg: str) -> float:
        try:
            return max(0.5, min(1.5, float(tab.get(str(lg).lower(), 1.0))))
        except (TypeError, ValueError):
            return 1.0
    raw = [one(lg) for lg in leagues]
    top = max(raw, default=1.0)
    return [r / top for r in raw]


def freshness(stale: float, p: DirectorParams = DIRECTOR) -> float:
    """(b): 1 for a game just seen, rising in a straight line to 1 + fresh_w at stale_ref staleness points."""
    if p.stale_ref <= 0:
        return 1.0
    return 1.0 + p.fresh_w * min(1.0, max(0.0, stale) / p.stale_ref)


def pending(x, t: int, edge: int, look: int) -> bool:
    """(a): is a high-leverage moment pending in game x (the PARK core's Arr) at show second t? It is in progress and the
    feed's last `look` seconds up to its edge hold a high-leverage play (the same test as `Show.anchor`)."""
    e = min(x.n - 1, max(0, edge))
    return x.a0 <= t <= x.a1 and x.hlc[e + 1] - x.hlc[max(0, e - look)] > 0


def driver(src: Weigh, dst: Weigh, why: str = "") -> str:
    """Which goal drove a jump from a game weighed `src` to one weighed `dst`: "a" when a big moment is pending there and
    not here; else whichever of (b) freshness and (c) taste favours the landing more; when neither does, "c" for a jump
    made because the better play is there (the value scale is Nick's grader) and "b" for one that leaves dead time or a
    finished game for live play."""
    if dst.a > src.a:
        return "a"
    rb, rc = dst.b / max(1e-9, src.b), dst.c / max(1e-9, src.c)
    if max(rb, rc) > 1.0 + 1e-9:
        return "b" if rb >= rc else "c"
    return "c" if "better play" in why else "b"


def driver_text(letter: str, dst: Weigh, league: str) -> str:
    """The driver in words, for the decision record and the Show tab."""
    if letter == "a":
        return "a: never miss a big play (a close, late moment is pending there)"
    if letter == "b":
        if dst.unseen_s is None:
            return "b: keep every game fresh (not seen yet tonight)"
        return f"b: keep every game fresh (unseen {dst.unseen_s // 60} min {dst.unseen_s % 60} s, staleness {dst.stale:.0f})"
    return f"c: Nick's taste ({str(league).upper()} weighted {dst.c:.2f} on this slate; the value scale)"


class Director:
    """Weighs every game at each choice (`weigh`); `now` holds the last weights, `at` the show second they were made."""

    def __init__(self, leagues: Sequence[str], params: DirectorParams = DIRECTOR):
        self.p, self.leagues = params, list(leagues)
        self.taste = taste_weights(self.leagues, params.league_weight) if params.taste else [1.0] * len(self.leagues)
        self.now: list[Weigh] = []
        self.at: int | None = None

    def weigh(self, t: int, on: int, arrs: Sequence, edges: Sequence[int], seen) -> list[Weigh]:
        """Each game's weights at show second t: `on` is the game on screen, arrs the show's arrays, edges[g] the last
        second of game g the feed has reported, seen the show's `seen.SeenTracker`."""
        p, out = self.p, []
        for g, x in enumerate(arrs):
            st = 0.0 if g == on else float(seen.staleness(g))
            out.append(Weigh(p.big_w if pending(x, t, edges[g], p.big_look) else 1.0, freshness(st, p), self.taste[g], st,
                             seen.last_seen_s(g)))
        self.now, self.at = out, t
        return out

    def why(self, g1: int, g2: int, base_why: str = "") -> dict:
        """The driver of a jump from game g1 to game g2 by the latest weights: {"driver": letter, "director": words}."""
        if not self.now:
            letter = "c" if "better play" in base_why else "b"
            return {"driver": letter, "director": f"{letter}: {GOALS[letter]}"}
        letter = driver(self.now[g1], self.now[g2], base_why)
        return {"driver": letter, "director": driver_text(letter, self.now[g2], self.leagues[g2])}
