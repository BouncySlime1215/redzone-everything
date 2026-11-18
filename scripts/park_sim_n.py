"""PARK simulator for N streams and an honest feed: a script-side copy of the two-stick simulator as it stood on Nov 10.

    used by scripts/park_profiles.py and tests/test_park_profiles.py; park_plan_n.py is its planner

THIS IS A COPY, kept apart on purpose. The simulator core and the planner have since moved into redzone/noggin
(park_core.py, park_plan.py) and were reworked there for the app's own controller (its show layer, its step times, its
limit on open dead time). The changes below (an honest feed, any number of streams, a forward seek, a show delay, device
step times) were written against the Nov 10 code. Folding them into the product planner is a design decision for Nick
and the coordinator, not a merge; until it is made, this file and park_plan_n.py let the profile table run without
touching the planner the app runs. With the new settings at their defaults this copy is the Nov 10 simulator second for
second (tests/test_park_profiles.py pins that). It is NOT the planner the app runs today.

Everything below this line is the Nov 10 docstring, with the paragraph on the new settings.

PARK step 1: a fast simulator of the two-stick show on recorded nights, driven by the grader's own facts.

    nice -n 19 python scripts/park_sim_n.py --leagues nba --games 4 --cap 2 --brains hindsight foresight heuristic
    nice -n 19 python scripts/park_sim_n.py --leagues nba nfl --sweep 2000 --csv sweep.csv      # random settings, every row kept
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

Added Nov 10 (all OFF by default: with these at their defaults every number above is unchanged). Planner runs only.
`restart_known=rows`: the honest feed. The end of a dead ball is known only once a LATER play row whose game clock has moved
has been reported, not `feed_lead` seconds ahead. Until then the stoppage is OPEN: it is believed still stopped up to
`open_wait` seconds behind the feed, and that belief can be wrong (the game restarted and no row says so yet). The controller's
padding tests then read the belief; Rule #1 is still judged by the grader's facts at the true positions, so a wrong belief
shows up in the counters. `streams` = how many streams there are (one on screen, the rest hidden). `show_delay` = every
landing is at least this far behind the stream's live edge (the lateness cap stays hard). Step times the planner knows:
`park_confirm_s`, `left_pause_s`, `jump_done_s`, `seek_s`, `seek_over_s`. `DEVICES` holds the two device profiles.

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
PLAN_OK_S = 2        # a planner's scheduled jump is delivered when it happens within this of the planned second
FEED_LEAD_S = 27     # the feed runs this far ahead of the stream's live edge (stream delay 35 s less feed lag 8 s)
TUNE_TICKS = 3       # a tune within YouTube TV: 2.3-3.0 s
LIVE_BEHIND_S = 60   # a big play seen this close to the live edge counts as seen live
LONG_STOP_S = 20     # a stoppage the feed says still has this long to run may be left without having shown any play
SAME_BEHIND_S = 10   # a stick already behind live may skip forward to this close to the live edge
PAD_S = 400
INF = 10 ** 9        # a feed time that never comes
CLOCK_EPS = 0.05     # grading.resume_after: the game clock has moved when it differs by more than this
WHY = ("other_game_parked", "nothing_parked", "loading", "margin", "flip_in_progress", "min_stay", "past_reach", "screen_high_leverage",
       "screen_mid_play", "landing_not_dead", "landing_long_wait",
       # a planner's own reasons (park_plan.Planner.why): what its search found in the way
       "no_leave_reported", "no_landing_reported", "late_cap", "plan_other_game", "plan_stays", "plan_waits")


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
    feed_lead: float = FEED_LEAD_S
    prepark: bool = True          # while the brain is content, keep the hidden stick on its second choice
    load_fail_p: float = 0.0      # a load fails and is retried (twice the time)
    load_slow_s: float = 0.0      # every load is this much slower (an app change, a slow night)
    value: str = "dense"          # what the brain values: dense = value2 grades + 2 x must-sees + live action | v1 = must-sees only
    live_w: float = 0.05          # dense: brain points per second of live play (30 s of play = 1.5, about one good basket)
    seed: int = 0
    # ---- added Nov 10; planner runs only; the defaults leave every run as it was ----
    restart_known: str = "lead"   # lead = the end of a dead ball is read `feed_lead` ahead (optimistic) | rows = only once a
    #                               later play row whose game clock has moved has been reported (honest)
    open_wait: float = 0.0        # rows: an open stoppage is believed still stopped up to this far behind the feed. 0 = until
    #                               the feed shows the clock ran; 26 = the grader's own bound (grading.RESUME_LEAD)
    streams: int = 2              # streams in all: one on screen, the rest hidden (parked or being moved)
    show_delay: float = 0.0       # every landing is at least this far behind the stream's live edge
    park_confirm_s: float = 0.0   # seconds from the freeze to a park the controller may jump to
    left_pause_s: float = 0.0     # the stream that was left freezes this long after the jump starts
    jump_done_s: float = 0.0      # a jump is complete this long after it starts: no other jump starts before
    seek_s: float = 0.0           # a forward seek on a hidden stream takes this long (0 = none: 15 s skips only)
    seek_over_s: float = 0.0      # and may land this far past its target, so it is aimed this much early
    min_guard: int = 1            # the padding is never under this many seconds, whatever pad_k x the summed error gives


# The two picture sources, step by step, in seconds. Every number says where it comes from:
#   MEASURED = timed on Nick's TV (sticks) or on his Mac's Chrome (chrome) on Nov 10;  ASSUMED = not timed yet.
# Nothing here was produced by this simulator. The grid is 1 s: a step time is rounded to the nearest second and a
# non-zero one is at least 1 s (`ticks`), so no step is ever modelled faster than one grid second.
DEVICES = {
    "sticks": {
        "streams": 2,             # MEASURED: two Fire TV sticks on two HDMI inputs
        "tune_s": 2.7,            # MEASURED: tune a new channel
        "pause_key_s": 0.7,       # MEASURED: pause key to the freeze. Not a Setting: the key is sent this much early
        "flip": 2.06,             # MEASURED: start of a jump to the new picture (median, 169 of 169)
        "park_confirm_s": 2.0,    # MEASURED: a park is confirmed about 2 s after the freeze
        "jump_done_s": 3.0,       # MEASURED: a jump is complete about 3 s after it starts
        "left_pause_s": 2.06,     # ASSUMED: the stick that was left freezes at the end of the measured jump
        "seek_s": 0.0,            # no forward seek (15 s skip keys only)
        "seek_over_s": 0.0,
        "pos_sd": 0.3,            # MEASURED: a paused stick holds its place within 0.4 s
        "min_guard": 2,           # the grid: see below
    },
    "chrome": {
        "streams": 4,             # MEASURED: four channels played at once in four tabs (more was not tried)
        "tune_s": 5.0,            # ASSUMED: load another channel in a tab (never timed)
        "pause_key_s": 0.0,       # MEASURED: pauseVideo() held the frame exactly
        "switch_s": 0.3,          # ASSUMED: activate another tab (never timed). Not a Setting: part of `flip`
        "resume_s": 0.28,         # MEASURED: playVideo() to a moving picture. Not a Setting: part of `flip`
        "flip": 0.58,             # switch_s + resume_s: ASSUMED + MEASURED
        "park_confirm_s": 0.0,    # MEASURED: the player reports paused and its position at once
        "jump_done_s": 0.58,      # ASSUMED: the jump is complete when the new tab plays
        "left_pause_s": 0.58,     # ASSUMED: the tab that was left is paused right after the new one plays
        "seek_s": 0.46,           # MEASURED: a 4 s forward seek was playing again in 0.46 s (one seek)
        "seek_over_s": 0.22,      # MEASURED: it landed 0.22 s past the target (one seek)
        "pos_sd": 0.0,            # MEASURED: the player gives its position to the millisecond
        # With an exact position the padding rule (4 x the summed error) gives 1.5 s at the best timing, which rounds to
        # 1 s. On this 1 s grid a 1 s padding is thinner than the rounding of positions: on hand-made nights it gave
        # mid-play cuts with or without the honest feed, and 2 s gave none. So no profile runs under 2 s.
        "min_guard": 2,
    },
}
NOT_SETTINGS = ("pause_key_s", "switch_s", "resume_s")


def profile(name: str, **kw) -> dict:
    """Setting keywords for a device by name ("sticks" | "chrome"); `kw` overrides (streams=8, show_delay=45, ...)."""
    out = {k: v for k, v in DEVICES[name].items() if k not in NOT_SETTINGS}
    out.update(kw)
    return out


def ticks(x: float) -> int:
    """A step time on the 1 s grid: 0 stays 0, anything else is at least one second."""
    return 0 if x <= 0 else max(1, int(round(x)))


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
        self.shot_i = sorted({int(x - base) for x in g["shots"]})
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
        self.rows = [(r[0], r[3]) for r in sorted(rows)]      # every play row with a score: (second, game clock left; < 0 = none)
        self.stamps = [r[0] for r in self.rows]
        self.n = n
        self._leave: dict[tuple, list[int]] = {}
        self._feed: dict[int, tuple] = {}

    def feed(self, q: int = 0) -> tuple[list[int], list[int], list[int]]:
        """The honest feed (`restart_known=rows`), per second: (bf, bt, rv), all feed times.
        A second is BELIEVED legal while bf <= feed time < bt. rv = the feed time at which the end of the dead ball this
        second is in becomes known: the stamp of the first LATER row whose game clock differs from the clock the ball went
        dead at (grading.resume_after reads the restart off that row). Before that the stoppage is open: second x is
        believed still stopped from feed time x + q on. That is a belief: the seconds between the true restart and that
        row are believed stopped too (bt ends it), which is how an honest reader can cut into play.
        The 4 s after a shot are legal by rule as soon as the shot row is in; so is a made basket's own short dead ball."""
        if q not in self._feed:
            n, dead, legal, a1 = self.n, self.dead, self.legal, self.a1
            stamps, clocks = self.stamps, [r[1] for r in self.rows]
            bf, bt, rv = [INF] * n, [INF] * n, [INF] * n
            win = int(pc.SHOT_WINDOW_S)
            by_rule = bytearray(n)
            for si in self.shot_i:
                for x in range(max(0, si), min(n, si + win)):
                    by_rule[x] = 1
            for x in range(n):
                if legal[x] and (by_rule[x] or x >= a1):
                    bf[x] = x                           # after a shot, or the game is over: known as the feed gets there
                    if not dead[x] or x >= a1:
                        rv[x] = x
            i = 0
            while i < n:
                if not dead[i]:
                    i += 1
                    continue
                ds = i
                while i < n and dead[i]:
                    i += 1
                de = i
                if de >= n:
                    for x in range(ds, min(a1, n)):
                        bf[x] = min(bf[x], max(x, min(a1, x + q)))      # the last dead ball: open until the final row
                    continue
                if de - ds <= win + 1 and bisect.bisect_right(self.shot_i, ds) - bisect.bisect_left(self.shot_i, ds - 1) > 0:
                    for x in range(ds, de):             # a made basket's own dead ball: 4 s at most, by rule
                        bf[x], rv[x] = x, de
                    continue
                kl = bisect.bisect_left(stamps, de) - 1          # the last row before the restart
                kc = kl
                while kc >= 0 and clocks[kc] < 0:
                    kc -= 1
                rd = INF
                for k in range(kl + 1, len(stamps)):
                    if kc < 0 or (clocks[k] >= 0 and abs(clocks[k] - clocks[kc]) > CLOCK_EPS):
                        rd = max(stamps[k], de)
                        break
                for x in range(ds, de):
                    bf[x] = min(bf[x], max(x, min(rd, x + q)))
                    rv[x] = rd
                if rd < INF:
                    for x in range(de, min(rd, n)):     # play has restarted and no row says so yet
                        if bf[x] == INF:
                            bf[x], bt[x] = x + q, rd
            self._feed[q] = (bf, bt, rv)
        return self._feed[q]

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
    __slots__ = ("sg", "sb", "se", "s_land", "s_from", "s_kind", "s_live", "hg", "hb", "he", "h_ready", "h_paused", "h_kind", "black", "rng", "gd",
                 "busy", "pool", "hk")
    # With more than two streams `pool` holds every hidden stream as (game, behind, error, ready, paused, kind) and the
    # h* slots are the working copy of pool[hk]. With two streams pool is empty and the h* slots are the hidden stick.


def hidden_of(S) -> list[tuple]:
    """Every hidden stream as (game, seconds behind, position error, ready second, paused), in a fixed order."""
    one = (S.hg, S.hb, S.he, S.h_ready, S.h_paused)
    pool = getattr(S, "pool", ())
    return [one] if not pool else [one if k == S.hk else pool[k][:5] for k in range(len(pool))]


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
    `planner` (park_plan.Planner) replaces the rule set: each second it says whether to jump and what the hidden stick does
    (tune to a game, pause at a spot, skip forward to one, or roll). The physics, the padding tests and the counters below are
    the same ones; the brain is then only asked what it WANTS, for the audit's yes-rate.
    With `streams` > 2 the planner gives one such command per hidden stream (`planner.acts`) and names the one to jump to."""
    A = night.arrs(s.shots, s.value, s.live_w)
    N, n = len(A), night.n
    a, b = night.span
    t0, t1 = (a if t0 is None else t0), (b if t1 is None else t1)
    live = s.mode == "live"
    rows, ns, D, wait = s.restart_known == "rows", int(s.streams), int(round(s.show_delay)), int(round(s.open_wait))
    conf, lpause, jdone, seek = ticks(s.park_confirm_s), ticks(s.left_pause_s), ticks(s.jump_done_s), ticks(s.seek_s)
    if s.restart_known not in ("lead", "rows") or ns < 2:
        raise ValueError("restart_known is lead or rows, and there are at least two streams")
    if (rows or ns != 2 or D or conf or lpause or jdone or seek) and (planner is None or live or s.route != "tune" or not s.pause_holds):
        raise ValueError("restart_known=rows, streams, show_delay and the step times need planner= and the tune-and-pause route")
    multi = ns > 2
    sd = 0.6 * s.pos_err if s.pos_sd < 0 else s.pos_sd
    guard = int(round(s.pos_err if s.guard < 0 else s.guard))
    if s.pad_k > 0:
        guard = max(1, int(round(s.pad_k * math.sqrt(sd ** 2 + s.delay_sd ** 2 + s.stamp_sd ** 2 + s.key_sd ** 2))))   # the grid is 1 s
    guard = max(guard, int(s.min_guard))
    edge_sd = math.sqrt(s.stamp_sd ** 2 + s.key_sd ** 2)
    flip = max(1, int(round(s.flip)))
    lattice = 0 if (s.pause_holds or live) else int(pc.SKIP_STEP_S)
    tune = s.route != "rewind" and s.pause_holds and not live
    fresh = 0 if (live or tune) else int(s.min_behind)      # where a game no stick holds would be picked up
    fresh = max(fresh, D)
    reach = int(s.max_behind)                                # a paused stick
    rw_reach = int(pc.REACH_S if s.clips else min(s.max_behind, pc.REACH_S))   # a rewind
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
        S.busy, S.hk = t0, 0
        S.pool = tuple((-1, 0.0, 0.0, t0, False, "park") for _ in range(ns - 1)) if multi else ()
    rng = S.rng

    def padded(x: Arr, i: int, K: int) -> bool:
        """Is believed position i at least `guard` inside a legal stretch, as far as the controller can know at feed time K."""
        if not rows:
            return x.since[i] > guard and x.left[i] >= guard
        bf, bt, _ = x.feed(wait)
        for y in range(max(0, i - guard), min(n, i + guard)):
            if bf[y] > K or bt[y] <= K:
                return False
        return True

    def load(k: int) -> None:
        """Make hidden stream k the one in the h* slots."""
        if k != S.hk:
            pool = list(S.pool)
            pool[S.hk] = (S.hg, S.hb, S.he, S.h_ready, S.h_paused, S.h_kind)
            S.hg, S.hb, S.he, S.h_ready, S.h_paused, S.h_kind = pool[k]
            S.pool, S.hk = tuple(pool), k

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
            v = x.ahead(p, min(p + RUN_UP_S, known), p)
            if s.park_target == "recent":
                return p, False, v
            if best is None or v > best[0] + 1e-9:
                best = (v, p)
        if best is None:
            return None
        return best[1], bool(s.clips and best[0] > 0 and best[1] < t - int(s.max_behind)), best[0]

    def start_park(g: int, t: int) -> None:
        same = g == S.hg
        load = TUNE_TICKS if live else (pc.RESCRUB_1_S if same else s.load_s) + s.load_slow_s
        if s.load_fail_p and rng.random() < s.load_fail_p:
            load *= 2.0
            st.load_fail += 1
        ready = int(math.ceil(t + load + (0 if (live or lattice) else pc.SKIP_STEP_S / 2.0)))
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
        got = choose_park(S.hg, t, t, lo=max(cur + 1, t - reach), hi=t - SAME_BEHIND_S)
        if got is None:
            return False
        move_to(got[0] - (rng.randrange(lattice) if lattice else 0), t)
        return True

    def move_to(p: int, t: int) -> None:
        """15 s skips and then play, to believed position p on the game the hidden stick holds (it then sits paused one
        second short of p)."""
        d = max(0, p - int(t - S.hb + S.he))
        cost = seek if seek else 2.0 + pc.PRESS_S * math.floor(d / pc.SKIP_STEP_S) + (0 if lattice else d % pc.SKIP_STEP_S)
        ready = int(math.ceil(t + cost)) + conf           # one forward seek where there is one; the park is confirmed `conf` later
        S.he = rng.gauss(0.0, sd) + s.delay_error + S.gd[S.hg]
        S.h_ready, S.h_paused, S.hb = ready, not lattice, (ready - p) + S.he
        st.rolls += 1

    def hid(act: tuple, t: int) -> None:
        """A planner's command for the hidden stream in the h* slots: tune, skip or seek forward to the spot, or play on
        to it and stop there with one pause."""
        desire = act[1] if act[1] >= 0 else S.hg
        if desire >= 0 and (desire != S.hg or act[3] == "tune"):
            start_park(desire, t)
            return
        if S.hg < 0 or t < S.h_ready:
            return
        x = A[S.hg]
        hb_i = min(n - 1, max(0, int(t - S.hb + S.he)))
        if act[3] == "move" and act[2] > hb_i:
            move_to(act[2], t)
        elif act[2] < 0 or hb_i < act[2]:
            S.h_paused = False
        elif not S.h_paused and padded(x, hb_i, t + know) and hb_i + guard <= t + know:
            S.h_paused = True
            if conf:                      # frozen now, but the controller may not jump to it until the park is confirmed
                S.h_ready, S.hb = t + conf, S.hb + conf - 1

    if planner is not None:
        planner.bind(A, s, guard=guard, reach=reach, flip=flip, lead=lead, know=know, n=n, stats=st)

    for t in range(t0, t1):
        st.ticks += 1
        if S.hg >= 0 and S.h_paused and t >= S.h_ready:
            S.hb += 1.0
        held: dict | tuple = ()
        if multi:
            S.pool = tuple(h if (k == S.hk or h[0] < 0 or not h[4] or t < h[3]) else (h[0], h[1] + 1.0) + h[2:] for k, h in enumerate(S.pool))
            held = {h[0]: int(t - h[1] + h[2]) for k, h in enumerate(S.pool) if k != S.hk and h[0] >= 0 and t >= h[3]}
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
            v = brain.score(g, h_bel, t, False, 0) if (g == S.hg and h_ok) else brain.score(g, held[g], t, False, 0) if g in held \
                else brain.score(g, t - fresh, t, False, back)
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
            # per hidden stream: (jump now, its game, the spot to pause at, "" | "tune" | "move")
            acts = planner.acts(t, S) if multi else (planner.act(t, S),)
            kj = -1
            for k, act in enumerate(acts):
                if multi:
                    load(k)
                if act[0]:
                    kj = k
                hid(act, t)
            if multi:      # the jump goes to stream kj; with none, the reasons below are about the stream on the wanted game
                load(kj if kj >= 0 else next((k for k, h in enumerate(hidden_of(S)) if h[0] == want), S.hk))
                act = acts[S.hk]
            target = S.hg if act[0] else -1
        else:
            if want >= 0:
                st.wants.append((t, want))
            # the hidden stick: a load in progress is finished; an idle stick moves to the brain's top other game
            if S.hg < 0 or not (A[S.hg].a0 <= t <= A[S.hg].a1 + fresh):
                desire = pk if (s.prepark or want >= 0) else -1
            elif h_ok and target < 0 and pk >= 0 and pk != S.hg and (s.prepark or want >= 0) and pk_sc > pk_h + s.repark_margin:
                desire = pk
        if act is not None:
            pass                            # a planner's hidden streams were moved above (hid)
        elif desire >= 0 and desire != S.hg:
            start_park(desire, t)
        elif h_ok and not live:
            x = A[S.hg]
            hb_i = min(n - 1, max(0, h_bel))
            if lattice:
                if x.legal[hb_i] and x.left[hb_i] <= guard and x.since[hb_i] - int(pc.SKIP_STEP_S) > guard:
                    S.hb += pc.SKIP_STEP_S          # one step back holds the stick in its dead stretch
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
                        S.hb, S.h_ready = (reach - 2 * pc.SKIP_STEP_S) + S.he, t + 2 + int(pc.PRESS_S * 2)
            elif S.h_paused and s.skip_dead and not lattice and x.left[hb_i] > max(s.land_wait_max, guard + lead + pc.SKIP_STEP_S) \
                    and hb_i + x.left[hb_i] <= t + know:
                # parked early in a long stoppage whose end the feed now knows: skip to just before the play
                d = x.left[hb_i] - guard - lead
                ready = int(math.ceil(t + 2.0 + pc.PRESS_S * math.floor(d / pc.SKIP_STEP_S) + d % pc.SKIP_STEP_S))
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
        if target >= 0 and S.hg == target and t >= S.h_ready and t >= S.black and t >= S.busy and t - S.s_land >= s.min_stay \
                and (live or S.hb - S.he <= reach + flip):
            xs, xh = A[S.sg], A[target]
            i = min(n - 1, max(0, s_bel))
            calm = xs.hlc[min(n, i + guard + 1)] - xs.hlc[max(0, i - guard)] == 0
            in_s = padded(xs, i, t + know)
            leave = (in_s and calm) or (s.leave_rule == "low_leverage" and calm) or t > xs.a1 + S.sb
            j = min(n - 1, max(0, int(t - S.hb + S.he) + (lead if S.h_paused else flip)))
            if i + guard > t + know or j + guard > t + know:
                leave = False           # the feed has not yet reported the seconds the padding has to cover
            rem, in_h, wait_ok = xs.left[i], padded(xh, j, t + know), s.land_wait_max >= xh.left[j]
            shown = S.s_live
            if rows:                    # how long this stoppage, and the landing's, still run is known only once revealed
                if xs.feed(wait)[2][i] > t + know:
                    rem = t + know - wait - i if in_s else 0
                wait_ok = s.land_wait_max >= 999.0 or (xh.feed(wait)[2][j] <= t + know and wait_ok)
                # and the play this visit has shown is known only up to the game's last reported row
                kr = bisect.bisect_right(xs.stamps, t + know) - 1
                shown = xs.dead.count(0, min(n - 1, max(0, int(S.s_from + S.se))), max(0, min(i, xs.stamps[kr] + 1 if kr >= 0 else 0)))
            if shown < s.min_live_s and rem < LONG_STOP_S and t <= xs.a1 + S.sb:
                leave = False           # nothing of this game has been shown yet: a hop through dead air is not a visit
                if why is None and want >= 0 and force is None:
                    why = "min_stay"
            if D and (S.hb - S.he) + (flip - lead if S.h_paused else 0) < D:
                leave = False           # the show delay: no picture comes up closer to the live edge than this
            if why is None and want >= 0 and force is None:
                why = ("screen_high_leverage" if not calm else "screen_mid_play" if not leave
                       else "landing_not_dead" if not in_h
                       else "landing_long_wait" if not wait_ok else None)
            if leave and in_h and wait_ok:
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
                if jdone:
                    S.busy = t + jdone
                if live:
                    S.hg, S.hb, S.he, S.h_ready, S.h_paused, S.h_kind = old[0], old[1], old[2], t, False, "pp"
                elif s.ping_pong or act is not None:
                    S.hg, S.hb, S.he, S.h_ready, S.h_paused, S.h_kind = old[0], old[1], old[2], t, not lattice, "pp"
                    if lpause or jdone:
                        # the stream that was left plays on for `lpause`, freezes there, and may be jumped to again once
                        # this jump is complete
                        d = max(lpause, jdone)
                        S.h_ready, S.hb = t + d, old[1] + (d - lpause) - 1
                    if s.skip_dead and not lattice and act is None:
                        end = i + xs.left[i]
                        p = end - guard - lead
                        if end <= t + int(s.feed_lead) and p - i >= pc.SKIP_STEP_S and end - xs.starts[bisect.bisect_right(xs.starts, i) - 1] >= need:
                            ready = int(math.ceil(t + 2.0 + pc.PRESS_S * math.ceil((p - i) / pc.SKIP_STEP_S)))
                            if ready - p >= s.min_behind:
                                S.he = rng.gauss(0.0, sd) + s.delay_error + S.gd[S.hg]
                                S.h_ready, S.hb = ready, (ready - p) + S.he
                else:
                    S.hg, S.h_ready, S.h_paused = -1, t, False
                continue
        if want >= 0 and force is None:
            ok = A[S.sg].leave_in(guard, s.leave_rule == "low_leverage")
            st.leave_soon += int(ok[min(n - 1, max(0, s_bel))] <= YES_S - flip)
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


def served(st: Stats) -> list[bool]:
    """Per wanted second: was that game's picture up within YES_S (the audit's yes)."""
    ups: dict[int, list[int]] = {}
    for at, g in st.ups:
        ups.setdefault(g, []).append(at)
    out = []
    for t, g in st.wants:
        u = ups.get(g, ())
        i = bisect.bisect_right(u, t)
        out.append(i < len(u) and u[i] <= t + YES_S)
    return out


def yes_rate(st: Stats) -> tuple[int, int]:
    return sum(served(st)), len(st.wants)


def delivered(st: Stats, sched: list | None = None) -> int:
    """A planner's scheduled jumps that happened: onto the planned game, within PLAN_OK_S of the planned second, with no
    Rule #1 break on that flip."""
    by: dict[int, list[tuple[int, int]]] = {}
    for t, g, bad in st.flips:
        by.setdefault(g, []).append((t, bad))
    n = 0
    for _, at, g in (st.sched if sched is None else sched):
        fl = by.get(g, ())
        i = bisect.bisect_left(fl, (at - PLAN_OK_S, -1))
        n += int(any(bad == 0 for t, bad in fl[i:i + 4] if t <= at + PLAN_OK_S))
    return n


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
    # the same reasons counted over the REFUSED seconds only (wanted, and that game's picture was not up within YES_S)
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


def nights_for(league: str, n_games: int, cap: int, cache: Path | None) -> list[Night]:
    out = []
    for night in pc.load_nights(league, cap, cache):
        sl = pc.slate(night, n_games)
        if sl:
            out.append(Night(sl))
    return out


def slate_nights(name: str, cap: int, cache: Path | None) -> list[Night]:
    """Nick's slates by name: "nba 4", "mnf+3" (park_ceiling.slates_for; the mixed one is synthetic)."""
    return [Night(x) for x in pc.slates_for(name, cap, cache)]


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
    "pause_holds": [True], "route": ["tune", "tune", "either", "rewind"], "load_s": [18.5, 25.0, 30.0], "resume_lead_s": [0.0, 1.0, 2.0],
    "pos_sd": [0.3], "delay_sd": [0.2, 0.5, 1.0, 2.0, 3.5], "stamp_sd": [0.3, 0.6], "key_sd": [0.1, 0.3], "pad_k": [3.0, 4.0],
    "feed_lead": [10.0, 20.0, 27.0],
    "min_dead": [4.0, 8.0, 12.0, 20.0], "max_behind": [90.0, 175.0, 300.0, 600.0], "ping_pong": [True, False],
    "min_stay": [0.0, 8.0, 15.0, 30.0], "min_live_s": [3.0, 3.0, 8.0], "land_wait_max": [8.0, 15.0, 30.0, 999.0], "margin": [0.0, 1.0, 3.0], "skip_dead": [True, False], "clips": [True, False],
    "leave_rule": ["dead", "low_leverage"], "park_at": ["end", "start"], "park_target": ["value", "recent"],
    "shots": [False, True], "prepark": [True, False], "delay_error": [0.0], "live_w": [0.0, 0.05, 0.15],
    "repark_margin": [0.0, 1.0, 3.0], "value": ["dense", "dense", "v1"], "lookahead_s": [0.0, 15.0, 30.0, 45.0],
    "bore_w": [0.0, 0.0, 0.02, 0.05, 0.1],
}


def draw(rng: random.Random, league: str) -> Setting:
    """One random setting from the grid (the guard is pad_k x the four error parts together; the any-shot rule needs basketball)."""
    d = {k: rng.choice(v) for k, v in GRID.items()}
    if league in ("nfl", "cfb", "nhl", "mlb"):
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
    ap.add_argument("--slates", nargs="+", default=None, help='Nick\'s slates by name instead of --leagues/--games: "nba 4" "mnf+3"')
    ap.add_argument("--cap", type=int, default=2, help="dev nights per league")
    ap.add_argument("--brains", nargs="+", default=["hindsight", "foresight"], choices=sorted(BRAINS))
    ap.add_argument("--set", action="append", default=[], help="key=value on Setting (repeatable)")
    ap.add_argument("--sweep", type=int, default=0, help="this many random settings from GRID per league and game count")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--cache", default=None, help="a folder OUTSIDE the repo for the per-game facts")
    ap.add_argument("--csv", default=None)
    a = ap.parse_args()
    fixed = parse_sets(a.set)          # in a sweep these stay fixed and the other knobs are drawn
    base = Setting(**fixed)
    names = [f.name for f in fields(Setting)]
    fh = w = None
    rng = random.Random(a.seed)
    cache = Path(a.cache) if a.cache else None
    jobs = [(x, 0, slate_nights(x, a.cap, cache)) for x in a.slates] if a.slates else \
        [(lg, ng, nights_for(lg, ng, a.cap, cache)) for lg in a.leagues for ng in a.games]
    for lg, ng, nights in jobs:
        for _ in (0,):
            if not nights:
                continue
            ng = ng or len(nights[0].games)
            todo = [("baseline_live", replace(base, mode="live")), ("base", base)] if not a.sweep else \
                [("baseline_live", replace(Setting(mode="live"), **fixed)), ("default", replace(Setting(), **fixed))] \
                + [(f"s{i}", replace(draw(rng, lg), **fixed)) for i in range(a.sweep)]
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
