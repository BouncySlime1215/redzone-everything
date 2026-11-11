"""PARK core: the two-stick show, one second at a time, on a night's per-second facts.

The settings (`Setting`), one game on the night's 1 s grid (`Arr`), a night (`Night`), the two sticks (`State`), the
counters (`Stats`), the step function (`run`) and the summary of a run (`summarize`). Pure: no TV, no adb, no network,
no file reads. Moved here unchanged from `scripts/park_sim.py` so the app and the scripts run ONE copy; the scripts keep
their command line, night loading and simulator brains and import the rest from here.

The timing numbers that used to be module constants (`SEEN_LEAD_S`, `SKIP_STEP_S`, ...) are fields of `ParkParams`, read
from `park.<name>` by `ParkParams.from_config`; `PARK_PARAMS` holds the defaults, which are exactly the old values.
Nothing in the app imports this module yet (a later unit wires it in behind `park.enabled`).

How `run` works, the brains it asks, and how Rule #1 is judged: see the docstring of `scripts/park_sim.py`.

Why: one copy of the PARK simulator core that both the product and the scripts use, so a planner tested in the
simulator is byte for byte the planner the app runs (INTEGRATION-PLAN unit PARK-CORE).
"""
from __future__ import annotations

import bisect
import math
import random
import statistics
from collections.abc import Callable
from dataclasses import dataclass, fields


@dataclass(frozen=True)
class ParkParams:
    """Timing and reach numbers of the PARK show (seconds unless named otherwise). The defaults are the measured values
    the scripts used as module constants; the integer ones stay integers because they index the 1 s grid."""
    run_up_s: int = 75                  # how far ahead a brain's value looks (veto_audit.RUN_UP_S)
    seen_lead_s: int = 7                # the picture must be up this long before a moment to have seen it (veto_audit.PLAY_LEN_S)
    yes_s: int = 20                     # the audit's yes: the wanted game's picture up within this (veto_audit.YES_S)
    plan_ok_s: int = 2                  # a planner's scheduled jump is delivered when it happens within this of the planned second
    feed_lead_s: int = 27               # the feed runs this far ahead of the stream's live edge (stream delay 35 s less feed lag 8 s)
    live_behind_s: int = 60             # a big play seen this close to the live edge counts as seen live
    long_stop_s: int = 20               # a stoppage the feed says still has this long to run may be left without having shown any play
    same_behind_s: int = 10             # a stick already behind live may skip forward to this close to the live edge
    pad_s: int = 400                    # empty seconds kept before the first tip and after the last final on a night's grid
    hop_gain_s: int = 10                # a visit that has shown no play is left only for a play that starts this much sooner
    shot_window_s: float = 4.0          # the any-shot rule: this long after any shot counts as a legal point
    skip_step_s: float = 15.0           # one forward or back skip on the stick
    press_s: float = 1.28               # each further key press of a skip or rewind
    reach_s: float = 175.0              # deepest a REWIND puts a stick behind live
    rescrub_1_s: float = 19.3 - 2.5 - 3.0   # a fresh scrub on the channel the stick is already on (load = tune + 3 s + rewind)

    @classmethod
    def from_config(cls, get: Callable[..., object]) -> "ParkParams":
        """Each field from `park.<name>`, or its default here. A bad value keeps the default (arbiter.Params.from_config)."""
        kw = {}
        for f in fields(cls):
            d = f.default
            v = get(f"park.{f.name}", d)
            kw[f.name] = type(d)(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else d
        return cls(**kw)


PARK_PARAMS = ParkParams()
P = PARK_PARAMS                         # the short name the step function reads
TUNE_TICKS = 3                          # live-edge physics: a tune within YouTube TV (2.3-3.0 s), in whole seconds
WINDOWS_MIN = (5, 10, 20, 30, 45, 60)   # the pace windows of a summary, in minutes
WHY = ("other_game_parked", "nothing_parked", "loading", "margin", "flip_in_progress", "min_stay", "past_reach", "screen_high_leverage",
       "screen_mid_play", "landing_not_dead", "landing_long_wait",
       # a planner's own reasons (park_plan.Planner.why): what its search found in the way
       "no_leave_reported", "no_landing_reported", "late_cap", "plan_other_game", "plan_stays", "plan_waits")


def span(games: list[dict]) -> tuple[float, float]:
    """While at least two of these games are in progress."""
    return sorted(g["t0"] for g in games)[1], sorted(g["t1"] for g in games)[-2]


def pct(xs: list[float], q: float) -> float:
    """The q quantile of xs by nearest rank (NaN for none)."""
    if not xs:
        return float("nan")
    xs = sorted(xs)
    return xs[min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))]


@dataclass(frozen=True)
class Setting:
    mode: str = "park"            # park | live (today's physics: both sticks at the live edge)
    pause_holds: bool = True      # measured Nov 10: it does. False = the fallback where only scrub positions hold
    route: str = "tune"           # how the hidden stick gets to another game: tune (tune, play to the spot, one pause key) |
    #                               rewind (the 19 s rewind load, every time) | either (rewind only for a better spot already passed)
    tune_s: float = 3.0           # a tune within YouTube TV: 2.5-3.7 s, then the stick is at the live edge
    load_s: float = 19.3          # the rewind load on a new game (18.5-30 s measured)
    rewind_sd: float = 3.0        # extra position doubt after a rewind (the on-screen label)
    resume_lead_s: float = 0.0    # the parked stick is resumed this long before its picture is up (the pause banner has to clear)
    pos_err: float = 5.0          # the position label is right within about this (sd = 0.6 x)
    guard: float = -1.0           # seconds kept from every edge of a dead stretch; -1 = pos_err
    delay_error: float = 0.0      # every true position is this much later than believed (the audit's --delay-error)
    # what is still uncertain for a parked stick, as four separate parts (each an sd in seconds)
    pos_sd: float = -1.0          # position readout, drawn per park; -1 = 0.6 x pos_err
    delay_sd: float = 0.0         # channel delay, drawn once per game per night (moves every cut on that game alike)
    stamp_sd: float = 0.0         # feed stamp against the picture, drawn per whistle or shot used
    key_sd: float = 0.0           # key latency spread, drawn per jump
    pad_k: float = 0.0            # > 0: guard = pad_k x the root of the summed squares of the four (overrides guard)
    min_dead: float = 10.0        # shortest dead stretch worth parking on
    max_behind: float = 175.0     # how far behind the live edge a paused stick may sit (315 s was held on the TV)
    late_cap: float = 145.0       # Nick's lateness cap: no picture comes up further behind the stream's live edge than this
    #                               (180 s behind real life less about 35 s of stream delay). 0 = no cap (the sweeps before Nov 10)
    min_behind: float = 45.0      # the smallest rewind lands about this far behind
    ping_pong: bool = True        # the stick being left stays on its game, paused where the viewer left
    min_stay: float = 0.0         # seconds on a game before the next jump
    min_live_s: float = 3.0       # seconds of live play a visit must have shown before the next jump (waived in a long stoppage)
    land_wait_max: float = 15.0   # a landing must be within this many seconds of a play starting (999 = return exactly where you left)
    margin: float = 0.0           # brain points the parked game must lead the screen game by before a jump
    repark_margin: float = 1.0    # brain points another game must lead the parked one by before the hidden stick moves
    lookahead_s: float = 0.0      # the hidden stick is parked on the game the brain will score highest this many seconds on
    bore_w: float = 0.0           # brain points the screen game loses per second it has been on (Nick wants many jumps)
    skip_dead: bool = True        # the left stick skips its dead time forward to the next play
    clips: bool = False           # park back to a play the feed already reported (up to the 175 s reach)
    leave_rule: str = "dead"      # dead | low_leverage
    park_at: str = "end"          # end = just before the play (R3) | start = just after the whistle
    park_target: str = "value"    # value = before the best known moment in reach | recent = the latest dead stretch
    shots: bool = False           # R2: the 4 s after any shot is a legal point (basketball)
    flip: float = 2.05
    feed_lead: float = P.feed_lead_s
    prepark: bool = True          # while the brain is content, keep the hidden stick on its second choice
    load_fail_p: float = 0.0      # a load fails and is retried (twice the time)
    load_slow_s: float = 0.0      # every load is this much slower (an app change, a slow night)
    value: str = "dense"          # what the brain values: dense = value2 grades + 2 x must-sees + live action | v1 = must-sees only
    live_w: float = 0.05          # dense: brain points per second of live play (30 s of play = 1.5, about one good basket)
    seed: int = 0


class Arr:
    """One game on the night's 1 s grid (index = seconds since the night's base)."""

    def __init__(self, g: dict, base: int, n: int, shots: bool, value: str = "dense", live_w: float = 0.05):
        self.a0, self.a1 = int(g["t0"] - base), min(n - 1, int(g["t1"] - base))
        dead, brk, prev = bytearray(b"\x01") * n, bytearray(n), self.a0
        for (s, e), b in zip(g["dead"], g["brk"]):
            si, ei = max(0, int(s - base)), min(n, int(e - base))
            if si > prev:
                dead[prev:si] = bytes(si - prev)
            if b and ei > si:
                brk[si:ei] = b"\x01" * (ei - si)
            prev = max(prev, ei)
        if self.a1 > prev:
            dead[prev:self.a1] = bytes(self.a1 - prev)
        legal = bytearray(dead)
        if shots:
            for s in g["shots"]:
                si = max(0, int(s - base))
                legal[si:si + int(P.shot_window_s)] = b"\x01" * len(legal[si:si + int(P.shot_window_s)])
        self.dead, self.brk, self.legal = dead, brk, legal
        since, left, run = [0] * n, [0] * n, 0
        self.starts, self.ends = [], []          # legal stretches that end in a play (so a stick can be parked on them)
        for i in range(n):
            if legal[i]:
                if not run:
                    self.starts.append(i)
                run += 1
            else:
                if run:
                    self.ends.append(i)
                run = 0
            since[i] = run
        if len(self.starts) > len(self.ends):
            self.starts.pop()
        run = 0
        for i in range(n - 1, -1, -1):
            run = run + 1 if legal[i] else 0
            left[i] = run
        self.since, self.left = since, left
        hl = bytearray(n)
        for a, b in g["hl"]:
            ai, bi = max(0, int(a - base)), min(n, int(b - base) + 1)
            if bi > ai:
                hl[ai:bi] = b"\x01" * (bi - ai)
        self.hl = hl
        self.hlc = [0] * (n + 1)
        self.mom: dict[int, float] = {}
        for t, v in g["big"]:
            i = int(t - base)
            if 0 <= i < n:
                self.mom[i] = self.mom.get(i, 0.0) + v
        self.v2: dict[int, float] = {}          # value2 live value of the graded plays (dead ones are negative)
        for t, v, _ in g.get("v2", ()):
            i = int(t - base)
            if 0 <= i < n:
                self.v2[i] = self.v2.get(i, 0.0) + v
        self.cumv, self.cumvt = [0.0] * (n + 1), [0.0] * (n + 1)
        dense = value == "dense"
        for i in range(n):
            v = self.mom.get(i, 0.0)
            if dense:
                v = 2.0 * v + self.v2.get(i, 0.0) + (live_w if not dead[i] else 0.0)
            self.cumv[i + 1] = self.cumv[i] + v
            self.cumvt[i + 1] = self.cumvt[i] + v * i
            self.hlc[i + 1] = self.hlc[i] + hl[i]
        self.lev = [1.0] * n
        self.margin = [0] * n
        self.left_s = [-1.0] * n
        k, cur = 0, (1.0, 0, -1.0)
        rows = [(int(w - base), lv, m, lf) for w, lv, m, lf in g["lev"]]
        for i in range(n):
            while k < len(rows) and rows[k][0] <= i:
                cur = rows[k][1:]
                k += 1
            self.lev[i], self.margin[i], self.left_s[i] = cur
        self.n = n
        self._leave: dict[tuple, list[int]] = {}

    def leave_in(self, guard: int, low_leverage: bool) -> list[int]:
        """Seconds from each picture position until this game next offers a legal leave (the controller's own test)."""
        key = (guard, low_leverage)
        if key not in self._leave:
            n, out, nxt = self.n, [0] * self.n, 10 ** 6
            for i in range(n - 1, -1, -1):
                calm = self.hlc[min(n, i + guard + 1)] - self.hlc[max(0, i - guard)] == 0
                if calm and (low_leverage or (self.since[i] > guard and self.left[i] >= guard)):
                    nxt = i
                out[i] = nxt - i
            self._leave[key] = out
        return self._leave[key]

    def ahead(self, lo: int, hi: int, c: int) -> float:
        """Must-see value in (lo, hi], sooner counting a bit more (veto_audit.Truth.ahead)."""
        lo, hi = max(0, lo), min(self.n - 1, hi)
        if hi <= lo:
            return 0.0
        sv = self.cumv[hi + 1] - self.cumv[lo + 1]
        return sv * (1.0 + 0.5 * c / P.run_up_s) - (0.5 / P.run_up_s) * (self.cumvt[hi + 1] - self.cumvt[lo + 1]) if sv else 0.0


class Night:
    def __init__(self, games: list[dict]):
        self.games = games
        self.base = int(min(g["t0"] for g in games)) - P.pad_s
        self.n = int(max(g["t1"] for g in games) - self.base) + P.pad_s
        a, b = span(games)
        self.span = (int(a - self.base), int(b - self.base))
        self._arrs: dict[tuple, list[Arr]] = {}
        self.big_total = self.v2_total = 0.0

    def arrs(self, shots: bool, value: str = "dense", live_w: float = 0.05) -> list[Arr]:
        key = (shots, value, live_w)
        if key not in self._arrs:
            if len(self._arrs) > 6:
                self._arrs.clear()
            self._arrs[key] = [Arr(g, self.base, self.n, shots, value, live_w) for g in self.games]
            a, b = self.span
            self.big_total = sum(v for x in self._arrs[key] for i, v in x.mom.items() if a <= i < b)
            self.v2_total = sum(v for x in self._arrs[key] for i, v in x.v2.items() if a <= i < b and v > 0)
        return self._arrs[key]



class State:
    __slots__ = ("sg", "sb", "se", "s_land", "s_from", "s_kind", "s_live", "hg", "hb", "he", "h_ready", "h_paused", "h_kind", "black", "rng", "gd")


class Stats:
    def __init__(self):
        self.jumps, self.wants, self.ups, self.behind, self.visits = [], [], [], [], []
        self.hops: list[int] = []      # every flip; `jumps` holds only those whose visit went on to show 3 s or more of live play
        self.seen = {"live": 0.0, "parked": 0.0, "clip": 0.0}
        self.pic = self.live_s = self.dead_s = self.brk_s = 0
        self.mid_entry = self.mid_leave = self.hl_leave = self.walk_entry = self.walk_leave = 0
        self.parks = self.load_fail = self.ticks = self.rolls = self.leave_soon = self.play_visits = 0
        self.v2_seen = self.brain_value = 0.0
        self.why: dict[str, int] = {}          # per second the brain wanted another game and no jump went: the first thing in the way
        self.why_t: dict[int, str] = {}        # the same, by second (so the refused seconds can be counted apart)
        self.shown: set[tuple[int, int]] = set()   # (game, second) of every valued moment already counted: a play watched twice counts once
        self.flips: list[tuple[int, int, int]] = []   # every flip: (second, game joined, Rule #1 breaks on this one)
        self.sched: list[tuple[int, int, int]] = []   # a planner's scheduled jumps: (second it was scheduled, planned second, game)
        self.firm: list[tuple[int, int, int]] = []    # the same, logged when the jump came inside the planner's commit window


def run(night: Night, brain, s: Setting, *, t0: int | None = None, t1: int | None = None, state: State | None = None,
        stats: Stats | None = None, force: tuple | None = None, planner=None) -> tuple[Stats, State]:
    """Play the night (or t0..t1 from a saved state). `force` = (kind, game, until): until that second the controller is told
    what to do ('stay', 'park' the hidden stick on a game, or 'jump' to it) instead of asking the brain.
    `planner` (redzone.noggin.park_plan.Planner) replaces the rule set: each second it says whether to jump and what the hidden stick does
    (tune to a game, pause at a spot, skip forward to one, or roll). The physics, the padding tests and the counters below are
    the same ones; the brain is then only asked what it WANTS, for the audit's yes-rate."""
    A = night.arrs(s.shots, s.value, s.live_w)
    N, n = len(A), night.n
    a, b = night.span
    t0, t1 = (a if t0 is None else t0), (b if t1 is None else t1)
    live = s.mode == "live"
    sd = 0.6 * s.pos_err if s.pos_sd < 0 else s.pos_sd
    guard = int(round(s.pos_err if s.guard < 0 else s.guard))
    if s.pad_k > 0:
        guard = max(1, int(round(s.pad_k * math.sqrt(sd ** 2 + s.delay_sd ** 2 + s.stamp_sd ** 2 + s.key_sd ** 2))))   # the grid is 1 s
    edge_sd = math.sqrt(s.stamp_sd ** 2 + s.key_sd ** 2)
    flip = max(1, int(round(s.flip)))
    lattice = 0 if (s.pause_holds or live) else int(P.skip_step_s)
    tune = s.route != "rewind" and s.pause_holds and not live
    fresh = 0 if (live or tune) else int(s.min_behind)      # where a game no stick holds would be picked up
    reach = int(s.max_behind)                                # a paused stick
    rw_reach = int(P.reach_s if s.clips else min(s.max_behind, P.reach_s))   # a rewind
    lead, know = int(round(s.resume_lead_s)), int(s.feed_lead)
    if s.late_cap > 0 and not live:
        # the lateness cap: a jump needs the stick within reach + flip of the live edge, and its picture then comes up
        # flip - lead later, so this keeps every landing within late_cap
        reach = min(reach, int(s.late_cap) - 2 * flip + lead)
        rw_reach = min(rw_reach, reach)
    sd_rw = math.sqrt(sd ** 2 + s.rewind_sd ** 2)
    back, la = 0, int(s.lookahead_s)
    need = max(int(s.min_dead), 2 * guard + lattice)
    st = stats or Stats()
    brain.bind(A, s)
    S = state
    if S is None:
        S = State()
        S.rng = random.Random(s.seed)
        S.gd = [S.rng.gauss(0.0, s.delay_sd) if s.delay_sd else 0.0 for _ in range(N)]
        sc0 = [brain.score(g, t0 - fresh, t0, False, 0) if A[g].a0 <= t0 <= A[g].a1 else -1e9 for g in range(N)]
        S.sg = max(range(N), key=lambda g: sc0[g])
        S.sb, S.se, S.s_land, S.s_from, S.s_kind = float(fresh), S.rng.gauss(0.0, sd) + s.delay_error + S.gd[S.sg], t0, t0 - fresh, "park"
        S.sb += S.se
        S.s_live = 0
        S.hg, S.hb, S.he, S.h_ready, S.h_paused, S.h_kind, S.black = -1, 0.0, 0.0, t0, False, "park", t0
    rng = S.rng

    def choose_park(g: int, ready: int, t: int, lo: int | None = None, hi: int | None = None) -> tuple[int, bool, float] | None:
        """(believed position to park at, is it a clip of an already reported moment, its value) for a stick ready at `ready`."""
        x = A[g]
        lo, hi = (ready - rw_reach if lo is None else lo), (ready - int(s.min_behind) if hi is None else hi)
        if s.park_at == "end":
            i, j = bisect.bisect_left(x.ends, lo + guard), bisect.bisect_right(x.ends, hi + guard)
        else:
            i, j = bisect.bisect_left(x.starts, lo - guard), bisect.bisect_right(x.starts, hi - guard)
        best = None
        known = min(n - 1, t + int(s.feed_lead))
        for k in range(j - 1, i - 1, -1):
            if x.ends[k] - x.starts[k] < need or x.ends[k] > known:
                continue
            p = x.ends[k] - guard - lead if s.park_at == "end" else x.starts[k] + guard
            v = x.ahead(p, min(p + P.run_up_s, known), p)
            if s.park_target == "recent":
                return p, False, v
            if best is None or v > best[0] + 1e-9:
                best = (v, p)
        if best is None:
            return None
        return best[1], bool(s.clips and best[0] > 0 and best[1] < t - int(s.max_behind)), best[0]

    def start_park(g: int, t: int) -> None:
        same = g == S.hg
        load = TUNE_TICKS if live else (P.rescrub_1_s if same else s.load_s) + s.load_slow_s
        if s.load_fail_p and rng.random() < s.load_fail_p:
            load *= 2.0
            st.load_fail += 1
        ready = int(math.ceil(t + load + (0 if (live or lattice) else P.skip_step_s / 2.0)))
        if live:
            S.hg, S.he, S.h_ready, S.h_paused, S.h_kind = g, rng.gauss(0.0, sd) + s.delay_error + S.gd[g], ready, False, "park"
            S.hb = S.he
            return
        got = None if s.route == "tune" and tune else choose_park(g, ready, t)
        if tune and (got is None or s.route == "tune" or got[2] <= A[g].ahead(t, min(n - 1, t + know), t) + 1.0):
            # tune to it; the stick then plays from the live edge and is paused with one key at the next spot (the loop below)
            cost = (s.tune_s + s.load_slow_s) * (2.0 if load > s.load_s + s.load_slow_s + 1 else 1.0)
            S.hg, S.he, S.h_ready, S.h_paused, S.h_kind = g, rng.gauss(0.0, sd) + s.delay_error + S.gd[g], int(math.ceil(t + cost)), False, "park"
            S.hb = S.he
            st.parks += 1
            return
        if got is None:
            if not same:
                S.hg, S.h_ready, S.h_paused = -1, t, False
            return
        p, clip, _ = got
        if lattice:
            p -= rng.randrange(lattice)
        S.hg, S.he, S.h_ready, S.h_paused, S.h_kind = g, rng.gauss(0.0, sd_rw) + s.delay_error + S.gd[g], ready, not lattice, "clip" if clip else "park"
        S.hb = (ready - p) + S.he
        st.parks += 1

    def roll_forward(t: int) -> bool:
        """Move the hidden stick forward on its own game, by 15 s skips and then play, to the next spot it can be landed on.
        A stick that is already behind live needs no fresh rewind for this, so it may end closer to live than `min_behind`."""
        cur = int(t - S.hb + S.he)
        got = choose_park(S.hg, t, t, lo=max(cur + 1, t - reach), hi=t - P.same_behind_s)
        if got is None:
            return False
        move_to(got[0] - (rng.randrange(lattice) if lattice else 0), t)
        return True

    def move_to(p: int, t: int) -> None:
        """15 s skips and then play, to believed position p on the game the hidden stick holds (it then sits paused one
        second short of p)."""
        d = max(0, p - int(t - S.hb + S.he))
        cost = 2.0 + P.press_s * math.floor(d / P.skip_step_s) + (0 if lattice else d % P.skip_step_s)
        ready = int(math.ceil(t + cost))
        S.he = rng.gauss(0.0, sd) + s.delay_error + S.gd[S.hg]
        S.h_ready, S.h_paused, S.hb = ready, not lattice, (ready - p) + S.he
        st.rolls += 1

    if planner is not None:
        planner.bind(A, s, guard=guard, reach=reach, flip=flip, lead=lead, know=know, n=n, stats=st)

    for t in range(t0, t1):
        st.ticks += 1
        if S.hg >= 0 and S.h_paused and t >= S.h_ready:
            S.hb += 1.0
        s_true = int(t - S.sb)
        s_bel = int(t - S.sb + S.se)
        h_ok = S.hg >= 0 and t >= S.h_ready
        h_bel = int(t - S.hb + S.he) if S.hg >= 0 else 0
        # the brain
        stay, best, best_sc, sc_h = -1e9, -1, -1e9, -1e9
        pk, pk_sc, pk_h = -1, -1e9, -1e9          # the same question asked `lookahead_s` on: which game to have parked by then
        for g in range(N):
            x = A[g]
            if g == S.sg:
                stay = (brain.score(g, s_bel, t, True, 0) if t <= x.a1 + S.sb else 0.0) - s.bore_w * max(0, t - S.s_land)
                continue
            if not (x.a0 <= t <= x.a1 + fresh):
                continue
            v = brain.score(g, h_bel, t, False, 0) if (g == S.hg and h_ok) else brain.score(g, t - fresh, t, False, back)
            if g == S.hg:
                sc_h = v
            if v > best_sc:
                best, best_sc = g, v
            if la:      # two looks, 15 s apart, so one play entering or leaving the window does not move the stick
                v = 0.5 * (brain.score(g, t - fresh + la, t, False, back) + brain.score(g, t - fresh + la + 15, t, False, back))
                if g == S.hg:
                    pk_h = v
                if v > pk_sc:
                    pk, pk_sc = g, v
        if not la:
            pk, pk_sc, pk_h = best, best_sc, sc_h
        want = best if best >= 0 and best_sc > stay + 1e-9 else -1
        # the jump goes to the game the hidden stick holds, whenever the brain scores it above the screen by the margin
        target = S.hg if S.hg >= 0 and sc_h > stay + 1e-9 and sc_h - stay >= s.margin else -1
        desire, act = S.hg, None
        if force is not None and t < force[2]:
            kind, fg = force[0], force[1]
            target = fg if kind == "jump" else -1
            desire = fg if kind in ("jump", "park") else S.hg
        elif planner is not None:
            if want >= 0:
                st.wants.append((t, want))
            act = planner.act(t, S)          # (jump now, the hidden stick's game, the spot to pause at, "" | "tune" | "move")
            target = S.hg if act[0] else -1
            desire = act[1] if act[1] >= 0 else S.hg
        else:
            if want >= 0:
                st.wants.append((t, want))
            # the hidden stick: a load in progress is finished; an idle stick moves to the brain's top other game
            if S.hg < 0 or not (A[S.hg].a0 <= t <= A[S.hg].a1 + fresh):
                desire = pk if (s.prepark or want >= 0) else -1
            elif h_ok and target < 0 and pk >= 0 and pk != S.hg and (s.prepark or want >= 0) and pk_sc > pk_h + s.repark_margin:
                desire = pk
        if desire >= 0 and (desire != S.hg or (act is not None and act[3] == "tune")):
            start_park(desire, t)
        elif h_ok and act is not None:
            # a planner's hidden stick: skip forward to the spot, or play on to it and stop there with one pause key
            x = A[S.hg]
            hb_i = min(n - 1, max(0, h_bel))
            if act[3] == "move" and act[2] > hb_i:
                move_to(act[2], t)
            elif act[2] < 0 or hb_i < act[2]:
                S.h_paused = False
            elif not S.h_paused and x.since[hb_i] > guard and x.left[hb_i] >= guard and hb_i + guard <= t + know:
                S.h_paused = True
        elif h_ok and not live:
            x = A[S.hg]
            hb_i = min(n - 1, max(0, h_bel))
            if lattice:
                if x.legal[hb_i] and x.left[hb_i] <= guard and x.since[hb_i] - int(P.skip_step_s) > guard:
                    S.hb += P.skip_step_s          # one step back holds the stick in its dead stretch
                    S.h_ready = t + 3
            far = (S.hb - S.he) > reach + 1
            spot = x.since[hb_i] > guard and x.left[hb_i] >= guard
            if far or (S.h_paused and not spot):
                # parked too long, or on a spot that cannot be landed on: skip forward to the next good spot; with none in
                # reach yet, let the stick play on hidden (it stops falling behind) and pull it inside the reach
                if not roll_forward(t):
                    S.h_paused = False
                    if far:
                        S.he = rng.gauss(0.0, sd) + s.delay_error + S.gd[S.hg]
                        S.hb, S.h_ready = (reach - 2 * P.skip_step_s) + S.he, t + 2 + int(P.press_s * 2)
            elif S.h_paused and s.skip_dead and not lattice and x.left[hb_i] > max(s.land_wait_max, guard + lead + P.skip_step_s) \
                    and hb_i + x.left[hb_i] <= t + know:
                # parked early in a long stoppage whose end the feed now knows: skip to just before the play
                d = x.left[hb_i] - guard - lead
                ready = int(math.ceil(t + 2.0 + P.press_s * math.floor(d / P.skip_step_s) + d % P.skip_step_s))
                p = hb_i + d
                if ready - p >= 0:
                    S.he = rng.gauss(0.0, sd) + s.delay_error + S.gd[S.hg]
                    S.h_ready, S.hb = ready, (ready - p) + S.he
                    st.rolls += 1
            elif not S.h_paused and not lattice and spot and x.since[hb_i] + x.left[hb_i] > need and hb_i + guard <= t + know \
                    and (s.park_at == "start" or (x.left[hb_i] <= guard + lead + 1 and hb_i + x.left[hb_i] <= t + know)):
                S.h_paused = True               # one pause key: a stick playing on hidden stops just before the next play starts
        # the jump
        why = None
        if want >= 0 and force is None:
            why = ("other_game_parked" if S.hg >= 0 and S.hg != want else "nothing_parked" if S.hg < 0 else "loading" if t < S.h_ready
                   else "margin" if target < 0 else "flip_in_progress" if t < S.black else "min_stay" if t - S.s_land < s.min_stay
                   else "past_reach" if not (live or S.hb - S.he <= reach + flip) else None)
            if act is not None and target < 0:
                why = planner.why(t, S, want)
        if target >= 0 and S.hg == target and t >= S.h_ready and t >= S.black and t - S.s_land >= s.min_stay \
                and (live or S.hb - S.he <= reach + flip):
            xs, xh = A[S.sg], A[target]
            i = min(n - 1, max(0, s_bel))
            calm = xs.hlc[min(n, i + guard + 1)] - xs.hlc[max(0, i - guard)] == 0
            leave = (xs.since[i] > guard and xs.left[i] >= guard and calm) or (s.leave_rule == "low_leverage" and calm) or t > xs.a1 + S.sb
            j = min(n - 1, max(0, int(t - S.hb + S.he) + (lead if S.h_paused else flip)))
            if i + guard > t + know or j + guard > t + know:
                leave = False           # the feed has not yet reported the seconds the padding has to cover
            if S.s_live < s.min_live_s and xs.left[i] < P.long_stop_s and t <= xs.a1 + S.sb:
                leave = False           # nothing of this game has been shown yet: a hop through dead air is not a visit
                if why is None and want >= 0 and force is None:
                    why = "min_stay"
            if why is None and want >= 0 and force is None:
                why = ("screen_high_leverage" if not calm else "screen_mid_play" if not leave
                       else "landing_not_dead" if not (xh.since[j] > guard and xh.left[j] >= guard)
                       else "landing_long_wait" if xh.left[j] > s.land_wait_max else None)
            if leave and xh.since[j] > guard and s.land_wait_max >= xh.left[j] >= guard:
                ti = min(n - 1, max(0, s_true if not edge_sd else int(t - S.sb + rng.gauss(0.0, edge_sd))))
                bad = st.mid_leave + st.hl_leave + st.mid_entry
                if not xs.dead[ti]:
                    if xs.legal[ti]:
                        st.walk_leave += 1
                    else:
                        st.mid_leave += 1
                st.hl_leave += xs.hl[ti]
                new_sb = S.hb + (flip - lead if S.h_paused else 0)
                up = t + flip
                ei = min(n - 1, max(0, int(up - new_sb)))
                ej = ei if not edge_sd else min(n - 1, max(0, int(up - new_sb + rng.gauss(0.0, s.stamp_sd if S.h_paused else edge_sd))))
                if not xh.dead[ej]:
                    if xh.legal[ej]:
                        st.walk_entry += 1
                    else:
                        st.mid_entry += 1
                st.hops.append(t)
                st.flips.append((t, target, st.mid_leave + st.hl_leave + st.mid_entry - bad))
                if S.s_live >= 3 and st.hops[:-1] and S.s_land - flip == st.hops[-2]:
                    st.jumps.append(st.hops[-2])       # the flip that began the visit now ending: it showed play, so it counts
                st.ups.append((up, target))
                st.visits.append(t - S.s_land)
                st.play_visits += int(S.s_live >= 3)
                S.s_live = 0
                old = (S.sg, S.sb, S.se)
                S.sg, S.sb, S.se, S.s_land, S.s_from, S.s_kind, S.black = target, new_sb, S.he, up, ei, S.h_kind, up
                if live:
                    S.hg, S.hb, S.he, S.h_ready, S.h_paused, S.h_kind = old[0], old[1], old[2], t, False, "pp"
                elif s.ping_pong or act is not None:
                    S.hg, S.hb, S.he, S.h_ready, S.h_paused, S.h_kind = old[0], old[1], old[2], t, not lattice, "pp"
                    if s.skip_dead and not lattice and act is None:
                        end = i + xs.left[i]
                        p = end - guard - lead
                        if end <= t + int(s.feed_lead) and p - i >= P.skip_step_s and end - xs.starts[bisect.bisect_right(xs.starts, i) - 1] >= need:
                            ready = int(math.ceil(t + 2.0 + P.press_s * math.ceil((p - i) / P.skip_step_s)))
                            if ready - p >= s.min_behind:
                                S.he = rng.gauss(0.0, sd) + s.delay_error + S.gd[S.hg]
                                S.h_ready, S.hb = ready, (ready - p) + S.he
                else:
                    S.hg, S.h_ready, S.h_paused = -1, t, False
                continue
        if want >= 0 and force is None:
            ok = A[S.sg].leave_in(guard, s.leave_rule == "low_leverage")
            st.leave_soon += int(ok[min(n - 1, max(0, s_bel))] <= P.yes_s - flip)
        if why:
            st.why[why] = st.why.get(why, 0) + 1
            st.why_t[t] = why
        # what the screen shows this second
        if t >= S.black:
            xs = A[S.sg]
            ti = min(n - 1, max(0, s_true))
            st.pic += 1
            if xs.dead[ti]:
                if xs.brk[ti]:
                    st.brk_s += 1
                else:
                    st.dead_s += 1
            else:
                st.live_s += 1
                S.s_live += 1
            st.behind.append(S.sb)
            if (S.sg, ti) not in st.shown:
                if S.s_from <= ti - P.seen_lead_s and (ti in xs.mom or ti in xs.v2):
                    st.shown.add((S.sg, ti))
                    v = xs.mom.get(ti)
                    if v:
                        st.seen["clip" if S.s_kind == "clip" else "live" if S.sb <= P.live_behind_s else "parked"] += v
                    v = xs.v2.get(ti)
                    if v and v > 0:
                        st.v2_seen += v
                st.brain_value += xs.cumv[ti + 1] - xs.cumv[ti]
    return st, S


def served(st: Stats) -> list[bool]:
    """Per wanted second: was that game's picture up within `yes_s` seconds (the audit's yes)."""
    ups: dict[int, list[int]] = {}
    for at, g in st.ups:
        ups.setdefault(g, []).append(at)
    out = []
    for t, g in st.wants:
        u = ups.get(g, ())
        i = bisect.bisect_right(u, t)
        out.append(i < len(u) and u[i] <= t + P.yes_s)
    return out


def yes_rate(st: Stats) -> tuple[int, int]:
    return sum(served(st)), len(st.wants)


def delivered(st: Stats, sched: list | None = None) -> int:
    """A planner's scheduled jumps that happened: onto the planned game, within `plan_ok_s` seconds of the planned second, with no
    Rule #1 break on that flip."""
    by: dict[int, list[tuple[int, int]]] = {}
    for t, g, bad in st.flips:
        by.setdefault(g, []).append((t, bad))
    n = 0
    for _, at, g in (st.sched if sched is None else sched):
        fl = by.get(g, ())
        i = bisect.bisect_left(fl, (at - P.plan_ok_s, -1))
        n += int(any(bad == 0 for t, bad in fl[i:i + 4] if t <= at + P.plan_ok_s))
    return n


def summarize(runs: list[tuple[Night, Stats]]) -> dict:
    """One row: pace per minute and per window (median/10th pct/max), yes-rate, big plays, behind live, Rule #1 counters."""
    mins = sum((n.span[1] - n.span[0]) / 60.0 for n, _ in runs)
    out: dict = {"nights": len(runs), "minutes": round(mins, 1)}
    nj = sum(len(x.jumps) for _, x in runs)
    out["jumps_per_min"] = round(nj / mins, 3) if mins else 0.0
    for w in (1,) + WINDOWS_MIN:
        counts = []
        for n, x in runs:
            a, b = n.span
            t = a
            while t + w * 60 <= b:
                counts.append(bisect.bisect_left(x.jumps, t + w * 60) - bisect.bisect_left(x.jumps, t))
                t += 30
        out[f"per_{w}m_med"], out[f"per_{w}m_p10"], out[f"per_{w}m_max"] = (pct(counts, 0.5), pct(counts, 0.1), max(counts)) if counts else ("", "", "")
    yes = [yes_rate(x) for _, x in runs]
    wants = sum(y[1] for y in yes)
    out["wanted_s_per_min"] = round(wants / mins, 2) if mins else 0.0
    out["yes_rate"] = round(sum(y[0] for y in yes) / wants, 4) if wants else ""
    out["yes_ceiling"] = round(sum(x.leave_soon for _, x in runs) / wants, 4) if wants else ""   # the screen game offered a legal leave in time
    big = sum(n.big_total for n, _ in runs)
    for k in ("live", "parked", "clip"):
        out[f"big_{k}"] = round(sum(x.seen[k] for _, x in runs) / big, 4) if big else ""
    out["big_seen"] = round(sum(sum(x.seen.values()) for _, x in runs) / big, 4) if big else ""
    v2 = sum(n.v2_total for n, _ in runs)
    out["v2_seen"] = round(sum(x.v2_seen for _, x in runs) / v2, 4) if v2 else ""
    out["brain_value"] = round(sum(x.brain_value for _, x in runs), 1)
    beh = sorted(v for _, x in runs for v in x.behind)
    out["behind_med_s"] = round(beh[len(beh) // 2], 1) if beh else ""
    out["behind_p90_s"] = round(beh[int(0.9 * (len(beh) - 1))], 1) if beh else ""
    out["behind_max_s"] = round(beh[-1], 1) if beh else ""
    sched = sum(len(x.sched) for _, x in runs)
    out["planned_jumps"] = sched          # a planner's scheduled jumps, and the share that happened as planned
    out["planned_delivered"] = round(sum(delivered(x) for _, x in runs) / sched, 4) if sched else ""
    firm = sum(len(x.firm) for _, x in runs)
    out["firm_jumps"] = firm              # those logged inside the commit window (a few seconds ahead), and their share
    out["firm_delivered"] = round(sum(delivered(x, x.firm) for _, x in runs) / firm, 4) if firm else ""
    pic = sum(x.pic for _, x in runs) or 1
    out["live_share"] = round(sum(x.live_s for _, x in runs) / pic, 3)
    out["dead_share"] = round(sum(x.dead_s for _, x in runs) / pic, 3)
    out["commercial_share"] = round(sum(x.brk_s for _, x in runs) / pic, 3)
    vis = [v for _, x in runs for v in x.visits]
    out["visit_med_s"] = statistics.median(vis) if vis else ""
    out["play_visit_share"] = round(sum(x.play_visits for _, x in runs) / len(vis), 3) if vis else ""   # visits that showed 3 s or more of live play
    for k in ("mid_entry", "mid_leave", "hl_leave", "walk_entry", "walk_leave", "parks", "rolls", "load_fail"):
        out[k] = sum(getattr(x, k) for _, x in runs)
    out["jumps"] = nj
    out["hops_per_min"] = round(sum(len(x.hops) for _, x in runs) / mins, 3) if mins else 0.0   # every flip, dead-air hops included
    why: dict[str, int] = {}
    for _, x in runs:
        for k, v in x.why.items():
            why[k] = why.get(k, 0) + v
    for k in WHY:
        out[f"why_{k}"] = why.get(k, 0)
    # the same reasons counted over the REFUSED seconds only (wanted, and that game's picture was not up within `yes_s` seconds)
    ref: dict[str, int] = {}
    for _, x in runs:
        for (t, _g), ok in zip(x.wants, served(x)):
            if not ok:
                k = x.why_t.get(t, "jumped_elsewhere")
                ref[k] = ref.get(k, 0) + 1
    out["refused_s"] = sum(ref.values())
    for k in WHY + ("jumped_elsewhere",):
        out[f"ref_{k}"] = ref.get(k, 0)
    return out
