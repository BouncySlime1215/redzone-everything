"""Run a whole replayed night through the real system, on a virtual clock. See __init__.py.

Only the outside world is fake: the sticks and the TV (house.py), the score feed (NightFeed:
archived ESPN play-by-play delivered as it happened), the watchdog's view of the web API, and
launchctl. Everything that decides and acts is the production code, unmodified: Engine (all of its
loops), Decider, Scorer, Controller (two-stick flips, preloads, resume ladder, input recovery),
Tuners, standby Planner, Supervisor, Tonight's plan, discovery, and agent/watchdog.py.

Instance-level stand-ins on the Engine (no class is patched): poll (the replayed feed instead of
ESPN), refresh_links (no Prime/ESPN+ link lookups offline), _keep_awake (no caffeinate).
"""
from __future__ import annotations

import asyncio
import functools
import os
import json
import logging
import random
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from redzone.lab.night import faults as faultlib
from redzone.lab.night import house as houselib
from redzone.lab.night.vclock import VirtualClock

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parents[3]
FIX = ROOT / "tests" / "fixtures"
FEED_LAG_S = 8.0
DELAY_S = {"youtubetv": 45.0, "prime": 40.0}     # how far each app's picture runs behind real life
PLAY_LEN_S = {"nfl": 8.0, "cfb": 8.0, "nba": 6.0, "nhl": 6.0}
HOSTS = ("10.0.0.11:5555", "10.0.0.12:5555")
log = logging.getLogger("redzone.night")


# ---- the night: five archived games, moved onto one Sunday evening ----------------------------

@dataclass
class GameSpec:
    league: str
    fixture: str
    target: str            # where it's on
    kick_et: str | None    # move the game to start here (None: it already ran on this night)
    channel: str = ""      # what YouTube TV's media session calls the channel


NIGHT_DAY = "2026-11-01"
GAMES = [
    GameSpec("nhl", "espn_nhl_summary_401892626", "youtubetv:espn", None, "ESPN"),
    GameSpec("nfl", "espn_nfl_summary_401772894", "youtubetv:fox", "16:25", "FOX"),
    GameSpec("cfb", "espn_cfb_summary_401858282", "youtubetv:abc", "19:45", "ABC"),
    GameSpec("nfl", "espn_nfl_summary_401772931", "youtubetv:nbc", "20:20", "NBC"),
    GameSpec("nba", "espn_nba_summary_401909906", "prime:amzn1.dv.gti.night-tor-lal", None),
]
YTTV_CODES = {"espn": "ESPNnight01", "fox": "FOXnight001", "abc": "ABCnight001", "nbc": "NBCnight001"}

_ISO = re.compile(r'"(20\d\d-\d\d-\d\dT\d\d:\d\d(?::\d\d(?:\.\d+)?)?)(Z|[+-]\d\d:?\d\d)?"')


def _shift_text(text: str, delta: float) -> str:
    def one(m):
        s, tz = m.group(1), m.group(2) or ""
        fmt = "%Y-%m-%dT%H:%M:%S.%f" if "." in s else ("%Y-%m-%dT%H:%M:%S" if s.count(":") == 2 else "%Y-%m-%dT%H:%M")
        t = datetime.strptime(s, fmt) + timedelta(seconds=delta)
        out = t.strftime(fmt)
        if "." in s:
            out = out[:len(s)]
        return f'"{out}{tz}"'
    return _ISO.sub(one, text)


def load_games(work: Path):
    """The replays (redzone.lab.backtest.Replay), each moved to tonight, and their targets."""
    from redzone.lab.backtest import Replay
    from redzone.models import League
    out = []
    for g in GAMES:
        text = (FIX / f"{g.fixture}.json").read_text()
        if g.kick_et:
            date = json.loads(text)["header"]["competitions"][0]["date"]
            start = datetime.strptime(date.replace("Z", ""), "%Y-%m-%dT%H:%M").replace(tzinfo=ZoneInfo("UTC"))
            want = datetime.strptime(f"{NIGHT_DAY} {g.kick_et}", "%Y-%m-%d %H:%M").replace(tzinfo=ET)
            text = _shift_text(text, (want - start).total_seconds())
        p = work / "games" / g.league / f"summary_{g.fixture.split('_')[-1]}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        r = Replay.load(League(g.league), p)
        out.append((g, r))
    return out


# ---- the replayed score feed ---------------------------------------------------------------

class NightFeed:
    """The archived play-by-play, delivered FEED_LAG_S after each play happened (what the
    backtest and the replay demo use), through the engine's merger like a real poll."""

    def __init__(self, night: "Night"):
        from redzone.lab.backtest import _feed
        self.night = night
        self.outage = False
        self.games = night.games
        self.feeds = {r.gid: _feed(r, random.Random(r.gid), FEED_LAG_S, 3.0, None, 0.0, 0.0, 0.0,
                                   night.t0, night.t1) for _, r in self.games}
        self.idx = {r.gid: -1 for _, r in self.games}
        self.calibrated: dict[str, float] = {}
        self.polls = 0
        for _, r in self.games:
            pre = r.state_at(-1)
            if pre is not None:
                pre.home.score = pre.away.score = 0     # the header carries the final

    async def poll(self) -> None:
        from redzone.lab.backtest import _deliver
        from redzone.models import Phase
        e = self.night.engine
        self.polls += 1
        await asyncio.sleep(0.2)                          # an HTTP round trip
        if self.outage:
            for _, r in self.games:
                e.merger.record_error("espn_summary")
            e._source_health()
            return
        now = time.time()
        for spec, r in self.games:
            f = self.feeds[r.gid]
            f.fresh_at = now
            i = self.idx[r.gid]
            while i + 1 < len(r.plays) and f.arrive[i + 1] <= now:
                i += 1
            self.idx[r.gid] = i
            if i < 0:
                g = r.state_at(-1)
                if g is None or (g.start_time and g.start_time - now > 6 * 3600):
                    continue
                g.updated_at = now
            else:
                g = _deliver(r, f, i, now)
                if g is None:
                    continue
            got = e.merger.update("espn_summary", g)
            if got is not g:
                # The archive is the whole truth: a "regression" (a clock reset after a review)
                # must not bench the only source there is (same rule as the replay demo).
                g.updated_at, g.sources = now, {"espn_summary": now}
                e.merger.games[g.game_id] = g
                e.merger._per_source["espn_summary"][g.game_id] = g
                h = e.merger.health["espn_summary"]
                h.benched, h.regressions = False, 0
            if g.phase == Phase.LIVE:
                self._calibrate(spec.target, now)
            if g.phase == Phase.LIVE or (g.phase == Phase.POST and g.game_id in e.decider.timelines):
                e.decider.timeline(g.game_id, g.league).observe(g)
        e._source_health()

    def _calibrate(self, target: str, now: float) -> None:
        """You tapped "I just saw it" on each channel early on and every 30 min (same assumption
        as the backtest and the demo). The sample is the channel's true delay."""
        last = self.calibrated.get(target)
        if last is not None and now - last < 1800:
            return
        self.calibrated[target] = now
        self.night.engine.delays.calibrate(target, DELAY_S[target.split(":")[0]])


# ---- one night -----------------------------------------------------------------------------

@dataclass
class Sample:
    t: float
    pending_s: float
    locked: bool
    stage: str
    safe: bool
    two_stick: bool
    two_stick_ok: bool
    input_unknown: bool
    current: str | None
    live: int
    usable: int
    stick_states: tuple


@dataclass
class Night:
    clock: VirtualClock
    work: Path
    seed: int = 7
    end_s: float | None = None          # run only this long (seconds from the start)
    start_at: str | None = None         # "HH:MM" ET: start the night here instead
    fault_specs: list[dict] | None = None
    plugins: list[str] = field(default_factory=list)
    default_faults: bool = True
    use_watchdog: bool = True
    games: list = field(default_factory=list)

    def __post_init__(self):
        self.api_down = False
        self.restarts: list[float] = []
        self.engine = None
        self.house = houselib.House()
        self.samples: list[Sample] = []
        self.errors: list[dict] = []
        self.watchdog_rows: list[dict] = []
        self.fault_log: list[dict] = []
        self.lock_holds: list[tuple[float, float, str]] = []   # every hold of the controller lock
        self.crashed: str | None = None

    # ---- setup -----------------------------------------------------------------------
    def prepare(self) -> None:
        w = self.work
        (w / "data").mkdir(parents=True, exist_ok=True)
        self.games = load_games(w)
        firsts = [r.wall[0] for _, r in self.games]
        lasts = [r.wall[-1] for _, r in self.games]
        t0 = min(firsts) - 900
        if self.start_at:
            hh, mm = map(int, self.start_at.split(":"))
            t0 = datetime.strptime(NIGHT_DAY, "%Y-%m-%d").replace(hour=hh, minute=mm, tzinfo=ET).timestamp()
        self.t0 = t0
        self.t1 = max(lasts) + 600
        if self.end_s:
            self.t1 = min(self.t1, t0 + self.end_s)
        self.clock.reset(t0)
        self.live_windows = [(r.wall[0] - t0, r.wall[-1] - t0 + 120) for _, r in self.games
                             if r.wall[-1] > t0 and r.wall[0] < self.t1]
        self.live_windows.sort()
        d = w / "data"
        cfg = {
            "mode": {"dry_run": False, "leagues": {"mlb": False, "cbb": False, "wcbb": False}},
            "sunday_ticket": False,
            "reel": {"enabled": False},
            "autonomy": {"enabled": False},
            "plan": {"verify": False},
        }
        (d / "config.yaml").write_text(yaml.safe_dump(cfg))
        targets = {r.gid: spec.target for spec, r in self.games}
        (d / "channels.json").write_text(json.dumps({"codes": YTTV_CODES, "overrides": targets}))
        (d / "devices.json").write_text(json.dumps({"sticks": [
            {"host": HOSTS[0], "name": "Fire TV Stick 4K (2023)", "hdmi": "HDMI1"},
            {"host": HOSTS[1], "name": "Fire TV Stick 4K Plus", "hdmi": "HDMI2"}]}))
        h = self.house
        if os.environ.get("RZ_NIGHT_TRACE"):
            h.trace = []
        h.add(HOSTS[0], "HDMI1", "Living Room")
        h.add(HOSTS[1], "HDMI2", "Den")
        h.tv.source = h.sticks[HOSTS[0]]
        for spec, r in self.games:
            app, _, rest = spec.target.partition(":")
            if app == "youtubetv":
                h.urls[f"https://tv.youtube.com/watch/{YTTV_CODES[rest]}"] = spec.target
                h.names[spec.target] = spec.channel
                h.titles[spec.target] = f"{r.matchup}"
            else:
                h.urls[f"https://app.primevideo.com/detail?gti={rest}"] = spec.target
        self.target_game = {spec.target: r.gid for spec, r in self.games}
        self.by_gid = {r.gid: (spec, r) for spec, r in self.games}

    def patch(self) -> None:
        """Point the production modules' outside-world edges at the fakes and at the work folder."""
        from redzone import channels, control, engine as engine_mod, governor, network, plan, recorder, reel, screen_ocr, supervisor, tv
        from redzone.discovery import yttv
        from redzone.tuners import screen_prompts
        import redzone.autonomy as autonomy
        d = self.work / "data"
        houselib.install(self.house)
        control.Stick = houselib.FakeStick
        autonomy.Stick = houselib.FakeStick
        control.DEVICES = d / "devices.json"
        engine_mod.ChannelMap = functools.partial(channels.ChannelMap, path=d / "channels.json")
        tv.SamsungTV.from_devices = classmethod(lambda cls: cls())
        tv.TOKEN_FILE = d / "tv_token.txt"
        yttv.STATE_PATH, yttv.DEVICES_PATH = d / "yttv_verified.json", d / "devices.json"
        plan.DATA = supervisor.DATA = d
        reel.REEL_DIR = engine_mod.REEL_DIR = d / "reels"
        recorder.ROOT = d
        screen_ocr.recognize = houselib.fake_recognize
        screen_prompts.png_luma = houselib.fake_luma_path
        pin_host(governor)
        pin_network(network)

    def build_engine(self):
        from redzone.config import Config
        from redzone.engine import Engine
        from redzone.recorder import Recorder
        from redzone.store import Store
        d = self.work / "data"
        cfg = Config(user_path=d / "config.yaml", history_dir=d / "config_history")
        e = Engine(cfg, store=Store(d / "redzone.db"))
        e.recorder = Recorder(d)
        e.poll = self.feed.poll
        e.refresh_links = _no_links
        e._keep_awake = lambda live: None
        e.supervisor.luma = houselib.fake_luma
        e.control._lock = _TimedLock(self.lock_holds)
        self.engine = e
        return e

    # ---- the loops around the engine ---------------------------------------------------
    async def _ticker(self) -> None:
        while True:
            await asyncio.sleep(0.25)
            self.house.observe(cause="auto")

    async def _faults(self, faults: list) -> None:
        pending = sorted(((self.t0 + f.at, 0, f) for f in faults), key=lambda x: x[0])
        stops = []
        while pending or stops:
            nxt = min([p[0] for p in pending] + [s[0] for s in stops])
            await asyncio.sleep(max(0.0, nxt - time.time()))
            now = time.time()
            for p in [p for p in pending if p[0] <= now]:
                pending.remove(p)
                f = p[2]
                try:
                    f.start(self)
                    self.fault_log.append({"t": now, "event": "start", **f.describe()})
                except Exception as ex:
                    self.fault_log.append({"t": now, "event": "start failed", "error": str(ex), **f.describe()})
                if f.dur:
                    stops.append((now + f.dur, f))
            for s in [s for s in stops if s[0] <= now]:
                stops.remove(s)
                s[1].stop(self)
                self.fault_log.append({"t": now, "event": "stop", **s[1].describe()})

    async def _monitor(self) -> None:
        from redzone.models import Phase
        while True:
            await asyncio.sleep(5.0)
            e = self.engine
            if e is None:
                continue
            c = e.control
            pend = time.time() - e._pending_since if e._pending_decision is not None else 0.0
            live = sum(1 for g in e.merger.games.values() if g.phase == Phase.LIVE)
            self.samples.append(Sample(time.time(), pend, c._lock.locked(), str(c.stage.get("stage")),
                                       e.decider.safe_mode, c.two_stick, c.two_stick_ok, c.input_unknown,
                                       e.decider.current, live, len(c.usable),
                                       tuple(s.state for s in c.sticks)))

    def api_state(self) -> dict | None:
        """What GET /api/state answers (server.py: the engine's snapshot, JSON round-tripped)."""
        if self.api_down or self.engine is None:
            return None
        return json.loads(json.dumps(self.engine.snapshot(), default=str))

    async def _watchdog(self) -> None:
        import agent.watchdog as wd
        d = self.work / "data"
        wd.LOG, wd.JOURNAL, wd.DATA = d / "service.log", d / "agent.jsonl", d
        wd.fetch_state = lambda timeout=6.0: self.api_state()
        night = self

        class Proc:
            def __init__(self, rc=0, out="", err=""):
                self.returncode, self.stdout, self.stderr = rc, out, err

        class FakeSubprocess:
            TimeoutExpired = __import__("subprocess").TimeoutExpired

            @staticmethod
            def run(cmd, **kw):
                if cmd and cmd[0] == "launchctl":
                    night.restart_requested = True
                    return Proc(0)
                if cmd and "adb" in str(cmd[0]):
                    ok, out = houselib.adb_cmd(list(cmd[1:]))
                    return Proc(0 if ok else 1, out)
                return Proc(1, "", "not available in the night test")
        wd.subprocess = FakeSubprocess
        dog = wd.Watchdog(dry_run=False, use_model=False, clock=time.time)
        dog._push = lambda text: self.watchdog_rows.append({"t": time.time(), "push": text})
        self.restart_requested = False
        self.watchdog = dog
        await asyncio.sleep(30)
        while True:
            try:
                row = dog.tick()
                self.watchdog_rows.append({"t": time.time(), **row})
            except Exception as ex:
                self.errors.append({"t": time.time(), "where": "watchdog", "msg": repr(ex)})
            if self.restart_requested:
                self.restart_requested = False
                await self.restart()
            await asyncio.sleep(wd.CHECK_S)

    async def restart(self) -> None:
        """launchctl kickstart -k: the old process dies, a fresh one starts on the same data."""
        old, task = self.engine, self.engine_task
        self.restarts.append(time.time())
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        for t in (old.control._task, old.control._preload_task, old.control._resume_task,
                  old.control._refresh_task, old._reel_task):
            if t is not None and not t.done():
                t.cancel()
        self.api_down = False                 # a fresh process answers again
        e = self.build_engine()
        self.engine_task = asyncio.create_task(e.run(), name="engine")
        self.engine_task.add_done_callback(self._engine_done)

    def _engine_done(self, t: asyncio.Task) -> None:
        if t.cancelled():
            return
        ex = t.exception()
        self.crashed = repr(ex) if ex else "engine.run() returned"

    async def main(self) -> dict:
        rng = random.Random(self.seed)
        self.feed = NightFeed(self)
        self.build_engine()
        faults: list = []
        if self.default_faults:
            faults += faultlib.default(rng, self.live_windows)
        for spec in self.fault_specs or []:
            faults.append(faultlib.from_spec(spec))
        for p in self.plugins:
            faults += faultlib.plug(p, self, rng)
        faults = [f for f in faults if f.at < self.t1 - self.t0]
        if not self.use_watchdog:
            faults = [f for f in faults if f.name != "api_down"]
        self.faults = faults
        self.engine_task = asyncio.create_task(self.engine.run(), name="engine")
        self.engine_task.add_done_callback(self._engine_done)
        side = [asyncio.create_task(c) for c in (self._ticker(), self._faults(faults), self._monitor())]
        if self.use_watchdog:
            side.append(asyncio.create_task(self._watchdog()))
        await asyncio.sleep(max(0.0, self.t1 - time.time()))
        self.engine_task.remove_done_callback(self._engine_done)
        if self.engine_task.done() and not self.engine_task.cancelled() and self.crashed is None:
            self._engine_done(self.engine_task)
        for t in side + [self.engine_task]:
            t.cancel()
        await asyncio.gather(*side, self.engine_task, return_exceptions=True)
        with (self.work / "screen.jsonl").open("w") as f:   # what the TV showed, for debugging a report
            for x in self.house.shown:
                f.write(json.dumps({"t": round(x.t, 2), "et": _et(x.t), "host": x.host, "target": x.target,
                                    "what": x.what, "behind": round(x.behind, 1), "cause": x.cause}) + "\n")
        if self.house.trace is not None:
            (self.work / "trace.txt").write_text("\n".join(" | ".join(map(str, r)) for r in self.house.trace))
        return grade(self)


async def _no_links(eventlinks) -> int:
    return 0


# ---- logging: the service log the watchdog reads, and every traceback -----------------------

class Catch(logging.Handler):
    def __init__(self, night: Night):
        super().__init__(logging.WARNING)
        self.night = night

    def emit(self, record):
        if record.levelno >= logging.ERROR:
            self.night.errors.append({"t": time.time(), "where": record.name, "msg": record.getMessage()[:300],
                                      "exc": self.format(record).splitlines()[-1][:300] if record.exc_info else ""})


def setup_logging(night: Night, verbose: bool) -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)
    fh = logging.FileHandler(night.work / "data" / "service.log")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(fh)
    root.addHandler(Catch(night))
    if verbose:
        sh = logging.StreamHandler()
        sh.setLevel(logging.WARNING)
        sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(sh)


# ---- grading -------------------------------------------------------------------------------

BREAK_TYPES = ("timeout", "official timeout", "end period", "end of half", "two-minute warning",
               "end of regulation", "end of game", "coin toss")


def _plays_on_screen(night: Night, gid: str, screen_t: float) -> list[str]:
    """Plays of this game in progress at this moment of game time (the backtest's rule)."""
    from redzone.lab.backtest import REAL_BREAKS
    from redzone.sources import espn
    spec, r = night.by_gid[gid]
    plen = PLAY_LEN_S.get(spec.league, 7.0)
    hits = []
    for w, p in zip(r.wall, r.plays):
        if w < screen_t - 1 or w - plen > screen_t + 1:
            continue
        t = (espn._get(p, "type", "text") or "").lower()
        if t in BREAK_TYPES or (espn.stoppage_for(r.league, p) in REAL_BREAKS and not p.get("scoringPlay")):
            continue
        if w - plen <= screen_t <= w:
            hits.append((p.get("text") or t)[:80])
    return hits


def cuts(night: Night) -> tuple[list[dict], list[dict]]:
    """Every time the TV stopped showing a game that was playing, because of something we did:
    (mid-play cuts, all cuts). Graded from the TV's own record, at each channel's true delay."""
    mid, every = [], []
    sh = night.house.shown
    for a, b in zip(sh, sh[1:]):
        if a.what != "playing" or not a.target or a.target not in night.target_game:
            continue
        if b.what == "prompt" or (b.host, b.target) == (a.host, a.target) and b.what == "playing":
            continue
        gid = night.target_game[a.target]
        delay = DELAY_S[a.target.split(":")[0]]
        screen_t = b.t - delay - a.behind
        plays = _plays_on_screen(night, gid, screen_t)
        rec = {"t": round(b.t, 1), "et": _et(b.t), "game": night.by_gid[gid][1].matchup, "from": a.target,
               "to": b.target or b.what, "behind": round(a.behind), "cause": b.cause, "plays": plays}
        every.append(rec)
        if plays and a.behind < 1:
            mid.append(rec)
    return mid, every


def _et(t: float) -> str:
    return datetime.fromtimestamp(t, ET).strftime("%-I:%M:%S %p")


def pin_host(governor) -> None:
    """The night grades the house, not the computer it runs on: the resource governor (the app's
    and the watchdog's) sees a calm, idle, plugged-in machine. Left alone it read the real host:
    on Nick's Mac the live RedZone app (`python -m redzone`, over the 1200 MB cap) showed up in
    `ps`, so the night's watchdog kept restarting the night's own engine; a busy or battery Mac
    also changed OCR pacing. CI has neither, so the night passed there and failed on the Mac."""
    def calm(root=None, run=None, clock=time.time):
        return governor.Sample(at=clock(), on_battery=False, battery_pct=100, low_power=False,
                               thermal_level=0, mem_pressure=1, swap_used_mb=0.0, load_per_core=0.1,
                               disk_free_mb=100_000.0, procs={})
    governor.sample = calm


def pin_network(network) -> None:
    """The night's sticks live at 10.0.0.x on the fake home LAN, so the network check must not read the
    real computer either. Left alone it compared them with the host's own LAN: on Nick's Mac
    (100.110.165.x, Tailscale up) it said "away from home", the supervisor then skips every adb
    reconnect, and a dropped stick stayed offline all night. CI runners sit on 10.x, so it passed there."""
    def home(probe, home, clock=time.time):
        return network.NetState(where="home", home=home.label(), home_subnet=str(home.subnet or ""),
                                message="On the home network (the night's house)", checked_at=clock())
    network.assess = home


class _HostileProbe:
    """redzone.network.Probe for a host that is NOT the fake house: home Wi-Fi on 192.168.1.x and a
    Tailscale tunnel (utun4, 100.64/10) taking the 10.0.0.x routes the fake sticks live on."""
    def interfaces(self):
        return {"lo0": [("127.0.0.1", 8)], "en0": [("192.168.1.20", 24)], "utun4": [("100.101.1.2", 32)]}

    def default_route(self):
        return "192.168.1.1", "en0"

    def route_iface(self, ip):
        return "utun4" if ip.startswith("10.") else "en0"

    def arp(self):
        return {}

    def nudge(self, hosts):
        return None


def hostile_host() -> None:
    """Make the machine the night runs on look as unlike the fake house as possible (the CI night sets
    RZ_NIGHT_HOSTILE_HOST=1): another subnet, a tunnel stealing 10.x, and a busy battery-powered Mac
    with a big live RedZone app in ps. Night.patch() must pin all of it away (pin_host, pin_network),
    or the night fails in CI the way it failed on Nick's Mac (RC 2 and RC 3 deploys)."""
    from redzone import governor, network
    network.Probe = _HostileProbe

    def busy(root=None, run=None, clock=time.time):
        return governor.Sample(at=clock(), on_battery=True, battery_pct=5, load_per_core=14.0,
                               mem_pressure=4, disk_free_mb=100.0, procs={"service": 1500.0})
    governor.sample = busy


def _still_off_at_end(samples: list[Sample], pred) -> float:
    """How long the run of samples matching pred that is still going at the end has lasted (0 if none)."""
    if not samples or not pred(samples[-1]):
        return 0.0
    start = samples[-1].t
    for s in reversed(samples):
        if not pred(s):
            break
        start = s.t
    return samples[-1].t - start


def _episodes(samples: list[Sample], pred) -> list[tuple[float, float]]:
    out, start = [], None
    for s in samples:
        if pred(s):
            start = s.t if start is None else start
        elif start is not None:
            out.append((start, s.t))
            start = None
    if start is not None:
        out.append((start, samples[-1].t if samples else start))
    return out


# Real bugs this night found that are reported to their owner and not fixed yet. Each is still
# listed in the report (no_crash.detail.known_errors) but doesn't fail the night. Remove an entry
# once its fix lands, so the night guards against it coming back.
KNOWN_ERRORS = (
    # control.boundary_reads reads s.tuned["target"] again after awaiting another stick's
    # playback; a switch can clear s.tuned meanwhile (SWITCHING, redzone/control.py:293).
    ("boundary read failed", "TypeError: 'NoneType' object is not subscriptable"),
)


def _known(err: dict) -> bool:
    return any(m in str(err.get("msg", "")) and e in str(err.get("exc", "")) for m, e in KNOWN_ERRORS)


FAR_BEHIND_S = 120.0   # a picture this far behind live (s) is reported as stale


class _TimedLock(asyncio.Lock):
    """The controller's lock, recording each hold: (acquired, released, the holder's coroutine).
    Sampling `locked()` every few seconds can't tell one stuck hold from many short ones."""

    def __init__(self, holds: list):
        super().__init__()
        self._holds = holds
        self._since = None
        self._who = ""

    async def acquire(self):
        r = await super().acquire()
        self._since = time.time()
        task = asyncio.current_task()
        coro = task.get_coro() if task else None
        self._who = getattr(coro, "__qualname__", "") or (task.get_name() if task else "")
        return r

    def release(self):
        if self._since is not None:
            self._holds.append((self._since, time.time(), self._who))
            self._since = None
        super().release()

    def held_s(self) -> float:
        return time.time() - self._since if self._since is not None else 0.0


def grade(night: Night) -> dict:
    from redzone.decider.decider import REEL
    e = night.engine
    mid, every = cuts(night)
    s = night.samples
    checks: list[dict] = []

    def check(name: str, ok: bool, detail) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    real_errors = [x for x in night.errors if x["where"] != "redzone.night"]
    known = [x for x in real_errors if _known(x)]
    real_errors = [x for x in real_errors if not _known(x)]
    check("no_crash", night.crashed is None and not real_errors,
          {"crashed": night.crashed, "errors": real_errors[:20], "n_errors": len(real_errors),
           "known_errors": known[:20]})
    max_switch = float(e.cfg.get("control.max_switch_s", 150))
    pend = max((x.pending_s for x in s), default=0.0)
    holds = list(night.lock_holds)
    lk = getattr(e.control, "_lock", None)
    if isinstance(lk, _TimedLock) and lk.locked() and lk._since is not None:
        holds.append((lk._since, time.time(), lk._who + " (still held)"))
    long_lock = max((b - a for a, b, _ in holds), default=0.0)
    unknown = _episodes(s, lambda x: x.input_unknown)
    long_unknown = max((b - a for a, b in unknown), default=0.0)
    final = s[-1] if s else None
    check("no_stuck_state", pend <= max_switch + 40 and long_lock <= 180 and long_unknown <= 600
          and final is not None and final.pending_s <= max_switch and not final.input_unknown,
          {"longest_pending_switch_s": round(pend), "longest_controller_lock_s": round(long_lock),
           "long_locks": [(_et(a), round(b - a), who) for a, b, who in holds if b - a > 60],
           "longest_input_unknown_s": round(long_unknown), "end": final.__dict__ if final else None})
    safe = _episodes(s, lambda x: x.safe)
    check("safe_mode_clears", all(b - a <= 900 for a, b in safe) and (final is None or not final.safe),
          {"episodes": [(_et(a), round(b - a)) for a, b in safe]})
    off = _episodes(s, lambda x: not x.two_stick_ok)
    # Two-stick mode (and its inputs: both sticks connected, the HDMI map) is back at the end, or the
    # gap still running at the end is younger than the same 420 s every off episode gets.
    tail_off = _still_off_at_end(s, lambda x: not x.two_stick)
    check("two_stick_recovers", all(b - a <= 420 for a, b in off) and tail_off <= 420,
          {"off_episodes": [(_et(a), round(b - a)) for a, b in off],
           "two_stick_at_end": final.two_stick if final else None, "off_at_end_s": round(tail_off),
           "end": final.__dict__ if final else None})
    check("zero_midplay_cuts", not mid, {"mid_play": mid, "cuts": len(every)})
    # Every fault the night injected was dealt with.
    ev = night.house.events
    shown_prompts = [x for x in ev if x[1] == "prompt_shown"]
    cleared = [x for x in ev if x[1] == "prompt_cleared"]
    prompt_ok = all(any(c[0] >= p[0] and c[0] - p[0] <= 60 for c in cleared) for p in shown_prompts)
    check("still_watching_cleared", prompt_ok,
          {"shown": [_et(p[0]) for p in shown_prompts], "cleared": [_et(c[0]) for c in cleared]})
    paused_by_center = [x for x in ev if x[1] == "center_paused_player"]
    check("no_center_in_player", not paused_by_center, {"events": [(_et(x[0]), x[2], x[3]) for x in paused_by_center]})
    wedges = [f for f in night.fault_log if f["name"] == "stick_wedge" and f["event"] == "start"]
    fixes = [x for x in ev if x[1] == "adb_disconnect"]
    wedge_ok = all(any(fx[2] == w["host"] and 0 <= fx[0] - w["t"] <= 600 for fx in fixes) for w in wedges)
    check("wedged_stick_reconnected", wedge_ok and all(not d.wedged for d in night.house.sticks.values()),
          {"wedges": [(_et(w["t"]), w["host"]) for w in wedges], "disconnects": [(_et(x[0]), x[2]) for x in fixes]})
    sup = [json.loads(line) for line in (night.work / "data" / "supervisor.jsonl").read_text().splitlines()
           if line.strip()] if (night.work / "data" / "supervisor.jsonl").exists() else []
    outages = [f for f in night.fault_log if f["name"] == "feed_outage" and f["event"] == "start"]
    stale_fixes = [x for x in sup if x.get("kind") == "fix" and x.get("rule") == "stale_feeds"]
    check("feed_outage_handled", all(any(0 <= x["ts"] - o["t"] <= o["dur"] + 60 for x in stale_fixes) for o in outages)
          if outages else True, {"outages": [(_et(o["t"]), round(o["dur"])) for o in outages],
                                 "supervisor_fixes": [(_et(x["ts"]), x.get("action")) for x in stale_fixes]})
    downs = [f for f in night.fault_log if f["name"] == "api_down" and f["event"] == "start"]
    rows = [r for r in night.watchdog_rows if "action" in r]
    restarts = [r for r in rows if r["action"] == "restart_service"]
    expected = all(any(0 <= x - d["t"] <= 300 for x in night.restarts) for d in downs)
    stray = [x for x in night.restarts if not any(0 <= x - d["t"] <= d["dur"] for d in downs)]
    check("watchdog_fixes_what_it_should", expected and not stray if night.use_watchdog else True,
          {"api_down": [(_et(d["t"]), round(d["dur"])) for d in downs], "restarts": [_et(x) for x in night.restarts],
           "stray_restarts": [_et(x) for x in stray],
           "actions": Counter(r["action"] for r in rows), "notes": [r.get("why") for r in rows if r["action"] != "none"][:12]})
    # The screen: how much of the live night showed a live game.
    live_s = shown_live = far_s = prompt_s = 0.0
    far: list = []
    sh = night.house.shown
    for a, b in zip(sh, sh[1:] + [houselib.Shown(night.t1, None, None, "end", 0, "")]):
        dt = b.t - a.t
        if any(night.t0 + lo <= a.t <= night.t0 + hi for lo, hi in night.live_windows):
            live_s += dt
            if a.what == "playing":
                shown_live += dt
                if a.behind >= FAR_BEHIND_S:
                    far_s += dt
                    far.append((_et(a.t), a.target, round(a.behind), round(dt)))
            elif a.what == "prompt":
                prompt_s += dt
    coverage = shown_live / live_s if live_s else 1.0
    # Informational (not graded): a stick that paused off screen and resumed where it paused comes
    # back this far behind live; and how long a prompt sat on screen in all.
    check("tv_shows_games", coverage >= 0.75,
          {"playing_share_while_live": round(coverage, 3), "far_behind_share": round(far_s / live_s, 3) if live_s else 0.0,
           "far_behind": far[:20], "prompt_on_screen_s": round(prompt_s)})
    switches = e.store._db.execute("SELECT ok, dry_run, detail, kind FROM switches").fetchall() \
        if hasattr(e.store, "_db") else []
    return {
        "window": [_et(night.t0), _et(night.t1)], "hours": round((night.t1 - night.t0) / 3600, 2),
        "ok": all(c["ok"] for c in checks), "checks": checks,
        "cuts": len(every), "mid_play_cuts": mid, "tv_flips": night.house.tv.flips,
        "switches": {"total": len(switches), "ok": sum(1 for x in switches if x[0]),
                     "failed": sum(1 for x in switches if not x[0])},
        "faults": [f.describe() | {"et": _et(night.t0 + f.at)} for f in night.faults],
        "restarts": [_et(x) for x in night.restarts], "polls": night.feed.polls,
        "reel_target": REEL,
        "stick_commands": {h: d.cmds for h, d in night.house.sticks.items()},
    }
