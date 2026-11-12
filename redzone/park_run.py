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
import json
import logging
import math
import re
import time

from redzone.models import Phase
from redzone.noggin.k_table import read_k_table
from redzone.noggin import park_prime
from redzone.noggin.park_core import PARK_PARAMS
from redzone.noggin.park_facts import FactsParams
from redzone.noggin.park_show import FactsFeed, LiveNight, Show, ShowParams, ShowTiming, engine_setting
from redzone.noggin.park_timing import GameK, GameTiming, TimingParams, k_for
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
        self.tune_lag = (params.read_back(self.steps) - self.delay) if self.steps else 0.0
        _, self.unseen_s, self.hold_off = params.flip_times()    # the one derivation (park_show.ShowParams.flip_times)
        self.jump_s = params.jump_real
        self.pause_lead = tuple(hands.pause_lead(s) for s in ctl.sticks[:2])
        self.names = tuple(ctl._stick_label(s.host) for s in ctl.sticks[:2])
        self.ports = tuple(str(ctl.hdmi.get(s.host) or "") for s in ctl.sticks[:2])
        self.game, self.paused, self.lag, self.frozen, self.read = [None, None], [False, False], [0.0, 0.0], [0.0, 0.0], [False, False]
        self.depth: dict = {}                                    # [PARK-NFL-LAG] game -> s deeper (the show sets it)
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
        self.extra = getattr(self, "extra", {})
        self.extra[k] = 0.0
        if self.depth.get(game):
            self.hands.depth_s[self.targets[game]] = float(self.depth[game])     # [PARK-NFL-LAG] park it deeper
            self.extra[k] = float(self.depth[game])
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
        """The stick left's read-back still running after the jump returned, as a future of (ok, the frozen second), or
        None. Flag off: always None (park_jump reads the stick left back before it returns). [park.fast_steps] its BACK
        and read-back run after the jump (ParkHands.pending): the show holds the stream as not ready until this ends.
        Cancelling it never cancels the read-back itself; a new step on that stick ends it (ParkHands._settle)."""
        if not self.hands.pending:
            return None
        t = self.hands.pending.get(self.ctl.sticks[k].host)
        if t is None or t.done():
            self.sync()                                           # (a read-back that already ended: the stream as it left it)
            return None
        return asyncio.ensure_future(self._held(k, t))

    async def _held(self, k: int, t) -> tuple:
        try:
            ok = bool(await asyncio.shield(t))
        except asyncio.CancelledError:
            if not t.done():
                raise
            ok = False
        except Exception:
            ok = False
        self.sync()
        return (True, self.frozen[k]) if ok and self.paused[k] else (False, None)

    async def look(self, k: int) -> dict | None:
        """Where stream k, the one ON SCREEN, is now by one media-session read (Controller.park_look: no key): its lag
        behind the show's edge, and the report it came from (the session's own position and its update time)."""
        if self.ctl.main is None or self.ctl.sticks.index(self.ctl.main) != k:
            return None
        out = await self.ctl.park_look()
        n = out.numbers
        if not out.ok or n.get("behind_youtube_s") is None:
            return None
        self.counts["steps"] += 1
        upd = n.get("updated_wall")
        return {"lag": float(n["behind_youtube_s"]) - self.live_lag - self.delay, "pos_s": n.get("report_pos_s"),
                "updated": None if upd is None else round(float(upd) - self.clock.base, 2), "state": n.get("state")}

    async def reread(self, k: int) -> tuple:
        """Read stream k's place again after it drifted: only the stream on screen (Controller.park_start(reread=True):
        the read gesture, which steps the picture back once; with park.reread_in_place on it seeks nothing)."""
        t0 = self.clock.now()
        if self.ctl.main is None or self.ctl.sticks.index(self.ctl.main) != k:
            return False, 0.0
        out = await self.ctl.park_start(reread=True)
        self._over.pop(k, None)
        self._done(k, out)
        return bool(out.ok and self.read[k]), self.clock.now() - t0

    async def finish(self) -> None:
        try:
            await self.ctl.park_finish()
        except Exception:
            log.exception("park: finish")


def football_slider(cfg) -> float | None:
    """[football slider] park.football_slider (0-100; 50 = value decides, as before), or None when it is 50."""
    v = cfg.get("park.football_slider", 50)
    if not isinstance(v, (int, float)) or isinstance(v, bool) or float(v) == 50.0:
        return None
    return min(100.0, max(0.0, float(v)))


def director_params(cfg):
    """[DIRECTOR] Director v1's numbers when park.director_v1 is on (redzone/noggin/director.py), else None: off, the show
    builds no director and decides as before."""
    if cfg.get("park.director_v1", False) is not True:
        return None
    from redzone.noggin.director import DirectorParams
    return DirectorParams.from_config(cfg.get)


def feed_lags(cfg, leagues: list, base: float) -> list:
    """[PARK-NFL-LAG] Each game's feed lag to start from: with park.feed_lag.enabled, a listed league's quantile
    (park.feed_lag.prior_q) from redzone/lab/feed_lag_profile.json; every other game (and every game with it off): `base`."""
    out = [float(base)] * len(leagues)
    if cfg.get("park.feed_lag.enabled", False) is not True:
        return out
    import json
    from pathlib import Path
    listed = {str(x).lower() for x in (cfg.get("park.feed_lag.leagues", None) or [])}
    try:
        prof = json.loads((Path(__file__).parent / "lab" / "feed_lag_profile.json").read_text())
        qs, q = prof["quantiles"], int(cfg.get("park.feed_lag.prior_q", 90) or 90)
    except (OSError, ValueError, KeyError):
        return out
    for k, lg in enumerate(leagues):
        row = (prof.get("leagues") or {}).get(str(lg).lower())
        if str(lg).lower() in listed and row and q in qs:
            out[k] = max(float(base), float(row["q"][qs.index(q)]))
    return out


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
        self.end, self.retry_at, self.shows, self.told, self.starting = 0, 0.0, 0, 0, False

    # ---- what others ask ---------------------------------------------------------------------------------------------
    def on(self) -> bool:
        return bool(self.e.cfg.get("park.enabled", False))

    def owns(self) -> bool:
        """True while a show holds the sticks, and while one is being started (the stream on screen is being placed:
        no other decider may switch under the read)."""
        return self.on() and (self.starting or (self.show is not None and not self.show.stop))

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
        if park_prime.prime_on(self.e.cfg.get):
            self._prime_k()
        if self.e.cfg.get("park.feed_lag.enabled", False) is True:
            self._feed_lag()
        await show.tick(t)
        if t % 300 == 0:
            self.keep_plays()                                    # (every 5 minutes, and at every stand-down)
        if show.jumps_done != self.told:
            self.tell_screen()
        if show.stop:
            self.final = show.stop
            await self.stand_down(f"stopped: {show.stop}")
        elif over := self.ended():
            await self.stand_down(f"{', '.join(over)} ended: its channel may now carry another game")
        elif t >= self.end or not self.still_live():
            await self.stand_down("the session ended")

    def tell_screen(self) -> None:
        """A jump landed: the decider learns which game is on screen now (`Decider.adopt`: no tune, no switch), so the
        phone's "now" and every other reader of the screen's game follow the show."""
        show, dec = self.show, self.e.decider
        self.told = show.jumps_done
        gid, adopt = self.gids[show.S.sg], getattr(dec, "adopt", None)
        try:
            if adopt is not None and getattr(dec, "current", None) != gid:
                adopt(gid)
        except Exception:
            log.exception("park show: telling the decider the screen's game")

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

    def ended(self) -> list[str]:
        """[PARK-GAME-OVER] The show's games the scoreboard calls finished, as "AWAY @ HOME". A finished game's channel
        airs whatever comes next (Nov 11: ESPN's CLE @ NY, then GS @ HOU) and the engine stops polling its summary, so
        its facts freeze before the end: the show must not keep that game's name and facts on the stream."""
        games = self.e.merger.games
        return [f"{games[gid].away.abbr} @ {games[gid].home.abbr}" for gid in self.gids
                if gid in games and games[gid].phase == Phase.POST]

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
        out, claims = [], {}
        for gid, g in e.merger.games.items():
            target = dec.target_of(gid) or ""
            if g.phase == Phase.LIVE and target:
                claims.setdefault(target, []).append(f"{g.away.abbr} @ {g.home.abbr}")
            if g.phase == Phase.LIVE and g.league.value in leagues and park_prime.takes(target, e.cfg.get) and gid in e.summaries:
                out.append((gid, g, target))
        on = e.control.on_target
        if len(claims.get(on, ())) > 1:
            # [PARK-GAME-OVER] one channel, one game: which of them the picture shows is not known (a doubleheader
            # running long, a game moved to an overflow channel), so the show takes neither
            return [], f"{' and '.join(claims[on])} both claim the channel on screen"
        out = [x for x in out if len(claims.get(x[2], ())) == 1]
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
        lags = feed_lags(cfg, [g.league.value for _, g, _ in games], P.feed_lag)     # [PARK-NFL-LAG] off: P.feed_lag for every game
        leads = [sh - lags[k] - P.poll - 0.5 for k, sh in enumerate(shifts)]
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
            self.starting = True
            try:
                out = await ctl.park_start()
            finally:
                self.starting = False
            rig.sync()
            if not out.ok or rig.game[on] != 0:
                self.retry_at = self._now() + float(cfg.get("park.start_retry_s", 30) or 30)
                self._set("waiting", f"the stream on screen could not be placed ({out.why or out.outcome})")
                return
        a = int(clock.now()) + 2
        self.end = a + int(minutes * 60)
        night = LiveNight(len(games), base, PARK_PARAMS.pad_s + int(minutes * 60) + 900, (a, self.end), eng)
        stop_min = dict(cfg.get("park.show.stop_min", None) or {}) if P.stops else {}
        feed = FactsFeed([(g.league, gid) for gid, g, _ in games], night, shifts, leads, stop_min=stop_min,
                         params=FactsParams.from_config(cfg.get))   # (park.late_row_s: late-edit rows are put back)
        names = [g.matchup for _, g, _ in games]
        folder = cfg.get("show.state_dir", "") or "data/show"
        self.board = Board(folder, now=self._now)
        prime = park_prime.prime_on(cfg.get)                     # [park.prime] off: every line below is as before
        yttv_s = P.tune0_code + (P.read if steps else 0.0)
        keep = None
        if prime:
            self._prime_setup(ctl, rig, P, games)
            kf = next((k for k, (_, g, t) in enumerate(games)
                       if park_prime.app_of(t) == park_prime.PRIME and g.league.base.value == "nfl"), None)
            if cfg.get("park.prime.pin_football", False) is True and kf is not None and rig.game[on] == kf:
                keep = on                                        # [pinned anchor] the Prime football stream is never re-tuned
        self.show = Show(night, eng, tm, rig, feed, names=names,
                         chans=[("prime" if park_prime.app_of(t) == park_prime.PRIME else t.split(":", 1)[1]) for t in targets],
                         leagues=[g.league.value for _, g, _ in games], board=self.board, say=lambda line: log.info("park show: %s", line),
                         tune0={k: (park_prime.tune_cost(targets[k], cfg, yttv_s=yttv_s, show_delay=P.show_delay,
                                                         tune_lag=rig.tune_lag) if prime else yttv_s) for k in range(len(games))},
                         prefs=my_prefs(names, self.my_teams(), P.my_pref),
                         params=P, model=self.model, eyes=self.watcher,
                         no_back={k for k, t in enumerate(targets) if not park_prime.rewinds(t, cfg.get)} if prime else None,
                         depth=({k: max(0.0, lags[k] - P.feed_lag) for k in range(len(games)) if lags[k] > P.feed_lag}
                                if cfg.get("park.feed_lag.enabled", False) is True else None),
                         football_slider=football_slider(cfg), keep=keep, keep_live=keep is not None,
                         seen=cfg.get("park.seen", False) is True, director=director_params(cfg))
        self.show.begin(0, on, a)
        self.rig, self.clock, self.gids, self.seen, self.told = rig, clock, [gid for gid, _, _ in games], {}, 0
        self.shows += 1
        self.board.start(self.show, "live", minutes)
        self._set("running", f"{len(games)} games")

    def keep_plays(self, show: Show | None = None) -> int:
        """Write each game's play list as polled (rows, the stamps used, when each row was first seen) beside the show's
        state, so a live run can be regraded afterwards. Public play-by-play, in the state folder (never in the repo)."""
        show = show or self.show
        if show is None or self.board is None or self.board.dir is None or not hasattr(show.feed, "kept"):
            return 0
        n = 0
        try:
            folder = self.board.dir / "plays"
            folder.mkdir(parents=True, exist_ok=True)
            for g, gid in enumerate(self.gids):
                tmp = folder / f"{re.sub(r'[^A-Za-z0-9_-]', '_', str(gid))}.json.tmp"
                tmp.write_text(json.dumps({"game": show.names[g], "channel": show.chans[g], **show.feed.kept(g)}))
                tmp.replace(tmp.with_suffix(""))
                n += 1
        except OSError:
            log.exception("park show: play lists not written")
        return n

    # ---- [park.prime] Prime Video games -------------------------------------------------------------------------------
    def _prime_setup(self, ctl, rig, P, games) -> None:
        """Tell the hands where a fresh Prime stream is held to (where a YouTube TV tune and its read leave one) and,
        per Prime game, its teams and the feed's running-clock plays for its score-bug K."""
        hands = ctl.park_hands()
        hands.prime_behind_s = rig.tune_lag + rig.live_lag + P.show_delay
        timelines = getattr(self.e.decider, "timelines", {}) or {}
        for gid, g, target in games:
            if park_prime.app_of(target) != park_prime.PRIME:
                continue

            def plays(per, _gid=gid):
                tl = timelines.get(_gid)
                return sorted((w, cl) for w, p, cl in (getattr(tl, "clocks", ()) or ()) if p == per and cl is not None)
            hands.bug_ctx[target] = (g.home, g.away, plays)
            if g.league.base.value == "nfl":
                hands.football.add(target)                     # [PARK-NFL-LAG] the play clock gate applies (park.prime.bug_gate)

    def _prime_k(self) -> None:
        """A Prime game with no K yet takes the one its own stick's bug reads have earned (park_prime.game_k)."""
        show, hands = self.show, self.e.control.park_hands()
        if show is None or hands is None:
            return
        for g, target in enumerate(self.rig.targets):
            if park_prime.app_of(target) == park_prime.PRIME and not show.tm.g[g].known:
                got = hands.prime_k(target)
                if got is not None:
                    show.set_k(g, GameK(got[0], got[1], got[2], "bug"))

    def _feed_lag(self) -> None:
        """[PARK-NFL-LAG] A late-feed game's measured lag replaces its prior: its feed edge (leads) and its depth follow."""
        show, cfg = self.show, self.e.cfg
        leagues = {str(x).lower() for x in (cfg.get("park.feed_lag.leagues", None) or [])}
        min_n = int(cfg.get("park.feed_lag.min_samples", 5) or 5)
        for g in range(show.N):
            if str(show.leagues[g]).lower() not in leagues:
                continue
            lag = show.feed.lag_s(g, min_n)
            if lag is None:
                continue
            old = show.P.feed_lag + show.depth.get(g, 0.0)
            if abs(lag - old) >= float(cfg.get("park.feed_lag.move_s", 5) or 5):
                show.feed.leads[g] += old - lag
                show.depth[g] = max(0.0, lag - show.P.feed_lag)
                log.info("park show: %s feed lag measured %.0f s (was %.0f s): parked %.0f s deeper", show.names[g], lag, old, show.depth[g])

    async def stand_down(self, why: str) -> None:
        """End the show: the sticks are finished first (never leave one paused), then the state says why."""
        show, self.show = self.show, None
        if show is not None:
            self.keep_plays(show)
            try:
                await show.close()
            except Exception:
                log.exception("park show: close")
        self.rig = self.clock = None
        self._set("stopped" if self.final else "waiting", why)
