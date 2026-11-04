"""Hot standby: what the hidden stick should be playing, so the next switch is a 1-2s input flip.

With two sticks, the one you're not watching can be tuned ahead of time. A right guess turns the
next switch from a 5-10s tune (Prime more) into a 1-2s flip; a wrong guess costs nothing you can
see. So the options are ranked by what the next switch most likely needs, highest class first:

  a) HOME    we're on a break visit (or away from your focus game): keep YOUR game live on the
             hidden stick, so going back is a flip at the exact moment its play resumes.
  b) REPLAY  a catch-up replay is queued and the game on screen is about to break: load that
             channel, rewind to the play and hold it there, so the replay starts the instant we flip.
  b2) BREAK  the game on screen is about to go to a commercial (redzone/scoring/breaks.py: a
             punt or score, an owed NBA/college/NHL TV timeout, the end of a quarter): hold where
             the decider will spend that break, so the break visit is a flip.
  c) MOMENT  a big moment is about to start (4th & goal, power play, bases loaded late, final
             minute of a one-possession game), crunch time is coming, or it's your team (unless it's
             in a lull 20+ points behind the best game) — never in garbage time, your team's included.
  d) BEST    the best other live game. The game we just left counts extra: going back is common.

Above all of them, NEXT is the decider's own word, not a guess: a switch it already wants and only
waits on a safe moment for (Decision.want), e.g. the trip home at the end of a break visit.

Within a class, value = excitement score + the seconds a preload would save (a Prime game, slow
to tune, is worth holding over a YouTube TV game a few points better).

Hysteresis (Planner): a re-tune leaves the hidden stick useless for 5-10s, so it keeps what it has
for at least two_stick.hold_s and then only moves for a higher class or a clearly better game
(two_stick.min_gain points). (a) and (b) can't wait: they take over at once.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from redzone import config
from redzone.models import ByLeague, GameState, League, Phase, pro_period

NEXT, HOME, REPLAY, BREAK, MOMENT, BEST = -1, 0, 1, 2, 3, 4
KIND = {NEXT: "next", HOME: "home", REPLAY: "replay", BREAK: "break", MOMENT: "moment", BEST: "best"}
RETURN_BONUS = 10.0   # the decider goes back to the game it left over one up to switch_margin better


@dataclass
class Option:
    game_id: str
    target: str
    cls: int
    value: float                  # compared within a class: score points + seconds a preload saves
    why: str                      # plain English, for the phone
    back_s: float | None = None   # REPLAY: rewind this far from the live edge
    replay_id: str | None = None  # REPLAY: which missed play

    @property
    def kind(self) -> str:
        return KIND[self.cls]


# --- what kind of game is it right now ----------------------------------------------------------

# Crunch time coming: (final period, seconds left at or under, max margin). Overtime counts if close.
_CRUNCH = ByLeague({League.NFL: (4, 420, 8), League.NBA: (4, 360, 8), League.NHL: (3, 480, 1)})


def crunch_soon(g: GameState) -> bool:
    """The last minutes of a close game are here, or a few minutes off: the decider will want it."""
    d = g.score_diff
    if g.phase != Phase.LIVE or d is None:
        return False
    p = pro_period(g.league, g.period, g.clock_s)  # men's college halves read as NBA quarters
    if g.league == League.MLB:
        return p >= 8 and abs(d) <= 2
    last, clock, margin = _CRUNCH[g.league]
    if abs(d) > margin:
        return False
    return p > last or (p == last and g.clock_s is not None and g.clock_s <= clock)


def break_soon(g: GameState | None, s=None) -> bool:
    """Will the game on screen show a break within about a minute?

    The data runs ~45s ahead of your screen, so a break in the data IS a break coming to your
    screen. Otherwise the clock says when a quarter, half or period is about to end, or the
    two-minute warning; in baseball, two outs means the half-inning may end on any pitch."""
    if s is not None and s.is_break:
        return True
    if g is None or g.phase != Phase.LIVE:
        return False
    if g.league == League.MLB:
        return (g.situation.outs or 0) >= 2
    p, clk = pro_period(g.league, g.period, g.clock_s), g.clock_s
    if clk is None:
        return False
    if g.league.base == League.NFL:
        return (p in (1, 2, 3) and clk <= 60) or (p in (2, 4) and 120 < clk <= 165)
    if g.league.base == League.NBA:
        return p in (1, 2, 3) and clk <= 60
    return p in (1, 2) and clk <= 90   # NHL intermission


def likely_replay(queue, now: float, max_age: float):
    """The queued replay the decider will most likely play next: the most valuable, discounted
    for age the way the decider does. A wrong guess only means a normal tune when it comes."""
    pool = [m for m in queue if not m.replayed and now - m.t <= max_age]
    return max(pool, key=lambda m: m.value * (1.0 - 0.5 * min(1.0, (now - m.t) / max_age)), default=None)


YOUR_TEAM = "your team"


def _moment(s, g: GameState | None, my_teams: dict) -> str:
    """Why this game is likely needed soon (class c), or "" if it isn't."""
    if getattr(s, "garbage", False):
        return ""   # decided game: not even your team's earns the standby slot
    if s.pre_moment:
        return f"about to happen: {s.pre_moment}"
    if g is None:
        return ""
    if crunch_soon(g):
        return "crunch time coming"
    if g.involves(my_teams.get(g.league, set())):
        return YOUR_TEAM
    return ""


# --- ranking ------------------------------------------------------------------------------------

def options(cands: dict, *, current: str | None, home: str | None = None, back_to: str | None = None,
            muted=frozenset(), games: dict | None = None, my_teams: dict | None = None,
            replay: Option | None = None, tunable: Callable[[str], bool] = lambda t: True,
            save_s: Callable[[str], float] = lambda t: 0.0, return_bonus: float = RETURN_BONUS,
            want: str | None = None, brk: str | None = None, brk_why: str = "",
            replay_sure: bool = False) -> list[Option]:
    """Every game the hidden stick could usefully hold, best first.

    Never: what's on screen, a muted game, a stale feed, a channel we can't tune (no link, app not
    installed, just failed). Games on a break are skipped (nobody goes there until it resumes),
    except your game in (a): its break is exactly why we're away. Your team only counts as (c)
    while it's within reach of the best game: in a lull 20+ points behind, it's not where we go next.

    brk: where the decider will spend the break the game on screen is about to take (BREAK).
    replay_sure: the replay is the decider's own pick for that break, not a guess: keep it."""
    games, my_teams = games or {}, my_teams or {}
    out = [replay] if replay is not None and tunable(replay.target) else []
    ok = {gid: x for gid, x in cands.items()
          if gid != current and gid not in muted and x.target and tunable(x.target)}
    top = max((x.scored.score for gid, x in ok.items() if gid != home and not x.stale and not x.scored.is_break),
              default=0.0)
    for gid, x in ok.items():
        s = x.scored
        if gid == want:
            # The decider already picked it and only waits on a safe moment: not a guess.
            out.append(Option(gid, x.target, NEXT, s.score, "next up — waiting on a safe moment to switch"))
            continue
        if gid == home:
            out.append(Option(gid, x.target, HOME, s.score, "your game — back the moment its break ends"))
            continue
        # Crunch time and your team late and close are where the decider goes even mid-stoppage
        # (a timeout is the way in): keep them eligible through their breaks.
        hot = bool(s.crunch or (getattr(x, "mine_late", False) and not s.garbage))
        if x.stale or (s.is_break and not hot):
            continue
        if gid == brk:
            out.append(Option(gid, x.target, BREAK, s.score, f"break coming ({brk_why}): spend it here"))
            continue
        value = s.score + save_s(x.target) + (return_bonus if gid == back_to else 0.0)
        why = _moment(s, games.get(gid), my_teams)
        if why == YOUR_TEAM and s.score < top - 2 * return_bonus and not getattr(x, "mine_late", False):
            why = ""   # (late and close, your team is the decider's hard tier: never a lull)
        if not why and hot:
            why = "crunch time" if s.crunch else "your team, late and close"
        if why:
            out.append(Option(gid, x.target, MOMENT, value, why))
        else:
            lead = "the game you just left" if gid == back_to else "next best"
            out.append(Option(gid, x.target, BEST, value, f"{lead}: {s.reason}"))
    if replay is not None and replay.value < top and not replay_sure:
        # The decider only spends a break on a replay that beats the best live game there
        # (Decider._replay_option): otherwise the break goes to that game, so cue it instead.
        out = [o for o in out if o is not replay]
    out.sort(key=lambda o: (o.cls, -o.value))
    return out


class Planner:
    """Decides WHEN to re-tune the hidden stick; options() decides what would be best."""

    def __init__(self, cfg, clock=time.time):
        self.cfg = cfg
        self._clock = clock
        self._held: tuple[str | None, str | None] = (None, None)
        self._since = 0.0
        self.last: list[Option] = []   # latest ranking, for the phone

    def plan(self, opts: list[Option], standby: tuple[str | None, str | None]) -> Option | None:
        """What to preload now, or None to leave the hidden stick alone.

        standby is what the hidden stick has: (target, None) live, (target, replay_id) parked on a
        replay, (None, None) nothing usable."""
        now = self._clock()
        if standby != self._held:
            # It changed (our preload, a switch, a flip): the hold starts now.
            self._held, self._since = standby, now
        self.last = opts
        if not opts:
            return None
        want = opts[0]
        target, parked = standby
        if (want.target, want.replay_id) == (target, parked):
            return None
        if want.cls in (NEXT, HOME, REPLAY, BREAK):
            # A switch already decided, your game's return, a replay or a break about to start: can't wait.
            return want
        # Parked replay footage is no use for a live switch.
        have = None if parked else next((o for o in opts if o.target == target and o.replay_id is None), None)
        if have is None:
            return want
        gain = float(self.cfg.get("two_stick.min_gain", 8))
        if now - self._since < float(self.cfg.get("two_stick.hold_s", 20)):
            # Churn guard: a re-tune blinds the hidden stick for 5-10s. Only a big moment about to
            # start (it's where the decider chases next) or a much better game moves it this soon.
            soon = want.cls == MOMENT and want.why.startswith("about to happen") and have.cls > MOMENT
            return want if soon or want.value >= have.value + 2 * gain else None
        if want.cls < have.cls or want.value >= have.value + gain:
            return want
        return None


class ShadowStick:
    """The hidden stick, modeled, for replays and backtests: what a preload would have ready when the
    decider switches. A tune takes tune_s; after every switch the stick we left holds the game we left
    (control re-tunes it in the background: a stick that loses the TV input pauses its app).

    Hit rate = switches that landed on a stick already playing the destination (a 1-3s flip)."""

    def __init__(self, tune_s: float | None = None, keep_s: float | None = None,
                 app_s: dict[str, float] | None = None, flip_s: float | None = None, cfg=None):
        def knob(value, key):   # two_stick.shadow.* from cfg, else default_config.yaml
            if value is not None:
                return float(value)
            dotted = f"two_stick.shadow.{key}"
            return float(cfg.get(dotted, config.default(dotted)) if cfg is not None else config.default(dotted))

        self.FLIP_S = knob(flip_s, "flip_s")   # an input flip to a ready stick
        self.tune_s = knob(tune_s, "tune_s")
        # Per-app tune seconds (Prime ~30 s, YouTube TV ~5 s): a model where every tune is 7 s can't
        # see what a Prime preload is worth.
        self.app_s = dict(app_s or {})
        # A Prime game we leave keeps playing live on its stick (control._kept_playing): ready again
        # once two playback reads confirm it, not after a re-tune.
        self.keep_s = knob(keep_s, "keep_s")
        self.target: str | None = None
        self.replay_id: str | None = None
        self.ready_at = 0.0
        self.preloads = 0
        self.hits = 0
        self.loading = 0   # right target, still tuning: faster than cold, not yet a clean flip
        self.misses: list[tuple[float, str | None, str | None]] = []   # (when, wanted, had)
        self.breaks = 0       # switches that spent a break somewhere else (visit, catch-up, chase)
        self.break_hits = 0   # ... onto a stick already playing it
        self.by_app: dict[str, list[float]] = {}   # app -> seconds to picture, per switch

    def tune_for(self, target: str | None) -> float:
        app = (target or "").split(":", 1)[0]
        return float(self.app_s.get(app, self.tune_s))

    @property
    def standby(self) -> tuple[str | None, str | None]:
        return (self.target, self.replay_id)

    def preload(self, target: str | None, replay_id: str | None, now: float) -> None:
        if not target or (target, replay_id) == self.standby:
            return
        self.target, self.replay_id, self.ready_at = target, replay_id, now + self.tune_for(target)
        self.preloads += 1

    def switch(self, to: str | None, replay_id: str | None, left: str | None, now: float,
               brk: bool = False) -> bool:
        hit = bool(to) and (to, replay_id) == self.standby and now >= self.ready_at
        self.breaks += brk
        self.break_hits += brk and hit
        if hit:
            self.hits += 1
            secs = self.FLIP_S
        else:
            if to and (to, replay_id) == self.standby:
                self.loading += 1
                secs = max(self.FLIP_S, self.ready_at - now)
            else:
                secs = self.tune_for(to)   # tuned on screen, cold
            self.misses.append((now, to, self.target))
        if to:
            self.by_app.setdefault(to.split(":", 1)[0], []).append(secs)
        kept = bool(left) and left.startswith("prime:")
        self.target, self.replay_id, self.ready_at = left, None, now + (self.keep_s if kept else self.tune_for(left))
        return hit

    def card(self) -> dict:
        n = self.hits + len(self.misses)
        return {"switches": n, "hits": self.hits, "hit_rate": round(100 * self.hits / max(1, n), 1),
                "still_loading": self.loading, "preloads": self.preloads,
                "breaks_filled": f"{self.break_hits}/{self.breaks}",
                "break_fill_rate": round(100 * self.break_hits / max(1, self.breaks), 1),
                "seconds_to_picture": {a: {"switches": len(v), "mean_s": round(sum(v) / len(v), 1),
                                           "instant": sum(x <= self.FLIP_S for x in v)}
                                       for a, v in sorted(self.by_app.items()) if v}}
