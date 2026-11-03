"""Samsung TV: flip HDMI inputs (two-stick mode) and duck the volume (announcer).

Two input command methods; the one confirmed by selected-TV readback is remembered:
  1. local  - samsungtvws over the home network (port 8002, a token saved after you click
              "Allow" on the TV once). Keys KEY_HDMI1/KEY_HDMI2... are a no-op on some models.
  2. cloud  - SmartThings API (needs SMARTTHINGS_TOKEN in .env). Capability and input names
              are READ from the TV, never guessed. Personal tokens made after Dec 2024 expire
              after 24h; an expired token is reported on the phone, not retried in a loop.
              Requires an explicitly selected device ID, never the first TV in the account.

Every call has a timeout. False means unconfirmed, not necessarily unchanged: callers must
not issue a second visible command after uncertain delivery. A successful network write
alone never confirms which input the viewer sees.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import re
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
class InputEvidence:
    """Transport acceptance and the TV's reported input are separate facts."""
    requested: str
    source: str = ""
    accepted: bool | None = False  # None: delivery is unknown (timeout / lost reply)
    verified: bool = False
    observed: str | None = None
    detail: str = ""


@dataclass
class SamsungTV:
    host: str | None = None
    method: str | None = None            # "local" | "smartthings" once one is proven
    st_device: str | None = None
    st_capability: str | None = None
    st_inputs: list[str] = field(default_factory=list)
    last_error: str = ""
    last_input: InputEvidence | None = None
    _tv: object = None

    @classmethod
    def from_devices(cls) -> "SamsungTV":
        f = ROOT / "data" / "devices.json"
        d = json.loads(f.read_text()) if f.exists() else {}
        tv = d.get("tv") or {}
        return cls(host=tv.get("ip"), method=tv.get("input_method"),
                   st_device=tv.get("smartthings_device_id") or tv.get("st_device"))

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
        """Read capabilities for the explicitly selected TV; never select the first cloud TV."""
        if not self.st_device:
            self.last_error = "no SmartThings TV selected; save this TV's smartthings_device_id first"
            return False
        devs = await self._st("GET", "/devices")
        for d in (devs or {}).get("items", []):
            if d.get("deviceId") != self.st_device:
                continue
            caps = {c["id"] for comp in d.get("components", []) for c in comp.get("capabilities", [])}
            cap = "samsungvd.mediaInputSource" if "samsungvd.mediaInputSource" in caps else \
                "mediaInputSource" if "mediaInputSource" in caps else None
            if not cap:
                continue
            self.st_capability = cap
            st = await self._st("GET", f"/devices/{d['deviceId']}/components/main/capabilities/{cap}/status")
            if cap == "samsungvd.mediaInputSource":
                self.st_inputs = [x.get("id") for x in (st or {}).get("supportedInputSourcesMap", {}).get("value") or []]
            else:
                self.st_inputs = list((st or {}).get("supportedInputSources", {}).get("value") or [])
            return True
        if not self.last_error:
            self.last_error = "selected SmartThings TV is unavailable or has no input-source capability"
        return False

    async def st_input(self, source: str) -> bool:
        if (not self.st_device or not self.st_capability) and not await self.st_discover():
            return False
        if self.st_inputs and source not in self.st_inputs:
            self.last_error = f"TV doesn't list input {source}; it lists {self.st_inputs}"
            return False
        r = await self._st("POST", f"/devices/{self.st_device}/commands", {"commands": [{
            "component": "main", "capability": self.st_capability, "command": "setInputSource",
            "arguments": [source]}]})
        return r is not None

    async def input_status(self) -> str | None:
        """Read the selected TV's input. Missing or unreadable evidence stays unknown."""
        if (not self.st_device or not self.st_capability) and not await self.st_discover():
            return None
        status = await self._st("GET", f"/devices/{self.st_device}/components/main/capabilities/"
                                f"{self.st_capability}/status")
        source = (status or {}).get("inputSource")
        value = source.get("value") if isinstance(source, dict) else None
        return re.sub(r"\s+", "", value).upper() if isinstance(value, str) else None

    async def _verify_input(self, evidence: InputEvidence) -> bool:
        if not self.st_device:
            evidence.detail = "command sent; active input has no readback"
            return False
        for attempt in range(3):
            evidence.observed = await self.input_status()
            if evidence.observed == evidence.requested:
                evidence.verified = True
                evidence.detail = "selected TV reports the requested input"
                return True
            if attempt < 2:
                await asyncio.sleep(0.25)
        evidence.detail = "requested input was not confirmed by the selected TV"
        return False

    # --- the one call the controller uses -------------------------------------------------

    async def set_input(self, hdmi: str) -> bool:
        """Return True only when the selected TV reports the requested input.

        A sent key is not confirmation. An ambiguous delivery never triggers another
        method, since the original command may already have changed the visible input.
        """
        n = hdmi.upper().replace(" ", "")
        self.last_input = evidence = InputEvidence(n)
        if not re.fullmatch(r"HDMI[1-9][0-9]*", n):
            self.last_error = evidence.detail = "invalid HDMI input"
            return False
        if self.method in (None, "local"):
            if self.host:
                evidence.source = "local"
                evidence.accepted = True if await self.key(f"KEY_{n}") else None
                ok = await self._verify_input(evidence)
                if ok:
                    self.method = "local"
                else:
                    self.last_error = evidence.detail
                return ok
        if self.method in (None, "smartthings"):
            evidence.source = "smartthings"
            if not self.st_device:
                self.last_error = evidence.detail = "no SmartThings TV explicitly selected"
                return False
            evidence.accepted = True if await self.st_input(n) else None
            if await self._verify_input(evidence):
                self.method = "smartthings"
                return True
            self.last_error = evidence.detail
        return False
