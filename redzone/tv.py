"""Samsung TV: flip HDMI inputs (two-stick mode) and duck the volume (announcer).

Two ways to switch inputs, tried in this order, and the one that works is remembered:
  1. local  - samsungtvws over the home network (port 8002, a token saved after you click
              "Allow" on the TV once). Keys KEY_HDMI1/KEY_HDMI2... are a no-op on some models.
  2. cloud  - SmartThings API (needs SMARTTHINGS_TOKEN in .env). Capability and input names
              are READ from the TV, never guessed. Personal tokens made after Dec 2024 expire
              after 24h; an expired token is reported on the phone, not retried in a loop.

Every call has a timeout; failures return False so the caller can fall back to single-stick mode.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent
TOKEN_FILE = ROOT / "data" / "tv_token.txt"
ST_API = "https://api.smartthings.com/v1"


def _env(name: str) -> str | None:
    if os.environ.get(name):
        return os.environ[name]
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith(f"{name}=") and line.split("=", 1)[1].strip():
                return line.split("=", 1)[1].strip()
    return None


@dataclass
class SamsungTV:
    host: str | None = None
    method: str | None = None            # "local" | "smartthings" once one is proven
    st_device: str | None = None
    st_capability: str | None = None
    st_inputs: list[str] = field(default_factory=list)
    last_error: str = ""
    _tv: object = None

    @classmethod
    def from_devices(cls) -> "SamsungTV":
        f = ROOT / "data" / "devices.json"
        d = json.loads(f.read_text()) if f.exists() else {}
        tv = d.get("tv") or {}
        return cls(host=tv.get("ip"), method=tv.get("input_method"))

    # --- local (samsungtvws) ---------------------------------------------------------

    def _local(self):
        if self._tv is None:
            from samsungtvws import SamsungTVWS
            TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            self._tv = SamsungTVWS(host=self.host, port=8002, token_file=str(TOKEN_FILE),
                                   name="RedZone Everything", timeout=6)
        return self._tv

    async def key(self, key: str, times: int = 1) -> bool:
        if not self.host:
            self.last_error = "no TV address saved (run scripts/doctor.py)"
            return False

        def send():
            tv = self._local()
            for _ in range(times):
                tv.send_key(key)
                time.sleep(0.15)
        try:
            await asyncio.wait_for(asyncio.to_thread(send), 12)
            return True
        except Exception as e:
            self.last_error = f"local key failed: {e}"
            self._tv = None
            return False

    async def volume_duck(self, steps: int = 4, hold_s: float = 4.0) -> None:
        """Lower the volume for an announcer callout, then put it back."""
        if await self.key("KEY_VOLDOWN", steps):
            await asyncio.sleep(hold_s)
            await self.key("KEY_VOLUP", steps)

    # --- smartthings ---------------------------------------------------------------------

    async def _st(self, method: str, path: str, body: dict | None = None) -> dict | None:
        token = _env("SMARTTHINGS_TOKEN")
        if not token:
            self.last_error = "SMARTTHINGS_TOKEN not set in .env"
            return None
        try:
            async with httpx.AsyncClient(timeout=8) as c:
                r = await c.request(method, ST_API + path, json=body,
                                    headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError as e:
            self.last_error = f"SmartThings unreachable: {e}"
            return None
        if r.status_code == 401:
            self.last_error = "SmartThings token expired or invalid — make a new one and paste it in .env"
            return None
        if r.status_code >= 300:
            self.last_error = f"SmartThings HTTP {r.status_code}"
            return None
        return r.json() if r.content else {}

    async def st_discover(self) -> bool:
        """Find the TV in SmartThings and READ its input-source capability + valid inputs."""
        devs = await self._st("GET", "/devices")
        for d in (devs or {}).get("items", []):
            caps = {c["id"] for comp in d.get("components", []) for c in comp.get("capabilities", [])}
            cap = "samsungvd.mediaInputSource" if "samsungvd.mediaInputSource" in caps else \
                "mediaInputSource" if "mediaInputSource" in caps else None
            if not cap:
                continue
            self.st_device, self.st_capability = d["deviceId"], cap
            st = await self._st("GET", f"/devices/{d['deviceId']}/components/main/capabilities/{cap}/status")
            if cap == "samsungvd.mediaInputSource":
                self.st_inputs = [x.get("id") for x in (st or {}).get("supportedInputSourcesMap", {}).get("value") or []]
            else:
                self.st_inputs = list((st or {}).get("supportedInputSources", {}).get("value") or [])
            return True
        if not self.last_error:
            self.last_error = "no TV with an input-source capability in SmartThings"
        return False

    async def st_input(self, source: str) -> bool:
        if not self.st_device and not await self.st_discover():
            return False
        if self.st_inputs and source not in self.st_inputs:
            self.last_error = f"TV doesn't list input {source}; it lists {self.st_inputs}"
            return False
        r = await self._st("POST", f"/devices/{self.st_device}/commands", {"commands": [{
            "component": "main", "capability": self.st_capability, "command": "setInputSource",
            "arguments": [source]}]})
        return r is not None

    # --- the one call the controller uses -------------------------------------------------

    async def set_input(self, hdmi: str) -> bool:
        """hdmi like "HDMI1". Uses whichever method has worked before; learns on first use."""
        n = hdmi.upper().replace(" ", "")
        if self.method in (None, "local"):
            if await self.key(f"KEY_{n}"):
                if self.method is None:
                    log.info("TV input via local keys sent (can't confirm locally; verify on screen)")
                return True
        if self.method in (None, "smartthings"):
            if await self.st_input(n):
                self.method = "smartthings"
                return True
        return False
