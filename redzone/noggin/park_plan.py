"""PARK planner: the search that plans the two-stick show (stay, jump, hold, roll, skip, tune) a few jumps ahead.

`Plan` (its knobs), `Tab` (one game's landing spots for one padding) and `Planner` (act() each second, why() for the
refused seconds, the search). Pure: it reads only the per-second grids of `park_core` and what the feed has reported.
Moved here unchanged from `scripts/park_plan.py`, which keeps the command line, the three timing scenarios and the
evaluation loop and imports the rest from here. What the planner values and what it never trades away (the Rule #1
padding, the must-see guarantee, the lateness cap): see the docstring of `scripts/park_plan.py`.
Nothing in the app imports this module yet (a later unit wires it in behind `park.enabled`).

Why: one copy of the PARK planner for the app, the scripts and the local show runner, so what the simulator measured
is what runs (INTEGRATION-PLAN unit PARK-CORE).
"""
from __future__ import annotations

import math
from bisect import bisect_left
from dataclasses import dataclass

from redzone.noggin.park_core import PARK_PARAMS, Arr, ParkParams, Setting

MUST_W = 1000.0                      # brain points per must-see point: more than a night of grades and bonuses
FAR = 10 ** 9                        # the end of a stretch that never ends
HOLD, ROLL, MOVE, TUNE = 0, 1, 2, 3  # how the hidden stick gets to a landing
ROUTES = ("hold", "roll", "skip", "tune")
FRESH_BEHIND = 20                    # a stick is re-tuned onto its own game only when it sits at least this far behind
JOIN_SLACK = 5                       # a leave the feed has not reported is assumed to stay open this long


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
    __slots__ = ("x", "la", "lb", "le", "cl", "cv")

    def __init__(self, x: Arr, guard: int):
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


def tabs_for(A: list, guard: int) -> list[Tab]:
    out = []
    for x in A:
        cache = x.__dict__.setdefault("_plan_tabs", {})
        if guard not in cache:
            cache[guard] = Tab(x, guard)
        out.append(cache[guard])
    return out


class Planner:
    """Drives park_core.run(planner=...). act() each second -> (jump now, the hidden stick's game, the spot to pause at, how).
    `params` holds the timing numbers the search reads (an instance may be given its own)."""
    name = "planner"
    params: ParkParams = PARK_PARAMS

    def __init__(self, plan: Plan | None = None, unknown=None):
        self.p = plan or Plan()
        self.unknown = unknown        # None, or an object with bind(arrs, setting) and rate(game, last reported second, now):
        #                               the value of one second the feed has not reported (scripts/park_table.py ModelBrain)
        self.A = None
        self.searches = 0
        self.trace: list | None = None    # set to a list to keep every search's options (park_table reads them)
        self._reset()

    def _reset(self) -> None:
        self.next = -1                # the next second a search is due
        self.land = None              # S.s_land at the last search: a new landing forces one
        self.cmd = (-1, -1, "")       # the hidden stick's game, the spot to pause at, a one-off "tune" | "move"
        self.go = None                # the jump being waited for: (screen game, lo, hi, game, la, lb, planned second)
        self.open = None              # the scheduled jump last logged: (planned second, game)
        self.firm = None              # the scheduled jump last logged inside the commit window
        self.info = (False, (), (), -1, False, False)   # for why(): leave reported, games with a landing, capped games, plan game, tentative, jump

    def bind(self, A: list, s: Setting, *, guard: int, reach: int, flip: int, lead: int, know: int, n: int, stats) -> None:
        if self.A is not A:
            self._reset()
        self.A, self.s, self.G, self.reach, self.flip, self.lead, self.know, self.n = A, s, guard, reach, flip, lead, know, n
        self.st = stats
        self.tabs = tabs_for(A, guard)
        self.tune = int(math.ceil(s.tune_s + s.load_slow_s))
        self.lw = s.live_w if s.value == "dense" else 0.0
        self.min_live = int(math.ceil(s.min_live_s))
        if self.unknown is not None:
            self.unknown.bind(A, s)

    # ---- each second ----------------------------------------------------------------------------------------------

    def act(self, t: int, S) -> tuple:
        if t >= self.next or S.s_land != self.land or t == S.h_ready:
            self._search(t, S)
        game, spot, how = self.cmd
        if how:
            self.cmd = (game, spot - 1 if how == "move" else spot, "")     # a skip ends one second short of where it is sent
            self.next = t + 1
            return False, game, spot, how
        go = self.go
        if go is not None and S.sg == go[0] and S.hg == go[3] and t >= S.h_ready and t >= S.black:
            cs = int(t - S.sb + S.se)
            if go[1] <= cs <= go[2]:
                hb = int(t - S.hb + S.he)
                # park_sim pauses a stick that has reached its spot before it looks at the jump, in the same second
                paused = S.h_paused or (0 <= spot <= hb and go[4] <= hb <= go[5] and hb + self.G <= t + self.know)
                j = hb + (self.lead if paused else self.flip)
                if go[4] <= j <= go[5]:
                    self.next = t + 1         # whether or not park_sim lets it through, look again next second
                    return True, game, spot, ""
        return False, game, spot, ""

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
        p, tabs, A, prm = self.p, self.tabs, self.A, self.params
        G, flip, lead, reach, n = self.G, self.flip, self.lead, self.reach, self.n
        lw, late_w, bonus, min_live, tune, spots = self.lw, p.late_w, p.jump_bonus, self.min_live, self.tune, p.spots
        nleaves, beam = p.leaves, p.beam
        K = min(n - 2, t + self.know)             # the last second of every game the feed has reported
        rho = 0.0                                  # what a second is worth in jumps still to come
        if p.cycle_k > 0 and bonus > 0:
            spots_n = secs = 0
            for tb in tabs:
                if tb.x.a0 < K:
                    spots_n += bisect_left(tb.le, K + 1) - bisect_left(tb.le, tb.x.a0)
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

        def seg(g: int, a: int, b: int, m0: int) -> float:
            """The value of game g's seconds a..b-1 on screen; graded plays count from second m0 (7 s after the landing)."""
            if b > n:
                b = n
            if b <= a:
                return 0.0
            tb = tabs[g]
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
            la, lb, le, cl = tb.la, tb.lb, tb.le, tb.cl
            k, m = bisect_left(lb, c), len(lb)
            need = min_live - live0
            while k < m and len(out) < lim:
                lo = la[k]
                if lo < c:
                    lo = c
                if lo > last:
                    break
                hi = lb[k]
                if hi > last:
                    hi = last
                if need > 0 and cl[lo] - cl[c] < need:    # no play shown yet: only a long stoppage may be left
                    h2 = (le[k] if le[k] <= K else K1) - prm.long_stop_s
                    if h2 < hi:
                        hi = h2
                if lo <= hi:
                    lo = calm(x, lo, hi)
                    if lo >= 0:
                        out.append((lo, hi))
                k += 1
            return out

        def lands(scr: int, hg: int, hp: bool, c0: int, w0: int, tc: int) -> list:
            """Where the hidden stick could be landed on: (game, route, stretch, ready second, position then, paused position
            or -1). The stick holds game hg at position c0 from second w0 (paused if hp); a new key can go at second tc."""
            out = []
            wr = cr = tc
            if hg >= 0:
                tb = tabs[hg]
                la, lb, le = tb.la, tb.lb, tb.le
                k, m = bisect_left(lb, c0 + lead), len(lb)
                wr = w0 if w0 > tc else tc
                cr = c0 if hp else c0 + (wr - w0)
                if hp and k < m and la[k] <= c0 + lead <= last:
                    out.append((hg, HOLD, k, w0, c0, c0))
                    k += 1
                kk, cnt = k, 0
                while kk < m and cnt < spots and le[kk] <= K:      # play on to a later stretch and pause before its play
                    if lb[kk] - lead >= cr:
                        out.append((hg, ROLL, kk, wr, cr, -1))
                        cnt += 1
                    kk += 1
                if w0 <= tc:                                        # or skip there in 15 s steps
                    kk, cnt = k, 0
                    while kk < m and cnt < spots:
                        q = lb[kk] - lead
                        if le[kk] > K or q > tc - prm.same_behind_s:
                            break
                        d = q + 1 - cr
                        if d >= prm.skip_step_s:
                            r = int(math.ceil(tc + 2.0 + prm.press_s * math.floor(d / prm.skip_step_s) + d % prm.skip_step_s))
                            out.append((hg, MOVE, kk, r, q, q))
                            cnt += 1
                        kk += 1
            w1 = tc + tune                                          # a tune puts the stick at the live edge
            for g in range(N):
                x = A[g]
                if g == scr or x.a0 > t or (x.a1 <= K and tc > x.a1) or (g == hg and wr - cr < FRESH_BEHIND):
                    continue
                tb = tabs[g]
                lb, le = tb.lb, tb.le
                k, m, cnt = bisect_left(lb, w1 + lead), len(lb), 0
                while k < m and cnt < spots and le[k] <= K:
                    out.append((g, TUNE, k, w1, w1, -1))
                    cnt += 1
                    k += 1
            return out

        def pair(lo: int, hi: int, c: int, w: int, xs, opt: tuple):
            """The first second the screen (position c at second w, legal to leave at lo..hi) can jump to this landing:
            (second, leave position, landing position) or None. xs = None for a leave the feed has not reported."""
            g, _, k, r, c0, pp = opt
            tau = w + lo - c
            if tau < r:
                tau = r
            if pp >= 0:
                j = pp + lead
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
                if ct + flip <= lb_k:
                    j = ct + flip
                    if r - c0 > cap:
                        cap_hit.add(g)
                        return None
                else:
                    wp = r + lb_k - lead - c0             # the second it is paused before the play
                    if tau < wp:
                        tau = wp
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

        def jumps(g: int, c: int, w: int, m0: int, live0: int, paid: bool, lv: list, ld: list, d: int, T: int, known: bool = True) -> list:
            """Every jump from this screen: [value of the plan to second T, tentative, second, leave, landing, lo, hi, option]."""
            xs, cl, rows = A[g], tabs[g].cl, []
            for lo, hi in lv:
                for o in ld:
                    pr = pair(lo, hi, c, w, xs if known else None, o)
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
                        here = (tabs[g].le[k1] if k1 < len(tabs[g].le) and tabs[g].le[k1] <= K else K1) - i
                        if tabs[g2].le[o[2]] - j + flip + prm.hop_gain_s > here:
                            continue
                    rows.append([va + stay(g2, j, up, j + prm.seen_lead_s, 0, False, leaves(g2, j, 0, 1), T), not known, tau, i, j, lo, hi, o, va])
            if d > 1 and rows:
                rows.sort(key=lambda r: -r[0])
                for row in rows[:beam]:
                    _, _, tau, i, j, _, _, o, va = row
                    g2, up = o[0], tau + flip
                    lv2 = leaves(g2, j, 0, nleaves)
                    sub = jumps(g2, j, up, j + prm.seen_lead_s, 0, False, lv2, lands(g2, g, True, i, tau + 1, tau + 1), d - 1, T)
                    if sub:
                        v = va + max(r[0] for r in sub)
                        if v > row[0]:
                            row[0] = v
            return rows

        # the screen now (believed), or as it will be when the flip in progress is over
        scr = S.sg
        tw = t if t >= S.black else S.black
        cs = int(t - S.sb + S.se) + (tw - t)
        s_from = min(n - 1, max(0, int(S.s_from + S.se)))
        m0s = s_from + prm.seen_lead_s
        live0 = tabs[scr].cl[min(n, max(cs, s_from))] - tabs[scr].cl[s_from]
        paid = live0 >= 3 or not self.st.hops        # the visit on screen has shown its play (the first one began with no jump)
        # the hidden stick now
        hg = S.hg
        if hg < 0:
            hp, c0, w0 = False, 0, t
        elif t < S.h_ready:
            hp, w0 = S.h_paused, S.h_ready
            c0 = int(S.h_ready - S.hb + S.he) - (1 if hp else 0)
        else:
            hp, c0, w0 = S.h_paused, int(t - S.hb + S.he), t
        lv = leaves(scr, cs, live0, nleaves)
        ld = lands(scr, hg, hp, c0, w0, t)
        if lv and tw + lv[0][0] - cs + flip + p.bank_s > T:
            T = tw + lv[0][0] - cs + flip + p.bank_s        # the first leave is further off than the horizon: plan to it
        v0 = stay(scr, cs, tw, m0s, live0, paid, lv, T)
        opts = jumps(scr, cs, tw, m0s, live0, paid, lv, ld, p.depth, T)
        best = max(opts, key=lambda r: r[0]) if opts else None
        landable = {row[7][0] for row in opts}       # games a reported leave could be paired with (for why())
        if best is None or best[0] <= v0 + 1e-9:
            # no jump worth making is in the feed's reports. Bank a landing: assume the next leave comes `exp_wait` past the
            # reports and put the hidden stick where the first `bank_s` seconds after that jump are worth most. The value
            # before the jump is the same for every landing, so only what follows it is compared. Never scheduled.
            i = (cs if cs > last else last + 1) + p.exp_wait
            opts = jumps(scr, i, tw + i - cs, i, 3, True, [(i, i + JOIN_SLACK)], ld, 1, tw + i - cs + flip + p.bank_s, False)
            best = max(opts, key=lambda r: r[0]) if opts else None
        if best is not None and self.go is not None and self.open is not None:
            # keep the scheduled jump unless another plan beats it by `stick`
            for row in opts:
                if not row[1] and row[7][0] == self.open[1] and abs(row[2] - self.open[0]) <= prm.plan_ok_s and row[0] > v0:
                    if best[0] - row[0] <= p.stick or (row[2] - t <= p.commit_s and best[0] - row[0] < MUST_W / 2):
                        best = row          # (a must-see the feed has just reported still wins over a committed jump)
                    break
        if best is not None and best[7][1] == TUNE and hg >= 0:
            # the hidden stick is not sent to another game for a gain smaller than `stick`
            here = max((row for row in opts if row[7][1] != TUNE), key=lambda r: r[0], default=None)
            if here is not None and best[0] - here[0] <= p.stick:
                best = here
        if self.trace is not None:
            self.trace.append((t, v0, cs, K, opts, best))
        if best is None:
            self.go = self.open = None
            self.info = (bool(lv), landable, cap_hit, -1, False, False)
            # nothing to land on yet: an idle stick, or one that can no longer be landed on, goes to the live edge of the
            # game with the most leverage and waits there for a spot
            if hg < 0 or (hp and t - c0 > cap) or c0 > A[hg].a1:
                cands = [g for g in range(N) if g != scr and A[g].a0 <= t and u[g] > 0]
                self.cmd = (max(cands, key=lambda g: u[g]), -1, "tune") if cands else (hg, c0 if hp else -1, "")
            else:
                self.cmd = (hg, c0 if hp else -1, "")
            return
        v, tent, tau, i, j, lo, hi, o, _ = best
        g, route, k, r, oc, pp = o
        tb = tabs[g]
        if route == HOLD:
            self.cmd = (g, pp, "")
        elif route == ROLL:
            self.cmd = (g, tb.lb[k] - lead, "")
        elif route == MOVE:
            self.cmd = (g, pp + 1, "move")
        else:
            self.cmd = (g, tb.lb[k] - lead, "tune")
        jump = not tent
        self.info = (bool(lv), landable, cap_hit, g, tent, jump)
        if not jump:
            self.go = self.open = None
            return
        self.go = (scr, lo, hi, g, tb.la[k], tb.lb[k], tau)
        if self.open is None or self.open[1] != g or abs(self.open[0] - tau) > prm.plan_ok_s:
            self.st.sched.append((t, tau, g))
        self.open = (tau, g)
        if tau - t <= p.commit_s and (self.firm is None or self.firm[1] != g or abs(self.firm[0] - tau) > prm.plan_ok_s):
            self.firm = (tau, g)            # the same jump, logged again the first time it is inside the commit window
            self.st.firm.append((t, tau, g))
