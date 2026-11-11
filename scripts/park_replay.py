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
from redzone.noggin.park_plan import Plan  # noqa: E402
from redzone.noggin.park_show import SHOW, FactsFeed, LiveNight, Show, ShowParams, ShowTiming, engine_setting, peak, warrant_counts  # noqa: E402

FEED_LEAD = {"tonight": 13.0, "nok": 25.0}      # replay: seconds the feed runs ahead of a stream's live edge (a K near 0: 21 s of
#                                                 delay less the feed's 8 s; no K: the 33 s prior less 8 s)
READ_BACK = (0.4, 1.6, 0.6)                     # BACK after a pause key; the read-back after BACK; the TV's read-back after a flip
RESUME_REPORT = 0.69                            # a resume key to its "playing" report
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

    def _draw(self, g: int, read_ok: bool) -> float:
        return self.rng.gauss(0.0, self.tm.errs(g, read_ok)[0]) + self.gd[g]

    def tuned(self, k: int, g: int, read_ok: bool = True) -> None:
        self.e[k] = self._draw(g, read_ok)

    def _at(self, x, p: float) -> int:
        return min(x.n - 1, max(0, int(p)))

    def second(self, k: int, g: int, pos: float, t: int | None = None) -> None:
        x = self.A[g]
        ti = self._at(x, pos - self.e.get(k, 0.0))
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
        ti = self._at(xs, i - self.e.get(k1, 0.0) + self.rng.gauss(0.0, math.hypot(e1[2], e1[3])))
        ej = self._at(xh, j - self.e.get(k2, 0.0) + self.rng.gauss(0.0, e2[2] if paused else math.hypot(e2[2], e2[3])))
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
                 hands: str = "runner", params: ShowParams = SHOW, tune_sd: float = 1.5):
        P = self.P = params
        self.clock, self.instant, self.rng, self.tune_sd = clock, instant, random.Random(seed), tune_sd
        self.unseen_s, self.pause_lead, self.hold_off = P.unseen, (0.70, 0.60), P.hold_off
        self.join_playing, self.deep = hands != "app", hands != "app"
        self.tune_range, self.latency, self.note = tune_range, latency, {}      # (whole tune seconds lo..hi; a channel's lag over the nominal)
        if not instant:                                          # the real TV's flips on Nov 10 night: 2.4 to 3.3 s
            self.flip_s = P.jump_real - P.resume_lead
            self.unseen_s = self.flip_s + P.resume_lead - 0.5
            self.hold_off = int(math.ceil(P.resume_lead + self.flip_s + self.unseen_s))
        self.bad_place = bad_place                               # seconds added to what a tune reports as the stream's place (a bad reading)
        self.game, self.paused, self.lag, self.frozen, self.read = [None, None], [False, False], [0.0, 0.0], [0.0, 0.0], [read0, read0]
        self.input = self.first = 0
        self.silent, self.sent, self.fail, self.tune_s = True, [], {}, tune_s or {}
        self.delay, self.steps, self.reads = delay, steps, reads or (lambda g: False)
        self.back = (P.step * steps + P.read_sat) if steps else 0.0   # what the read gesture puts a stream behind its live edge
        self.tune_lag = self.back - delay
        self._conf: dict = {}
        self.sound_at, self.incident = None, ""
        self.true_lag = [0.0, 0.0]

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
        self.game[k], self.note[k] = None, {"way": "code", "why": ""}
        await self._wait(max(took, SESSION_ZERO))                # (the session's position-0 seconds are inside every tune)
        if self._fails("tune"):
            self.note[k]["why"] = "made to fail"
            return False, took
        self.read[k] = bool(self.reads(game)) and not self._fails("read")
        self.note[k]["way"] = "code, read" if self.read[k] else "code, no read"
        self.game[k], self.paused[k] = game, False
        back = (P.step * steps + P.read_sat - self.delay) if steps else 0.0
        self.lag[k] = back + self.latency + (self.rng.gauss(0.0, self.tune_sd) if self.read[k] else 0.0) if (deep or not self.instant) else 0.0
        self.true_lag[k] = self.lag[k]
        self.lag[k] += self.bad_place
        if steps:
            self.sent += [("key", k, "KEYCODE_DPAD_CENTER")] + [("key", k, "KEYCODE_DPAD_LEFT")] * steps + [("key", k, "KEYCODE_DPAD_CENTER")]
        return True, took

    async def _freeze(self, k: int) -> None:
        self.sent.append(("key", k, "KEYCODE_MEDIA_PLAY_PAUSE"))
        await self._wait(self.pause_lead[k] + self.rng.gauss(0.0, 0.07))
        self.paused[k], self.frozen[k] = True, self._now() - self.lag[k]

    async def _confirm(self, k: int) -> tuple:
        await self._wait(READ_BACK[0])
        self.sent.append(("key", k, "KEYCODE_BACK"))
        await self._wait(READ_BACK[1])
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
        await self._wait(READ_BACK[2])
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
            self._conf[hold] = asyncio.ensure_future(self._confirm(hold))
        return "ok", ""

    async def finish(self) -> None:
        for fut in self._conf.values():
            fut.cancel()
        for k in (0, 1):
            if self.paused[k]:
                self.sent.append(("key", k, "KEYCODE_MEDIA_PLAY_PAUSE"))
                self.paused[k] = False


# ---------------------------------------------------------------------------------------------- the recorded night
def load_replay(night_id: str, n_games: int, league: str = "nba") -> tuple[list, list]:
    """(the recorded games of the slate, their whole-night facts): the slate park_plan.py would pick on that night."""
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
    slate = pc.slate([pc.truth_of(r) for r in rs], n_games)
    if not slate:
        raise SystemExit(f"night {night_id} has no slate of {n_games} games that start within 2.5 hours.")
    by = {r.gid: r for r in rs}
    return [by[g["gid"]] for g in slate], slate


class RowFeed:
    """Hands a recorded game to the show's feed row by row: a row stamped s is in the feed once the show's edge is
    `lead` short of s (the stream's delay less the feed's lag, plus the show's own delay); read every `poll` seconds."""

    def __init__(self, replays: list, feed: FactsFeed, base: int, lead: int, poll: int = 3):
        self.rs, self.feed, self.base, self.lead, self.poll = replays, feed, base, int(lead), max(1, poll)
        self.k, self.first = [0] * len(replays), True

    def step(self, t: int) -> None:
        if t % self.poll and not self.first:
            return
        self.first = False
        for g, r in enumerate(self.rs):
            k = bisect.bisect_right(r.wall, self.base + t + self.lead)
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
           model=None, eyes=None, emit=None) -> Show:
    """The app's PARK controller on a recorded dev night: fake sticks, a fake TV, a clock that is not the wall.
    first = "read" (the first picture's place was read, as the local runner starts) or "live" (the app's own start:
    the stream on screen was never read; it is taken to be at its live edge and cut at the wide padding)."""
    P = params or ShowParams.from_config(config.default)
    first = first or ("live" if hands == "app" else "read")
    rs, slate = loaded or load_replay(night_id, games, league)
    tm = ShowTiming.case(case, len(rs), league=league, delay=P.show_delay)
    known = case == "tonight"
    lead = int(P.show_delay + FEED_LEAD[case])
    steps = P.steps_for(known)
    eng = engine_setting(tm.pad0(), lead, late_cap=P.late_cap - P.show_delay - P.jump_slack, tune_s=3.0 + (P.read if steps else 0.0),
                         min_live=P.min_look, land_wait=P.land_wait, flip=P.jump_real, pause_reach=P.pause_reach)
    full = park_core.Night(slate)
    A = full.arrs(eng.shots, eng.value, eng.live_w)
    a, b = full.span
    b = min(b, a + int(minutes * 60)) if minutes else b
    night = LiveNight(len(rs), full.base, full.n, (a, b), eng)
    night.big_total, night.v2_total = full.big_total, full.v2_total
    if stop_min is None:
        stop_min = dict(config.default("park.show.stop_min") or {}) if P.stops else {}
    feed = FactsFeed([(r.league, r.gid) for r in rs], night, [0.0] * len(rs), [lead] * len(rs), stop_min=stop_min)
    _, S0 = park_core.run(full, _Hindsight(), eng, t0=a, t1=a)     # the first picture, picked the way park_plan.py picks it
    clock = FakeClock(float(a))
    rig = FakeRig(clock, instant=False, seed=seed, delay=P.show_delay, steps=steps, reads=tm.reads, hands=hands, params=P,
                  tune_sd=tm.p.tune_sd, **(rig_kw or {}))
    rig.fail.update(fail or {})
    rig.game[0] = S0.sg
    if first == "live":                                          # the app's start: on screen since before PARK, never read
        rig.read[0], rig.lag[0] = False, -float(P.show_delay)
    judge = Judge(A, tm, seed, S0.sg, first_read=rig.read[0])
    tune0 = {g: 2.7 + (P.read if steps else 0.0) for g in range(len(rs))}
    show = Show(night, eng, tm, rig, feed, plan=Plan(jump_bonus=P.bonus), judge=judge, board=board, say=say or (lambda line: None),
                names=[r.matchup or r.gid for r in rs], chans=[f"channel {k + 1}" for k in range(len(rs))], tune0=tune0,
                params=P, model=model, eyes=eyes, emit=emit)
    S = show.begin(S0.sg, 0, a)
    S.sb = float(rig.lag[0])
    rows = RowFeed(rs, feed, full.base, lead, poll)
    if board is not None:
        board.start(show, "replay", (b - a) / 60.0)
    asyncio.run(clock.run(drive(show, clock, rows, a, b)))
    return show


class _Hindsight:
    """Only picks the first picture, the way park_plan.py does (the must-see value about to air)."""
    name = "hindsight"

    def bind(self, arrs: list, s) -> None:
        self.A = arrs

    def score(self, g: int, c: int, t: int, on_screen: bool, back: int = 0) -> float:
        P = park_core.PARK_PARAMS
        return self.A[g].ahead(c - back + (0 if on_screen else P.seen_lead_s), c + P.run_up_s, c)


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
             f"  Rule #1, judged on the whole recorded night: joins mid-play {row['mid_entry']}, leaves mid-play {row['mid_leave']}, "
             f"high-leverage leaves {row['hl_leave']}; big plays seen {_pct(row['big_seen'])}, graded value seen {_pct(row['v2_seen'])}"]
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
    a = ap.parse_args()
    shown = [0]

    def say(line: str) -> None:
        shown[0] += 1
        if shown[0] <= a.lines:
            print(line)
    fail = {k: int(v) for k, v in (x.split("=", 1) for x in a.fail)}
    show = replay(a.night, a.games, case=a.case, hands=a.hands, first=a.first, minutes=a.minutes, seed=a.seed, league=a.league,
                  fail=fail, say=say)
    _, text = summary(show, f"SUMMARY (replayed on recorded night {a.night} with FAKE sticks, not measured on the TV; {a.games} games, "
                            f"case {a.case}, hands {a.hands}, paddings {sorted(set(show.pad))} s, show delay {show.delay} s)")
    print(f"... {max(0, shown[0] - a.lines)} more lines not shown\n{text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
