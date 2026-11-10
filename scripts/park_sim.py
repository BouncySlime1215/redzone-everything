"""PARK step 1: a fast simulator of the two-stick show on recorded nights, driven by the grader's own facts.

    nice -n 19 python scripts/park_sim.py --leagues nba --games 4 --cap 2 --brains hindsight foresight heuristic
    nice -n 19 python scripts/park_sim.py --leagues nba nfl --sweep 2000 --csv sweep.csv      # random settings, every row kept
    --set pos_err=3 --set ping_pong=false      one setting for this run (repeatable)

No TV, no adb, no engine, no network, dev (`fit`) nights only. One night under one setting runs in well under a second.

The show. Two sticks. The SCREEN stick shows a game some seconds behind the stream's live edge. The HIDDEN stick is being
loaded onto a game, or sits PARKED there: paused just before a play starts (`park_at=end`, one-play visits) or just after
a whistle (`park_at=start`). Because a parked stick is behind live, the play-by-play feed is ahead of its picture, so the
simulator may read each game's facts up to `feed_lead` seconds past the live edge and no further.
Each second a brain scores every game (any object with `score(g, c, t, on_screen, back)`); the game it scores above the
one on screen is what it WANTS. The controller then: jumps to the parked game (legal only when the screen game's believed
position is at least `guard` seconds inside a dead stretch and not in a high-leverage play, or with `leave_rule=low_leverage`
anywhere outside high leverage; and the parked position is at least `guard` inside a dead stretch); or re-parks the hidden
stick on the wanted game (costs `load_s`); or keeps the stick it just left paused where the viewer left it (`ping_pong`)
and skips its dead time forward (`skip_dead`).
`mode=live` is today's physics for comparison: both sticks at the live edge, the hidden one tuned to the wanted game, a
jump only when the screen game is dead now and the target is dead when its picture comes up.

Rule #1 is judged by the grader's facts at the TRUE positions, which differ from what the controller believes: every
park draws a position error (normal, sd 0.6 x `pos_err`, so "right within about 5 s" is sd 3) plus the fixed `delay_error`.
Counters: mid-play entries, mid-play leaves, high-leverage leaves (and, with the any-shot rule, entries and leaves in the
4 s walk after a shot, counted apart as the Nov 8 decision says).
`pause_holds=false` models "only scrub positions hold": a hidden stick cannot stop, it lands on a 15 s lattice and is held
in its dead stretch by one 15 s step back at a time.

yes-rate: of the seconds the brain wanted another game, the share where that game's picture was up within 20 s (the audit's
definition). Big plays: the audit's must-see moments (a score 5, a red-zone trip or turnover 3, others 1), seen when the
picture was on the game from 7 s before the moment; split by how it was seen (near live, parked behind live, a clip of a
play the feed had already reported).

Why: Nick wants many jumps, a yes-rate near 95% and zero mid-play cuts; the pace, the yes-rate and the Rule #1 counters of
every PARK setting have to be measurable in under a second so thousands of settings can be compared before anything is wired
into the live decider.
"""
from __future__ import annotations

import argparse
import bisect
import copy
import csv
import json
import math
import random
import statistics
import sys
import time
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT.as_posix(), (ROOT / "scripts").as_posix()):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import park_ceiling as pc  # noqa: E402

RUN_UP_S = 75        # veto_audit.RUN_UP_S: how far ahead a brain's value looks
SEEN_LEAD_S = 7      # veto_audit.PLAY_LEN_S: the picture must be up this long before a moment to have seen it
YES_S = 20           # veto_audit.YES_S
FEED_LEAD_S = 27     # the feed runs this far ahead of the stream's live edge (stream delay 35 s less feed lag 8 s)
TUNE_TICKS = 3       # a tune within YouTube TV: 2.3-3.0 s
LIVE_BEHIND_S = 60   # a big play seen this close to the live edge counts as seen live
SAME_BEHIND_S = 10   # a stick already behind live may skip forward to this close to the live edge
PAD_S = 400
WHY = ("other_game_parked", "nothing_parked", "loading", "margin", "flip_in_progress", "min_stay", "past_reach", "screen_high_leverage",
       "screen_mid_play", "landing_not_dead")


@dataclass(frozen=True)
class Setting:
    mode: str = "park"            # park | live (today's physics: both sticks at the live edge)
    pause_holds: bool = True      # False: only scrub positions hold
    load_s: float = 19.3          # park load on a new game (18.5-30 s measured)
    pos_err: float = 5.0          # the position label is right within about this (sd = 0.6 x)
    guard: float = -1.0           # seconds kept from every edge of a dead stretch; -1 = pos_err
    delay_error: float = 0.0      # every true position is this much later than believed (the audit's --delay-error)
    min_dead: float = 10.0        # shortest dead stretch worth parking on
    max_behind: float = 175.0     # how far behind the live edge a stick may sit
    min_behind: float = 45.0      # smallest rewind
    ping_pong: bool = True        # the stick being left stays on its game, paused where the viewer left
    min_stay: float = 0.0         # seconds on a game before the next jump
    margin: float = 0.0           # brain points the parked game must lead the screen game by before a jump
    repark_margin: float = 1.0    # brain points another game must lead the parked one by before the hidden stick moves
    lookahead_s: float = 0.0      # the hidden stick is parked on the game the brain will score highest this many seconds on
    skip_dead: bool = True        # the left stick skips its dead time forward to the next play
    clips: bool = False           # park back to a play the feed already reported (up to the 175 s reach)
    leave_rule: str = "dead"      # dead | low_leverage
    park_at: str = "end"          # end = just before the play (R3) | start = just after the whistle
    park_target: str = "value"    # value = before the best known moment in reach | recent = the latest dead stretch
    shots: bool = False           # R2: the 4 s after any shot is a legal point (basketball)
    flip: float = 2.05
    feed_lead: float = FEED_LEAD_S
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
                legal[si:si + int(pc.SHOT_WINDOW_S)] = b"\x01" * len(legal[si:si + int(pc.SHOT_WINDOW_S)])
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
                if calm and (low_leverage or (self.since[i] > guard and self.left[i] > guard)):
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
        return sv * (1.0 + 0.5 * c / RUN_UP_S) - (0.5 / RUN_UP_S) * (self.cumvt[hi + 1] - self.cumvt[lo + 1]) if sv else 0.0


class Night:
    def __init__(self, games: list[dict]):
        self.games = games
        self.base = int(min(g["t0"] for g in games)) - PAD_S
        self.n = int(max(g["t1"] for g in games) - self.base) + PAD_S
        a, b = pc.span(games)
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


# ---- brains: anything with score(g, c, t, on_screen, back) -> float ------------------------------------------------

class Hindsight:
    """Knows the future: the must-see value about to air on the game from picture position c (the audit's hindsight brain)."""
    name = "hindsight"

    def bind(self, arrs: list[Arr], s: Setting) -> None:
        self.A = arrs

    def score(self, g: int, c: int, t: int, on_screen: bool, back: int = 0) -> float:
        return self.A[g].ahead(c - back + (0 if on_screen else SEEN_LEAD_S), c + RUN_UP_S, c)


class Foresight:
    """No more than the app could know: only moments the feed has already reported (a stick behind live sees the feed ahead of
    its picture), plus a little of the score-and-clock leverage so it has a view when nothing is reported yet."""
    name = "foresight"

    def bind(self, arrs: list[Arr], s: Setting) -> None:
        self.A, self.lead = arrs, int(s.feed_lead)

    def score(self, g: int, c: int, t: int, on_screen: bool, back: int = 0) -> float:
        a = self.A[g]
        known = min(a.n - 1, t + self.lead)
        return a.ahead(c - back + (0 if on_screen else SEEN_LEAD_S), min(c + RUN_UP_S, known), c) + 0.2 * a.lev[min(max(c, 0), known)]


class Heuristic:
    """No future at all: the value2 leverage of the score and the clock, as the feed last reported it."""
    name = "heuristic"

    def bind(self, arrs: list[Arr], s: Setting) -> None:
        self.A, self.lead = arrs, int(s.feed_lead)

    def score(self, g: int, c: int, t: int, on_screen: bool, back: int = 0) -> float:
        a = self.A[g]
        return a.lev[min(a.n - 1, t + self.lead)]


BRAINS = {"hindsight": Hindsight, "foresight": Foresight, "heuristic": Heuristic}


class State:
    __slots__ = ("sg", "sb", "se", "s_land", "s_from", "s_kind", "hg", "hb", "he", "h_ready", "h_paused", "h_kind", "black", "rng")


class Stats:
    def __init__(self):
        self.jumps, self.wants, self.ups, self.behind, self.visits = [], [], [], [], []
        self.seen = {"live": 0.0, "parked": 0.0, "clip": 0.0}
        self.pic = self.live_s = self.dead_s = self.brk_s = 0
        self.mid_entry = self.mid_leave = self.hl_leave = self.walk_entry = self.walk_leave = 0
        self.parks = self.load_fail = self.ticks = self.rolls = self.leave_soon = 0
        self.v2_seen = self.brain_value = 0.0
        self.why: dict[str, int] = {}          # per second the brain wanted another game and no jump went: the first thing in the way
        self.shown: set[tuple[int, int]] = set()   # (game, second) of every valued moment already counted: a play watched twice counts once


def run(night: Night, brain, s: Setting, *, t0: int | None = None, t1: int | None = None, state: State | None = None,
        stats: Stats | None = None, force: tuple | None = None) -> tuple[Stats, State]:
    """Play the night (or t0..t1 from a saved state). `force` = (kind, game, until): until that second the controller is told
    what to do ('stay', 'park' the hidden stick on a game, or 'jump' to it) instead of asking the brain."""
    A = night.arrs(s.shots, s.value, s.live_w)
    N, n = len(A), night.n
    a, b = night.span
    t0, t1 = (a if t0 is None else t0), (b if t1 is None else t1)
    live = s.mode == "live"
    guard = int(round(s.pos_err if s.guard < 0 else s.guard))
    sd = 0.6 * s.pos_err
    flip = max(1, int(round(s.flip)))
    lattice = 0 if (s.pause_holds or live) else int(pc.SKIP_STEP_S)
    fresh = 0 if live else int(s.min_behind)
    reach = int(pc.REACH_S if s.clips else s.max_behind)
    back, la = 0, int(s.lookahead_s)
    need = max(int(s.min_dead), 2 * guard + lattice + 1)
    st = stats or Stats()
    brain.bind(A, s)
    S = state
    if S is None:
        S = State()
        S.rng = random.Random(s.seed)
        sc0 = [brain.score(g, t0 - fresh, t0, False, 0) if A[g].a0 <= t0 <= A[g].a1 else -1e9 for g in range(N)]
        S.sg = max(range(N), key=lambda g: sc0[g])
        S.sb, S.se, S.s_land, S.s_from, S.s_kind = float(fresh), S.rng.gauss(0.0, sd) + s.delay_error, t0, t0 - fresh, "park"
        S.sb += S.se
        S.hg, S.hb, S.he, S.h_ready, S.h_paused, S.h_kind, S.black = -1, 0.0, 0.0, t0, False, "park", t0
    rng = S.rng

    def choose_park(g: int, ready: int, t: int, lo: int | None = None, hi: int | None = None) -> tuple[int, bool] | None:
        """(believed position to park at, is it a clip of an already reported moment) for a stick ready at `ready`."""
        x = A[g]
        lo, hi = (ready - reach if lo is None else lo), (ready - fresh if hi is None else hi)
        if s.park_at == "end":
            i, j = bisect.bisect_left(x.ends, lo + guard), bisect.bisect_right(x.ends, hi + guard)
        else:
            i, j = bisect.bisect_left(x.starts, lo - guard), bisect.bisect_right(x.starts, hi - guard)
        best = None
        known = min(n - 1, t + int(s.feed_lead))
        for k in range(j - 1, i - 1, -1):
            if x.ends[k] - x.starts[k] < need or x.ends[k] > known:
                continue
            p = x.ends[k] - guard - 1 if s.park_at == "end" else x.starts[k] + guard + 1
            if s.park_target == "recent":
                return p, False
            v = x.ahead(p, min(p + RUN_UP_S, known), p)
            if best is None or v > best[0] + 1e-9:
                best = (v, p)
        if best is None:
            return None
        return best[1], bool(s.clips and best[0] > 0 and best[1] < t - int(s.max_behind))

    def start_park(g: int, t: int) -> None:
        same = g == S.hg
        load = TUNE_TICKS if live else (pc.RESCRUB_1_S if same else s.load_s) + s.load_slow_s
        if s.load_fail_p and rng.random() < s.load_fail_p:
            load *= 2.0
            st.load_fail += 1
        ready = int(math.ceil(t + load + (0 if (live or lattice) else pc.SKIP_STEP_S / 2.0)))
        if live:
            S.hg, S.he, S.h_ready, S.h_paused, S.h_kind = g, rng.gauss(0.0, sd) + s.delay_error, ready, False, "park"
            S.hb = S.he
            return
        got = choose_park(g, ready, t)
        if got is None:
            if not same:
                S.hg, S.h_ready, S.h_paused = -1, t, False
            return
        p, clip = got
        if lattice:
            p -= rng.randrange(lattice)
        S.hg, S.he, S.h_ready, S.h_paused, S.h_kind = g, rng.gauss(0.0, sd) + s.delay_error, ready, not lattice, "clip" if clip else "park"
        S.hb = (ready - p) + S.he
        st.parks += 1

    def roll_forward(t: int) -> bool:
        """Move the hidden stick forward on its own game, by 15 s skips and then play, to the next spot it can be landed on.
        A stick that is already behind live needs no fresh rewind for this, so it may end closer to live than `min_behind`."""
        cur = int(t - S.hb + S.he)
        got = choose_park(S.hg, t, t, lo=max(cur + 1, t - reach), hi=t - SAME_BEHIND_S)
        if got is None:
            return False
        p = got[0] - (rng.randrange(lattice) if lattice else 0)
        d = max(0, p - cur)
        cost = 2.0 + pc.PRESS_S * math.floor(d / pc.SKIP_STEP_S) + (0 if lattice else d % pc.SKIP_STEP_S)
        ready = int(math.ceil(t + cost))
        S.he = rng.gauss(0.0, sd) + s.delay_error
        S.h_ready, S.h_paused, S.hb = ready, not lattice, (ready - p) + S.he
        st.rolls += 1
        return True

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
                stay = brain.score(g, s_bel, t, True, 0) if t <= x.a1 + S.sb else 0.0
                continue
            if not (x.a0 <= t <= x.a1 + fresh):
                continue
            v = brain.score(g, h_bel, t, False, 0) if (g == S.hg and h_ok) else brain.score(g, t - fresh, t, False, back)
            if g == S.hg:
                sc_h = v
            if v > best_sc:
                best, best_sc = g, v
            if la:
                v = brain.score(g, t - fresh + la, t, False, back)
                if g == S.hg:
                    pk_h = v
                if v > pk_sc:
                    pk, pk_sc = g, v
        if not la:
            pk, pk_sc, pk_h = best, best_sc, sc_h
        want = best if best >= 0 and best_sc > stay + 1e-9 else -1
        # the jump goes to the game the hidden stick holds, whenever the brain scores it above the screen by the margin
        target = S.hg if S.hg >= 0 and sc_h > stay + 1e-9 and sc_h - stay >= s.margin else -1
        desire = S.hg
        if force is not None and t < force[2]:
            kind, fg = force[0], force[1]
            target = fg if kind == "jump" else -1
            desire = fg if kind in ("jump", "park") else S.hg
        else:
            if want >= 0:
                st.wants.append((t, want))
            # the hidden stick: a load in progress is finished; an idle stick moves to the brain's top other game
            if S.hg < 0 or not (A[S.hg].a0 <= t <= A[S.hg].a1 + fresh):
                desire = pk if (s.prepark or want >= 0) else -1
            elif h_ok and pk >= 0 and pk != S.hg and (s.prepark or want >= 0) and pk_sc > pk_h + s.repark_margin:
                desire = pk
        if desire >= 0 and desire != S.hg:
            start_park(desire, t)
        elif h_ok and not live:
            x = A[S.hg]
            hb_i = min(n - 1, max(0, h_bel))
            if lattice:
                if x.legal[hb_i] and x.left[hb_i] <= guard + 1 and x.since[hb_i] - int(pc.SKIP_STEP_S) > guard:
                    S.hb += pc.SKIP_STEP_S          # one step back holds the stick in its dead stretch
                    S.h_ready = t + 3
            far = (S.hb - S.he) > reach + 1
            spot = x.since[hb_i] > guard and x.left[hb_i] > guard
            if far or (S.h_paused and not spot):
                # parked too long, or on a spot that cannot be landed on: skip forward to the next good spot; with none in
                # reach yet, let the stick play on hidden (it stops falling behind) and pull it inside the reach
                if not roll_forward(t):
                    S.h_paused = False
                    if far:
                        S.he = rng.gauss(0.0, sd) + s.delay_error
                        S.hb, S.h_ready = (reach - 2 * pc.SKIP_STEP_S) + S.he, t + 2 + int(pc.PRESS_S * 2)
            elif not S.h_paused and not lattice and spot and x.left[hb_i] <= guard + 2 and x.since[hb_i] + x.left[hb_i] > need:
                S.h_paused = True               # a stick playing on hidden stops just before the next play starts
        # the jump
        why = None
        if want >= 0 and force is None:
            why = ("other_game_parked" if S.hg >= 0 and S.hg != want else "nothing_parked" if S.hg < 0 else "loading" if t < S.h_ready
                   else "margin" if target < 0 else "flip_in_progress" if t < S.black else "min_stay" if t - S.s_land < s.min_stay
                   else "past_reach" if not (live or S.hb - S.he <= reach + flip) else None)
        if target >= 0 and S.hg == target and t >= S.h_ready and t >= S.black and t - S.s_land >= s.min_stay \
                and (live or S.hb - S.he <= reach + flip):
            xs, xh = A[S.sg], A[target]
            i = min(n - 1, max(0, s_bel))
            calm = xs.hlc[min(n, i + guard + 1)] - xs.hlc[max(0, i - guard)] == 0
            leave = (xs.since[i] > guard and xs.left[i] > guard and calm) or (s.leave_rule == "low_leverage" and calm) or t > xs.a1 + S.sb
            j = min(n - 1, max(0, int(t - S.hb + S.he) + (0 if S.h_paused else flip)))
            if why is None and want >= 0 and force is None:
                why = ("screen_high_leverage" if not calm else "screen_mid_play" if not leave
                       else "landing_not_dead" if not (xh.since[j] > guard and xh.left[j] > guard) else None)
            if leave and xh.since[j] > guard and xh.left[j] > guard:
                ti = min(n - 1, max(0, s_true))
                if not xs.dead[ti]:
                    if xs.legal[ti]:
                        st.walk_leave += 1
                    else:
                        st.mid_leave += 1
                st.hl_leave += xs.hl[ti]
                new_sb = S.hb + (flip if S.h_paused else 0)
                up = t + flip
                ei = min(n - 1, max(0, int(up - new_sb)))
                if not xh.dead[ei]:
                    if xh.legal[ei]:
                        st.walk_entry += 1
                    else:
                        st.mid_entry += 1
                st.jumps.append(t)
                st.ups.append((up, target))
                st.visits.append(t - S.s_land)
                old = (S.sg, S.sb, S.se)
                S.sg, S.sb, S.se, S.s_land, S.s_from, S.s_kind, S.black = target, new_sb, S.he, up, ei, S.h_kind, up
                if live:
                    S.hg, S.hb, S.he, S.h_ready, S.h_paused, S.h_kind = old[0], old[1], old[2], t, False, "pp"
                elif s.ping_pong:
                    S.hg, S.hb, S.he, S.h_ready, S.h_paused, S.h_kind = old[0], old[1], old[2], t, not lattice, "pp"
                    if s.skip_dead and not lattice:
                        end = i + xs.left[i]
                        p = end - guard - 1
                        if end <= t + int(s.feed_lead) and p - i >= pc.SKIP_STEP_S and end - xs.starts[bisect.bisect_right(xs.starts, i) - 1] >= need:
                            ready = int(math.ceil(t + 2.0 + pc.PRESS_S * math.ceil((p - i) / pc.SKIP_STEP_S)))
                            if ready - p >= s.min_behind:
                                S.he = rng.gauss(0.0, sd) + s.delay_error
                                S.h_ready, S.hb = ready, (ready - p) + S.he
                else:
                    S.hg, S.h_ready, S.h_paused = -1, t, False
                continue
        if want >= 0 and force is None:
            ok = A[S.sg].leave_in(guard, s.leave_rule == "low_leverage")
            st.leave_soon += int(ok[min(n - 1, max(0, s_bel))] <= YES_S - flip)
        if why:
            st.why[why] = st.why.get(why, 0) + 1
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
            st.behind.append(S.sb)
            if (S.sg, ti) not in st.shown:
                if S.s_from <= ti - SEEN_LEAD_S and (ti in xs.mom or ti in xs.v2):
                    st.shown.add((S.sg, ti))
                    v = xs.mom.get(ti)
                    if v:
                        st.seen["clip" if S.s_kind == "clip" else "live" if S.sb <= LIVE_BEHIND_S else "parked"] += v
                    v = xs.v2.get(ti)
                    if v and v > 0:
                        st.v2_seen += v
                st.brain_value += xs.cumv[ti + 1] - xs.cumv[ti]
    return st, S


def yes_rate(st: Stats) -> tuple[int, int]:
    ups: dict[int, list[int]] = {}
    for at, g in st.ups:
        ups.setdefault(g, []).append(at)
    yes = 0
    for t, g in st.wants:
        u = ups.get(g, ())
        i = bisect.bisect_right(u, t)
        yes += int(i < len(u) and u[i] <= t + YES_S)
    return yes, len(st.wants)


def summarize(runs: list[tuple[Night, Stats]]) -> dict:
    """One row: pace per minute and per window (median/10th pct/max), yes-rate, big plays, behind live, Rule #1 counters."""
    mins = sum((n.span[1] - n.span[0]) / 60.0 for n, _ in runs)
    out: dict = {"nights": len(runs), "minutes": round(mins, 1)}
    nj = sum(len(x.jumps) for _, x in runs)
    out["jumps_per_min"] = round(nj / mins, 3) if mins else 0.0
    for w in (1,) + pc.WINDOWS_MIN:
        counts = []
        for n, x in runs:
            a, b = n.span
            t = a
            while t + w * 60 <= b:
                counts.append(bisect.bisect_left(x.jumps, t + w * 60) - bisect.bisect_left(x.jumps, t))
                t += 30
        out[f"per_{w}m_med"], out[f"per_{w}m_p10"], out[f"per_{w}m_max"] = (pc.pct(counts, 0.5), pc.pct(counts, 0.1), max(counts)) if counts else ("", "", "")
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
    pic = sum(x.pic for _, x in runs) or 1
    out["live_share"] = round(sum(x.live_s for _, x in runs) / pic, 3)
    out["dead_share"] = round(sum(x.dead_s for _, x in runs) / pic, 3)
    out["commercial_share"] = round(sum(x.brk_s for _, x in runs) / pic, 3)
    vis = [v for _, x in runs for v in x.visits]
    out["visit_med_s"] = statistics.median(vis) if vis else ""
    for k in ("mid_entry", "mid_leave", "hl_leave", "walk_entry", "walk_leave", "parks", "rolls", "load_fail"):
        out[k] = sum(getattr(x, k) for _, x in runs)
    out["jumps"] = nj
    why: dict[str, int] = {}
    for _, x in runs:
        for k, v in x.why.items():
            why[k] = why.get(k, 0) + v
    for k in WHY:
        out[f"why_{k}"] = why.get(k, 0)
    return out


def nights_for(league: str, n_games: int, cap: int, cache: Path | None) -> list[Night]:
    out = []
    for night in pc.load_nights(league, cap, cache):
        sl = pc.slate(night, n_games)
        if sl:
            out.append(Night(sl))
    return out


def evaluate(nights: list[Night], brain_name: str, s: Setting) -> dict:
    """One setting under one brain. With a delay error, both signs are run and the worse arm (more Rule #1 breaks, then the
    lower yes-rate) is the row, as the audit does."""
    arms = [s] if not s.delay_error else [s, replace(s, delay_error=-s.delay_error)]
    rows = []
    for arm in arms:
        brain = BRAINS[brain_name]()
        rows.append(summarize([(n, run(n, brain, arm)[0]) for n in nights]))
    return max(rows, key=lambda r: (r["mid_entry"] + r["mid_leave"] + r["hl_leave"], -(r["yes_rate"] or 0.0)))


GRID = {
    "pause_holds": [True, False], "load_s": [18.5, 25.0, 30.0], "pos_err": [1.0, 3.0, 5.0, 8.0], "guard_x": [1.0, 1.5, 2.0, 2.5],
    "min_dead": [8.0, 12.0, 20.0, 30.0], "max_behind": [60.0, 90.0, 120.0, 175.0], "ping_pong": [True, False],
    "min_stay": [0.0, 8.0, 15.0, 30.0], "margin": [0.0, 1.0, 3.0], "skip_dead": [True, False], "clips": [True, False],
    "leave_rule": ["dead", "low_leverage"], "park_at": ["end", "start"], "park_target": ["value", "recent"],
    "shots": [False, True], "prepark": [True, False], "delay_error": [0.0, 3.0], "live_w": [0.0, 0.05, 0.15],
    "repark_margin": [0.0, 1.0, 3.0], "value": ["dense", "v1"], "lookahead_s": [0.0, 15.0, 30.0, 45.0],
}


def draw(rng: random.Random, league: str) -> Setting:
    """One random setting from the grid (guard = guard_x x pos_err; the any-shot rule only for basketball)."""
    d = {k: rng.choice(v) for k, v in GRID.items()}
    gx = d.pop("guard_x")
    d["guard"] = round(gx * d["pos_err"], 1)
    if league not in ("nba", "cbb", "wcbb"):
        d["shots"] = False
    return Setting(**d)


def parse_sets(items: list[str]) -> dict:
    out, types = {}, {f.name: f.type for f in fields(Setting)}
    for it in items or ():
        k, v = it.split("=", 1)
        ty = types[k]
        out[k] = v.lower() in ("1", "true", "yes", "on") if ty == "bool" else float(v) if ty == "float" else int(v) if ty == "int" else v
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--leagues", nargs="+", default=["nba"])
    ap.add_argument("--games", nargs="+", type=int, default=[4])
    ap.add_argument("--cap", type=int, default=2, help="dev nights per league")
    ap.add_argument("--brains", nargs="+", default=["hindsight", "foresight"], choices=sorted(BRAINS))
    ap.add_argument("--set", action="append", default=[], help="key=value on Setting (repeatable)")
    ap.add_argument("--sweep", type=int, default=0, help="this many random settings from GRID per league and game count")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--cache", default=None, help="a folder OUTSIDE the repo for the per-game facts")
    ap.add_argument("--csv", default=None)
    a = ap.parse_args()
    base = Setting(**parse_sets(a.set))
    names = [f.name for f in fields(Setting)]
    fh = w = None
    rng = random.Random(a.seed)
    for lg in a.leagues:
        for ng in a.games:
            nights = nights_for(lg, ng, a.cap, Path(a.cache) if a.cache else None)
            if not nights:
                continue
            todo = [("baseline_live", replace(base, mode="live")), ("base", base)] if not a.sweep else \
                [("baseline_live", Setting(mode="live")), ("default", Setting())] + [(f"s{i}", draw(rng, lg)) for i in range(a.sweep)]
            for tag, s in todo:
                for bn in a.brains:
                    t_start = time.time()
                    row = {"league": lg, "games": ng, "brain": bn, "tag": tag, **asdict(s), **evaluate(nights, bn, s)}
                    row["run_s"] = round(time.time() - t_start, 3)
                    if a.csv:
                        if w is None:
                            fh = open(a.csv, "a", newline="")
                            w = csv.DictWriter(fh, fieldnames=list(row))
                            if fh.tell() == 0:
                                w.writeheader()
                        w.writerow(row)
                        fh.flush()
                    if not a.sweep:
                        print(json.dumps({k: v for k, v in row.items() if k not in names or k in ("mode",)}))
    if fh:
        fh.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
