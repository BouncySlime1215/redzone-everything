"""PARK replay: the app's own PARK controller on a recorded dev night, with fake sticks and a fake TV (no device).

    python scripts/park_replay.py --night 20261023 --games 4
    python scripts/park_replay.py --night 20261023 --games 2 --hands runner     # the local show runner's stick moves
    python scripts/park_replay.py --night 20261023 --games 4 --fail flip=2 --fail read=3 --lines 30

What runs: `redzone.noggin.park_show.Show` (the controller the app runs with park.enabled), fed the recorded games row
by row as a live feed would have had them (`FactsFeed.offer_rows`), driving `FakeRig`: two fake streams and a fake TV
whose steps take the times measured on the real sticks on Nov 10 (a tune with its read 13-19 s, of which the first
2 s report position 0; a flip 2.4-3.3 s; the read gesture leaves the stream 38 s behind its live edge; a stream that
was held is as far behind as it was held). `--fail step=n` makes the next n of a step fail (tune, read, pause,
resume, key, flip, readback, sound). `--hands app` (default) gives the fake the moves the app's controller has
(`Controller.park_*`: a jump only onto a PARKED stick, no tune sent further back than the read's own steps);
`--hands runner` gives it the local show runner's (it may also join a stream that plays and send a tune further back).

Rule #1 is judged where the pictures TRULY were (`Judge`): on the whole recorded night's facts, with the errors a real
night has drawn against what the controller believed (each game's K error once per night, the position readout per
tune, the stamp and key spread per cut). Dev (`fit`) nights only.

Replayed with fake sticks is NOT measured on the TV. It proves the controller's decisions and guards on recorded
feeds; step times on the real sticks, the label reads and the TV's flips are only proven by a live run.

Why: the in-app PARK controller has to show, before it is switched on, that it jumps at the pace the local show
runner did and never breaks Rule #1 (integration plan units 4 and 8).
"""
from __future__ import annotations

import argparse
import asyncio
import bisect
import copy
import heapq
import math
import random
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT.as_posix(), (ROOT / "scripts").as_posix()):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import park_ceiling as pc  # noqa: E402
from redzone import config  # noqa: E402
from redzone.models import League  # noqa: E402
from redzone.noggin import park_core  # noqa: E402
from redzone.noggin import park_show as show_mod  # noqa: E402
from redzone.noggin.park_facts import GRID, FactsParams, stamps_of  # noqa: E402
from redzone.noggin.park_plan import Plan  # noqa: E402
from redzone.noggin.park_show import SHOW, FactsFeed, LiveNight, Show, ShowParams, ShowTiming, engine_setting, peak, warrant_counts  # noqa: E402

FEED_LEAD = {"tonight": 13.0, "nok": 25.0}      # replay: FEED LEAD (not a K; park_timing names the three) = seconds the
#                                                 feed runs ahead of a stream's live edge (a GAME K near 0: 21 s of
#                                                 delay less the feed's 8 s; no K: the 33 s prior less 8 s)
READ_BACK = (0.4, 1.6, 0.6)                     # BACK after a pause key; the read-back after BACK; the TV's read-back after a flip
RESUME_REPORT = 0.69                            # a resume key to its "playing" report
# [park.fast_steps] `--steps`: what each device step costs, beyond the model's own numbers above. "model" is the replay as it
# was. "off" is the app's steps with park.fast_steps OFF as tonight's real-TV hands test measured them (Nov 11, n=20:
# tune 16.0 s, park 4.2 s with 0.3 s of it waiting for its frame, jump 6.9 s: resume lead 0.3, flip 2.4, 1.6 s to the
# picture read back, then 2.85 s holding the stick left INSIDE the jump). "on" is the same steps with the flag on, each
# moved by what scripts/park_step_bench.py measured between off and on (its flag-off steps match the TV's within 0.2 s;
# 40 moves back to back): the picture read back 0.35 s after the flip instead of 1.65 s, the stick left's BACK and
# read-back after the jump (the show waits on them through `confirming`, as the model already did), the park 0.35 s
# shorter from its key (3.72 -> 3.37 s), the tune 0.15 s shorter (15.92 -> 15.79 s).
STEP_COSTS = {
    "model": {},
    "off": {"tune_add": 1.7, "back": 0.65, "confirm": 2.62, "readback": 1.65, "hold_in_jump": True,
            "hold_back": 0.45, "hold_confirm": 2.4},
    "on": {"tune_add": 1.55, "back": 0.5, "confirm": 2.42, "readback": 0.35, "hold_in_jump": False,
           "hold_back": 0.25, "hold_confirm": 2.25},
}
SESSION_ZERO = 2.0                              # after a tune the media session says position 0.0 this long (finding 2)


# ---------------------------------------------------------------------------------------------- the judge
class Judge:
    """Rule #1, the pace, the plays per look and the big plays seen, judged where the pictures TRULY were, on the whole
    recorded night's facts. A stream's true second is its believed one less the errors a real night has: this game's K
    error (once per game per night), the position readout (per tune), the stamp and key spread (per cut)."""

    def __init__(self, A: list, timing: ShowTiming, seed: int, first_game: int, first_stream: int = 0, first_read: bool = True):
        self.A, self.tm, self.rng = A, timing, random.Random(1000 + seed)
        self.gd = [self.rng.gauss(0.0, timing.errs(g)[1]) for g in range(len(A))]
        self.e = {first_stream: self._draw(first_game, first_read)}
        self.mid_entry = self.mid_leave = self.hl_leave = self.walk_entry = self.walk_leave = self.live = self.plays = self.one_play = 0
        self.flips, self.jumps, self.last = [], [], None
        self.was_dead, self.look_plays, self.from_true = True, 0, None
        self.big = self.v2 = 0.0
        self.shown: set = set()
        self.pic = self.live_s = self.dead_s = self.brk_s = self.avoid_s = self.noplay = self.brk_land = 0
        self.land_wait: list = []                                # per jump: seconds from the landing to the play's start
        self.cuts: list = []                                     # per jump: (game left, its true second, game joined, its true second)
        self.e_log: list = []
        self.on: dict = {}                                       # [PARK-NFL] game -> the true seconds that were on screen
        self.tl: dict = {}                                       # [football slider] grid second -> the game on screen

    def _draw(self, g: int, read_ok: bool) -> float:
        return self.rng.gauss(0.0, self.tm.errs(g, read_ok)[0]) + self.gd[g]

    def tuned(self, k: int, g: int, read_ok: bool = True) -> None:
        self.e[k] = self._draw(g, read_ok)

    def _at(self, x, p: float) -> int:
        return min(x.n - 1, max(0, int(p)))

    def second(self, k: int, g: int, pos: float, t: int | None = None) -> None:
        x = self.A[g]
        ti = self._at(x, pos - self.e.get(k, 0.0))
        self.on.setdefault(g, set()).add(ti)
        if t is not None:
            self.tl[int(t)] = g                                  # [football slider] which game was on screen at each grid second
        dead = bool(x.dead[ti])
        self.pic += 1
        if not dead:
            self.live_s += 1
        elif x.brk[ti]:
            self.brk_s += 1
        else:
            self.dead_s += 1
        if dead and t is not None and x.left[ti] >= 10:          # 10 s or more of dead air still to come on screen ...
            for o, y in enumerate(self.A):                       # ... while another game is in live play at the show's edge
                if o != g and y.a0 <= t <= y.a1 and not y.dead[self._at(y, t)]:
                    self.avoid_s += 1
                    break
        if self.from_true is None:
            self.from_true = ti
        if not dead:
            self.live += 1
            if self.was_dead:
                self.look_plays += 1                             # a play that began while this picture was up
        self.was_dead = dead
        if (g, ti) not in self.shown and self.from_true <= ti - park_core.PARK_PARAMS.seen_lead_s and (ti in x.mom or ti in x.v2):
            self.shown.add((g, ti))
            self.big += x.mom.get(ti, 0.0)
            self.v2 += max(0.0, x.v2.get(ti, 0.0))

    def flip(self, t: int, k1: int, g1: int, i: int, k2: int, g2: int, j: int, paused: bool) -> None:
        xs, xh, bad = self.A[g1], self.A[g2], 0
        e1, e2 = self.tm.errs(g1), self.tm.errs(g2)
        true_i = i - self.e.get(k1, 0.0) + self.rng.gauss(0.0, math.hypot(e1[2], e1[3]))
        true_j = j - self.e.get(k2, 0.0) + self.rng.gauss(0.0, e2[2] if paused else math.hypot(e2[2], e2[3]))
        ti, ej = self._at(xs, true_i), self._at(xh, true_j)
        self.cuts.append((g1, true_i, g2, true_j))
        self.e_log.append((self.e.get(k1, 0.0), self.e.get(k2, 0.0)))    # each stream's standing error at this cut (leave, join)
        if not xs.dead[ti]:
            if xs.legal[ti]:
                self.walk_leave += 1
            else:
                self.mid_leave, bad = self.mid_leave + 1, bad + 1
        if xs.hl[ti]:
            self.hl_leave, bad = self.hl_leave + 1, bad + 1
        if not xh.dead[ej]:
            if xh.legal[ej]:
                self.walk_entry += 1
            else:
                self.mid_entry, bad = self.mid_entry + 1, bad + 1
        if self.last is not None and self.live >= 3:
            self.jumps.append(self.last)                         # the flip that began the visit now ending: it showed play
            self.plays += self.look_plays
            self.one_play += int(self.look_plays <= 1)
        elif self.last is not None:
            self.noplay += 1                                     # a look that showed no play
        wait = xh.left[ej] if xh.dead[ej] else 0
        self.land_wait.append(wait)
        self.brk_land += int(bool(xh.brk[ej]) and wait > 10)     # landed on a break with more than 10 s of it still to come
        self.flips.append((t, g2, bad))
        self.last, self.live, self.look_plays, self.was_dead, self.from_true = t, 0, 0, True, None

    def stats(self, st):
        out = copy.copy(st)
        out.jumps, out.flips = list(self.jumps), list(self.flips)
        for k in ("mid_entry", "mid_leave", "hl_leave", "walk_entry", "walk_leave"):
            setattr(out, k, getattr(self, k))
        out.seen, out.v2_seen = {"live": self.big, "parked": 0.0, "clip": 0.0}, self.v2
        return out


# ---------------------------------------------------------------------------------------------- fake devices
class FakeClock:
    """Time that only moves when everything is waiting: a replay or a test never sleeps for real."""

    def __init__(self, t0: float = 0.0):
        self.t, self._q, self._n = float(t0), [], 0

    def now(self) -> float:
        return self.t

    async def sleep(self, s: float) -> None:
        if s <= 0:
            await asyncio.sleep(0)
            return
        fut = asyncio.get_running_loop().create_future()
        self._n += 1
        heapq.heappush(self._q, (self.t + s, self._n, fut))
        await fut

    async def run(self, main):
        task = asyncio.ensure_future(main)
        while True:
            for _ in range(12):
                await asyncio.sleep(0)                           # let everything run until it waits
            if task.done():
                return task.result()
            if not self._q:
                raise RuntimeError("everything is waiting on nothing")
            when, _, fut = heapq.heappop(self._q)
            self.t = max(self.t, when)
            if not fut.done():
                fut.set_result(None)


class FakeRig:
    """Two fake streams and a fake TV behind the device interface the show uses (see `park_show.Show`):
        game[k], paused[k], lag[k] (seconds behind the show's edge), frozen[k] (the second it is paused on), read[k]
        tune(k, game) -> (ok, seconds)      pause(k, at) -> (ok, the frozen second)     resume(k) -> (ok, lag)
        jump(k, resume, hold) -> ("ok" | "kept" | "stop", why)      confirming(k) -> the read-back still running, or None
        finish()
    instant=True: every step works at once and the simulator's timings rule. instant=False: steps take the times measured
    on Nov 10 on `clock`: `steps` LEFT presses of the per-tune read after every tune (0 = none), `delay` = the show's
    delay. `fail[step] = n` makes the next n of that step fail (tune, read, pause, resume, key, flip, readback, sound).
    hands="app": the moves of the app's controller (a jump only onto a parked stream; no tune sent further back)."""
    names, ports, jump_s = ("s2023", "plus"), ("HDMI1", "HDMI2"), 2.3

    def __init__(self, clock=None, *, instant: bool = True, seed: int = 0, tune_s: dict | None = None, delay: int = 0, steps: int = 0,
                 reads=None, tune_range: tuple | None = None, latency: float = 0.0, bad_place: float = 0.0, read0: bool = True,
                 hands: str = "runner", params: ShowParams = SHOW, tune_sd: float = 1.5, drift: float = 0.0, drift_n: int = 10 ** 9,
                 costs: dict | None = None):
        P = self.P = params
        self.cost = {"tune_add": 0.0, "back": READ_BACK[0], "confirm": READ_BACK[1], "readback": READ_BACK[2],
                     "hold_in_jump": False, "hold_back": None, "hold_confirm": None, **(costs or {})}   # [park.fast_steps] --steps
        self.clock, self.instant, self.rng, self.tune_sd = clock, instant, random.Random(seed), tune_sd
        self.unseen_s, self.pause_lead, self.hold_off = P.unseen, (0.70, 0.60), P.hold_off
        self.join_playing, self.deep = hands != "app", hands != "app"
        self.tune_range, self.latency, self.note = tune_range, latency, {}      # (whole tune seconds lo..hi; a channel's lag over the nominal)
        if not instant:                                          # the real TV's flips on Nov 10 night: 2.4 to 3.3 s
            self.flip_s, self.unseen_s, self.hold_off = P.flip_times()   # the one derivation (park_show.ShowParams)
        self.bad_place = bad_place                               # seconds added to what a tune reports as the stream's place (a bad reading)
        self.game, self.paused, self.lag, self.frozen, self.read = [None, None], [False, False], [0.0, 0.0], [0.0, 0.0], [read0, read0]
        self.input = self.first = 0
        self.silent, self.sent, self.fail, self.tune_s = True, [], {}, tune_s or {}
        self.delay, self.steps, self.reads = delay, steps, reads or (lambda g: False)
        self.back = P.read_back(steps)                           # what the read gesture puts a stream behind its live edge
        self.tune_lag = self.back - delay
        self._conf: dict = {}
        self.sound_at, self.incident = None, ""
        self.true_lag = [0.0, 0.0]
        self.drift = [0.0, 0.0]                                  # seconds a minute stream k creeps after a resume (a pause that did not hold)
        self.drift_rate, self.drift_left = float(drift), int(drift_n)   # ...made to order: the next drift_n resumes creep at this rate
        self.played_at = [None, None]                            # when stream k last started playing (its session's update time)
        self.depth: dict = {}                                    # [PARK-NFL-LAG] game -> s deeper (set by the show)
        self.prime_games: set = set()                            # [PARK-NFL-LAG] games on Prime: depth by a longer pause hold
        self.extra: dict = {}                                    # [PARK-NFL-LAG] stream -> s its last tune was put deeper

    def _fails(self, what: str) -> bool:
        n = self.fail.get(what, 0)
        self.fail[what] = max(0, n - 1)
        return n > 0

    def _now(self) -> float:
        return self.clock.now() if self.clock else 0.0

    async def _wait(self, s: float) -> None:
        if not self.instant:
            await self.clock.sleep(s)

    async def tune(self, k: int, game: int, steps: int | None = None) -> tuple:
        P = self.P
        self.sent.append(("tune", k, game))
        deep = steps is not None and steps > self.steps and self.deep     # sent further back than usual: a clip
        steps = steps if deep else self.steps
        took = (self.tune_s.get(game) or min(4.9, 2.2 + self.rng.expovariate(1 / 0.6))) \
            + ((P.read + min(6.0, self.rng.expovariate(1 / 1.5))) if steps else 0.0) + 0.6 * max(0, steps - max(1, self.steps))
        if self.tune_range:
            took = self.rng.uniform(*self.tune_range)
        if self.cost["tune_add"]:
            took += self.cost["tune_add"]
        self.game[k], self.note[k] = None, {"way": "code", "why": ""}
        await self._wait(max(took, SESSION_ZERO))                # (the session's position-0 seconds are inside every tune)
        if self._fails("tune"):
            self.note[k]["why"] = "made to fail"
            return False, took
        self.read[k] = bool(self.reads(game)) and not self._fails("read")
        self.note[k]["way"] = "code, read" if self.read[k] else "code, no read"
        self.game[k], self.paused[k] = game, False
        back = (P.read_back(steps) - self.delay) if steps else 0.0
        self.lag[k] = back + self.latency + (self.rng.gauss(0.0, self.tune_sd) if self.read[k] else 0.0) if (deep or not self.instant) else 0.0
        extra = float(self.depth.get(game, 0.0) or 0.0)
        self.extra[k] = 0.0
        if extra > 0:                                            # [PARK-NFL-LAG] parked deeper: Prime holds the pause longer,
            if game in self.prime_games:                         # YouTube TV steps back with more LEFT presses (15 s each)
                took += extra
                await self._wait(extra)
                self.lag[k] += extra
                self.extra[k] = extra
            else:
                add = int(math.ceil(extra / P.step))
                took += 0.6 * add
                await self._wait(0.6 * add)
                self.lag[k] += P.step * add
                self.extra[k] = P.step * add
                self.sent += [("key", k, "KEYCODE_DPAD_LEFT")] * add
        self.true_lag[k] = self.lag[k]
        self.lag[k] += self.bad_place
        if steps:
            self.sent += [("key", k, "KEYCODE_DPAD_CENTER")] + [("key", k, "KEYCODE_DPAD_LEFT")] * steps + [("key", k, "KEYCODE_DPAD_CENTER")]
        return True, took

    async def _freeze(self, k: int) -> None:
        self.sent.append(("key", k, "KEYCODE_MEDIA_PLAY_PAUSE"))
        await self._wait(self.pause_lead[k] + self.rng.gauss(0.0, 0.07))
        self.paused[k], self.frozen[k] = True, self._now() - self.lag[k]

    async def _confirm(self, k: int, hold: bool = False) -> tuple:
        c = self.cost
        await self._wait(c["hold_back"] if hold and c["hold_back"] is not None else c["back"])
        self.sent.append(("key", k, "KEYCODE_BACK"))
        await self._wait(c["hold_confirm"] if hold and c["hold_confirm"] is not None else c["confirm"])
        return True, self.frozen[k]

    async def pause(self, k: int, at: float | None) -> tuple:
        if at is not None and not self.instant:
            await self.clock.sleep(at - self.clock.now())
        if self._fails("pause"):
            self.sent.append(("key", k, "KEYCODE_MEDIA_PLAY_PAUSE"))
            return False, None
        await self._freeze(k)
        return await self._confirm(k)

    def confirming(self, k: int):
        return self._conf.pop(k, None)

    def replace(self, k: int, lag: float) -> None:
        """Forget what was read of stream k's place: it is `lag` behind the show's edge now, and its tune counts as not read."""
        self.lag[k], self.read[k] = float(lag), False

    def _play(self, k: int, key_at: float) -> None:
        self.paused[k], self.lag[k] = False, key_at + RESUME_REPORT - (self.frozen[k] + 0.18)
        self.played_at[k] = key_at + RESUME_REPORT
        self.drift[k] = self.drift_rate if (self.drift_rate and self.drift_left > 0) else 0.0
        self.drift_left -= int(bool(self.drift[k]))

    async def look(self, k: int):
        """One media-session read of stream k: its lag now (with any creep since its resume) and the report behind it."""
        await self._wait(0.1)
        if self._fails("look") or self.game[k] is None:
            return None
        since = self._now() - (self.played_at[k] if self.played_at[k] is not None else self._now())
        lag = self.lag[k] + self.drift[k] * since / 60.0
        return {"lag": lag, "pos_s": round(self._now() - lag, 2), "updated": self.played_at[k], "state": "playing"}

    async def reread(self, k: int) -> tuple:
        """The read gesture on stream k again: it ends `steps` LEFT presses further back, read (or not)."""
        P = self.P
        took = P.read + min(6.0, self.rng.expovariate(1 / 1.5))
        since = self._now() - (self.played_at[k] if self.played_at[k] is not None else self._now())
        await self._wait(took)
        self.sent += [("key", k, "KEYCODE_DPAD_CENTER")] + [("key", k, "KEYCODE_DPAD_LEFT")] * self.steps + [("key", k, "KEYCODE_DPAD_CENTER")]
        self.read[k] = bool(self.reads(self.game[k])) and not self._fails("read")
        self.lag[k] += self.drift[k] * since / 60.0 + P.step * self.steps + P.read_sat
        self.drift[k], self.played_at[k] = 0.0, self._now()
        return self.read[k], took

    async def resume(self, k: int) -> tuple:
        self.sent.append(("key", k, "KEYCODE_MEDIA_PLAY_PAUSE"))
        if self._fails("resume"):
            return False, None
        at = self._now()
        await self._wait(RESUME_REPORT)
        self._play(k, at)
        return True, self.lag[k]

    async def jump(self, k: int, resume: bool, hold: int | None = None) -> tuple:
        P, at = self.P, self._now()
        if not resume and not self.join_playing:
            return "kept", "the stick is not parked on this game"          # the app's jump step refuses: nothing was sent
        if resume:
            self.sent.append(("key", k, "KEYCODE_MEDIA_PLAY_PAUSE"))
            if self._fails("key"):
                return "kept", "the resume key was not sent"
            await self._wait(P.resume_lead)
        self.sent.append(("tv", "set_input", k))
        flipped, took = not self._fails("flip"), min(3.3, 2.4 + self.rng.expovariate(1 / 0.3))
        self.jump_s = took + (P.resume_lead if resume else 0.0)
        held = hold is not None and not self._fails("pause")
        if hold is not None and not self.instant:
            await self._wait(max(1.0, self.flip_s - 0.75))       # the pause key to the stream being left: it lands at the swap
            if held:
                await self._freeze(hold)
            else:
                self.sent.append(("key", hold, "KEYCODE_MEDIA_PLAY_PAUSE"))
            await self._wait(max(0.0, took - (self._now() - at - (P.resume_lead if resume else 0.0))))
        else:
            await self._wait(took)
            if held:
                self.sent.append(("key", hold, "KEYCODE_MEDIA_PLAY_PAUSE"))
                self.paused[hold] = True
        if flipped:
            self.input = k
        if resume and not self._fails("resume"):
            self._play(k, at)
        self.sent.append(("tv", "state", None))
        await self._wait(self.cost["readback"])
        self.sound_at = self._now()
        why = "the TV could not be read back" if self._fails("readback") else ""
        if not why and (not self.silent or self._fails("sound")):          # not muted at volume 0, or unreadable twice: an incident
            self.silent = False
            self.incident = why = "SOUND INCIDENT: the TV is not muted at volume 0"
        if not why and self.input != k:
            why = "the flip did not confirm"
        if why:
            if held and self.paused[hold] and self.input != k:   # the picture that is still up was frozen: start it again at once
                self.sent.append(("key", hold, "KEYCODE_MEDIA_PLAY_PAUSE"))
                self._play(hold, self._now())
            return ("kept" if why == "the flip did not confirm" else "stop"), why
        if self.paused[k]:
            return "stop", "the new picture did not start playing"
        if held and not self.instant:
            if self.cost["hold_in_jump"]:                        # (--steps off: the app's jump holds the stick left before it returns)
                await self._confirm(hold, True)
            else:
                self._conf[hold] = asyncio.ensure_future(self._confirm(hold, True))
        return "ok", ""

    async def finish(self) -> None:
        for fut in self._conf.values():
            fut.cancel()
        for k in (0, 1):
            if self.paused[k]:
                self.sent.append(("key", k, "KEYCODE_MEDIA_PLAY_PAUSE"))
                self.paused[k] = False


# ---------------------------------------------------------------------------------------------- the recorded night
def load_replay(night_id: str, n_games: int, league: str = "nba", repaired: bool = True) -> tuple[list, list]:
    """(the recorded games of the slate, their whole-night facts): the slate park_plan.py would pick on that night.
    repaired: late-edit rows are put back first (`repair`); False = the loader's own stamps."""
    from redzone.lab import splits
    from redzone.lab.backtest import Replay
    d = ROOT / "data/archive" / league
    if str(night_id) not in (splits.nights_for(d, "fit") or set()):
        raise SystemExit(f"{night_id} is not a dev night of the {league} archive. Replay runs on dev nights only.")
    rs = []
    for gid in splits.archive_nights(d).get(str(night_id), ()):
        f = d / f"summary_{gid}.json"
        try:
            r = Replay.load(League(league), f) if f.exists() else None
        except (OSError, ValueError):
            r = None
        if r is not None and len(r.plays) >= 60:
            rs.append(r)
    if repaired:
        rs = [repair(r) for r in rs]
    slate = pc.slate([pc.truth_of(r) for r in rs], n_games)
    if not slate:
        raise SystemExit(f"night {night_id} has no slate of {n_games} games that start within 2.5 hours.")
    by = {r.gid: r for r in rs}
    return [by[g["gid"]] for g in slate], slate


def repair(r, late_row_s: float | None = None):
    """The recorded game with its late-edit rows put back (`park_facts.stamps_of`, the show's own rule): a row the feed
    re-stamped long after the rows that follow it takes the stamp of the row before it, instead of the loader's
    never-backwards rule dragging every later row up to the edit's time. The frozen grader's functions are unchanged;
    they are only given these stamps (as the coordinator's regrade of the live flips of Nov 10 does)."""
    late = float(config.default("park.late_row_s", 60.0)) if late_row_s is None else late_row_s
    wall, fixed = stamps_of(r.plays, late)
    out = replace(r, wall=wall, _cache={})
    out.restamped = len(fixed)
    return out


class RowFeed:
    """Hands a recorded game to the show's feed row by row: a row stamped s is in the feed once the show's edge is
    `lead` short of s (the stream's delay less the feed's lag, plus the show's own delay); read every `poll` seconds."""

    def __init__(self, replays: list, feed: FactsFeed, base: int, lead, poll: int = 3):
        self.rs, self.feed, self.base, self.poll = replays, feed, base, max(1, poll)
        self.leads = [int(x) for x in lead] if isinstance(lead, (list, tuple)) else [int(lead)] * len(replays)
        self.k, self.first = [0] * len(replays), True

    def step(self, t: int) -> None:
        if t % self.poll and not self.first:
            return
        self.first = False
        for g, r in enumerate(self.rs):
            k = bisect.bisect_right(r.wall, self.base + t + self.leads[g])
            if k != self.k[g]:
                self.feed.offer_rows(g, r.plays[self.k[g]:k], t, final=k == len(r.plays), data=r.data)
                self.k[g] = k
            else:
                self.feed.at[g] = t


async def drive(show: Show, clock, rows: RowFeed, a: int, b: int) -> None:
    try:
        for t in range(a, b):
            if not show.rig.instant:
                await clock.sleep(t - clock.now())
            rows.step(t)
            await show.tick(t)
            if show.stop:
                show.say(f"STOPPED: {show.stop}. No more jumps.")
                break
    finally:
        await show.close()


def replay(night_id: str, games: int = 4, *, case: str = "tonight", hands: str = "app", first: str = "", minutes: float = 0,
           seed: int = 0, league: str = "nba", fail: dict | None = None, poll: int = 3, say=None, loaded: tuple | None = None,
           params: ShowParams | None = None, stop_min: dict | None = None, board=None, rig_kw: dict | None = None,
           model=None, eyes=None, emit=None, any_shot: bool = False, grid: tuple | None = None, prime: int = 0,
           prime_k: str = "bug", prime_pad: int | None = None, walk: str = "any", nfl: int = -1, nfl_lag: float | None = None,
           deep: bool = False, slider: float | None = None, pin: bool = False, seen: bool = False, director: bool = False,
           step_costs: str = "model") -> Show:
    """The app's PARK controller on a recorded dev night: fake sticks, a fake TV, a clock that is not the wall.
    first = "read" (the first picture's place was read: the app's `Controller.park_start`, the local runner's start) or
    "live" (what happens WITHOUT that step: the stream on screen sits at its live edge, never read; the feed is never
    far enough ahead of it for a padded leave, so the show almost never makes its first jump).
    seen / director: [SEEN] park.seen and [DIRECTOR] park.director_v1 for this run (Director v1's numbers and the league
    weights from the default config); both off = the replay as before."""
    P = params or ShowParams.from_config(config.default)
    first = first or "read"
    rs, slate = loaded or load_replay(night_id, games, league)
    if nfl >= 0:                                                 # [PARK-NFL] one recorded NFL night game, time-shifted onto this slate
        rs, slate = list(rs) + [shifted_nfl(nfl, slate)[0]], list(slate) + [shifted_nfl(nfl, slate)[1]]
    if walk != "any":
        slate = walk_points(rs, slate, walk)
    tm = ShowTiming.case(case, len(rs), league=league, delay=P.show_delay)
    known = case == "tonight"
    lead = int(P.show_delay + FEED_LEAD[case])
    steps = P.steps_for(known)
    primes = prime_games(len(rs), prime)
    for g, r in enumerate(rs):                                   # [PARK-NFL] a football game keeps its own league's timing
        if r.league.base == League.NFL:
            tm.g[g] = football_timing(tm.g[g])
    for g in primes:
        tm.g[g] = prime_timing(tm.g[g], prime_k, prime_pad)
    eng = engine_setting(tm.pad0(), lead, late_cap=P.late_cap - P.show_delay - P.jump_slack, tune_s=3.0 + (P.read if steps else 0.0),
                         min_live=P.min_look, land_wait=P.land_wait, flip=P.jump_real, pause_reach=P.pause_reach,
                         any_shot=any_shot)                      # (any_shot: the what-if OUTSIDE Rule #1; the app never sets it)
    full = park_core.Night(slate)
    A = full.arrs(eng.shots, eng.value, eng.live_w)
    a, b = full.span
    b = min(b, a + int(minutes * 60)) if minutes else b
    night = LiveNight(len(rs), full.base, full.n, (a, b), eng, grid=GRID if grid is None else grid)
    night.big_total, night.v2_total = full.big_total, full.v2_total
    if stop_min is None:
        stop_min = dict(config.default("park.show.stop_min") or {}) if P.stops else {}
    leads = [lead if (r.league.base != League.NFL or nfl_lag is None) else int(P.show_delay + FEED_LEAD["tonight"] + 8.0 - nfl_lag)
             for r in rs]                                        # [PARK-NFL] a football feed nfl_lag s late instead of the show's 8 s
    feed = FactsFeed([(r.league, r.gid) for r in rs], night, [0.0] * len(rs), leads, stop_min=stop_min,
                     params=FactsParams.from_config(config.default), keep_shots=any_shot)
    _, S0 = park_core.run(full, _Hindsight(), eng, t0=a, t1=a)     # the first picture, picked the way park_plan.py picks it
    clock = FakeClock(float(a))
    tune0 = {g: 2.7 + (P.read if steps else 0.0) for g in range(len(rs))}
    if primes:                                                   # [park.prime] a Prime tune: switch_cost.prime + its hold
        rk = dict(rig_kw or {})
        cost = prime_cost(P, steps)
        rk["tune_s"] = {**dict(rk.get("tune_s") or {}), **{g: cost - (P.read if steps else 0.0) for g in primes}}
        rig_kw = rk
        tune0.update({g: cost for g in primes})
    if STEP_COSTS.get(step_costs):                               # [park.fast_steps] --steps off|on: the step costs measured on the TV
        rig_kw = {**dict(rig_kw or {}), "costs": {**STEP_COSTS[step_costs], **dict((rig_kw or {}).get("costs") or {})}}
    rig = FakeRig(clock, instant=False, seed=seed, delay=P.show_delay, steps=steps, reads=tm.reads, hands=hands, params=P,
                  tune_sd=tm.p.tune_sd, **(rig_kw or {}))
    rig.fail.update(fail or {})
    rig.prime_games = set(primes)
    depth = None
    if deep and nfl_lag is not None:                             # [PARK-NFL-LAG] each late feed's game parked (lag - the show's 8 s) deeper
        depth = {g: max(0.0, nfl_lag - P.feed_lag) for g, r in enumerate(rs) if r.league.base == League.NFL}
        for g, x in depth.items():
            tune0[g] = tune0[g] + (x if g in primes else 0.6 * math.ceil(x / P.step))
    first_g = S0.sg
    if pin and any(r.league.base == League.NFL for r in rs):     # [pinned anchor] the football game starts on stream 0, kept there
        first_g = next(g for g, r in enumerate(rs) if r.league.base == League.NFL)
    rig.game[0] = first_g
    if pin and depth and first_g in depth:                       # the pinned football stream starts as deep as its feed needs
        rig.lag[0] = rig.true_lag[0] = float(depth[first_g])
    if first == "live":                                          # the app's start: on screen since before PARK, never read
        rig.read[0], rig.lag[0] = False, -float(P.show_delay)
    judge = Judge(A, tm, seed, first_g, first_read=rig.read[0])
    show = Show(night, eng, tm, rig, feed, plan=Plan(jump_bonus=P.bonus), judge=judge, board=board, say=say or (lambda line: None),
                names=[r.matchup or r.gid for r in rs], chans=[f"channel {k + 1}" for k in range(len(rs))], tune0=tune0,
                params=P, model=model, eyes=eyes, emit=emit, no_back=set(primes) or None, depth=depth,
                football_slider=slider, keep=0 if pin and any(r.league.base == League.NFL for r in rs) else None, keep_live=pin,
                seen=seen, director=_director_params() if director else None)
    show.replays, show.base = rs, full.base                      # for the frozen grader (graded) and the stamp = snap judge
    S = show.begin(first_g, 0, a)
    S.sb = float(rig.lag[0])
    rows = RowFeed(rs, feed, full.base, leads, poll)
    if board is not None:
        board.start(show, "replay", (b - a) / 60.0)
    asyncio.run(clock.run(drive(show, clock, rows, a, b)))
    return show


# ---------------------------------------------------------------------------------------------- [park.prime] Prime games
# ---------------------------------------------------------------------------------------------- [PARK-NFL] a football game
def nfl_nights() -> list[str]:
    """The dev nights that hold exactly one NFL game (a Monday, Thursday or Sunday night), oldest first."""
    from redzone.lab import splits
    d = ROOT / "data/archive/nfl"
    keep, games = splits.nights_for(d, "fit") or set(), splits.archive_nights(d)
    return [n for n in sorted(keep) if len(games.get(n, ())) == 1]


_NFL_CACHE: dict = {}


def shifted_nfl(i: int, slate: list) -> tuple:
    """The i-th one-game NFL dev night, every row's wallclock moved so it kicks off at the basketball slate's median tip
    (park_ceiling.slates_for's synthetic "mnf+K" slate: the archives are different seasons, so this is NOT a recorded
    night; different games' dead and live stretches do not depend on each other). (Replay, its truth)."""
    import json
    import statistics
    import tempfile
    from datetime import datetime, timezone

    from redzone.lab import splits
    from redzone.lab.backtest import Replay
    from redzone.sources import espn
    tip = statistics.median(g["t0"] for g in slate)
    key = (i, int(tip))
    if key in _NFL_CACHE:
        return _NFL_CACHE[key]
    d = ROOT / "data/archive/nfl"
    night = nfl_nights()[i]
    gid = splits.archive_nights(d)[night][0]
    src = d / f"summary_{gid}.json"
    r0 = Replay.load(League.NFL, src)
    dt = int(round(tip - r0.wall[0]))
    data = json.loads(src.read_text())

    def move(p):
        t = espn._ts(p.get("wallclock"))
        if t is not None:
            p["wallclock"] = datetime.fromtimestamp(t + dt, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for dr in (espn._get(data, "drives", "previous") or []):
        for p in dr.get("plays") or []:
            move(p)
    for p in (espn._get(data, "drives", "current", "plays") or []):
        move(p)
    with tempfile.TemporaryDirectory(prefix="rz-nfl-") as tmp:
        f = Path(tmp) / f"summary_{gid}.json"
        f.write_text(json.dumps(data))
        r = Replay.load(League.NFL, f)
    out = (r, pc.truth_of(r))
    _NFL_CACHE[key] = out
    return out


def football_timing(gt):
    """A football game's timing: its own league (stamp sd park.timing.stamp_sd_football, so padding about 8 s), its K
    as the replay case gives it."""
    from redzone.noggin.park_timing import GameTiming
    return GameTiming("nfl", gt.k, gt.params)


def picture_grade(show: Show, after_s: float = 6.0) -> dict:
    """[PARK-NFL-LAG] A second judge for football, the picture model: ESPN's NFL stamp is the SNAP (watcher, Nov 11:
    snap = stamp + 0.57 s, dead ball = stamp + 5.49 s), so a cut whose true second on a football game is within
    [stamp, stamp + after_s] is mid-play on the picture. The frozen grader's own counts are not changed by it."""
    import bisect as _b

    from redzone.grading import is_action
    j, rs, base = show.judge, getattr(show, "replays", None), getattr(show, "base", None)
    if j is None or rs is None:
        return {}
    acts = {g: [w for w, p in zip(r.wall, r.plays) if is_action(r.league, p)] for g, r in enumerate(rs)
            if r.league.base == League.NFL}

    def live(g, sec):
        ws = acts.get(g)
        if not ws:
            return False
        i = _b.bisect_right(ws, base + sec) - 1
        return i >= 0 and base + sec - ws[i] <= after_s
    joins = sum(1 for g1, ti, g2, ej in j.cuts if live(g2, ej))
    leaves = sum(1 for g1, ti, g2, ej in j.cuts if live(g1, ti))
    return {"football_joins_mid": joins, "football_leaves_mid": leaves,
            "football_cuts": sum(1 for g1, ti, g2, ej in j.cuts if g1 in acts or g2 in acts)}


def backfill(show: Show, g: int) -> dict:
    """[football slider] Seconds the picture showed another game while football game g was in dead time at the show's
    edge (its breaks backfilled), and while it was in play there (football play away from screen, shown later or not)."""
    j = show.judge
    x = j.A[g]
    fill = away = 0
    for t, on in j.tl.items():
        if on == g or not (x.a0 <= t <= x.a1):
            continue
        if x.dead[min(x.n - 1, max(0, t))]:
            fill += 1
        else:
            away += 1
    return {"backfill_s": fill, "away_in_play_s": away}


def snap_share(show: Show, g: int) -> dict:
    """Of football game g's plays (action rows), how many were on screen from 1 s before the snap (stamp - 7 s, the
    grader's play) to the whistle, judged where the picture truly was."""
    from redzone.grading import is_action, plen
    j, rs, base = show.judge, getattr(show, "replays", None), getattr(show, "base", None)
    if j is None or rs is None:
        return {}
    r, on = rs[g], j.on.get(g, set())
    n = seen = 0
    for w, p in zip(r.wall, r.plays):
        if not is_action(r.league, p):
            continue
        n += 1
        a, b = int(w - plen(r.league) - 1 - base), int(w - base)
        seen += all(x in on for x in range(a, b + 1))
    return {"plays": n, "seen_from_snap": seen, "share": round(seen / n, 3) if n else None}


class _Defaults:
    get = staticmethod(config.default)


def _director_params():
    """[DIRECTOR] Director v1's numbers and the league weights, as the default config has them."""
    from redzone.noggin.director import DirectorParams
    return DirectorParams.from_config(config.default)


def prime_games(n: int, k: int) -> list[int]:
    """Which games of the slate are on Prime Video: the last k (k <= n)."""
    return list(range(max(0, n - max(0, k)), n))


def prime_cost(P: ShowParams, steps: int) -> float:
    """A Prime tune ready to park: decider.switch_cost.prime plus the hold to where a YouTube TV tune and its read sit
    (park_prime.tune_cost, the app's own number)."""
    from redzone.noggin import park_prime
    lag = (P.step * steps + P.read_sat - P.show_delay) if steps else 0.0
    return park_prime.tune_cost("prime:x", _Defaults(), yttv_s=0.0, show_delay=P.show_delay, tune_lag=lag)


def prime_timing(gt, how: str, pad: int | None):
    """A Prime game's timing: "none" = no K (the wide prior); "bug" = a K from its own bug reads at the K floor (the
    same model as a YouTube TV game with a K); with `pad`, every error part scaled so the padding is `pad` s (a what-if
    for the bug check of the parked frame)."""
    from dataclasses import replace

    from redzone.noggin.park_timing import GameK, GameTiming
    if how == "none":
        return GameTiming(gt.league, None, gt.params)
    p = gt.params
    if pad is not None:
        tot = GameTiming(gt.league, GameK(0.0, p.k_sd_min), p).errors(True).total
        f = float(pad) / (p.pad_k * tot)
        p = replace(p, read_sd=p.read_sd * f, k_sd_min=p.k_sd_min * f, stamp_sd_nba=p.stamp_sd_nba * f,
                    stamp_sd_college=p.stamp_sd_college * f, key_sd=p.key_sd * f)
    return GameTiming(gt.league, GameK(0.0, p.k_sd_min, 10, "bug"), p)


def walk_points(rs: list, slate: list, walk: str) -> list:
    """The slate's walk-in points (the any-shot rule's 4 s after a shot, which the replay's mask also lets a LEAVE use):
    "made" = after MADE field goals only (the frozen grader's join window); "none" = none at all, so every leave and
    every join must be in dead time (the coordinator's Nov 11 bar, until the planner keeps leaves to dead time itself)."""
    from redzone.grading import raw_type
    out = []
    for r, g in zip(rs, slate):
        made = [w for w, p in zip(r.wall, r.plays) if p.get("shootingPlay") and p.get("scoringPlay")
                and "free throw" not in raw_type(p)] if walk == "made" else []
        out.append(dict(g, shots=made))
    return out


class _Hindsight:
    """Only picks the first picture, the way park_plan.py does (the must-see value about to air)."""
    name = "hindsight"

    def bind(self, arrs: list, s) -> None:
        self.A = arrs

    def score(self, g: int, c: int, t: int, on_screen: bool, back: int = 0) -> float:
        P = park_core.PARK_PARAMS
        return self.A[g].ahead(c - back + (0 if on_screen else P.seen_lead_s), c + P.run_up_s, c)


# ---------------------------------------------------------------------------------------------- the frozen grader
def graded(show: Show) -> dict:
    """The replay's cuts as the FROZEN grader scores them (`redzone.lab.cutgrade.grade`, read only, never edited here):
    each jump becomes one switch-log row whose leave and entry are the feed times that were truly on screen at the
    cut. The replay's own judge reads the PARK planner's own mask, so its Rule #1 line alone checks PARK against
    PARK; this is the independent count.
        entries_mid_play         the grader's Rule #1 entry count (v49: clean from a made field goal for 8 s NBA /
                                 9 s college, or until the next shot)
        entries_mid_play_strict  the same without that window (the scoring before v49)
        leaves_mid_play          leaves the grader does not find on a dead ball (leaves have no made-basket window)
        joins_after_miss         entries on a live ball that the PARK mask called legal (a shot window) and whose last
                                 shot was NOT a made field goal: what the any-shot rule lets through outside Rule #1"""
    from redzone.grading import SwitchLog, dead_at
    from redzone.lab import cutgrade
    j, rs, base = show.judge, show.replays, show.night.base
    log, after_miss, walk = [], 0, 0
    for g1, pi, g2, pj in j.cuts:
        log.append(SwitchLog(base + pj, rs[g1].gid, rs[g2].gid, "jump", "park replay", leave_dt=pi - pj))
        r, st = rs[g2], base + pj
        if r.wall[0] <= st <= r.wall[-1] and not dead_at(r.league, r.wall, r.plays, st)[0]:
            x = j.A[g2]
            if x.legal[j._at(x, pj)]:
                walk += 1
                after_miss += int(not cutgrade.after_bucket(r.league, r.wall, r.plays, st))
    card = cutgrade.grade(rs, log, 0.0, 0.0)
    return {"grader_entries": card.entries, "grader_entries_mid_play": card.entries_mid_play,
            "grader_entries_mid_play_strict": card.entries_mid_play_strict, "grader_entries_after_bucket": card.entries_after_bucket,
            "grader_leaves": card.leaves, "grader_leaves_mid_play": card.leaves - card.leaves_in_dead - card.leaves_between_plays,
            "joins_in_shot_window": walk, "joins_after_miss": after_miss}


def seen_card(show: Show) -> dict:
    """[SEEN] The phase 2C scorecard, judged where the pictures truly were (the judge's own seconds on the whole recorded
    night's facts), whatever the show believed:
        big_seen_live / big_total   must-see plays (the grader's big plays, park_core.Arr.mom) in the replayed span that
                                    were on screen with their lead-in as they happened (the judge's rule)
        worst_unseen_s              the longest any game in progress went with no second of it on screen (the show's clock;
                                    a game never on counts from its start or the show's)
        mean_unseen_s               seconds since each game in progress was last on screen, averaged over every second and
                                    every game in progress (the age of what the viewer knows of it; 0 for the one on screen)
        worst_unseen_live_s         the most seconds of LIVE PLAY (at the show's edge) any game ran with none of it on
                                    screen in between: the same stretch with halftimes, timeouts and dead balls left out
        drivers                     with the director on: jumps carried out by the goal that drove each (a, b, c)"""
    j = show.judge
    a = show.night.span[0]
    b = (show.last_t + 1) if show.last_t is not None else a
    A = j.A
    big = [(g, i) for g, x in enumerate(A) for i in x.mom if a <= i < b]
    seen_big = sum(1 for k in big if k in j.shown)
    last = [max(a, x.a0) for x in A]
    worst, live, worst_live, total, n = [0] * len(A), [0] * len(A), [0] * len(A), 0, 0
    for t in range(a, b):
        on = j.tl.get(t)
        for g, x in enumerate(A):
            if not (x.a0 <= t <= x.a1):
                continue
            if on == g:
                last[g], live[g] = t, 0
            elif not x.dead[min(x.n - 1, t)]:
                live[g] += 1
                worst_live[g] = max(worst_live[g], live[g])
            age = t - last[g]
            worst[g] = max(worst[g], age)
            total, n = total + age, n + 1
    out = {"big_seen_live": seen_big, "big_total": len(big), "worst_unseen_s": max(worst, default=0),
           "mean_unseen_s": round(total / n, 1) if n else 0.0, "worst_unseen_by_game_s": worst,
           "worst_unseen_live_s": max(worst_live, default=0)}
    if show.director is not None:
        out["drivers"] = dict(show.drivers)
    return out


# ---------------------------------------------------------------------------------------------- the numbers
def result(show: Show) -> dict:
    """The run as one row: park_core.summarize's own columns, judged where the pictures truly were."""
    end = (show.last_t + 1) if show.last_t is not None else show.night.span[0]
    night = copy.copy(show.night)
    night.span = (show.night.span[0], max(show.night.span[0] + 1, min(show.night.span[1], end)))
    j = show.judge
    st = show.st if j is None else j.stats(show.st)
    row = park_core.summarize([(night, st)])
    row["flips"], row["steps_refused"] = len(st.hops), dict(show.refused)
    row["stayed_s"] = dict(sorted(show.stayed.items(), key=lambda kv: -kv[1]))
    row["behind_med_s"], row["behind_max_s"] = (row["behind_med_s"] or 0) + show.delay, (row["behind_max_s"] or 0) + show.delay
    looks = sorted(x for x, live in show.looks if live >= 3)
    row["look_med_s"] = looks[len(looks) // 2] if looks else ""
    times = list(j.jumps) if j is not None else list(st.jumps)
    row["peak_1m"], row["best_5m"] = peak(times, 60), peak(times, 300)
    row.update(warrant_counts(show.warrants))
    row.update(tunes=show.tunes_sent, tunes_skipped=show.tunes_skipped, back_tunes=show.back_tunes)
    mins = max(1e-9, row["minutes"])
    if j is not None:
        row.update(graded(show))
        pic = max(1, j.pic)
        row.update(plays_per_look=round(j.plays / max(1, len(j.jumps)), 2), live_share=round(j.live_s / pic, 3),
                   dead_share=round(j.dead_s / pic, 3), commercial_share=round(j.brk_s / pic, 3), noplay_looks=j.noplay,
                   break_lands=j.brk_land, value_per_min=round(j.v2 / mins, 3),
                   land_wait_med_s=sorted(j.land_wait)[len(j.land_wait) // 2] if j.land_wait else "")
    return row


def _pct(v) -> str:
    return f"{100 * v:.0f}%" if isinstance(v, (int, float)) else "-"


def summary(show: Show, head: str) -> tuple[dict, str]:
    row = result(show)
    why = "; ".join(f"{k} {v}" for k, v in sorted(row["warrant_why"].items(), key=lambda kv: -kv[1])) or "none"
    dl = f"{round(row['planned_delivered'] * row['planned_jumps'])} of {row['planned_jumps']}" if row["planned_jumps"] else "none planned"
    lines = [head, f"  minutes {row['minutes']}, jumps {row['jumps']} ({row['jumps_per_min']} a minute), flips {row['flips']}",
             f"  most jumps in one minute {row['peak_1m']}, in the best 5 minutes {row['best_5m']}",
             f"  warranted: {row['warranted']} of the {row['warrant_n']} jumps made ({why})",
             f"  on screen: live play {_pct(row.get('live_share'))}, dead balls {_pct(row.get('dead_share'))}, breaks {_pct(row.get('commercial_share'))}",
             f"  planned jumps delivered: {dl}; tunes sent {row['tunes']} (not needed {row['tunes_skipped']}, sent back {row['back_tunes']})",
             f"  looks that showed play: median {row['look_med_s']} s; looks with no play {row.get('noplay_looks', '-')}; landed in a break {row.get('break_lands', '-')}",
             f"  behind live: median {row['behind_med_s']} s, worst {row['behind_max_s']} s",
             "  seconds stayed, by cause: " + ("; ".join(f"{k} {v}" for k, v in row["stayed_s"].items()) or "none"),
             "  steps refused: " + ("; ".join(f"{k} x{v}" for k, v in show.refused.items()) or "none"),
             f"  Rule #1 by the FROZEN grader (cutgrade.grade, on where the pictures truly were): joins mid-play {row['grader_entries_mid_play']} "
             f"of {row['grader_entries']} (strict, without the made-basket window: {row['grader_entries_mid_play_strict']}), leaves mid-play "
             f"{row['grader_leaves_mid_play']} of {row['grader_leaves']}; joins in a shot window {row['joins_in_shot_window']}, of which after a MISS {row['joins_after_miss']}",
             f"  Rule #1 by the replay's own judge (the planner's own mask, so PARK against PARK): joins mid-play {row['mid_entry']}, leaves mid-play "
             f"{row['mid_leave']}, high-leverage leaves {row['hl_leave']}; cuts in a shot window: joins {row['walk_entry']}, leaves {row['walk_leave']}",
             f"  big plays seen {_pct(row['big_seen'])}, graded value seen {_pct(row['v2_seen'])}"]
    return row, "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="The app's PARK controller on a recorded dev night, with fake sticks.")
    ap.add_argument("--night", required=True, help="a dev night id, like 20261023")
    ap.add_argument("--games", type=int, default=4)
    ap.add_argument("--league", default="nba")
    ap.add_argument("--case", choices=tuple(FEED_LEAD), default="tonight", help="tonight: every game has a K and a read per tune; nok: no K")
    ap.add_argument("--hands", choices=("app", "runner"), default="app", help="app: the moves Controller.park_* has; runner: the local show runner's")
    ap.add_argument("--first", choices=("", "read", "live"), default="", help="the first picture's place: read, or taken to be live and unread")
    ap.add_argument("--minutes", type=float, default=0.0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--fail", action="append", default=[], help="step=n: the next n of that step fail (tune, read, pause, resume, key, flip, readback, sound)")
    ap.add_argument("--lines", type=int, default=12, help="how many event lines to show")
    ap.add_argument("--what-if-grid", type=int, nargs=2, default=None, metavar=("HEAD", "TAIL"),
                    help="WHAT-IF: whole seconds cut off the start and the end of every dead stretch for the 1 s grid (the show uses 1 2)")
    ap.add_argument("--what-if-any-shot", action="store_true",
                    help="WHAT-IF, OUTSIDE RULE #1: also cut in the 4 s after ANY shot, made or missed (the app never does)")
    ap.add_argument("--prime", type=int, default=0, help="[park.prime] how many of the slate's games are on Prime Video (the last N)")
    ap.add_argument("--prime-k", choices=("bug", "none"), default="bug", help="a Prime game's K: from its own bug reads, or none")
    ap.add_argument("--prime-pad", type=int, default=None, help="what-if: a Prime game's padding in s (the parked-frame bug check)")
    ap.add_argument("--nfl", type=int, default=-1, help="[PARK-NFL] add the i-th one-game NFL dev night, time-shifted onto the slate")
    ap.add_argument("--pin", action="store_true", help="[pinned anchor] the football game stays on stream 0 and is never re-tuned")
    ap.add_argument("--slider", type=float, default=None, help="[football slider] 0 = more everything else ... 100 = more football")
    ap.add_argument("--deep", action="store_true", help="[PARK-NFL-LAG] park the late-feed game (lag - 8 s) deeper")
    ap.add_argument("--nfl-lag", type=float, default=None, help="[PARK-NFL] the football feed's lag in s (default: the show's 8 s)")
    ap.add_argument("--steps", choices=tuple(STEP_COSTS), default="model",
                    help="[park.fast_steps] step costs: model (as before), off / on (the app's steps with park.fast_steps off / on, "
                         "priced from the Nov 11 TV test and scripts/park_step_bench.py)")
    ap.add_argument("--walk", choices=("any", "made", "none"), default="any",
                    help="walk-in points: after any shot (as before), MADE field goals only, or none (dead time only)")
    ap.add_argument("--seen", action="store_true", help="[SEEN] track what the viewer has seen (park.seen) and print the phase 2C scorecard")
    ap.add_argument("--director", action="store_true", help="[DIRECTOR] Director v1 on (park.director_v1); implies --seen")
    a = ap.parse_args()
    shown = [0]

    def say(line: str) -> None:
        shown[0] += 1
        if shown[0] <= a.lines:
            print(line)
    fail = {k: int(v) for k, v in (x.split("=", 1) for x in a.fail)}
    show = replay(a.night, a.games, case=a.case, hands=a.hands, first=a.first, minutes=a.minutes, seed=a.seed, league=a.league,
                  fail=fail, say=say, any_shot=a.what_if_any_shot, grid=tuple(a.what_if_grid) if a.what_if_grid else None,
                  prime=a.prime, prime_k=a.prime_k, prime_pad=a.prime_pad, walk=a.walk, nfl=a.nfl, nfl_lag=a.nfl_lag,
                  deep=a.deep, slider=a.slider, pin=a.pin, seen=a.seen or a.director, director=a.director, step_costs=a.steps)
    _, text = summary(show, f"SUMMARY (replayed on recorded night {a.night} with FAKE sticks, not measured on the TV; {a.games} games, "
                            f"case {a.case}, hands {a.hands}, paddings {sorted(set(show.pad))} s, show delay {show.delay} s)"
                      + ("\n  WHAT-IF, OUTSIDE RULE #1: the any-shot rule is ON (cuts in the 4 s after any shot, made or missed)" if a.what_if_any_shot else ""))
    print(f"... {max(0, shown[0] - a.lines)} more lines not shown\n{text}")
    if any(r.league.base == League.NFL for r in getattr(show, "replays", [])):
        pic = picture_grade(show)
        print(f"  stamp = snap judge (football): joins mid-play {pic['football_joins_mid']}, leaves mid-play {pic['football_leaves_mid']} "
              f"of {pic['football_cuts']} football cuts; plays seen from the snap {snap_share(show, len(show.replays) - 1)}")
    if a.seen or a.director:
        sc = seen_card(show)
        print(f"  [SEEN] big plays seen live {sc['big_seen_live']} of {sc['big_total']}; longest a game in progress went unseen "
              f"{sc['worst_unseen_s'] / 60:.1f} min ({sc['worst_unseen_live_s'] / 60:.1f} min of it live play); mean age of what is "
              f"known of a game {sc['mean_unseen_s'] / 60:.1f} min"
              + (f"; jumps by goal: a {sc['drivers']['a']}, b {sc['drivers']['b']}, c {sc['drivers']['c']}" if "drivers" in sc else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
