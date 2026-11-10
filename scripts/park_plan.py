"""PARK-BRAIN: a planning controller for the two-stick show, run inside park_sim (its nights, physics, padding and counters).

    nice -n 19 python scripts/park_plan.py --slates "nba 4" "mnf+3" --errors today middle spec --bonus 0 0.5 1 2 --csv curve.csv
    --plan horizon=120 --plan depth=3       one Plan knob for this run (repeatable); --set key=value sets a park_sim.Setting knob
    --model park_unknown.npz                value the seconds the feed has not reported with a trained model (park_table.ModelBrain)
    --baseline                              also print live-edge physics under the same padding
    --report curve.csv                      print the trade-off curve of a CSV written by --csv, one table per slate and error

No TV, no adb, no engine, no network, no training. Dev (`fit`) nights only. A night runs in well under a second.

What it does. park_sim's rule set parks the hidden stick on the game the brain scores highest and jumps when it can. This
controller PLANS instead. Every `replan_s` seconds (and whenever a jump is due or the hidden stick comes up) it searches the
next `horizon` seconds of what the two sticks can do and takes the first step of the best plan:
    stay                            keep the screen game
    jump                            to the hidden stick, at a legal leave of the screen game the feed has reported
    hold                            the stick just left stays paused where the viewer left it (a later jump returns there)
    roll / skip                     play that stick on, or skip it forward in 15 s steps, and pause it before a later play
    tune                            put the hidden stick on another game: tune (3 s), play to a spot, one pause key
A plan is up to `depth` jumps (the best `beam` first jumps are searched one level deeper). It reads ONLY what the feed has
reported: nothing past the live edge + `feed_lead`. A second the feed has not reported is worth `unk_w` x the game's
score-and-clock leverage (the foresight brain's no-future view), or what a trained model says (`--model`).
When no jump in the reports is worth making it BANKS a landing: it assumes the next leave comes `exp_wait` past the reports
and puts the hidden stick where the first `bank_s` seconds after such a jump are worth most. That is never scheduled.

What a plan is worth: the simulator's dense value of every second it puts on screen (value2 grades, twice the must-sees,
live play; a graded play counts only when the picture was up 7 s before it, as the simulator counts it), plus `jump_bonus`
for each visit that shows live play (paid when it has, so a hop through dead air earns nothing), less `late_w` per second
on screen per second behind the stream's live edge. A plan holds only `depth` jumps, so the seconds after its last known
leave are worth `jump_bonus` per hoped-for cycle (`cycle_k` x the average spacing of landing spots per game in the feed so
far): that is what makes a jump now better than the same jump later, and a landing with a quick next leave better.
Never traded, whatever the bonus:
    the Rule #1 padding and no leave inside a high-leverage span: the planner only ASKS for a jump, park_sim's own tests
        (pad_k x the summed error from every edge, both ends reported by the feed) decide, and it asks only where they pass;
    the must-see guarantee: a must-see the feed has reported is worth MUST_W, more than any bonus or grade can add up to, so a
        plan that shows it always beats one that does not (it also overrides a committed jump);
    the lateness cap: no landing further behind the stream's live edge than Setting.late_cap (park_sim refuses it too);
    a visit that has shown no play is left only for a play that starts at least HOP_GAIN_S sooner (no dead-air ping-pong).
One thing it reads the way park_sim's own leave test does: whether a second is inside a high-leverage span, which is marked
from 7 s before the play's stamp. A game being close and late is known before the play, so this is not a look at the result.

The yes-rate for a planner. The audit's yes-rate (the brain wanted a game and its picture was up within 20 s) is kept: the
brain passed in is only asked what it wants. Added, "planned jump delivered": each time the plan's next jump is first
scheduled, or moves to another game or by more than 2 s, it is logged; it is delivered when a flip onto that game happens
within 2 s of the planned second with no Rule #1 break on it. `firm` is the same count over the jumps as they stood when
they came inside the commit window (`commit_s` ahead), which is the promise the app could show on screen.

Added Nov 10, all read from park_sim.Setting and OFF at its defaults (a default run is the same second for second):
    restart_known=rows   the honest feed. A stretch's end (the restart) is used only once a later row whose clock has moved
                         has been reported (`Tab.rk`). Before that the stoppage is open: it may be left, and a stream paused
                         in it may be landed on, only where it is still believed stopped (`open_wait` behind the feed). Each
                         game's seconds count as reported only up to its last reported row.
    streams > 2          one command per hidden stream. Every hidden stream is parked where a jump to it from the screen
                         would be worth most; the jump goes to the best of them; the second jump of a plan may land on any
                         other one, so looks queue back to back. A game no stream holds is tuned by the least useful stream.
    seek_s > 0           a park is one forward seek to the spot (aimed `seek_over_s` early) and a pause, not 15 s skips.
    show_delay           no landing closer to the stream's live edge than this; a stream too close is paused and waits.
    park_confirm_s, left_pause_s, jump_done_s   the device's step times, as the simulator plays them.

Why: the rule-set controller found by random search delivered about a third of the jump pace the two sticks can supply,
because with one spare stick the result is decided by which game is parked BEFORE the want arrives; that is a planning
problem, and Nick wants many jumps, a yes-rate near 95% and zero mid-play cuts at the same time.
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
import time
from bisect import bisect_left
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT.as_posix(), (ROOT / "scripts").as_posix()):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import park_ceiling as pc  # noqa: E402
import park_sim as ps  # noqa: E402

MUST_W = 1000.0                      # brain points per must-see point: more than a night of grades and bonuses
FAR = 10 ** 9                        # the end of a stretch that never ends
HOLD, ROLL, MOVE, TUNE = 0, 1, 2, 3  # how the hidden stick gets to a landing
ROUTES = ("hold", "roll", "skip", "tune")
FRESH_BEHIND = 20                    # a stick is re-tuned onto its own game only when it sits at least this far behind
JOIN_SLACK = 5                       # a leave the feed has not reported is assumed to stay open this long
HOP_GAIN_S = 10                      # a visit that has shown no play is left only for a play that starts this much sooner
# the three timing scenarios of the brief: how well the channel delay is known (sd, seconds); the other three parts are fixed
ERRORS = {"today": 3.5, "middle": 1.0, "spec": 0.2}
ERR_FIXED = {"pos_sd": 0.3, "stamp_sd": 0.3, "key_sd": 0.1, "pad_k": 4.0}


@dataclass(frozen=True)
class Plan:
    jump_bonus: float = 0.5       # brain points for a jump whose visit shows live play: the restlessness
    horizon: int = 90             # seconds of screen a plan covers
    depth: int = 2                # jumps in a plan
    beam: int = 5                 # first jumps kept for the second level
    replan_s: int = 2             # seconds between searches (one also runs when a jump is due or the hidden stick comes up)
    leaves: int = 3               # legal leaves of the screen game looked at
    spots: int = 2                # landings looked at per game and route
    late_w: float = 3e-4          # brain points per second on screen per second behind the stream's live edge
    unk_w: float = 0.02           # a second the feed has not reported is worth this x the game's leverage
    stick: float = 0.1            # a new plan has to beat the scheduled one by this
    commit_s: int = 10            # and a scheduled jump this close is kept whatever else comes up (while it can still be made)
    cycle_k: float = 1.0          # the pace hoped for beyond the plan: one jump per this x the average spacing of landing
    #                               spots per game in the feed so far (a jump that would put the next one further off than
    #                               that is not worth its bonus). 0 = no such urgency
    cycle_s: float = 20.0         # and never hoped faster than one jump per this many seconds
    exp_wait: int = 10            # with no jump worth making in the feed's reports, the next leave is assumed this far past them
    bank_s: int = 60              # and the hidden stick is put where the first this-many seconds after such a jump are worth most


class Tab:
    """One game's landing spots for one padding: stretch k may be joined or left at believed positions la[k]..lb[k] and
    its play starts at le[k]. cl = live seconds before each second, cv = dense value before each second (must-sees x MUST_W)."""
    __slots__ = ("x", "la", "lb", "le", "cl", "cv", "rk")

    def __init__(self, x: ps.Arr, guard: int):
        self.x = x
        self.la, self.lb, self.le = [], [], []
        for a, b in zip(x.starts, x.ends):
            if b - a >= 2 * guard:
                self.la.append(a + guard)
                self.lb.append(b - guard)
                self.le.append(b)
        n, dead, mom, cumv = x.n, x.dead, x.mom, x.cumv
        if x.legal[n - 1] and x.since[n - 1] >= 2 * guard:
            # the last stretch of the night never ends in a play. It is still a place to leave from: whether a stretch the
            # feed is in the middle of will ever end is not something the planner may know, so it is listed like the others
            self.la.append(n - x.since[n - 1] + guard)
            self.lb.append(FAR)
            self.le.append(FAR)
        cl, cv, live, must = [0] * (n + 1), [0.0] * (n + 1), 0, 0.0
        for i in range(n):
            if not dead[i]:
                live += 1
            if i in mom:
                must += mom[i]
            cl[i + 1] = live
            cv[i + 1] = cumv[i + 1] + MUST_W * must
        self.cl, self.cv = cl, cv
        self.rk = None

    def reveals(self) -> list[int]:
        """Per stretch: the feed time its end is known in the honest feed (park_sim.Arr.feed), never earlier than the
        stretch before it. The stretch that never ends is never revealed."""
        if self.rk is None:
            rv, out, top = self.x.feed(0)[2], [], 0
            for b in self.le:
                top = max(top, FAR if b >= FAR else max(b, rv[b - 1]))
                out.append(top)
            self.rk = out
        return self.rk


def tabs_for(A: list, guard: int) -> list[Tab]:
    out = []
    for x in A:
        cache = x.__dict__.setdefault("_plan_tabs", {})
        if guard not in cache:
            cache[guard] = Tab(x, guard)
        out.append(cache[guard])
    return out


class Planner:
    """Drives park_sim.run(planner=...). act() each second -> (jump now, the hidden stick's game, the spot to pause at, how)."""
    name = "planner"

    def __init__(self, plan: Plan | None = None, unknown=None):
        self.p = plan or Plan()
        self.unknown = unknown        # None, or an object with bind(arrs, setting) and rate(game, last reported second, now):
        #                               the value of one second the feed has not reported (park_table.ModelBrain)
        self.A = None
        self.searches = 0
        self.trace: list | None = None    # set to a list to keep every search's options (park_table reads them)
        self._reset()

    def _reset(self) -> None:
        self.next = -1                # the next second a search is due
        self.land = None              # S.s_land at the last search: a new landing forces one
        self.cmds = ((-1, -1, ""),)   # per hidden stream: its game, the spot to pause at, a one-off "tune" | "move"
        self.go = None                # the jump being waited for: (screen game, lo, hi, game, la, lb, planned second, stream)
        self.open = None              # the scheduled jump last logged: (planned second, game)
        self.firm = None              # the scheduled jump last logged inside the commit window
        self.info = (False, (), (), -1, False, False)   # for why(): leave reported, games with a landing, capped games, plan game, tentative, jump

    def bind(self, A: list, s: ps.Setting, *, guard: int, reach: int, flip: int, lead: int, know: int, n: int, stats) -> None:
        if self.A is not A:
            self._reset()
        self.A, self.s, self.G, self.reach, self.flip, self.lead, self.know, self.n = A, s, guard, reach, flip, lead, know, n
        self.st = stats
        self.tabs = tabs_for(A, guard)
        self.tune = int(math.ceil(s.tune_s + s.load_slow_s))
        self.lw = s.live_w if s.value == "dense" else 0.0
        self.min_live = int(math.ceil(s.min_live_s))
        self.rows, self.wait, self.D = s.restart_known == "rows", int(round(s.open_wait)), int(round(s.show_delay))
        self.conf, self.lpause, self.jdone = ps.ticks(s.park_confirm_s), ps.ticks(s.left_pause_s), ps.ticks(s.jump_done_s)
        self.seek, self.over = ps.ticks(s.seek_s), (ps.ticks(s.seek_over_s) if s.seek_s > 0 else 0)
        self.ek = [tb.reveals() if self.rows else tb.le for tb in self.tabs]     # when each stretch's end is known
        self.stamps = [[r[0] for r in x.rows] for x in A] if self.rows else None
        if self.unknown is not None:
            self.unknown.bind(A, s)

    @property
    def cmd(self) -> tuple:
        """The first hidden stream's command (the hidden stick, with two streams)."""
        return self.cmds[0]

    # ---- each second ----------------------------------------------------------------------------------------------

    def act(self, t: int, S) -> tuple:
        return self.acts(t, S)[0]

    def acts(self, t: int, S) -> list:
        """One command per hidden stream, in park_sim.hidden_of's order. At most one of them says jump."""
        H = ps.hidden_of(S)
        if t >= self.next or S.s_land != self.land or any(t == h[3] for h in H):
            self._search(t, S)
        out, cmds, go = [], list(self.cmds), self.go
        for sx, (game, spot, how) in enumerate(cmds):
            if how:
                cmds[sx] = (game, spot - 1 if how == "move" else spot, "")     # a skip ends one second short of where it is sent
                self.next = t + 1
                out.append((False, game, spot, how))
                continue
            jump = False
            hg, hbh, he, ready, hp = H[sx]
            if go is not None and go[7] == sx and S.sg == go[0] and hg == go[3] and t >= ready and t >= S.black and t >= S.busy:
                cs = int(t - S.sb + S.se)
                if go[1] <= cs <= go[2]:
                    hb = int(t - hbh + he)
                    # park_sim pauses a stick that has reached its spot before it looks at the jump, in the same second
                    paused = hp or (0 <= spot <= hb and go[4] <= hb <= go[5] and hb + self.G <= t + self.know)
                    j = hb + (self.lead if paused else self.flip)
                    if go[4] <= j <= go[5]:
                        self.next = t + 1         # whether or not park_sim lets it through, look again next second
                        jump = True
            out.append((jump, game, spot, ""))
        self.cmds = tuple(cmds)
        return out

    def why(self, t: int, S, want: int) -> str:
        """The first thing in the way of the game the brain wants (asked only on seconds no jump was asked for)."""
        leave, landable, capped, g, tent, jump = self.info
        if t < S.black:
            return "flip_in_progress"
        if not leave:
            return "no_leave_reported"
        if want not in landable:
            return "late_cap" if want in capped else "no_landing_reported"
        if g != want:
            return "plan_other_game" if (g >= 0 and jump) else "plan_stays"
        if not jump:
            return "plan_stays"
        return "loading" if (S.hg != want or t < S.h_ready) else "plan_waits"

    # ---- the search -----------------------------------------------------------------------------------------------

    def _search(self, t: int, S) -> None:
        p, tabs, A = self.p, self.tabs, self.A
        G, flip, lead, reach, n = self.G, self.flip, self.lead, self.reach, self.n
        lw, late_w, bonus, min_live, tune, spots = self.lw, p.late_w, p.jump_bonus, self.min_live, self.tune, p.spots
        nleaves, beam = p.leaves, p.beam
        honest, eks, D, conf, lpause, jdone, seek, over = self.rows, self.ek, self.D, self.conf, self.lpause, self.jdone, self.seek, self.over
        near, step = (1, 1) if seek else (ps.SAME_BEHIND_S, pc.SKIP_STEP_S)    # how close to live, and how short, a forward move may be
        K = min(n - 2, t + self.know)             # the last second of every game the feed has reported
        KB = K - (self.wait if honest else 0)       # an open stoppage is believed still stopped up to here
        rho = 0.0                                  # what a second is worth in jumps still to come
        if p.cycle_k > 0 and bonus > 0:
            spots_n = secs = 0
            for tb, ek in zip(tabs, eks):
                if tb.x.a0 < K:
                    spots_n += bisect_left(ek, K + 1) - bisect_left(tb.le, tb.x.a0)
                    secs += min(K, tb.x.a1) - tb.x.a0
            rho = bonus / max(p.cycle_s, p.cycle_k * secs / max(1, spots_n))
        K1, last = K + 1, K - G                    # `last`: the last position whose padding the feed has reported
        T = t + p.horizon
        N = len(A)
        self.searches += 1
        self.next, self.land = t + p.replan_s, S.s_land
        unk = self.unknown
        u = [0.0] * N
        for g in range(N):
            x = A[g]
            if x.a0 <= K < x.a1:
                u[g] = unk.rate(g, K, t) if unk is not None else p.unk_w * x.lev[K]
        cap = reach + flip
        cap_hit: set[int] = set()
        # the last second of each game that counts as reported: the feed's edge, or in the honest feed the game's last row
        k1s = [K1] * N
        if honest:
            for g in range(N):
                i = bisect_left(self.stamps[g], K1) - 1
                if K > A[g].a0 and i >= 0:
                    k1s[g] = self.stamps[g][i] + 1

        def seg(g: int, a: int, b: int, m0: int) -> float:
            """The value of game g's seconds a..b-1 on screen; graded plays count from second m0 (7 s after the landing)."""
            if b > n:
                b = n
            if b <= a:
                return 0.0
            tb = tabs[g]
            K1 = k1s[g]
            kb = b if b < K1 else K1
            v = 0.0
            if a < kb:
                cl = tb.cl
                if m0 <= a:
                    v = tb.cv[kb] - tb.cv[a]
                elif m0 < kb:
                    v = tb.cv[kb] - tb.cv[m0] + lw * (cl[m0] - cl[a])
                else:
                    v = lw * (cl[kb] - cl[a])
            if b > K1:
                v += (b - (a if a > K1 else K1)) * u[g]
            return v

        def shows_play(g: int, a: int, b: int, live0: int) -> bool:
            """Will a visit of game g from second a to b show live play (3 s reported, or enough still unreported)."""
            if b > n:
                b = n
            K1 = k1s[g]
            kb = b if b < K1 else K1
            return (a < kb and live0 + tabs[g].cl[kb] - tabs[g].cl[a] >= 3) or (b - max(a, K1) >= 5 and u[g] > 0)

        def calm(x, lo: int, hi: int) -> int:
            hlc = x.hlc
            while lo <= hi:
                if hlc[min(n, lo + G + 1)] - hlc[max(0, lo - G)] == 0:
                    return lo
                lo += 1
            return -1

        def leaves(g: int, c: int, live0: int, lim: int) -> list:
            """Legal leaves the feed has reported for a picture of game g now at position c: (first, last) positions."""
            tb = tabs[g]
            x, out = tb.x, []
            if c > x.a1 + G:                               # the final whistle has gone, by the padding: any reported second
                if c <= last:
                    out.append((c, last))
                return out
            la, lb, le, cl, ek = tb.la, tb.lb, tb.le, tb.cl, eks[g]
            k, m = bisect_left(lb, c), len(lb)
            if honest and k > 0 and ek[k - 1] > K:
                k -= 1                                     # the stretch before is still open: its true end is not known
            need = min_live - live0
            while k < m and len(out) < lim:
                lo = la[k]
                if lo < c:
                    lo = c
                if lo > last:
                    break
                opn = honest and ek[k] > K                   # open: believed stopped up to KB, wherever it truly ends
                hi = KB - G if opn else lb[k]
                if hi > last:
                    hi = last
                if need > 0 and cl[lo] - cl[c] < need:    # no play shown yet: only a long stoppage may be left
                    h2 = (le[k] if ek[k] <= K else KB + 1) - ps.LONG_STOP_S
                    if h2 < hi:
                        hi = h2
                if lo <= hi:
                    lo = calm(x, lo, hi)
                    if lo >= 0:
                        out.append((lo, hi))
                if opn:
                    break                                  # nothing after an open stoppage has been reported
                k += 1
            return out

        def lands(scr: int, Hs: list, tc: int) -> list:
            """Where the hidden streams could be landed on: (game, route, stretch, ready second, position then, paused
            position or -1, stream). Stream sx holds game hg at position c0 from second w0 (paused if hp); a new key can go
            at second tc."""
            out, at, holder = [], [], {}
            for sx, (hg, hp, c0, w0) in enumerate(Hs):
                wr = cr = tc
                if hg >= 0:
                    holder[hg] = sx
                    tb = tabs[hg]
                    la, lb, ek = tb.la, tb.lb, eks[hg]
                    k, m = bisect_left(lb, c0 + lead), len(lb)
                    wr = w0 if w0 > tc else tc
                    cr = c0 if hp else c0 + (wr - w0)
                    kh = k - 1 if (honest and k > 0 and ek[k - 1] > K) else k        # an open stretch has no known end
                    if hp and kh < m and la[kh] <= c0 + lead <= (min(last, KB - G) if (honest and ek[kh] > K) else last):
                        out.append((hg, HOLD, kh, w0, c0, c0, sx))
                        k = kh + 1
                    kk, cnt = k, 0
                    while kk < m and cnt < spots and ek[kk] <= K:      # play on to a later stretch and pause before its play
                        if lb[kk] - lead >= cr:
                            out.append((hg, ROLL, kk, wr, cr, -1, sx))
                            cnt += 1
                        kk += 1
                    if w0 <= tc:                                        # or skip there in 15 s steps (one seek, where there is one)
                        kk, cnt = k, 0
                        while kk < m and cnt < spots:
                            q = lb[kk] - lead - over
                            if ek[kk] > K or q > tc - near:
                                break
                            d = q + 1 - cr
                            if d >= step and q + lead >= la[kk]:
                                r = tc + seek if seek else int(math.ceil(tc + 2.0 + pc.PRESS_S * math.floor(d / pc.SKIP_STEP_S) + d % pc.SKIP_STEP_S))
                                out.append((hg, MOVE, kk, r + conf, q, q, sx))
                                cnt += 1
                            kk += 1
                at.append((wr, cr))
            # a tune puts a stream at the live edge. A game some stream holds is re-tuned by that stream; a game none holds
            # goes to the stream that is idle, else whose game is over, else whose game has the least leverage
            vict = 0
            if len(Hs) > 1:
                vict = min(range(len(Hs)), key=lambda sx: (Hs[sx][0] >= 0, Hs[sx][0] >= 0 and Hs[sx][2] <= A[Hs[sx][0]].a1,
                                                           u[Hs[sx][0]] if Hs[sx][0] >= 0 else 0.0, sx))
            w1 = tc + tune
            for g in range(N):
                x = A[g]
                sx = holder.get(g, vict)
                if g == scr or x.a0 > t or (x.a1 <= K and tc > x.a1) or (g in holder and at[sx][0] - at[sx][1] < FRESH_BEHIND):
                    continue
                tb = tabs[g]
                lb, ek = tb.lb, eks[g]
                k, m, cnt = bisect_left(lb, w1 + lead), len(lb), 0
                while k < m and cnt < spots and ek[k] <= K:
                    out.append((g, TUNE, k, w1, w1, -1, sx))
                    cnt += 1
                    k += 1
            return out

        def pair(lo: int, hi: int, c: int, w: int, xs, opt: tuple, tmin: int = 0):
            """The first second the screen (position c at second w, legal to leave at lo..hi) can jump to this landing:
            (second, leave position, landing position) or None. xs = None for a leave the feed has not reported."""
            g, _, k, r, c0, pp, _ = opt
            tau = w + lo - c
            if tau < r:
                tau = r
            if tau < tmin:
                tau = tmin
            if pp >= 0:
                j = pp + lead
                if D and tau < pp + D - flip + lead:
                    tau = pp + D - flip + lead         # the show delay: wait until the paused stream is far enough behind
                if tau - pp > cap:
                    cap_hit.add(g)
                    return None
            else:
                tb = tabs[g]
                lb_k = tb.lb[k]
                wj = r + tb.la[k] - flip - c0              # the first second it can be joined while it plays
                if tau < wj:
                    tau = wj
                ct = c0 + tau - r
                if ct + flip <= lb_k and (not D or r - c0 >= D):
                    j = ct + flip
                    if r - c0 > cap:
                        cap_hit.add(g)
                        return None
                else:
                    wp = r + lb_k - lead - c0 + conf      # the second it is paused before the play (and the park confirmed)
                    if tau < wp:
                        tau = wp
                    if D and tau < lb_k + D - flip:
                        tau = lb_k + D - flip             # the show delay: it waits there, paused
                    j = lb_k
                    if tau - (lb_k - lead) > cap:
                        cap_hit.add(g)
                        return None
            i = c + tau - w
            if i > hi:
                return None
            if i != lo and xs is not None and xs.hlc[min(n, i + G + 1)] - xs.hlc[max(0, i - G)] != 0:
                return None
            return tau, i, j

        def stay(g: int, c: int, w: int, m0: int, live0: int, paid: bool, lv: list, T: int) -> float:
            """Keep game g (position c at second w) on screen to second T. `rho` a second for the jumps the plan does not
            hold: from the first leave the feed has reported, or from where its reports end."""
            e = c + T - w
            v = seg(g, c, e, m0) - late_w * (w - c) * (T - w)
            if not paid and shows_play(g, c, e, live0):
                v += bonus
            if rho:
                wf = w + (lv[0][0] - c if lv else (last - c + 1 if last >= c else 0) + p.exp_wait)
                if wf < T:
                    v += rho * (T - wf)
            return v

        def jumps(g: int, c: int, w: int, m0: int, live0: int, paid: bool, lv: list, ld: list, d: int, T: int, known: bool = True,
                  Hs: list | None = None, tmin: int = 0) -> list:
            """Every jump from this screen: [value of the plan to second T, tentative, second, leave, landing, lo, hi, option].
            Hs = the hidden streams as they are (for the second jump of a plan); no jump starts before second tmin."""
            xs, cl, rows = A[g], tabs[g].cl, []
            for lo, hi in lv:
                for o in ld:
                    pr = pair(lo, hi, c, w, xs if known else None, o, tmin)
                    if pr is None:
                        continue
                    tau, i, j = pr
                    up = tau + flip
                    if up >= T:
                        continue
                    va = seg(g, c, i, m0) - late_w * (w - c) * (i - c)
                    g2 = o[0]
                    if live0 + cl[min(n, i)] - cl[c] >= 3:
                        if not paid:
                            va += bonus
                    elif known and i <= xs.a1:
                        # leaving a stoppage that has shown no play: only for a play that starts well before this one would.
                        # (Two paused sticks in dead air would otherwise be hopped between for ever: neither moves on.)
                        k1 = bisect_left(tabs[g].lb, i)
                        here = (tabs[g].le[k1] if k1 < len(tabs[g].le) and eks[g][k1] <= K else KB + 1) - i
                        if (KB + 1 if (honest and eks[g2][o[2]] > K) else tabs[g2].le[o[2]]) - j + flip + HOP_GAIN_S > here:
                            continue
                    rows.append([va + stay(g2, j, up, j + ps.SEEN_LEAD_S, 0, False, leaves(g2, j, 0, 1), T), not known, tau, i, j, lo, hi, o, va])
            if d > 1 and rows:
                rows.sort(key=lambda r: -r[0])
                for row in rows[:beam]:
                    _, _, tau, i, j, _, _, o, va = row
                    g2, up = o[0], tau + flip
                    lv2 = leaves(g2, j, 0, nleaves)
                    # the stream that was left takes the place of the one jumped to: paused where the viewer left it (or
                    # `lpause` on), usable again once the jump is complete
                    H2 = list(Hs)
                    H2[o[6]] = (g, True, i + lpause, tau + max(1, lpause, jdone))
                    sub = jumps(g2, j, up, j + ps.SEEN_LEAD_S, 0, False, lv2, lands(g2, H2, tau + 1), d - 1, T, True, H2, tau + jdone)
                    if sub:
                        v = va + max(r[0] for r in sub)
                        if v > row[0]:
                            row[0] = v
            return rows

        # the screen now (believed), or as it will be when the flip in progress is over
        scr = S.sg
        tw = max(t, S.black, S.busy)
        cs = int(t - S.sb + S.se) + (tw - t)
        s_from = min(n - 1, max(0, int(S.s_from + S.se)))
        m0s = s_from + ps.SEEN_LEAD_S
        live0 = tabs[scr].cl[min(n, max(cs, s_from))] - tabs[scr].cl[s_from]
        if honest:                                    # the play it has shown is known only up to the game's last reported row
            live0 = tabs[scr].cl[max(s_from, min(n, max(cs, s_from), k1s[scr]))] - tabs[scr].cl[s_from]
        paid = live0 >= 3 or not self.st.hops        # the visit on screen has shown its play (the first one began with no jump)
        # the hidden streams now: (game, paused, position, from second)
        H = []
        for hg, hbh, he, ready, hp in ps.hidden_of(S):
            if hg < 0:
                H.append((-1, False, 0, t))
            elif t < ready:
                H.append((hg, hp, int(ready - hbh + he) - (1 if hp else 0), ready))
            else:
                H.append((hg, hp, int(t - hbh + he), t))
        hg, hp, c0, _ = H[0]
        lv = leaves(scr, cs, live0, nleaves)
        ld = lands(scr, H, t)
        if lv and tw + lv[0][0] - cs + flip + p.bank_s > T:
            T = tw + lv[0][0] - cs + flip + p.bank_s        # the first leave is further off than the horizon: plan to it
        v0 = stay(scr, cs, tw, m0s, live0, paid, lv, T)
        opts = jumps(scr, cs, tw, m0s, live0, paid, lv, ld, p.depth, T, True, H)
        best = max(opts, key=lambda r: r[0]) if opts else None
        landable = {row[7][0] for row in opts}       # games a reported leave could be paired with (for why())
        if best is None or best[0] <= v0 + 1e-9:
            # no jump worth making is in the feed's reports. Bank a landing: assume the next leave comes `exp_wait` past the
            # reports and put the hidden stick where the first `bank_s` seconds after that jump are worth most. The value
            # before the jump is the same for every landing, so only what follows it is compared. Never scheduled.
            i = (cs if cs > last else last + 1) + p.exp_wait
            opts = jumps(scr, i, tw + i - cs, i, 3, True, [(i, i + JOIN_SLACK)], ld, 1, tw + i - cs + flip + p.bank_s, False, H)
            best = max(opts, key=lambda r: r[0]) if opts else None
        if best is not None and self.go is not None and self.open is not None:
            # keep the scheduled jump unless another plan beats it by `stick`
            for row in opts:
                if not row[1] and row[7][0] == self.open[1] and abs(row[2] - self.open[0]) <= ps.PLAN_OK_S and row[0] > v0:
                    if best[0] - row[0] <= p.stick or (row[2] - t <= p.commit_s and best[0] - row[0] < MUST_W / 2):
                        best = row          # (a must-see the feed has just reported still wins over a committed jump)
                    break

        def settled(row, mine):
            """A stream that holds a game is not sent to another one for a gain smaller than `stick`."""
            if row is not None and row[7][1] == TUNE and H[row[7][6]][0] >= 0:
                here = max((r for r in mine if r[7][1] != TUNE), key=lambda r: r[0], default=None)
                if here is not None and row[0] - here[0] <= p.stick:
                    return here
            return row

        best = settled(best, opts)
        if self.trace is not None:
            self.trace.append((t, v0, cs, K, opts, best))
        # one command per hidden stream: the best plan's landing for the stream it jumps to; for every other stream the best
        # landing a jump to IT would have (so it is parked and ready for the look after this one); a stream with nothing to
        # land on that is idle, past the cap or on a finished game goes to the live edge of the game with the most leverage
        taken = {h[0] for h in H if h[0] >= 0} if len(H) > 1 else set()
        cmds = []
        for sx, (hg, hp, c0, _) in enumerate(H):
            row = best if (best is not None and best[7][6] == sx) else None
            if row is None and len(H) > 1:
                mine = [r for r in opts if r[7][6] == sx]
                row = settled(max(mine, key=lambda r: r[0]), mine) if mine else None
            if row is None:
                cmd = (hg, c0 if hp else -1, "")
                if hg < 0 or (hp and t - c0 > cap) or c0 > A[hg].a1:
                    cands = [g for g in range(N) if g != scr and A[g].a0 <= t and u[g] > 0 and (g == hg or g not in taken)]
                    if cands:
                        cmd = (max(cands, key=lambda g: u[g]), -1, "tune")
            else:
                g, route, k, _, _, pp, _ = row[7]
                cmd = (g, pp, "") if route == HOLD else (g, tabs[g].lb[k] - lead, "") if route == ROLL else (g, pp + 1, "move") if route == MOVE \
                    else (g, tabs[g].lb[k] - lead, "tune")
            taken.add(cmd[0])
            cmds.append(cmd)
        self.cmds = tuple(cmds)
        if best is None:
            self.go = self.open = None
            self.info = (bool(lv), landable, cap_hit, -1, False, False)
            return
        v, tent, tau, i, j, lo, hi, o, _ = best
        g, route, k, r, oc, pp, sx = o
        tb = tabs[g]
        jump = not tent
        self.info = (bool(lv), landable, cap_hit, g, tent, jump)
        if not jump:
            self.go = self.open = None
            return
        # (a landing in a stoppage the feed has not seen the end of has no known last second)
        self.go = (scr, lo, hi, g, tb.la[k], FAR if (honest and eks[g][k] > K) else tb.lb[k], tau, sx)
        if self.open is None or self.open[1] != g or abs(self.open[0] - tau) > ps.PLAN_OK_S:
            self.st.sched.append((t, tau, g))
        self.open = (tau, g)
        if tau - t <= p.commit_s and (self.firm is None or self.firm[1] != g or abs(self.firm[0] - tau) > ps.PLAN_OK_S):
            self.firm = (tau, g)            # the same jump, logged again the first time it is inside the commit window
            self.st.firm.append((t, tau, g))


# ---- running it ------------------------------------------------------------------------------------------------------

def setting(error: str, league_shots: bool, **kw) -> ps.Setting:
    """The planner's park_sim setting for one of the three timing scenarios. A landing may wait any time for its play (the
    plan prices the dead air), and the any-shot rule is on wherever the padding fits inside the 4 s window."""
    base = {"route": "tune", "pause_holds": True, "ping_pong": True, "land_wait_max": 999.0, "max_behind": pc.PAUSE_REACH_S,
            "delay_sd": ERRORS[error], "shots": league_shots, **ERR_FIXED}
    base.update(kw)
    return ps.Setting(**base)


def evaluate(nights: list, s: ps.Setting, plan: Plan, brain: str = "hindsight", unknown=None) -> dict:
    """One planner setting on these nights. The brain is only asked what it wants (the audit's yes-rate)."""
    runs, searches, t0 = [], 0, time.time()
    for night in nights:
        pl = Planner(plan, unknown)
        runs.append((night, ps.run(night, ps.BRAINS[brain](), s, planner=pl)[0]))
        searches += pl.searches
    row = ps.summarize(runs)
    row["searches"] = searches
    row["s_per_night"] = round((time.time() - t0) / max(1, len(nights)), 2)
    return row


DROP = 0.15          # the curve is read up to the point where graded value seen has fallen this far below its best


def report(path: str) -> None:
    """The trade-off curve as markdown, one table per slate and error. A row past the 15% fall in graded value is marked."""
    rows = list(csv.DictReader(open(path)))

    def f(r: dict, k: str) -> float:
        try:
            return float(r[k])
        except (KeyError, ValueError):
            return float("nan")

    def pc_(r: dict, k: str) -> str:
        v = f(r, k)
        return f"{100 * v:.0f}%" if v == v else "-"
    for slate, err in dict.fromkeys((r["slate"], r["error"]) for r in rows):
        part = [r for r in rows if (r["slate"], r["error"]) == (slate, err)]
        top = max(f(r, "v2_seen") for r in part if r["tag"].startswith("planner"))
        print(f"\n**{slate}, {err}: {part[0]['nights']} dev nights, {f(part[0], 'minutes'):.0f} minutes"
              f"{', SYNTHETIC slate' if '+' in slate else ''}. Simulated.**\n")
        print("| row | jumps/min | 5 / 10 / 20 / 30 / 45 / 60 min (median) | graded value seen | big plays seen | yes-rate (audit) | planned jump "
              "delivered (all / firm) | behind live s (median / p90) | mid-play joins / leaves / high-leverage leaves | flips | live share |")
        print("|---|---|---|---|---|---|---|---|---|---|---|")
        for r in part:
            past = r["tag"].startswith("planner") and f(r, "v2_seen") < (1.0 - DROP) * top
            win = " / ".join(r[f"per_{w}m_med"] for w in pc.WINDOWS_MIN)
            deliv = f"{pc_(r, 'planned_delivered')} / {pc_(r, 'firm_delivered')}" if r["tag"].startswith("planner") else "-"
            print(f"| {r['tag']}{' (past the 15% fall)' if past else ''} | {f(r, 'jumps_per_min'):.2f} | {win} | {pc_(r, 'v2_seen')} | "
                  f"{pc_(r, 'big_seen')} | {pc_(r, 'yes_rate')} | {deliv} | {f(r, 'behind_med_s'):.0f} / {f(r, 'behind_p90_s'):.0f} | "
                  f"{r['mid_entry']} / {r['mid_leave']} / {r['hl_leave']} | "
                  f"{round(f(r, 'hops_per_min') * f(r, 'minutes'))} | {pc_(r, 'live_share')} |")


def parse_plan(items: list[str]) -> dict:
    out, types = {}, {f.name: f.type for f in fields(Plan)}
    for it in items or ():
        k, v = it.split("=", 1)
        out[k] = int(v) if types[k] == "int" else float(v)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--slates", nargs="+", default=["nba 4"], help='Nick\'s slates by name: "nba 4" "mnf+3" (the mixed one is synthetic)')
    ap.add_argument("--errors", nargs="+", default=["spec"], choices=sorted(ERRORS))
    ap.add_argument("--bonus", nargs="+", type=float, default=[0.5], help="jump_bonus values (the trade-off curve)")
    ap.add_argument("--cap", type=int, default=10, help="dev nights per league (10 = all of them)")
    ap.add_argument("--brain", default="hindsight", choices=sorted(ps.BRAINS), help="whose wants the audit's yes-rate is counted on")
    ap.add_argument("--plan", action="append", default=[], help="key=value on Plan (repeatable)")
    ap.add_argument("--set", action="append", default=[], help="key=value on park_sim.Setting (repeatable)")
    ap.add_argument("--model", default=None, help="an .npz of trees (kaggle_park.py) that values the seconds the feed has not reported")
    ap.add_argument("--baseline", action="store_true", help="also run live-edge physics under the same padding")
    ap.add_argument("--cache", default=None, help="a folder OUTSIDE the repo for the per-game facts")
    ap.add_argument("--csv", default=None)
    ap.add_argument("--report", default=None, help="print the curve tables of a CSV written by --csv, and stop")
    a = ap.parse_args()
    if a.report:
        report(a.report)
        return 0
    cache = Path(a.cache) if a.cache else None
    base_plan = Plan(**parse_plan(a.plan))
    sets = ps.parse_sets(a.set)
    unknown = None
    if a.model:
        import park_table  # the model hook lives beside the tables it is trained on
        unknown = park_table.ModelBrain.load(a.model)
    w = fh = None
    for name in a.slates:
        nights = ps.slate_nights(name, a.cap, cache)
        if not nights:
            continue
        for err in a.errors:
            s = setting(err, True, **sets)
            todo = [("live-edge physics", None)] if a.baseline else []
            todo += [(f"planner bonus {b:g}", replace(base_plan, jump_bonus=b)) for b in a.bonus]
            for tag, plan in todo:
                if plan is None:
                    r = ps.evaluate(nights, a.brain, replace(s, mode="live"))
                else:
                    r = evaluate(nights, s, plan, a.brain, unknown)
                row = {"slate": name, "error": err, "tag": tag, "brain": a.brain,
                       **{k: getattr(s, k) for k in ("delay_sd", "pos_sd", "stamp_sd", "key_sd", "pad_k", "late_cap", "max_behind", "feed_lead", "shots")},
                       **(asdict(plan) if plan is not None else {f.name: "" for f in fields(Plan)}), **r}
                print(f"{name:6s} {err:6s} {tag:22s} {r['jumps_per_min']:5.2f}/min  yes {_pc(r['yes_rate'])}  delivered {_pc(r['planned_delivered'])}"
                      f"  value {_pc(r['v2_seen'])}  big {_pc(r['big_seen'])}  behind {r['behind_med_s']}/{r['behind_p90_s']}/{r['behind_max_s']}"
                      f"  breaks {r['mid_entry']}/{r['mid_leave']}/{r['hl_leave']}  nights {r['nights']}  {r.get('s_per_night', '')} s/night", flush=True)
                if a.csv:
                    if w is None:
                        fh = open(a.csv, "a", newline="")
                        w = csv.DictWriter(fh, fieldnames=list(row), extrasaction="ignore", restval="")
                        if fh.tell() == 0:
                            w.writeheader()
                    w.writerow(row)
                    fh.flush()
    if fh:
        fh.close()
    return 0


def _pc(v) -> str:
    return f"{100 * v:.0f}%" if isinstance(v, (int, float)) else "-"


if __name__ == "__main__":
    raise SystemExit(main())
