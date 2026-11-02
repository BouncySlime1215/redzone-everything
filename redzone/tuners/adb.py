"""Talk to a Fire TV stick over the network with the adb command-line tool.

Guardrails:
- Every command has a timeout; a hung adb never freezes the app.
- Lost connection -> reconnect with backoff (10s, 20s, ... 60s), forever, quietly.
- "unauthorized" -> stop and tell you to approve the prompt on the TV.
- Package names are discovered with `pm list packages`, never assumed.
- Speed: each stick keeps ONE `adb shell` open and sends commands down it (~10-40 ms each)
  instead of starting a new adb process per command (~80-250 ms each; a tune sends 5-30).
  Anything odd about that shell (won't start, dies, hangs, garbled reply) -> the command runs the
  old way (one adb process) and the shell is reopened later. RZ_ADB_SESSION=0 turns it off.
- A key press or app launch is never sent twice: if the shell dies mid-command we can't tell
  whether it ran, and a doubled DPAD_CENTER can pause the game. Only plain lookups are retried.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import secrets
import shlex
import shutil
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

ADB = shutil.which("adb") or "/opt/homebrew/bin/adb"
SESSION_ON = os.environ.get("RZ_ADB_SESSION", "1").strip().lower() not in ("0", "off", "false", "no")
LOCK_WAIT_S = 0.15     # shell busy longer than this (a slow command) -> this one runs as its own adb process
HANDSHAKE_S = 3.0      # a fresh shell must answer `echo ok` this fast (normally ~0.1 s), cleanly, or we don't use it
MAX_REPLY = 32 << 20   # biggest reply we'll buffer (a full UI dump is ~100 KB)
AWAKE_TTL_S = 60.0     # trust "the stick is awake" this long before asking again

YTTV_PACKAGES = ["com.amazon.firetv.youtube.tv", "com.google.android.youtube.tvunplugged"]
PRIME_PACKAGES = ["com.amazon.firebat", "com.amazon.avod.thirdpartyclient", "com.amazon.amazonvideo.livingroom",
                  "com.amazon.avod"]
YOUTUBE_PACKAGES = ["com.amazon.firetv.youtube", "com.google.android.youtube.tv"]
ESPN_PACKAGES = ["com.espn.gtv", "com.espn.score_center", "com.espn.androidtv", "com.espn.espnplus"]

# Model code -> (name, os). From Amazon's device table; Vega sticks can't be controlled.
MODELS = {
    "AFTMM": ("Fire TV Stick 4K (2018)", "Fire OS 6"), "AFTKA": ("Fire TV Stick 4K Max (2021)", "Fire OS 7"),
    "AFTSS": ("Fire TV Stick Lite / HD", "Fire OS 7"), "AFTSSS": ("Fire TV Stick (3rd gen)", "Fire OS 7"),
    "AFTKM": ("Fire TV Stick 4K (2023)", "Fire OS 8"), "AFTKRT": ("Fire TV Stick 4K Max (2023)", "Fire OS 8"),
    "AFTMA08C15": ("Fire TV Stick 4K Plus", "Fire OS 8"), "AFTGAZL": ("Fire TV Cube (3rd gen)", "Fire OS 7"),
    "AFTCA002": ("Fire TV Stick 4K Select", "Vega OS"), "AFTCL001": ("Fire TV Stick HD (2026)", "Vega OS"),
    "AFTCR001": ("Fire TV Stick 4K (2026)", "Vega OS"), "AFTT": ("Fire TV Stick (2nd gen)", "Fire OS 5"),
    "AFTM": ("Fire TV Stick (1st gen)", "Fire OS 5"),
}

# Media session PlaybackState codes. Newer Android prints "state=PLAYING(3)" instead of "state=3".
PLAYBACK_STATES = {3: "playing", 2: "paused", 6: "buffering", 8: "connecting", 1: "stopped", 0: "none",
                   7: "error"}
_STATE_RE = re.compile(r"state=PlaybackState \{state=(?:[A-Z_]+\()?(\d+)\)?, position=(-?\d+)")
_UPDATED_RE = re.compile(r"\bupdated=(-?\d+)")
# Only the lines the playback parser reads: ~1 KB instead of a 20-60 KB dump.
PLAYBACK_CMD = "dumpsys media_session | grep -E 'package=|state=PlaybackState|metadata:.*description='"


@dataclass
class RunResult:
    ok: bool
    out: str
    took: float


@dataclass
class Stick:
    host: str                      # ip:port
    name: str = ""
    model: str = ""
    os: str = ""
    packages: dict[str, str] = field(default_factory=dict)  # role -> package
    state: str = "unknown"         # connected | offline | unauthorized | unknown
    connect_error: str = ""         # last failed connection/probe, kept separate from playback status
    last_ok: float = 0.0
    _next_retry: float = 0.0
    _backoff: float = 10.0
    # What our last confirmed tune put on screen ({target, pkg, report}); cleared by any key press or
    # app launch, because after one of those we no longer know what's showing.
    tuned: dict | None = field(default=None, repr=False, compare=False)
    _awake_at: float = field(default=-1e9, repr=False, compare=False)
    _session: AdbShell | None = field(default=None, repr=False, compare=False)

    @property
    def controllable(self) -> bool:
        return self.state == "connected" and "Vega" not in self.os

    async def run(self, *args: str, timeout: float = 8.0) -> RunResult:
        """One adb process for one command: the old way, and the fallback for the always-open shell."""
        t0 = time.monotonic()
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                ADB, "-s", self.host, *args,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        except (asyncio.TimeoutError, OSError) as e:
            if proc is not None:
                _close_proc(proc)
            return RunResult(False, f"timeout/error: {e}", time.monotonic() - t0)
        except BaseException:
            if proc is not None:
                _close_proc(proc)     # cancelled: don't leave an adb process running behind us
            raise
        text = out.decode("utf-8", "replace")
        ok = proc.returncode == 0 and "error:" not in text[:200]
        problem = _adb_problem(text)
        if problem:
            self.state = problem
            ok = False
        elif ok:
            self.last_ok = time.time()
        return RunResult(ok, text, time.monotonic() - t0)

    async def shell(self, cmd: str, timeout: float = 8.0) -> RunResult:
        if _changes_screen(cmd):
            self.tuned = None
        return await self._shell(cmd, timeout)

    async def _shell(self, cmd: str, timeout: float) -> RunResult:
        """Always-open shell first; one adb process when it can't be used for this command."""
        sess = self._shell_session()
        if sess is not None:
            t0 = time.monotonic()
            status, r = await sess.run(cmd, timeout)
            if status == DONE:
                if r.ok:
                    self.last_ok = time.time()
                return r
            if status == TIMED_OUT or (status == LOST and not _repeatable(cmd)):
                return r      # it may have run: never send it twice, and don't double a stall
            timeout = max(2.0, timeout - (time.monotonic() - t0))
        return await self.run("shell", cmd, timeout=timeout)

    def _shell_session(self) -> AdbShell | None:
        if not SESSION_ON:
            return None
        loop = asyncio.get_running_loop()
        s = self._session
        if s is not None and s.loop is not loop:
            s.abandon()       # this Stick moved to a new event loop (e.g. a script calling asyncio.run twice)
            s = None
        if s is None:
            s = self._session = AdbShell(self.host)
        return s

    async def close(self) -> None:
        """Close the always-open shell (app shutdown, end of a script)."""
        s, self._session = self._session, None
        if s is None:
            return
        if s.loop is asyncio.get_running_loop():
            await s.close()
        else:
            s.abandon()

    def drop_session(self) -> None:
        """Close the always-open shell without waiting (for code that can't await)."""
        s, self._session = self._session, None
        if s is not None:
            s.abandon()

    def session_status(self) -> str:
        """Plain-English state of the always-open shell (for `rz.py bench` and the logs)."""
        s = self._session
        if not SESSION_ON:
            return "off (RZ_ADB_SESSION=0): one adb process per command"
        if s is None:
            return "not opened yet"
        if s.alive:
            return f"working (opened in {s.ready_ms or 0:.0f} ms; opened {s.opens} time(s) so far)"
        if s.last_error:
            return f"not working on this stick ({s.last_error}); using one adb process per command"
        return "closed; it reopens on the next command"

    async def connect(self) -> bool:
        now = time.time()
        if now < self._next_retry:
            return self.state == "connected"
        r = await _adb("connect", self.host, timeout=6)
        text = r.out.lower()
        detail = r.out.strip()
        if "connected to" in text or "already connected" in text:
            if self._session is not None:
                self._session.retry_at = 0.0   # the stick is back: give the always-open shell another go now
            probe = await self.shell("echo ok", timeout=5)
            if probe.ok and "ok" in probe.out:
                self.state = "connected"
                self.connect_error = ""
                self._backoff = 10.0
                return True
            # adb connect can succeed before its shell is authorized. Keep the
            # actual probe failure instead of replacing it with generic offline.
            detail = probe.out.strip()
            text = detail.lower()
        self.connect_error = detail[:300] or "ADB did not establish a usable shell"
        if "unauthorized" in text or "failed to authenticate" in text:
            self.state = "unauthorized"
        else:
            self.state = "offline"
        self._next_retry = now + self._backoff
        self._backoff = min(60.0, self._backoff * 2)
        return False

    async def identify(self) -> None:
        r = await self.shell("getprop ro.product.model; getprop ro.build.version.release; "
                             "getprop ro.build.version.name; settings get global device_name", timeout=6)
        lines = [x.strip() for x in r.out.splitlines()]
        if lines:
            self.model = lines[0]
            known = MODELS.get(self.model)
            if known:
                self.name, self.os = known
            else:
                self.name = lines[3] if len(lines) > 3 and lines[3] != "null" else self.model
                self.os = f"Fire OS (Android {lines[1]})" if len(lines) > 1 and lines[1] else "unknown"
            if len(lines) > 2 and lines[2] and "Fire" in lines[2]:
                self.os = lines[2].split("(")[0].strip()
        pk = await self.shell("pm list packages", timeout=10)
        installed = set(re.findall(r"package:([\w.]+)", pk.out))
        for role, cands in (("youtubetv", YTTV_PACKAGES), ("prime", PRIME_PACKAGES), ("youtube", YOUTUBE_PACKAGES),
                            ("espn", ESPN_PACKAGES)):
            hit = next((p for p in cands if p in installed), None)
            if hit is None and role == "espn":
                hit = next((p for p in sorted(installed) if "espn" in p), None)
            if hit:
                self.packages[role] = hit

    # --- playback ground truth ---------------------------------------------------

    async def playback(self, package: str | None = None) -> dict:
        """What the stick says is playing: {state: playing|paused|buffering|stopped|none, pkg, position,
        updated}. `updated` is the stick's clock (ms since boot) when the app last changed that state."""
        started = time.monotonic()
        r = await self.shell(PLAYBACK_CMD, timeout=4)
        report = parse_playback(r.out if r.ok else "", package)
        report.update(read_ok=r.ok, read_started=started, read_finished=time.monotonic())
        return report

    async def foreground(self) -> str:
        r = await self.shell("dumpsys activity activities | grep -m1 -E 'mResumedActivity|topResumedActivity'",
                             timeout=6)
        m = re.search(r"([\w.]+)/([\w.$]+)", r.out)
        return f"{m.group(1)}/{m.group(2)}" if m else ""

    async def screen_text(self) -> str:
        """All visible text in the current UI (menus, buttons, prompts). Video itself isn't text.
        Slow (1-3 s), so it runs as its own adb process: the always-open shell stays free for the
        quick "playing yet?" checks that run alongside it."""
        r = await self.run("shell", "uiautomator dump /sdcard/rz_ui.xml >/dev/null 2>&1; cat /sdcard/rz_ui.xml",
                           timeout=12)
        texts = re.findall(r'(?:text|content-desc)="([^"]+)"', r.out)
        return " | ".join(t for t in texts if t.strip())

    async def hdmi_port(self) -> str | None:
        """Which TV input this stick is plugged into, from its HDMI-CEC physical address
        (1.0.0.0 = HDMI1, 2.0.0.0 = HDMI2...). None if CEC is off or the stick won't say."""
        r = await self.shell("dumpsys hdmi_control | grep -i -m3 -E 'physical[ _]?address'", timeout=6)
        return hdmi_port_from_dump(r.out) if r.ok else None

    async def key(self, *codes: str) -> RunResult:
        result = RunResult(True, "", 0.0)
        for c in codes:
            result = await self.shell(f"input keyevent {c}", timeout=4)
            if not result.ok:
                break
        return result

    async def view(self, url: str, package: str, wait: bool = False) -> RunResult:
        """Send a deep link. Plain `am start` returns as soon as the app has the link (~0.1 s);
        wait=True adds `-W`: return only once the app has finished opening (1-3 s)."""
        url = url.replace("'", "")
        w = "-W " if wait else ""
        return await self.shell(f"am start {w}-a android.intent.action.VIEW -d '{url}' {package}",
                                timeout=15 if wait else 8)

    # --- awake / what's on -----------------------------------------------------------

    def mark_awake(self) -> None:
        self._awake_at = time.monotonic()

    async def ensure_awake(self, may_cut=None) -> bool:
        """Press WAKEUP only if the stick is really asleep (or dozing / screensaver). Asking costs
        ~50 ms and the answer is trusted for a minute; a key press costs ~300 ms because Android
        starts a small program for each one. Can't tell -> press it anyway (the old, safe behavior).
        Returns True if it pressed WAKEUP."""
        if time.monotonic() - self._awake_at < AWAKE_TTL_S:
            return False
        r = await self.shell("dumpsys power | grep -m1 'mWakefulness='", timeout=4)
        m = re.search(r"mWakefulness=(\w+)", r.out)
        asleep = not (m and m.group(1) == "Awake")
        if asleep:
            # The power query can take seconds; a safe gap checked by the caller
            # before that read may have closed. Waking can steal HDMI via CEC.
            if may_cut is not None and not may_cut():
                return False
            await self.key("KEYCODE_WAKEUP")
        self.mark_awake()
        return asleep

    def remember(self, target: str, report: dict) -> None:
        """A tune to `target` was confirmed by this playback report."""
        self.tuned = {"target": target, "pkg": report.get("pkg"), "report": dict(report)}
        self.mark_awake()   # it's playing video, so it's awake

    def still_on(self, target: str, report: dict) -> bool:
        """Provably still on `target`, live: we tuned it there, nothing has been pressed or launched
        since, and the app's playback clock shows no pause, seek or new stream since our tune."""
        t = self.tuned
        return bool(t and t["target"] == target and playback_ready(report)
                    and report.get("pkg") == t["pkg"] and same_stream(t["report"], report) is True)


def hdmi_port_from_dump(text: str) -> str | None:
    """Read a direct HDMI topology address, not active-source history or a device ID.

    Fire OS emits physical_address; other Android builds use physicalAddress or
    physical address. This identifies a port, not the monitor's selected input.
    Conflicting or invalid addresses remain unknown.
    """
    values = re.findall(r"physical[ _]?address\s*[:=]\s*(0x[0-9a-f]{4}|[0-9a-f](?:\.[0-9a-f]){3}|[0-9a-f]{4})\b",
                        text, re.IGNORECASE)
    ports = set()
    for value in values:
        digits = value.lower().removeprefix("0x").replace(".", "")
        if len(digits) != 4 or digits[0] == "0" or digits[1:] != "000":
            continue
        ports.add(int(digits[0], 16))
    return f"HDMI{next(iter(ports))}" if len(ports) == 1 else None


# --- the always-open shell ---------------------------------------------------------------

DONE, NOT_SENT, LOST, TIMED_OUT = "done", "not sent", "lost", "timed out"


class AdbShell:
    """One always-open `adb -s HOST shell`; commands go down it one at a time.

    Each command is sent as a single line:
        ( eval '<cmd>' ) </dev/null 2>&1; __rz=$?; echo; echo <marker> $__rz
    and its reply is everything before "\\n<marker> ". The marker is random per command, so a
    command can't print it by accident and a stale reply can't pass for a new one. Why each piece:
    - eval of the single-quoted command: a stray quote breaks only that command, instead of the
      shell waiting forever for the rest of a "string" and swallowing the commands after it.
    - ( ... ): a subshell, so `cd`, `exit` or variables can't leak into the next command (same
      behavior as one adb process per command).
    - </dev/null: a command can't read (and eat) whatever we send next.
    - 2>&1: errors are part of the reply, like before.
    - echo before the marker: a reply with no final newline still leaves the marker on its own line.
    Results: DONE (we have the reply), NOT_SENT (it never reached the stick: safe to run another
    way), LOST (the shell died or got out of step mid-command: it may have run), TIMED_OUT.
    """

    def __init__(self, host: str) -> None:
        self.host = host
        self.loop = asyncio.get_running_loop()
        self.lock = asyncio.Lock()
        self.proc: asyncio.subprocess.Process | None = None
        self.opens = 0                     # times we've started it
        self.failures = 0                  # failed starts in a row (drives the backoff)
        self.retry_at = 0.0                # monotonic time before which we don't try starting it again
        self.last_error = ""
        self.ready_ms: float | None = None

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def run(self, cmd: str, timeout: float) -> tuple[str, RunResult | None]:
        if not self.alive and time.monotonic() < self.retry_at:
            return NOT_SENT, None          # it failed to start recently: don't pay for that again yet
        try:
            await asyncio.wait_for(self.lock.acquire(), LOCK_WAIT_S)
        except asyncio.TimeoutError:
            return NOT_SENT, None          # busy with a slow command: the caller runs this one separately
        try:
            if not self.alive and (time.monotonic() < self.retry_at or not await self._open()):
                return NOT_SENT, None
            return await self._exchange(cmd, timeout)
        finally:
            self.lock.release()

    async def _open(self) -> bool:
        self.opens += 1
        t0 = time.monotonic()
        try:
            self.proc = await asyncio.create_subprocess_exec(
                ADB, "-s", self.host, "shell", stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, limit=MAX_REPLY)
        except OSError as e:
            return self._broken(f"couldn't start adb: {e}")
        status, r = await self._exchange("echo ok", HANDSHAKE_S)
        # A plain shell answers exactly "ok\n". A terminal-style shell echoes our line back and uses
        # \r\n; its replies can't be trusted byte-for-byte, so we don't use it.
        if status != DONE or not r.out.endswith("ok\n") or "\r" in r.out:
            self._kill()
            return self._broken((r.out.strip()[-120:] if r.out.strip() else status))
        self.failures, self.last_error = 0, ""
        self.ready_ms = (time.monotonic() - t0) * 1000
        log.info("adb: always-open shell to %s ready in %.0f ms", self.host, self.ready_ms)
        return True

    def _broken(self, why: str) -> bool:
        self.failures += 1
        wait = min(300.0, 5.0 * 2 ** (self.failures - 1))
        self.retry_at = time.monotonic() + wait
        self.last_error = why
        log.warning("adb: always-open shell to %s not usable (%s); one adb process per command for %.0fs",
                    self.host, why, wait)
        return False

    async def _exchange(self, cmd: str, timeout: float) -> tuple[str, RunResult]:
        t0 = time.monotonic()
        proc = self.proc
        if proc is None or proc.returncode is not None:
            self._kill()
            return NOT_SENT, RunResult(False, "adb shell closed", 0.0)
        marker = f"__RZ{secrets.token_hex(8)}__"
        line = f"( eval {shlex.quote(cmd)} ) </dev/null 2>&1; __rz=$?; echo; echo {marker} $__rz\n"
        try:
            proc.stdin.write(line.encode())
            await proc.stdin.drain()
        except (ConnectionError, OSError) as e:
            # write/drain may fail AFTER bytes reached adb. Retrying a mutation
            # here could launch twice, rewind twice, or flip the input twice.
            self._kill()
            return LOST, RunResult(False, f"adb shell outcome unknown ({e})", time.monotonic() - t0)
        except BaseException:
            self._kill()  # cancellation during drain must not leave a live command behind
            raise
        end = f"\n{marker} ".encode()
        try:
            async with asyncio.timeout(timeout):
                data = await proc.stdout.readuntil(end)
                tail = await proc.stdout.readline()
        except TimeoutError:
            self._kill()                   # hung: the next command gets a fresh shell, not this one's leftovers
            return TIMED_OUT, RunResult(False, f"timeout/error: no reply in {timeout:g}s", time.monotonic() - t0)
        except asyncio.IncompleteReadError as e:
            self._kill()
            said = e.partial.decode("utf-8", "replace").strip()[-200:]
            return LOST, RunResult(False, f"adb shell closed: {said}".strip(), time.monotonic() - t0)
        except asyncio.LimitOverrunError:
            self._kill()
            return LOST, RunResult(False, "reply too large", time.monotonic() - t0)
        except BaseException:
            self._kill()                   # cancelled mid-command: its reply would land in the next read
            raise
        try:
            rc = int(tail)
        except ValueError:
            self._kill()
            return LOST, RunResult(False, "adb shell out of step (garbled reply)", time.monotonic() - t0)
        text = data[:-len(end)].decode("utf-8", "replace")
        return DONE, RunResult(rc == 0 and "error:" not in text[:200], text, time.monotonic() - t0)

    def _kill(self) -> None:
        p, self.proc = self.proc, None
        if p is not None:
            _close_proc(p)

    def abandon(self) -> None:
        """Kill it without waiting (works even if its event loop is gone)."""
        self._kill()

    async def close(self) -> None:
        p, self.proc = self.proc, None
        if p is None:
            return
        _close_proc(p)
        try:
            await asyncio.wait_for(p.wait(), 2)
        except (asyncio.TimeoutError, ProcessLookupError, RuntimeError):
            pass


def _close_proc(p) -> None:
    """Kill an adb process and drop our ends of its pipes right away. Waiting for it to close
    them could hang if something it started still holds them open."""
    try:
        p.kill()
    except (ProcessLookupError, RuntimeError, OSError):
        pass
    t = getattr(p, "_transport", None)   # asyncio's handle; closing it releases the pipes
    if t is not None:
        try:
            t.close()
        except (RuntimeError, OSError):
            pass


# --- command classification ------------------------------------------------------------

_LOOKUP = re.compile(r"(echo|printf|true|getprop|dumpsys|pm list|settings get|cat|ls|grep|head|tail|"
                     r"uiautomator dump)\b")
_SCREEN_CHANGERS = re.compile(r"\binput\b|\bam\s+(?:start|force-stop|kill)\b|\bmonkey\b")


def _repeatable(cmd: str) -> bool:
    """Safe to send twice? Only plain lookups. Key presses, app launches and input flips are not:
    if we can't tell whether they ran, running them again could pause the game or double a rewind."""
    parts = re.split(r"\|\||&&|[;|]", re.sub(r"'[^']*'", "''", cmd))
    return all(_LOOKUP.match(p.strip()) for p in parts if p.strip())


def _changes_screen(cmd: str) -> bool:
    """Could this command change what the stick is showing? (Waking it up can't.)"""
    return bool(_SCREEN_CHANGERS.search(cmd.replace("input keyevent KEYCODE_WAKEUP", "")))


def _adb_problem(text: str) -> str | None:
    """adb's own complaint about the connection -- not a command's output, which can say
    "not found" or "offline" for its own reasons. Returns "unauthorized" / "offline" / None."""
    for line in text[:400].lower().splitlines():
        line = line.strip()
        if not line.startswith(("error:", "adb:")):
            continue
        if "unauthorized" in line:
            return "unauthorized"
        if "offline" in line or "not found" in line or "no devices" in line:
            return "offline"
    return None


# --- playback reports --------------------------------------------------------------------

def parse_playback(text: str, package: str | None = None) -> dict:
    """Read `dumpsys media_session` (full or grep'd). A playing session wins; otherwise the last one."""
    empty = {"state": "none", "pkg": None, "position": None, "updated": None}
    blocks, current = [], None
    for line in text.splitlines():
        m = re.search(r"package=([\w.]+)", line)
        if m:
            current = dict(empty, pkg=m.group(1))
            blocks.append(current)
        if current is None:
            continue
        m = _STATE_RE.search(line)
        if m:
            code = int(m.group(1))
            u = _UPDATED_RE.search(line)
            current.update(state=PLAYBACK_STATES.get(code, f"state{code}"),
                           position=int(m.group(2)), updated=int(u.group(1)) if u else None)
            speed = re.search(r"\bspeed=(-?\d+(?:\.\d+)?)", line)
            if speed:
                current["speed"] = float(speed.group(1))
        metadata = re.search(r"metadata:\s*size=\d+,\s*description=(.*)$", line)
        if metadata:
            # Android MediaDescription renders title, subtitle, description in that
            # order. Keep the title attached to its own session even when metadata
            # occurs after PlaybackState, and never borrow another app's title.
            fields = metadata.group(1).rsplit(", ", 2)
            fields += [""] * (3 - len(fields))
            for key, value in zip(("title", "subtitle", "description"), fields):
                current[key] = "" if value.strip().lower() == "null" else value.strip()
    eligible = [b for b in blocks if (package is None or b["pkg"] == package)
                and b["position"] is not None]
    return next((b for b in eligible if b["state"] == "playing"), eligible[-1] if eligible else empty)


def playback_ready(report: dict) -> bool:
    """A usable moving-playback report, not proof of frames or game identity.

    Fire OS can report PLAYING with speed=0 while a player reloads. Missing
    speed remains compatible with providers that omit it; explicit invalid,
    stationary, or reverse transport must not confirm readiness.
    """
    if report.get("state") != "playing" or report.get("read_ok") is False:
        return False
    speed = report.get("speed")
    if speed is None:
        return True
    try:
        return math.isfinite(float(speed)) and float(speed) > 0
    except (TypeError, ValueError):
        return False


def same_stream(a: dict, b: dict, slack_ms: int = 3000) -> bool | None:
    """Is report b the same stream as report a, playing on without a break? The app stamps each
    report with the stick's clock: position p at time u should be p + (u2 - u) at time u2.
    True = provably the same, uninterrupted. False = a pause, seek or different stream.
    None = can't tell (no positions, as some live streams report)."""
    pa, ua, pb, ub = a.get("position"), a.get("updated"), b.get("position"), b.get("updated")
    if None in (pa, ua, pb, ub):
        return None
    if (pa, ua) == (pb, ub):
        return True       # the app hasn't changed its state at all since then
    if pa <= 0 or pb <= 0:
        return None
    return abs(pb - (pa + (ub - ua))) <= slack_ms


async def _adb(*args: str, timeout: float = 8.0) -> RunResult:
    t0 = time.monotonic()
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(ADB, *args, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        return RunResult(proc.returncode == 0, out.decode("utf-8", "replace"), time.monotonic() - t0)
    except (asyncio.TimeoutError, OSError) as e:
        if proc is not None:
            _close_proc(proc)
        return RunResult(False, str(e), time.monotonic() - t0)


async def adb_available() -> bool:
    r = await _adb("version", timeout=5)
    return r.ok
