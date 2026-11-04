"""Faults for the end-to-end night, and the hook to plug more in.

A fault is anything with `name`, `at` (seconds after the night starts), `dur` (seconds, 0 = a
one-shot) and `start(world)` / `stop(world)`. The night calls start at `at` and stop at `at + dur`.
`world` is the running Night (redzone/lab/night/run.py): world.house (fake sticks + TV),
world.engine (the real Engine, replaced on a restart), world.feed (the replayed ESPN feed),
world.clock (virtual time), world.watchdog (agent/watchdog.py's Watchdog).

The ones here are only what the I4 night itself needs (the faults the user listed: stick drops,
a "Still watching?" prompt, a feed outage, a TV that ignores a flip, a hung web server for the
watchdog). The platform's reusable fault module (redzone/platform/faults.py, being built in its
own thread) plugs in with `--faults module:function`, where function(world, rng) returns a list
of faults with this shape; they run next to (or, with --no-default-faults, instead of) these.
"""
from __future__ import annotations

import importlib
import random
from dataclasses import dataclass, field


@dataclass
class Fault:
    name: str
    at: float
    dur: float = 0.0
    host: str = ""                 # a stick ("A"/"B" = its role at start time: "visible"/"hidden" also work)
    info: dict = field(default_factory=dict)

    def start(self, world) -> None:
        raise NotImplementedError

    def stop(self, world) -> None:
        return None

    def describe(self) -> dict:
        return {"name": self.name, "at": self.at, "dur": self.dur, "host": self.host, **self.info}


def _pick(world, which: str) -> str:
    """A stick's host from 'visible' / 'hidden' (as the TV shows it right now) or a host."""
    if which in ("visible", "hidden"):
        src = world.house.tv.source
        hosts = list(world.house.sticks)
        vis = src.host if src is not None else hosts[0]
        return vis if which == "visible" else next(h for h in hosts if h != vis)
    return which


class StickDrop(Fault):
    """The stick stops answering adb (Wi-Fi blip, reboot). It keeps playing whatever it played."""
    def start(self, world):
        self.host = _pick(world, self.host)
        world.house.sticks[self.host].online = False
        world.house.event("fault", self.host, "drop")

    def stop(self, world):
        world.house.sticks[self.host].online = True


class StickWedge(Fault):
    """The adb transport wedges: connect says "already connected" but every shell fails until an
    `adb disconnect`. Only a reconnect (supervisor rule_adb or the watchdog) clears it."""
    def start(self, world):
        self.host = _pick(world, self.host)
        world.house.sticks[self.host].wedged = True
        world.house.event("fault", self.host, "wedge")


class StillWatching(Fault):
    """YouTube TV's "Are you still watching?" on the stick on screen (the next time it's playing)."""
    def start(self, world):
        world.house.active_faults.setdefault("still_watching", set()).add("*" if not self.host else _pick(world, self.host))


class FeedOutage(Fault):
    """The score feed returns nothing (ESPN 5xx / internet down) for `dur` seconds."""
    def start(self, world):
        world.feed.outage = True
        world.house.event("fault", "-", "feed outage")

    def stop(self, world):
        world.feed.outage = False


class FlipIgnored(Fault):
    """The TV ignores the next <Active Source> from this stick (CEC dropped once)."""
    def start(self, world):
        world.house.active_faults.setdefault("flip_ignored", set()).add(_pick(world, self.host or "hidden")
                                                                         if self.host not in ("", "*") else "*")


class ApiDown(Fault):
    """The app's web server stops answering (a wedged event loop on the real Mac). Only a restart
    (the watchdog's restart_service) brings it back; stop() is the deadline if none comes."""
    def start(self, world):
        world.api_down = True

    def stop(self, world):
        world.api_down = False


class ClockSkew(Fault):
    """The wall clock jumps (NTP); monotonic time does not."""
    def start(self, world):
        world.clock.skew += float(self.info.get("seconds", 30.0))

    def stop(self, world):
        world.clock.skew -= float(self.info.get("seconds", 30.0))


# ---- playbook faults (docs/playbooks.md): one night-lab kind per entry that had none ----------

class FeedFrozen(Fault):
    """FEED-02: the feed keeps answering (304 / the same cached body) but nothing advances: no error,
    so only the no-progress rule can tell. The game on screen must not be left or entered on stale data."""
    def start(self, world):
        world.feed.frozen = True
        world.house.event("fault", "-", "feed frozen")

    def stop(self, world):
        world.feed.frozen = False


class RateLimit(Fault):
    """FEED-06: every poll answers 429 for `dur` seconds (counted as source errors, like a 5xx)."""
    def start(self, world):
        world.feed.rate_limited = True
        world.house.event("fault", "-", "feed 429")

    def stop(self, world):
        world.feed.rate_limited = False


class SlowPoll(Fault):
    """MAC-05 slow tick: each feed poll's round trip takes info.seconds longer (a saturated link or
    a hot, throttled Mac), so data arrives late and the engine's loop runs slower."""
    def start(self, world):
        world.feed.slow_s = float(self.info.get("seconds", 6.0))

    def stop(self, world):
        world.feed.slow_s = 0.0


class WifiDegrade(Fault):
    """SAFE-03: every adb reply from both sticks is info.scale times slower (weak Wi-Fi), and the sync
    probe reports the bad link (info.q, buffering + an RSSI drop) for the stick on screen."""
    def start(self, world):
        world.house.adb_scale = float(self.info.get("scale", 8.0))
        e = getattr(world, "engine", None)
        if e is not None:
            self.info["target"] = e.decider.target_of(e.decider.current) or "visible"
            e._sync_condition(self.info["target"], float(self.info.get("q", 13.0)), {})
            # The probe re-reports every few seconds on a real stick; stand in for that for the whole fault.
            q, t = e.decider.safety.net[self.info["target"]]
            e.decider.safety.net[self.info["target"]] = (q, t + self.dur)
        world.house.event("fault", "*", f"wifi x{world.house.adb_scale:g}")

    def stop(self, world):
        world.house.adb_scale = 1.0
        if getattr(world, "engine", None) is not None and "target" in self.info:
            world.engine._sync_condition(self.info["target"], 1.0, {})


class Buffering(Fault):
    """NET-02: the stick's player spins (BUFFERING) for `dur` seconds, then plays again by itself."""
    def start(self, world):
        from redzone.lab.night import house as h
        self.host = _pick(world, self.host or "visible")
        d = world.house.sticks[self.host]
        a = d.apps.get(d.fg)
        self.info["pkg"] = a.pkg if a else None
        if a is not None and a.screen == "player" and a.state == h.PLAYING:
            d._stamp(a, h.BUFFERING)
            self.info["hit"] = True
        world.house.event("fault", self.host, "buffering")

    def stop(self, world):
        from redzone.lab.night import house as h
        d = world.house.sticks.get(self.host)
        a = d.apps.get(self.info.get("pkg") or "") if d else None
        if a is not None and a.state == h.BUFFERING:
            d._stamp(a, h.PLAYING, d._live_pos(a))


PLAYBOOK = {"feed_frozen": "FEED-02", "rate_limit": "FEED-06", "slow_poll": "MAC-05", "wifi_degrade": "SAFE-03",
            "buffering": "NET-02"}   # fault kind -> docs/playbooks.md entry it exercises


class FeedStale(Fault):
    """[playbooks SAFE-01] Every live game's feed but the one on screen stops (one source wedged on
    those games): any switch would go into a game whose feed is stale."""
    def start(self, world):
        cur = world.engine.decider.current
        world.feed.stale_games = {r.gid for _, r in world.feed.games if r.gid != cur}
        self.info["frozen"] = sorted(world.feed.stale_games)
        world.house.event("fault", "-", f"feed stale on {len(world.feed.stale_games)} games")

    def stop(self, world):
        world.feed.stale_games = set()


class SyncWide(Fault):
    """[playbooks SAFE-02] Every channel's delay doubt jumps to `sd` seconds (sync lost: buffering, an app
    restart) until the fault ends or a fresh calibration lands."""
    def start(self, world):
        e, now = world.engine, world.clock.time()
        self._was = {}
        for t in list(e.delays.book):
            d = e.delays.get(t)
            self._was[t] = (d.sigma, d.calibrated_at)
            d.sigma = max(d.sigma, float(self.info.get("sd", 12.0)))
        self.info["sigma"] = {t: round(e.delays.get(t).current_sigma(now), 1) for t in list(e.delays.book)}
        world.house.event("fault", "-", "sync doubt widened")

    def stop(self, world):
        for t, (sig, cal) in getattr(self, "_was", {}).items():
            d = world.engine.delays.get(t)
            if d.calibrated_at == cal:      # not re-measured meanwhile
                d.sigma = sig


KINDS = {"stick_drop": StickDrop, "stick_wedge": StickWedge, "still_watching": StillWatching,
         "feed_outage": FeedOutage, "flip_ignored": FlipIgnored, "api_down": ApiDown, "clock_skew": ClockSkew,
         "feed_frozen": FeedFrozen, "rate_limit": RateLimit, "slow_poll": SlowPoll, "wifi_degrade": WifiDegrade,
         "buffering": Buffering, "feed_stale": FeedStale, "sync_wide": SyncWide}


def from_spec(spec: dict) -> Fault:
    """{"name": "stick_drop", "at": 3600, "dur": 60, "host": "hidden"} -> a Fault."""
    kind = KINDS[spec["name"]]
    extra = {k: v for k, v in spec.items() if k not in ("name", "at", "dur", "host")}
    return kind(spec["name"], float(spec["at"]), float(spec.get("dur", 0.0)), str(spec.get("host", "")), extra)


def default(rng: random.Random, live_windows: list[tuple[float, float]]) -> list[Fault]:
    """The I4 night: everything the user listed, at seeded times while games are live.
    live_windows are (start, end) offsets from the night's start when something is live."""
    def live(t: float) -> int:
        return sum(1 for lo, hi in live_windows if lo + 300 <= t <= hi - 600)

    def when(lo_frac: float, hi_frac: float) -> float:
        """A seeded time in this part of the night when at least one game is well under way."""
        a, b = live_windows[0][0], max(hi for _, hi in live_windows)
        for _ in range(200):
            t = a + (b - a) * rng.uniform(lo_frac, hi_frac)
            if live(t):
                return t
        return t
    faults: list[Fault] = []
    # Random stick drops: short blips on either stick, a few per night.
    for i in range(4):
        faults.append(StickDrop("stick_drop", when(0.05 + 0.2 * i, 0.2 + 0.2 * i), rng.uniform(25, 90),
                                rng.choice(["visible", "hidden"])))
    faults.append(StickWedge("stick_wedge", when(0.30, 0.40), 0.0, "hidden"))
    faults.append(StillWatching("still_watching", when(0.15, 0.25), 0.0, "visible"))
    faults.append(FeedOutage("feed_outage", when(0.45, 0.55), rng.uniform(300, 420)))
    faults.append(FlipIgnored("flip_ignored", when(0.60, 0.70), 0.0, "*"))
    faults.append(ApiDown("api_down", when(0.75, 0.85), 600.0))
    return sorted(faults, key=lambda f: f.at)


def safety(world, rng: random.Random) -> list[Fault]:
    """[playbooks SAFE-01/02/03/05] One fault per safety playbook, while games are live:
    `python -m redzone.lab.night --faults redzone.lab.night.faults:safety --set playbooks.safe.enabled=true`."""
    lw = world.live_windows
    a, b = lw[0][0], max(hi for _, hi in lw)

    def when(lo_frac: float, hi_frac: float) -> float:
        for _ in range(200):
            t = a + (b - a) * rng.uniform(lo_frac, hi_frac)
            if any(lo + 300 <= t <= hi - 600 for lo, hi in lw):
                return t
        return t
    return [FeedStale("feed_stale", when(0.10, 0.20), 900.0),
            SyncWide("sync_wide", when(0.30, 0.40), 600.0, info={"sd": 12.0}),
            WifiDegrade("wifi_degrade", when(0.50, 0.60), 900.0, info={"q": 13.0}),
            ClockSkew("clock_skew", when(0.70, 0.80), 1800.0, info={"seconds": 30.0})]


def playbook(world, rng: random.Random) -> list[Fault]:
    """`--faults redzone.lab.night.faults:playbook`: one of each playbook fault at seeded live times,
    next to (or with --no-default-faults instead of) the I4 night. Each is graded in the report's
    `playbook_faults` (cuts and mid-play cuts while it ran and 60 s after)."""
    wins = world.live_windows
    a, b = wins[0][0], max(hi for _, hi in wins)

    def when(lo, hi):
        for _ in range(200):
            t = a + (b - a) * rng.uniform(lo, hi)
            if any(w0 + 300 <= t <= w1 - 600 for w0, w1 in wins):
                return t
        return t
    out = [FeedFrozen("feed_frozen", when(0.10, 0.20), rng.uniform(90, 180)),
           RateLimit("rate_limit", when(0.25, 0.35), rng.uniform(60, 180)),
           SlowPoll("slow_poll", when(0.40, 0.50), rng.uniform(300, 600), info={"seconds": rng.uniform(4, 9)}),
           WifiDegrade("wifi_degrade", when(0.55, 0.65), rng.uniform(300, 600), info={"scale": rng.choice([4.0, 8.0, 15.0])}),
           Buffering("buffering", when(0.70, 0.80), rng.uniform(8, 40), rng.choice(["visible", "hidden"]))]
    return sorted(out, key=lambda f: f.at)


def plug(spec: str, world, rng: random.Random) -> list[Fault]:
    """`module:function` -> function(world, rng) -> faults (the platform fault module's hook)."""
    mod, _, fn = spec.partition(":")
    return list(getattr(importlib.import_module(mod), fn or "faults")(world, rng))
