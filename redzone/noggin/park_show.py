"""PARK show: the in-app controller of the two-stick show, one second at a time (integration plan unit 8).

PARK = the hidden stick is tuned to the next game and paused on a dead-ball frame; a jump = resume it, flip the TV
input, pause the stick that was left. Each second `Show.tick(t)`:

  1. takes each game's new facts (`FactsFeed`: `park_facts.GameFacts` built from the app's own ESPN summaries),
  2. cuts each game at EXACTLY the padding the guard will enforce for the stream it is on (`_repad`: the small one
     only where that stream's tune was read, `park_timing`),
  3. steps the planner one second (`park_core.run` with `park_plan.Planner`) for stay / jump / park game g,
  4. checks a jump once more with the hard guard (`park_facts.refusal`) before any key goes out, and
  5. carries it out through a RIG: the app's `Controller.park_*` moves (`redzone/park_run.ControllerRig`) or the
     replay's fake sticks (`scripts/park_replay.py`).

Never traded for pace: no leave inside a padded high-leverage span, no leave or landing outside a padded legal window,
nothing shown more than the lateness cap behind live; a failed step or a missing read-back = no jump, the picture
that is up stays up and one line says why; a sound stop is final. Every jump carries a warrant worked out before it
(Nick, Nov 10: "If we jump the warrant has to exist"); pace is a soft pull (the jump bonus), never a rule.

Moved from the local show runner (tools/park_live `Show`, `Timing`, `LiveNight`, `LiveFeed`), which ran on the real
TV on Nov 10 and found the planning lessons this file keeps: a stream already on its game is not re-tuned; the hidden
stream does not wait on a game in a long break while another is in play; a new tune must beat the best plan without
one; a stream's place is believed only inside what a tune can leave; the plan uses the step times the sticks took.

Pure: no device, no network, no file. Imported only with `park.enabled` on. Numbers are fields of `ShowParams`
(`park.show.<name>`; rule K2). Basketball, and football when `park.leagues` lists it (park_facts reads both).

Why: the show that worked on the TV lived in a local tool with its own loop; the app must run the same show itself.
"""
from __future__ import annotations

import asyncio
import bisect
import math
from collections.abc import Callable
from dataclasses import dataclass, fields

from redzone.grading import RESUME_LEAD, is_action, raw_type
from redzone.models import League
from redzone.noggin import park_core
from redzone.noggin.park_core import PARK_PARAMS, Arr, Night, Setting, State, Stats
from redzone.noggin.park_facts import FACTS, GRID, FactsParams, GameFacts, merge_eyes, pad_arr, refusal, shift
from redzone.noggin.park_plan import NO_TUNE, FAR, FRESH_BEHIND, Plan, Planner
from redzone.noggin.park_timing import TIMING, ErrorParts, GameTiming, TimingParams
from redzone.sources import espn

NEVER, ALWAYS = -10 ** 9, 10 ** 9
PLAIN = {"no_leave_reported": "no safe exit yet", "screen_mid_play": "no safe exit yet", "min_stay": "nothing shown here yet",
         "screen_high_leverage": "a big moment is on", "no_landing_reported": "no safe place to join the other game yet",
         "landing_not_dead": "no safe place to join the other game yet", "late_cap": "the other game is too far behind live",
         "loading": "the other stick is not ready", "plan_other_game": "the plan goes to a different game",
         "plan_stays": "the plan is to stay", "plan_waits": "waiting for the planned second", "flip_in_progress": "a jump is under way",
         "other_game_parked": "the other stick is on a different game", "nothing_parked": "nothing is parked yet",
         "past_reach": "the other game is too far behind live", "landing_long_wait": "no safe place to join the other game yet"}


@dataclass(frozen=True)
class ShowParams:
    """The show's numbers (seconds unless named otherwise). Defaults are what the local show runner ended the night of
    Nov 10 on (its READY-B state): measured on the real sticks and TV, or picked in its replays."""
    show_delay: int = 35            # the whole show runs this far behind each stream's live edge: the feed is then complete for
    #                                 what is on screen, and the per-tune read (which steps the stream back) costs nothing extra
    bonus: float = 3.0              # the planner's jump bonus: the soft pull toward pace (picked in replay; never a rule)
    min_look: float = 3.0           # seconds of play a look must show before the next jump
    warrant: str = "need"           # need: no jump without a warrant | note: only write it down | off
    crunch: bool = True             # in crunch time the best game is the anchor: the others only fill its stoppages
    stops: bool = True              # trust an open stoppage for the shortest time its kind has lasted (`stop_min`)
    unseen: float = 1.7             # a resumed stick plays this long before its picture is up
    jump_real: float = 3.0          # a jump on the real TV: 2.4 to 3.5 s from its first key to the new picture (ledger L45)
    land_wait: int = 20             # no landing more than this before a play start the feed has reported
    dead_w: float = 0.0             # brain points a second of dead air on screen costs the plan (0.05 cost jumps in replay)
    stale: int = 99999              # a held stream this far behind that no jump is planned onto goes back to live (off)
    back_spots: int = 2             # dead balls already gone by, per game, that a tune may be sent back to
    roll_same: bool = True          # a stream held early in a long stoppage may play on to just before its play
    long_break: int = 90            # stopped this long at the feed's edge with no restart = a long break (halftime, a delay)
    wide_pad: int = 10              # a game cut at more than this (no K) can only be joined at its longest stoppages...
    wide_prospect: float = 0.3      # ...so the hidden stream waits on it only this readily
    tune_stick: float = 1.0         # a new tune must be worth this many jump bonuses more than the best plan without one
    resume_lead: float = 0.3        # the resume key goes out this long before the flip command
    confirm: int = 2                # a park is read back about 2 s after the freeze: no jump onto it before
    hold_off: int = 4               # the stream left: first second seen on a return, past the leave
    read: float = 10.5              # the per-tune read gesture, first key to playing again (tunes of 13-19 s with it)
    step: float = 15.0              # one LEFT step back is exactly this
    read_sat: float = 8.0           # of the read, the stream sits paused about this long
    late_cap: float = 145.0         # Nick's lateness cap: nothing comes up further behind the stream's live edge
    jump_slack: int = 1             # a real jump can take a second more than planned: the cap allows for it
    pause_reach: float = 180.0      # a paused stream may sit at most this far behind live: the longest hold whose place was
    #                                 read back after its resume is 180 s (4 pauses of 20-180 s on one channel, ledger L74)
    max_park: int = 180             # park.max_park_s: a stream held paused longer than this is not jumped onto; it is tuned afresh
    hold_ok: float = 1.0            # after a resume the stream's place is read again: within this of where it was = it HELD
    hold_drift: float = 2.0         # ...more than this off = it DRIFTED: unread at once, no cut on it until it is read again
    lag_sane: int = 60              # after a tune a stream sits where the read put it: a place more than this off is a bad reading
    lag_read_max: float = 300.0     # an adopted stream whose place was read is believed up to this far behind
    lag_early: float = 10.0         # ...and no place more than this ahead of the stream's own live edge
    refresh: int = 15               # a tune of the game a stream already plays: only once it is this far behind a fresh tune
    cool: int = 10                  # no second try this long after a jump step failed
    tune_retry: int = 20            # a game whose tune failed is not tuned again for this long
    keep_margin: int = 5
    stay_say: int = 15              # the same reason for staying is said again after this long
    clip_reach: int = 150           # a must-see up to this far back may be landed before...
    clip_steps_max: int = 9         # ...with at most this many LEFT presses (the rewind held 175 s on the TV)
    crunch_look: int = 90           # crunch time: the feed's last this-many seconds hold a high-leverage play
    feed_lag: float = 8.0           # the feed reports a play about this long after it happens
    poll: float = 2.5               # ...and is read this often
    tune0_code: float = 3.5         # a tune by its direct code, before the read (2.8 s measured)
    tune0_search: float = 19.5      # ...by search
    my_pref: float = 1.25           # Nick's teams: what their games are worth is weighted up (never a Rule #1 pass)

    @classmethod
    def from_config(cls, get: Callable[..., object]) -> "ShowParams":
        """Each field from `park.show.<name>`, or its default. A value of the wrong kind keeps the default.
        [PARK-UNIFY] A number the device steps also use has ONE home, the step's own key (STEP_HOME): the show reads
        `park.steps.resume_lead_s` and `park.steps.left_step_s`; `park.show.<name>` is still read first, as an alias."""
        kw = {}
        for f in fields(cls):
            d = f.default
            if f.name in STEP_HOME:
                v = get(f"park.show.{f.name}", None)
                v = get(STEP_HOME[f.name], d) if v is None else v
            else:
                v = get("park.max_park_s" if f.name == "max_park" else f"park.show.{f.name}", d)   # (the park age cap is ONE key)
            if isinstance(d, bool):
                kw[f.name] = v if isinstance(v, bool) else d
            elif isinstance(d, str):
                kw[f.name] = v if isinstance(v, str) and v in ("need", "note", "off") else d
            else:
                kw[f.name] = type(d)(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else d
        return cls(**kw)

    @property
    def open_hold(self) -> int:
        """A stoppage with no restart reported is trusted only this far short of the feed's edge (the grader's lead)."""
        return int(math.ceil(RESUME_LEAD[League.NBA]))

    def flip_times(self) -> tuple[float, float, int]:
        """The one derivation of a real jump's times from `jump_real` and `resume_lead` (the device rig and the replay's
        realistic rig both use it): (the TV's flip after the resume key, seconds the resumed stick plays unseen before
        its picture is up, the first second of the stream left that a return can show)."""
        flip_s = self.jump_real - self.resume_lead
        unseen_s = flip_s + self.resume_lead - 0.5
        return flip_s, unseen_s, int(math.ceil(self.resume_lead + flip_s + unseen_s))

    def read_back(self, steps: int) -> float:
        """How far behind its live edge the read gesture leaves a stream: `steps` LEFT steps plus the seconds it sat
        paused for the read (0 with no read). The one formula for the device rig, the replay rig and the planner."""
        return self.step * steps + self.read_sat if steps else 0.0

    def steps_for(self, reads: bool) -> int:
        """LEFT presses of the read gesture after every tune: enough to sit `show_delay` behind live, one at least for a read."""
        d = self.show_delay
        return max(1 if reads else 0, int(math.ceil((d - self.read_sat) / self.step)) if d > 0 else 0)


SHOW = ShowParams()
# [PARK-UNIFY] show numbers whose one home is a device step's key (`park.show.<name>` stays readable as an alias)
STEP_HOME = {"resume_lead": "park.steps.resume_lead_s", "step": "park.steps.left_step_s"}
HOLD_AT = (10, 30, 60, 120)        # seconds after a resume at which the stream on screen has its place read again (while it stays up)


# ---------------------------------------------------------------------------------------------- the football slider
SLIDER_RANGE = 16.0         # at the ends football's value is weighted x16 (100) or x1/16 (0) against the others; 50 = x1.
#                             The ends are weights, not anchors (coordinator, Nov 11: "others backfill in" at 100)


def football_weight(s: float) -> float:
    """Football's value weight at slider position s (0-100): SLIDER_RANGE ** ((s - 50) / 50), smooth and symmetric in
    log: 0 -> 1/16, 25 -> 1/4, 50 -> 1, 75 -> 4, 100 -> 16."""
    s = min(100.0, max(0.0, float(s)))
    return SLIDER_RANGE ** ((s - 50.0) / 50.0)


# ---------------------------------------------------------------------------------------------- timing
class ShowTiming:
    """Each game's clock as the show uses it: `park_timing.GameTiming` per game (its K or none), whether tunes are
    read, the show's own delay. pad(g, read_ok) is the padding the guard enforces for game g on a stream whose tune
    was read (or was not)."""

    def __init__(self, games: list[GameTiming], *, delay: int = 0, read: bool = True, eyes: bool = False,
                 params: TimingParams = TIMING):
        self.g, self.delay, self.read, self.eyes, self.p = games, int(delay), read, eyes, params

    def known(self, g: int) -> bool:
        return self.g[g].known

    def reads(self, g: int) -> bool:
        """Is each tune of this game read (where the stick sits on YouTube's clock)? Only a game with a K gains by it."""
        return self.read and self.known(g)

    def errs(self, g: int, read_ok: bool = True) -> ErrorParts:
        return self.g[g].errors(self.read and read_ok)

    def _grid(self, errs: ErrorParts) -> int:
        """The padding in whole grid seconds, never rounded DOWN (4 x 1.62 s = 6.48 s is 7, not 6). The grid's own
        slack (flags read once a second, times and positions cut to whole seconds) is cut into the arrays apart: GRID."""
        return max(1, int(math.ceil(errs.padding(self.p.pad_k) - 1e-9)))

    def pad(self, g: int, read_ok: bool = True) -> int:
        return self._grid(self.errs(g, read_ok))

    def eyes_pad(self, read_ok: bool = True) -> int:
        """A stoppage edge the watcher saw is on YouTube's clock, like a read stream: no K and no feed stamp in it."""
        return self._grid(ErrorParts(self.p.read_sd if read_ok else self.p.tune_sd, self.p.eyes_sd, 0.0, self.p.key_sd))

    def pad0(self) -> int:
        pads = [self.pad(g) for g in range(len(self.g))]
        return min(pads + ([self.eyes_pad()] if self.eyes and any(self.known(g) for g in range(len(self.g))) else []))

    def feed_delay(self, g: int) -> float:
        """Believed seconds from a feed stamp to the frame on a stick at its live edge."""
        return self.g[g].air_delay()

    @classmethod
    def case(cls, name: str, n: int, *, league: str = "nba", delay: int = 0, k_sd: float | None = None,
             params: TimingParams = TIMING) -> "ShowTiming":
        """The replay's timing cases: tonight (every game has a K and a read per tune), nok (no K: the wide prior)."""
        from redzone.noggin.park_timing import GameK
        if name == "nok":
            return cls([GameTiming(league, None, params) for _ in range(n)], delay=delay, read=False, params=params)
        sd = params.k_sd_min if k_sd is None else k_sd
        return cls([GameTiming(league, GameK(0.0, sd), params) for _ in range(n)], delay=delay, params=params)


def engine_setting(pad0: int, know: float, *, late_cap: float, tune_s: float = 3.0, min_live: float = 3.0,
                   land_wait: float = 999.0, flip: float = 3.0, pause_reach: float = SHOW.pause_reach,
                   any_shot: bool = False) -> Setting:
    """park_core's setting for the show: tune-and-pause, ping-pong, ONE padding (the smallest game's; the others are
    cut into their arrays) and no error drawn inside it: it runs on what is BELIEVED.

    Where a cut may be made is the frozen grader's dead ball and nothing else (`grading.dead_at`: a stoppage, or the 4 s
    after a SCORING play), for leaves and for joins. The simulator's "any-shot rule" (4 s after ANY shot, made or
    missed, counted as a legal point) is OUTSIDE Rule #1: Nick's ruling of Nov 9 is made baskets only, and the grader
    counts a join after a miss as mid-play and gives a leave no shot window at all. So the app never sets it; `any_shot`
    exists only for the replay's labelled what-if."""
    return Setting(route="tune", pause_holds=True, ping_pong=True, land_wait_max=float(land_wait), max_behind=float(pause_reach),
                   shots=bool(any_shot), feed_lead=float(know), tune_s=float(tune_s), late_cap=float(late_cap), min_live_s=float(min_live),
                   flip=float(flip), pos_sd=0.0, delay_sd=0.0, stamp_sd=0.0, key_sd=0.0, pad_k=0.0, guard=float(pad0))


# ---------------------------------------------------------------------------------------------- the night as it comes in
def blank(end: float) -> dict:
    """A game the feed has said nothing about yet."""
    return {"gid": "", "t0": end, "t1": end, "dead": [], "brk": [], "shots": [], "big": [], "hl": [], "lev": [], "v2": []}


def clip(g: dict, lo: float) -> dict:
    """Nothing before the grid starts (the arrays have no room for it)."""
    if g["t0"] >= lo:
        return g
    keep = [(max(a, lo), b, k) for (a, b), k in zip(g["dead"], g["brk"]) if b > lo]
    return {**g, "t0": lo, "dead": [(a, b) for a, b, _ in keep], "brk": [k for _, _, k in keep],
            "shots": [x for x in g["shots"] if x >= lo], "hl": [(max(a, lo), b) for a, b in g["hl"] if b > lo],
            "big": [x for x in g["big"] if x[0] >= lo], "v2": [x for x in g["v2"] if x[0] >= lo]}


def feed_stop_at(g: dict, at: float) -> tuple | None:
    """The feed's stoppage that covers second `at` (absolute): (start, end, is a break), or None."""
    for (a, b), k in zip(reversed(g["dead"]), reversed(g["brk"])):
        if a <= at:
            return (a, b, k) if b > at else None
    return None


class LiveNight(Night):
    """A night whose games are filled in as the feed reports them. The list `A` is the one the engine and the planner
    read; a game's entry is replaced in place, so the planner keeps its plan."""

    def __init__(self, n_games: int, base: int, n: int, span: tuple, s: Setting, grid: tuple = GRID):
        self.games = [blank(base + n) for _ in range(n_games)]
        self.base, self.n, self.span, self.s, self.grid = base, n, span, s, tuple(grid)
        self.big_total = self.v2_total = 0.0
        self.A = [Arr(g, base, n, s.shots, s.value, s.live_w) for g in self.games]
        self._arrs = {(s.shots, s.value, s.live_w): self.A}

    def put(self, k: int, g: dict, extra: int = 0, eyes: tuple | None = None) -> None:
        s = self.s
        self.games[k] = g
        self.A[k] = pad_arr(Arr(g, self.base, self.n, s.shots, s.value, s.live_w), extra, eyes, self.grid)


def line_of(league: League, p: dict) -> dict:
    """Period, clock and score of one feed row, for the Show tab (public numbers only)."""
    per = espn._int(espn._get(p, "period", "number")) or 0
    regs = 2 if league.value == "cbb" else 4
    name = ("OT" if per == regs + 1 else f"{per - regs}OT") if per > regs else {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}.get(per)
    hs, as_ = espn._int(p.get("homeScore")), espn._int(p.get("awayScore"))
    return {"period": name, "clock": espn._get(p, "clock", "displayValue"), "score": None if hs is None or as_ is None else f"{as_}-{hs}"}


class FactsFeed:
    """Each game's facts for the show, from its ESPN summaries as they arrive (`offer`). A game's times are moved by its
    own delay (its K, the live lag and the show's delay), so second t of the grid is what a stream at the show's edge
    shows at wall second base + t. `stop_min` = {"<league>|<row type>": seconds}: the shortest dead time such a row
    has been followed by; an open stoppage is trusted that far (its floor). The same class feeds the app (summaries
    the engine polled) and the replay (a recorded game cut off at the rows a live feed would have had)."""

    def __init__(self, games: list[tuple], night: LiveNight, shifts: list[float], leads: list[float], *,
                 stop_min: dict | None = None, params: FactsParams = FACTS, keep_shots: bool = False):
        self.games = [(League(lg), str(gid)) for lg, gid in games]
        self.night, self.shifts, self.leads, self.stop_min = night, list(shifts), list(leads), stop_min or {}
        self.keep_shots = keep_shots       # False (the app, always): the show's arrays are handed NO shots, so nothing but the
        #                                    grader's dead time can make a second legal (only the replay's what-if keeps them)
        self.fx = [GameFacts(lg, gid, params) for lg, gid in self.games]
        self.fresh: dict = {}
        self.key = [None] * len(games)
        self.at = [NEVER] * len(games)
        self.errors = 0
        self.seen: list[list[int]] = [[] for _ in games]         # per game, per row as polled: the grid second it was first seen

    def kept(self, g: int) -> dict:
        """Game g's play list as polled, for regrading a run afterwards: the rows as the feed gave them (public
        play-by-play), the stamps the show used, how many late-edit rows were put back, and when each row was first seen."""
        fx, base = self.fx[g], self.night.base
        return {"league": self.games[g][0].value, "game_id": self.games[g][1], "shift_s": round(self.shifts[g], 3), "base": base,
                "rows": list(fx.raw), "stamps": [round(w, 3) for w in fx.wall], "restamped": fx.restamped,
                "first_seen": [base + t for t in self.seen[g]]}

    def _floor(self, g: int, since: float) -> float | None:
        """The feed time an open stoppage that began at `since` is surely dead until: each of its rows has a shortest
        dead time after it."""
        fx, lg, floor = self.fx[g], self.games[g][0], None
        k = len(fx.plays) - 1
        lo = since - 1.0
        if fx.football:                                          # [PARK-NFL-LAG] football's table is measured from the action
            a = k                                                # row before the stoppage (the gap's start), so its rows are
            while a >= 0 and not is_action(lg, fx.plays[a]):     # looked at from there
                a -= 1
            lo = min(lo, fx.wall[a] - 1.0) if a >= 0 else lo
        while k >= 0 and fx.wall[k] > lo:
            m = self.stop_min.get(f"{lg.value}|{raw_type(fx.plays[k])}")
            if m:
                base = fx.wall[k]
                if fx.football:
                    a = k
                    while a >= 0 and not is_action(lg, fx.plays[a]):
                        a -= 1
                    base = fx.wall[a] if a >= 0 else base     # the gap's own start, as the table measured it
                floor = max(floor or 0.0, base + float(m))
            k -= 1
        return floor

    def _stoppage(self, g: int, since: float) -> dict | None:
        """The open stoppage as the stoppage model reads one: every row of it the feed has shown, as (seconds after its
        first row, type text, play text), its period, and the grid second of its first row."""
        fx = self.fx[g]
        k0 = len(fx.plays) - 1
        while k0 > 0 and fx.wall[k0 - 1] > since - 1.0:
            k0 -= 1
        if k0 < 0:
            return None
        rows = [(fx.wall[k] - fx.wall[k0], str(espn._get(fx.plays[k], "type", "text") or ""), str(fx.plays[k].get("text") or ""))
                for k in range(k0, len(fx.plays))]
        return {"rows": rows, "period": espn._int(espn._get(fx.plays[k0], "period", "number")),
                "first": int(fx.wall[k0] + self.shifts[g] - self.night.base)}

    def offer(self, g: int, summary: dict, at: int, final: bool | None = None) -> bool:
        """A summary of game g read at grid second `at`. True when it changed that game's facts."""
        try:
            self.fx[g].edge_hint = self.night.base + at + self.leads[g] - self.shifts[g]
            self.fx[g].update(summary, final)
        except (KeyError, TypeError, ValueError):
            self.errors += 1
            return False
        return self._publish(g, at)

    def lag_s(self, g: int, min_n: int = 5, last: int = 40) -> float | None:
        """[PARK-NFL-LAG] The feed's measured lag for game g: the median, over its newest `last` rows that came after the
        first poll, of (when the row was first seen, `seen`) - its stamp. None before min_n such rows."""
        fx, seen = self.fx[g], self.seen[g]
        if not seen or len(seen) != len(fx.wall):
            return None
        lags = [self.night.base + t - w for t, w in zip(seen, fx.wall) if t > seen[0]][-last:]
        return sorted(lags)[len(lags) // 2] if len(lags) >= min_n else None

    def offer_rows(self, g: int, rows: list, at: int, final: bool = False, data: dict | None = None) -> bool:
        """The same from rows handed over one by one (the replay: `rows` are the game's NEW rows, in feed order)."""
        fx = self.fx[g]
        fx.edge_hint = self.night.base + at + self.leads[g] - self.shifts[g]
        if data is not None and fx.data is None:
            fx.data = data
        for p in rows:
            fx.add(p)
        if final:
            fx.set_final()
        return self._publish(g, at)

    def _publish(self, g: int, at: int) -> bool:
        fx = self.fx[g]
        self.at[g] = at
        seen = self.seen[g]
        del seen[len(fx.raw):]                                   # (the feed rewrote its list: those rows are seen anew)
        seen.extend([at] * (len(fx.raw) - len(seen)))
        if not fx.plays:
            return False
        key = (len(fx.plays), fx.wall[-1], fx.final)
        if key == self.key[g]:
            return False
        self.key[g] = key
        d, base = self.shifts[g], self.night.base
        edge = base + at + self.leads[g] - d                     # the feed time the feed has been read up to
        view, of = fx.planner_view(base + self.night.n - d, edge)
        if not self.keep_shots:
            view["shots"] = []
        op = None
        if of is not None:
            o = fx._open(max(edge, fx.wall[-1]))
            mine = o is not None and of >= o.since - 1.0         # what is not final is the stoppage still open at the last row
            floor = self._floor(g, o.since) if (mine and self.stop_min) else None
            cap = fx.clock_cap_at() if (mine and fx.football) else None     # [PARK-NFL] the play clock's limit, or None
            op = (max(1, int(of + d - base)), None if floor is None else max(1, int(floor + d - base)),
                  self._stoppage(g, o.since) if mine else None, None if cap is None else int(cap + d - base))
        self.fresh[g] = (clip(shift(view, d), base + 1), op)
        return True

    def take(self, t: int) -> list:
        out, self.fresh = [(g, f, of) for g, (f, of) in self.fresh.items()], {}
        return out

    def edge(self, g: int) -> int:
        return self.at[g] + int(self.leads[g])

    def line(self, g: int, p: int) -> dict:
        fx = self.fx[g]
        i = bisect.bisect_right(fx.wall, self.night.base + p - self.shifts[g]) - 1
        return line_of(self.games[g][0], fx.plays[i]) if i >= 0 else {}

    def row_at(self, g: int, p: int) -> dict:
        """The feed row a cut at grid second p of game g relied on: the last row at or before it, with its age there
        (public game data only: the row's type, period and clock)."""
        fx = self.fx[g]
        st = self.night.base + p - self.shifts[g]
        i = bisect.bisect_right(fx.wall, st) - 1
        if i < 0:
            return {}
        r = fx.plays[i]
        return {"type": str(espn._get(r, "type", "text") or "")[:40], "period": espn._int(espn._get(r, "period", "number")),
                "clock": espn._get(r, "clock", "displayValue"), "age_s": round(st - fx.wall[i], 1), "rows_in": len(fx.plays)}

    def health(self, now: int | None = None) -> str:
        if now is None or min(self.at) <= NEVER:
            return "ok" if min(self.at) > NEVER else "no answer yet"
        late = now - min(self.at)
        return "ok" if late < 15 else f"no answer for {late} s"


# ---------------------------------------------------------------------------------------------- the seams (each off by default)
class NoModel:
    """The stoppage model seam, off. A model reader answers the same two questions, with the stage 3 reader's own
    arguments (`surely_left(league, features, elapsed_s, alpha)`, `landing_soon(league, features, horizon_s)`);
    None = "I do not know", and the show then runs exactly as it does without a model. No model file is in the repo."""

    def surely_left(self, league: str, features: dict, elapsed_s: float, alpha: float = 0.01) -> float | None:
        """Seconds of dead time SURELY still to come (at 1 - alpha) in an open stoppage, `elapsed_s` after its first row.
        features: {"rows": [(seconds after the first row, type text, play text), ...], "period", "first"}. The show
        only ever takes it as a LATER floor: it can add trust to an open stoppage, never take the feed's away."""
        return None

    def landing_soon(self, league: str, features: dict, horizon_s: float) -> float | None:
        """The chance (0-1) that a dead stretch long enough to land on begins within horizon_s on a game in play.
        features: {"period", "clock_s", "margin"} as the feed last reported them. It only weights where the hidden
        stream WAITS when nothing can be planned; it never passes a guard."""
        return None


class NoWatcher:
    """The watcher seam, off. A watcher (the hidden Chrome that reads the on-air game clock) gives exact stop and
    restart times and a verdict on each cut:
        step(t, show) -> the games whose spans changed this second
        spans(g)      -> [{"lo", "hi", "stops": [(a, b)], "open": a | None}] in grid seconds: what it saw of game g
        verdict(g, p) -> "ok" | "bad" | "unknown": was grid second p of game g inside a stopped game clock"""

    def step(self, t: int, show) -> set:
        return set()

    def spans(self, g: int) -> list:
        return []

    def verdict(self, g: int, p: int) -> str:
        return "unknown"


class Wants:
    """What the audit's brain would WANT each second, from no more than the feed has reported (park_sim's Foresight):
    only asked for the yes-rate counters; the planner decides."""
    name = "foresight"

    def bind(self, arrs: list, s: Setting) -> None:
        self.A, self.lead = arrs, int(s.feed_lead)

    def score(self, g: int, c: int, t: int, on_screen: bool, back: int = 0) -> float:
        a, P = self.A[g], PARK_PARAMS
        known = min(a.n - 1, t + self.lead)
        return a.ahead(c - back + (0 if on_screen else P.seen_lead_s), min(c + P.run_up_s, known), c) + 0.2 * a.lev[min(max(c, 0), known)]


# ---------------------------------------------------------------------------------------------- the show
class Show:
    """One second at a time: new facts in, one planner second, then the stick moves that second asked for.

    `rig` is the device layer. It holds, per stream k (0 and 1): game[k], paused[k], lag[k] (seconds behind the show's
    edge), frozen[k] (the grid second it is paused on), read[k] (was its tune read); and the steps
        tune(k, game[, steps]) -> (ok, seconds)    pause(k, at) -> (ok, the frozen second)    resume(k) -> (ok, lag)
        jump(k, resume, hold) -> ("ok" | "kept" | "stop", why)    confirming(k)    replace(k, lag)    finish()
    plus what its steps take: unseen_s, pause_lead, tune_lag, steps, hold_off, and whether it can join a stream that
    plays (`join_playing`) or send a tune further back (`deep`). With an `instant` rig every step is done in the same
    second and the simulator's own timings rule; otherwise a step runs beside the clock, the stream it touches is "not
    ready" until the step is confirmed, and the believed position is then read back from the rig."""

    def __init__(self, night, s: Setting, timing: ShowTiming, rig, feed, *, plan: Plan | None = None, names=None, chans=None,
                 leagues=None, keep=None, judge=None, eyes=None, model=None, board=None, say=print, emit=None, tune0=None,
                 prefs=None, params: ShowParams = SHOW, crunch: bool | None = None, clips: bool = False, warrant: str | None = None,
                 no_back=None, depth=None, football_slider: float | None = None, keep_live: bool = False):
        P = self.P = params
        self.depth = dict(depth or {})                           # [PARK-NFL-LAG] game -> seconds deeper its stream is parked (its feed is late)
        if depth is not None:
            rig.depth = self.depth                               # the rig puts each tune of that game this much further back
        self.no_back = frozenset(no_back or ())                 # [park.prime] games whose app cannot be sent back (no rewind)
        self.night, self.s, self.tm, self.rig, self.feed = night, s, timing, rig, feed
        self.A = night.arrs(s.shots, s.value, s.live_w)
        self.N = len(self.A)
        self.pad0 = int(round(s.guard))
        self.pad = [timing.pad(g) for g in range(self.N)]
        self.extra = [max(0, p - self.pad0) for p in self.pad]
        self.eyes_pad, self.delay = timing.eyes_pad(), timing.delay
        self.flip, self.know = max(1, int(round(s.flip))), int(s.feed_lead)
        self.reach = min(int(s.max_behind), int(s.late_cap) - 2 * self.flip) if s.late_cap > 0 else int(s.max_behind)
        timed = not rig.instant
        warrant = P.warrant if warrant is None else warrant
        plan = plan or Plan(jump_bonus=P.bonus)
        self.join_playing = bool(getattr(rig, "join_playing", True))
        deep = bool(getattr(rig, "deep", True))
        self.pl = Planner(plan, show=self, skip=False, confirm=P.confirm if timed else 0,
                          tune_lag=int(math.ceil(rig.tune_lag + (timing.p.tune_sd if rig.steps else 0.0))) if timed else 0,
                          hold_off=rig.hold_off if timed else 0, hold_ready=rig.hold_off + 1 if timed else 1,
                          resume_s=1 if timed else 0, sticky=timed, dead_w=P.dead_w if timed else 0.0,
                          max_wait=int(s.land_wait_max) if s.land_wait_max < 900 else FAR,
                          warrant=warrant if timed else "off", back=P.back_spots if timed and rig.steps and deep else 0,
                          roll_same=P.roll_same and timed,
                          tune_stick=P.tune_stick * plan.jump_bonus if timed and P.tune_stick > 0 else None,
                          join_playing=self.join_playing or not timed)
        self.seen_to = [-1] * self.N                             # the last second of each game that has been on screen
        self.read_hope, self.read_now, self._pad_key = True, [True] * self.N, None     # was the last tune read; each game's stream; what the pads were made on
        self.h_deep = None                                       # the tune in flight was sent back: (the lag it should end at, LEFT presses)
        self.back_tunes = 0
        self.warrants: list = []                                 # one per jump carried out: (warranted, there, here, sooner, why)
        self.brain, self.st, self.S = Wants(), Stats(), None
        self.names = names or [f"game {g + 1}" for g in range(self.N)]
        self.chans = chans or ["" for _ in range(self.N)]
        self.leagues = leagues or [x.league for x in timing.g]
        self.on, self.keep, self.keep_rolls = 0, keep, False      # the stream on screen; the stream never to be re-tuned
        # [pinned anchor] the kept stream is not held paused when it is left: it plays on unseen at a constant lateness and is
        # parked again on a dead ball before the way back (a Prime football game: no re-tune, no back route)
        self.keep_live = bool(keep_live) and keep is not None
        self.judge, self.board, self.say, self.emit = judge, board, say, emit or (lambda row: None)
        self.eyes = eyes                                         # the watcher seam (NoWatcher's shape), or None
        self.model = model                                       # the stoppage model seam (NoModel's shape), or None
        self.raw, self.open = [None] * self.N, [None] * self.N    # each game's facts and its not-final start, from the feed
        self.open_from, self.sure, self.tail = [None] * self.N, [ALWAYS] * self.N, list(self.pad)
        self.fix, self.adv = [NEVER] * self.N, [ALWAYS] * self.N  # the two parts of `sure`: a floor that stands, an edge that moves
        self.h_op = self.j_op = self.j_ctx = None
        self.retune, self.no_jump_until, self.stop = False, -1, ""
        self.block: dict[int, int] = {}                          # game -> the second a failed tune may be tried again
        self.tunes: dict[int, list] = {g: [v] for g, v in (tune0 or {}).items()}
        self.lags: dict[int, list] = {}                          # game -> where its tunes have left a stream, behind the show's edge
        self.fresh = [0.0, 0.0]                                  # each stream's lag right after its last tune
        self.h_t0, self.tunes_sent, self.tunes_skipped, self.refreshed = 0.0, 0, 0, 0
        self.to_judge: list = []                                 # cuts the watcher has still to rule on: [what, game, second, tries]
        self.eyes_says = {"leaves_ok": 0, "leaves_bad": 0, "leaves_unknown": 0, "joins_ok": 0, "joins_bad": 0, "joins_unknown": 0}
        self.prefs, self.crunch, self.anchor_now = prefs or [1.0] * self.N, (P.crunch if crunch is None else crunch), -1
        # [football slider] Nick, Nov 11: "one end is max football plays other sports backfill in / and the reverse". None
        # (no football game, or the park flag off) and 50 change nothing; the ends are value weights (football_weight).
        from redzone.noggin.park_facts import is_football
        self.football = [is_football(lg) for lg in self.leagues]
        self.slider = float(football_slider) if (football_slider is not None and any(self.football)) else None
        if self.slider is not None and self.slider != 50.0:
            w = football_weight(self.slider)
            self.prefs = [p * (w if f else 1.0) for p, f in zip(self.prefs, self.football)]
        self.clips, self.aired, self.clip_tunes = clips, [[] for _ in range(self.N)], 0     # must-see seconds that have been on screen
        self.refused: dict[str, int] = {}
        self.stayed: dict[str, int] = {}
        self.last_ref, self.stay, self.jumps_done, self.last_t = ("", -9), ["", 0, -99], 0, None
        self.looks: list = []                                    # (seconds on the picture, of which play) per look ended
        self.jump_at: list = []                                  # the second of every jump carried out
        self.records: list = []                                  # the decision record of every jump carried out (warrant included)
        self.since, self.parked_at, self.look_live = None, None, 0
        self.paused_since, self.aged, self.longest_hold = None, 0, 0   # the hidden stream's pause: since when; times it hit the cap; the longest
        self.watch = self.look_op = self.reread_op = None        # the look on screen: its hold being graded; a read in flight; a re-read in flight
        self.drifted: set = set()                                # streams whose place moved after a resume: no cut on them until read again
        self.reread_at = 0
        self.holds = {"held": 0, "loose": 0, "drifted": 0, "unread": 0}

    def begin(self, first: int, on: int, a: int) -> State:
        """The state at the start: game `first` is on screen on stream `on`, nothing is held, second a."""
        S = self.S = State()
        import random
        S.rng, S.gd = random.Random(0), [0.0] * self.N
        S.sg, S.sb, S.se, S.s_land, S.s_from, S.s_kind, S.s_live = first, float(self.rig.lag[on]), 0.0, a, a, "park", 0
        S.hg, S.hb, S.he, S.h_ready, S.h_paused, S.h_kind, S.black = -1, 0.0, 0.0, a, False, "park", a
        self.on = on
        if not self.rig.instant and self.tm.reads(first) and not self.rig.read[on]:
            self.read_hope = False                               # the first picture's place was not read either
        return S

    # ---- what the planner asks the show ----------------------------------------------------------------------------
    def tune_cost(self, g: int, default: int) -> int:
        if self.keep is not None and self.keep == 1 - self.on and not self._kept_too_late():
            return NO_TUNE                                           # the hidden stream is the kept one: no tune is on offer
        if self.last_t is not None and self.last_t < self.block.get(g, -1):
            return NO_TUNE                                           # its tune has just failed
        v = self.tunes.get(g)
        return default if not v else int(math.ceil(sorted(v)[int(0.8 * (len(v) - 1) + 0.5)]))     # the slow side of what its tunes have taken

    def _kept_too_late(self) -> bool:
        """[pinned anchor] The kept stream, held while left, has run up to the lateness cap: only then may it be re-tuned
        (a Prime football game: about 100 s with its hold), since it can never catch up any other way."""
        S = self.S
        return bool(self.keep_live and S is not None and S.hg >= 0 and S.hb - S.he >= self.reach - self.P.keep_margin)

    def fresh_gap(self, hidden: bool) -> int:
        """A tune of the game a stream already holds lands where a stream that simply kept playing already is: it is only
        worth planning once the stream has fallen `refresh` behind where its last tune put it (and the simulator's 20 s)."""
        return max(FRESH_BEHIND, int(math.ceil(self.fresh[1 - self.on if hidden else self.on] + self.P.refresh)))

    def tune_lag_of(self, g: int, default: int) -> int:
        v = self.lags.get(g)
        if not v and self.depth.get(g):
            return int(default + math.ceil(self.depth[g]))     # [PARK-NFL-LAG] a deep game's tune lands this much further back
        return default if not v else int(math.ceil(sorted(v)[len(v) // 2] + self.tm.p.tune_sd))

    def limit(self, g: int) -> tuple:
        """(of, fix, adv) for the planner: from second `of` on game g's dead time is not final; such a second p is
        trusted at clock second w only when p <= fix (a floor that stands) or p <= w + adv (the feed's or the watcher's
        edge, which moves on with the clock)."""
        of = self.open_from[g]
        if of is None:
            return None, 0, 0
        return of, self.fix[g] - self.tail[g], self.adv[g] - self.tail[g] - (self.last_t or 0)

    def pref(self, g: int) -> float:
        return self.prefs[g]

    def prospect(self, g: int) -> float:
        """How soon a landing can come on game g (a weight on where the hidden stream waits when the feed's reports
        hold nothing to land on): 0 in a long break (stopped for `long_break` at the feed's edge and no restart
        reported), less than 1 for a game with no K (only its longest stoppages can be joined), 1 for a game in play
        with a K."""
        op, edge = self.open[g], self.feed.edge(g)
        if op is not None and edge < 10 ** 8 and edge - op[0] >= self.P.long_break:
            return 0.0
        base = 1.0 if self.pad[g] <= self.P.wide_pad else self.P.wide_prospect
        if self.model is not None and op is None and 0 <= edge < 10 ** 8:
            x = self.A[g]
            e = min(x.n - 1, edge)
            soon = self.model.landing_soon(self.leagues[g], {"period": (self.feed.line(g, e) or {}).get("period"),
                                                             "clock_s": x.left_s[e], "margin": x.margin[e]}, float(self.P.land_wait))
            if soon is not None:                                 # the model seam: how likely a landing comes soon (never below 0.05:
                base *= min(1.0, max(0.05, float(soon)))         # only a long break takes a game off the list)
        return base

    def anchor(self) -> int:
        """Crunch time: the game that must be on whenever it is in play (the other games only fill its stoppages), or -1.
        A game is in crunch time while the feed's last `crunch_look` seconds hold a high-leverage play (the grader's own
        close-and-late rule, overtime included); of several, the one with the most leverage."""
        if not self.crunch or self.last_t is None:
            return -1
        best, top = -1, -1.0
        for g in range(self.N):
            x = self.A[g]
            e = min(x.n - 1, max(0, min(self.last_t + self.know, self.feed.edge(g))))
            if x.a0 <= self.last_t <= x.a1 and x.hlc[e + 1] - x.hlc[max(0, e - self.P.crunch_look)] > 0 and x.lev[e] > top:
                best, top = g, x.lev[e]
        self.anchor_now = best
        return best

    def unseen(self, g: int) -> list:
        """The seconds of game g's must-see plays, reported by the feed in the last few minutes, that were not on screen."""
        if not self.clips or self.last_t is None:
            return []
        lo, aired = self.last_t - self.P.clip_reach, self.aired[g]
        return sorted(m for m in self.A[g].mom if m >= lo and all(abs(m - a) > 3 for a in aired))

    def clip_lag(self, need: int, g: int = -1) -> tuple | None:
        """A stream has to sit `need` seconds behind the show's edge when its tune is done: (where the read gesture's
        LEFT presses put it, the extra seconds they take, the presses), or None when that is past the rewind's reach.
        A game whose tunes have ended further back than the gesture alone puts them (a channel that runs late) keeps
        that extra."""
        if g in self.no_back:
            return None                                          # [park.prime] no rewind on this game's app: no back route
        rig, P, sd = self.rig, self.P, self.tm.p.tune_sd
        base = max(1, rig.steps)
        usual = int(math.ceil(rig.tune_lag + sd))
        late = max(0, self.tune_lag_of(g, usual) - usual) if g >= 0 else 0
        steps = max(base, int(math.ceil((need - late + self.delay - P.read_sat + sd) / P.step)))
        if steps > P.clip_steps_max:
            return None
        return int(math.ceil(P.read_back(steps) - self.delay + sd)) + late, int(math.ceil(0.6 * (steps - base))), steps

    def clip_steps(self, t: int, game: int) -> tuple | None:
        """The tune being started now, when the plan's spot has already gone by: (the lag it should end at, LEFT presses)."""
        g, spot, _ = self.pl.cmd
        if not (self.clips or self.pl.back) or g != game or spot < 0 or not self.rig.steps:
            return None
        cost = self.tune_cost(game, int(math.ceil(self.s.tune_s)))
        deep = self.clip_lag(t + cost + 2 - spot, game)
        if deep is None or deep[2] <= max(1, self.rig.steps):
            return None
        return deep[0], deep[2]

    def allow(self, t: int, S, out: tuple) -> tuple:
        jump, game, spot, how = out
        hid_kept = self.keep is not None and self.keep == 1 - self.on and not self._kept_too_late()
        hold = (False, S.hg, int(t - S.hb + S.he) if S.h_paused else -1, "")
        if (how == "tune" or (game >= 0 and game != S.hg)) and (hid_kept or t < self.block.get(game, -1)):
            return (False, S.hg, -1, "") if hid_kept else hold   # a kept stream plays on instead
        if hid_kept and not jump and S.hg >= 0 and not self.keep_live:
            if self.keep_rolls or (S.h_paused and S.hb - S.he >= self.reach - self.P.keep_margin):
                self.keep_rolls = True                           # close to the lateness cap and it cannot be re-tuned:
                return False, S.hg, -1, ""                       # from here on it plays on hidden and is joined as it plays
        if jump and (self.on in self.drifted or (1 - self.on) in self.drifted):
            return hold                                          # a place drifted: nothing is cut on it until it is read again
        if not S.h_paused or S.hg < 0:
            self.paused_since = None
        elif self.paused_since is not None:
            age = t - self.paused_since
            self.longest_hold = max(self.longest_hold, age)
            if not self.rig.instant and age > self.P.max_park and how != "tune":
                # held paused past the park age cap (park.max_park_s): no hold that long has had its place read back after
                # the resume, so it is not jumped onto: it is tuned afresh (the kept stream plays on instead)
                self.aged += 1
                self.paused_since = None
                return (False, S.hg, -1, "") if hid_kept or t < self.block.get(S.hg, -1) else (False, S.hg, -1, "tune")
        go = self.pl.go
        if (not self.rig.instant and not jump and not how and S.hg >= 0 and game == S.hg and S.h_paused and t >= S.h_ready
                and S.hb - S.he >= self.P.stale and not (go is not None and go[3] == S.hg and go[6] - t <= 2 * self.pl.p.commit_s)):
            # held and gone stale, and no jump onto it is coming: back to live by a tune (the kept stream plays on instead)
            self.refreshed += 1
            return (False, S.hg, -1, "") if hid_kept or t < self.block.get(S.hg, -1) else (False, S.hg, -1, "tune")
        return out

    def extra_of(self, k: int, g: int) -> int:
        """Extra padding for stream k showing game g: its tune should have been read and was not. (0 whenever the game's
        arrays already carry it, which `_repad` sees to every second: this is the guard's own second look.)"""
        if self.rig.instant or not self.tm.reads(g) or self.rig.read[k]:
            return 0
        return max(0, self.tm.pad(g, False) - self.pad[g])

    def _repad(self) -> None:
        """Each game's padding as the guard enforces it NOW. A game with a K is cut at its small padding only on a stream
        whose tune was read; on a stream that was not read it gets the wide one (and so does a game on neither stream
        while the last tune went unread). The plan and the guard read the same arrays, so the plan never schedules a
        jump the guard must refuse (Nov 10, real sticks: no tune was read, the plan was made at 3 s, the guard held 6 s,
        every planned jump died at the guard and the parked stream went stale)."""
        rig = self.rig
        if rig.instant:
            return
        key = (tuple(rig.game), tuple(rig.read), self.read_hope)
        if key == self._pad_key:
            return
        self._pad_key = key
        for g in range(self.N):
            if not self.tm.reads(g):
                continue
            on = [k for k in (0, 1) if rig.game[k] == g]
            ok = all(rig.read[k] for k in on) if on else self.read_hope
            pad = self.tm.pad(g, ok)
            if pad == self.pad[g] and ok == self.read_now[g]:
                continue
            if pad != self.pad[g]:
                self.say(f"PADDING: {self.names[g]} is now cut at {pad} s (was {self.pad[g]} s): "
                         + ("its stream's place was read." if ok else "its stream's place was NOT read after the tune."))
                self.emit({"ev": "padding", "tick": self.last_t, "game": g, "pad_s": pad, "was_s": self.pad[g], "read": int(ok)})
            self.pad[g], self.read_now[g] = pad, ok
            self.extra[g] = max(0, pad - self.pad0)
            self._rebuild(g)

    def set_k(self, g: int, k) -> bool:
        """[park.prime] Game g's K, learned during the show (a Prime stick's own score-bug reads, `park_prime.game_k`):
        its timing is replaced and its padding made again at the next second. Only a game with no K of its own takes
        one this way, and never a K of another game. True when taken."""
        from redzone.noggin.park_timing import GameTiming
        if k is None or not 0 <= g < self.N or self.tm.g[g].known:
            return False
        old = self.tm.g[g]
        self.tm.g[g] = GameTiming(old.league, k, old.params)
        self._pad_key = None
        self.say(f"K: {self.names[g]} now has its own K from its stick's score bug ({k.k:+.2f} s, sd {k.spread:.2f}, {k.n} reads).")
        self.emit({"ev": "k", "tick": self.last_t, "game": g, "k_s": k.k, "sd_s": round(k.spread, 3), "n": k.n, "source": k.source})
        return True

    # ---- one second ----------------------------------------------------------------------------------------------
    async def tick(self, t: int) -> None:
        S, st, self.last_t = self.S, self.st, t
        if self.since is None:
            self.since = t
        changed = set()
        for g, f, of in self.feed.take(t):
            self.raw[g], self.open[g] = f, of
            changed.add(g)
        if self.eyes is not None:
            changed |= self.eyes.step(t, self)
        for g in changed:
            self._rebuild(g)
        if self.to_judge:
            self._eyes_judge()
        self._collect(t)
        self._hold_watch(t)
        self._repad()
        self._trust()
        if not self.stop:
            if self.j_op is not None:
                S.black = max(S.black, t + 1)                    # a jump is being carried out: no second one
            if self.h_op is not None:
                S.h_ready = max(S.h_ready, t + 1)                # the hidden stream is busy
            snap = {"S": {k: getattr(S, k) for k in State.__slots__ if k != "rng"},
                    "lists": [len(x) for x in (st.hops, st.flips, st.ups, st.visits, st.jumps)], "parks": st.parks,
                    "ints": [st.play_visits, st.mid_entry, st.mid_leave, st.hl_leave, st.walk_entry, st.walk_leave]}
            park_core.run(self.night, self.brain, self.s, t0=t, t1=t + 1, state=S, stats=st, planner=self.pl)
            if st.rolls:
                raise RuntimeError("the planner asked for a skip: that route is off")
            if st.parks > snap["parks"]:
                self.retune = True
            if len(st.hops) > snap["lists"][0]:
                await self._jump(t, snap)
            if self.j_op is None and not self.stop:
                await self._drive(t)
            if t >= S.black:
                p = min(self.night.n - 1, max(0, int(t - S.sb + S.se)))
                self.look_live += 0 if self.A[S.sg].dead[p] else 1
                if p > self.seen_to[S.sg]:
                    self.seen_to[S.sg] = p
                if p in self.A[S.sg].mom:
                    self.aired[S.sg].append(p)                   # a must-see play was on screen
                if self.judge is not None:
                    self.judge.second(self.on, S.sg, t - S.sb + S.se, t)
            self._stayed(t)
        if self.board is not None:
            self.board.write(self, t)

    def _rebuild(self, g: int) -> None:
        if self.raw[g] is None:
            return
        spans = self.eyes.spans(g) if self.eyes is not None else []
        if not spans:
            self.night.put(g, self.raw[g], self.extra[g])
            return
        stops = [x for sp in spans for x in sp["stops"]]
        starts = {a for a, _ in stops} | {sp["open"] for sp in spans if sp["open"] is not None}
        self.night.put(g, merge_eyes(self.raw[g], spans, self.night.base, self.night.base + self.night.n), self.extra[g],
                       (starts, {b for _, b in stops}, max(0, min(self.extra[g], self.tm.eyes_pad(self.read_now[g]) - self.pad0))))

    def _eyes_judge(self, final: bool = False) -> None:
        """Rule #1 measured with nobody watching: was each cut inside a stopped game clock, on YouTube's own time? The
        watcher only knows the game it was watching at that second, and only a stream whose tune was read is on its
        clock: any other cut stays "unknown"."""
        keep = []
        for item in self.to_judge:
            what, g, p, tries, on_clock = item
            v = self.eyes.verdict(g, p) if on_clock else "unknown"
            if v == "unknown" and not final and on_clock and tries < 90:
                item[3] += 1
                keep.append(item)
                continue
            self.eyes_says[f"{what}_{v}"] += 1
            self.emit({"ev": "eyes", "what": what, "game": g, "second": p, "says": v})
            for rec in self.records:                             # the per-jump verdict goes into that jump's decision record
                if rec.get(what[:-1] + "_at") == (g, p):
                    rec.setdefault("watcher", {})[what[:-1]] = v
            if v == "bad":
                self.say(f"WATCHER: a cut was NOT inside a stopped clock ({what[:-1]}, {self.names[g]}).")
                if self.board is not None:
                    self.board.event("alert", level="error", text=f"watcher: a {what[:-1]} was not inside a stopped clock ({self.names[g]})")
        self.to_judge = keep

    def _trust(self) -> None:
        """For each game: the second from which its dead time is not final, and how far that is trusted all the same:
        up to a floor that stands (the shortest such a stoppage has lasted, or the model's "surely left") or up to an
        edge that moves with the clock (the feed's, less the grader's restart lead; or the watcher's last frame, which
        is exact)."""
        for g in range(self.N):
            op, of, fix, adv, tail = self.open[g], None, NEVER, ALWAYS, self.pad[g]
            eyes_pad = self.tm.eyes_pad(self.read_now[g])
            if op is not None:
                of, adv = op[0], self.feed.edge(g) - self._open_hold(g)
                fix = op[1] if op[1] is not None else fix
                if len(op) > 3 and op[3] is not None:            # [PARK-NFL] never past the play clock with nothing to say why
                    adv, fix = min(adv, op[3]), min(fix, op[3])
                if self.model is not None and len(op) > 2 and op[2] is not None:
                    more = self.model.surely_left(self.leagues[g], op[2], max(0.0, float(self.feed.edge(g) - op[2]["first"])))
                    if more is not None and more > 0:            # the model seam: a later floor only (never an earlier one)
                        fix = max(fix, int(self.feed.edge(g) + more))
            sp = (self.eyes.spans(g) or [None])[-1] if self.eyes is not None else None
            if sp is not None and sp["open"] is not None:
                # the watcher saw the clock stop and had not seen it start again at its last frame
                if of is not None and of <= sp["hi"] + 1:        # the feed has that stoppage open too: the watcher's start, the later edge
                    of = sp["open"]
                    if sp["hi"] >= adv:
                        adv, tail = sp["hi"], eyes_pad
                elif self.raw[g] is None or feed_stop_at(self.raw[g], self.night.base + sp["hi"]) is None:
                    of, fix, adv, tail = sp["open"], NEVER, sp["hi"], eyes_pad     # the feed has no stoppage there: only the watcher's word
                # else: the feed has reported its restart, so it is final
            elif sp is not None and of is not None and of <= sp["hi"]:
                of = sp["hi"] + 1                                # inside what the watcher saw, its word is final and exact
            self.open_from[g], self.fix[g], self.adv[g], self.tail[g] = of, fix, adv, tail
            self.sure[g] = max(fix, adv) if of is not None else ALWAYS

    def _open_hold(self, g: int) -> int:
        """How far short of the feed's edge an open stoppage is trusted: basketball the grader's lead (open_hold), football
        [PARK-NFL] its own play plus the feed lag (park_facts.FactsParams.football_lead_s)."""
        from redzone.noggin.park_facts import is_football
        if is_football(self.leagues[g]):
            return int(math.ceil(self.feed.fx[g].p.lead(League(self.leagues[g]))))
        return self.P.open_hold

    def _collect(self, t: int) -> None:
        """Steps that finished since the last second."""
        if self.j_op is not None and self.j_op.done():
            fut, self.j_op = self.j_op, None
            self._landed(self.j_ctx, _result(fut, ("stop", "the jump step broke")), t)
        if self.h_op is not None and self.h_op[2].done():
            kind, game, fut = self.h_op
            self.h_op = None
            self._hidden_done(kind, game, _result(fut), t)

    async def _drive(self, t: int) -> None:
        """Bring the hidden stream to what the engine holds for it, one step at a time."""
        S, rig, h, P = self.S, self.rig, 1 - self.on, self.P
        if S.hg < 0:
            return
        want_tune = self.retune or rig.game[h] != S.hg
        if self.h_op is not None:
            if want_tune and self.h_op[0] == "hold" and not self.h_op[2].done():
                self.h_op[2].cancel()                            # it is going to another game: no need to finish holding it
                self.h_op = None
            else:
                return
        if want_tune:
            self.retune = False
            self.h_deep = self.clip_steps(t, S.hg)
            steps = self.h_deep[1] if self.h_deep is not None else None
            if not rig.instant and steps is None and rig.game[h] == S.hg and not rig.paused[h] and rig.lag[h] < self.fresh[h] + P.refresh:
                # the engine asked to tune the game this stream already plays, where a fresh tune would put it: nothing is
                # sent (a tune cannot help); the stream plays on and the plan's pause spot simply moves
                self.tunes_skipped += 1
                S.hb, S.h_paused, S.h_ready = S.he + rig.lag[h], False, t
                self.emit({"ev": "step", "kind": "tune", "tick": t, "game": S.hg, "ok": 1, "way": "not needed: already playing it", "s": 0})
                return
            self.clip_tunes += int(steps is not None and self.clips)
            self.back_tunes += int(steps is not None)
            self.tunes_sent += 1
            await self._start("tune", S.hg, rig.tune(h, S.hg) if steps is None else rig.tune(h, S.hg, steps), t)
            if not rig.instant:                                  # where the plan takes this tune to end, until its own read-back
                S.hb = S.he + (self.h_deep[0] if self.h_deep is not None
                               else self.tune_lag_of(S.hg, int(math.ceil(rig.tune_lag + self.tm.p.tune_sd))))
        elif S.h_paused and self.keep == h and self.keep_rolls:
            S.h_paused = False                                   # a kept stream near the cap is never paused again
        elif S.h_paused and not rig.paused[h]:
            await self._start("park" if S.h_kind != "pp" else "hold", S.hg, rig.pause(h, None), t)
        elif not S.h_paused and rig.paused[h]:
            await self._start("resume", S.hg, rig.resume(h), t)
        elif not rig.instant and not rig.paused[h] and t >= S.h_ready:
            game, spot, _ = self.pl.cmd                          # the plan wants it paused at `spot`: the key goes out early
            x = self.A[S.hg]
            go = self.pl.go                                      # (a jump planned before the park could be read back joins it as it plays)
            soon = self.join_playing and go is not None and go[3] == S.hg and go[6] < spot + rig.lag[h] + self.pl.confirm
            if game == S.hg and not soon and 0 <= spot < x.n and x.since[spot] > self.pad0 and x.left[spot] >= self.pad0:
                at = spot - rig.unseen_s - rig.pause_lead[h] + rig.lag[h]
                if t - 0.5 <= at <= t + 1.0:
                    await self._start("park", S.hg, rig.pause(h, at), t)

    async def _start(self, kind: str, game: int, step, t: int) -> None:
        self.h_t0 = self.rig.clock.now() if getattr(self.rig, "clock", None) is not None else float(t)
        if self.rig.instant:
            self._hidden_done(kind, game, await step, t)
        else:
            self.h_op = (kind, game, asyncio.ensure_future(step))
            self.S.h_ready = max(self.S.h_ready, t + 1)

    def _hidden_done(self, kind: str, game: int, res: tuple, t: int) -> None:
        ok, val = res
        S, rig, h, mine, P = self.S, self.rig, 1 - self.on, self.S.hg == game, self.P
        note = getattr(rig, "note", {}).get(h, {}) if not rig.instant else {}
        took = (rig.clock.now() - self.h_t0) if getattr(rig, "clock", None) is not None else 0.0
        self.emit({"ev": "step", "kind": kind, "tick": t, "game": game, "stream": h, "ok": int(bool(ok)), "s": round(took, 1),
                   **{k: v for k, v in note.items() if isinstance(v, (int, float))},
                   "way": note.get("way", ""), "why": "" if ok else str(note.get("why", ""))[:90], "lag_s": round(rig.lag[h], 1),
                   "paused": int(bool(rig.paused[h])), "frozen_s": round(rig.frozen[h], 1) if rig.paused[h] else -1.0})
        if kind == "tune":
            if ok:
                if game in self.tunes:
                    self.tunes[game].append(float(val))
                deep, self.h_deep = self.h_deep, None
                back_s = P.step * (deep[1] - max(1, rig.steps)) if deep is not None else 0.0     # sent back this much further than usual
                adopted = str(note.get("way", "")).startswith(("adopted:", "kept:"))     # not reloaded: it is where its read says (up to minutes back)
                top = P.lag_read_max if (rig.read[h] and adopted) else rig.tune_lag + P.lag_sane
                extra = float((getattr(rig, "extra", None) or {}).get(h, 0.0)) if self.depth.get(game) else 0.0   # [PARK-NFL-LAG]
                if not rig.instant and not (-self.delay - P.lag_early <= rig.lag[h] - back_s - extra <= top):
                    # not a place a fresh tune can be at: take the gesture's own (it is where the stream was just put), and
                    # count its tune as not read (the wide padding)
                    self.emit({"ev": "step", "kind": "place", "tick": t, "game": game, "stream": h, "ok": 0, "lag_s": round(rig.lag[h], 1),
                               "why": "not a place a fresh tune can be at: the read gesture's own place is used"})
                    self.say(f"NOTE: the hidden stick's place after its tune was not believable ({rig.lag[h]:.0f} s behind): using where the tune put it.")
                    rig.replace(h, rig.tune_lag + back_s + extra)
                self.fresh[h] = 0.0 if rig.instant else (rig.lag[h] - back_s) if not adopted else float(rig.tune_lag)
                if not rig.instant and not adopted:
                    self.lags.setdefault(game, []).append(rig.lag[h] - back_s)
                if self.judge is not None:
                    self.judge.tuned(h, game, rig.instant or rig.read[h])
                if not rig.instant and self.tm.reads(game):
                    self.read_hope = bool(rig.read[h])           # the next tune is planned as this one went
                if mine and not rig.instant:
                    S.hb, S.h_paused, S.h_ready = S.he + rig.lag[h], False, t
                elif mine and rig.lag[h]:
                    S.hb = S.he + rig.lag[h]                     # (instant sticks: a stream sent back for a clip)
                if self.board is not None:
                    self.board.event("park", game=self.names[game], channel=self.chans[game], took_s=round(float(val), 1))
            else:
                self.block[game] = t + P.tune_retry
                self._refuse("a tune did not work", t, loud=True, note=f" ({self.names[game]}: {str(note.get('why', ''))[:60] or 'no reason given'})")
                if mine:
                    self._drop("its tune did not work", t)
        elif kind == "resume":
            if ok and mine and not rig.instant:
                S.hb, S.h_paused, S.h_ready = S.he + rig.lag[h], False, t
            elif not ok:
                self._refuse("a resume did not work", t, loud=True)
                if mine:
                    self._drop("its resume did not work", t)     # where it is cannot be trusted: tune it again
        else:
            if ok:
                if mine and not rig.instant:
                    self._paused_at(int(math.floor(val + rig.unseen_s)), t)
                if kind == "park" and mine:
                    self.parked_at = t
                    self.say(f"PARKED {self.names[game]} on {self.chans[game]}, waiting for a dead ball")
                    self.emit({"ev": "parked", "tick": t, "game": game, "at": int(t - S.hb)})
            else:
                self._refuse("a pause did not hold", t, loud=True)
                if mine and rig.game[h] != game:
                    self._drop("its pause left it neither paused nor playing", t)
                elif mine:
                    S.h_paused, S.h_ready = False, t
                    if not rig.instant:
                        S.hb = S.he + rig.lag[h]

    # ---- every look grades its own hold ---------------------------------------------------------------------------------
    def _hold_watch(self, t: int) -> None:
        """After every resume the stream on screen has its place read again at HOLD_AT seconds (one media-session read
        each, no key), for as long as it stays on screen. Each CHANGED position report is written to the event log with
        its update time; the change against the lag at the resume gives the look's verdict: held (within hold_ok),
        drifted (over hold_drift), loose (between), unread (no read came back). A drifted stream is marked unread at
        once, is not cut on again until its place is read again, and that read is asked for here."""
        rig, w = self.rig, self.watch
        if rig.instant or not hasattr(rig, "look"):
            return
        if self.look_op is not None and self.look_op[1].done():
            at_s, fut = self.look_op
            self.look_op = None
            if w is not None:
                self._hold_read(w, at_s, _result(fut, None), t)
        if self.reread_op is not None and self.reread_op[1].done():
            k, fut = self.reread_op
            self.reread_op = None
            ok = bool(_result(fut, (False, 0.0))[0])
            if ok and rig.read[k]:
                self.drifted.discard(k)
                if k == self.on:
                    self.S.sb = self.S.se + rig.lag[k]
                if self.judge is not None and rig.game[k] is not None:
                    self.judge.tuned(k, rig.game[k], True)
                self.say("READ AGAIN: the stream on screen has a place once more.")
                self.emit({"ev": "reread", "tick": t, "stream": k, "ok": 1, "lag_s": round(rig.lag[k], 1)})
            else:
                self.reread_at = t + self.P.tune_retry
                self.emit({"ev": "reread", "tick": t, "stream": k, "ok": 0})
        if self.on in self.drifted and self.reread_op is None and self.j_op is None and t >= self.reread_at and hasattr(rig, "reread"):
            self.reread_op = (self.on, asyncio.ensure_future(rig.reread(self.on)))
        if w is None:
            return
        if w["k"] != self.on:                                    # that stream has left the screen: its look is over
            if self.j_op is None:
                self._hold_end()
            return
        if self.look_op is None and self.j_op is None and w["due"] and t - w["t0"] >= w["due"][0]:
            self.look_op = (w["due"].pop(0), asyncio.ensure_future(rig.look(w["k"])))

    def _hold_read(self, w: dict, at_s: int, res, t: int) -> None:
        P = self.P
        row = {"at_s": at_s, "lag_s": None, "change_s": None}
        if res is not None:
            row = {"at_s": at_s, "lag_s": round(float(res["lag"]), 2), "change_s": round(float(res["lag"]) - w["lag0"], 2)}
            rep = (res.get("pos_s"), res.get("updated"))
            if rep != w["last"]:                                 # each CHANGED position report, with its update time
                w["last"] = rep
                self.emit({"ev": "position", "tick": t, "n": w["n"], "stream": w["k"], "pos_s": rep[0], "updated": rep[1],
                           "state": res.get("state"), "lag_s": row["lag_s"]})
                if self.board is not None:
                    self.board.event("position", n=w["n"], pos_s=rep[0], updated=rep[1], lag_s=row["lag_s"])
        w["reads"].append(row)
        got = [abs(r["change_s"]) for r in w["reads"] if r["change_s"] is not None]
        was, w["verdict"] = w["verdict"], ("unread" if not got else "drifted" if max(got) > P.hold_drift
                                           else "held" if max(got) <= P.hold_ok else "loose")
        hold = {"verdict": w["verdict"], "lag_at_resume_s": round(w["lag0"], 2), "reads": list(w["reads"])}
        if 0 < w["n"] <= len(self.records):
            self.records[w["n"] - 1]["hold"] = hold              # the verdict goes into that jump's decision record
        self.emit({"ev": "hold", "tick": t, "n": w["n"], **hold})
        if self.board is not None and (w["verdict"] != was or not w["due"]):
            self.board.event("hold", n=w["n"], verdict=w["verdict"], change_s=row["change_s"], at_s=at_s)
        if w["verdict"] == "drifted" and w["k"] not in self.drifted and res is not None:
            k = w["k"]
            self.drifted.add(k)
            self.rig.replace(k, float(res["lag"]))               # unread at once (the wide padding), at where it is now
            if k == self.on:
                self.S.sb = self.S.se + float(res["lag"])
            self.reread_at = t + self.P.cool
            self.say(f"DRIFTED: the stream on screen moved {row['change_s']:+.1f} s from where it was read ({at_s} s after its resume). "
                     f"No cut on it until its place is read again.")
            if self.board is not None:
                self.board.event("alert", level="warn", text=f"a stream's place drifted {row['change_s']:+.1f} s after its resume: it is read again before any cut")

    def _hold_end(self) -> None:
        """The look being graded is over (its stream left the screen, or the show ends): count its verdict."""
        w, self.watch = self.watch, None
        if w is not None:
            self.holds[w["verdict"]] = self.holds.get(w["verdict"], 0) + 1
            if 0 < w["n"] <= len(self.records):
                self.records[w["n"] - 1].setdefault("hold", {"verdict": "unread", "lag_at_resume_s": round(w["lag0"], 2), "reads": []})

    def _drop(self, why: str, t: int) -> None:
        """The hidden stream can no longer be counted on for its game: say so, once, with the cause; it will be tuned again."""
        S = self.S
        game = self.names[S.hg] if S.hg >= 0 else "its game"
        S.hg, S.h_paused, S.h_ready = -1, False, t
        self.say(f"DROPPED {game} from the hidden stick: {why}. It will be tuned again.")
        self.emit({"ev": "drop", "tick": t, "why": why})
        if self.board is not None:
            self.board.event("alert", level="warn", text=f"hidden stick dropped: {why}")

    def _paused_at(self, p: int, t: int) -> None:
        """The hidden stream is paused and the first second a viewer would see of it is p. (Called before the engine's
        second t, which moves a paused stream's lag on by one.)"""
        if self.paused_since is None:
            self.paused_since = t                                # the park age cap counts from here (park.max_park_s)
        self.S.h_paused, self.S.hb, self.S.h_ready = True, float(t - 1 - p) + self.S.he, t

    # ---- the jump --------------------------------------------------------------------------------------------------
    def guard(self, t: int, g1: int, i: int, over: bool, g2: int, j: int, k1: int, k2: int) -> str:
        """The hard guard, asked before every jump ("" = it may go): leave game g1 at believed second i (stream k1),
        first second seen of game g2 = j (stream k2). Never traded for pace."""
        if k1 in self.drifted or k2 in self.drifted:
            return "a stream's place drifted after its resume: no cut on it until it is read again"
        for g, p, skip in ((g1, i, over), (g2, j, False)):
            # Nothing is cut on within the grader's restart lead of the feed's edge, final or not: the rows that date that
            # second may not be in yet (a row can also arrive carrying a stamp far from its place in the list). A stream
            # placed by its read sits about 50 s behind the edge, so this only ever stops a stream at its live edge.
            if not skip and self.feed.edge(g) < 10 ** 8 and self.feed.edge(g) - p < self.P.open_hold:
                return "the cut is too close to the feed's edge: the rows that date it may not be in yet"
        return refusal(self.A, self.pad0, t, self.flip, self.know, self.s.late_cap, g1, i, over, g2, j,
                       self.sure, self.open_from, self.tail, self.extra_of(k1, g1), self.extra_of(k2, g2))

    def _relied(self, t: int, g1: int, i: int, over: bool, g2: int, j: int) -> dict:
        """For the jump's record, so a run can be regraded afterwards: each end's kind, how deep inside its stretch the
        cut is (seconds since the stretch began and left in it, as the show's own arrays have them), whether that
        stretch is final or still open in the feed, and the feed row it rests on (type, period, clock, age)."""
        def end(g: int, p: int) -> dict:
            x = self.A[g]
            q = min(x.n - 1, max(0, p))
            since, left = x.since[q], x.left[q]
            kind = "timeout or break" if x.brk[q] else "dead ball" if x.dead[q] else "live play"
            row = self.feed.row_at(g, q) if hasattr(self.feed, "row_at") else {}
            return {"kind": kind, "second": int(p), "since_s": int(since), "left_s": int(min(left, 9999)),
                    "open": bool(self.open_from[g] is not None and q >= self.open_from[g]), "feed_edge_s": int(self.feed.edge(g) - q), "row": row}
        return {"leave": {"kind": "that game is over"} if over else end(g1, i), "join": end(g2, j)}

    async def _jump(self, t: int, snap: dict) -> None:
        """The engine has just jumped (its own padding tests passed). Check it again here, then carry it out."""
        S, rig, S0, tgt = self.S, self.rig, snap["S"], 1 - self.on
        g1, g2, i, j = S0["sg"], S.sg, int(t - S0["sb"] + S0["se"]), int(S.s_from + S.se)
        paused = S.sb - S0["hb"] > 0.5                           # the engine joined a paused stream (not one that plays)
        wr = self.pl.wr
        why = "waiting after a step that failed" if t < self.no_jump_until else \
            self.guard(t, g1, i, t > self.A[g1].a1 + S0["sb"], g2, j, self.on, tgt)
        if not why and not rig.instant and self.pl.warrant == "need" and (wr is None or not wr[0]):
            why = "no warrant was worked out for this jump"      # (the plan only schedules warranted jumps: this is the second look)
        if not why and rig.game[tgt] != g2:
            why = "the stream to join is not on that game"
        if not why and not rig.instant and rig.paused[tgt] != paused:
            why = "the stream to join is not parked yet" if paused else "the stream to join is paused"
        if not why and not rig.instant and not paused and not self.join_playing:
            why = "the stream to join is not parked yet"
        if why:
            self._revert(snap)
            if why.endswith("not parked yet"):
                S.h_paused = True                                # keep the engine's pause, so the park is carried out
            self._refuse(why, t)
            return
        if rig.instant and paused and not rig.paused[tgt]:
            await rig.pause(tgt, None)                           # the engine parked it and jumped in the same second
        hold = None if (self.keep == self.on and (self.keep_rolls or self.keep_live)) else self.on
        self.held_s = (t - self.paused_since) if (paused and self.paused_since is not None) else 0   # how long the landing was held
        self.relied = self._relied(t, g1, i, t > self.A[g1].a1 + S0["sb"], g2, j)   # what both ends of this cut rest on, as known NOW
        ctx = (snap, t, g1, i, g2, j, paused, self.on, tgt, wr)
        self.on = tgt
        if rig.instant:
            self._landed(ctx, await rig.jump(tgt, paused, hold), t)
        else:
            self.j_ctx, self.j_op = ctx, asyncio.ensure_future(rig.jump(tgt, paused, hold))
            if hold is not None:                                 # paused at the swap, read back hold_off + 1 s after the jump began
                S.h_ready = max(S.h_ready, t + rig.hold_off + 1)  # (the engine lets it run on till then: hold_off past the leave)

    def _landed(self, ctx: tuple, res: tuple, t: int) -> None:
        snap, t0, g1, i, g2, j, paused, was_on, tgt, wr = ctx
        status, why = res[0], res[1]
        S, rig = self.S, self.rig
        if status == "ok":
            x, self.jumps_done = self.A[g1], self.jumps_done + 1
            ii = min(x.n - 1, max(0, i))
            kind = "that game is over" if t0 > x.a1 + snap["S"]["sb"] else "timeout or break" if x.brk[ii] else "dead ball" if x.dead[ii] else "after a shot"
            left = "no end reported yet" if self.open_from[g1] is not None and ii >= self.open_from[g1] else f"about {x.left[ii]} s of dead time left"
            look = t0 - (self.since if self.since is not None else t0)
            self.looks.append((look, self.look_live))
            self.jump_at.append(t0)
            self.since, self.look_live, self.parked_at = t0 + self.flip, 0, None
            wd = warrant_row(wr)
            self.warrants.append(wr)
            if not rig.instant and hasattr(rig, "look"):         # every look grades its own hold (see _hold_watch)
                self._hold_end()
                self.watch = {"n": self.jumps_done, "k": tgt, "t0": t, "lag0": float(rig.lag[tgt]), "due": list(HOLD_AT), "reads": [],
                              "last": None, "verdict": "unread"}
            rec = {"n": self.jumps_done, "tick": t0, "from": self.names[g1], "to": self.names[g2], "reason": kind, "warrant": wd,
                   "leave_at": (g1, i), "join_at": (g2, j), "behind_s": round(t0 + self.flip - j + self.delay, 1),
                   "pad_s": {"from": self.pad[g1], "to": self.pad[g2]}, "read": {"from": bool(rig.instant or rig.read[was_on]), "to": bool(rig.instant or rig.read[tgt])},
                   "held_s": int(getattr(self, "held_s", 0)), **getattr(self, "relied", {})}
            self.records.append(rec)
            self.say(f"JUMP -> {self.names[g2]} on {self.chans[g2]} ({kind}, {left} on the game we left). Why: {wd['why']}"
                     + (f" (worth {wd['there']} there, {wd['here']} here)" if wr is not None else ""))
            self.emit({"ev": "jump", "tick": t0, "from": g1, "to": g2, "leave": i, "land": j, "behind_s": rec["behind_s"],
                       "n": self.jumps_done, "look_s": look, "look_live_s": self.looks[-1][1], "warrant": wd,
                       "leave_on": rec.get("leave"), "join_on": rec.get("join"), "held_s": rec["held_s"]})
            if self.board is not None:
                self.board.event("jump", **{"from": self.names[g1], "to": self.names[g2], "reason": kind,
                                            "jump_s": round(float(getattr(rig, "jump_s", self.flip)), 1), "look_s": look, "warrant": wd,
                                            "leave_on": rec.get("leave"), "join_on": rec.get("join"), "held_s": rec["held_s"],
                                            "n": self.jumps_done})
            if self.judge is not None:
                self.judge.flip(t0, was_on, g1, i, tgt, g2, j, paused)
            if self.eyes is not None:                            # the watcher rules on both ends once it has seen those seconds
                self.to_judge += [["leaves", g1, i, 0, rig.instant or rig.read[was_on]],
                                  ["joins", g2, j, 0, rig.instant or rig.read[tgt]]]
            if not rig.instant:                                  # the streams as the rig holds them after the swap
                S.sb = S.se + rig.lag[tgt]
                fut = rig.confirming(was_on)
                if S.hg == g1 and not self.retune and rig.paused[was_on]:
                    self._paused_at(int(math.floor(rig.frozen[was_on] + rig.unseen_s)), t)
                    if fut is not None:
                        self.h_op = ("hold", g1, fut)
                        S.h_ready = t + 1
                elif fut is not None:
                    fut.cancel()
                if self.keep == was_on and (self.keep_rolls or self.keep_live):
                    S.h_paused, S.hb = False, S.he + rig.lag[was_on]
            return
        self.on, self.retune = was_on, False                     # nothing changed on the TV: the picture that was up stays up
        self._revert(snap)
        self.no_jump_until = t + self.P.cool
        self._refuse(why, t, loud=True)
        if status == "stop":
            self.stop = why                                      # a sound stop (or a broken step) is final: no more jumps
        if rig.instant:
            S.h_paused = bool(rig.paused[tgt])
        else:
            self._sync(t)

    def _revert(self, snap: dict) -> None:
        S, st = self.S, self.st
        for k, v in snap["S"].items():
            setattr(S, k, v)
        for x, n in zip((st.hops, st.flips, st.ups, st.visits, st.jumps), snap["lists"]):
            del x[n:]
        st.play_visits, st.mid_entry, st.mid_leave, st.hl_leave, st.walk_entry, st.walk_leave = snap["ints"]
        self.pl.next = -1

    def _sync(self, t: int) -> None:
        """After a jump that did not happen: both streams as the rig holds them now."""
        S, rig, h = self.S, self.rig, 1 - self.on
        S.sb, S.black = S.se + rig.lag[self.on], min(S.black, t)
        if S.hg >= 0 and rig.game[h] != S.hg:
            self._drop("it was lost in a jump that did not happen", t)
        elif S.hg < 0:
            S.h_paused, S.h_ready = False, t
        elif rig.paused[h]:
            self._paused_at(int(math.floor(rig.frozen[h] + rig.unseen_s)), t)
        else:
            S.hb, S.h_paused, S.h_ready = S.he + rig.lag[h], False, t

    # ---- talking -----------------------------------------------------------------------------------------------------
    def _refuse(self, why: str, t: int, loud: bool = False, note: str = "") -> None:
        if why != self.last_ref[0] or t > self.last_ref[1] + 1:
            self.refused[why] = self.refused.get(why, 0) + 1
            self.emit({"ev": "refused", "tick": t, "why": why})
            if loud:
                self.say(f"NO JUMP: {why}{note}. The picture that is up stays up.")
                if self.board is not None:
                    self.board.event("alert", level="warn", text=f"no jump: {why}")
        self.last_ref = (why, t)

    def _stayed(self, t: int) -> None:
        why = self.st.why_t.get(t) or (self.last_ref[0] if self.last_ref[1] == t else "")
        why = PLAIN.get(why, why.replace("_", " "))
        if not why:
            self.stay[1] = 0
            return
        self.stay[1] += 1
        self.stayed[why] = self.stayed.get(why, 0) + 1
        if why != self.stay[0] or t - self.stay[2] >= self.P.stay_say:
            self.stay[0], self.stay[2] = why, t
            self.say(f"STAYED: {why} ({self.stay[1]} s)")
            if self.board is not None:
                self.board.event("stay", reason=why, for_s=self.stay[1])

    async def close(self) -> None:
        for fut in (self.h_op[2] if self.h_op else None, self.j_op, self.look_op[1] if self.look_op else None,
                    self.reread_op[1] if self.reread_op else None):
            if fut is not None and not fut.done():
                fut.cancel()
        self._hold_end()
        await self.rig.finish()
        if self.to_judge:
            self._eyes_judge(final=True)
        if self.board is not None and self.last_t is not None:
            self.board.write(self, self.last_t, mode="stopped", force=True)


def warrant_row(wr: tuple | None) -> dict:
    """A jump's warrant as it is written to the decision record and the Show tab: was there one, what the next seconds
    are worth there and here (the grader's value of the plays, big plays twice, 0.05 a live second), how many seconds
    sooner live play is on screen, and the reason in words."""
    if wr is None:
        return {"ok": None, "there": None, "here": None, "live_sooner_s": None, "why": "not worked out"}
    return {"ok": bool(wr[0]), "there": round(float(wr[1]), 2), "here": round(float(wr[2]), 2), "live_sooner_s": int(wr[3]), "why": wr[4]}


def warrant_counts(warrants: list) -> dict:
    """How many of the jumps carried out had a warrant, and the reasons."""
    why: dict = {}
    for w in warrants:
        k = "not worked out" if w is None else w[4]
        why[k] = why.get(k, 0) + 1
    return {"warrant_n": len(warrants), "warranted": sum(1 for w in warrants if w is not None and w[0]), "warrant_why": why}


def peak(times: list, window_s: int) -> int:
    """The most jumps in any stretch of window_s seconds."""
    ts, best, a = sorted(times), 0, 0
    for b, x in enumerate(ts):
        while ts[a] <= x - window_s:
            a += 1
        best = max(best, b - a + 1)
    return best


def _result(fut, bad=(False, None)) -> tuple:
    try:
        return fut.result()
    except (Exception, asyncio.CancelledError):
        return bad
