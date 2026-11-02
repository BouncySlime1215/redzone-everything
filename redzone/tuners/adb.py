"""Talk to a Fire TV stick over the network with the adb command-line tool.

Guardrails:
- Every command has a timeout; a hung adb never freezes the app.
- Lost connection -> reconnect with backoff (10s, 20s, ... 60s), forever, quietly.
- "unauthorized" -> stop and tell you to approve the prompt on the TV.
- Package names are discovered with `pm list packages`, never assumed.
"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

ADB = shutil.which("adb") or "/opt/homebrew/bin/adb"

YTTV_PACKAGES = ["com.amazon.firetv.youtube.tv", "com.google.android.youtube.tvunplugged"]
PRIME_PACKAGES = ["com.amazon.firebat", "com.amazon.avod.thirdpartyclient", "com.amazon.amazonvideo.livingroom",
                  "com.amazon.avod"]
YOUTUBE_PACKAGES = ["com.amazon.firetv.youtube", "com.google.android.youtube.tv"]

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
    last_ok: float = 0.0
    _next_retry: float = 0.0
    _backoff: float = 10.0

    @property
    def controllable(self) -> bool:
        return self.state == "connected" and "Vega" not in self.os

    async def run(self, *args: str, timeout: float = 8.0) -> RunResult:
        t0 = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                ADB, "-s", self.host, *args,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            out, _ = await asyncio.wait_for(proc.communicate(), timeout)
            text = out.decode("utf-8", "replace")
            ok = proc.returncode == 0 and "error:" not in text[:200]
            if "unauthorized" in text:
                self.state = "unauthorized"
                ok = False
            elif "offline" in text[:200] or "not found" in text[:200]:
                self.state = "offline"
                ok = False
            elif ok:
                self.last_ok = time.time()
            return RunResult(ok, text, time.monotonic() - t0)
        except (asyncio.TimeoutError, OSError) as e:
            try:
                proc.kill()  # type: ignore[possibly-undefined]
            except Exception:
                pass
            return RunResult(False, f"timeout/error: {e}", time.monotonic() - t0)

    async def shell(self, cmd: str, timeout: float = 8.0) -> RunResult:
        return await self.run("shell", cmd, timeout=timeout)

    async def connect(self) -> bool:
        now = time.time()
        if now < self._next_retry:
            return self.state == "connected"
        r = await _adb("connect", self.host, timeout=6)
        text = r.out.lower()
        if "connected to" in text or "already connected" in text:
            probe = await self.shell("echo ok", timeout=5)
            if probe.ok and "ok" in probe.out:
                self.state = "connected"
                self._backoff = 10.0
                return True
        if "unauthorized" in text:
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
        for role, cands in (("youtubetv", YTTV_PACKAGES), ("prime", PRIME_PACKAGES), ("youtube", YOUTUBE_PACKAGES)):
            hit = next((p for p in cands if p in installed), None)
            if hit:
                self.packages[role] = hit

    # --- playback ground truth ---------------------------------------------------

    async def playback(self, package: str | None = None) -> dict:
        """What the stick says is playing: {state: playing|paused|buffering|stopped|none, pkg, position}."""
        r = await self.shell("dumpsys media_session", timeout=6)
        best = {"state": "none", "pkg": None, "position": None}
        block_pkg = None
        for line in r.out.splitlines():
            m = re.search(r"package=([\w.]+)", line)
            if m:
                block_pkg = m.group(1)
            m = re.search(r"state=PlaybackState \{state=(\d+), position=(-?\d+)", line)
            if m and (package is None or block_pkg == package):
                code = int(m.group(1))
                st = {3: "playing", 2: "paused", 6: "buffering", 8: "connecting", 1: "stopped", 0: "none",
                      7: "error"}.get(code, f"state{code}")
                best = {"state": st, "pkg": block_pkg, "position": int(m.group(2))}
                if st == "playing":
                    break
        return best

    async def foreground(self) -> str:
        r = await self.shell("dumpsys activity activities | grep -m1 -E 'mResumedActivity|topResumedActivity'",
                             timeout=6)
        m = re.search(r"([\w.]+)/([\w.$]+)", r.out)
        return f"{m.group(1)}/{m.group(2)}" if m else ""

    async def screen_text(self) -> str:
        """All visible text in the current UI (menus, buttons, prompts). Video itself isn't text."""
        r = await self.shell("uiautomator dump /sdcard/rz_ui.xml >/dev/null 2>&1; cat /sdcard/rz_ui.xml", timeout=12)
        texts = re.findall(r'(?:text|content-desc)="([^"]+)"', r.out)
        return " | ".join(t for t in texts if t.strip())

    async def key(self, *codes: str) -> None:
        for c in codes:
            await self.shell(f"input keyevent {c}", timeout=4)

    async def view(self, url: str, package: str) -> RunResult:
        url = url.replace("'", "")
        return await self.shell(f"am start -W -a android.intent.action.VIEW -d '{url}' {package}", timeout=15)


async def _adb(*args: str, timeout: float = 8.0) -> RunResult:
    t0 = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(ADB, *args, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        return RunResult(proc.returncode == 0, out.decode("utf-8", "replace"), time.monotonic() - t0)
    except (asyncio.TimeoutError, OSError) as e:
        return RunResult(False, str(e), time.monotonic() - t0)


async def adb_available() -> bool:
    r = await _adb("version", timeout=5)
    return r.ok
