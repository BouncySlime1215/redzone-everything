"""The producer loop: poll every feed -> merge -> score -> decide -> act -> tell the phone.

Runs forever. Every stage is wrapped so one failure (a feed down, a bad game, a stick
offline) degrades that piece and keeps everything else running.
"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

from redzone import bets as betlib
from redzone import reel, standby, trace, userstate, views
from redzone.reel import REEL_DIR
from redzone.channels import REDZONE_TARGET, SETUP_CHANNELS, ChannelMap, describe, redzone_on
from redzone.config import Config, ConfigError
from redzone.control import Controller, Outcome
from redzone.decider.decider import REDZONE, REEL, Candidate, Decider, Decision, FocusMode, Missed, State
from redzone.decider.screen import (PLAY_LEN_S, TAP_HISTORY_S, Delay, DelayBook, break_on_arrival, catchable,
                                   leave_gate, leave_window, screen_evidence)
from redzone.models import GameState, League, Phase
from redzone.plan import Tonight
from redzone.scoring import breaks
from redzone.scoring.drive import DriveScorer
from redzone.scoring.engine import Personal, Scored, Scorer
from redzone.sources import espn, fastcast, fastcast_check, leagues
from redzone.sources.http import Fetcher
from redzone.sources.feeds import FeedBook
from redzone.sources.merge import Merger
from redzone.recorder import Recorder
from redzone.screen import boundary, scorebug
from redzone.store import Store

log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
FASTCAST_SYNC_S = 2.0
DECIDE_EVERY_S = 0.5
CLOCK_JUMP_S = 5.0     # wall clock moved this much more (or less) than time.monotonic(): it jumped
# Outcomes that say nothing about the target itself: a closed safe window, a cancel, an unknown
# TV input. They neither cool the target down nor count against it.
NOT_THE_TARGETS_FAULT = ("no safe moment", "safe exit window closed", "switch cancelled",
                         "TV input is unknown", "TV control was disabled", "no channel",
                         "stick not responding")
HOLD_RESUME_S = 45.0  # a held game turned garbage time: autopilot takes back over after this   # decision tick (cheap: re-scores ~40 games and runs the decider)


class Engine:
    def __init__(self, cfg: Config | None = None, store: Store | None = None):
        self.cfg = cfg or Config()
        self.store = store or Store()
        self.channels = ChannelMap()
        self.fetcher = Fetcher()
        self.merger = Merger()
        self._poll_every: dict[str, float] = {}   # game id -> the poll interval it was last fetched at
        self.feeds = FeedBook(self.merger, self.fetcher, self._feed_urls, self._feed_cadence)
        self.scorer = DriveScorer(self.cfg)
        self.delays = DelayBook(self.cfg.get("delay.default"), self.cfg.get("delay.sigma_default"),
                                model=self.cfg.get("delay.model", "channel"))
        try:   # every "I just saw it" from the last 30 days: a restart keeps tonight's taps
            self.delays.load(self.store.calibrations(since=time.time() - TAP_HISTORY_S))
        except Exception as e:
            log.warning("could not reload delay calibrations: %s", e)
        self.decider = Decider(self.cfg, self.delays)
        data_dir = getattr(self.cfg, "user_path", None)
        # [auto sync] every channel's delay tracked by a Kalman filter from the automatic signals
        # (redzone/decider/sync.py); every signal is logged to data/sync.jsonl for scripts/eval_sync.py.
        self.sync = None
        if self.cfg.get("delay.sync.enabled", True):
            self.sync = self.delays.attach_sync(
                min_sigma_s=float(self.cfg.get("delay.sync.min_sigma_s", 4.0)),
                log_path=data_dir.parent / "sync.jsonl" if data_dir and self.cfg.get("delay.sync.log", True) else None)
        self.control = Controller(self.cfg, self.channels,
                                  latency_path=data_dir.parent / "switch_times.json" if data_dir else None)
        self.decider.deadline_cost = self.control.deadline_cost
        # Break visits: the game we leave keeps playing on the other stick, so the trip back is one
        # flip; with one stick it's a full tune of that game.
        self.decider.return_cost = lambda t: (self.control.flip_estimate() if self.control.two_stick
                                              else self.control.deadline_cost(t))
        # Stream delay measured from YouTube TV show changes on the quarter hour (no taps needed).
        self.boundary = boundary.BoundaryWatcher(estimate=self._delay_estimate, on_sample=self._boundary_sample,
                                                 on_live=self.delays.set_live, on_position=self._sync_position,
                                                 on_tune=self._sync_tune)
        self.control.on_playback = self.boundary.observe_stick
        # Stream delay measured from the broadcast score bug in Prime screenshots (not DRM-blanked).
        self.scorebug = scorebug.ScoreBugWatcher(on_sample=self._scorebug_sample, on_tune=self._sync_tune,
                                                 on_clock=self._sync_clock)
        self.planner = standby.Planner(self.cfg)   # what the hidden stick holds between switches
        # owed TV timeouts per game: when a break is coming (+ the learned brain, when brain.enabled)
        self.breaks = breaks.BreakBook(brain=self._break_brain)
        self._cost_mode: bool | None = None         # two-stick mode when switch costs were last synced
        self._cost_synced = 0.0
        self.boosted: set[str] = set()
        self.skipped: dict[str, float] = {}   # game -> skip expires at
        self.scored: dict[str, Scored] = {}
        self.where: dict[str, object] = {}
        self.last_decision = None
        self.subscribers: set[asyncio.Queue] = set()
        self.source_status: dict[str, dict] = {}
        self._caffeinate: subprocess.Popen | None = None
        self._last_logged_reason = ""
        self._tick = 0
        self.started = time.time()
        self.summaries: dict[str, dict] = {}  # latest raw summary per live game (for the detail sheet)
        self.reeler = reel.Reeler(self.cfg)   # catch-up reel kept ready from the summaries' clips
        self._reel_task: asyncio.Task | None = None
        self.recorder = Recorder()
        self._last_snapshot = 0.0
        self.mic = None
        self.fantasy = None   # latest fantasy.Matchup
        self._suspended_focus: tuple | None = None   # (game, mode) paused for garbage time
        self.hold: dict | None = None   # "Watch this" from the phone: a manual hold until "Resume auto"
        self._mic_used: set[tuple[str, float]] = set()
        self._last_mic = 0.0
        self._pending_decision = None
        self._pending_since = 0.0
        self._pending_mono = 0.0   # the same moment on time.monotonic(): wall-clock jumps can't age a switch
        self._pending_done = None          # the in-flight switch's done callback (for the deadline)
        self._fail_cool: dict[str, float] = {}   # tuner target -> no retry before this time
        self.mic_suggestion = None
        self.fastcast = None
        self._fastcast_task = None
        self._fastcast_games = {}
        self._fastcast_mode = None          # "merge" (sources.fastcast) | "shadow" (the live check) | None
        # [fastcast live check] FastCast in shadow next to polling until it proves faster and consistent
        self.fc_check = fastcast_check.FastcastCheck(
            path=data_dir.parent / "fastcast_check.json" if data_dir else None)
        self._fc_graded = 0.0
        self._fresh = asyncio.Event()
        self._house_device_task = None
        self._st_scan_task = None
        from redzone.recorded import RecordedPlayback
        self.recorded = RecordedPlayback(self)
        from redzone.discovery.runtime import DiscoveryRuntime
        self.discovery = DiscoveryRuntime(self)
        from redzone.network import NetWatch
        # Home / away from home / Tailscale stealing the home routes (redzone/network.py). Read-only.
        self.network = NetWatch(self.discovery.autonomy.paths.devices)
        from redzone.platform.wake import WakeWatch
        self.wake = WakeWatch()                       # hardening.mac: the Mac slept / changed network
        self._net_where: str | None = None
        self.tonight = Tonight(self)   # today's plan + pre-game checks on the hidden stick
        from redzone.governor import Governor
        # Backs optional work off when the Mac is busy, hot, on battery or short of disk (redzone/governor.py).
        self.governor = Governor.from_config(self.cfg, **({"root": data_dir.parent} if data_dir else {}))
        from redzone.supervisor import DATA as SUPERVISOR_DATA, Supervisor
        # Per-switch TraceEvents (redzone/contracts/trace.py): data/traces.jsonl; in memory without a data dir.
        self.trace_sink = (trace.JsonlTrace(data_dir.parent / "traces.jsonl") if data_dir
                           else trace.MemoryTrace())
        # Watches the live system and fixes what it can on its own (redzone/supervisor.py).
        self.supervisor = Supervisor(self, root=data_dir.parent if data_dir else SUPERVISOR_DATA)
        self.control.target_names = self._target_names
        # Pause / "Watch this" / focus / lock survive a service restart (redzone/userstate.py).
        self._user_state_path = data_dir.parent / "user_state.json" if data_dir else None
        self._user_state_saved = userstate.capture(self.decider.user, self.hold)   # defaults: nothing to write
        self._restore_user_state()

    def _restore_user_state(self) -> None:
        if self._user_state_path is None:
            return
        try:
            state = userstate.load(self._user_state_path)
            if state is None:
                return
            self.hold = userstate.apply(state, self.decider.user, FocusMode)
            self._user_state_saved = userstate.capture(self.decider.user, self.hold)
            log.info("restored after restart: %s", self._user_state_saved)
        except Exception:
            log.exception("could not restore pause/hold/focus/lock")

    def _save_user_state(self) -> None:
        """Written when it changes (checked every decision tick, so within half a second of a tap)."""
        if self._user_state_path is None or self.recorded.active:
            return
        state = userstate.capture(self.decider.user, self.hold)
        if state != self._user_state_saved:
            userstate.save(self._user_state_path, state)
            self._user_state_saved = state

    # ------------------------------------------------------------------ loop

    async def run(self) -> None:
        """Three loops: data (adaptive 3-10s), decisions (every second), housekeeping."""
        await self.control.start()
        if self.cfg.get("screen.mic_calibration", False):
            self.set_mic(True)
        await asyncio.gather(self._poll_loop(), self._decide_loop(), self._house_loop(), self._links_loop(),
                             self._fantasy_loop(), self._fastcast_loop(), self.discovery.run(),
                             self._boundary_loop(), self._scorebug_loop(), self.tonight.run(),
                             self.supervisor.run(), self.network.run(), self.governor.run())

    def _delay_estimate(self, target: str) -> tuple[float, float]:
        d = self.delays.get(target)
        return d.mu, d.current_sigma(time.time())

    # ------------------------------------------------------------------ [auto sync] signals -> filter
    def _sync_measure(self, target: str, z: float, kind: str, t: float, sd: float | None = None) -> None:
        if self.sync is None:
            return
        try:
            self.sync.measure(target, z, kind, t=t, sd=sd)
        except Exception:
            log.exception("sync: could not use a %s sample on %s", kind, target)

    def _sync_position(self, target: str, wall: float, position_s: float, sd: float) -> None:
        if self.sync is not None:
            try:
                self.sync.position(target, position_s, t=wall, sd=sd)
            except Exception:
                log.exception("sync: could not use a position read on %s", target)

    def _sync_tune(self, target: str, wall: float) -> None:
        if self.sync is not None:
            try:
                self.sync.tuned(target, wall)
            except Exception:
                log.exception("sync: could not re-sync %s", target)

    def _sync_clock(self, target: str, wall: float, delay_s: float) -> None:
        self._sync_measure(target, delay_s, "clock", wall)

    def _boundary_sample(self, s) -> None:
        from redzone.decider.sync import SIGMA
        self._sync_measure(s.target, s.sample, "flip", s.flip_wall, max(SIGMA["flip"], s.error_s))
        d = self.delays.boundary(s.target, s.sample, s.flip_wall)
        try:
            self.store.calibration(s.target, s.sample, d.mu, d.sigma, kind="boundary")
        except Exception:
            log.exception("could not save a boundary delay sample")

    def _scorebug_sample(self, s) -> None:
        from redzone.decider.sync import SIGMA
        self._sync_measure(s.target, s.sample, "score", s.onset, max(SIGMA["score"], s.error_s))
        d = self.delays.screen(s.target, s.sample, s.onset)
        try:
            self.store.calibration(s.target, s.sample, d.mu, d.sigma, kind="screen")
        except Exception:
            log.exception("could not save a score-bug delay sample")

    def _game_on(self, target: str, visible: bool) -> str | None:
        """The game the engine thinks this target shows (the current one first, for the visible stick)."""
        cur = self.decider.current
        if visible and cur and getattr(self.where.get(cur), "target", None) == target:
            return cur
        hits = [gid for gid, w in self.where.items() if getattr(w, "target", None) == target]
        return hits[0] if len(hits) == 1 else None

    async def _scorebug_loop(self) -> None:
        """While a Prime game is on screen (and, with delay.scorebug_hidden, on the live hidden stick):
        a screenshot every ~2 s, the score bug read by local OCR, each score change timed against the
        feed (redzone/screen/scorebug.py). Never presses a key; stops at once for a switch or flip."""
        if not self.cfg.get("delay.scorebug", True):
            return
        from redzone.screen_ocr import recognize
        while True:
            t = time.time()
            wait = scorebug.EVERY_S
            try:
                frames = []
                if (self.cfg.get("mode.dry_run", True) or self._pending_decision is not None
                        or self.decider.replaying or self.decider.after_replay):
                    self.scorebug.reset()
                    wait = 5.0
                elif not self.control.scorebug_sticks(bool(self.cfg.get("delay.scorebug_hidden", False))):
                    self.scorebug.reset()
                    wait = 5.0
                else:
                    if not self.governor.allow("ocr"):   # the Mac is critical: no OCR for now
                        self.scorebug.reset()
                        await asyncio.sleep(10.0)
                        continue
                    wait = scorebug.EVERY_S * self.governor.scale("ocr")
                    frames = await self.control.scorebug_frames(bool(self.cfg.get("delay.scorebug_hidden", False)))
                    if not frames:
                        self.scorebug.reset()
                for f in frames:
                    gid = self._game_on(f["target"], f["visible"])
                    g = self.merger.games.get(gid or "")
                    tl = self.decider.timelines.get(gid or "")
                    bug = None
                    if g is not None and tl is not None:
                        bug = await asyncio.to_thread(scorebug.read_png, f["png"], g.home, g.away, recognize)
                    # The pipeline (switch/flip) may have moved while OCR ran: those frames don't count.
                    if self._pending_decision is not None or not any(
                            s is f["stick"] and tgt == f["target"] for s, tgt, _ in self.control.scorebug_sticks(
                                bool(self.cfg.get("delay.scorebug_hidden", False)))):
                        self.scorebug.reset()
                        break
                    self.scorebug.observe(f["stick"].host, f["target"], f["tune"], f["t0"], bug, tl)
            except Exception:
                log.exception("score bug read failed")
                self.scorebug.reset()
                wait = 10.0
            await asyncio.sleep(max(0.2, wait - (time.time() - t)))

    async def _boundary_loop(self) -> None:
        """Read the YouTube TV sticks every ~0.5 s for 3 minutes either side of each quarter hour (as it
        reaches the screen), so a show change times the stream delay to about a quarter second.
        Outside those windows the watcher still sees the controller's own reads."""
        if not self.cfg.get("delay.boundary", True):
            return
        while True:
            now = time.time()
            wait = boundary.READ_EVERY_S
            try:
                mu = max((d.mu for t, d in self.delays.book.items() if t.startswith("youtubetv:")), default=45.0)
                if self.cfg.get("mode.dry_run", True) or not boundary.in_window(now, min(mu, 60.0)):
                    wait = 10.0
                elif not await self.control.boundary_reads():
                    wait = 2.0
            except Exception:
                log.exception("boundary read failed")
                wait = 10.0
            await asyncio.sleep(max(0.1, wait - (time.time() - now)))

    async def _poll_loop(self) -> None:
        while True:
            t0 = time.time()
            try:
                await self.poll()
            except Exception:
                log.exception("poll failed")
                self.decider.note_error()
            self._reel_tick()
            live = any(g.phase == Phase.LIVE for g in self.merger.games.values())
            soon = any(g.phase == Phase.PRE and g.start_time and 0 < g.start_time - time.time() < 900
                       for g in self.merger.games.values())
            self._keep_awake(live)
            if live:
                interval = self.cfg.get("polling.hot_s", 3)
            else:
                interval = self.cfg.get("polling.live_s") if soon else self.cfg.get("polling.idle_s")
                if not soon:   # nothing live or about to start: idle polling may slow down...
                    slow = interval * self.governor.scale("idle_poll")
                    # ...but never past the moment the next known game enters its 15-minute "soon" window
                    lead = min((g.start_time - time.time() - 900 for g in self.merger.games.values()
                                if g.phase == Phase.PRE and g.start_time), default=None)
                    interval = slow if lead is None else max(interval, min(slow, lead))
            await asyncio.sleep(max(1.0, interval - (time.time() - t0)))

    def _reel_tick(self) -> None:
        """Feed new clips to the catch-up reel and rebuild it in the background. Never blocks the
        poll loop, never raises: a reel is a bonus, the live switching is the job."""
        try:
            if not self.reeler.enabled() or not self.governor.allow("reel"):
                return
            leagues = {gid: g.league.value for gid, g in self.merger.games.items()}
            mine = {lg.value: teams for lg, teams in self._my_teams().items()}
            self.reeler.ingest(self.summaries, leagues, mine)
            if self._reel_task is None or self._reel_task.done():
                self._reel_task = asyncio.create_task(self._reel_refresh())
        except Exception:
            log.exception("reel tick failed")

    async def _reel_refresh(self) -> None:
        try:
            await self.reeler.refresh(seen=self._reel_seen)
        except Exception:
            log.exception("reel refresh failed")

    def _reel_seen(self, c) -> bool:
        """You saw this play live: its game was on screen when the play reached the screen."""
        if c.play_at is None:
            return False
        target = self.decider.target_of(c.game_id) or ""
        mu = self.delays.get(target).mu if target else 45.0
        return self.decider._saw(c.game_id, c.play_at + mu - 3, c.play_at + mu + 6)

    _agent_cache: tuple[float, list] = (0.0, [])

    def agent_notes(self, limit: int = 5, max_age_s: float = 86400.0) -> list[dict]:
        """The watchdog's recent notes and actions (agent/watchdog.py writes data/agent.jsonl),
        re-read only when the file changes."""
        path = Path(getattr(self.cfg, "user_path", Path("data/config.yaml"))).parent / "agent.jsonl"
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return []
        if mtime != self._agent_cache[0]:
            rows = []
            try:
                with path.open("rb") as f:
                    f.seek(0, 2)
                    f.seek(max(0, f.tell() - 200_000))
                    for line in f.read().decode("utf-8", "replace").splitlines():
                        try:
                            r = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(r, dict) and r.get("action") not in (None, "none"):
                            rows.append({"ts": r.get("ts"), "action": r.get("action"), "why": r.get("why"),
                                         "result": r.get("result")})
            except OSError:
                return []
            self._agent_cache = (mtime, rows)
        now = time.time()
        return [r for r in self._agent_cache[1] if now - (r.get("ts") or 0) <= max_age_s][-limit:]

    def reel_view(self) -> dict | None:
        r = self.reeler.current
        if r is None:
            return None
        return {"seconds": r.seconds, "clips": r.clips, "built_at": r.built_at, "url": "/reel.mp4",
                "play": bool(self.cfg.get("reel.play", False))}

    async def _decide_loop(self) -> None:
        """Decide twice a second: gates open and close within seconds, so a 10s cadence would
        miss the moment a play ends on screen or a big play becomes catchable — and with a
        preloaded second stick the flip follows the decision at once, so every tick of lag is
        on screen (1s ticks = 0.5s late on average; 0.5s ticks = 0.25s)."""
        last_push = 0.0
        while True:
            try:
                self._mic_calibrate()
                changed = self.decide()
            except Exception:
                log.exception("decide failed")
                self.decider.note_error()
                changed = False
            try:
                self._save_user_state()
            except Exception:
                log.exception("could not save pause/hold/focus/lock")
            try:
                if changed or time.time() - last_push > 5:
                    last_push = time.time()
                    self.broadcast()
            except Exception:
                # A phone-view bug is not a switching error: it must not put the TV in safe mode.
                log.exception("broadcast failed")
            await self._next_tick(DECIDE_EVERY_S)

    async def _next_tick(self, seconds: float) -> None:
        """Sleep until the next decision, or less when pushed data just arrived: a big play
        from the push feed is acted on now, not up to a second later. Without the push feed
        nothing sets _fresh, so this is exactly the old 1s sleep."""
        try:
            await asyncio.wait_for(self._fresh.wait(), seconds)
            await asyncio.sleep(0.05)   # let a burst of pushed updates land together
        except asyncio.TimeoutError:
            pass
        self._fresh.clear()

    def reset_feeds(self, hard: bool = False) -> tuple[bool, str]:
        """Supervisor: live data stopped moving. Soft: forget every host's fetch back-off and poll
        now. Hard: also a fresh HTTP client (a wedged connection pool) and a push-feed restart."""
        self.fetcher._backoff.clear()
        if not hard:
            self._fresh.set()
            return True, "fetch back-off cleared"
        import httpx
        old = self.fetcher._client
        self._stop_fastcast()   # it holds the old client; the fastcast loop restarts it with the new one
        self.fetcher._client = httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=4.0), follow_redirects=True,
                                                 headers=dict(old.headers), http2=False)
        try:
            asyncio.get_running_loop().create_task(old.aclose())
        except RuntimeError:
            pass
        self._fresh.set()
        return True, "new HTTP client; push feed restarting"

    async def _links_loop(self) -> None:
        """Every 5 minutes: find exact Prime Video / ESPN+ event links for today's games."""
        from redzone.sources import eventlinks
        while True:
            try:
                await self.refresh_links(eventlinks)
            except Exception:
                log.exception("event links failed")
            await asyncio.sleep(300)

    async def refresh_links(self, eventlinks) -> int:
        today = datetime.now(ET).strftime("%Y-%m-%d")
        links = await eventlinks.fetch_event_links(self.fetcher._client, today)
        mine = set().union(*[set(v) for v in (self.cfg.get("teams") or {}).values()])
        n = 0
        for g in list(self.merger.games.values()):
            if g.phase == Phase.POST:
                continue
            names_a = {g.away.abbr, g.away.name}
            names_h = {g.home.abbr, g.home.name}
            found = eventlinks.match(links, g.league.value, names_a, names_h,
                                     espn_event_id=g.game_id, expected_start=g.start_time)
            if found:
                self.channels.apply_links(g, found, mine)
                self.discovery.remember(g, found)
                n += 1
        if n:
            self.channels.save()
        self.discovery.links_refreshed(n)
        return n

    async def _fantasy_loop(self) -> None:
        """ESPN fantasy lineup + live matchup score, every 5 minutes (only if a league is set)."""
        from redzone import fantasy
        while True:
            lid = str(self.cfg.get("fantasy.league_id", "") or "").strip()
            if lid:
                try:
                    tid = self.cfg.get("fantasy.team_id", 0) or None
                    self.fantasy = await fantasy.fetch(self.fetcher._client, lid,
                                                       int(self.cfg.get("fantasy.season", 2026)), tid)
                except Exception:
                    log.exception("fantasy failed")
            await asyncio.sleep(300)

    async def _house_loop(self) -> None:
        while True:
            await asyncio.sleep(10)
            try:
                await self._after_wake()
            except Exception:
                log.exception("wake check failed")
            try:
                await self._device_keepalive()
                self._tick += 1
                if self._tick % 360 == 0:
                    self.store.prune()
            except Exception:
                log.exception("housekeeping failed")

    async def _after_wake(self) -> list[str] | None:
        """hardening.mac: after a sleep or a network change, every connection re-proves itself now
        (redzone/platform/wake.py), before this tick's device keepalive. Off: nothing."""
        slept = self.wake.check()
        where = self.network.state.where
        prev, self._net_where = self._net_where, where
        moved = prev not in (None, "unknown") and where not in (prev, "unknown")
        if not self.cfg.get("hardening.mac", False) or not (slept or moved):
            return None
        from redzone.platform.wake import recover
        why = f"woke after {slept:.0f}s" if slept else f"network changed to {where}"
        if slept:
            await self.network.refresh(force=True)
            self._net_where = self.network.state.where
        return recover(self, why)

    async def tick(self) -> None:
        """One full cycle (used by tests and one-shot tools)."""
        self._tick += 1
        await self.poll()
        await self._device_keepalive()
        self.decide()
        self.broadcast()

    async def _device_keepalive(self):
        if self.recorded.active:
            return
        self._house_device_task = asyncio.create_task(self.control.keepalive())
        try:
            await self._house_device_task
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling() or not self.recorded.active:
                raise
        finally:
            self._house_device_task = None

    # ------------------------------------------------------------------ data

    def _leagues(self) -> list[League]:
        on = self.cfg.get("mode.leagues", {})
        return [lg for lg in League if on.get(lg.value, True)]

    async def poll(self) -> None:
        lgs = self._leagues()
        boards = await asyncio.gather(*(self._scoreboard(lg) for lg in lgs), return_exceptions=True)
        for lg, games in zip(lgs, boards):
            if isinstance(games, Exception) or games is None:
                self.merger.record_error(f"espn:{lg.value}")
                continue
            for g in games:
                self.merger.update("espn_scoreboard", g)
        live = [g for g in self.merger.games.values() if g.phase == Phase.LIVE and g.league in lgs]
        sem = asyncio.Semaphore(6)
        hot = self._hot_games(live)

        async def one(g: GameState):
            every = self.cfg.get("polling.hot_s", 3) if g.game_id in hot else self.cfg.get("polling.live_s", 10)
            async with sem:
                data = await self.fetcher.get_json(espn.summary_url(g.league, g.game_id), min_interval=every - 0.5)
            # Recorded after the fetch: a game that just turned hot keeps its slower rate until it has
            # been polled at the new one, so it isn't flagged stale (and unenterable) for that moment.
            self._poll_every[g.game_id] = float(every)
            if not data:
                self.merger.record_error("espn_summary")
                return
            if data is self.summaries.get(g.game_id):
                return  # cached copy, nothing new
            try:
                plays = espn.flatten_plays(g.league, data)
                s = espn.parse_summary(g.league, data, plays=plays)
                self.recorder.plays(g, plays, "espn_summary")
                if self._fastcast_mode == "shadow":
                    self.fc_check.saw(fastcast_check.POLLED, g.game_id, plays)
            except Exception:
                log.exception("summary parse failed %s", g.game_id)
                s = None
            if s and s.game_id:
                self.summaries[s.game_id] = data
                self.merger.update("espn_summary", s)
        await asyncio.gather(*(one(g) for g in live))
        await self._league_feeds(live)
        self._source_health()
        self.recorder.race(self.merger)
        await self._archive_finished()
        for g in self.merger.games.values():
            # [mid-play guard] Also the polls after a game we were following goes Final, so the decider
            # knows it ENDED (and stays for the last play to air) instead of seeing it vanish.
            if g.phase == Phase.LIVE or (g.phase == Phase.POST and g.game_id in self.decider.timelines):
                self.decider.timeline(g.game_id, g.league).observe(g)

    async def _archive_finished(self) -> None:
        """A game we watched just ended: save its final play-by-play for future backtests."""
        for gid in [gid for gid in self.summaries
                    if self.merger.games.get(gid) and self.merger.games[gid].phase == Phase.POST]:
            g = self.merger.games[gid]
            data = await self.fetcher.get_json(espn.summary_url(g.league, gid), min_interval=0)
            if data:
                try:
                    self.recorder.archive(g, data)
                except OSError:
                    log.exception("archive failed")
            self.summaries.pop(gid, None)

    def _garbage_focus(self) -> None:
        """Focused game turned into a blowout: roam instead (even for your team), and come back
        automatically if it gets close again. A game you LOCKED stays locked — that was explicit."""
        u = self.decider.user
        if self._suspended_focus:
            gid, mode = self._suspended_focus
            if u.focus_game != gid or u.focus_mode != FocusMode.AUTOPILOT:
                self._suspended_focus = None  # you changed focus yourself meanwhile
                return
            g, s = self.merger.games.get(gid), self.scored.get(gid)
            if g is None or g.phase != Phase.LIVE:
                self._suspended_focus = None
            elif s and not s.garbage:
                u.focus_mode = mode
                self._suspended_focus = None
                self.toast(f"It's close again — back to focusing on {self._name(gid)}")
            return
        gid = u.focus_game
        s = self.scored.get(gid or "")
        if gid and u.focus_mode != FocusMode.AUTOPILOT and s and s.garbage:
            self._suspended_focus = (gid, u.focus_mode)
            u.focus_mode = FocusMode.AUTOPILOT
            self.toast(f"{self._name(gid)} is garbage time — roaming until it's close again")

    def _hot_games(self, live: list[GameState]) -> set[str]:
        """Games worth polling every few seconds: what's on, what's next, anything tense or personal."""
        dec = self.decider
        hot = {dec.current, dec.home_break_visit, dec.user.focus_game} - {None}
        ranked = sorted((self.scored[g.game_id] for g in live if g.game_id in self.scored),
                        key=lambda s: -s.score)
        hot |= {s.game_id for s in ranked[:4]}
        hot |= {s.game_id for s in ranked if s.lock or s.pre_moment or s.personal > 0}
        return hot

    async def _scoreboard(self, lg: League) -> list[GameState] | None:
        if lg.college:
            return await self._college_scoreboard(lg)
        data = await self.fetcher.get_json(espn.scoreboard_url(lg), min_interval=8)
        if not data:
            return None
        return espn.parse_scoreboard(lg, data)

    async def _college_scoreboard(self, lg: League) -> list[GameState] | None:
        """Ranked/featured board + your conference's board, filtered to your teams and ranked games
        (never all of Division I: every kept live game costs a summary poll)."""
        urls = espn.scoreboard_urls(lg, (self.cfg.get("college.groups") or {}).get(lg.value) or [])
        boards = await asyncio.gather(*(self.fetcher.get_json(u, min_interval=8) for u in urls),
                                      return_exceptions=True)
        if not any(isinstance(b, dict) and b for b in boards):
            return None
        mine = self._my_teams().get(lg, set())
        top_n = int(self.cfg.get("college.top_n", espn.TOP_N))
        out: dict[str, GameState] = {}
        for b in boards:
            if isinstance(b, dict):
                for g in espn.parse_scoreboard(lg, b):
                    if espn.keep_college(g, mine, top_n):
                        out.setdefault(g.game_id, g)
        return list(out.values())

    async def _league_feeds(self, live: list[GameState]) -> None:
        today = datetime.now(ET).strftime("%Y-%m-%d")
        nhl = [g for g in live if g.league == League.NHL]
        if nhl:
            sc = await self.fetcher.get_json(leagues.NHL_SCORE.format(date=today), min_interval=8)
            if sc:
                idx = leagues.nhl_index(sc)
                for g in nhl:
                    gm = leagues.nhl_match(g, idx)
                    if not gm:
                        continue
                    pbp = await self.fetcher.get_json(leagues.NHL_PBP.format(id=gm.get("id")), min_interval=8)
                    try:
                        self.merger.update("nhl", leagues.enrich_nhl(self.merger.games[g.game_id], gm, pbp))
                    except Exception:
                        log.exception("nhl enrich")
        nba = [g for g in live if g.league == League.NBA]
        if nba:
            sb = await self.fetcher.get_json(leagues.NBA_SCOREBOARD, min_interval=5)
            if sb:
                idx = leagues.nba_index(sb)
                for g in nba:
                    gm = leagues.nba_match(g, idx)
                    if gm:
                        try:
                            self.merger.update("nba", leagues.enrich_nba(self.merger.games[g.game_id], gm))
                        except Exception:
                            log.exception("nba enrich")

    def _feed_urls(self, g: GameState) -> list[str]:
        """The URLs a game's state comes from: its summary and its league's scoreboard(s)."""
        if g.league.college:
            boards = espn.scoreboard_urls(g.league, (self.cfg.get("college.groups") or {}).get(g.league.value) or [])
        else:
            boards = [espn.scoreboard_url(g.league)]
        return [espn.summary_url(g.league, g.game_id), *boards]

    def _feed_cadence(self, gid: str) -> float:
        return self._poll_every.get(gid, float(self.cfg.get("polling.live_s", 10)))

    def _source_health(self) -> None:
        now = time.time()
        for name, h in self.merger.health.items():
            self.source_status[name] = {"name": name, "ok": (now - h.last_ok) < 120 and not h.benched,
                                        "age_s": round(now - h.last_ok) if h.last_ok else None,
                                        "errors": h.errors, "benched": h.benched}

    # ------------------------------------------------------------------ push feed

    async def _fastcast_loop(self) -> None:
        """ESPN's push feed (sources.fastcast), next to polling, never instead of it. The setting
        is re-read every few seconds, so turning it on or off needs no restart."""
        while True:
            try:
                self.sync_fastcast()
            except Exception:
                log.exception("fastcast sync failed")
            await asyncio.sleep(FASTCAST_SYNC_S)

    def sync_fastcast(self) -> None:
        """Match the setting; subscribe to every live game, drop games that went final. With FastCast
        off, the live check (sources.fastcast_check) runs it in shadow on a few live games instead."""
        mode = "merge" if self.cfg.get("sources.fastcast", False) else (
            "shadow" if self.cfg.get("sources.fastcast_check", True) and not self.fc_check.done else None)
        if mode != self._fastcast_mode:
            self._stop_fastcast()
        if mode is None:
            return
        if self.fastcast is None:
            cb = self._on_fastcast if mode == "merge" else self._on_fastcast_shadow
            self.fastcast = fastcast.FastcastClient(cb, http=self.fetcher._client)
            self._fastcast_task = asyncio.get_running_loop().create_task(self.fastcast.run())
            self._fastcast_mode = mode
        lgs = self._leagues()
        live = [g for g in self.merger.games.values() if g.phase == Phase.LIVE and g.league in lgs]
        if mode == "shadow":
            # a few games, keeping the ones already subscribed (their pairing is under way)
            have = {gid for _, gid in self._fastcast_games.values()}
            live.sort(key=lambda g: (g.game_id not in have, g.game_id))
            live = live[:fastcast_check.MAX_TOPICS]
        self._fastcast_games = {
            fastcast.game_topic(g.league, g.game_id, self.summaries.get(g.game_id)): (g.league, g.game_id)
            for g in live}
        self.fastcast.set_topics(self._fastcast_games)
        if mode == "shadow" and time.time() - self._fc_graded >= 60:
            self._grade_fastcast()

    def _grade_fastcast(self) -> None:
        self._fc_graded = time.time()
        v = self.fc_check.tick()
        if v["result"] == "pass" and self.cfg.get("sources.fastcast_auto_enable", False):
            log.warning("fastcast live check passed: turning FastCast on (sources.fastcast_auto_enable)")
            self.cfg.update({"sources": {"fastcast": True}})

    def _on_fastcast_shadow(self, topic: str, doc, received_at: float, published_at: float | None) -> None:
        """[fastcast live check] Parsed and logged, never merged: nothing on screen can change."""
        game = self._fastcast_games.get(topic)
        if game is None:
            return
        league, gid = game
        try:
            g, plays = fastcast.to_game_state(league, doc, expect_id=gid)
        except Exception:
            self.fc_check.unusable()
            raise
        if not fastcast.usable(g):
            self.fc_check.unusable()
            return
        self.recorder.plays(g, plays, "espn_fastcast")
        self.fc_check.saw(fastcast_check.PUSHED, gid, plays, received_at)

    def _stop_fastcast(self) -> None:
        if self.fastcast is not None:
            self.fastcast.stop()
            self.fastcast = None
        if self._fastcast_task is not None:
            self._fastcast_task.cancel()
            self._fastcast_task = None
        self._fastcast_games = {}
        self._fastcast_mode = None

    def _on_fastcast(self, topic: str, doc, received_at: float, published_at: float | None) -> None:
        """A pushed game update takes the same path as a polled summary (merge, flight recorder,
        timeline). If it moved the game (new play, score, stoppage), the decision loop wakes now.
        Errors propagate to the client, which logs them; the polled sources are untouched."""
        game = self._fastcast_games.get(topic)
        if game is None:
            return   # unsubscribed a moment ago
        league, gid = game
        try:
            g, plays = fastcast.to_game_state(league, doc, expect_id=gid)
        except Exception:
            self.merger.record_error("espn_fastcast")
            raise
        if not fastcast.usable(g):   # wrong game, or a shape we can't fully read: polling covers it
            self.merger.record_error("espn_fastcast")
            return
        self.recorder.plays(g, plays, "espn_fastcast")
        before = _game_fingerprint(self.merger.games.get(gid))
        merged = self.merger.update("espn_fastcast", g)
        if merged.phase == Phase.LIVE or (merged.phase == Phase.POST and gid in self.decider.timelines):
            self.decider.timeline(gid, league).observe(merged)
        if _game_fingerprint(merged) != before:
            self._fresh.set()   # most pushes are odds/stat tweaks every 10s: those don't wake it

    # ------------------------------------------------------------------ decide

    def _my_teams(self) -> dict[League, set[str]]:
        return {League(k): set(v) for k, v in (self.cfg.get("teams") or {}).items() if k in League._value2member_map_}

    def personal(self) -> Personal:
        teams = self._my_teams()
        sweat, labels = {}, {}
        for b in self.store.bets():
            g = self.merger.games.get(b["game"])
            if not g:
                continue
            s = betlib.sweat(g, b)
            if s >= sweat.get(g.game_id, 0):
                sweat[g.game_id] = s
                labels[g.game_id] = betlib.label(g, b)
        fan = {}
        if self.fantasy is not None and not self.fantasy.error:
            from redzone import fantasy
            for g in self.merger.games.values():
                b = fantasy.boosts(self.fantasy, g, self.cfg.get("score.personal.fantasy_mine"),
                                   self.cfg.get("score.personal.fantasy_opponent"))
                if b:
                    fan[g.game_id] = b
        return Personal(my_teams=teams, bet_sweat=sweat, bet_labels=labels, fantasy=fan, boosted=set(self.boosted))

    def decide(self) -> bool:
        if self.recorded.active:
            return False
        now = time.time()
        for gid, until in list(self.skipped.items()):
            if now >= until:
                self.skipped.pop(gid)
                self.decider.user.muted.discard(gid)
        for tgt, until in list(self._fail_cool.items()):
            if now >= until:
                self._fail_cool.pop(tgt)
        self._switch_deadline(now)
        if (self._pending_decision is None and not self.control.input_unknown
                and self.decider.auto_resume(self.cfg.get("decider.safe_auto_resume_s", 600))):
            log.warning("safe mode: auto-resumed after %ss with no Resume tap",
                        self.cfg.get("decider.safe_auto_resume_s", 600))
            self.toast("Safe mode ended on its own; auto-switching again", "info")
        me = self.personal()
        cands: dict[str, Candidate] = {}
        dry = self.cfg.get("mode.dry_run", True)
        st = self.cfg.get("sunday_ticket", True)
        for gid, g in self.merger.games.items():
            if g.league not in self._leagues():
                continue
            try:
                sc = self.scorer.score(g, me)
            except Exception:
                log.exception("score failed %s", gid)
                continue
            self.scored[gid] = sc
            w = self.supervisor.route(gid, self.discovery.choose(g, self.channels.where(g, sunday_ticket=st)))
            self.where[gid] = w
            if g.phase != Phase.LIVE:
                continue
            # In dry run, "likely" targets count so you can see what it would do.
            target = w.target if (w.tunable or dry) else None
            if target in self._fail_cool:
                target = None   # failed a moment ago: don't hammer it every tick
            # A dark feed (sources/feeds.py, flagged within 5 s) blocks ENTERING a game, never leaving
            # the one on screen: for that one only the merger's old 60 s no-progress rule applies, so
            # an ESPN blip can't empty the watchable set and send you to a catch-up reel.
            stale = self.merger.is_stale(gid) if gid == self.decider.current else self.feeds.stale(gid)
            cands[gid] = Candidate(sc, target, stale=stale)
        self._reasoner_narrate()
        self._garbage_focus()
        self._hold_check()
        if self._pending_decision is not None:
            return False  # score/feed updates continue; don't race a tune in flight
        if not dry and self.control.input_unknown and getattr(self.control, "live_control", False):
            # Input recovery owns the TV (Controller._maybe_reassert, the supervisor): every switch
            # would be refused at once ("TV input is unknown"). Submitting anyway flooded the
            # switches table, ~240 rows in 2 minutes on the e2e night test. Decide again once it's known.
            return False
        before = self.decider.current
        redzone = self._redzone_target(st, dry)
        d = self.decider.decide(cands, redzone, commit=dry, reel=self._reel_target())
        self.last_decision = d
        changed = d.reason != self._last_logged_reason or d.action != "stay"
        if changed:
            self.store.decision(d)
            self._last_logged_reason = d.reason
        if d.action in ("switch", "replay") and d.target:
            self._act(d, before, cands)
        else:
            self._preload_standby(cands, redzone)
        self._sync_switch_costs()
        if time.time() - self._last_snapshot > 60:
            self._last_snapshot = time.time()
            self.store.snapshot([(time.time(), gid, s.league.value, s.score, s.moment, s.reason,
                                  "now" if gid == self.decider.current else "")
                                 for gid, s in self.scored.items() if s.raw > 0])
        return changed

    def _reasoner_narrate(self) -> None:
        """Ask the local model for each hot game's storyline (background, event-driven, rate-limited)
        and keep the model server loaded while games are live. Never raises, never blocks a poll."""
        r = getattr(self.scorer, "reasoner", None)
        if r is None or not r.on() or not self.cfg.get("reasoner.model", True):
            return
        try:
            if r.client is None:
                from redzone.reasoner.reasoner import Client
                r.client = Client(str(self.cfg.get("reasoner.server_url", "http://127.0.0.1:8779")))
            from redzone.reasoner import brief as rbrief
            cur = self.decider.current
            cur_g = self.merger.games.get(cur or "")
            live = [g for g in self.merger.games.values() if g.phase == Phase.LIVE]
            for g in live:
                rr = r.cached(g.game_id)
                if rr is None:
                    continue
                f = rr.facts
                ctx = rbrief.Context(mine=bool(f.get("mine")), garbage=bool(f.get("garbage")),
                                     crunch=bool(f.get("crunch")), late=bool(f.get("late")),
                                     on_screen=g.game_id == cur,
                                     screen_game=cur_g.matchup if cur_g is not None and g.game_id != cur else "")
                r.narrate(g, rr, ctx)
            now = time.time()
            if live and now - getattr(self, "_llm_keepalive_at", 0.0) >= 60:
                self._llm_keepalive_at = now
                r.keepalive(len(live))
        except Exception:  # noqa: BLE001 - the phone text is never worth a failed poll
            log.exception("reasoner: narration step failed")

    def _reel_target(self) -> tuple[str, float] | None:
        """(tuner target, seconds) of the ready catch-up reel, when reels may go on the TV."""
        r = self.reeler.current
        if r is None or not self.cfg.get("reel.play", False) or not r.path.exists():
            return None
        return f"reel:{int(r.built_at)}", r.seconds

    def _redzone_target(self, sunday_ticket: bool, dry: bool) -> str | None:
        """NFL RedZone's tuner target while it's on the air (and you've pasted its link)."""
        if not sunday_ticket or not redzone_on(self.merger.games.values()):
            return None
        return REDZONE_TARGET if dry or self.channels.yttv_code(REDZONE_TARGET) else None

    def _act(self, d, before, cands) -> None:
        if d.target == REEL and self.reeler.current is not None:
            # Its plays count as seen from here; the next reel is built from what's left.
            self.reeler.played(self.reeler.current)
        # A catch-up replay can be of a game that just ended (no longer a live candidate).
        target = cands[d.target].target if d.target in cands else self.decider.target_of(d.target)
        back = None
        if d.action == "replay" and d.replay:
            delay = self.delays.get(target or "unknown")
            back = d.replay.back_s(time.time(), delay.mu, PLAY_LEN_S.get(self.merger.games[d.target].league, 8))
        cmd_ts = time.time()
        event_ts = d.replay.t if d.replay else None
        deferred = not self.cfg.get("mode.dry_run", True)
        replay_exit = max(self.decider.replay_until, self.decider.replay_exit_after) \
            if (self.decider.replaying or self.decider.after_replay) else None
        self._pending_decision = d
        self._pending_since = time.time()
        self._pending_mono = time.monotonic()
        before_target = getattr(self.control, "on_target", None) or getattr(self.where.get(before or ""), "target", None)
        if deferred:
            # Conservatively mark the transition unknown until observed; never
            # mark a big play seen because a command merely was submitted.
            self.decider._screen_log.append((cmd_ts, None))
            if self.decider.replaying:
                self.decider.abort_replay()

        def done(out):
            if self._pending_decision is not d:
                return
            self._pending_decision = None
            self._pending_done = None
            cancelled = out.detail.startswith("switch cancelled")
            if deferred:
                if out.ok and not out.dry_run:
                    self.decider.confirm(d, cands)
                    if d.kind == "user":
                        self.decider.user.manual_until = time.time() + self.cfg.get("decider.manual_pause_s")
                else:
                    self.decider.reject(d)
                    # Playback can be unknown after a failed launch. Hold for
                    # recovery instead of making new decisions from a fiction.
                    if out.screen_unchanged:
                        # The proposal expired without a visible command. The
                        # old stream remains the current game; keep planning.
                        self.decider._screen_log.append((time.time(), "replay" if replay_exit is not None else before))
                    # A visible failure counts as an error below (3 in 5 minutes -> safe mode),
                    # not straight to safe mode: one app hiccup must not end the night.
            if (not out.ok and not out.dry_run and d.action == "switch" and d.kind != "user" and target
                    and not out.detail.startswith(NOT_THE_TARGETS_FAULT)):
                self._fail_cool[target] = time.time() + self.cfg.get("decider.fail_cooldown_s", 90)
            detail = {"took": out.took, "detail": out.detail, "path": out.path, "waited": round(out.waited, 1)}
            try:
                detail["trace"] = trace.build(d, before, target, out, cmd_ts, delays=self.delays,
                                              before_target=before_target, labels=self._trace_labels(before, d.target),
                                              from_replay=replay_exit is not None)
                trace.emit(self.trace_sink, detail["trace"])
            except Exception:   # a trace must never cost the switch its row
                log.exception("switch trace failed")
            self.store.switch(ts=cmd_ts, frm=before, to_game=d.target, target=target, kind=d.kind, reason=d.reason,
                              event_ts=event_ts, cmd_ts=cmd_ts, confirmed_ts=out.confirmed_at, ok=out.ok,
                              dry_run=out.dry_run, detail=detail)
            if not out.ok and not out.dry_run:
                log.warning("switch to %s failed after %.1fs (%s): %s", d.target, out.took, out.path, out.detail[:300])
            if not cancelled:   # a cancel (pause, your tap, a newer command) says nothing about the target
                self.discovery.on_outcome(out, d.target, replay=d.action == "replay")
            # 'TV input is unknown' is a refusal by the input-recovery state, which has its own
            # watchdog: counting it too put the TV in safe mode at 21:07 on Nov 2.
            unknown_input = out.detail.startswith("TV input is unknown")
            if not out.ok and not out.dry_run and not out.screen_unchanged and not cancelled and not unknown_input:
                self.decider.note_error()
                self.toast(f"Couldn't switch: {out.detail[:80]}", "error")
            self.broadcast()
        self._pending_done = done
        self.control.submit(target, back, done, may_cut=lambda: (
                            not self.decider.user.paused and not self.cfg.get("mode.dry_run", True)
                            and ((d.kind == "user" and (not self._hold_waits(d) or self.safe_to_leave(before, required_s=8.0)))
                                 or (d.kind != "user" and self._entry_valid(d, target)
                                 and (time.time() >= replay_exit if replay_exit is not None
                                      else self.safe_to_leave(before, required_s=8.0))))),
                            replay_id=d.replay.pid if d.replay else None,
                            entry_rewind_s=getattr(d, "rewind_s", 0.0) if d.action == "switch" else 0.0)

    def pending_age(self, now: float) -> float:
        """Seconds the pending switch has been running, as of wall-clock `now`. If the wall clock
        jumped since the switch started (NTP fix, laptop waking), the jump is taken back out using
        time.monotonic(), so a healthy 1 s-old switch is never declared hung (fault drill: clock_skew)."""
        if not self._pending_since:
            return 0.0
        age = now - self._pending_since
        if self._pending_mono:
            jump = (time.time() - self._pending_since) - (time.monotonic() - self._pending_mono)
            if abs(jump) > CLOCK_JUMP_S:
                age -= jump
        return max(0.0, age)

    def _trace_labels(self, *gids: str | None) -> dict:
        out = {}
        for gid in gids:
            g = self.merger.games.get(gid or "")
            if g is not None:
                try:
                    out[gid] = g.matchup
                except Exception:
                    pass
        return out

    def _switch_deadline(self, now: float) -> None:
        """A switch that never reports back (a hung tuner path) would block every decision,
        preload and recovery: give up on it after control.max_switch_s."""
        d, done = self._pending_decision, self._pending_done
        if d is None or done is None:
            return
        limit = self.cfg.get("control.max_switch_s", 150)
        if d.kind == "start":   # nothing confirmed on screen yet: a hung first pick is retried sooner
            limit = min(limit, self.cfg.get("control.max_start_switch_s", 75))
        age = self.pending_age(now)
        if age < limit:
            return
        log.warning("switch to %s still running after %.0fs: giving up on it", d.target, age)
        self.control.cancel_pending()
        done(Outcome(False, None, age,
                     f"switch timed out after {limit:.0f}s; playback not confirmed", False))

    def _entry_valid(self, d: Decision, target: str | None) -> bool:
        """Refresh the destination just before any visible mutation, not just at planning time."""
        if d.action == "replay":
            return bool(d.replay and not d.replay.replayed)
        if d.target == REDZONE:
            return redzone_on(self.merger.games.values())
        if d.target == REEL:
            return bool(target and target.startswith("reel:")) and (REEL_DIR / "reel.mp4").exists()
        g = self.merger.games.get(d.target or "")
        if not g or g.phase != Phase.LIVE or self.feeds.stale(g.game_id):
            return False
        if getattr(self.where.get(g.game_id), "target", None) != target:
            return False
        tl = self.decider.timelines.get(g.game_id)
        if tl is None:
            return False
        now = time.time()
        delay = self.delays.get(target or "unknown")
        remaining = self.control.deadline_cost(target)
        if d.kind == "chase" and catchable(tl, delay, now, remaining) is None:
            return False
        cands1 = {d.target: Candidate(self.scored[d.target], target)} if d.target in self.scored else {}
        if d.action == "switch" and d.kind != "user" and self.decider._cut_timed(d.target, cands1):
            # [cut timing] the picture must still come up on a dead ball, with the planned rewind
            # (only where cut timing is on: decider.entry.leagues)
            plan = self.decider.entry_plan(d.target, cands1, now, remaining,
                                           urgent=d.kind == "crunch")
            if plan is not None and (not plan.ok or plan.rewind_s != getattr(d, "rewind_s", 0.0)):
                return False
            if plan is not None and plan.rewind_s:
                return True   # landing before the snap on purpose: the break's tail is the line-up
        return not break_on_arrival(tl, delay, now, now + remaining)

    def _start_st_scan(self) -> tuple[bool, str]:
        if self.recorded.active:
            return False, "Stop the recorded session before scanning live channels"
        if self._st_scan_task and not self._st_scan_task.done():
            return False, "Sunday Ticket scan is already running"
        codes = self.channels.st_codes
        if not codes:
            return False, "Paste your Sunday Ticket channel links in Settings → Channels first"
        if not self.control.live_control:
            return False, "Turn on TV control first (the scan uses the stick)"
        games = [g for g in self.merger.games.values() if g.league == League.NFL and g.phase != Phase.POST]

        def allowed():
            return not self.recorded.active and not self.cfg.get("mode.dry_run", True)

        def found(g, code):
            if not allowed():
                return
            self.channels.st_slots[g.game_id] = code
            self.channels.save()
            self.toast(f"Sunday Ticket: {g.matchup} found")

        async def run():
            if not allowed():
                return
            got = await self.control.scan_sunday_ticket(codes, games, found, may_run=allowed)
            if allowed():
                self.toast(f"Sunday Ticket scan done: matched {len(got)} games")
        self._st_scan_task = asyncio.create_task(run())
        where = "on the hidden stick" if self.control.two_stick else "— your TV will flip channels for ~2 minutes"
        return True, f"Scanning {len(codes)} Sunday Ticket channels {where}"

    def set_mic(self, on: bool) -> tuple[bool, str]:
        from redzone.screen.audio import MicWatcher
        if on and self.mic is None:
            m = MicWatcher()
            if not m.start():
                return False, f"Couldn't use the microphone: {m.error}"
            self.mic = m
            return True, "Listening for possible delay hints; confirm timing with a known play (nothing is recorded)"
        if not on and self.mic is not None:
            self.mic.stop()
            self.mic = None
        self.mic_suggestion = None
        return True, "Microphone off"

    def _mic_calibrate(self) -> None:
        """Every few seconds: match recent big plays in the game on screen to loudness surges."""
        from redzone.screen.audio import match_delay
        now = time.time()
        if (self.mic is None or now - self._last_mic < 5 or self.decider.replaying
                or self._pending_decision is not None or self.cfg.get("mode.dry_run", True)):
            return
        self._last_mic = now
        gid = self.decider.current
        tl = self.decider.timelines.get(gid or "")
        target = getattr(self.where.get(gid or ""), "target", None)
        if not tl or not target:
            return
        d = self.delays.get(target)
        for e in tl.events:
            key = (gid, e.seen)
            if e.kind != "play" or e.spread > 0 or not e.big or key in self._mic_used or not (now - 150 < e.seen < now - 25):
                continue
            # Only plays we were actually watching when they aired.
            if self.decider.on_screen_at(e.seen + d.mu) != gid:
                continue
            sample = match_delay(e.seen, self.mic.surges(e.seen), expected=d.mu)
            if sample is not None and now > e.seen + sample + 3:
                self._mic_used.add(key)
                # A volume surge may be an ad, commentary, or another sound.
                # It cannot identify a play, so it never changes trusted timing.
                self.mic_suggestion = {"target": target, "delay_s": round(sample, 1),
                                       "play_id": e.pid, "label": e.label, "at": now,
                                       "verified": False}

    def _target_names(self, target: str) -> list[str]:
        """Team names/abbreviations of the game(s) routed to this target (for reading a score bug)."""
        out = []
        for gid, w in self.where.items():
            g = self.merger.games.get(gid)
            if g is not None and getattr(w, "target", None) == target:
                for t in (g.home, g.away):
                    out += [t.abbr, t.name, (t.name or "").split()[-1] if t.name else ""]
        return [x for x in out if x]

    def safe_to_leave(self, gid: str | None, required_s: float = 0.0) -> bool:
        """Re-checked right before a two-stick input flip: is a play on screen in the game we're leaving?"""
        if not gid or gid == REDZONE:
            return True
        if self.decider.replaying or self.decider.after_replay:
            return time.time() >= max(self.decider.replay_until, self.decider.replay_exit_after)
        if gid not in self.decider.timelines:
            return False  # no screen timing evidence is not an open cutting window
        if self.decider._ending_still_airing(gid, time.time()) > 0:
            return False
        g = self.merger.games.get(gid)
        if g is not None and g.phase == Phase.POST:
            return True   # its last play has aired (checked above): there's nothing left to cut
        target = getattr(self.where.get(gid), "target", None) or "unknown"
        gate = leave_window(self.decider.timelines[gid], self.delays.get(target), time.time(),
                            self.cfg.get("decider.uncalibrated_sigma_s"), required_s)
        return gate.safe

    def _preload_standby(self, cands: dict, redzone: str | None = None) -> None:
        """Two sticks: keep the hidden one on what the next switch most likely needs, so that switch
        is a 1-2s input flip instead of a 5-10s tune. What and when: redzone/standby.py."""
        if not self.control.two_stick or self.decider.user.paused:
            return
        pick = self.planner.plan(self._standby_options(cands, redzone), self.control.standby)
        if pick:
            self.control.preload(pick.target, back_s=pick.back_s, replay_id=pick.replay_id)

    def _standby_options(self, cands: dict, redzone: str | None = None) -> list[standby.Option]:
        dec, u = self.decider, self.decider.user
        if redzone and self.cfg.get("decider.redzone_channel", False):
            # NFL RedZone is where Sunday breaks go and home base when nothing's close: rank it the
            # way the decider does (decider.redzone_score), so those switches can be flips too.
            rz = float(self.cfg.get("decider.redzone_score", 45))
            cands = {**cands, REDZONE: Candidate(Scored(
                game_id=REDZONE, league=League.NFL, score=rz, raw=rz, moment=rz, stakes=1.0, personal=0.0,
                reason="NFL RedZone", tags=[], is_break=False, lock=None, pre_moment=None, closeness=1.0), redzone)}
        home = dec.home_break_visit
        if home is None and u.focus_game and u.focus_mode != FocusMode.AUTOPILOT:
            home = u.focus_game   # away from your focus game: it's where we go back to
        if home == dec.current:
            home = None
        for g in self.merger.games.values():
            self.breaks.observe(g)
        # Your game's return outranks a replay, and a replay already rolling needs nothing cued.
        brk, why, replay = (None, "", None) if (home or dec.replaying) else self._break_standby(cands)
        sure = replay is not None
        if replay is None and not (home or dec.replaying):
            replay = self._replay_standby(cands)
        return standby.options(cands, current=dec.current, home=home, back_to=dec.stack[-1] if dec.stack else None,
                               muted=u.muted, games=self.merger.games, my_teams=self._my_teams(), replay=replay,
                               tunable=self.control.can_tune, save_s=self.control.tune_estimate,
                               return_bonus=float(self.cfg.get("decider.switch_margin", 10)),
                               want=getattr(self.last_decision, "want", None), brk=brk, brk_why=why,
                               replay_sure=sure)

    def _break_brain(self):
        """(brain, blend weight) for the break forecast, or (None, 0) with the brain off."""
        if not self.cfg.get("brain.enabled", False):
            return None, 0.0, False
        return (self.scorer.brain, float(self.cfg.get("brain.break_weight", 0.5)),
                self.cfg.get("brain.v3_lengths", ["nfl", "nba"]))

    def _break_standby(self, cands: dict) -> tuple[str | None, str, standby.Option | None]:
        """The game on screen is about to break (redzone/scoring/breaks.py): where the decider will
        spend it, asked the way it will ask (its own destination and catch-up rules), so the hidden
        stick already plays it. (dest, why, replay option) — all None when no break is coming."""
        dec = self.decider
        cur = dec.current
        if not cur or cur == REDZONE or dec.after_replay:
            return None, "", None
        f = self.breaks.forecast(self.merger.games.get(cur))
        if f.p < float(self.cfg.get("two_stick.break_p", 0.5)) or not f.visit:
            return None, "", None
        u = dec.user
        live = {gid: x for gid, x in cands.items()
                if x.scored.raw > 0 or x.scored.moment > 0 or x.scored.is_break or gid == REDZONE}
        watchable = {gid: x for gid, x in live.items() if x.target and not x.stale and gid not in u.muted}
        try:
            if f.expect_s >= 600:
                # Halftime / intermission: the decider visits the best live game (_long_break_visit).
                pool = {gid: x for gid, x in watchable.items()
                        if gid != cur and not x.scored.is_break and gid not in dec._screen_break}
                dest = max(pool, key=lambda gid: pool[gid].scored.score, default=None)
            else:
                dest = dec._break_destination(watchable, exclude=cur)
            m = None
            if f.expect_s >= 600 and self.cfg.get("replay.enabled", True):
                back = dec._deadline_cost(dec._target_of.get(cur)) + 6
                m = dec._best_replay(cands, time.time(), 800.0 - back, allow_seen=dest is None,
                                     top=dec._vs_replay(dest, cands) if dest else -1.0, margin=0.0)
        except Exception:
            log.exception("break standby")
            return None, "", None
        rp = self._replay_option(m, cands) if m is not None else None
        return dest, f.why, rp

    def _replay_standby(self, cands: dict) -> standby.Option | None:
        """(b) A catch-up is queued and the game on screen is about to break: cue it on the hidden stick."""
        dec = self.decider
        cur = dec.current
        if not self.cfg.get("replay.enabled", True) or not dec.queue or not cur:
            return None
        now = time.time()
        m = standby.likely_replay(dec.queue, now, float(self.cfg.get("replay.max_age_s", 1800)))
        return self._replay_option(m, cands) if m is not None else None

    def _replay_option(self, m: Missed, cands: dict) -> standby.Option | None:
        """A replay cued on the hidden stick: its channel, rewound to the play and held there."""
        dec, now = self.decider, time.time()
        # A replay can be of a game that just ended (no longer a live candidate).
        target = cands[m.game_id].target if m.game_id in cands else dec.target_of(m.game_id)
        if not target or target.split(":", 1)[0] not in self.cfg.get("replay.apps", ["youtubetv"]):
            return None
        g = self.merger.games.get(m.game_id)
        back = m.back_s(now, self.delays.get(target).mu, PLAY_LEN_S.get(g.league, 8.0) if g else 8.0)
        return standby.Option(m.game_id, target, standby.REPLAY, m.value, f"replay ready: {m.label[:60]}",
                              back_s=back, replay_id=m.pid)

    def _sync_switch_costs(self) -> None:
        """Hand measured switch times to the decider, which plans with decider.switch_cost.<app>
        (dead air, how early to head home, which plays are still catchable live).

        Only through validated settings, only on a real change (>= 0.5s and 10%), at most every
        switch_speed.sync_every_s — or at once when two-stick mode turns on or off (a flip is ~1-2s,
        a tune 5-10s: the right number changes completely)."""
        now = time.time()
        mode = self.control.two_stick
        if mode == self._cost_mode and now - self._cost_synced < float(self.cfg.get("switch_speed.sync_every_s", 300)):
            return
        self._cost_mode, self._cost_synced = mode, now
        self.control.save_latency()
        if not self.cfg.get("switch_speed.learn", True):
            return
        known = set((getattr(self.cfg, "defaults", {}).get("decider") or {}).get("switch_cost") or {})
        cur = self.cfg.get("decider.switch_cost") or {}
        changes = {app: v for app, v in self.control.switch_costs().items() if app in known and _moved(cur.get(app), v)}
        if not changes:
            return
        try:
            # Learned, not your edit: kept out of the undo history.
            self.cfg.update({"decider": {"switch_cost": changes}}, record=False)
            log.info("switch costs now %s (%s)", changes, "two sticks" if mode else "one stick")
        except ConfigError as e:
            log.warning("couldn't save learned switch costs: %s", e)

    # ------------------------------------------------------------------ user actions

    def action(self, action: str, game_id: str | None = None, value=None) -> tuple[bool, str]:
        if self.recorded.active and action not in {"pause", "dry_run", "label", "vote"}:
            return False, "Stop the recorded session before using live controls"
        if self.recorded.active and action == "dry_run" and not value:
            return False, "Recorded device control is managed by the recorded session"
        dec, u = self.decider, self.decider.user
        gid = game_id or dec.current
        if action in ("hold", "watch"):
            return self._hold(gid, force=value == "force")
        if action == "release":
            return self._release_hold("Back on autopilot")
        if action == "hold_auto":
            if not self.hold:
                return False, "Nothing is held"
            self.hold.update(auto_resume=bool(value), resume_at=None, why="")
            return True, ("I'll hand back to autopilot when it's over or a blowout" if value
                          else f"Staying on {self._name(self.hold['game_id'])} until you tap Resume auto")
        if action == "redzone":
            target = self._redzone_target(self.cfg.get("sunday_ticket", True), self.cfg.get("mode.dry_run", True))
            if not target:
                return False, "NFL RedZone isn't on the air (or its channel isn't set)"
            dec._target_of[REDZONE] = target
            return self._hold(REDZONE, force=value == "force")
        if action == "two_stick":
            self.cfg.update({"two_stick": {"enabled": bool(value)}})
            return True, "Two-stick mode on" if value else "Two-stick mode off: one stick does everything"
        if action == "focus":
            if not gid:
                return False, "Pick a game to focus on"
            u.focus_game = gid
            u.focus_mode = FocusMode(value or "focus")
            if dec.current != gid:
                self._act_manual(gid)
            return True, f"Focused on {self._name(gid)}"
        if action == "autopilot":
            u.focus_game, u.focus_mode = None, FocusMode.AUTOPILOT
            return True, "Autopilot on"
        if action in ("boost", "unboost"):
            (self.boosted.add if action == "boost" else self.boosted.discard)(gid)
            return True, f"{'Boosted' if action == 'boost' else 'Unboosted'} {self._name(gid)}"
        if action in ("mute", "unmute"):
            (u.muted.add if action == "mute" else u.muted.discard)(gid)
            return True, f"{'Muted' if action == 'mute' else 'Unmuted'} {self._name(gid)}"
        if action == "lock":
            u.locked_game = gid
            return True, f"Staying on {self._name(gid)}"
        if action == "unlock":
            u.locked_game = None
            self.hold = None
            return True, "Unlocked"
        if action == "pause":
            u.paused = True
            dec.abort_replay()
            self.control.cancel_pending()
            if self._st_scan_task and not self._st_scan_task.done():
                self._st_scan_task.cancel()
            return True, "Auto-switching paused"
        if action == "resume":
            dec.resume()
            return True, "Back on autopilot"
        if action == "skip":
            if not dec.current:
                return False, "Nothing to skip"
            self.skipped[dec.current] = time.time() + 300
            if self.hold and self.hold["game_id"] == dec.current:
                self.hold, u.locked_game = None, None
            u.muted.add(dec.current)
            dec.user.manual_until = 0
            return True, f"Skipping {self._name(dec.current)} for 5 minutes"
        if action == "back":
            if not dec.stack:
                return False, "Nowhere to go back to"
            prev = dec.stack[-1]
            self._act_manual(prev)
            return True, f"Back to {self._name(prev)}"
        if action == "watch":
            if not gid:
                return False, "Pick a game"
            self._act_manual(gid)
            return True, f"Switching to {self._name(gid)}"
        if action == "saw_it":
            if game_id and game_id != dec.current:
                return False, "Only the game on your TV can be calibrated"
            return self._calibrate(str(value) if value else None)
        if action == "vote":
            self.store.feedback(dec.current, int(value or 0), context={"reason": getattr(self.last_decision, "reason", "")})
            return True, "Thanks — logged for training"
        if action == "dry_run":
            was_dry = self.cfg.get("mode.dry_run", True)
            self.cfg.update({"mode": {"dry_run": bool(value)}})
            if value:
                dec.abort_replay()
                self.control.cancel_pending()
                if self._st_scan_task and not self._st_scan_task.done():
                    self._st_scan_task.cancel()
                if self.recorded.active:
                    self.recorded.cancel_navigation()
            elif was_dry:
                # A simulated current game is not evidence of a physical stream.
                dec.abort_replay()
                dec.current = None
                dec._screen_log.clear()
                dec._arrival_until = 0.0
                dec.after_replay = False
                dec.replay_exit_after = 0.0
                self.control.on_target = None
            return True, "Dry run on: the TV won't be touched" if value else "TV control ON"
        if action == "replay":
            m = next((m for m in dec.queue if m.pid == str(value)), None)
            if not m:
                return False, "That replay isn't available anymore"
            self._act_manual(m.game_id, replay=m)
            return True, f"Replaying: {m.label[:60]}"
        if action == "scan_st":
            return self._start_st_scan()
        if action == "mic":
            ok, msg = self.set_mic(bool(value))
            if ok:
                self.cfg.update({"screen": {"mic_calibration": bool(value) and self.mic is not None}})
            return ok, msg
        if action == "label":
            self.store.label(str(value), dec.current, {"decision": getattr(self.last_decision, "reason", "")})
            return True, f"Labeled: {value}"
        return False, f"Unknown action {action}"

    def _hold(self, gid: str | None, force: bool = False) -> tuple[bool, str]:
        """"Watch this": go there at the next safe moment (never mid-play) and stay until
        "Resume auto". Tapping it again while it waits cuts over right away."""
        if not gid:
            return False, "Pick a game"
        dec, u = self.decider, self.decider.user
        g = self.merger.games.get(gid)
        if gid != REDZONE and (g is None or g.phase != Phase.LIVE):
            return False, f"{self._name(gid)} isn't live"
        name = self._name(gid)
        p = self._pending_decision
        waiting = bool(self.hold and self.hold["game_id"] == gid and p is not None and p.target == gid)
        prev = self.hold if self.hold and self.hold["game_id"] == gid else None
        self.hold = {"game_id": gid, "since": prev["since"] if prev else time.time(),
                     "auto_resume": prev["auto_resume"] if prev else True,
                     "resume_at": None, "why": "", "force": force or waiting}
        u.locked_game = gid
        u.paused = False   # holding IS the pause: autopilot won't switch away
        if waiting:
            return True, f"Switching to {name} now"
        if dec.current == gid:
            return True, f"Holding on {name} — tap Resume auto to hand back"
        self._act_manual(gid)
        if force or self.cfg.get("mode.dry_run", True):
            return True, f"On {name} — holding here"
        return True, f"Switching to {name} at the next safe moment (tap again to go now)"

    def _hold_waits(self, d: Decision) -> bool:
        """A switch to the held game honors the never-cut-mid-play rule unless you forced it."""
        h = self.hold
        return bool(h and d.action == "switch" and d.target == h["game_id"] and not h.get("force"))

    def _release_hold(self, msg: str) -> tuple[bool, str]:
        dec, u = self.decider, self.decider.user
        h, self.hold = self.hold, None
        u.locked_game = None
        u.focus_game, u.focus_mode = None, FocusMode.AUTOPILOT
        self._suspended_focus = None
        u.manual_until = 0.0
        dec.resume()
        p = self._pending_decision
        if h and p is not None and p.kind == "user" and p.target == h["game_id"]:
            self.control.cancel_pending()   # still waiting for a safe moment: don't go after all
        return True, msg

    def _hold_check(self) -> None:
        """A held game ended or turned into a blowout: count down, then autopilot takes over."""
        h, u = self.hold, self.decider.user
        if not h:
            return
        gid = h["game_id"]
        if u.locked_game != gid:
            self.hold = None   # unlocked or re-locked elsewhere
            return
        if self.decider.current == gid:
            h["force"] = False
        if gid == REDZONE:
            over, garbage = not redzone_on(self.merger.games.values()), False
        else:
            g, s = self.merger.games.get(gid), self.scored.get(gid)
            # Before the first poll after a restart no game is known yet: a restored hold isn't "over".
            over = (g is None and bool(self.merger.games)) or (g is not None and g.phase == Phase.POST)
            garbage = bool(s and s.garbage)
        why = "is over" if over else "is garbage time" if garbage else ""
        if not (h["auto_resume"] and why):
            if h["resume_at"] and not why:
                self.toast(f"{self._name(gid)} is close again — staying")
            h.update(resume_at=None, why="")
            return
        now = time.time()
        if h["resume_at"] is None:
            wait = 5.0 if over else HOLD_RESUME_S
            h.update(resume_at=now + wait, why=why)
            self.toast(f"{self._name(gid)} {why} — autopilot takes over in {wait:.0f}s")
        elif now >= h["resume_at"]:
            self._release_hold("")
            self.toast(f"{self._name(gid)} {why} — autopilot is back")

    def _hold_view(self) -> dict | None:
        h = self.hold
        if not h:
            return None
        p = self._pending_decision
        return {"game_id": h["game_id"], "matchup": self._name(h["game_id"]), "since": h["since"],
                "auto_resume": h["auto_resume"], "resume_at": h["resume_at"], "why": h["why"],
                "force": bool(h.get("force")), "on": self.decider.current == h["game_id"],
                "waiting": bool(p is not None and p.target == h["game_id"] and self._hold_waits(p))}

    def _act_manual(self, gid: str, replay: Missed | None = None) -> None:
        w = self.where.get(gid)
        target = getattr(w, "target", None)
        before = self.decider.current
        cands = {gid: Candidate(self.scored[gid], target)} if gid in self.scored else {}
        d = Decision(time.time(), "replay" if replay else "switch", gid,
                     "you selected this replay" if replay else "you selected this game", State.PAUSED,
                     kind="user", replay=replay)
        if self.cfg.get("mode.dry_run", True):
            self.decider.confirm(d, cands)
            self.decider.user.manual_until = time.time() + self.cfg.get("decider.manual_pause_s")
        self._act(d, before, cands)

    def _calibrate(self, play_id: str | None = None) -> tuple[bool, str]:
        """You tapped "I just saw it": match the tap to the play that just aired on your screen."""
        dec = self.decider
        if (self.cfg.get("mode.dry_run", True) or self._pending_decision is not None
                or dec.replaying or dec.after_replay or dec.safe_mode):
            return False, "Calibrate only while watching a confirmed live stream, outside a switch or replay"
        gid = dec.current
        tl = dec.timelines.get(gid or "")
        w = self.where.get(gid or "")
        target = getattr(w, "target", None)
        if not tl or not target:
            return False, "Nothing on to calibrate against"
        tap = time.time() - 0.7  # typical reaction time
        d = self.delays.get(target)
        plays = [e for e in tl.events if e.kind == "play" and e.spread == 0
                 and tap - 150 < e.seen < tap - 3 and (not play_id or e.pid == play_id)]
        if not plays:
            return False, "No recent play to match — tap right as a play ends"
        e = min(plays, key=lambda e: abs(e.seen + d.mu - tap))
        if not play_id:
            ranked = sorted(plays, key=lambda p: abs(p.seen + d.mu - tap))
            # Once a tap has measured this stream, the nearest play wins unless a second one is
            # practically as close (free throws a few seconds apart no longer bounce the tap).
            margin = 1.5 if d.samples else 3
            if (abs(e.seen + d.mu - tap) > max(10, 2 * d.sigma)
                    or (len(ranked) > 1 and abs(ranked[1].seen + d.mu - tap)
                        - abs(e.seen + d.mu - tap) < margin)):
                return False, "Several plays could match that tap; choose the exact play before calibrating"
        sample = tap - e.seen
        self._sync_measure(target, sample, "tap", time.time())
        d = self.delays.calibrate(target, sample)
        self.store.calibration(target, sample, d.mu, d.sigma)
        return True, f"Matched {e.label[:60]}; delay {d.mu:.0f}s (±{2*d.sigma:.0f}) for {describe(target)}"

    def _name(self, gid: str | None) -> str:
        if gid == REDZONE:
            return "NFL RedZone"
        g = self.merger.games.get(gid or "")
        return g.matchup if g else "that game"

    # ------------------------------------------------------------------ phone

    def game_item(self, gid: str) -> dict | None:
        g = self.merger.games.get(gid)
        if g is None:
            return None
        dec = self.decider
        s = self.scored.get(gid)
        w = self.where.get(gid)
        live = g.phase == Phase.LIVE
        return {
            "id": gid, "league": g.league.value, "league_logo": views.league_logo(g.league.value),
            "matchup": g.matchup, "phase": g.phase.value,
            "status_text": g.status_text or _status(g), "home": views.team(g.home), "away": views.team(g.away),
            "period": g.period, "clock": views.clock_text(g) if live else "",
            "score": s.display if s else 0, "moment": round(s.moment) if s else 0,
            "stakes": round(s.stakes, 2) if s else 1.0, "personal": round(s.personal) if s else 0,
            "reason": s.reason if s else "", "tags": s.tags if s else [],
            "is_break": bool(s and s.is_break and live),
            "lock": s.lock if s and live else None, "pre_moment": s.pre_moment if s and live else None,
            "where": {"label": getattr(w, "label", "unknown"), "confidence": getattr(w, "confidence", "unknown"),
                      "tunable": getattr(w, "tunable", False), "target": getattr(w, "target", None)},
            "odds": g.odds_text, "mine": g.involves(set((self.cfg.get("teams") or {}).get(g.league.value, []))),
            "boosted": gid in self.boosted, "muted": gid in dec.user.muted, "focus": gid == dec.user.focus_game,
            "stale": self.feeds.stale(gid), "start_time": g.start_time, "on_now": gid == dec.current,
            "wp": g.home_win_prob, "situation": views.situation(g), "leaders": g.leaders,
            "spotlight": g.spotlight, "venue": g.venue_info,
            "brain": (s.brain or None) if s and live else None,
            "reasoner": (s.reasoner or None) if s and live else None,
        }

    async def game_detail(self, gid: str) -> dict | None:
        g = self.merger.games.get(gid)
        if g is None:
            return None
        summary = self.summaries.get(gid)
        if summary is None:
            summary = await self.fetcher.get_json(espn.summary_url(g.league, gid), min_interval=60)
        out = views.game_detail(g, summary, self.game_item(gid))
        # The momentum graph (ESPN-style game flow): the brain's hotness per play, live games only.
        out["momentum"] = self.scorer.brain.timeline(gid) if self.cfg.get("brain.enabled", False) else []
        return out

    async def teams(self) -> dict:
        return await views.teams(self.fetcher, self.cfg.get("teams") or {})

    async def team_detail(self, league: str, team_id: str) -> dict:
        lg = League(league)
        gid = next((x.game_id for x in self.merger.games.values()
                    if x.league == lg and team_id in (x.home.id, x.away.id)), None)
        return await views.team_detail(self.fetcher, lg, team_id, self.cfg.get("teams") or {},
                                       self.game_item(gid) if gid else None)

    def snapshot(self) -> dict:
        dec = self.decider
        d = self.last_decision
        games = [self.game_item(gid) for gid, g in self.merger.games.items() if g.league in self._leagues()]
        order = {"live": 0, "pre": 1, "unknown": 2, "post": 3}
        games.sort(key=lambda x: (order.get(x["phase"], 2), -x["score"], x["start_time"] or 0))
        cur = self.merger.games.get(dec.current or "")
        now = None
        if cur:
            s = self.scored.get(cur.game_id)
            now = {**self.game_item(cur.game_id), "game_id": cur.game_id,
                   "where": getattr(self.where.get(cur.game_id), "label", ""),
                   "reason": (d.reason if d and d.action != "stay" else (s.reason if s else "")) or "",
                   "since": dec.since, "kind": "replay" if dec.replaying else "live",
                   "replay": {"label": dec.replaying.label, "game_id": dec.replaying.game_id,
                              "play_id": dec.replaying.pid} if dec.replaying else None}
        if dec.current == REDZONE:
            now = {"game_id": REDZONE, "id": REDZONE, "matchup": "NFL RedZone", "league": "nfl",
                   "league_logo": views.LEAGUE_LOGO.format("nfl"), "where": describe(REDZONE_TARGET),
                   "reason": d.reason if d else "", "since": dec.since, "score": 0, "kind": "live",
                   "replay": None, "home": None, "away": None, "status_text": "Every score, no commercials"}
        nxt = None
        if d and d.action == "stay" and getattr(d, "want", None):
            wg = self.merger.games.get(d.want)
            reason = d.reason.split(" — waiting")[0]
            reason = reason[5:] if reason.startswith("want ") else reason
            nxt = {"game_id": d.want, "matchup": wg.matchup if wg else self._name(d.want),
                   "reason": reason, "why_waiting": d.gate,
                   "home": views.team(wg.home) if wg else None, "away": views.team(wg.away) if wg else None}
        return {
            "ts": time.time(),
            "mode": {"dry_run": self.cfg.get("mode.dry_run", True), "paused": dec.user.paused,
                     "safe": dec.safe_mode, "frozen_until": dec.frozen_until or None,
                     "focus_game": dec.user.focus_game, "focus_mode": dec.user.focus_mode.value,
                     "locked_game": dec.user.locked_game, "state": d.state.value if d else "idle",
                     "decision": f"{d.action}: {d.reason}"[:200] if d else "",
                     "hold": self._hold_view(), "two_stick_enabled": bool(self.cfg.get("two_stick.enabled", True)),
                     "redzone_on": bool(self._redzone_target(self.cfg.get("sunday_ticket", True),
                                                             self.cfg.get("mode.dry_run", True))),
                     "tv_control": self.control.live_control, "two_stick": self.control.two_stick,
                     "control_note": self.control.note, "mic": self.mic is not None, "announcer_hint": ""},
            "now": now, "next": nxt, "standby": self._standby_view(), "games": games,
            "reel": self.reel_view(), "agent": self.agent_notes(),
            "tonight": self.tonight.view(),
            "screen_evidence": self.screen_evidence(),
            "queue": [self._queue_item(m) for m in sorted(dec.queue, key=lambda m: -m.value)[:10]],
            "log": self._log(),
            "server_time": time.time(),
            "recorded_playback": self.recorded.snapshot(),
            "delays": [{"target": t, "label": describe(t), "mu": round(x.mu, 1), "sigma": round(x.sigma, 1),
                        "calibrated": x.calibrated_at is not None, "source": x.source,
                        "settled": self.delays.settled(self.delays.app(t)),
                        "boundary_samples": self.delays.boundary_count(self.delays.app(t)),
                        "live": x.live is not None} for t, x in self.delays.book.items()],
            "sync": self.sync.status() if self.sync is not None else [],   # [auto sync] estimate +- error per channel
            "delay_watch": self.boundary.status(),
            "scorebug_watch": self.scorebug.status(),
            "health": {"sources": list(self.source_status.values()), "sticks": self._sticks_view(),
                       "network": self.network.view(),
                       "tv": {"state": "unknown"}, "race": self.merger.race_summary(),
                       "adb": self.control.adb_ok, "switch_times": self.control.switch_times(),
                       "fastcast": self.fastcast.status() if self.fastcast else None,
                       "fastcast_check": self.fc_check.verdict,
                       "discovery": self.discovery.status(), "supervisor": self.supervisor.status(),
                       "governor": self.governor.view()},
            "bets": self.bets_view(), "feedback": self.store.feedback_counts(),
            "fantasy": None if self.fantasy is None else {
                "me": self.fantasy.me, "opponent": self.fantasy.opponent, "my_points": self.fantasy.my_points,
                "opp_points": self.fantasy.opp_points, "error": self.fantasy.error,
                "starters": [{"name": x.name, "pos": x.pos, "team": x.team, "mine": x.mine}
                             for x in self.fantasy.starters]},
        }

    def _sticks_view(self) -> list[dict]:
        """Stick rows for the phone. Away from home a stick that can't answer is "away", not "offline"
        (the watchdog only reconnects "offline" ones)."""
        rows = self.control.status()
        if self.network.away:
            for r in rows:
                if r.get("state") != "connected":
                    r["state"] = "away"
        return rows

    def screen_evidence(self) -> dict:
        if self.recorded.active:
            return {"status": "unknown", "mode": "recorded", "game_id": None,
                    "identity": "unverified", "media_state": "unknown", "safe_to_leave": False,
                    "reason": "Recorded content: live-feed timing and delay calibration do not apply",
                    "delay_s": None, "delay_margin_s": None, "calibrated": False,
                    "switch": dict(self.control.stage), "mic_suggestion": None}
        dec, ctl = self.decider, self.control
        gid = dec.current
        target = getattr(self.where.get(gid or ""), "target", None) or "unknown"
        now = time.time()
        delay = self.delays.get(target) if target != "unknown" else Delay(0, self.cfg.get("delay.sigma_default"))
        evidence = screen_evidence(dec.timelines.get(gid or ""), delay, now,
                                   self.cfg.get("decider.uncalibrated_sigma_s"))
        if target == "unknown":
            evidence.update(delay_s=None, delay_margin_s=None, calibrated=False)
        dry = bool(self.cfg.get("mode.dry_run", True))
        if not dry and (self._pending_decision is not None or ctl.input_unknown or dec.safe_mode
                        or dec.on_screen_at(now) != gid or dec.replaying or dec.after_replay):
            evidence.update(status="unknown", safe_to_leave=False,
                            reason="replay or display transition: live-feed timing does not describe this picture")
        stage = dict(ctl.stage)
        if self._pending_decision is None and stage["stage"] not in ("confirmed", "failed"):
            stage = {"stage": "idle", "target": None, "reason": ""}
        evidence.update(mode="simulation" if dry else "live", game_id=gid,
                        identity="unverified", media_state="unknown", switch=stage,
                        mic_suggestion=self.mic_suggestion)
        return evidence

    def _queue_item(self, m) -> dict:
        """A catch-up replay card: who made the play (headshot) and for which team."""
        item = {"game_id": m.game_id, "matchup": self._name(m.game_id), "play_id": m.pid,
                "label": m.label, "at": m.t, "kind": m.kind, "team": None, "headshot": None, "player": None}
        g, summary = self.merger.games.get(m.game_id), self.summaries.get(m.game_id)
        if g is None or summary is None:
            return item
        try:
            p = next((x for x in espn.flatten_plays(g.league, summary) if str(x.get("id")) == m.pid), None)
            if p:
                a = espn.play_athlete(g.league, p, espn.athletes(g.league, summary))
                if a:
                    item.update(headshot=a["headshot"], player=a["name"], team=a.get("team"))
        except Exception:
            log.debug("queue item enrich failed", exc_info=True)
        return item

    def _standby_view(self) -> dict | None:
        c = self.control
        t = c.hidden_target
        if not (c.two_stick and t):
            return None
        gid = next((g for g, w in self.where.items() if getattr(w, "target", None) == t), None)
        parked = c.parked_id
        pick = next((o for o in self.planner.last if o.target == t and o.replay_id == parked), None)
        return {"game_id": pick.game_id if pick else gid, "matchup": self._name(pick.game_id if pick else gid),
                "where": describe(t), "ready": c.hidden_ready,
                "kind": pick.kind if pick else ("replay" if parked else "back"),
                "why": pick.why if pick else "the game you just left, still live",
                "flip_s": round(c.flip_estimate(), 1)}

    def _log(self) -> list[dict]:
        out = []
        for r in self.store.recent_switches(25):
            took = json.loads(r.get("detail") or "{}").get("took") if isinstance(r.get("detail"), str) else None
            out.append({"ts": r["ts"], "kind": r["kind"], "to": r["to_game"], "matchup": self._name(r["to_game"]),
                        "reason": r["reason"], "dry_run": bool(r["dry_run"]), "ok": bool(r["ok"]),
                        "took": took, "logo": _logo_of(self.merger.games.get(r["to_game"] or ""))})
        return out

    def bets_view(self) -> list[dict]:
        out = []
        for b in self.store.bets():
            g = self.merger.games.get(b["game"])
            out.append({"id": b["id"], "game_id": b["game"], "matchup": g.matchup if g else "game not found",
                        "kind": b["kind"], "team": b["team"], "line": b["line"], "side": b["side"],
                        "note": b["note"], "label": betlib.label(g, b) if g else "",
                        "sweat": round(betlib.sweat(g, b), 2) if g else 0.0})
        return out

    def channels_view(self) -> dict:
        from redzone.channels import PRETTY
        return {"channels": [{"key": k, "label": PRETTY.get(k, k), "code": self.channels.codes.get(k)}
                             for k in SETUP_CHANNELS],
                "overrides": self.channels.overrides}

    # ------------------------------------------------------------------ push

    def broadcast(self) -> None:
        if not self.subscribers:
            return
        payload = json.dumps(self.snapshot(), default=str)
        for q in list(self.subscribers):
            if q.qsize() > 5:
                continue  # slow phone: skip, it'll get the next one
            q.put_nowait(("state", payload))

    def toast(self, text: str, level: str = "info") -> None:
        for q in list(self.subscribers):
            q.put_nowait(("toast", json.dumps({"text": text, "level": level})))

    # ------------------------------------------------------------------ mac

    def _keep_awake(self, live: bool) -> None:
        """caffeinate only while games are live, so the Mac sleeps normally otherwise."""
        running = self._caffeinate is not None and self._caffeinate.poll() is None
        if live and not running:
            try:
                self._caffeinate = subprocess.Popen(["caffeinate", "-i", "-s"])
            except OSError:
                self._caffeinate = None
        elif not live and running:
            self._caffeinate.terminate()
            self._caffeinate = None

    async def close(self) -> None:
        if self._st_scan_task and not self._st_scan_task.done():
            self._st_scan_task.cancel()
            await asyncio.gather(self._st_scan_task, return_exceptions=True)
        await self.recorded.close()
        self._keep_awake(False)
        task = self._fastcast_task
        self._stop_fastcast()
        if task:
            await asyncio.gather(task, return_exceptions=True)
        await self.discovery.close()
        self.control.save_latency()
        await self.control.close()
        await self.fetcher.close()


def _team(t) -> dict:
    rec = f"{t.wins}-{t.losses}" + (f"-{t.ties}" if t.ties else "") if t.wins is not None and t.losses is not None else ""
    return {"abbr": t.abbr, "name": t.name, "score": t.score, "logo": t.logo, "color": t.color, "record": rec}


def _moved(old, new: float) -> bool:
    """Worth rewriting a switch cost? Not for jitter: at least half a second and 10%."""
    if not isinstance(old, (int, float)) or isinstance(old, bool):
        return True
    return abs(new - old) >= max(0.5, 0.1 * abs(old))


def _logo_of(g: GameState | None) -> str:
    return g.home.logo if g else ""


def _status(g: GameState) -> str:
    if g.phase == Phase.PRE and g.start_time:
        return datetime.fromtimestamp(g.start_time, ET).strftime("%-I:%M %p")
    return g.phase.value


def _game_fingerprint(g: GameState | None) -> tuple | None:
    """What the decider reacts to. A copy of the values: the Merger edits games in place."""
    if g is None:
        return None
    return (g.last_play.id if g.last_play else None, g.home.score, g.away.score, g.stoppage, g.phase, g.period)
