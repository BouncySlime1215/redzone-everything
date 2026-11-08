"""One-time TV setup for instant switching (scripts/tv_setup.py is the command; this is the logic,
with every network/device touch in one `Home` object that tests replace with a fake).

Steps (each prints what it found, and on failure the exact cause and the TV menu to change):
  1. find the TV by its MAC: sweep the sticks' /24 (fills the ARP table), read ARP, and ask each
     stick (`ip neigh`) in case the Mac can't see it (a guest SSID / client isolation).
  2. read the exact model from http://IP:8001/api/v2/ (modelName, OS, TokenAuthSupport,
     PowerState, wifiMac) and the UPnP description on :9197/dmr; save it with network_verified=true
     only when the TV's own wifiMac matches the saved MAC.
  3. pair on 8002 (press Allow on the TV once); the token goes to data/tv_token.txt.
  4. test KEY_HDMI1 / KEY_HDMI2 for each stick's port: which stick says it gained / lost the input
     (CEC), a screenshot from the stick, and you looking at the TV. Latency is measured key -> CEC.
  5. if local fails: the cause (wrong subnet, isolation, port closed, IP Remote off, pairing denied).
  6. SmartThings OAuth (refresh token, renewed automatically) with input readback.
  7. with a TV path proven: HDMI-CEC off on both sticks, so the stick that loses the input never
     pauses (scripts/stick_cec.py; HOME stays the last-resort flip and turns CEC on for itself).
Whatever path works is saved in data/devices.json and becomes the default flip on the next start.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import secrets
import time
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from redzone import tv as tvmod
from redzone.autonomy import arp_table, local_network, norm_mac, port_open, samsung_info, scan_port

try:   # the network-aware branch owns find-by-MAC; until it lands, a stub with the same signature
    from redzone.network import find_by_mac  # type: ignore[attr-defined]
except ImportError:
    async def find_by_mac(mac: str, subnet: ipaddress.IPv4Network | None = None, probe=None) -> str | None:
        """Temporary stand-in for redzone.network.find_by_mac: read ARP; if the MAC isn't there,
        touch the subnet (`probe`, default a TCP sweep of port 8001) to fill ARP, and read again."""
        want = norm_mac(mac)
        hit = next((ip for ip, m in arp_table().items() if m == want), None)
        if hit or subnet is None:
            return hit
        await (probe(subnet) if probe is not None else scan_port(subnet, 8001))
        return next((ip for ip, m in arp_table().items() if m == want), None)

ROOT = Path(__file__).resolve().parent.parent
DEVICES = ROOT / "data" / "devices.json"
ACTIVE_SOURCE_CMD = "dumpsys hdmi_control | grep -E -c 'mIsActiveSource: true'"
CEC_GET = "settings get global hdmi_control_enabled"
CEC_SET = "settings put global hdmi_control_enabled {v}"
DEFAULT_REDIRECT = "https://httpbin.org/get"   # echoes ?code=... back so you can paste it

MENU_IP_REMOTE = ("On the TV: Settings > General > External Device Manager > Device Connection Manager:\n"
                  "     Access Notification = First Time Only (or Always), and check the Device List has no\n"
                  "     'RedZone Everything' marked Deny. Also Settings > General > Network > Expert Settings:\n"
                  "     IP Remote = On, Power On with Mobile = On.")
MENU_SAME_NET = ("Put the TV on the SAME Wi-Fi as the sticks (not a guest network, not the 5 GHz/2.4 GHz twin "
                 "if your router isolates bands): Settings > General > Network > Network Status shows its IP.")


def _ip(host: str) -> str:
    return host.split(":", 1)[0]


def net24(host: str) -> ipaddress.IPv4Network | None:
    try:
        return ipaddress.ip_network(f"{_ip(host)}/24", strict=False)
    except ValueError:
        return None


NEIGH = re.compile(r"^(\d+\.\d+\.\d+\.\d+)\s.*lladdr\s+([0-9a-fA-F:]+)", re.M)


def parse_ip_neigh(text: str) -> dict[str, str]:
    """`ip neigh` on a stick -> IP: MAC."""
    return {ip: m for ip, mac in NEIGH.findall(text or "") if (m := norm_mac(mac))}


def parse_upnp(xml: str) -> dict:
    """The UPnP device description (:9197/dmr) -> model / name fields. {} if it isn't one."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return {}
    out = {}
    for el in root.iter():
        tag = el.tag.split("}", 1)[-1]
        if tag in ("modelName", "modelNumber", "friendlyName", "manufacturer", "modelDescription") and el.text:
            out.setdefault(tag, el.text.strip())
    return out


@dataclass
class Report:
    lines: list[str] = field(default_factory=list)
    ok: list[str] = field(default_factory=list)       # paths proven: "local", "smartthings"
    causes: list[str] = field(default_factory=list)

    def say(self, icon: str, text: str) -> None:
        self.lines.append(f"  {icon} {text}")
        print(f"  {icon} {text}", flush=True)


OK, WARN, BAD = "✅", "⚠️ ", "❌"


class Home:
    """Everything tv_setup touches, so tests can fake it."""

    def __init__(self, sticks: list[dict]):
        self._sticks = sticks
        self._objs: dict[str, Any] = {}

    def mac_network(self) -> ipaddress.IPv4Network | None:
        return local_network()

    async def find_by_mac(self, mac: str, subnet: ipaddress.IPv4Network | None) -> str | None:
        return await find_by_mac(mac, subnet)

    async def port(self, ip: str, port: int) -> bool:
        return await port_open(ip, port, timeout=1.5)

    async def samsung(self, ip: str) -> dict | None:
        return await samsung_info(ip)

    async def upnp(self, ip: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=3) as c:
                r = await c.get(f"http://{ip}:9197/dmr")
            return parse_upnp(r.text) if r.status_code == 200 else {}
        except Exception:
            return {}

    def stick(self, host: str):
        if host not in self._objs:
            from redzone.tuners.adb import Stick
            self._objs[host] = Stick(host)
        return self._objs[host]

    async def shell(self, host: str, cmd: str, timeout: float = 6.0) -> tuple[bool, str]:
        s = self.stick(host)
        if s.state != "connected":
            await s.connect()
        r = await s.shell(cmd, timeout=timeout)
        return r.ok, r.out

    async def screencap(self, host: str) -> bytes:
        return await self.stick(host).screencap(timeout=15)

    async def pair(self, ip: str, wait_s: float = 30.0) -> tuple[bool, str]:
        """Open the 8002 remote socket; the TV shows Allow/Deny the first time. True once a token is saved."""
        def go():
            from samsungtvws import SamsungTVWS
            tvmod.TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            t = SamsungTVWS(host=ip, port=8002, token_file=str(tvmod.TOKEN_FILE), name="RedZone Everything",
                            timeout=wait_s)
            t.open()
            t.close()
        try:
            await asyncio.wait_for(asyncio.to_thread(go), wait_s + 5)
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"
        ok = tvmod.TOKEN_FILE.exists() and tvmod.TOKEN_FILE.read_text().strip() != ""
        return ok, "" if ok else "the TV accepted the connection but sent no token (it may not need one)"

    async def key(self, ip: str, key: str) -> bool:
        t = tvmod.SamsungTV(host=ip)
        ok = await t.key(key)
        t._drop_local()
        return ok

    def ask(self, question: str) -> bool | None:
        try:
            a = input(f"     {question} [y/n] ").strip().lower()
        except EOFError:
            return None
        return True if a.startswith("y") else False if a.startswith("n") else None

    def prompt(self, question: str) -> str:
        try:
            return input(f"     {question} ").strip()
        except EOFError:
            return ""

    def sleep(self, s: float):
        return asyncio.sleep(s)

    def now(self) -> float:
        return time.monotonic()

    def http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=10)


# ------------------------------------------------------------------------------------------ steps

async def find_tv(home: Home, mac: str, sticks: list[dict], saved_ip: str | None, rep: Report) -> str | None:
    """Step 1: the TV's IP from its MAC. The typed IP is only a hint."""
    mac = norm_mac(mac)
    if not mac:
        rep.say(BAD, "No TV MAC saved in data/devices.json (tv.mac): can't look for it. Pass --ip to try one.")
        return saved_ip
    nets = []
    for s in sticks:
        n = net24(s.get("host", ""))
        if n and n not in nets:
            nets.append(n)
    mine = home.mac_network()
    if mine is not None and nets and not any(mine.overlaps(n) for n in nets):
        rep.say(BAD, f"This Mac is on {mine}, but the sticks are on {', '.join(map(str, nets))}: you're not on the "
                     "home network (or on a guest network). Join the sticks' Wi-Fi and run this again.")
        rep.causes.append("wrong subnet")
        return None
    hit = None
    for n in nets or ([mine] if mine else []):
        rep.say("…", f"Looking for the TV ({mac}) on {n} ...")
        hit = await home.find_by_mac(mac, n)
        if hit:
            break
    if hit:
        rep.say(OK, f"Found the TV at {hit} (this Mac's ARP table, MAC {mac})"
                    + ("" if hit == saved_ip else f" — the saved IP {saved_ip} was wrong"))
        return hit
    for s in sticks:
        ok, out = await home.shell(s["host"], "ip neigh", timeout=6)
        if not ok:
            continue
        seen = parse_ip_neigh(out)
        hit = next((ip for ip, m in seen.items() if m == mac), None)
        if hit:
            rep.say(WARN, f"Only the stick {s['host']} sees the TV, at {hit}: this Mac can't reach it directly "
                          "(client isolation or a different SSID).")
            rep.causes.append("isolation")
            return hit
    rep.say(BAD, f"No device with the TV's MAC {mac} answered on the home network"
                 + (f" (and nothing answers at the saved {saved_ip})" if saved_ip else "") + ".")
    rep.say("  ", "Likely: the TV is off/asleep with network standby off, on a guest or other-band Wi-Fi, or")
    rep.say("  ", "the saved MAC is the wired one. " + MENU_SAME_NET)
    rep.causes.append("not on the network")
    return None


async def read_model(home: Home, ip: str, mac: str | None, rep: Report) -> dict | None:
    """Step 2: the TV's own description. network_verified only when its wifiMac matches."""
    info = await home.samsung(ip)
    up = await home.upnp(ip)
    if info is None:
        if await home.port(ip, 8001) or await home.port(ip, 8002):
            rep.say(BAD, f"{ip} has the TV's remote port open but no Samsung status page: is IP Remote on?")
            rep.say("  ", MENU_IP_REMOTE)
            rep.causes.append("IP Remote off")
        else:
            rep.say(BAD, f"{ip}: ports 8001/8002 closed. The TV is off, or IP Remote is off.")
            rep.say("  ", MENU_IP_REMOTE)
            rep.causes.append("port closed")
        return None
    seen_mac = norm_mac(info.get("wifi_mac"))
    verified = bool(mac and seen_mac and seen_mac == norm_mac(mac))
    out = {"ip": ip, "name": info.get("name"), "model": info.get("model") or up.get("modelName"),
           "os": info.get("os"), "token_auth": info.get("token_auth"), "power": info.get("power"),
           "wifi_mac": seen_mac or None, "upnp": up or None, "network_verified": verified}
    rep.say(OK if verified else WARN,
            f"TV: {out['model'] or 'unknown model'} ({out['name'] or 'no name'}), {out['os'] or 'OS ?'}, "
            f"power {out['power'] or '?'}, token auth {out['token_auth']}"
            + ("" if verified else f" — its Wi-Fi MAC {seen_mac or '?'} is NOT the saved {mac}: not saved as verified"))
    return out


async def pair(home: Home, ip: str, rep: Report) -> bool:
    rep.say("…", "Pairing: press ALLOW on the TV now (you have 30 s) ...")
    ok, why = await home.pair(ip)
    if ok:
        rep.say(OK, "Paired: the TV's token is saved in data/tv_token.txt (not in git).")
        return True
    rep.say(BAD, f"Pairing failed: {why}")
    rep.say("  ", "If you didn't see an Allow prompt, or pressed Deny: " + MENU_IP_REMOTE)
    rep.causes.append("pairing denied or timed out")
    return False


async def _active(home: Home, host: str) -> bool | None:
    ok, out = await home.shell(host, ACTIVE_SOURCE_CMD, timeout=4)
    return out.strip().startswith("1") if ok else None


async def test_local_inputs(home: Home, ip: str, sticks: list[dict], rep: Report,
                            ask: bool = True, shots: Path | None = None) -> dict[str, dict]:
    """Step 4: KEY_HDMIx for each stick's port, twice around, so each one is a real change."""
    results: dict[str, dict] = {}
    order = [s for s in sticks if s.get("hdmi")]
    for rnd in range(2):
        for s in order:
            other = next((o for o in order if o is not s), None)
            hdmi = s["hdmi"].upper().replace(" ", "")
            t0 = home.now()
            sent = await home.key(ip, f"KEY_{hdmi}")
            gained = lost = None
            took = None
            while home.now() - t0 < 4.0 and sent:
                gained = await _active(home, s["host"])
                if gained:
                    took = home.now() - t0
                    break
                await home.sleep(0.15)
            if other is not None:
                lost = (await _active(home, other["host"])) is False
            if shots is not None and sent:
                try:
                    png = await home.screencap(s["host"])
                    if png:
                        shots.mkdir(parents=True, exist_ok=True)
                        (shots / f"tv_setup_{hdmi}.png").write_bytes(png)
                except Exception:
                    pass
            seen = home.ask(f"Is the TV now showing {s.get('name') or s['host']} ({hdmi})?") if (ask and sent) else None
            r = results.setdefault(hdmi, {"sent": 0, "cec": 0, "seen": 0, "no": 0, "latency_s": []})
            r["sent"] += bool(sent)
            r["cec"] += bool(gained)
            r["seen"] += seen is True
            r["no"] += seen is False
            if took is not None:
                r["latency_s"].append(round(took, 2))
            rep.say(OK if (seen or (seen is None and gained)) else BAD,
                    f"KEY_{hdmi}: {'sent' if sent else 'NOT sent'}; "
                    f"{s['host']} {'became' if gained else 'did not become'} the active source"
                    + (f" in {took:.2f}s" if took is not None else "")
                    + ("" if other is None else f", {other['host']} {'lost it' if lost else 'did not report losing it'}")
                    + ("" if seen is None else f"; you said {'yes' if seen else 'no'}"))
    return results


def local_proven(results: dict[str, dict], sticks: list[dict], asked: bool) -> bool:
    """Every stick's port switched both times: by your eyes when asked, else by CEC on that stick."""
    ports = [s["hdmi"].upper().replace(" ", "") for s in sticks if s.get("hdmi")]
    if len(ports) < 2:
        return False
    for p in ports:
        r = results.get(p) or {}
        if r.get("no"):
            return False
        if (r.get("seen", 0) if asked else r.get("cec", 0)) < 2:
            return False
    return True


async def try_source_key(home: Home, ip: str, rep: Report) -> None:
    """KEY_SOURCE opens the TV's source list (it doesn't pick one): reported, never used to switch."""
    if not await home.key(ip, "KEY_SOURCE"):
        return
    seen = home.ask("Did the TV's Source list open?")
    await home.key(ip, "KEY_RETURN")
    rep.say(OK if seen else WARN, "KEY_SOURCE " + ("opens the source list (not used: it can't pick an input)"
                                                    if seen else "did nothing visible"))


# ------------------------------------------------------------------------------------------ SmartThings

def authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    q = urllib.parse.urlencode({"client_id": client_id, "response_type": "code", "redirect_uri": redirect_uri,
                                "scope": tvmod.ST_SCOPES, "state": state})
    return f"{tvmod.ST_AUTHORIZE_URL}?{q}"


def code_from(pasted: str, state: str | None = None) -> str | None:
    """The `code` from a pasted redirect URL, or the bare code itself. A wrong `state` is refused."""
    pasted = (pasted or "").strip()
    if not pasted:
        return None
    if "code=" in pasted:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(pasted).query or pasted.split("?", 1)[-1])
        if state and q.get("state") and q["state"][0] != state:
            return None
        return (q.get("code") or [None])[0]
    # httpbin echoes JSON: {"args": {"code": "..."}}
    m = re.search(r'"code"\s*:\s*"([^"]+)"', pasted)
    if m:
        return m.group(1)
    return pasted if re.fullmatch(r"[A-Za-z0-9._~-]{4,}", pasted) else None


async def create_oauth_app(http: httpx.AsyncClient, pat: str, redirect_uri: str) -> tuple[str, str] | str:
    """An API_ONLY SmartThings app with OAuth (what `smartthings apps:create` makes for an OAuth-In
    app), created with a personal token you make once; returns (client_id, client_secret), or the
    reason SmartThings gave (status + the start of its error body; the token is never in it).
    No query string: `?signatureType=ST_PADLOCK&requireConfirmation=true` is for webhook apps, and
    for API_ONLY SmartThings answers 400 "Param(s) not supported for AppType" (Nick, 2026-11-03)."""
    body = {"appName": f"redzone-everything-{secrets.token_hex(3)}", "displayName": "RedZone Everything",
            "description": "Switches the TV input for RedZone Everything", "appType": "API_ONLY",
            "classifications": ["CONNECTED_SERVICE"], "singleInstance": True,
            "apiOnly": {}, "oauth": {"clientName": "RedZone Everything", "scope": tvmod.ST_SCOPES.split(),
                                     "redirectUris": [redirect_uri]}}
    r = await http.post(f"{tvmod.ST_API}/apps", json=body, headers={"Authorization": f"Bearer {pat}"})
    if r.status_code >= 300:
        text = " ".join((r.text or "").split())
        if pat:
            text = text.replace(pat, "[token]")
        return f"HTTP {r.status_code}: {text[:300] or 'no details'}"
    try:
        d = r.json()
    except ValueError:
        return f"HTTP {r.status_code}: the answer wasn't JSON"
    cid, sec = d.get("oauthClientId"), d.get("oauthClientSecret")
    return (cid, sec) if cid and sec else "SmartThings created no OAuth client (no oauthClientId/oauthClientSecret)"


def pick_tv(devices: list[dict], tv: dict) -> list[dict]:
    """TVs in the account that have an input-source capability, best match to our TV first."""
    out = []
    for d in devices:
        caps = {c.get("id") for comp in d.get("components", []) for c in comp.get("capabilities", [])}
        if not caps & {"mediaInputSource", "samsungvd.mediaInputSource"}:
            continue
        label = " ".join(str(d.get(k) or "") for k in ("label", "name", "deviceTypeName")).lower()
        score = sum(bool(x) and str(x).lower() in label for x in (tv.get("name"), tv.get("model")))
        out.append((score, d))
    out.sort(key=lambda x: -x[0])
    return [d for _, d in out]


async def setup_smartthings(home: Home, d: dict, rep: Report, *, client_id: str | None = None,
                            client_secret: str | None = None, redirect_uri: str = DEFAULT_REDIRECT,
                            transport: httpx.AsyncBaseTransport | None = None) -> str | None:
    """Step 6: OAuth with a refresh token. Returns the chosen device id, or None."""
    tv = d.setdefault("tv", {})
    rep.say("…", "SmartThings: works through Samsung's cloud, so the Mac never has to reach the TV.")
    async with httpx.AsyncClient(timeout=10, transport=transport) as http:
        pat = ""
        if not (client_id and client_secret):
            rep.say("  ", "1) On your phone or Mac open https://account.smartthings.com/tokens , sign in with the")
            rep.say("  ", "   Samsung account the TV uses, Generate new token, tick Devices (all) + Apps (all), copy it.")
            pat = home.prompt("Paste that token (used once, never saved):")
            if not pat:
                rep.say(BAD, "No token pasted: SmartThings skipped.")
                return None
            made = await create_oauth_app(http, pat, redirect_uri)
            if isinstance(made, str):
                rep.say(BAD, "SmartThings refused to create the app (token missing the Apps scope, or expired).")
                rep.say("  ", "   SmartThings said: " + made)
                rep.causes.append("smartthings app not created")
                return None
            client_id, client_secret = made
            rep.say(OK, "Created the SmartThings app 'RedZone Everything'.")
        state = secrets.token_urlsafe(12)
        rep.say("  ", "2) Open this link, sign in, press Authorize:")
        rep.say("  ", "   " + authorize_url(client_id, redirect_uri, state))
        code = code_from(home.prompt("3) Paste the whole address you land on (it contains code=...):"), state)
        if not code:
            rep.say(BAD, "No authorization code found in what you pasted.")
            return None
        auth = tvmod.SmartThingsAuth(transport=transport)
        if not await auth.exchange_code(client_id, client_secret, code, redirect_uri):
            rep.say(BAD, f"SmartThings sign-in failed: {auth.last_error}")
            return None
        rep.say(OK, "Signed in: tokens saved in data/smartthings_oauth.json (owner-only, not in git); "
                    "they renew themselves.")
        token = await auth.token()
        r = await http.get(f"{tvmod.ST_API}/devices", headers={"Authorization": f"Bearer {token}"})
        devices = (r.json() or {}).get("items", []) if r.status_code == 200 else []
    cands = pick_tv(devices, tv)
    if not cands:
        rep.say(BAD, "No TV with an input-source capability in that SmartThings account. Add the TV in the "
                     "SmartThings app (Devices > + > TV) and run this again with --smartthings.")
        return None
    chosen = cands[0]
    if len(cands) > 1:
        for i, c in enumerate(cands, 1):
            rep.say("  ", f"   {i}. {c.get('label') or c.get('name')} ({c.get('deviceId')})")
        a = home.prompt("Which one is the sports TV? [number]")
        try:
            chosen = cands[int(a) - 1]
        except (ValueError, IndexError):
            rep.say(BAD, "No TV chosen: SmartThings device not saved.")
            return None
    tv["smartthings_device_id"] = chosen["deviceId"]
    rep.say(OK, f"SmartThings TV: {chosen.get('label') or chosen.get('name')}")
    return chosen["deviceId"]


async def test_cloud_inputs(home: Home, tv: tvmod.SamsungTV, sticks: list[dict], rep: Report) -> bool:
    """setInputSource for each stick's port, READ BACK from the TV, twice around."""
    good = 0
    ports = [s["hdmi"].upper().replace(" ", "") for s in sticks if s.get("hdmi")]
    for rnd in range(2):
        for p in ports:
            ok = await tv.set_input(p, methods=["smartthings"])
            ev = tv.last_input
            good += ok
            rep.say(OK if ok else BAD, f"SmartThings {p}: " + (f"TV reports {ev.observed} in {ev.total_s:.2f}s"
                                                                 if ok else f"{ev.detail or tv.last_error}"))
    return good == 2 * len(ports) and len(ports) >= 2


async def set_cec(home: Home, sticks: list[dict], on: bool, rep: Report) -> bool:
    """Every stick's HDMI-CEC setting, read back after the change."""
    all_ok = True
    for s in sticks:
        await home.shell(s["host"], CEC_SET.format(v=1 if on else 0))
        ok, out = await home.shell(s["host"], CEC_GET)
        val = out.strip().splitlines()[-1].strip() if ok and out.strip() else "?"
        good = val == ("1" if on else "0")
        all_ok &= good
        rep.say(OK if good else BAD, f"{s.get('name') or s['host']}: HDMI-CEC {'on' if val == '1' else 'off' if val == '0' else 'unknown'}"
                + ("" if good else " — set it by hand: Settings > Display & Sounds > HDMI CEC Device Control"))
    return all_ok


# ------------------------------------------------------------------------------------------ the whole run

def load(path: Path = DEVICES) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def save(d: dict, path: Path = DEVICES) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1))
    os.replace(tmp, path)


async def run(home: Home, *, path: Path = DEVICES, ip: str | None = None, ask: bool = True,
              smartthings: bool | None = None, cec_off: bool | None = None,
              client_id: str | None = None, client_secret: str | None = None,
              redirect_uri: str = DEFAULT_REDIRECT, transport: httpx.AsyncBaseTransport | None = None,
              shots: Path | None = None) -> Report:
    rep = Report()
    d = load(path)
    tv = d.setdefault("tv", {})
    sticks = d.get("sticks") or []
    mac = tv.get("mac") or tv.get("wifi_mac")
    print("\nStep 1 — find the TV")
    found = ip or await find_tv(home, mac, sticks, tv.get("ip"), rep)
    local_ok = False
    if found and smartthings is not True:
        print("\nStep 2 — read the TV's model")
        info = await read_model(home, found, mac, rep)
        if info:
            tv.update({k: v for k, v in info.items() if v is not None})
            tv["ip"] = found
            save(d, path)
            print("\nStep 3 — pair (press Allow on the TV)")
            if await pair(home, found, rep):
                print("\nStep 4 — test the inputs (watch the TV)")
                res = await test_local_inputs(home, found, sticks, rep, ask=ask, shots=shots)
                lat = [x for r in res.values() for x in r["latency_s"]]
                local_ok = local_proven(res, sticks, ask) and bool(tv.get("network_verified"))
                tv["local_input_verified"] = local_ok
                tv["local_input_test"] = {"at": time.time(), "results": res}
                if local_ok:
                    tv["input_method"] = "local"   # proven now: wins over an old "smartthings" (DEV-05)
                    rep.ok.append("local")
                    rep.say(OK, "The TV's own input keys work: this is now the default flip"
                            + (f" (median {sorted(lat)[len(lat)//2]:.2f}s key -> stick on screen)" if lat else ""))
                else:
                    rep.say(BAD, "KEY_HDMIx didn't switch this TV reliably (some models ignore them).")
                    rep.causes.append("HDMI keys ignored")
                    if ask:
                        await try_source_key(home, found, rep)
                save(d, path)
    elif not found:
        rep.say(BAD, "Local control isn't possible from here: " + (", ".join(rep.causes) or "TV not found") + ".")
    want_st = smartthings if smartthings is not None else not local_ok
    if want_st:
        print("\nStep 6 — SmartThings (cloud, with automatic renewal)")
        dev = await setup_smartthings(home, d, rep, client_id=client_id, client_secret=client_secret,
                                      redirect_uri=redirect_uri, transport=transport)
        if dev:
            save(d, path)
            t = tvmod.SamsungTV(st_device=dev, transport=transport, log_path=tvmod.INPUT_LOG)
            if await test_cloud_inputs(home, t, sticks, rep):
                rep.ok.append("smartthings")
                if not local_ok:
                    tv["input_method"] = "smartthings"
                rep.say(OK, "SmartThings switches AND reads back the input"
                        + (": this is now the default flip." if not local_ok else ": it also verifies local keys."))
            save(d, path)
    print("\nStep 7 — HDMI-CEC on the sticks")
    if rep.ok:
        do = cec_off if cec_off is not None else (home.ask("Turn HDMI-CEC OFF on both sticks so the one you leave "
                                                           "never pauses? (recommended)") is not False)
        if do:
            if await set_cec(home, sticks, False, rep):
                tv["sticks_cec_off"] = True
            save(d, path)
    else:
        rep.say(WARN, "No TV path works yet, so HDMI-CEC stays ON: HOME is still the only flip and it needs CEC.")
    print("\nResult")
    if rep.ok:
        rep.say(OK, f"Flip order from the next start: {' -> '.join(rep.ok)} -> HOME (last resort). "
                    "Restart the service to pick it up.")
    else:
        rep.say(BAD, "Still HOME-only. Causes: " + (", ".join(dict.fromkeys(rep.causes)) or "unknown"))
    return rep
