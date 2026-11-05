"""Autonomy: the app runs itself. After the one-time "Allow" prompts on the TV, nothing here needs you.

What it does on its own:
- Devices: a Fire TV stick missing from the network for 2+ minutes gets looked for (adb port 5555),
  recognized by its serial number rather than its address, and followed to its new address. Same for
  the Samsung TV (port 8001, recognized by its Wi-Fi MAC). data/devices.json is rewritten atomically.
- First run: nothing saved -> find the sticks and the TV, save them, and say in plain English what's
  left to do ("approve the prompt on the TV"). Keeps looking every few minutes until it finds them.
- Jobs, each at a fixed Eastern time with its own "is it safe right now?" rule:
      Sunday Ticket scan   NFL Sundays 12:45 / 3:55 PM   which game is on each Sunday Ticket channel
      nightly app check    4:00 AM                       plays a known channel on each app, unseen
      YouTube TV codes     4:20 AM                       redzone.discovery.yttv.verify_codes (if present)
      game archive         5:00 AM                       yesterday's finished games, for the lab
      lab tune-up          Tuesdays 4:40 AM              searches better settings; only RECOMMENDS them
      game links           every 5 min (engine)          Prime Video / ESPN+ links (status shown here)
- Delay bootstrap: the first time a channel is on screen, get its delay measured once (mic, or one tap).

Guardrails:
- Every job is time-boxed, single-flight, and logged to data/autonomy.jsonl with its outcome.
- A job that isn't safe to run waits and re-checks; if its window closes, it's logged as missed.
- Nothing here changes your settings. The lab tune-up writes data/lab/recommendation.json and stops.
- Never touches a stick the TV may be showing, and never wakes a sleeping stick at night while
  HDMI-CEC is on (waking it can turn the TV on or switch the TV to it).
- Every network call has a timeout and never raises; one broken piece never stops the others.

On the real devices (read-only):   .venv/bin/python -m redzone.autonomy check
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib
import inspect
import ipaddress
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

import httpx

from redzone.channels import describe
from redzone.models import League, Phase
from redzone.network import arp_table, find_by_mac, norm_mac  # noqa: F401  (re-exported)
from redzone.tuners.adb import YTTV_PACKAGES, Stick, adb_available
from redzone.tuners.tuners import TUNERS

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
TUE, SUN = 1, 6

LOST_AFTER_S = 120.0                      # a stick/TV missing this long gets looked for on the network
SCAN_FIRST_S, SCAN_MAX_S = 600.0, 3600.0  # wait between network scans: 10 min, doubling to 1 h
TV_CHECK_S = 120.0                        # how often the TV's own status page is read
HEAL_TIMEOUT_S = 120.0                    # one device-healing pass can't take longer than this
SETUP_TIMEOUT_S = 180.0                   # one first-run discovery can't take longer than this
SETUP_EVERY_S = 300.0                     # first run: look again every 5 min until a stick is saved
TV_SETUP_EVERY_S = 1800.0                 # ...and for the TV every 30 min until one is saved
ST_BATCH = 4                              # Sunday Ticket channels per controller hold (~30-40 s)
ARCHIVE_DAYS = 3                          # yesterday + 2 earlier days (catches up after a missed night)
MIN_FREE_BYTES = 2 * 1024 ** 3            # the archive never fills the disk
MAX_FEED_BYTES = 20_000_000
TEST_APPS = ("youtubetv", "prime", "espn")
APP_NAMES = {"youtubetv": "YouTube TV", "prime": "Prime Video", "espn": "ESPN"}
# 24/7 channels: something is always live on them, so a 4 AM test can prove video actually plays.
TEST_CHANNELS = ("espnews", "nflnetwork", "espn", "espn2", "fs1", "nbatv", "mlbnetwork", "nhlnetwork",
                 "cbssn", "tnt", "usa")
PART_TIME = {"redzone"}   # only on air during games: never a 4 AM test channel
SIGNED_OUT = ("sign in", "signin", "log in", "login to", "activate your", "register your device",
              "enter this code")
LEAGUE_PATHS = {"nfl": "football/nfl", "nba": "basketball/nba", "nhl": "hockey/nhl", "mlb": "baseball/mlb"}
ESPN_SITE = "https://site.api.espn.com/apis/site/v2/sports/"
LAB_CODE = ("import json; from redzone.lab.optimize import search; r = search(); "
            "print(json.dumps({k: r.get(k) for k in ('current', 'best', 'slates', 'seconds')}, default=str))")

# Plain-English next steps for first run.
ENABLE_ADB = ("No Fire TV stick answered yet. On each stick: Settings → My Fire TV → About → click the "
              "stick's name 7 times, go back → Developer Options → turn on ADB Debugging. "
              "I'll keep looking every few minutes.")
APPROVE = ("On the TV, a box asks “Allow USB debugging?” for the stick at {ip} — tick “Always allow "
           "from this computer” and press OK. I'll connect as soon as you do.")
NO_HDMI = ("I couldn't tell which TV input {name} is plugged into — turn on Anynet+ (HDMI-CEC) in the "
           "TV's settings so two-stick mode can switch inputs.")
VEGA = "{name} runs Vega OS, which can't be controlled — I'll use your other stick."
INSTALL_ADB = "Install the Android tools once (in Terminal): brew install --cask android-platform-tools"
NO_NETWORK = "This Mac isn't on a network — connect it to the same Wi-Fi as the TV and I'll look again."
TV_FOUND = ("Found your TV ({name}) at {ip}. The first time I switch its input it asks to allow "
            "“RedZone Everything” — choose Allow.")
TV_MISSING = ("No Samsung TV answered — it has to be on and on the same Wi-Fi. I'll keep looking; "
              "it's only needed for two-stick mode.")


# ============================================================================ small helpers


@dataclass
class Paths:
    """Where autonomy reads and writes. Tests point these at a temp folder."""
    devices: Path = DATA / "devices.json"
    log: Path = DATA / "autonomy.jsonl"
    state: Path = DATA / "autonomy_state.json"
    archive: Path = DATA / "archive"
    lab: Path = DATA / "lab"

    @classmethod
    def under(cls, root: Path) -> "Paths":
        return cls(root / "devices.json", root / "autonomy.jsonl", root / "autonomy_state.json",
                   root / "archive", root / "lab")


def read_json(path: Path, default: Any) -> Any:
    """Parsed JSON, or `default` when the file is missing or damaged (never raises)."""
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def read_devices(path: Path) -> dict:
    """data/devices.json as a dict ({} when missing, damaged, or not an object)."""
    d = read_json(path, {})
    return d if isinstance(d, dict) else {}


def write_bytes(path: Path, body: bytes) -> None:
    """Atomic: write a temp file, then rename it over the old one. A crash mid-write can't leave half
    a file, and nothing reading it (the controller, the lab) ever sees a partial one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(body)
    os.replace(tmp, path)


def write_json(path: Path, data: Any, compact: bool = False) -> None:
    text = json.dumps(data, default=str) if compact else json.dumps(data, indent=1, default=str)
    write_bytes(path, text.encode())


def append_jsonl(path: Path, rec: dict) -> None:
    """One line per event. A full disk only costs that line, never the app."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(rec, default=str, separators=(",", ":")) + "\n")
    except OSError as e:
        log.warning("couldn't write %s: %s", path.name, e)


def tail_lines(path: Path, keep: int) -> list[str]:
    """The newest `keep` lines; the file is trimmed to that once it grows past twice that."""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    if len(lines) > 2 * keep:
        with contextlib.suppress(OSError):
            write_bytes(path, ("\n".join(lines[-keep:]) + "\n").encode())
    return lines[-keep:]


def when_text(ts: float | None) -> str:
    """'Sun 12:45 PM' (Eastern), the way the phone shows times."""
    return datetime.fromtimestamp(ts, ET).strftime("%a %-I:%M %p") if ts else "never"


def dur_text(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 90:
        return f"{s}s"
    return f"{round(s / 60)} min" if s < 5400 else f"{s / 3600:.1f} h"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _games_text(games: list) -> str:
    """'DAL @ WSH is' / '3 games are' — the start of a plain-English reason."""
    return f"{games[0].matchup} is" if len(games) == 1 else f"{len(games)} games are"


# ============================================================================ schedule math


@dataclass(frozen=True)
class At:
    """A wall-clock time in US/Eastern (DST handled), on some weekdays (0=Mon … 6=Sun; () = daily)."""
    hour: int
    minute: int = 0
    days: tuple[int, ...] = ()

    def on(self, day: date) -> float | None:
        if self.days and day.weekday() not in self.days:
            return None
        return datetime(day.year, day.month, day.day, self.hour, self.minute, tzinfo=ET).timestamp()


def _slots(when: tuple[At, ...], now: float, ahead_days: int = 8) -> list[float]:
    today = datetime.fromtimestamp(now, ET).date()
    days = [today + timedelta(days=i) for i in range(-1, ahead_days + 1)]
    return sorted(t for d in days for a in when if (t := a.on(d)) is not None)


def current_slot(when: tuple[At, ...], now: float, window_s: float) -> float | None:
    """The scheduled time whose start window holds `now` (a job may start up to window_s late)."""
    hits = [t for t in _slots(when, now, ahead_days=0) if t <= now < t + window_s]
    return hits[-1] if hits else None


def next_slot(when: tuple[At, ...], now: float) -> float | None:
    return next((t for t in _slots(when, now) if t > now), None)


# ============================================================================ the scheduler


@dataclass
class JobResult:
    ok: bool | None   # True = worked, False = failed (shown red), None = nothing to do this time
    message: str


@dataclass
class Job:
    """One autonomous chore. `guard` returns why it can't run right now, or None to go ahead."""
    name: str
    label: str
    work: Callable[[], Awaitable[JobResult]] | None   # None = runs elsewhere; status only, via note()
    when: tuple[At, ...] = ()
    window_s: float = 3600.0
    timeout_s: float = 600.0
    retry_s: float = 300.0
    guard: Callable[[], Awaitable[str | None]] | None = None
    group: str | None = None     # jobs in one group never overlap (they all need the sticks)
    attempts: int = 1            # a failed run is retried inside its window up to this many tries


@dataclass
class _JobState:
    running: bool = False
    last_run: float | None = None
    ok: bool | None = None
    message: str = "hasn't run yet"
    done_slot: float | None = None   # the slot that's finished (it ran, or its window closed)
    slot: float | None = None        # the slot we're trying to run now
    tries: int = 0
    not_before: float = 0.0
    waiting: str = ""                # last reason it couldn't run (logged once per slot)
    next_run: float | None = None    # jobs that run elsewhere tell us this via note()


class Scheduler:
    """Runs jobs at their times: one at a time per job (and per group), each inside its time box,
    every outcome appended to data/autonomy.jsonl. Restarts pick up from that log, so a job that
    already ran in this window doesn't run twice."""

    def __init__(self, jobs: list[Job], log_path: Path, clock: Callable[[], float] = time.time):
        self.jobs = {j.name: j for j in jobs}
        self.state = {j.name: _JobState() for j in jobs}
        self.log_path = log_path
        self.clock = clock
        self.tasks: set[asyncio.Task] = set()
        self._restore()

    def _restore(self) -> None:
        for line in tail_lines(self.log_path, 2000):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            st = self.state.get(rec.get("job"))
            if st is None or rec.get("outcome") == "waiting":
                continue
            if rec.get("outcome") != "missed":
                st.last_run, st.ok = rec.get("t"), rec.get("ok")
            st.message = rec.get("message", st.message)
            if rec.get("slot"):
                st.done_slot = max(st.done_slot or 0.0, float(rec["slot"]))

    async def tick(self) -> list[asyncio.Task]:
        """Start whatever is due and safe. Returns the tasks it started (tests await them)."""
        now, started = self.clock(), []
        for job in self.jobs.values():
            if job.work is None or not job.when:
                continue
            st = self.state[job.name]
            slot = current_slot(job.when, now, job.window_s)
            self._close(job, st, slot)
            if slot is None or slot == st.done_slot or st.running or now < st.not_before:
                continue
            if st.slot != slot:
                st.slot, st.tries, st.waiting = slot, 0, ""
            why = self._group_busy(job) or await self._check(job)
            if why:
                self._wait(job, st, why, now)
                continue
            if st.running:   # a "run now" started it while the safety check was awaited
                continue
            started.append(self._launch(job, slot))
        return started

    def _close(self, job: Job, st: _JobState, slot: float | None) -> None:
        """A window ended without a run: log it once as missed, with the last reason it waited."""
        if st.slot is None or st.slot == slot or st.running or st.done_slot == st.slot:
            return
        if st.tries == 0:
            st.message = f"Didn't run {when_text(st.slot)}: {st.waiting or 'no safe moment'}"
            self._log(job, "missed", None, st.message, st.slot)
        st.done_slot, st.slot = st.slot, None

    async def _check(self, job: Job) -> str | None:
        if job.guard is None:
            return None
        try:
            return await job.guard()
        except Exception as e:  # can't tell whether it's safe -> it isn't
            log.exception("safety check for %s failed", job.name)
            return f"couldn't check whether it's safe ({type(e).__name__})"

    def _group_busy(self, job: Job) -> str | None:
        if not job.group:
            return None
        other = next((j for j in self.jobs.values() if j is not job and j.group == job.group
                      and self.state[j.name].running), None)
        return f"waiting for the {other.label.lower()} to finish" if other else None

    def _wait(self, job: Job, st: _JobState, why: str, now: float) -> None:
        st.not_before = now + job.retry_s
        st.message = f"Waiting: {why}"
        if st.waiting != why:  # each new reason is logged once, not on every re-check
            st.waiting = why
            self._log(job, "waiting", None, why, st.slot)

    def _launch(self, job: Job, slot: float | None) -> asyncio.Task:
        self.state[job.name].running = True   # set before the task starts: single-flight from here on
        task = asyncio.create_task(self._run(job, slot))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def _run(self, job: Job, slot: float | None) -> JobResult:
        st = self.state[job.name]
        st.running = True
        if slot is not None:
            st.tries += 1
        t0, m0 = self.clock(), time.monotonic()
        try:
            res = await asyncio.wait_for(job.work(), job.timeout_s)
            outcome = "ok" if res.ok else ("nothing" if res.ok is None else "failed")
        except asyncio.TimeoutError:
            res, outcome = JobResult(False, f"Stopped after {dur_text(job.timeout_s)} — it took too long"), "timeout"
        except Exception as e:  # a job's bug never takes the app down
            log.exception("autonomy job %s crashed", job.name)
            res, outcome = JobResult(False, f"Crashed ({type(e).__name__}: {e})"[:200]), "crashed"
        finally:
            st.running = False
        st.last_run, st.ok, st.message = t0, res.ok, res.message
        if slot is not None:
            if res.ok is not False or st.tries >= job.attempts:
                st.done_slot = slot
            else:
                st.not_before = self.clock() + job.retry_s
        self._log(job, outcome, res.ok, res.message, slot, took=time.monotonic() - m0)
        return res

    async def run_now(self, name: str, force: bool = False) -> JobResult:
        """Run a job right away (phone button, command line). Still single-flight and time-boxed;
        its safety check applies unless force=True (the job's own per-stick rules always apply)."""
        job = self.jobs.get(name)
        if job is None:
            return JobResult(False, f"No job called {name!r}")
        if job.work is None:
            return JobResult(None, f"{job.label} runs by itself")
        if self.state[name].running:
            return JobResult(None, f"{job.label} is already running")
        why = self._group_busy(job) or await self._check(job)
        why = why or self._group_busy(job)
        if why or self.state[name].running:   # re-checked: the safety check awaited
            return JobResult(None, f"Not now: {why or 'it just started'}")
        self.state[name].running = True
        return await self._run(job, None)

    def note(self, name: str, ok: bool | None, message: str, next_run: float | None = None) -> None:
        """Status from a job that runs elsewhere (the engine's 5-minute game-link refresh).
        Logged only when it flips between working and failing, not every 5 minutes."""
        st = self.state.get(name)
        if st is None:
            return
        flipped = st.last_run is None or ok != st.ok
        st.last_run, st.ok, st.message, st.next_run = self.clock(), ok, message, next_run
        if flipped:
            self._log(self.jobs[name], "ok" if ok else ("nothing" if ok is None else "failed"), ok, message, None)

    def status(self) -> list[dict]:
        now = self.clock()
        return [{"job": j.name, "label": j.label, "last_run": s.last_run, "ok": s.ok, "message": s.message,
                 "next_run": self._next(j, s, now), "running": s.running}
                for j, s in ((j, self.state[j.name]) for j in self.jobs.values())]

    def _next(self, job: Job, st: _JobState, now: float) -> float | None:
        if st.running:
            return None
        if not job.when:
            return st.next_run
        slot = current_slot(job.when, now, job.window_s)
        if slot is not None and slot != st.done_slot:
            return max(now, st.not_before)
        return next_slot(job.when, now)

    def _log(self, job: Job, outcome: str, ok: bool | None, message: str, slot: float | None,
             took: float | None = None) -> None:
        rec: dict[str, Any] = {"t": round(self.clock(), 1), "job": job.name, "outcome": outcome, "ok": ok,
                               "message": message}
        if slot:
            rec["slot"] = slot
        if took is not None:
            rec["took_s"] = round(took, 1)
        append_jsonl(self.log_path, rec)
        log.log(logging.WARNING if ok is False else logging.INFO, "%s: %s — %s", job.label, outcome, message)


# ============================================================================ the home network


def local_network() -> ipaddress.IPv4Network | None:
    """This Mac's home subnet from its default route, clamped to the 1,024 addresses around the Mac
    (campus networks are huge). None when offline."""
    try:
        route = subprocess.run(["route", "-n", "get", "default"], capture_output=True, text=True, timeout=5).stdout
        iface = re.search(r"interface: (\S+)", route).group(1)
        ifc = subprocess.run(["ifconfig", iface], capture_output=True, text=True, timeout=5).stdout
        m = re.search(r"inet (\d+\.\d+\.\d+\.\d+) netmask (0x[0-9a-f]+)", ifc)
        prefix = bin(int(m.group(2), 16)).count("1")
        return ipaddress.ip_network(f"{m.group(1)}/{max(prefix, 22)}", strict=False)
    except (OSError, subprocess.SubprocessError, AttributeError, ValueError):
        return None


async def port_open(host: str, port: int, timeout: float = 0.6) -> bool:
    try:
        _, w = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except (OSError, asyncio.TimeoutError):
        return False
    w.close()
    return True


async def scan_port(net: ipaddress.IPv4Network, port: int, conc: int = 128) -> list[str]:
    """Addresses on the home network with `port` open (~5 s for 1,024 addresses)."""
    sem = asyncio.Semaphore(conc)

    async def one(h: str) -> str | None:
        async with sem:
            return h if await port_open(h, port) else None
    return [h for h in await asyncio.gather(*(one(str(h)) for h in list(net.hosts())[:1024])) if h]


async def samsung_info(ip: str, timeout: float = 3.0, transport: httpx.AsyncBaseTransport | None = None
                       ) -> dict | None:
    """The TV's description from its own status page (no pairing needed), or None when it doesn't
    answer — which almost always just means it's off."""
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as c:
            r = await c.get(f"http://{ip}:8001/api/v2/")
        dev = r.json().get("device") if r.status_code == 200 else None
    except Exception:  # off, asleep, not a Samsung, garbage reply: all mean "no answer"
        return None
    if not isinstance(dev, dict):
        return None
    return {"ip": ip, "name": dev.get("name"), "model": dev.get("modelName"), "os": dev.get("OS"),
            "token_auth": dev.get("TokenAuthSupport"), "power": dev.get("PowerState"),
            "wifi_mac": norm_mac(dev.get("wifiMac"))}


class Net:
    """Everything that touches the home network, in one object tests swap for a fake."""

    async def adb_ok(self) -> bool:
        return await adb_available()

    async def network(self) -> ipaddress.IPv4Network | None:
        return await asyncio.to_thread(local_network)

    async def scan(self, net: ipaddress.IPv4Network | None, port: int) -> list[str]:
        return await scan_port(net, port) if net is not None else []

    async def arp(self) -> dict[str, str]:
        return await asyncio.to_thread(arp_table)

    async def samsung(self, ip: str) -> dict | None:
        return await samsung_info(ip)

    async def find_mac(self, mac: str, subnet: Any, sweep: bool = True, prefer: str = "") -> str | None:
        """Where a device with this MAC is right now (ARP, then one UDP nudge of the home /24)."""
        return await find_by_mac(mac, subnet, sweep=sweep, prefer=prefer)

    def stick(self, host: str) -> Stick:
        return Stick(host)


async def stick_identity(stick: Stick) -> dict:
    """Serial number, model, and the name you gave the stick. The serial survives address changes,
    so it's how a stick is recognized after the router moves it."""
    r = await stick.shell("getprop ro.serialno; getprop ro.product.model; settings get global device_name",
                          timeout=6)
    if not r.ok:
        return {}
    lines = [x.strip() for x in r.out.splitlines()] + ["", "", ""]
    return {"serial": lines[0], "model": lines[1], "device_name": "" if lines[2] == "null" else lines[2]}


async def stick_facts(stick: Stick) -> dict:
    """Is it awake? Is HDMI-CEC on? Is the TV showing it right now? Each is None if it won't say."""
    r = await stick.shell("dumpsys power | grep -m1 'mWakefulness='; settings get global hdmi_control_enabled; "
                          "dumpsys hdmi_control | grep -m1 -i 'isactivesource'; true", timeout=8)
    out = r.out if r.ok else ""
    wake = re.search(r"mWakefulness=(\w+)", out)
    cec = next((ln.strip() for ln in out.splitlines() if ln.strip() in ("0", "1", "null")), None)
    active = re.search(r"isactivesource\W*(true|false)", out, re.I)
    return {"awake": None if not wake else wake.group(1).lower() in ("awake", "dreaming"),
            "cec": None if cec is None else cec != "0",   # "null" = never changed = on (Fire OS default)
            "active": None if not active else active.group(1).lower() == "true"}


# ============================================================================ following devices


def _ip(host: str) -> str:
    return (host or "").rsplit(":", 1)[0]


def _entry(d: dict, host: str) -> dict | None:
    return next((e for e in d.get("sticks") or [] if e.get("host") == host), None)


def _name(e: dict) -> str:
    """How the owner knows a device: the name they gave it, else its model, else its address."""
    return e.get("device_name") or e.get("name") or e.get("model") or _ip(e.get("host", "")) or "a stick"


def _stick_name(s: Stick) -> str:
    return s.name or s.model or _ip(s.host)


def match_kind(saved: dict, seen: dict) -> str | None:
    """Is `seen` the stick we saved? "strong": same serial (or same MAC when no serial is known);
    "weak": same model and same name, trusted only when exactly one device matches; else None.
    A different serial is a definite no, whatever else matches."""
    for key in ("serial", "mac"):
        if saved.get(key) and seen.get(key):
            return "strong" if saved[key] == seen[key] else None
    same = bool(saved.get("model") and saved.get("device_name")) and \
        (saved["model"], saved["device_name"]) == (seen.get("model"), seen.get("device_name"))
    return "weak" if same else None


def same_tv(saved: dict, seen: dict) -> bool:
    """Same TV? Its Wi-Fi MAC when known on both sides, else its saved IP address."""
    a, b = norm_mac(saved.get("wifi_mac")), norm_mac(seen.get("wifi_mac"))
    if a and b:
        return a == b
    # Two identical TVs are common. A model name never proves device identity.
    return bool(saved.get("ip")) and saved.get("ip") == seen.get("ip")


def _tv_macs(tv: dict) -> list[str]:
    """The TV's MACs to look for: its Wi-Fi MAC from its status page and/or one you saved as "mac"."""
    return list(dict.fromkeys(m for m in (norm_mac(tv.get("mac")), norm_mac(tv.get("wifi_mac"))) if m))


def _point_tv(controller: Any, ip: str) -> None:
    tv = getattr(controller, "tv", None)
    if tv is None:
        return
    tv.host = ip
    if hasattr(tv, "_tv"):
        tv._tv = None   # drop the remote-control connection that's bound to the old address


def _index(controller: Any, stick: Stick) -> int | None:
    """Where this exact Stick object sits in the controller's list (None if it's been replaced)."""
    return next((i for i, x in enumerate(getattr(controller, "sticks", [])) if x is stick), None)


def _forget_playing(controller: Any, index: int | None) -> None:
    """What this stick shows is unknown now (it was gone, or we just tested it): make the controller
    re-tune before trusting it, instead of thinking it's "already on it"."""
    if index is None:
        return
    if hasattr(controller, "_forget"):
        stick = controller.sticks[index]
        controller._forget(stick)
        stick.tuned = None
        return
    if index == getattr(controller, "active", 0):
        controller.on_target = None
    else:
        controller.hidden_target, controller.hidden_ready = None, False


@dataclass
class Backoff:
    """Spacing between network scans: 10 min, doubling to 1 h while nothing turns up, so a stick
    that's simply unplugged doesn't mean a network scan every few minutes forever."""
    first: float = SCAN_FIRST_S
    cap: float = SCAN_MAX_S
    wait: float = 0.0
    next_at: float = 0.0

    def ready(self, now: float) -> bool:
        return now >= self.next_at

    def failed(self, now: float) -> None:
        self.wait = min(self.cap, max(self.first, self.wait * 2))
        self.next_at = now + self.wait

    def found(self, now: float) -> None:
        self.wait, self.next_at = 0.0, now


class DeviceHealer:
    """Follows the sticks and the TV when the router hands them new addresses (DHCP), and fills in what
    setup couldn't learn (serial, MAC, HDMI input) while they're reachable. Without this, a router
    reboot quietly costs you TV control until someone re-runs the setup check."""

    LEARN_EVERY_S = 1800.0

    def __init__(self, hooks: Any, path: Path, net: Net, clock: Callable[[], float] = time.time):
        self.hooks, self.path, self.net, self.clock = hooks, path, net, clock
        self.offline_since: dict[str, float] = {}
        self.sticks_scan, self.tv_scan = Backoff(), Backoff()
        self.tv_lost_since: float | None = None
        self.tv_checked = -math.inf
        self.tv_saved = False
        self.tv_power: str | None = None          # "on" | "standby" | None (didn't answer)
        self.strangers: dict[str, float] = {}     # host -> until: not ours, don't connect (or prompt) again
        self.learned: dict[str, float] = {}       # host -> last time we tried to learn its identity
        self.notes: list[str] = []
        self.last_check: float | None = None
        self.tv_on_lan = False                    # the TV's MAC is on the network, but its control port is shut
        self._busy = False
        self._dirty = False

    @property
    def controller(self) -> Any:
        return self.hooks.controller

    async def check(self) -> list[str]:
        """One pass (every ~15 s). Cheap unless something is missing. Returns notes about changes."""
        if self._busy:
            return []
        self._busy = True
        try:
            return await asyncio.wait_for(self._check(), HEAL_TIMEOUT_S)
        except asyncio.TimeoutError:
            log.warning("device check took over %.0fs; trying again next pass", HEAL_TIMEOUT_S)
            return []
        finally:
            self._busy = False

    @property
    def network(self) -> Any:
        """The app's NetWatch (redzone/network.py), or None (tests, older engines): then behave as before."""
        return getattr(self.hooks, "network", None)

    def _net_state(self) -> Any:
        nw = self.network
        return getattr(nw, "state", None) if nw is not None else None

    def _home_subnet(self) -> ipaddress.IPv4Network | None:
        st = self._net_state()
        if st is None or st.where != "home" or not st.home_subnet:
            return None
        try:
            return ipaddress.ip_network(st.home_subnet, strict=False)
        except ValueError:
            return None

    async def _check(self) -> list[str]:
        d = read_devices(self.path)
        nw = self.network
        st = await nw.refresh() if nw is not None else None
        if st is not None and st.away:
            # Not a fault: nothing here can answer. Don't scan someone else's network, and start the
            # "missing for 2 minutes" clocks fresh when we're back.
            self.offline_since.clear()
            self.tv_lost_since, self.tv_power, self.tv_on_lan = None, None, False
            self.last_check = self.clock()
            return []
        notes = await self._learn(d)
        if st is not None and st.routes_broken:
            # The Mac sends home traffic into a tunnel: a scan would find nothing and back off for an hour.
            self.last_check = self.clock()
            return notes
        notes += await self._follow_sticks(d)
        notes += await self._follow_tv(d)
        if nw is not None:
            up = [s.host for s in self.controller.sticks if s.state == "connected"]
            if nw.learn_into(d, up):
                self._dirty = True
                self._save(d)
                notes.append(f"Learned your home network ({d['home'].get('subnet')}) and its router")
        for n in notes:
            log.info("Devices: %s", n)
        self.notes = (notes + self.notes)[:5]
        self.last_check = self.clock()
        return notes

    def _save(self, d: dict) -> None:
        if self._dirty:
            write_json(self.path, d)
            self._dirty = False

    # --- sticks ---------------------------------------------------------------------

    async def _learn(self, d: dict) -> list[str]:
        """Record what identifies each connected stick (serial, MAC, HDMI input, apps) while it's
        reachable: that's what lets us recognize it later at a new address."""
        notes, arp, now = [], None, self.clock()
        for s in list(self.controller.sticks):
            e = _entry(d, s.host)
            if e is None or s.state != "connected" or self._complete(e) or \
                    now - self.learned.get(s.host, -math.inf) < self.LEARN_EVERY_S:
                continue
            self.learned[s.host] = now
            arp = arp if arp is not None else await self.net.arp()
            seen = {**await stick_identity(s), "mac": arp.get(_ip(s.host), "")}
            seen = {"name": s.name, "os": s.os, "packages": s.packages, **seen, "model": seen.get("model") or s.model}
            if not e.get("hdmi"):
                seen["hdmi"] = await s.hdmi_port()
            new = {k: v for k, v in seen.items() if v and not e.get(k)}
            if not new:
                continue
            e.update(new)
            self._dirty = True
            if new.get("hdmi"):
                self.controller.hdmi.setdefault(s.host, new["hdmi"])
                notes.append(f"{_name(e)} is on TV input {new['hdmi']}")
        self._save(d)
        return notes

    @staticmethod
    def _complete(e: dict) -> bool:
        return all(e.get(k) for k in ("serial", "mac", "hdmi", "packages"))

    def _lost(self, s: Stick, now: float) -> bool:
        """Missing for 2+ minutes. "Waiting for approval" isn't missing: that address is right."""
        if s.state in ("connected", "unauthorized"):
            self.offline_since.pop(s.host, None)
            return False
        return now - self.offline_since.setdefault(s.host, now) >= LOST_AFTER_S

    async def _follow_sticks(self, d: dict) -> list[str]:
        """Sticks missing 2+ minutes: scan for adb (port 5555) and follow each to its new address."""
        now = self.clock()
        lost = [s for s in list(self.controller.sticks) if self._lost(s, now)]
        if not lost or not self.sticks_scan.ready(now) or not getattr(self.controller, "adb_ok", True):
            return []
        mine = {s.host for s in self.controller.sticks}
        by_mac, all_by_mac = await self._sticks_by_mac(d, lost, mine)
        subnet = self._home_subnet()
        # Every lost stick found by its MAC: no port scan needed. Otherwise scan the HOME network (not
        # whatever /22 the Mac is on) for adb, and try the MAC hits first.
        hosts = by_mac if all_by_mac else \
            list(dict.fromkeys(by_mac + await self.net.scan(subnet or await self.net.network(), 5555)))
        arp = await self.net.arp()
        cands = [f"{h}:5555" for h in hosts if f"{h}:5555" not in mine and self.strangers.get(f"{h}:5555", 0) <= now]
        notes, moved = [], 0
        for s in lost:
            hit = await self._find(_entry(d, s.host) or {}, cands, arp, notes)
            if hit:
                cands.remove(hit[0].host)
                notes.append(self._rehome(d, s, *hit))
                moved += 1
        (self.sticks_scan.found if moved else self.sticks_scan.failed)(now)
        self._save(d)
        return notes

    async def _sticks_by_mac(self, d: dict, lost: list[Stick], mine: set[str]) -> tuple[list[str], bool]:
        """Addresses the lost sticks' saved MACs have right now: ARP first, then (once) a UDP nudge of the
        home /24. Returns (new addresses, whether every lost stick was found this way)."""
        subnet = self._home_subnet()
        macs = [norm_mac((_entry(d, s.host) or {}).get("mac")) for s in lost]
        if subnet is None or not all(macs):
            return [], False
        found: list[str] = []
        swept = False
        for s, mac in zip(lost, macs):
            ip = await self.net.find_mac(mac, subnet, sweep=False)
            if ip is None and not swept:
                swept = True
                ip = await self.net.find_mac(mac, subnet, sweep=True)
            if ip is None or f"{ip}:5555" in mine:
                return found, False   # not on the network, or it kept its address (adb itself is down)
            found.append(ip)
        return found, True

    async def _find(self, saved: dict, cands: list[str], arp: dict, notes: list[str]) -> tuple | None:
        """Connect to candidates — the one with the saved MAC first — until one proves to be this stick."""
        if not (saved.get("serial") or saved.get("mac") or (saved.get("model") and saved.get("device_name"))):
            return None   # nothing to recognize it by (never connected since setup): don't poke strangers
        mac, now, weak = saved.get("mac"), self.clock(), []
        for host in cands:
            # Never prompt an unfamiliar device while trying to recover ours.
            if not mac or norm_mac(arp.get(_ip(host))) != norm_mac(mac):
                continue
            st = self.net.stick(host)
            if not await st.connect():
                self._stranger(host, st, now, notes)
                continue
            seen = {**await stick_identity(st), "mac": arp.get(_ip(host), "")}
            kind = match_kind(saved, seen)
            if kind == "strong":
                return st, seen
            if kind == "weak":
                weak.append((st, seen))
            else:
                self.strangers[host] = now + 3600
        return weak[0] if len(weak) == 1 else None

    def _stranger(self, host: str, st: Stick, now: float, notes: list[str]) -> None:
        """Couldn't connect. One asking for approval may be someone else's device (don't keep popping
        the prompt on it) — or yours after a reset, so say so once."""
        if st.state == "unauthorized" and host not in self.strangers:
            notes.append(f"A device at {_ip(host)} is asking to allow this Mac — if it's your Fire TV, "
                         "approve the prompt on the TV")
        self.strangers[host] = now + 86400

    def _rehome(self, d: dict, old: Stick, new: Stick, seen: dict) -> str:
        """Point the controller and devices.json at the new address. The new Stick takes the old one's
        place in the list, so its role (on screen / standby) and its HDMI input carry over."""
        c = self.controller
        for attr in ("name", "model", "os"):
            setattr(new, attr, getattr(new, attr) or getattr(old, attr))
        new.packages = new.packages or dict(old.packages)
        i = _index(c, old)
        if i is not None:
            c.sticks[i] = new
            if old.host in c.hdmi:
                c.hdmi[new.host] = c.hdmi.pop(old.host)
            _forget_playing(c, i)   # gone 2+ minutes (likely rebooted): re-tune before trusting it
        e = _entry(d, old.host)
        if e is None:
            e = {"name": old.name, "model": old.model, "os": old.os, "packages": dict(old.packages)}
            d.setdefault("sticks", []).append(e)
        e.update({k: v for k, v in seen.items() if v})
        e["host"] = new.host
        self._dirty = True
        self.offline_since.pop(old.host, None)
        return f"{_name(e)} moved from {_ip(old.host)} to {_ip(new.host)} — following it"

    # --- the TV ---------------------------------------------------------------------

    async def _follow_tv(self, d: dict) -> list[str]:
        """Read the TV's status page every 2 min (power state; still at this address?). Gone 2+ minutes:
        scan port 8001 for it. Usually it's just off and the scan finds nothing, hence the backoff."""
        now, tv = self.clock(), d.get("tv") or {}
        macs = _tv_macs(tv)
        self.tv_saved = bool(tv.get("ip"))
        if not (self.tv_saved or macs) or now - self.tv_checked < TV_CHECK_S:
            return []
        self.tv_checked = now
        info = await self.net.samsung(tv["ip"]) if self.tv_saved else None
        if info and same_tv(tv, info):
            self.tv_power, self.tv_lost_since, self.tv_on_lan = info.get("power") or "on", None, False
            self._learn_tv(d, tv, info)
            return []
        self.tv_power = None
        self.tv_lost_since = self.tv_lost_since or now
        lost = now - self.tv_lost_since >= LOST_AFTER_S
        moved = await self._tv_by_mac(d, tv, macs, sweep=lost and self.tv_scan.ready(now), now=now)
        if moved is not None:
            return moved
        if not self.tv_saved or not lost or not self.tv_scan.ready(now):
            return []
        hosts = await self.net.scan(self._home_subnet() or await self.net.network(), 8001)
        found = [i for i in [await self.net.samsung(h) for h in hosts] if i]
        return self._move_tv(d, tv, found, now)

    async def _tv_by_mac(self, d: dict, tv: dict, macs: list[str], sweep: bool, now: float) -> list[str] | None:
        """The TV's MAC at a new address -> follow it there, even when its control port (8001) is shut
        (that's how a wrong saved IP gets corrected). None when there's nothing to do."""
        subnet = self._home_subnet()
        if subnet is None or not macs:
            return None
        ip = None
        for mac in macs:
            ip = await self.net.find_mac(mac, subnet, sweep=sweep, prefer=tv.get("ip", ""))
            if ip:
                break
        if sweep and not ip and not tv.get("ip"):
            self.tv_scan.failed(now)   # with a saved address, the port-8001 scan below backs off for both
        self.tv_on_lan = bool(ip) and ip == tv.get("ip")
        if not ip or ip == tv.get("ip"):
            return None
        old, tv["ip"] = tv.get("ip"), ip
        d["tv"] = tv
        self._dirty = True
        info = await self.net.samsung(ip)
        if info and same_tv(tv, info):
            self.tv_power, self.tv_lost_since = info.get("power") or "on", None
            self._learn_tv(d, tv, info)
        self._save(d)
        self.tv_saved = True
        _point_tv(self.controller, ip)
        self.tv_scan.found(now)
        return [f"The TV moved from {old} to {ip} (found by its MAC) — following it" if old
                else f"Found the TV at {ip} by its MAC"]

    def _move_tv(self, d: dict, tv: dict, found: list[dict], now: float) -> list[str]:
        hits = [i for i in found if same_tv(tv, i)]
        if len(hits) != 1 or hits[0]["ip"] == tv.get("ip"):
            self.tv_scan.failed(now)
            return []
        old, tv["ip"] = tv.get("ip"), hits[0]["ip"]
        d["tv"] = tv
        self._learn_tv(d, tv, hits[0])
        self._dirty = True
        self._save(d)
        _point_tv(self.controller, tv["ip"])
        self.tv_scan.found(now)
        self.tv_power, self.tv_lost_since = hits[0].get("power") or "on", None
        return [f"The TV moved from {old} to {tv['ip']} — following it"]

    def _learn_tv(self, d: dict, tv: dict, info: dict) -> None:
        if not tv.get("wifi_mac") and info.get("wifi_mac"):
            tv["wifi_mac"] = info["wifi_mac"]
            d["tv"] = tv
            self._dirty = True
            self._save(d)

    async def tv_power_now(self) -> tuple[bool, str | None]:
        """(is a TV saved?, "on" | "standby" | None when it doesn't answer — almost always: it's off)."""
        tv = read_devices(self.path).get("tv") or {}
        if not tv.get("ip"):
            return False, None
        info = await self.net.samsung(tv["ip"])
        if info and same_tv(tv, info):
            return True, info.get("power") or "on"
        return True, None

    def summary(self) -> tuple[bool | None, str]:
        """One line for the phone: what's connected, what's missing, what changed last."""
        sticks, now = list(self.controller.sticks), self.clock()
        st = self._net_state()
        if st is not None and st.away:
            return None, st.message   # not a fault, and not "offline"
        if st is not None and st.routes_broken:
            return False, st.message
        if not sticks:
            return None, "No Fire TV sticks saved yet"
        up = [s for s in sticks if s.state == "connected"]
        parts = [f"{len(up)} of {_plural(len(sticks), 'stick')} connected"]
        for s in sticks:
            if s.state == "unauthorized":
                parts.append(f"{_stick_name(s)}: approve the prompt on the TV")
            elif s.host in self.offline_since:
                parts.append(f"{_stick_name(s)} offline {dur_text(now - self.offline_since[s.host])}")
        # Dry-run deliberately never probes devices. Saved configuration is still
        # known locally; an unperformed network check must not erase that fact.
        saved_tv = bool((read_devices(self.path).get("tv") or {}).get("ip"))
        if saved_tv:
            if not math.isfinite(self.tv_checked):
                parts.append("TV saved; connection not checked")
            else:
                parts.append({"on": "TV on", "standby": "TV in standby"}.get(
                    self.tv_power or "", "TV on the network, but its control port isn't answering"
                    if self.tv_on_lan else "TV saved; not answering"))
        else:
            parts.append("no TV saved yet")
        if self.notes:
            parts.append(self.notes[0])
        return len(up) == len(sticks), " · ".join(parts)


# ============================================================================ first run


def _is_fire_tv(model: str, packages: dict) -> bool:
    """Amazon's Fire TV models all start with AFT. Anything else with adb on (a phone, someone's
    Android TV) isn't ours to control, unless it has the streaming apps we drive."""
    return (model or "").upper().startswith("AFT")


async def discover(net: Net, path: Path) -> tuple[dict, list[str]]:
    """Find the sticks and the TV, save them to devices.json (atomically), and return what the owner
    still needs to do, in plain English. Safe to repeat: saved entries are kept, new ones added."""
    d = read_devices(path)
    netw = await net.network()
    if netw is None:
        return d, [NO_NETWORK]
    steps = await _discover_sticks(net, netw, d) if await net.adb_ok() else [INSTALL_ADB]
    steps += await discover_tv(net, netw, d)
    write_json(path, d)
    return d, steps


async def _discover_sticks(net: Net, netw: ipaddress.IPv4Network, d: dict) -> list[str]:
    hosts = await net.scan(netw, 5555)
    arp = await net.arp() if hosts else {}
    steps: list[str] = []
    for ip in hosts:
        steps += await _add_stick(net, d, ip, arp)
    if not d.get("sticks"):
        steps.insert(0, ENABLE_ADB)
    # Controllable sticks first: the controller uses the first ones it can drive.
    d["sticks"] = sorted(d.get("sticks") or [], key=lambda e: "Vega" in (e.get("os") or ""))
    return steps


async def _add_stick(net: Net, d: dict, ip: str, arp: dict) -> list[str]:
    host = f"{ip}:5555"
    st = net.stick(host)
    ok = await st.connect()
    e = _entry(d, host)
    if st.state == "unauthorized":
        if e is None:   # saved now, so the controller connects by itself the moment you approve
            d.setdefault("sticks", []).append({"host": host, "name": "", "model": "", "os": "",
                                               "packages": {}, "hdmi": None})
        return [APPROVE.format(ip=ip)]
    if not ok:
        return []
    await st.identify()
    seen = await stick_identity(st)
    if not _is_fire_tv(seen.get("model") or st.model, st.packages):
        log.info("Setup: skipped %s at %s (not a Fire TV)", seen.get("model") or "a device", ip)
        return []
    if e is None:
        e = {"host": host}
        d.setdefault("sticks", []).append(e)
    hdmi = e.get("hdmi") or await st.hdmi_port()
    e.update({"name": st.name, "model": st.model or seen.get("model", ""), "os": st.os, "packages": st.packages,
              "controllable": "Vega" not in st.os, "hdmi": hdmi, "serial": seen.get("serial", ""),
              "device_name": seen.get("device_name", ""), "mac": arp.get(ip, "")})
    if "Vega" in st.os:
        return [VEGA.format(name=_name(e))]
    return [] if hdmi else [NO_HDMI.format(name=_name(e))]


async def discover_tv(net: Net, netw: ipaddress.IPv4Network, d: dict) -> list[str]:
    if (d.get("tv") or {}).get("ip"):
        return []
    found = [info for ip in await net.scan(netw, 8001) if (info := await net.samsung(ip))]
    if len(found) > 1:
        return ["More than one Samsung TV was found. Select the intended TV in the setup checker."]
    if len(found) == 1:
        d["tv"] = info = found[0]
        return [TV_FOUND.format(name=info.get("name") or "Samsung TV", ip=info["ip"])]
    return [TV_MISSING]


# ============================================================================ quiet rules


def on_screen(active: bool | None, hidden: bool) -> bool | None:
    """Is the TV showing this stick? HDMI-CEC's answer wins; otherwise the controller's standby role
    (the hidden stick isn't on screen); otherwise unknown."""
    if active is not None:
        return active
    return False if hidden else None


def quiet_plan(tv_off: bool, showing: bool | None, awake: bool | None, cec_on: bool | None,
               may_wake: bool = False) -> tuple[str, str]:
    """How much of a test a stick can take without anyone noticing: "full" (play something),
    "light" (look, don't touch) or "skip" (leave it alone), plus the reason.

    Never touch a stick the TV may be showing. Never wake a sleeping stick while HDMI-CEC is on:
    waking it can turn the TV on, or switch the TV over to it (that's what One Touch Play does)."""
    if not tv_off and showing is not False:
        return "skip", "the TV is on and may be showing it"
    if awake is None:
        return "light", "couldn't tell whether it's asleep"
    if not awake and cec_on is not False and not may_wake:
        return "light", "it's asleep, and waking it could turn your TV on"
    return "full", ""


def important_now(engine: Any, busy_score: float = 75.0) -> str | None:
    """Is something on that the owner would hate to have delayed? A Sunday Ticket scan on the hidden
    stick holds the controller for ~30 s per batch, which delays any switch until the batch ends."""
    if getattr(engine.decider, "replaying", None):
        return "a catch-up replay is playing"
    for gid, s in list(engine.scored.items()):
        g = engine.merger.games.get(gid)
        if g is not None and g.phase == Phase.LIVE and (s.lock or s.pre_moment or s.score >= busy_score):
            return f"{g.matchup} is at a big moment"
    return None


async def _other_app_playing(stick: Stick | None) -> str | None:
    """Single stick: is the owner watching something we didn't put on (Netflix, a movie…)?"""
    if stick is None:
        return None
    pb = await stick.playback()
    pkg = pb.get("pkg") or ""
    if pb.get("state") == "playing":
        return "the stick is playing video; an automatic scan would interrupt it"
    return None


# ============================================================================ jobs: helpers


@dataclass
class AppCheck:
    ok: bool | None
    message: str      # reads after the app's name: "YouTube TV played in 6s"
    version: str = ""


@dataclass
class DayResult:
    league: str
    day: str
    finished: int = 0
    saved: int = 0
    error: str = ""


def espn_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0), follow_redirects=True,
                             headers={"User-Agent": "Mozilla/5.0 RedZoneEverything/1.0"})


async def _fetch(client: httpx.AsyncClient, url: str, params: dict) -> tuple[bytes | None, str]:
    """(body, "") or (None, why). Never raises: a feed hiccup just means "try again later"."""
    try:
        r = await client.get(url, params=params)
    except (httpx.HTTPError, OSError) as e:
        return None, f"no answer ({type(e).__name__})"
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    if len(r.content) > MAX_FEED_BYTES:
        return None, "reply too large"
    return r.content, ""


def _finished(body: bytes | None) -> list[dict]:
    try:
        evs = (json.loads(body) or {}).get("events") or [] if body else []
    except (ValueError, AttributeError):
        return []
    return [e for e in evs if isinstance(e, dict) and e.get("id")
            and ((e.get("status") or {}).get("type") or {}).get("state") == "post"]


def _is_json(body: bytes) -> bool:
    try:
        json.loads(body)
        return True
    except ValueError:
        return False


def merge_scoreboard(path: Path, events: list[dict]) -> None:
    """Add finished events to that day's scoreboard file without dropping any it already lists
    (the season downloader may have saved more of that day's games)."""
    old = read_json(path, {})
    by_id = {e.get("id"): e for e in (old.get("events") or [] if isinstance(old, dict) else [])
             if isinstance(e, dict)}
    by_id.update({e["id"]: e for e in events})
    write_json(path, {"events": list(by_id.values())}, compact=True)


async def download_day(client: httpx.AsyncClient, league: str, day: date, arch: Path, pause_s: float = 0.25,
                       sleep: Callable[[float], Awaitable] = asyncio.sleep) -> DayResult:
    """One league's finished games for one day (scoreboard + play-by-play), skipping files we have.
    Same files and names as scripts/download_recent.py, so the lab reads them unchanged."""
    d = day.strftime("%Y%m%d")
    res = DayResult(league, d)
    base = ESPN_SITE + LEAGUE_PATHS[league]
    body, res.error = await _fetch(client, f"{base}/scoreboard", {"dates": d})
    events = _finished(body)
    if not events:
        return res
    res.finished = len(events)
    folder = arch / league
    merge_scoreboard(folder / f"scoreboard_{d}.json", events)
    for ev in events:
        out = folder / f"summary_{ev['id']}.json"
        if out.exists():
            continue
        got, err = await _fetch(client, f"{base}/summary", {"event": ev["id"]})
        if got and _is_json(got):
            write_bytes(out, got)
            res.saved += 1
        else:
            res.error = err or "summary wasn't JSON"
        await sleep(pause_s)   # polite: one request at a time, with a pause
    return res


def archive_result(results: list[DayResult]) -> JobResult:
    saved = {lg: sum(r.saved for r in results if r.league == lg) for lg in LEAGUE_PATHS}
    total = sum(saved.values())
    failed = sorted({r.league.upper() for r in results if r.error})
    if len(failed) == len(LEAGUE_PATHS) and not total:
        return JobResult(False, f"Couldn't reach ESPN ({next(r.error for r in results if r.error)}) — I'll try again")
    detail = ", ".join(f"{lg.upper()} {n}" for lg, n in saved.items() if n)
    msg = f"Saved {_plural(total, 'new game')} for the lab ({detail})" if total else "No new finished games to save"
    if failed:
        msg += f"; couldn't reach ESPN for {', '.join(failed)}"
    return JobResult(True, msg)


async def run_lab_search(root: Path = ROOT) -> dict:
    """redzone.lab.optimize.search() in its own low-priority process: it's minutes of CPU, and a
    separate process can be killed cleanly by the time box (a thread can't be)."""
    from redzone.governor import background_cmd
    cmd = background_cmd([sys.executable, "-c", LAB_CODE])   # efficiency cores, throttled I/O
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, cwd=str(root), stdin=subprocess.DEVNULL,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except OSError as e:
        return {"error": f"couldn't start the lab ({e})"}
    try:
        out, err = await proc.communicate()
    except asyncio.CancelledError:   # time box hit: don't leave a CPU-burning orphan behind
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        raise
    if proc.returncode != 0:
        lines = err.decode("utf-8", "replace").strip().splitlines() or ["unknown error"]
        return {"error": lines[-1][:200]}
    for line in reversed(out.decode("utf-8", "replace").splitlines()):
        with contextlib.suppress(ValueError):
            return json.loads(line)
    return {"error": "the lab printed no result"}


def recommend(report: dict, now: float) -> dict:
    """Turn a lab run into a plain-English suggestion. It is never applied automatically:
    settings that cut into plays more often than today's are never even suggested."""
    cur, best = report.get("current") or {}, report.get("best") or {}
    cp, bp = cur.get("params") or {}, best.get("params") or {}
    changes = {k: [cp.get(k), v] for k, v in bp.items() if cp.get(k) != v}
    safer = (best.get("mid_play_cuts") or 0) <= (cur.get("mid_play_cuts") or 0)
    gain = (best.get("score") or -math.inf) - (cur.get("score") or -math.inf)
    better = bool(changes) and safer and gain > 0.5
    if better:
        summary = (f"Better settings found: big plays seen {cur.get('big_seen_pct')}% → {best.get('big_seen_pct')}%, "
                   f"dead air {cur.get('dead_air_pct')}% → {best.get('dead_air_pct')}%. Nothing was changed — "
                   "review them in the Lab.")
    elif changes and not safer:
        summary = "The lab found higher-scoring settings, but they cut into plays more often — not suggesting them."
    else:
        summary = "Your current settings are still the best the lab found."
    return {"at": now, "better": better, "summary": summary, "changes": changes, "current": cur, "best": best,
            "slates": report.get("slates"), "applied": False}


def load_verify_codes() -> Callable:
    """redzone.discovery.yttv.verify_codes, imported only when needed: that module is built separately
    and may not exist yet. LookupError = not there (nothing to do); ImportError = there but broken."""
    try:
        mod = importlib.import_module("redzone.discovery.yttv")
    except ModuleNotFoundError as e:
        if (e.name or "").startswith("redzone.discovery"):
            raise LookupError("The YouTube TV channel checker isn't installed yet — nothing to check") from None
        raise ImportError(f"The YouTube TV channel checker couldn't load: {e}") from None
    except Exception as e:  # its own bug must not crash the scheduler
        raise ImportError(f"The YouTube TV channel checker couldn't load: {type(e).__name__}: {e}") from None
    fn = getattr(mod, "verify_codes", None)
    if not callable(fn):
        raise LookupError("The YouTube TV channel checker has no verify_codes() yet")
    return fn


def fit_kwargs(fn: Callable, avail: dict) -> tuple[dict, list[str]]:
    """Pass `fn` only the arguments it asks for, by name; list required ones we can't supply."""
    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return {}, []
    kwargs, missing = {}, []
    for p in params:
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        if avail.get(p.name) is not None:
            kwargs[p.name] = avail[p.name]
        elif p.default is p.empty:
            missing.append(p.name)
    return kwargs, missing


def as_result(out: Any, what: str) -> JobResult:
    """Whatever a plug-in returned (bool, dict, (ok, msg), None…) as a JobResult."""
    if isinstance(out, JobResult):
        return out
    if isinstance(out, bool):
        return JobResult(out, f"{what}: {'all good' if out else 'problems found'}")
    if isinstance(out, dict):
        ok = out.get("ok")
        msg = out.get("message") or out.get("summary") or f"{what}: {'done' if ok is not False else 'problems found'}"
        return JobResult(None if ok is None else bool(ok), str(msg)[:300])
    if isinstance(out, tuple) and len(out) == 2:
        return JobResult(None if out[0] is None else bool(out[0]), str(out[1])[:300])
    return JobResult(True, f"{what}: done" if out is None else f"{what}: {str(out)[:200]}")


def _ref_of(target: str, channels: Any) -> tuple[str, str] | None:
    """Tuner reference for a target, the same way the controller resolves it."""
    app, _, ref = target.partition(":")
    if app == "youtubetv":
        ref = channels.yttv_code(target) or ""
    return (ref, target) if ref else None


# ============================================================================ delay bootstrap


def calibration_history(store: Any) -> dict[str, tuple[float, float, float]]:
    """Latest measured (time, delay, spread) per channel from the app's database, so a restart doesn't
    forget delays it already measured. Read-only; {} when it can't be read."""
    query = getattr(store, "_x", None)   # Store has no public reader for calibrations (yet)
    if query is None:
        return {}
    try:
        rows = query("SELECT target, MAX(ts), mu, sigma FROM calibrations "
                     "WHERE COALESCE(kind, 'tap') = 'tap' GROUP BY target").fetchall()
    except Exception:
        return {}
    return {r[0]: (float(r[1]), float(r[2]), float(r[3])) for r in rows if r[0] and None not in r}


def seed_delays(delays: Any, history: dict[str, tuple[float, float, float]]) -> int:
    """Put measured delays back after a restart. They keep their original time, so the delay book
    treats old ones as less certain (its doubt grows with age); fresh taps still win (median)."""
    n = 0
    for target, (ts, mu, sigma) in history.items():
        d = delays.get(target)
        if d.calibrated_at is not None:
            continue
        d.mu, d.sigma, d.calibrated_at = mu, sigma, ts
        d.samples.append(mu)
        n += 1
    return n


@dataclass
class _Watch:
    target: str
    started: float
    mode: str            # "mic" (the crowd-roar match) | "auto" (break anchors, scorebug, show boundaries)


class Calibrator:
    """The first time a channel is on screen, watch for its stream delay to get measured, from automatic
    signals only: ad-break anchors, the scorebug clock, show boundaries, playback position (and the mic
    when it's on). It never asks for an "I just saw it" tap (Nick, Nov 4: no manual calibration work).
    A channel that isn't timed within WATCH_S stays on its app's delay until a later tune times it."""

    WATCH_S = 30 * 60
    REWATCH_S = 7 * 86400

    def __init__(self, hooks: Any, memory: dict, clock: Callable[[], float] = time.time):
        self.hooks, self.memory, self.clock = hooks, memory, clock
        self.watches: dict[str, _Watch] = {}
        self.timed: list[str] = []
        self.dirty = False

    def calibrated(self, target: str) -> bool:
        measured = getattr(self.hooks.delays, "measured", None)   # its own sample, or its app is settled
        if measured is not None:
            return measured(target)
        d = getattr(self.hooks.delays, "book", {}).get(target)
        return d is not None and d.calibrated_at is not None

    def on_tune(self, target: str | None, replay: bool = False) -> str | None:
        """The engine just put `target` on screen. Starts a one-time watch if its delay was never
        measured; returns "mic" / "auto" for what it started, else None. Replays are skipped: a
        rewound stream's delay says nothing about the live one."""
        if not target or replay or self.hooks.delays is None or ":" not in target:
            return None
        if self.calibrated(target) or target in self.watches:
            return None
        seen = self.memory.setdefault("watched", {}).get(target)
        if seen and self.clock() - seen < self.REWATCH_S:
            return None
        w = _Watch(target, self.clock(), "mic" if self.hooks.mic_on() else "auto")
        self.watches[target] = w
        self.memory["watched"][target] = w.started
        self.dirty = True
        return w.mode

    def poll(self) -> list[str]:
        """Every pass: finish watches that got measured and drop the rest after WATCH_S. Returns
        channels that just got timed. Nothing here asks you for anything."""
        now, done = self.clock(), []
        for t, w in list(self.watches.items()):
            if self.calibrated(t):
                del self.watches[t]
                done.append(t)
            elif now - w.started > self.WATCH_S:
                del self.watches[t]
        self.timed = (done + self.timed)[:5]
        return done

    def summary(self) -> tuple[bool | None, str]:
        if not self.watches:
            return True, (f"Timed {describe(self.timed[0])}" if self.timed else
                          "No new channels to time")
        w = max(self.watches.values(), key=lambda x: x.started)
        if w.mode == "mic":
            return None, f"Listening to time {describe(w.target)}"
        return None, f"Timing {describe(w.target)} from ad breaks and the scorebug"


# ============================================================================ the app's hooks


@dataclass
class Hooks:
    """What autonomy needs from the running app, as plain values and callables (tests pass fakes;
    EngineHooks wires the real Engine)."""
    controller: Any
    channels: Any
    cfg: Any = None
    delays: Any = None
    store: Any = None
    games: Callable[[], list] = lambda: []
    important: Callable[[], str | None] = lambda: None
    toast: Callable[..., None] = lambda text, level="info": None
    mic_on: Callable[[], bool] = lambda: False
    current_target: Callable[[], str | None] = lambda: None
    network: Any = None   # redzone.network.NetWatch; None = don't know where we are (old behavior)


class EngineHooks:
    """Live views into the running Engine. Read on every use, so objects the engine swaps (tests swap
    `channels`) are picked up."""

    def __init__(self, engine: Any):
        self.engine = engine

    controller = property(lambda self: self.engine.control)
    channels = property(lambda self: self.engine.channels)
    cfg = property(lambda self: self.engine.cfg)
    delays = property(lambda self: self.engine.delays)
    store = property(lambda self: self.engine.store)
    network = property(lambda self: getattr(self.engine, "network", None))

    def games(self) -> list:
        return list(self.engine.merger.games.values())

    def important(self) -> str | None:
        return important_now(self.engine, float(self.engine.cfg.get("autonomy.busy_score", 75)))

    def toast(self, text: str, level: str = "info") -> None:
        self.engine.toast(text, level)

    def mic_on(self) -> bool:
        return self.engine.mic is not None

    def current_target(self) -> str | None:
        e = self.engine
        return getattr(e.where.get(e.decider.current or ""), "target", None)


# ============================================================================ autonomy


class Autonomy:
    """The one object the engine holds: `await run()` forever, call on_tune() after each on-screen
    tune, and show status() on the phone. See INTEGRATION.md for the exact hooks."""

    def __init__(self, hooks: Any, *, paths: Paths | None = None, clock: Callable[[], float] = time.time,
                 net: Net | None = None, tuners: dict | None = None,
                 sleep: Callable[[float], Awaitable] = asyncio.sleep, options: dict | None = None,
                 verify_loader: Callable[[], Callable] = load_verify_codes,
                 http: Callable[[], httpx.AsyncClient] = espn_client,
                 optimizer: Callable[[], Awaitable[dict]] = run_lab_search):
        self.hooks, self.clock = hooks, clock
        self.paths = paths or Paths()
        self.net = net or Net()
        self.tuners = TUNERS if tuners is None else tuners
        self.options = options or {}
        self._sleep, self._verify_loader, self._http, self.optimizer = sleep, verify_loader, http, optimizer
        mem = read_json(self.paths.state, {})
        self.memory: dict = mem if isinstance(mem, dict) else {}
        for key in ("calibration", "health"):   # a hand-edited or damaged file can't break startup
            if not isinstance(self.memory.get(key), dict):
                self.memory[key] = {}
        self.healer = DeviceHealer(hooks, self.paths.devices, self.net, clock)
        self.calibration = Calibrator(hooks, self.memory["calibration"], clock)
        self.scheduler = Scheduler(self._jobs(), self.paths.log, clock)
        self.steps: list[str] = []
        self._said: set[str] = set()
        self._next_setup = self._next_tv_setup = -math.inf
        self._own_lock = asyncio.Lock()
        self._started = False

    @classmethod
    def for_engine(cls, engine: Any, **kw: Any) -> "Autonomy":
        return cls(EngineHooks(engine), **kw)

    def _jobs(self) -> list[Job]:
        hour, mins = 3600.0, 60.0
        return [
            Job("st_scan", "Sunday Ticket scan", self._st_scan, (At(12, 45, (SUN,)), At(15, 55, (SUN,))),
                window_s=35 * mins, timeout_s=15 * mins, retry_s=2 * mins, guard=self._st_guard, group="sticks",
                attempts=2),
            Job("selftest", "Nightly app check", self._selftest, (At(4, 0),), window_s=2 * hour,
                timeout_s=15 * mins, retry_s=15 * mins, guard=self._quiet_guard, group="sticks"),
            Job("yttv_codes", "YouTube TV channel check", self._yttv_codes, (At(4, 20),), window_s=2 * hour,
                timeout_s=15 * mins, retry_s=15 * mins, guard=self._night_guard, group="sticks"),
            Job("archive", "Game archive for the lab", self._archive, (At(5, 0),), window_s=6 * hour,
                timeout_s=20 * mins, retry_s=30 * mins, attempts=3, guard=self._archive_guard),
            Job("autotune", "Weekly lab tune-up", self._autotune, (At(4, 40, (TUE,)),), window_s=4 * hour,
                timeout_s=45 * mins, retry_s=30 * mins, guard=self._lab_guard),
            Job("event_links", "Game links (Prime Video, ESPN+)", None),
        ]

    # --- the loop -------------------------------------------------------------------

    async def run(self, every_s: float = 15.0) -> None:
        """Forever: first-run setup, device healing, scheduled jobs, calibration watches."""
        await self.start()
        while True:
            await self.step()
            await self._sleep(every_s)

    async def start(self) -> None:
        """Once at startup: put back channel delays measured before the restart."""
        if self._started:
            return
        self._started = True
        if self.hooks.delays is not None and self.hooks.store is not None:
            n = seed_delays(self.hooks.delays, calibration_history(self.hooks.store))
            if n:
                log.info("Restored %s measured before the restart", _plural(n, "channel delay"))

    async def step(self) -> None:
        """One pass of everything. Each piece is isolated: one failing never stops the others."""
        if self._cfg("mode.dry_run", True):
            await self._poll_calibration()
            return
        for name, part in (("setup", self._setup_if_needed), ("devices", self.healer.check),
                           ("jobs", self.scheduler.tick), ("calibration", self._poll_calibration)):
            try:
                await part()
            except Exception:
                log.exception("autonomy %s step failed", name)

    async def close(self) -> None:
        """Stop running jobs (app shutdown): each cleans up as it's cancelled."""
        for t in list(self.scheduler.tasks):
            t.cancel()
        await asyncio.gather(*self.scheduler.tasks, return_exceptions=True)

    # --- what the engine and the phone call --------------------------------------------

    def on_tune(self, target: str | None, replay: bool = False) -> str | None:
        return self.calibration.on_tune(target, replay)

    def links_refreshed(self, found: int | None, error: str = "") -> None:
        """The engine's own 5-minute game-link refresh reports in, so the phone shows it with the rest."""
        nxt = self.clock() + 300
        if error:
            self.scheduler.note("event_links", False, f"Couldn't fetch game links: {error}"[:200], nxt)
        else:
            msg = f"Found links for {_plural(found or 0, 'game')}" if found else "No new game links"
            self.scheduler.note("event_links", True, msg, nxt)

    async def run_now(self, job: str, force: bool = False) -> JobResult:
        if self._cfg("mode.dry_run", True):
            return JobResult(None, "Dry run is on; device jobs wait until TV control is enabled")
        return await self.scheduler.run_now(job, force)

    def status(self) -> list[dict]:
        """Short rows for the phone: {job, label, last_run, ok, message, next_run, running}."""
        ok, msg = self.healer.summary()
        if self.steps and not any(s.state == "connected" for s in self.hooks.controller.sticks):
            ok, msg = None, self.steps[0]   # setup isn't done: the next step is what matters
        rows = [{"job": "devices", "label": "Fire TV sticks and TV", "last_run": self.healer.last_check,
                 "ok": ok, "message": msg, "next_run": None, "running": False}]
        rows += self.scheduler.status()
        c_ok, c_msg = self.calibration.summary()
        rows.append({"job": "calibration", "label": "Channel delays", "last_run": None, "ok": c_ok,
                     "message": c_msg, "next_run": None, "running": False})
        return rows

    def tuner_health(self) -> dict[str, dict]:
        """Per app, from the nightly check: {app: {ok, message, at}} (ok None = not play-tested)."""
        out = {}
        for app, by_stick in self.memory["health"].items():
            rows = [r for r in by_stick.values() if isinstance(r, dict)] if isinstance(by_stick, dict) else []
            if not rows:
                continue
            oks = [r["ok"] for r in rows if r.get("ok") is not None]
            latest = max(rows, key=lambda r: r.get("at") or 0)
            out[app] = {"ok": all(oks) if oks else None, "message": latest.get("message", ""), "at": latest.get("at")}
        return out

    # --- first run -------------------------------------------------------------------

    async def _setup_if_needed(self) -> None:
        """First run (no sticks saved): look now, then every 5 minutes until one is saved.
        No TV saved: look for it every 30 minutes (it's usually just off)."""
        now = self.clock()
        d = read_devices(self.paths.devices)
        if not self._opt("discover_devices", False):
            if not d.get("sticks"):
                self.steps = ["Set up your Fire Stick, then run the setup checker to select your devices."]
            return
        if not d.get("sticks") and now >= self._next_setup:
            self._next_setup = now + SETUP_EVERY_S
            await self._discover_all()
        elif d.get("sticks") and not (d.get("tv") or {}).get("ip") and now >= self._next_tv_setup:
            self._next_tv_setup = now + TV_SETUP_EVERY_S
            await self._discover_tv(d)

    async def _discover_all(self) -> None:
        c = self.hooks.controller
        if not getattr(c, "adb_ok", True):
            c.adb_ok = await self.net.adb_ok()   # installed since startup? then the controller can use it
        log.info("Setup: looking for your Fire TV sticks and TV on the network…")
        try:
            d, steps = await asyncio.wait_for(discover(self.net, self.paths.devices), SETUP_TIMEOUT_S)
        except asyncio.TimeoutError:
            log.warning("Setup: the network search took too long; trying again in a few minutes")
            return
        self._adopt(d)
        self.steps = steps
        self._say(steps)

    async def _discover_tv(self, d: dict) -> None:
        netw = await self.net.network()
        if netw is None:
            return
        steps = await discover_tv(self.net, netw, d)
        if (d.get("tv") or {}).get("ip"):
            write_json(self.paths.devices, d)
            _point_tv(self.hooks.controller, d["tv"]["ip"])
        self._say(steps)

    def _say(self, steps: list[str]) -> None:
        """Log each next step once, not every time the search repeats."""
        for s in steps:
            if s not in self._said:
                self._said.add(s)
                log.warning("Setup: %s", s)

    def _adopt(self, d: dict) -> None:
        """Hand newly found sticks to the controller without disturbing ones it already drives."""
        c = self.hooks.controller
        have = {s.host for s in c.sticks}
        for e in d.get("sticks") or []:
            if e.get("host") in have:
                continue
            s = self.net.stick(e["host"])
            s.name, s.model, s.os = e.get("name") or "", e.get("model") or "", e.get("os") or ""
            s.packages = dict(e.get("packages") or {})
            c.sticks.append(s)
            if e.get("hdmi"):
                c.hdmi[s.host] = e["hdmi"]
        tv = d.get("tv") or {}
        if tv.get("ip") and not getattr(getattr(c, "tv", None), "host", None):
            _point_tv(c, tv["ip"])

    # --- shared checks ---------------------------------------------------------------

    def _opt(self, key: str, default: Any) -> Any:
        if key in self.options:
            return self.options[key]
        return self._cfg(f"autonomy.{key}", default)

    def _cfg(self, key: str, default: Any) -> Any:
        cfg = self.hooks.cfg
        return cfg.get(key, default) if cfg is not None else default

    def _live(self) -> list:
        return [g for g in self.hooks.games() if g.phase == Phase.LIVE]

    def _two_stick(self) -> bool:
        c = self.hooks.controller
        return bool(getattr(c, "two_stick", False)) and getattr(c, "hidden", None) is not None

    def _is_hidden(self, stick: Stick) -> bool:
        return self._two_stick() and stick is self.hooks.controller.hidden

    def _controller_lock(self) -> asyncio.Lock:
        """The controller's own command lock, so our work never interleaves with a tune. (It's private
        to the controller; fall back to our own if it's ever renamed.)"""
        lock = getattr(self.hooks.controller, "_lock", None)
        return lock if isinstance(lock, asyncio.Lock) else self._own_lock

    async def _tv_off(self) -> bool:
        known, power = await self.healer.tv_power_now()
        return known and power == "standby"

    async def _plan(self, stick: Stick, tv_off: bool) -> tuple[str, str]:
        f = await stick_facts(stick)
        return quiet_plan(tv_off, on_screen(f["active"], self._is_hidden(stick)), f["awake"], f["cec"],
                          bool(self._opt("selftest_may_wake", False)))

    async def _quiet_stick(self) -> Stick | None:
        """A stick that can be used right now without anyone noticing, or None (always None in dry run)."""
        if self._cfg("mode.dry_run", True):
            return None
        tv_off = await self._tv_off()
        for s in list(self.hooks.controller.usable):
            if (await self._plan(s, tv_off))[0] == "full":
                return s
        return None

    async def _night_guard(self) -> str | None:
        """Overnight chores wait for every game to end (the owner may be up watching a late game)."""
        live = self._live()
        return f"{_games_text(live)} still on" if live else None

    def _governor_says(self, kind: str) -> str | None:
        """The resource governor (redzone/governor.py) holds heavy chores while the Mac is busy, hot,
        on battery or short of disk. No governor (tests, tools): never holds."""
        gov = getattr(getattr(self.hooks, "engine", None), "governor", None)
        if gov is None or gov.allow(kind):
            return None
        return f"the Mac needs a break ({gov.why()})"

    async def _archive_guard(self) -> str | None:
        return self._governor_says("archive")

    async def _lab_guard(self) -> str | None:
        return await self._night_guard() or self._governor_says("lab")

    async def _quiet_guard(self) -> str | None:
        """Jobs that drive the sticks also need TV control on and a stick connected."""
        why = await self._night_guard()
        if why:
            return why
        if self._cfg("mode.dry_run", True):
            return "dry run is on, so I don't touch the sticks"
        return None if self.hooks.controller.usable else "no stick is connected"

    # --- job: Sunday Ticket scan --------------------------------------------------------

    def _st_games(self, now: float) -> list:
        """NFL games that need a Sunday Ticket channel: on now or starting within 75 minutes, and not
        matched to a channel yet."""
        out = []
        for g in self.hooks.games():
            if g.league != League.NFL or g.phase == Phase.POST:
                continue
            if g.phase != Phase.LIVE and not (g.start_time and g.start_time - now <= 75 * 60):
                continue
            if self.hooks.channels.where(g, sunday_ticket=True).target == "youtubetv:st":
                out.append(g)
        return out

    def _st_needed(self) -> str | None:
        if not self._cfg("sunday_ticket", True):
            return "Sunday Ticket is turned off in settings"
        if not self._st_candidates():
            return "the Sunday Ticket channels aren't known yet"
        if not self._st_games(self.clock()):
            return "no Sunday Ticket game needs a channel right now"
        return None

    def _st_candidates(self) -> list[str]:
        """Codes to inspect, not confirmed game mappings. Owner-supplied codes take priority."""
        from redzone.discovery.yttv import VerifyState, load_table, sunday_ticket_codes
        configured = list(self.hooks.channels.st_codes)
        if configured:
            return configured
        state = VerifyState(self.paths.state.parent / "yttv_verified.json")
        return sunday_ticket_codes(load_table(), state, include_defaults=True)

    async def _st_safe(self) -> str | None:
        """Two sticks: the scan runs on the hidden stick, so it only waits out big moments (it briefly
        holds up switching). One stick: the TV visibly flips channels, so only before kickoff, with no
        game on, and not over something you're watching in another app."""
        c = self.hooks.controller
        if not c.live_control:
            return "TV control is off (dry run, or no stick connected)"
        if self._two_stick():
            if (await self._plan(c.hidden, await self._tv_off()))[0] != "full":
                return "the standby stick cannot be used without risking the visible TV"
            return self.hooks.important()
        live = self._live()
        if live:
            return f"{_games_text(live)} on, and with one stick the scan would flip your TV"
        starts = [g.start_time for g in self._st_games(self.clock()) if g.start_time]
        if starts and min(starts) - self.clock() < 5 * 60:
            return "kickoff is too close to flip through channels on your only stick"
        return await _other_app_playing(c.main)

    async def _st_guard(self) -> str | None:
        return self._st_needed() or await self._st_safe()

    async def _st_scan(self) -> JobResult:
        """Read which game each Sunday Ticket channel shows, a few channels per controller hold.
        Between batches it re-checks: done early once every game is matched, and it stops if it
        stops being safe (a game went live on a one-stick setup, or a big moment started)."""
        c, ch = self.hooks.controller, self.hooks.channels
        games = [g for g in self.hooks.games() if g.league == League.NFL and g.phase != Phase.POST]
        codes, found, stopped = self._st_candidates(), {}, ""
        prev = None if self._two_stick() else c.on_target
        for i in range(0, len(codes), ST_BATCH):
            if i and not self._st_games(self.clock()):
                break
            if i and (stopped := await self._st_safe() or ""):
                break
            found.update(await c.scan_sunday_ticket(codes[i:i + ST_BATCH], games, self._st_found))
        if prev and not self._two_stick() and not stopped:
            c.submit(prev)   # one stick: put back what the app had on before the scan
        if found:
            self.hooks.toast(f"Sunday Ticket: found the channel for {_plural(len(found), 'game')}", "info")
        msg = f"Matched {_plural(len(found), 'game')} to Sunday Ticket channels"
        if stopped:
            msg += f" — stopped early: {stopped}"
        return JobResult(True if found else (None if stopped else False),
                         msg if found or stopped else "Couldn't read any game titles off the Sunday Ticket channels")

    def _st_found(self, g: Any, code: str) -> None:
        """Remember the channel, and forget any earlier game on the same channel (a 1 PM game's channel
        shows a 4 PM game later; the old mapping would tune the wrong game)."""
        ch = self.hooks.channels
        for gid, c in list(ch.st_slots.items()):
            if c == code and gid != g.game_id:
                del ch.st_slots[gid]
        ch.st_slots[g.game_id] = code
        ch.save()

    # --- job: nightly app check ----------------------------------------------------------

    async def _selftest(self) -> JobResult:
        """Play a known channel on each app, on each stick that can be tested without anyone noticing,
        and record which apps work. Sticks that aren't safe to touch get a look-only check."""
        c = self.hooks.controller
        tv_off = await self._tv_off()
        sticks = list(c.usable)
        lines, oks = [], []
        for stick in sticks:
            plan, why = await self._plan(stick, tv_off)
            who = f"{_stick_name(stick)}: " if len(sticks) > 1 else ""
            if plan == "skip":
                lines.append(f"{who}not tested ({why})")
                continue
            async with self._controller_lock():
                # The active input or dry-run setting may have changed while waiting.
                if self._cfg("mode.dry_run", True):
                    return JobResult(None, "Dry run is on")
                plan, why = await self._plan(stick, await self._tv_off())
                if plan == "skip":
                    continue
                for app in [a for a in TEST_APPS if stick.packages.get(a) and a in self.tuners]:
                    chk = await (self._test_app(stick, app) if plan == "full" else self._light_check(stick, app))
                    self._record(stick, app, plan, chk)
                    lines.append(f"{who}{APP_NAMES[app]} {chk.message}")
                    if chk.ok is not None:
                        oks.append(chk.ok)
                if plan == "full":
                    _forget_playing(c, _index(c, stick))
            if plan == "light":
                lines.append(f"{who}look-only check: {why}")
        self._save_state()
        if not lines:
            return JobResult(None, "No stick to check")
        return JobResult(all(oks) if oks else None, "; ".join(lines))

    def _test_target(self, app: str) -> tuple[str, str] | None:
        """Something that's always live: a configured target, else a 24/7 YouTube TV channel we have a
        code for. Prime Video / ESPN have nothing always-on, so they get an open-the-app check."""
        custom = (self._opt("test_targets", {}) or {}).get(app)
        if custom:
            return _ref_of(custom, self.hooks.channels)
        codes = self.hooks.channels.codes if app == "youtubetv" else {}
        anytime = [k for k in codes if codes[k] and k not in PART_TIME]
        key = next((k for k in TEST_CHANNELS if codes.get(k)), anytime[0] if anytime else None)
        return (codes[key], f"youtubetv:{key}") if key else None

    async def _test_app(self, stick: Stick, app: str) -> AppCheck:
        pkg = stick.packages[app]
        version = await _app_version(stick, pkg)
        if version == "":
            return AppCheck(False, "isn't installed anymore")
        tgt = self._test_target(app)
        try:
            if tgt is None:
                return await self._launch_check(stick, pkg, version)
            res = await self.tuners[app].tune(stick, tgt[0], tgt[1])
            if res.ok:
                return AppCheck(True, f"played {describe(tgt[1]).split(' · ')[0]} in {res.took:.0f}s", version)
            return AppCheck(False, f"didn't play: {res.error or 'no video'}", version)
        finally:
            await stick.shell(f"am force-stop {pkg}", timeout=6)   # no stream left running all night

    async def _launch_check(self, stick: Stick, pkg: str, version: str) -> AppCheck:
        """Open the app and make sure it isn't stuck on a sign-in screen."""
        for cat in ("android.intent.category.LEANBACK_LAUNCHER", "android.intent.category.LAUNCHER"):
            r = await stick.shell(f"monkey -p {pkg} -c {cat} 1", timeout=10)
            if "No activities found" not in r.out:
                break
        await self._sleep(6)
        if pkg not in await stick.foreground():
            return AppCheck(False, "didn't open", version)
        text = (await stick.screen_text()).lower()
        if any(w in text for w in SIGNED_OUT):
            return AppCheck(False, "is showing a sign-in screen", version)
        return AppCheck(True, "opens (nothing always-on to play)", version)

    async def _light_check(self, stick: Stick, app: str) -> AppCheck:
        """Look, don't touch: is the app still installed, and was it updated since it last passed?"""
        version = await _app_version(stick, stick.packages[app])
        if version is None:
            return AppCheck(None, "couldn't be checked")
        if not version:
            return AppCheck(False, "isn't installed anymore")
        by_stick = self.memory["health"].get(app)
        prev = (by_stick.get(self._stick_key(stick)) if isinstance(by_stick, dict) else None) or {}
        if prev.get("version") and prev["version"] != version:
            return AppCheck(None, f"was updated to {version} — it gets a play test when that's safe", version)
        return AppCheck(prev.get("ok"), f"is installed ({version})", version)

    def _stick_key(self, stick: Stick) -> str:
        e = _entry(read_devices(self.paths.devices), stick.host) or {}
        return e.get("serial") or stick.host

    def _record(self, stick: Stick, app: str, plan: str, chk: AppCheck) -> None:
        health = self.memory["health"]
        if not isinstance(health.get(app), dict):
            health[app] = {}
        health[app][self._stick_key(stick)] = {"ok": chk.ok, "message": chk.message, "level": plan,
                                               "at": self.clock(), "version": chk.version}

    def _save_state(self) -> None:
        self.memory["calibration"].pop("asked", None)   # the old tap asks (removed Nov 5)
        watched = self.memory["calibration"].get("watched")
        if isinstance(watched, dict):   # per-event targets pile up; a month of memory is plenty
            cutoff = self.clock() - 30 * 86400
            for t in [t for t, ts in watched.items() if not isinstance(ts, (int, float)) or ts < cutoff]:
                del watched[t]
        try:
            write_json(self.paths.state, self.memory)
        except OSError as e:
            log.warning("couldn't save autonomy state: %s", e)

    # --- job: YouTube TV channel codes -----------------------------------------------------

    async def _yttv_codes(self) -> JobResult:
        """Have the YouTube TV channel checker (a separate module) confirm every saved channel code
        still opens the right channel. Not installed yet = a quiet no-op."""
        from redzone.discovery.yttv import VerifyState, apply_verified, verify_codes
        c, ch = self.hooks.controller, self.hooks.channels
        async with self._controller_lock():
            stick = await self._quiet_stick()
            if stick is None or self._live():
                return JobResult(None, "No stick can be checked quietly right now")
            state = VerifyState(self.paths.state.parent / "yttv_verified.json")
            try:
                out = await verify_codes(
                    stick, state=state, budget_s=120,
                    may_run=lambda: not self._cfg("mode.dry_run", True) and not self._live())
                changes = apply_verified(ch, state=state)
                return JobResult(True if changes else None,
                                 f"Checked {out.checked} channel codes; {len(changes)} verified updates")
            finally:
                _forget_playing(c, _index(c, stick))
                stick.tuned = None

    # --- job: archive ---------------------------------------------------------------------

    async def _archive(self) -> JobResult:
        """Yesterday's finished games (all four leagues) into data/archive/, so the lab keeps growing."""
        arch = self.paths.archive
        arch.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(arch).free < MIN_FREE_BYTES:
            return JobResult(None, "Skipped: less than 2 GB free on this Mac")
        yday = datetime.fromtimestamp(self.clock(), ET).date() - timedelta(days=1)
        days = [yday - timedelta(days=i) for i in range(ARCHIVE_DAYS)]
        async with self._http() as client:
            results = [await download_day(client, lg, d, arch, sleep=self._sleep) for lg in LEAGUE_PATHS for d in days]
        return archive_result(results)

    # --- job: weekly lab tune-up --------------------------------------------------------------

    async def _autotune(self) -> JobResult:
        """Search for better switching settings on past games and write a recommendation. It never
        changes your settings: applying one is always your call."""
        report = await self.optimizer()
        if not isinstance(report, dict) or report.get("error") or not report.get("best"):
            why = report.get("error") if isinstance(report, dict) else None
            return JobResult(False, f"The lab run didn't finish: {why or 'no result'}"[:200])
        rec = recommend(report, self.clock())
        write_json(self.paths.lab / "recommendation.json", rec)
        return JobResult(True, rec["summary"])

    # --- calibration -------------------------------------------------------------------------

    async def _poll_calibration(self) -> None:
        self.calibration.poll()
        if self.calibration.dirty:
            self.calibration.dirty = False
            self._save_state()


async def _app_version(stick: Stick, pkg: str) -> str | None:
    """The app's version ("" = not installed, None = the stick didn't answer)."""
    r = await stick.shell(f"dumpsys package {pkg} | grep -m1 versionName; true", timeout=8)
    if not r.ok:
        return None
    m = re.search(r"versionName=(\S+)", r.out)
    return m.group(1) if m else ""


# ============================================================================ command line


def _yn(v: bool | None) -> str:
    return "unknown" if v is None else ("yes" if v else "no")


async def _cli_stick(net: Net, ip: str, arp: dict, saved: dict) -> int:
    host = f"{ip}:5555"
    st = net.stick(host)
    if not await st.connect():
        print(f"  ! {ip}: " + (APPROVE.format(ip=ip) if st.state == "unauthorized" else f"couldn't connect ({st.state})"))
        return 0
    seen = {**await stick_identity(st), "mac": arp.get(ip, "")}
    f = await stick_facts(st)
    print(f"  ✓ {ip}: {seen.get('model') or '?'} “{seen.get('device_name') or 'no name'}” · serial "
          f"{seen.get('serial') or '?'} · MAC {seen.get('mac') or '?'}")
    print(f"      awake: {_yn(f['awake'])} · HDMI-CEC on: {_yn(f['cec'])} · TV showing it: {_yn(f['active'])}")
    e = next((x for x in saved.get("sticks") or [] if match_kind(x, seen)), None)
    if e is None:
        print("      not saved yet — the app saves it by itself (or run: python -m redzone.autonomy setup)")
    elif e.get("host") != host:
        print(f"      saved at {_ip(e['host'])} — the app will follow it here automatically")
    else:
        print("      saved ✓")
    key = "serial number" if seen.get("serial") else ("MAC address" if seen.get("mac") else "")
    print(f"      after an address change it's recognized by: {key or 'nothing reliable ✗'}")
    return 1 if key else 0


async def _cli_tvs(net: Net, netw: ipaddress.IPv4Network, saved: dict) -> None:
    tv = saved.get("tv") or {}
    found = [i for i in [await net.samsung(h) for h in await net.scan(netw, 8001)] if i]
    for i in found:
        tag = "saved ✓" if same_tv(tv, i) and tv.get("ip") == i["ip"] else (
            "saved at another address — the app will follow it" if same_tv(tv, i) else "not saved")
        print(f"  ✓ TV {i.get('name')} ({i.get('model')}) at {i['ip']} · power {i.get('power') or '?'} · "
              f"Wi-Fi MAC {i.get('wifi_mac') or '?'} · {tag}")
    if not found:
        print("  ! " + TV_MISSING)


async def cli_check(net: Net | None = None, path: Path = DATA / "devices.json") -> int:
    """Read-only check on the real devices: can each stick and the TV be recognized after the router
    gives it a new address, and could tonight's app check run quietly?"""
    net = net or Net()
    print("RedZone autonomy check (read-only: changes nothing)\n")
    if not await net.adb_ok():
        print("  ✗ " + INSTALL_ADB)
    netw = await net.network()
    if netw is None:
        print("  ✗ " + NO_NETWORK)
        return 1
    print(f"  scanning {netw} for Fire TV sticks (port 5555) and the Samsung TV (port 8001)…")
    saved = read_devices(path)
    hosts = await net.scan(netw, 5555)
    arp = await net.arp()
    good = 0
    for ip in hosts:
        good += await _cli_stick(net, ip, arp, saved)
    if not hosts:
        print("  ! " + ENABLE_ADB)
    await _cli_tvs(net, netw, saved)
    print(f"\n  {_plural(good, 'stick')} can be followed automatically if the router changes its address.")
    return 0 if good else 1


async def _cli_autonomy() -> Autonomy:
    from redzone.channels import ChannelMap
    from redzone.config import Config
    from redzone.control import Controller
    cfg, ch = Config(), ChannelMap()
    c = Controller(cfg, ch)
    await c.start()
    return Autonomy(Hooks(controller=c, channels=ch, cfg=cfg))


async def cli_main(cmd: str) -> int:
    if cmd == "check":
        return await cli_check()
    if cmd == "setup":
        _, steps = await discover(Net(), DATA / "devices.json")
        print("\n".join(f"  • {s}" for s in steps) or "  ✓ Everything found and saved.")
        return 0
    if cmd == "status":
        auto = Autonomy(Hooks(controller=type("NoController", (), {"sticks": [], "hdmi": {}})(), channels=None))
        for r in auto.scheduler.status():
            state = {True: "ok", False: "FAILED", None: "-"}[r["ok"]]
            print(f"  {r['label']:<34} {state:<6} last {when_text(r['last_run']):<16} "
                  f"next {when_text(r['next_run']):<16} {r['message']}")
        return 0
    # selftest / archive: run now. force skips the job's timing rules; the per-stick quiet rules still apply.
    auto = await _cli_autonomy() if cmd == "selftest" else Autonomy(
        Hooks(controller=type("NoController", (), {"sticks": [], "hdmi": {}})(), channels=None))
    res = await auto.run_now(cmd, force=True)
    print(res.message)
    return 0 if res.ok is not False else 1


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m redzone.autonomy",
                                 description="RedZone's self-running parts, by hand.")
    ap.add_argument("command", nargs="?", default="check",
                    choices=["check", "setup", "status", "selftest", "archive"],
                    help="check: find + identify sticks and TV (read-only, default) · setup: find and save "
                         "them · status: every job's last result · selftest / archive: run that job now")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return asyncio.run(cli_main(a.command))


if __name__ == "__main__":
    sys.exit(main())
