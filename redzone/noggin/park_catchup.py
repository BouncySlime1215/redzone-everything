"""Catch-up on game X: the PARK show's third move beside stay and jump (flag park.catchup.enabled, off).

Why: Nick, Nov 11: when the show returns to a game after a while, "we're going to give you like 4 or 5 plays back to back,
and then we'll go back and join live" (phase 2 plan, 2C item 3). This is the director action that does it on two sticks.

The move, on the two sticks PARK already has (H = the hidden stick, parked on game X by the planner; V = the stick on screen):
  1. PREP (H hidden): H is sent back to the first clip's start ("queue it to the snap"), paused there. The screen keeps
     its game; the planner runs on, but H is the catch-up's (no jump, no step on it).
  2. LEAVE: at a padded dead ball of the screen's game, the same guard as a jump's leave (Show.guard's `refusal`), and
     never with a big play or high leverage in the feed's next look_ahead_s: the TV flips to H. The replay plays.
  3. CLIPS: H plays each clip; between two it is sent to the next one's start (on screen: a re-seek). Meanwhile V,
     free now, is tuned to game X LIVE and parked on a padded dead ball, as the planner parks any stream.
  4. JOIN: when the clips are done, the live join is an ordinary PARK landing: the hard guard again (landing at a
     padded dead ball, within the lateness cap), then V is resumed and the TV flips to it. Rule #1 and the frozen grader
     judge it like any jump. The replay H showed is not a live picture: its end is not a Rule #1 leave.
  Then the planner takes over again with nothing parked: H is tuned to whatever it wants next.

Never traded for a catch-up: no leave inside a padded high-leverage span or outside a padded dead ball; nothing
started in crunch time; a pinned (kept) stick is never used; any failed step before the leave drops the catch-up
(H is re-tuned; the picture that is up stays up). After the leave, the live join still waits for a guarded dead ball
(the replay plays on meanwhile).

Which plays and from where: redzone/noggin/catchup_pack.py (pure). The device steps are the rig's `cu_cue(k, game,
frame, source, visible) -> (ok, seconds)`: put stick k at grid second `frame` of `game` by `source` (rewind | reel |
moments), paused. The replay's fake rig has every source and cues on screen too (scripts/park_replay.py CatchupRig).
The app's ControllerRig (redzone/park_run.py, which builds this class when park.catchup.enabled is on: task E-01) cues
only the HIDDEN stick by rewind (Controller.park_cue; not yet run on the TV, task E-02) and says so with
`cue_on_screen = False`: then a package is one clip (a close pair played through counts as two plays, under Nick's age
rule for its play count), the live join is never made by re-cueing the replay stick, and a game whose way back needs
that ("h_join") gets no catch-up. A rig without `cu_cue` never starts one (fail closed).

The seen-state seam: `SeenLike.last_seen(game)`, the game's own grid second last on screen. With park.seen on the show
has the real tracker (redzone/noggin/seen.py, `Show.seen_state`) and `TrackerSeen` answers from it; otherwise `ShowSeen`
answers from the show's own `seen_to`.

Pure apart from the rig: no clock or I/O of its own. The app imports it only with park.catchup.enabled on.
"""
from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from dataclasses import dataclass, fields, replace
from typing import Protocol

from redzone.noggin import catchup_pack as cp
from redzone.models import League
from redzone.noggin.park_show import Show, _result, refusal
from redzone.sources import espn


@dataclass(frozen=True)
class CatchupParams:
    """The move's numbers (seconds). `park.catchup.<name>`; the package's own are catchup_pack.PackParams."""
    enabled: bool = False           # OFF
    min_gap_s: int = 120            # "after a while": a game unseen this long gets a catch-up when the show returns
    cool_s: int = 300               # no second catch-up of one game within this
    leave_wait_s: int = 20          # prep done: a guarded leave within this, or the catch-up is dropped
    join_wait_s: int = 30           # clips done: V's live join within this (the replay plays on), else H itself is re-cued to live
    replay_dead_s: int = 12         # ...and the cut leaves the replay on its own dead ball when one comes within this
    look_ahead_s: int = 30          # never leave the screen's game with a big play or high leverage this close in the feed
    hot_look_s: int = 300           # no catch-up (and no further clip) while another live game had a high-leverage play this recently
    plan_wait_s: float = 10.0       # the plan allows this for the leave after the prep (a later leave re-checks every clip)
    pack: cp.PackParams = cp.PackParams()

    @classmethod
    def from_config(cls, get: Callable[..., object]) -> CatchupParams:
        kw: dict = {}
        for f in fields(cls):
            if f.name == "pack":
                continue
            d, v = f.default, get(f"park.catchup.{f.name}", f.default)
            if isinstance(d, bool):
                kw[f.name] = v if isinstance(v, bool) else d
            else:
                kw[f.name] = type(d)(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else d
        return cls(pack=cp.PackParams.from_config(get), **kw)


class SeenLike(Protocol):
    """The seen-state's one question here."""

    def last_seen(self, game: int) -> float | None:
        """The last grid second of `game` the viewer saw live; None = never seen."""


class ShowSeen:
    """Stub of the seen-tracker: the show's own `seen_to`."""

    def __init__(self, show: Show):
        self.show = show

    def last_seen(self, game: int) -> float | None:
        s = self.show.seen_to[game]
        return None if s < 0 else float(s)


class TrackerSeen:
    """The real seen-tracker (redzone/noggin/seen.py, park.seen): the game's own second last on screen."""

    def __init__(self, tracker):
        self.tracker = tracker

    def last_seen(self, game: int) -> float | None:
        p = self.tracker.g[game].last_p
        return None if p is None else float(p)


def plays_of(show: Show, g: int, P: cp.PackParams) -> list[cp.Play]:
    """Game g's plays as its feed has reported them so far, on the show's grid, with what the package weighs: points,
    lead changes, ties, runs (from the scores), the grader's big plays and graded value (the show's own arrays), and
    each play's start: football the snap (the dead time before it ends), basketball the row before it (the rebound, the
    steal, the inbound). A basketball play with no row in the 36 s before it has no clean start and gets no clip."""
    fx, lg = show.feed.fx[g], show.leagues[g]
    football = League(lg).base == League.NFL
    d, base, x = show.feed.shifts[g], show.night.base, show.A[g]
    n = x.n
    out: list[cp.Play] = []
    hs0 = as0 = 0
    run_side, run = 0, 0
    prev = last = None                                           # the latest earlier row at another second; the latest row
    for p, w in zip(fx.plays, fx.wall):
        s = w + d - base
        if last is not None and s - 0.5 > last:
            prev = last
        hs, as_ = espn._int(p.get("homeScore")), espn._int(p.get("awayScore"))
        pts, side = 0, 0
        if hs is not None and as_ is not None:
            dh, da = hs - hs0, as_ - as0
            pts, side = max(0, dh) + max(0, da), (1 if dh > 0 else -1 if da > 0 else 0)
        i = min(n - 1, max(0, int(s)))
        big = sum(x.mom.get(k, 0.0) for k in range(i - 2, i + 3))
        val = max([x.v2.get(k, 0.0) for k in range(i - 1, i + 2)] + [0.0])
        if pts > 0 or big > 0 or val > 0:
            before = (hs0 > as0) - (hs0 < as0)
            after = ((hs or 0) > (as_ or 0)) - ((hs or 0) < (as_ or 0))
            if side:
                run = run + pts if side == run_side else pts
                run_side = side
            if football:
                j = i - 1
                while j > i - 20 and j > 0 and not x.dead[j - 1]:
                    j -= 1
                start = float(j) if j > i - 20 and not x.dead[j] else s - P.snap_s
            else:
                start = prev if prev is not None and s - prev <= P.possession_s + P.lead_s + 10.0 else None
            if start is not None:                                # no event in reach before it: no clean start, no clip
                out.append(cp.Play(g, s, min(start, s), pts, bool(pts and after and after != before), bool(pts and not after and before),
                                   run if side else 0, big, val, float(x.lev[i]), str(p.get("text") or "")[:80],
                                   espn._int(espn._get(p, "period", "number")) or 0, str(espn._get(p, "clock", "displayValue") or "")))
        if hs is not None and as_ is not None:
            hs0, as0 = hs, as_
        last = s
    return out


def _since_label(show: Show, g: int, since: float | None) -> str:
    if since is None:
        return "the start"
    row = show.feed.line(g, int(since))
    per, clock = row.get("period"), row.get("clock")
    return f"{per} {clock}" if per and clock else "last look"


class CatchupShow(Show):
    """The PARK show with the catch-up move (park.catchup.enabled). Off, or on a rig without `cu_cue`, it is the show."""

    def __init__(self, *args, catchup: CatchupParams | None = None, cu_seen: SeenLike | None = None, **kw):
        super().__init__(*args, **kw)
        c = catchup or CatchupParams()
        self.cp = replace(c, pack=replace(c.pack, delay_s=float(self.P.show_delay)))
        tracker = getattr(self, "seen_state", None)
        self.cu_seen = cu_seen if cu_seen is not None else TrackerSeen(tracker) if tracker is not None else ShowSeen(self)
        self.cu: dict | None = None                              # the catch-up under way
        self.cu_pkg: cp.Package | None = None
        self.cu_ok = bool(self.cp.enabled) and not self.rig.instant and callable(getattr(self.rig, "cu_cue", None))
        self.cu_screen = getattr(self.rig, "cue_on_screen", True) is not False   # can the stick on screen be re-cued
        self.cu_log: list = []                                   # one row per catch-up that reached the screen
        self.cu_chances: dict = {}                               # (game, park) -> "" started, or why not
        self.cu_aborts: dict = {}
        self.cu_last: dict = {}                                  # game -> second its last catch-up ended
        self.on_since = [-math.inf, -math.inf]                   # per stream: second it was tuned to its game

    # ---- the hooks ---------------------------------------------------------------------------------------------------
    async def tick(self, t: int) -> None:
        cu = self.cu
        if cu is not None and cu["phase"] in ("clips", "join"):
            await self._cu_screen(t)                             # the replay is on screen: the planner waits
            return
        if cu is not None and self.S is not None:
            self.S.h_ready = max(self.S.h_ready, t + 1)          # the hidden stick is the catch-up's: no jump, no step on it
        await super().tick(t)
        if self.cu_ok and not self.stop:
            self._cu_step(t)

    async def _drive(self, t: int) -> None:
        if self.cu is None:
            await super()._drive(t)

    def flip_pending(self) -> bool:
        """A flip of the catch-up's own is under way (the flip to the replay, or its live join): the show's `on` already
        names the stick it flips to while the TV still shows the other. The app's loop (ParkRun.step) asks this before
        it calls the input changed outside the show."""
        cu = self.cu
        return cu is not None and ((cu["phase"] == "clips" and cu.get("up") is None) or cu.get("join") is not None)

    def _hidden_done(self, kind: str, game: int, res: tuple, t: int) -> None:
        super()._hidden_done(kind, game, res, t)
        if kind == "tune" and res and res[0]:
            self.on_since[1 - self.on] = float(t)

    # ---- the director's seam -----------------------------------------------------------------------------------------
    def catchup_offer(self, t: int, game: int) -> tuple[cp.Package | None, str]:
        """For a director (Director v1, branch seen-director): the package a catch-up of `game` would play if the show
        returned to it now with the hidden stick, or why not. Asks only; starts nothing."""
        if not self.cu_ok or self.S is None:
            return None, "park.catchup is off or the sticks cannot cue a clip"
        why = self._cu_why_not(t, game, 1 - self.on, self.on, self.cu_seen.last_seen(game))
        return (None, why) if why else (self.cu_pkg, "")

    # ---- before the leave --------------------------------------------------------------------------------------------
    def _cu_step(self, t: int) -> None:
        cu = self.cu
        if cu is None:
            self._cu_consider(t)
        elif cu["phase"] == "prep":
            task = cu["task"]
            if task.done():
                ok, took = _result(task)
                if not ok:
                    self._cu_abort("its first clip could not be cued", t)
                else:
                    cu.update(phase="leave", ready=t, prep_took=float(took or 0.0))
        elif cu["phase"] == "leave":
            self._cu_leave(t)

    def _cu_consider(self, t: int) -> None:
        S, rig, C = self.S, self.rig, self.cp
        if S is None or self.j_op is not None or self.h_op is not None or t < S.black:
            return
        h, v, g = 1 - self.on, self.on, S.hg
        if g < 0 or g == S.sg or rig.game[h] != g or not rig.paused[h] or t < S.h_ready:
            return
        since = self.cu_seen.last_seen(g)
        if since is not None and t - since < C.min_gap_s:
            return
        key = (g, int(round(rig.frozen[h])))                     # this park (its frozen frame)
        if self.cu_chances.get(key) == "":
            return
        why = self._cu_why_not(t, g, h, v, since)
        if why:
            self.cu_chances[key] = why
            return
        pkg = self.cu_pkg
        self.cu_chances[key] = ""
        c0 = pkg.clips[0]
        self.cu = {"phase": "prep", "game": g, "h": h, "v": v, "pkg": pkg, "t0": t, "since": since, "key": key,
                   "mode": self._cu_mode(v), "task": asyncio.ensure_future(rig.cu_cue(h, g, c0.lo, c0.source, False))}
        self.say(f"CATCH-UP: setting up {len(pkg.plays)} plays of {self.names[g]} ({pkg.line}); the screen stays put")
        self.emit({"ev": "catchup", "what": "prep", "tick": t, "game": g, "plays": len(pkg.plays), "screen_s": round(pkg.screen_s, 1),
                   "prep_s": round(pkg.prep_s, 1), "sources": pkg.sources})

    def _cu_why_not(self, t: int, g: int, h: int, v: int, since: float | None) -> str:
        """Why no catch-up of game g now ("" = go; the package is left in self.cu_pkg)."""
        C = self.cp
        if self.keep is not None and self.keep == h:
            return "a pinned stick"                              # the kept stream is never sent back
        if self._cu_mode(v) == "h_join" and (g in self.no_back or not self.cu_screen):
            return "no way back to live"                         # H must join live itself, and this game's app (or the rig) cannot seek
        if self.anchor_now >= 0:
            return "crunch time"
        if self._cu_alarm(t) is not None:
            return "a big moment is on or coming"
        if t - self.cu_last.get(g, -10 ** 9) < C.cool_s:
            return "caught up on it lately"
        if t < self.block.get(g, -1):
            return "its tune failed lately"
        plays = plays_of(self, g, C.pack)
        sel = cp.select(plays, -math.inf if since is None else since, math.inf, C.pack)
        if sel and not self.cu_screen:                           # no re-cue on screen: the best stretch that plays through
            sel = max(cp._segments(sel, C.pack), key=lambda seg: (sum(cp.worth(p, C.pack) for p in seg), seg[-1].at))
        if not sel:
            return "nothing missed worth a clip"
        app = "prime" if g in self.no_back else "yttv"
        stick = cp.Stick(app, self.on_since[h])
        pkg = cp.plan(sel, float(t), stick, C.pack, wait_s=C.plan_wait_s, flip_s=float(self.flip), game=g)
        if pkg is None:
            return "no source can show it in time"
        wait = self._cu_exit_after(t, pkg)
        if wait is None:
            return "no exit known after the prep"                # the feed shows no safe leave once H could be ready
        if wait > C.plan_wait_s:
            pkg = cp.plan(sel, float(t), stick, C.pack, wait_s=wait, flip_s=float(self.flip), game=g)
            if pkg is None:
                return "no source can show it in time"
        if not self.cu_screen and len(pkg.clips) > 1:
            return "it needs a cue on screen"                    # (a stretch that could not play through)
        self.cu_pkg = replace(pkg, line=cp.recap_line(pkg.plays, _since_label(self, g, since), C.pack))
        return ""

    def _cu_alarm(self, t: int) -> int | None:
        """The game that needs the screen now, or None (Nick's first rule: never miss a big play). A live game is
        alarming when a high-leverage play (the grader's own close-and-late rule) is in its feed's last hot_look_s, or
        when the feed has reported a must-see that has not reached the show's edge yet (it is coming on screen). Of
        several, the one with the most leverage. No catch-up starts while one is; one under way is cut for it."""
        best, top = None, -1.0
        for g in range(self.N):
            x = self.A[g]
            if not x.a0 <= t <= x.a1:
                continue
            e = min(x.n - 1, max(0, min(t + self.know, self.feed.edge(g))))
            hot = x.hlc[e + 1] - x.hlc[max(0, e - self.cp.hot_look_s)] > 0
            if (hot or any(t < m <= e for m in x.mom)) and x.lev[e] > top:
                best, top = g, x.lev[e]
        return best

    def _cu_mode(self, v: int) -> str:
        """How the live join is made. "v_join": V (the stick on screen) is tuned to the game live, parked, then flipped
        to. "h_join": V keeps its game (held at the leave, or playing on if it is the pinned stream), to be the hidden
        stick on the way back, and H itself is re-cued to a fresh dead ball after the clips: used when V is pinned or
        holds a Prime game (its tune takes about 100 s and it has no rewind)."""
        S = self.S
        return "h_join" if (self.keep is not None and self.keep == v) or (S is not None and S.sg in self.no_back) else "v_join"

    def _cu_exit_after(self, t: int, pkg: cp.Package) -> float | None:
        """Seconds from the prep's end to the first padded leave of the screen's game the feed already shows (a known
        dead ball, calm), within leave_wait_s; None when there is none. The leave itself is still guarded when it comes:
        this only keeps a prep from being thrown away (a dropped prep costs the planner its park)."""
        S = self.S
        g1 = S.sg
        x = self.A[g1]
        if t > x.a1 + S.sb:
            return 0.0                                           # the game on screen is over: any second will do
        i = int(t - S.sb + S.se) + int(math.ceil(pkg.prep_s + 1.0))
        if not 0 <= i < x.n:
            return None
        w = x.leave_in(self.pad0 + self.extra_of(self.on, g1), False)[i]
        known = min(self.feed.edge(g1) - self.P.open_hold, t + self.know)
        if w > self.cp.leave_wait_s or i + w + self.pad0 > known or not self._cu_break_ok(g1, min(x.n - 1, i + w), pkg):
            return None
        return float(w)

    def _cu_break_ok(self, g1: int, i: int, pkg: cp.Package) -> bool:
        """Football comes first (CLAUDE.md league order): a football game is left for a catch-up only in a break that
        covers the clips, never between the plays of a drive. Basketball is left at any guarded dead ball."""
        if not self.football[g1]:
            return True
        x = self.A[g1]
        return bool(x.brk[i]) and x.left[i] >= min(pkg.screen_s + self.cp.join_wait_s, 150.0)

    def _cu_leave(self, t: int) -> None:
        cu, S, C = self.cu, self.S, self.cp
        if self.anchor_now >= 0 or self._cu_alarm(t) is not None:
            self._cu_abort("a big moment began or is coming", t)
            return
        if t - cu["ready"] > C.leave_wait_s:
            self._cu_abort("no safe exit in time", t)
            return
        g1 = S.sg
        x = self.A[g1]
        i = int(t - S.sb + S.se)
        over = t > x.a1 + S.sb
        if t < self.no_jump_until:
            return                                               # a step failed lately: no flip yet
        if not over:
            if t - S.s_land < self.s.min_stay or self._cu_leave_why(t, g1, i) or not self._cu_break_ok(g1, i, cu["pkg"]):
                return
            hi = min(x.n - 1, i + C.look_ahead_s)
            if x.hlc[hi + 1] - x.hlc[max(0, i)] or any(m in x.mom for m in range(i + 1, hi + 1)):
                return                                           # a big play or high leverage is coming on screen: stay
        pkg = cu["pkg"]
        shift = (t + self.flip) - pkg.clips[0].air
        n = len(pkg.plays)
        clips = [c for c in pkg.clips if cp.age_ok(c.lo, c.air + shift, C.pack, n)]
        if not clips or clips[0] is not pkg.clips[0]:
            self._cu_abort("its first clip would air too late", t)
            return
        # the leave: same moment a jump would take, the replay goes up on H
        if self.judge is not None and hasattr(self.judge, "leave_true"):
            cu["true_i"] = self.judge.leave_true(self.on, g1, i)
        self._hold_end()
        look = t - (self.since if self.since is not None else t)
        self.looks.append((look, self.look_live))
        cu.update(phase="clips", leave=t, g1=g1, i=i, clips=clips, pkg_s=0, shown=[], k=-1, up=None, done=None, target=cu["game"],
                  end=t + self.flip + sum(c.gap + c.secs for c in clips),
                  v_task=None, v_tuned=None, spot=None, p_task=None, p_kind="", j=None, join=None, tries=0, wait_from=None)
        self.on = cu["h"]
        cu["task"] = asyncio.ensure_future(self._cu_run(cu))
        self.say(f"CATCH-UP: leaving {self.names[g1]} at a dead ball for {len(cu['pkg'].plays)} plays of {self.names[cu['game']]}")

    def _cu_leave_why(self, t: int, g1: int, i: int) -> str:
        """The leave half of the hard guard (Show.guard): the stream's place, the feed's edge, a padded dead ball."""
        k = self.on
        if k in self.drifted:
            return "a stream's place drifted"
        if self.feed.edge(g1) < 10 ** 8 and self.feed.edge(g1) - i < self.P.open_hold:
            return "too close to the feed's edge"
        x1 = self.extra_of(k, g1)
        return refusal(self.A, self.pad0, t, self.flip, self.know, 0.0, g1, i, False, g1, i, self.sure, self.open_from,
                       self.tail, x1, x1)

    def _cu_abort(self, why: str, t: int) -> None:
        cu, self.cu = self.cu, None
        if cu is None:
            return
        task = cu.get("task")
        if task is not None and not task.done():
            task.cancel()
        self.cu_aborts[why] = self.cu_aborts.get(why, 0) + 1
        self.cu_chances[cu["key"]] = why
        self.retune = True
        if self.S is not None and self.S.hg == cu["game"]:
            self._drop(f"the catch-up was dropped: {why}", t)    # H was sent back: tune it again
        elif self.S is not None:
            self.S.h_ready = t
        self.emit({"ev": "catchup", "what": "dropped", "tick": t, "game": cu["game"], "why": why})

    # ---- the replay on screen, then the live join --------------------------------------------------------------------
    async def _cu_run(self, cu: dict) -> None:
        """The steps on the TV: flip to H (it resumes at the first clip's start), each clip in turn, re-cueing between
        them. In "v_join" V's live tune starts as soon as the replay is up; in "h_join" V is held at the leave (as a
        jump holds the stream it leaves; a pinned stream plays on) and keeps its game."""
        rig, h, v, g = self.rig, cu["h"], cu["v"], cu["game"]
        keeps = cu["mode"] == "h_join"
        hold = v if keeps and not (self.keep == v and self.keep_live) else None
        st, why = await rig.jump(h, True, hold)
        if st != "ok":
            cu["up"] = (st, why or "the flip did not land")
            return
        cu["up"] = ("ok", "")
        if not keeps:
            cu["v_task"] = asyncio.ensure_future(rig.tune(v, g))
        P = self.cp.pack
        for k, c in enumerate(cu["clips"]):
            if k and cu.get("cut"):
                cu["broke"] = "cut for live play"                # the rest of the clips can wait (a later catch-up)
                break
            if k:
                now = rig.clock.now()
                floor = cp.rewind_floor(now, cp.Stick("yttv", self.on_since[h]), P)
                if not cp.age_ok(c.lo, now + c.gap, P, len(cu["pkg"].plays)) or (c.source == "rewind" and c.lo < floor):
                    cu["skipped"] = cu.get("skipped", 0) + 1     # it ran late: out of reach or too old now, skip it
                    continue
                ok, _ = await rig.cu_cue(h, g, c.lo, c.source, True)
                if not ok:
                    cu["broke"] = "a clip could not be cued"
                    break
                ok, _ = await rig.resume(h)
                if not ok:
                    cu["broke"] = "a clip did not start"
                    break
            cu["k"], air = k, rig.clock.now()
            await rig.clock.sleep(c.secs)
            cu["shown"].append(replace(c, air=air))
        cu["done"] = True

    async def close(self) -> None:
        cu = self.cu
        for k in ("task", "v_task", "p_task", "join"):
            f = cu.get(k) if cu is not None else None
            if f is not None and not f.done():
                f.cancel()
        await super().close()

    async def _cu_screen(self, t: int) -> None:
        """One second with the replay up: the facts as Show.tick takes them, then the catch-up's own steps."""
        self.last_t = t
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
        self._repad()
        self._trust()
        if getattr(self, "seen_state", None) is not None:      # [SEEN] the replay is up: every other game goes on unseen
            self.seen_state.tick(t, self.cu["game"], None, self.A)
        await self._cu_drive(t)
        if self.board is not None:
            self.board.write(self, t)

    async def _cu_drive(self, t: int) -> None:
        cu, rig, C = self.cu, self.rig, self.cp
        g, h, v = cu["game"], cu["h"], cu["v"]
        if cu["up"] is None:
            return                                               # the flip to the replay is under way
        if cu["up"][0] != "ok":
            self._cu_flip_failed(t)
            return
        cu["pkg_s"] += 1
        if cu["join"] is None:
            c = self._cu_alarm(t)                                # ABORT-TO-LIVE: a big moment on or coming somewhere
            if c is not None:
                cu["cut"] = True                                 # the package ends with the clip on screen
                if c != cu["target"] and cu.get("redirects", 0) < 2:
                    self._cu_redirect(t, c)                      # ...and the live join goes to that game
        g = cu["target"]                                         # the game the live join is for (the catch-up's, or redirected)
        if cu["done"] and cu["wait_from"] is None:
            cu["wait_from"] = t
        # the live stick: tuned to the game, then parked on a padded dead ball near the clips' end
        vt = cu["v_task"]
        if vt is not None and cu["v_tuned"] is None and vt.done():
            ok, took = _result(vt)
            if ok:
                cu["v_tuned"] = t
                self.on_since[v] = float(t)
                self.tunes_sent += 1
                if self.judge is not None:
                    self.judge.tuned(v, g, bool(rig.read[v]))
            elif cu["tries"] < 2:
                cu["tries"] += 1
                cu["v_task"] = asyncio.ensure_future(rig.tune(v, g))
            else:
                cu["v_task"] = None
        if cu["v_tuned"] is not None and cu["j"] is None:
            self._cu_park(t)
        pt = cu["p_task"]
        if pt is not None and pt.done() and cu["p_kind"] == "rejoin":
            cu["p_task"] = None
            ok, val = _result(pt)
            if ok and val is not None:
                if self.judge is not None:
                    self.judge.tuned(h, g, True)                 # its place after the cue was read
                self._cu_landed(t, h, int(val))
            else:
                self._cu_hand_back(t, "the replay stick could not be sent back to live")
            return
        if pt is not None and pt.done():
            cu["p_task"] = None
            ok, val = _result(pt)
            if cu["p_kind"] in ("pause", "cue") and ok and val is not None:
                at = val if cu["p_kind"] == "pause" else float(cu["cue_to"])     # the paused frame, as read back
                j = int(math.floor(at + rig.unseen_s))
                if cu["p_kind"] == "cue" and self.judge is not None:
                    self.judge.tuned(v, g, True)                 # a cue is placed by a read, like a tune
                if self._cu_land_why(t, g, j, v):                # what the feed says now: no longer a safe landing
                    self._cu_resume(v)
                else:
                    cu["j"] = j
            elif cu["p_kind"] == "cue" and not ok:
                cu["no_cue"] = t                                 # (rig.cu_cue failed: V is lost; it plays on, parked forward)
            cu["spot"] = None if cu["j"] is None else cu["spot"]
        jt = cu["join"]
        if jt is not None:
            if jt.done():
                st, why = _result(jt, ("stop", "the jump step broke"))
                cu["join"] = None
                if st == "ok":
                    self._cu_landed(t, v, cu["j"])
                    return
                if st == "stop":
                    self.stop = why
                    self._cu_hand_back(t, f"the join stopped: {why}")
                    return
                self.on, cu["phase"] = h, "clips"                # the replay is still up; V may be playing: park it again
                cu["j"] = cu["spot"] = None
                self.no_jump_until = t + self.P.cool
            return
        if cu["done"] and cu["j"] is not None and cu["p_task"] is None and t >= self.no_jump_until:
            x, hf = self.A[cu["game"]], int(t - rig.lag[h])
            if 0 <= hf < x.n and not x.dead[hf] and t - cu["wait_from"] < C.replay_dead_s:
                return                                           # the replay runs on to its own dead ball: a clean cut
            why = self.guard(t, g, cu["j"], True, g, cu["j"], h, v)
            cu["last_why"] = why
            if not why:
                cu["phase"] = "join"
                self.on = v
                cu["join"] = asyncio.ensure_future(rig.jump(v, True, None))
                return
            if "behind live" in why:                             # held too long: play on to a later dead ball
                cu["j"] = cu["spot"] = None
                self._cu_resume(v)
        if cu["wait_from"] is None or cu["p_task"] is not None:
            return
        waited = t - cu["wait_from"]
        if cu["mode"] == "v_join" and waited <= C.join_wait_s:
            return
        if not cu.get("rejoin") and g == cu["game"] and g not in self.no_back and self.cu_screen:
            f = self._cu_fresh_spot(t, g, h)
            if f is not None:                                    # H itself is sent to a fresh dead ball: the live join
                cu["rejoin"] = f
                if cu["v_task"] is not None and not cu["v_task"].done():
                    cu["v_task"].cancel()
                cu["p_task"], cu["p_kind"] = asyncio.ensure_future(self._cu_rejoin(float(f))), "rejoin"
                return
        if waited > 2 * C.join_wait_s and cu["mode"] == "v_join" and cu.get("alt_from") != g:
            alt = self._cu_alt(t, g)                             # no dead ball in time here: abandon this join, go live elsewhere
            if alt is not None:
                cu["alt_from"] = g
                self._cu_redirect(t, alt)
                cu["wait_from"] = t
                return
        if waited > 6 * C.join_wait_s:
            self._cu_hand_back(t, "no safe live join in time")

    def _cu_redirect(self, t: int, c: int) -> None:
        """The live join goes to game c instead (a big moment there, or no dead ball in time where it was going).
        "v_join": V drops what it was doing and is tuned to c, then parked as before. "h_join": V holds the game it
        left; only that one can be joined through it (anything else waits for the planner after the join)."""
        cu, rig = self.cu, self.rig
        v = cu["v"]
        if cu["mode"] == "h_join":
            if c != cu["g1"]:
                return
            cu["target"], cu["v_tuned"], cu["spot"], cu["j"] = c, cu["v_tuned"] or t, None, None
            if rig.paused[v]:
                j = int(math.floor(rig.frozen[v] + rig.unseen_s))
                cu["j"] = None if self._cu_land_why(t, c, j, v) else j
        else:
            for k in ("v_task", "p_task"):
                if cu.get(k) is not None and not cu[k].done():
                    cu[k].cancel()
            cu.update(target=c, j=None, spot=None, v_tuned=None, tries=0, no_cue=None, p_task=None, rejoin=None)
            if rig.game[v] == c and not rig.paused[v]:
                cu["v_tuned"] = t
            else:
                cu["v_task"] = asyncio.ensure_future(rig.tune(v, c))
        cu["redirects"] = cu.get("redirects", 0) + 1
        self.emit({"ev": "catchup", "what": "redirect", "tick": t, "game": cu["game"], "to": c})

    def _cu_alt(self, t: int, g_out: int) -> int | None:
        """Another live game to join instead: the one whose feed shows the most padded dead balls a fresh stream could
        land on in the seconds it has confirmed."""
        best, most = None, 0
        for g in range(self.N):
            x = self.A[g]
            if g == g_out or not x.a0 <= t <= x.a1:
                continue
            n = sum(1 for f in range(int(t), int(t + self.know)) if not self._cu_land_why(t, g, f, self.cu["v"]))
            if n > most:
                best, most = g, n
        return best

    def _cu_fresh_spot(self, t: int, g: int, k: int) -> int | None:
        """The newest padded dead ball of game g the feed has confirmed, recent enough that stream k, sent there on
        screen now (a re-cue, then play), lands well inside the lateness cap. None: keep looking (the replay plays on)."""
        P = self.cp.pack
        lo = int(math.ceil(t + P.seek_gap_s + 4 + 10 - self.s.late_cap))
        return next((f for f in range(int(t + self.know), lo, -1) if not self._cu_land_why(t, g, f, k)), None)

    async def _cu_rejoin(self, f: float) -> tuple:
        cu, rig = self.cu, self.rig
        ok, _ = await rig.cu_cue(cu["h"], cu["game"], f, "rewind", True)
        if not ok:
            return False, None
        ok, _ = await rig.resume(cu["h"])
        return ok, f

    def _cu_resume(self, v: int) -> None:
        self.cu["p_task"], self.cu["p_kind"] = asyncio.ensure_future(self.rig.resume(v)), "resume"

    def _cu_land_why(self, t: int, g: int, j: int, k: int) -> str:
        """The landing half of the hard guard as known now (no lateness: the join is checked again when it goes)."""
        if self.feed.edge(g) < 10 ** 8 and self.feed.edge(g) - j < self.P.open_hold:
            return "too close to the feed's edge"
        x2 = self.extra_of(k, g)
        return refusal(self.A, self.pad0, t, self.flip, self.know, 0.0, g, j, True, g, j, self.sure, self.open_from,
                       self.tail, x2, x2)

    def _cu_park(self, t: int) -> None:
        """Park V on the first padded dead ball of the game its stream reaches (the feed confirms a dead ball only a
        little ahead of a stream at the show's edge, so a later one may never come in time), sending the pause key when
        its frame comes round, as Show._drive does. Held too long, the join's guard sends it on to a later one."""
        cu, rig, P = self.cu, self.rig, self.cp.pack
        g, v = cu["target"], cu["v"]
        if cu["p_task"] is not None or rig.paused[v]:
            return
        x = self.A[g]
        cur = t - rig.lag[v]
        if cu["spot"] is None or cu["spot"] < cur + 2 or self._cu_land_why(t, g, cu["spot"], v):
            lo = int(math.ceil(cur + 2))
            cu["spot"] = next((f for f in range(lo, min(x.n, lo + 240)) if not self._cu_land_why(t, g, f, v)), None)
        f = cu["spot"]
        end = cu["end"]
        if ((f is None or f + rig.lag[v] > end + 10) and g not in self.no_back and not cu.get("no_cue")
                and t + P.seek_base_s + 20 * P.seek_per_s >= end - 10):
            # no dead ball ahead in time for the clips' end: send V BACK to the latest one gone by (the same cue as a
            # clip's, read), close enough to live that the join is inside the lateness cap
            floor = max(cp.rewind_floor(t, cp.Stick("yttv", self.on_since[v]), P), end + 15 + self.flip - self.s.late_cap)
            back = next((p for p in range(int(cur) - 1, int(math.ceil(floor)), -1)
                         if not self._cu_land_why(t, g, int(math.floor(p + rig.unseen_s)), v)), None)
            if back is not None:
                cu["p_task"], cu["p_kind"], cu["cue_to"] = asyncio.ensure_future(rig.cu_cue(v, g, float(back), "rewind", False)), "cue", back
                cu["spot"] = None
                return
        if f is None:
            return
        at = f - rig.unseen_s - rig.pause_lead[v] + rig.lag[v]
        if t - 0.5 <= at <= t + 1.0:
            cu["p_task"], cu["p_kind"] = asyncio.ensure_future(rig.pause(v, at)), "pause"
        elif at < t - 0.5:
            cu["spot"] = None

    def _cu_landed(self, t: int, k: int, j: int) -> None:
        """The live join landed on stream k at second j: the planner takes over with game X on screen. In "h_join" the
        other stick still holds the game left (held at the leave, or the pinned stream playing on): it is the hidden
        stick on the way back, as after a jump. Otherwise nothing is parked and it is tuned again."""
        cu, S, rig = self.cu, self.S, self.rig
        g, h, pkg = cu["target"], cu["h"], cu["pkg"]
        cu["j"] = j
        shown = [p for c in cu["shown"] for p in c.plays]
        self.on = k
        other, g1 = 1 - k, cu["g1"]
        S.sg, S.sb, S.s_land, S.s_from, S.s_kind, S.black, S.s_live = g, S.se + rig.lag[k], t, j, "park", t, 0
        self.since, self.look_live, self.parked_at, self.paused_since = t, 0, None, None
        fut = rig.confirming(other) if hasattr(rig, "confirming") else None
        if fut is not None and not fut.done():
            fut.cancel()
        if cu["mode"] == "h_join" and rig.game[other] == g1:
            S.hg, S.h_kind, self.retune = g1, "pp", False
            if self.keep == other and self.keep_live:
                S.h_paused, S.hb, S.h_ready = False, S.he + rig.lag[other], t
            elif rig.paused[other]:
                self.paused_since = cu["leave"]                  # held since the leave (the park age cap counts from there)
                self._paused_at(int(math.floor(rig.frozen[other] + rig.unseen_s)), t)
            else:
                S.hg, S.h_paused, S.h_ready, self.retune = -1, False, t, True
        else:
            S.hg, S.h_paused, S.h_ready = -1, False, t
            self.retune = True
        self.pl.next = -1
        self.jumps_done += 1
        self.jump_at.append(cu["leave"])
        wr = (True, float(pkg.worth), 0.0, 0, "catch-up: the plays missed, then live")
        self.warrants.append(wr)
        behind = round(t - cu["j"] + self.delay, 1)
        rec = {"n": self.jumps_done, "tick": cu["leave"], "from": self.names[cu["g1"]], "to": self.names[g], "reason": "catch-up",
               "warrant": {"ok": True, "there": round(pkg.worth, 2), "here": None, "live_sooner_s": 0, "why": wr[4]},
               "leave_at": (cu["g1"], cu["i"]), "join_at": (g, cu["j"]), "behind_s": behind,
               "catchup": {"plays": len(shown), "screen_s": cu["pkg_s"], "sources": pkg.sources, "line": pkg.line}}
        self.records.append(rec)
        if self.judge is not None and hasattr(self.judge, "catchup_cut") and "true_i" in cu:
            self.judge.catchup_cut(t, cu["g1"], cu["true_i"], k, g, cu["j"], True)
        self.cu_log.append({"game": cu["game"], "joined": g, "cut": bool(cu.get("cut")), "from": cu["g1"], "leave": cu["leave"], "join": t, "screen_s": cu["pkg_s"],
                            "prep_s": round(cu.get("prep_took", 0.0), 1), "plays": shown, "clips": list(cu["shown"]),
                            "sources": pkg.sources, "behind_s": behind, "join_wait_s": t - (cu["wait_from"] or t),
                            "broke": cu.get("broke", ""), "line": pkg.line, "h": h, "rejoined": k == h, "i": cu["i"], "n_planned": len(pkg.plays)})
        if not cu.get("cut"):
            self.cu_last[cu["game"]] = t                         # a cut package's other plays may come in a later one
        self.cu = None
        self.say(f"CATCH-UP done: {len(shown)} plays of {self.names[g]} in {cu['pkg_s']} s, then live at a dead ball ({behind} s behind)")
        self.emit({"ev": "catchup", "what": "live", "tick": t, "game": g, "plays": len(shown), "screen_s": cu["pkg_s"], "behind_s": behind})

    def _cu_flip_failed(self, t: int) -> None:
        """The flip to the replay did not land: the screen's game is still up on V."""
        cu = self.cu
        st, why = cu["up"]
        self.on = cu["v"]
        if st == "stop":
            self.stop = why
        self.no_jump_until = t + self.P.cool
        self._cu_abort(f"the flip to the replay did not land ({why})", t)

    def _cu_hand_back(self, t: int, why: str) -> None:
        """No live join: the replay (game X, behind live) stays up and the planner takes over from it; its next jump
        leaves a replay, not a live game."""
        cu, S, rig = self.cu, self.S, self.rig
        for k in ("p_task", "join", "v_task"):
            if cu.get(k) is not None and not cu[k].done():
                cu[k].cancel()
        h = cu["h"]
        self.on = h
        S.sg, S.sb, S.s_land, S.s_kind, S.black, S.s_live = cu["game"], S.se + rig.lag[h], t, "park", t, 0
        S.hg, S.h_paused, S.h_ready = -1, False, t
        self.retune, self.pl.next = True, -1
        self.since, self.look_live = t, 0
        self.cu_aborts[why] = self.cu_aborts.get(why, 0) + 1
        self.cu_last[cu["game"]] = t
        self.cu = None
        self.say(f"CATCH-UP: {why}; the planner takes over from the replay")
        self.emit({"ev": "catchup", "what": "handed back", "tick": t, "game": cu["game"], "why": why, "tuned": cu.get("v_tuned"),
                   "spot": cu.get("spot"), "j": cu.get("j"), "v_lag": round(float(rig.lag[cu["v"]]), 1), "v_paused": bool(rig.paused[cu["v"]]),
                   "last": cu.get("last_why", "")})
