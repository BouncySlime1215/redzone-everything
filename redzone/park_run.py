"""The engine's side of the PARK show (integration plan unit 8): with `park.enabled` on, the app runs the two-stick
show itself.

`ParkRun.loop()` (one of the engine's loops; it only sleeps while the flag is off) each second:
  - stands down unless the PARK steps may run (`Controller.park_why()`: the flag, two sticks, the sound guard with
    silence required, no open incident), no user, safe-mode or replay owner holds the TV, and two or more live games
    of `park.leagues` are on YouTube TV channels with the game on screen among them;
  - starts a show: each game's K from the K file (never another game's, never a default), each game's padding from
    `park_timing`, the stream on screen placed by `Controller.park_start` (read, or tuned afresh), then
    `park_show.Show` over the engine's own summaries (`engine.summaries`);
  - ticks it, and hands its stick moves to `ControllerRig`, which only calls `Controller.park_*` (unit 7).
A sound stop, or a jump whose result is "stop", ends the show until the flag is turned off and on again by hand.
On any stand-down the sticks are finished first (`park_finish`: never leave a stick paused).

`ControllerRig` gives the show each stream's place from the moves' own results: which game, read or not, paused or
not, how far behind the show's edge. The plan and the guard then use exactly the padding that stream has earned.

No stick or TV I/O here (rule K3): only the controller's park steps. Nothing here runs or is imported with the flag
off beyond the loop's own sleep.

Why: the show ran on the TV from a local tool with its own loop, feed and sticks; the product is the app doing it.
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
import time

from redzone.models import Phase
from redzone.noggin.k_table import read_k_table
from redzone.noggin.park_core import PARK_PARAMS
from redzone.noggin.park_show import FactsFeed, LiveNight, Show, ShowParams, ShowTiming, engine_setting
from redzone.noggin.park_timing import GameTiming, TimingParams, k_for
from redzone.showboard import Board

log = logging.getLogger(__name__)
SLATE_MAX = 6                      # the show's slate: at most this many games (the planner was run on 2 to 6)
OTHER_OWNERS = ("user", "safe", "replay")


class WallClock:
    """The show's clock: seconds since the grid's base, on the wall clock."""

    def __init__(self, base: int, now=time.time, sleep=asyncio.sleep):
        self.base, self._now, self._sleep = base, now, sleep

    def now(self) -> float:
        return self._now() - self.base

    async def sleep(self, s: float) -> None:
        await self._sleep(max(0.0, s))


class ControllerRig:
    """The app's two sticks and TV behind the show's device interface (`park_show.Show`), through the controller's park
    steps only. Stream k is `ctl.sticks[k]`."""
    instant = False
    join_playing = False           # Controller.park_jump resumes a PARKED stick: it never joins one that plays
    deep = False                   # Controller.park_tune reads with park.steps.read_steps presses: no tune is sent further back

    def __init__(self, ctl, clock: WallClock, targets: list[str], params: ShowParams):
        hands = ctl.park_hands()
        self.ctl, self.clock, self.targets, self.P, self.hands = ctl, clock, list(targets), params, hands
        self.steps, self.delay, self.live_lag = int(hands.step_s("read_steps")), params.show_delay, hands._live_lag()
        self.tune_lag = (params.step * self.steps + params.read_sat - self.delay) if self.steps else 0.0
        self.jump_s, self.unseen_s = params.jump_real, params.jump_real - 0.5
        self.hold_off = int(math.ceil(params.jump_real + self.unseen_s))
        self.pause_lead = tuple(hands.pause_lead(s) for s in ctl.sticks[:2])
        self.names = tuple(ctl._stick_label(s.host) for s in ctl.sticks[:2])
        self.ports = tuple(str(ctl.hdmi.get(s.host) or "") for s in ctl.sticks[:2])
        self.game, self.paused, self.lag, self.frozen, self.read = [None, None], [False, False], [0.0, 0.0], [0.0, 0.0], [False, False]
        self.note: dict = {}
        self._over: dict = {}      # stream -> a place the show put in place of an unbelievable read (replace)
        self.counts = {"steps": 0, "failed": 0, "refused": 0}
        self.sync()

    # ---- each stream's place, from the controller's own records ---------------------------------------------------
    def sync(self) -> None:
        wall = self.clock.base + self.clock.now()
        for k, stick in enumerate(self.ctl.sticks[:2]):
            p = self.hands.place_of(stick)
            if p is None or p.target not in self.targets:
                self.game[k], self.paused[k], self.read[k] = None, False, False
                continue
            self.game[k], self.paused[k], self.read[k] = self.targets.index(p.target), bool(p.paused), bool(p.read)
            behind = p.behind_s(wall)                             # behind YouTube's clock; a stick at its live edge is live_lag behind
            self.lag[k] = behind - self.live_lag - self.delay
            if p.paused and p.frozen_pos is not None:
                self.frozen[k] = p.content(p.frozen_pos) + self.live_lag + self.delay - self.clock.base
            if k in self._over:
                self.lag[k], self.read[k] = self._over[k], False

    def replace(self, k: int, lag: float) -> None:
        """Forget what was read of stream k's place: it is `lag` behind the show's edge, and its tune counts as not read."""
        self._over[k] = float(lag)
        self.lag[k], self.read[k] = float(lag), False

    def sound(self) -> dict:
        g = getattr(self.ctl.tv, "sound_guard", None)
        s = dict(getattr(g, "last", None) or {})
        ok = bool(s) and str(s.get("mute", "")).lower() in ("muted", "true", "on") and s.get("volume") == 0
        return {"mute": s.get("mute"), "volume": s.get("volume"), "read_back_at": s.get("at") or s.get("read_back_at"), "ok": ok}

    def _hidden(self, k: int) -> bool:
        return self.ctl.hidden is not None and self.ctl.sticks.index(self.ctl.hidden) == k

    def _done(self, k: int, out) -> None:
        self.counts["steps"] += 1
        if not out.ok:
            self.counts["refused" if out.outcome == "refused" else "failed"] += 1
        self.note[k] = {"way": out.way, "why": out.why, **{a: b for a, b in out.numbers.items() if isinstance(b, (int, float)) and not isinstance(b, bool)}}
        self.sync()

    # ---- the steps (each is one controller park step) ----------------------------------------------------------------
    async def tune(self, k: int, game: int, steps: int | None = None) -> tuple:
        t0 = self.clock.now()
        if not self._hidden(k):
            self.note[k] = {"way": "", "why": "only the hidden stick is tuned"}
            return False, 0.0
        # the show asks for a tune of a game this stream already holds only when it has fallen too far behind: reload it
        out = await self.ctl.park_tune(self.targets[game], fresh=self.game[k] == game)
        self._over.pop(k, None)
        self._done(k, out)
        return bool(out.ok), self.clock.now() - t0

    async def pause(self, k: int, at: float | None) -> tuple:
        if self.game[k] is None or not self._hidden(k):
            return False, None
        wall = None if at is None else self.clock.base + at + self.pause_lead[k]     # the frame freezes pause_lead after the key
        out = await self.ctl.park_pause(self.targets[self.game[k]], wall)
        self._done(k, out)
        return (True, self.frozen[k]) if out.ok else (False, None)

    async def resume(self, k: int) -> tuple:
        if self.game[k] is None or not self._hidden(k):
            return False, None
        out = await self.ctl.park_resume(self.targets[self.game[k]])
        self._done(k, out)
        return (True, self.lag[k]) if out.ok else (False, None)

    async def jump(self, k: int, resume: bool, hold: int | None = None) -> tuple:
        if not resume or self.game[k] is None or not self._hidden(k):
            return "kept", "the stick to join is not parked on that game"
        out = await self.ctl.park_jump(self.targets[self.game[k]])
        self._done(k, out)
        if isinstance(out.numbers.get("jump_s"), (int, float)):
            self.jump_s = float(out.numbers["jump_s"])
        if out.ok:
            return "ok", ""
        if out.outcome == "stop" or (out.outcome == "failed" and out.numbers.get("input_ok")):
            return "stop", out.why or "the jump stopped"          # sound, or the TV flipped and the new picture is not playing
        return "kept", out.why or "the jump was refused"          # the picture that was up is still up

    def confirming(self, k: int):
        return None                                               # park_jump reads the stick left back before it returns

    async def finish(self) -> None:
        try:
            await self.ctl.park_finish()
        except Exception:
            log.exception("park: finish")


def my_prefs(names: list, teams: list, weight: float) -> list:
    """What each game is worth to Nick: his teams' games are weighted up. Only the plan's values change: every jump
    still passes the same padding tests."""
    mine = {str(t).upper() for t in teams}
    return [weight if set(re.split(r"[^A-Z0-9]+", str(n).upper())) & mine else 1.0 for n in names]


class ParkRun:
    """One per engine. `owns()` is what the decider asks (owner `park`): True while a show holds the two sticks."""

    def __init__(self, engine, *, now=time.time, sleep=asyncio.sleep, model=None, watcher=None):
        self.e, self._now, self._sleep = engine, now, sleep
        self.model, self.watcher = model, watcher                 # the seams: None = off (park_show.NoModel / NoWatcher shapes)
        self.show: Show | None = None
        self.rig: ControllerRig | None = None
        self.board: Board | None = None
        self.clock: WallClock | None = None
        self.state, self.why, self.final = "off", "", ""          # final: a stop that only a hand clears (flag off, then on)
        self.gids: list[str] = []
        self.seen: dict[str, int] = {}
        self.end, self.retry_at, self.shows = 0, 0.0, 0

    # ---- what others ask ---------------------------------------------------------------------------------------------
    def on(self) -> bool:
        return bool(self.e.cfg.get("park.enabled", False))

    def owns(self) -> bool:
        return self.show is not None and not self.show.stop and self.on()

    def view(self) -> dict:
        """For Engine.snapshot(): the show's state as the Show tab's contract (words and numbers only)."""
        out = {"state": self.state, "why": self.why, "shows": self.shows}
        if self.board is not None and self.board.latest:
            out["show"] = self.board.latest
        if self.show is not None:
            out["jumps"] = [dict(r, leave_at=None, join_at=None) for r in self.show.records[-5:]]
            out["refused"] = dict(self.show.refused)
        return out

    def _set(self, state: str, why: str = "") -> None:
        if (state, why) != (self.state, self.why):
            log.info("park show: %s %s", state, why)
        self.state, self.why = state, why

    # ---- the loop ----------------------------------------------------------------------------------------------------
    async def loop(self) -> None:
        idle = float(self.e.cfg.get("park.idle_poll_s", 5.0) or 5.0)
        while True:
            try:
                if not self.on():
                    if self.show is not None or self.final:
                        await self.stand_down("park.enabled is off")
                    self.final = ""
                    self._set("off")
                    await self._sleep(idle)
                    continue
                await self.step()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("park show: a tick failed")
                await self.stand_down("an error in the show: see the log")
                self.final = self.final or "an error in the show"
            if self.show is not None and self.clock is not None:
                await self.clock.sleep(math.floor(self.clock.now()) + 1 - self.clock.now())
            else:
                await self._sleep(1.0)

    def _blocked(self) -> str:
        ctl, dec = self.e.control, self.e.decider
        why = ctl.park_why()
        if why:
            return why
        owner = dec.owner_m.state
        if owner in OTHER_OWNERS:
            return f"the TV is held by: {owner}"
        return ""

    async def step(self) -> None:
        """One second of the loop (the flag is on)."""
        if self.final:
            self._set("stopped", self.final)
            return
        why = self._blocked()
        if why:
            if self.show is not None:
                await self.stand_down(why)
            self._set("waiting", why)
            return
        if self.show is None:
            if self._now() >= self.retry_at:
                await self.begin()
            return
        show, t = self.show, int(self.clock.now())
        if show.j_op is None and self.e.control.active != show.on:
            await self.stand_down("the TV input changed outside the show")
            return
        self.rig.sync()
        self.offer(t)
        await show.tick(t)
        if show.stop:
            self.final = show.stop
            await self.stand_down(f"stopped: {show.stop}")
        elif t >= self.end or not self.still_live():
            await self.stand_down("the session ended")

    def my_teams(self) -> list:
        """Nick's teams, as the engine reads them from `teams` (every league's, as short names)."""
        read = getattr(self.e, "_my_teams", None)
        try:
            return sorted({str(t) for teams in (read() if read is not None else {}).values() for t in teams})
        except Exception:
            log.exception("park show: my teams")
            return []

    def still_live(self) -> bool:
        games = self.e.merger.games
        return sum(1 for gid in self.gids if gid in games and games[gid].phase == Phase.LIVE) >= 2

    def offer(self, t: int) -> None:
        """Hand the feed every summary the engine has polled since the last second."""
        for g, gid in enumerate(self.gids):
            data = self.e.summaries.get(gid)
            if data is None or self.seen.get(gid) == id(data):
                continue
            self.seen[gid] = id(data)
            self.show.feed.offer(g, data, t)

    def slate(self) -> tuple[list, str]:
        """The games the show takes: live, in park.leagues, on a YouTube TV channel, with a summary; the game on screen
        first. ([], why) when there are fewer than two or the screen's game is not among them."""
        e, dec = self.e, self.e.decider
        leagues = {str(x).lower() for x in (e.cfg.get("park.leagues", None) or [])}
        out = []
        for gid, g in e.merger.games.items():
            target = dec.target_of(gid) or ""
            if g.phase == Phase.LIVE and g.league.value in leagues and target.startswith("youtubetv:") and gid in e.summaries:
                out.append((gid, g, target))
        on = e.control.on_target
        first = [x for x in out if x[2] == on]
        if not first:
            return [], "the game on screen is not a live game the show can take"
        rest = [x for x in out if x[2] != on and x[2] not in {y[2] for y in first}]
        chosen = (first[:1] + rest)[:SLATE_MAX]
        if len(chosen) < 2:
            return [], "fewer than two live games the show can take"
        return chosen, ""

    async def begin(self) -> None:
        e, ctl, cfg = self.e, self.e.control, self.e.cfg
        games, why = self.slate()
        if not games:
            self._set("waiting", why)
            return
        P, tp = ShowParams.from_config(cfg.get), TimingParams.from_config(cfg.get)
        table = read_k_table(cfg.get("timing.game_offset_file", "") or "") if cfg.get("timing.game_offset_file", "") else {}
        today = time.strftime("%Y-%m-%d", time.localtime(self._now()))
        steps = int(ctl.park_hands().step_s("read_steps"))
        tgs = [GameTiming(g.league.value, k_for(table, channel=target.split(":", 1)[1], league=g.league.value, game_id=gid,
                                               teams=(g.home.abbr, g.away.abbr), today=today, params=tp), tp)
               for gid, g, target in games]
        tm = ShowTiming(tgs, delay=P.show_delay, read=steps > 0, eyes=self.watcher is not None, params=tp)
        shifts = [tm.feed_delay(k) + tm.delay for k in range(len(games))]
        leads = [sh - P.feed_lag - P.poll - 0.5 for sh in shifts]
        eng = engine_setting(tm.pad0(), int(max(leads)), late_cap=P.late_cap - tm.delay - P.jump_slack, tune_s=P.tune0_code + (P.read if steps else 0.0),
                             min_live=P.min_look, land_wait=P.land_wait, flip=P.jump_real, pause_reach=P.pause_reach)
        minutes = float(cfg.get("park.session_min", 240) or 240)
        base = int(self._now()) - PARK_PARAMS.pad_s
        clock = WallClock(base, self._now, self._sleep)
        targets = [target for _, _, target in games]
        rig = ControllerRig(ctl, clock, targets, P)
        on = ctl.active
        if rig.game[on] != 0:                                    # the stream on screen has no place yet: read it, or tune it afresh
            self._set("starting", "placing the stream on screen")
            out = await ctl.park_start()
            rig.sync()
            if not out.ok or rig.game[on] != 0:
                self.retry_at = self._now() + float(cfg.get("park.start_retry_s", 30) or 30)
                self._set("waiting", f"the stream on screen could not be placed ({out.why or out.outcome})")
                return
        a = int(clock.now()) + 2
        self.end = a + int(minutes * 60)
        night = LiveNight(len(games), base, PARK_PARAMS.pad_s + int(minutes * 60) + 900, (a, self.end), eng)
        stop_min = dict(cfg.get("park.show.stop_min", None) or {}) if P.stops else {}
        feed = FactsFeed([(g.league, gid) for gid, g, _ in games], night, shifts, leads, stop_min=stop_min)
        names = [g.matchup for _, g, _ in games]
        folder = cfg.get("show.state_dir", "") or "data/show"
        self.board = Board(folder, now=self._now)
        self.show = Show(night, eng, tm, rig, feed, names=names, chans=[t.split(":", 1)[1] for t in targets],
                         leagues=[g.league.value for _, g, _ in games], board=self.board, say=lambda line: log.info("park show: %s", line),
                         tune0={k: P.tune0_code + (P.read if steps else 0.0) for k in range(len(games))},
                         prefs=my_prefs(names, self.my_teams(), P.my_pref),
                         params=P, model=self.model, eyes=self.watcher)
        self.show.begin(0, on, a)
        self.rig, self.clock, self.gids, self.seen = rig, clock, [gid for gid, _, _ in games], {}
        self.shows += 1
        self.board.start(self.show, "live", minutes)
        self._set("running", f"{len(games)} games")

    async def stand_down(self, why: str) -> None:
        """End the show: the sticks are finished first (never leave one paused), then the state says why."""
        show, self.show = self.show, None
        if show is not None:
            try:
                await show.close()
            except Exception:
                log.exception("park show: close")
        self.rig = self.clock = None
        self._set("stopped" if self.final else "waiting", why)
