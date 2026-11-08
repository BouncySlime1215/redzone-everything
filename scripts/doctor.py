"""Phase 0 setup checker. Run it any time something seems off:

    .venv/bin/python scripts/doctor.py            # check everything, find devices
    .venv/bin/python scripts/doctor.py --ip 192.168.1.42   # add a stick by IP if the scan misses it

It only reads and connects; it never changes settings on your Mac, sticks, or TV.
Results are saved to data/devices.json for the app to use.
Why: Phase 0 setup checker, to run whenever something seems off (docstring).
"""
from __future__ import annotations

import argparse
import asyncio
import ipaddress
import itertools
import json
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from redzone.tuners.adb import MODELS, Stick, adb_available  # noqa: E402

DEVICES = ROOT / "data" / "devices.json"
OK, WARN, BAD = "✅", "⚠️ ", "❌"


def say(icon: str, text: str) -> None:
    print(f"  {icon} {text}")


def local_network() -> tuple[str | None, ipaddress.IPv4Network | None, str]:
    """IP, subnet and interface of the Mac's main connection."""
    try:
        route = subprocess.run(["route", "-n", "get", "default"], capture_output=True, text=True, timeout=5).stdout
        iface = re.search(r"interface: (\S+)", route).group(1)
        ifc = subprocess.run(["ifconfig", iface], capture_output=True, text=True, timeout=5).stdout
        m = re.search(r"inet (\d+\.\d+\.\d+\.\d+) netmask (0x[0-9a-f]+)", ifc)
        ip, mask = m.group(1), int(m.group(2), 16)
        prefix = bin(mask).count("1")
        return ip, ipaddress.ip_network(f"{ip}/{prefix}", strict=False), iface
    except Exception:
        return None, None, ""


async def port_open(host: str, port: int, timeout: float = 0.6) -> bool:
    try:
        _, w = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        w.close()
        return True
    except (OSError, asyncio.TimeoutError):
        return False


async def scan(net: ipaddress.IPv4Network, port: int) -> list[str]:
    hosts = list(itertools.islice(net.hosts(), 1024))
    sem = asyncio.Semaphore(128)

    async def one(h):
        async with sem:
            return str(h) if await port_open(str(h), port) else None
    return [h for h in await asyncio.gather(*(one(h) for h in hosts)) if h]


async def samsung_info(ip: str) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get(f"http://{ip}:8001/api/v2/")
            if r.status_code == 200:
                d = r.json().get("device", {})
                return {"ip": ip, "name": d.get("name"), "model": d.get("modelName"),
                        "os": d.get("OS"), "token_auth": d.get("TokenAuthSupport"),
                        "power": d.get("PowerState"), "wifi_mac": d.get("wifiMac")}
    except Exception:
        return None
    return None


def select_tv(tvs: list[dict], previous: dict | None, explicit_ip: str | None) -> dict | None:
    """A discovered TV is not consent to select it (another may belong to the Xbox)."""
    if explicit_ip:
        return next((tv for tv in tvs if tv["ip"] == explicit_ip), None)
    if previous:
        mac = previous.get("wifi_mac")
        return next((tv for tv in tvs if (mac and tv.get("wifi_mac") == mac)
                     or (not mac and tv["ip"] == previous.get("ip"))), None)
    return None


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip", action="append", default=[], help="Fire TV stick IP to add (repeatable)")
    ap.add_argument("--tv-ip", type=lambda s: str(ipaddress.IPv4Address(s)),
                    help="Explicitly select this Samsung TV; other TVs are ignored")
    ap.add_argument("--display-type", choices=("monitor", "samsung"),
                    help="Select monitor to clear a previously saved Samsung target")
    ap.add_argument("--no-scan", action="store_true")
    a = ap.parse_args()
    old = json.loads(DEVICES.read_text()) if DEVICES.exists() else {}
    if a.display_type == "monitor":
        old.pop("tv", None)
        old["display_type"] = "monitor"
        if a.tv_ip:
            ap.error("--display-type monitor cannot select a Samsung --tv-ip")

    print("\nRedZone Everything — setup check\n")
    print("1. Your Mac")
    v = sys.version_info
    say(OK if v >= (3, 12) else BAD, f"Python {v.major}.{v.minor} " + ("" if v >= (3, 12) else "(need 3.12+)"))
    say(OK if shutil.which("brew") else WARN, "Homebrew " + ("found" if shutil.which("brew") else "not found"))
    adb = await adb_available()
    say(OK if adb else BAD, "adb " + ("found" if adb else "missing — run: brew install --cask android-platform-tools"))
    say(OK, f"macOS {platform.mac_ver()[0]}")

    print("\n2. Network")
    ip, net, iface = local_network()
    if not ip:
        say(BAD, "Couldn't find your network connection")
        return
    say(OK, f"Mac is {ip} on {iface} ({net})")
    # 100.64.0.0/10 alone proves nothing: Nick's home router hands out 100.110.165.x. Compare with the
    # saved home network instead, and check that Tailscale isn't taking the routes to it.
    from redzone.network import Probe, assess, home_from_devices
    nst = assess(Probe(), home_from_devices(old))
    if nst.where == "away":
        say(WARN, nst.message + " Run this check from home.")
    elif nst.routes_broken:
        say(BAD, nst.message)
    elif nst.where == "home":
        say(OK, nst.message)
    elif ipaddress.ip_address(ip) in ipaddress.ip_network("100.64.0.0/10") and nst.tailscale:
        say(WARN, "This network uses 100.64.0.0/10 addresses, the same range as Tailscale. If devices "
                  "aren't found, check `.venv/bin/python -m redzone.network`.")
    vpn = subprocess.run(["scutil", "--nc", "list"], capture_output=True, text=True).stdout
    for line in vpn.splitlines():
        if "(Connected)" in line:
            name = re.search(r'"([^"]+)"', line)
            say(WARN, f"VPN connected: {name.group(1) if name else line.strip()} — if devices aren't found, "
                      "turn it off while you use RedZone (Tailscale is usually fine).")

    print("\n3. Fire TV sticks (ADB on port 5555)")
    hosts = list(a.ip)
    if not a.no_scan and net is not None:
        print(f"     scanning {net} … (about 10 seconds)")
        hosts += [h for h in await scan(net, 5555) if h not in hosts]
    sticks = []
    if not hosts:
        say(WARN, "No sticks found with ADB on. On each stick: Settings → My Fire TV → About → click the "
                  "device name 7 times → back → Developer Options → ADB Debugging ON. Then run this again.")
    for h in hosts:
        st = Stick(h if ":" in h else f"{h}:5555")
        if not adb:
            break
        ok = await st.connect()
        if st.state == "unauthorized" or not ok:
            await asyncio.sleep(1)
            ok = await st.connect()
        if st.state == "unauthorized":
            say(WARN, f"{h}: waiting for you — on the TV, pick 'Always allow from this computer' → OK, then rerun.")
            await st.close()
            continue
        if not ok:
            say(BAD, f"{h}: couldn't connect ({st.state}) — {st.connect_error}")
            if "refused" in st.connect_error.lower():
                say(WARN, "    TCP was refused before ADB authorization. Check the current IP, debugging "
                          "service and network communication; an approval prompt cannot appear yet.")
            await st.close()
            continue
        await st.identify()
        vega = "Vega" in st.os
        say(BAD if vega else OK, f"{h}: {st.name or st.model} — {st.os}" +
            (" — Vega OS is unsupported by this app's ADB controller" if vega else ""))
        say(OK if st.session_status().startswith("working") else WARN, f"    fast commands: {st.session_status()}")
        for role in ("youtubetv", "prime", "youtube"):
            pkg = st.packages.get(role)
            say(OK if pkg else WARN, f"    {role}: {pkg or 'not installed'}")
        pb = await st.playback()
        say(OK, f"    playing now: {pb['state']}" + (f" ({pb['pkg']})" if pb.get("pkg") else ""))
        port = await st.hdmi_port()
        say(OK if port else WARN, f"    HDMI topology port: {port or 'unknown'} (does not prove the selected display input)")
        await st.close()   # its always-open adb shell
        sticks.append({"host": st.host, "name": st.name, "model": st.model, "os": st.os,
                       "packages": st.packages, "controllable": not vega, "hdmi": port})

    print("\n4. Display")
    if a.display_type == "monitor" or old.get("display_type") == "monitor":
        say(OK, "Monitor selected. Use its manually selected HDMI input; Samsung control is disabled.")
    tvs = []
    cand = [a.tv_ip] if a.tv_ip else []
    if net is not None and not a.no_scan and a.display_type != "monitor":
        cand += [h for h in await scan(net, 8001) if h not in cand]
    for h in cand:
        info = await samsung_info(h)
        if info:
            tvs.append(info)
            say(OK, f"{info['name']} ({info['model']}) at {h} — {info['os']}, power {info['power']}")
    if not tvs and a.display_type != "monitor" and old.get("display_type") != "monitor":
        say(WARN, "No Samsung TV found (it must be on, same Wi-Fi). Only needed for two-stick mode.")
    selected = select_tv(tvs, old.get("tv"), a.tv_ip)
    if selected:
        say(OK, f"Selected TV: {selected['name']} at {selected['ip']}")
    elif tvs or a.tv_ip:
        say(WARN, "TV selection unchanged. Rerun with --tv-ip ADDRESS for the TV with your Fire Stick; "
                  "discovered TVs are never selected automatically.")

    print("\n5. NFL Sunday Ticket")
    say(WARN, "Sunday Ticket + RedZone is configured, but account access must be verified on your selected stick")

    DEVICES.parent.mkdir(parents=True, exist_ok=True)
    if sticks:
        controllable = [s for s in sticks if s["controllable"]]
        old["sticks"] = controllable + [s for s in sticks if not s["controllable"]]
    if selected:
        old["tv"] = selected
        old["display_type"] = "samsung"
    tmp = DEVICES.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(old, indent=1))
    tmp.replace(DEVICES)
    n = len([s for s in sticks if s["controllable"]])
    print(f"\nSaved to data/devices.json. Controllable sticks: {n} → "
          f"{'single-stick available; dual-stick requires verified display input control' if n >= 2 else 'single-stick available' if n == 1 else 'none yet'}.\n")


if __name__ == "__main__":
    asyncio.run(main())
