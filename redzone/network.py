"""Where is this Mac, and can it reach the sticks and the TV? Read-only facts about the network.

Three things the app used to get wrong:

1. Away from home. The Mac is a laptop that goes to school by day. There the sticks can't answer, and
   that's not a fault: say "away from home" instead of "stick offline", don't reconnect in a loop, and
   don't scan a campus network for Fire TVs.
2. Tailscale stealing the home network. The home router hands out 100.110.165.x, which sits inside
   100.64.0.0/10, the range Tailscale uses for its own tunnel. If the Mac sends traffic for a stick
   into the tunnel instead of out over Wi-Fi, every stick looks dead. Detect it and say exactly how
   to fix it (or fix it, when run with --fix from a terminal).
3. New addresses. The router moves devices around (DHCP). A device's MAC address doesn't change, so
   find_by_mac() finds the sticks and the TV by MAC: from the ARP table, and if they aren't in it,
   after one quick UDP nudge to each home address (no ports opened, nothing paired, nothing prompted).

How "home" is known: data/devices.json "home" {"subnet", "gateway", "router_mac"} when present
(learned the first time a saved stick answers on the home network), else the /24 of the saved
devices' addresses. The router's MAC is what tells home apart from another network that happens to
use the same numbers.

Everything here has a timeout and never raises: on Linux (CI) or with a command missing, the answer is
"unknown" and the app behaves as it did before.

From a terminal on the Mac:
    .venv/bin/python -m redzone.network              where am I, and do routes to home go the right way?
    .venv/bin/python -m redzone.network --find MAC   which address has this MAC right now?
    .venv/bin/python -m redzone.network --fix        add the missing home route (asks for your password)
    .venv/bin/python -m redzone.network --save-tv-mac 8c:79:f5:8e:e2:5f    follow the TV by its MAC

Why: first added in 7f7250bd (2026-11-03) as "Network awareness: away from home, Tailscale route overlap, find devices by MAC"; no separate decision is recorded for this module.
"""
from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import logging
import os
import re
import socket
import subprocess
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DEVICES = ROOT / "data" / "devices.json"

TAILNET = ipaddress.ip_network("100.64.0.0/10")
SWEEP_MAX = 1024          # never nudge more addresses than this (campus networks are huge)
SWEEP_SETTLE_S = 1.5      # time for ARP replies to land after the nudge
CHECK_EVERY_S = 30.0      # NetWatch re-reads the Mac's network this often
# Interfaces that are never "the Wi-Fi / Ethernet the TV is on".
TUNNEL = re.compile(r"^(utun|ipsec|ppp|gif|stf|tun|tap|wg|tailscale|zt)")
NOT_LAN = re.compile(r"^(lo|utun|ipsec|ppp|gif|stf|tun|tap|wg|tailscale|zt|awdl|llw|anpi|bridge|ap\d|docker|veth)")

WHERE = ("home", "away", "offline", "unknown")


# ============================================================================ small helpers


def norm_mac(mac: Any) -> str:
    """'4C:EF:C0:2:34:56' -> '4c:ef:c0:02:34:56' ('' if it isn't a MAC). macOS's arp drops leading zeros."""
    parts = re.split(r"[:-]", str(mac or "").strip().lower())
    try:
        vals = [int(p, 16) for p in parts]
    except ValueError:
        return ""
    if len(vals) != 6 or not all(0 <= v <= 255 for v in vals):
        return ""
    if vals == [0] * 6 or vals == [255] * 6:
        return ""   # "incomplete" / broadcast entries are not a device
    return ":".join(f"{v:02x}" for v in vals)


def host_ip(host: str) -> str:
    """'100.110.165.150:5555' -> '100.110.165.150'."""
    return (host or "").strip().rsplit(":", 1)[0] if (host or "").count(":") == 1 else (host or "").strip()


def _v4(ip: str) -> ipaddress.IPv4Address | None:
    try:
        a = ipaddress.ip_address(host_ip(ip))
    except ValueError:
        return None
    return a if isinstance(a, ipaddress.IPv4Address) else None


def _net(s: Any) -> ipaddress.IPv4Network | None:
    if isinstance(s, ipaddress.IPv4Network):
        return s
    try:
        n = ipaddress.ip_network(str(s), strict=False)
    except ValueError:
        return None
    return n if isinstance(n, ipaddress.IPv4Network) else None


def _run(cmd: list[str], timeout: float = 5.0) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""


# ============================================================================ reading the Mac's network


ARP_LINE = re.compile(r"\((\d+\.\d+\.\d+\.\d+)\) at ([0-9a-fA-F:]+)")
IFCONFIG_IFACE = re.compile(r"^(\S+?):? flags=", re.M)
IFCONFIG_INET = re.compile(r"inet (\d+\.\d+\.\d+\.\d+)(?: --> \S+)? netmask (0x[0-9a-fA-F]+|\d+\.\d+\.\d+\.\d+)")


def parse_arp(out: str) -> dict[str, str]:
    """`arp -an` -> {ip: mac}. "(incomplete)" entries are skipped."""
    return {ip: m for ip, mac in ARP_LINE.findall(out or "") if (m := norm_mac(mac))}


def parse_ifconfig(out: str) -> dict[str, list[tuple[str, int]]]:
    """`ifconfig -a` (macOS) -> {iface: [(ipv4, prefix_len), ...]}."""
    res: dict[str, list[tuple[str, int]]] = {}
    marks = [(m.start(), m.group(1)) for m in IFCONFIG_IFACE.finditer(out or "")]
    for i, (start, name) in enumerate(marks):
        block = out[start: marks[i + 1][0] if i + 1 < len(marks) else len(out)]
        for ip, mask in IFCONFIG_INET.findall(block):
            try:
                prefix = bin(int(mask, 16)).count("1") if mask.startswith("0x") else \
                    ipaddress.ip_network(f"0.0.0.0/{mask}").prefixlen
            except ValueError:
                continue
            res.setdefault(name, []).append((ip, prefix))
    return res


def parse_route_get(out: str) -> tuple[str, str]:
    """`route -n get X` -> (gateway, interface); '' for what it doesn't say."""
    gw = re.search(r"gateway: (\S+)", out or "")
    iface = re.search(r"interface: (\S+)", out or "")
    return (gw.group(1) if gw else ""), (iface.group(1) if iface else "")


class Probe:
    """The real Mac (macOS commands). Tests pass a fake with the same four methods."""

    def interfaces(self) -> dict[str, list[tuple[str, int]]]:
        return parse_ifconfig(_run(["ifconfig", "-a"]))

    def default_route(self) -> tuple[str, str]:
        """(gateway, interface) of the default route; ('', '') with no network."""
        return parse_route_get(_run(["route", "-n", "get", "default"]))

    def route_iface(self, ip: str) -> str:
        """Which interface traffic to `ip` actually leaves through ('' when unknown)."""
        return parse_route_get(_run(["route", "-n", "get", ip]))[1]

    def arp(self) -> dict[str, str]:
        return parse_arp(_run(["arp", "-an"]))

    def nudge(self, hosts: Iterable[str]) -> None:
        """One tiny UDP datagram to each host (discard port 9). The reply doesn't matter: sending it makes
        the Mac ask "who has this address?", which puts every device that's there into the ARP table."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        except OSError:
            return
        try:
            s.setblocking(False)
            for h in hosts:
                try:
                    s.sendto(b"\0", (h, 9))
                except OSError:
                    pass   # host down / no route / buffer full: fine, ARP still went out for most
        finally:
            s.close()


def arp_table(probe: Probe | None = None) -> dict[str, str]:
    """IP -> MAC for devices this Mac has talked to recently."""
    try:
        return (probe or Probe()).arp()
    except Exception:
        return {}


# ============================================================================ home


@dataclass
class Home:
    subnet: ipaddress.IPv4Network | None = None
    gateway: str = ""
    router_mac: str = ""
    devices: list[str] = field(default_factory=list)   # saved stick/TV addresses (for the route check)
    learned: bool = False                               # subnet came from devices.json "home" (not guessed)

    @property
    def known(self) -> bool:
        return self.subnet is not None

    def label(self) -> str:
        if self.subnet is None:
            return "your home network"
        if self.subnet.prefixlen == 24:
            return ".".join(str(self.subnet.network_address).split(".")[:3]) + ".x"
        return str(self.subnet)


def _saved_ips(d: dict) -> list[str]:
    ips = []
    for e in d.get("sticks") or []:
        if isinstance(e, dict) and _v4(e.get("host", "")):
            ips.append(host_ip(e["host"]))
    tv = d.get("tv") if isinstance(d.get("tv"), dict) else {}
    if _v4(tv.get("ip", "")):
        ips.append(host_ip(tv["ip"]))
    return ips


def home_from_devices(d: dict) -> Home:
    """The home network from data/devices.json: its "home" block, else the /24 most saved devices share."""
    d = d if isinstance(d, dict) else {}
    h = d.get("home") if isinstance(d.get("home"), dict) else {}
    ips = _saved_ips(d)
    subnet = _net(h.get("subnet")) if h.get("subnet") else None
    learned = subnet is not None
    if subnet is None and ips:
        top, _ = Counter(str(ipaddress.ip_network(f"{ip}/24", strict=False)) for ip in ips).most_common(1)[0]
        subnet = _net(top)
    gw = host_ip(h.get("gateway", "")) if _v4(h.get("gateway", "")) else ""
    return Home(subnet, gw, norm_mac(h.get("router_mac")), ips, learned)


def read_devices(path: Path) -> dict:
    try:
        d = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


# ============================================================================ where am I?


@dataclass
class NetState:
    where: str = "unknown"            # home | away | offline | unknown
    ip: str = ""                      # the Mac's own address on the network it's using
    iface: str = ""                   # ...and which interface that is (en0)
    gateway: str = ""                 # default route's gateway
    home: str = ""                    # "100.110.165.x"
    home_subnet: str = ""
    router_mac: str = ""              # MAC the home gateway answers with right now ('' if unseen)
    tailscale: bool = False           # a tunnel interface has a 100.64/10 address
    stolen: list[str] = field(default_factory=list)   # home addresses whose traffic goes into a tunnel
    stolen_via: str = ""              # ...which interface takes it (utun4)
    fix: str = ""                     # the command that puts the home route back
    message: str = ""                 # one plain sentence for the phone
    checked_at: float = 0.0

    @property
    def away(self) -> bool:
        return self.where == "away"

    @property
    def routes_broken(self) -> bool:
        return bool(self.stolen)

    def view(self) -> dict:
        d = asdict(self)
        d["away"], d["routes_broken"] = self.away, self.routes_broken
        return d


def _lan_addrs(ifaces: dict[str, list[tuple[str, int]]]) -> list[tuple[str, str, int]]:
    """(iface, ip, prefix) for real LAN interfaces (Wi-Fi, Ethernet), not tunnels or loopback."""
    out = []
    for name, addrs in ifaces.items():
        if NOT_LAN.match(name):
            continue
        for ip, prefix in addrs:
            a = _v4(ip)
            if a is None or a.is_loopback or a.is_link_local:
                continue
            out.append((name, ip, prefix))
    return out


def _tunnel_on_tailnet(ifaces: dict[str, list[tuple[str, int]]]) -> bool:
    return any(TUNNEL.match(n) and any((a := _v4(ip)) is not None and a in TAILNET for ip, _ in addrs)
               for n, addrs in ifaces.items())


def assess(probe: Probe, home: Home, clock: Callable[[], float] = time.time) -> NetState:
    """One look at the Mac's network. Never raises."""
    st = NetState(home=home.label(), home_subnet=str(home.subnet or ""), checked_at=clock())
    try:
        ifaces = probe.interfaces() or {}
        gw, dev = probe.default_route()
    except Exception as e:   # a fake or a platform without these commands
        log.debug("network: couldn't read interfaces: %s", e)
        st.message = "Couldn't read this computer's network settings"
        return st
    st.gateway, st.tailscale = gw, _tunnel_on_tailnet(ifaces)
    lan = _lan_addrs(ifaces)
    if not lan and not gw:
        st.where, st.message = ("offline", "This Mac isn't connected to any network") if ifaces else \
            ("unknown", "Couldn't read this computer's network settings")
        return st
    # The address the Mac uses: the one on the default route's interface, else the first LAN one.
    mine = next((x for x in lan if x[0] == dev), lan[0] if lan else None)
    if mine:
        st.iface, st.ip = mine[0], mine[1]
    if not home.known:
        st.message = "No sticks or TV saved yet, so the home network isn't known"
        return st
    at_home = next((x for x in lan if (a := _v4(x[1])) is not None and a in home.subnet), None)
    try:
        arp = probe.arp() or {}
    except Exception:
        arp = {}
    if at_home is None and home.router_mac and mine and arp.get(gw) == home.router_mac:
        # Our own router, new numbers (it was reset or reconfigured): home, on the new subnet.
        new = ipaddress.ip_network(f"{mine[1]}/{max(mine[2], 24)}", strict=False)
        st.where, st.home_subnet, st.home, st.router_mac = "home", str(new), Home(new).label(), home.router_mac
        st.message = (f"On the home network, but the router now uses {Home(new).label()} instead of "
                      f"{home.label()}: looking for the sticks and TV there")
        _check_routes(probe, Home(new, gw, home.router_mac, []), st)
        return st
    if at_home is None:
        st.where = "away"
        st.message = (f"Away from home: this Mac is on {st.ip or 'another network'}, not {home.label()}. "
                      "The sticks and TV will reconnect when it's back on the home Wi-Fi.")
        return st
    st.iface, st.ip = at_home[0], at_home[1]
    router_ip = home.gateway or (gw if (a := _v4(gw)) is not None and a in home.subnet else "")
    st.router_mac = arp.get(router_ip, "") if router_ip else ""
    if home.router_mac and st.router_mac and st.router_mac != home.router_mac:
        # Same numbers, different router: someone else's network (a friend's, a hotel) that also uses them.
        st.where = "away"
        st.message = (f"Away from home: this network uses {home.label()} too, but its router isn't yours. "
                      "The sticks and TV will reconnect when the Mac is back on the home Wi-Fi.")
        return st
    st.where = "home"
    _check_routes(probe, home, st)
    if not st.message:
        st.message = f"On the home network ({st.ip} on {st.iface})"
    return st


def _check_routes(probe: Probe, home: Home, st: NetState) -> None:
    """At home: does traffic to the sticks/TV/router leave through the home Wi-Fi? If a tunnel (Tailscale's
    100.64/10 route) takes it instead, no stick can ever answer, so name the problem and the fix."""
    targets = list(dict.fromkeys([*home.devices, *([home.gateway] if home.gateway else [])]))
    if not targets and home.subnet is not None:
        targets = [str(next(home.subnet.hosts()))]
    for ip in targets:
        a = _v4(ip)
        if a is None or home.subnet is None or a not in home.subnet:
            continue
        try:
            via = probe.route_iface(ip)
        except Exception:
            via = ""
        if via and via != st.iface:
            st.stolen.append(ip)
            st.stolen_via = st.stolen_via or via
    if not st.stolen:
        return
    st.fix = f"sudo route -n add -net {home.subnet} -interface {st.iface}"
    who = "Tailscale" if (TUNNEL.match(st.stolen_via) and (st.tailscale or home.subnet.subnet_of(TAILNET))) \
        else st.stolen_via
    st.message = (f"{who} is sending traffic for the home devices ({', '.join(st.stolen[:3])}) through "
                  f"{st.stolen_via} instead of {st.iface}, so they look offline. Fix: run `{st.fix}` "
                  "(or `.venv/bin/python -m redzone.network --fix`), or turn off Tailscale's "
                  "'Use Tailscale subnets' while you watch.")


# ============================================================================ find a device by its MAC


def _sweep_hosts(subnet: ipaddress.IPv4Network) -> list[str]:
    if subnet.num_addresses > SWEEP_MAX + 2:
        return []
    return [str(h) for h in subnet.hosts()]


def lan_subnet(probe: Probe) -> ipaddress.IPv4Network | None:
    """The Mac's own LAN, clamped to the /24 around it (never sweep a campus /16)."""
    try:
        ifaces = probe.interfaces() or {}
        _, dev = probe.default_route()
    except Exception:
        return None
    lan = _lan_addrs(ifaces)
    mine = next((x for x in lan if x[0] == dev), lan[0] if lan else None)
    if not mine:
        return None
    return ipaddress.ip_network(f"{mine[1]}/{max(mine[2], 24)}", strict=False)


def _pick(arp: dict[str, str], mac: str, subnet: ipaddress.IPv4Network | None, prefer: str = "") -> str | None:
    hits = sorted(ip for ip, m in arp.items() if m == mac and (subnet is None or (_v4(ip) or 0) in subnet))
    if not hits:
        return None
    return prefer if prefer in hits else hits[0]


async def find_by_mac(mac: str, subnet: Any = None, probe: Probe | None = None, *, sweep: bool = True,
                      prefer: str = "") -> str | None:
    """The IPv4 address that `mac` has right now, or None.

    First the ARP table (free). If it isn't there and `sweep` is on, nudge every address in `subnet`
    (default: the Mac's own LAN, at most a /24 around it) with one UDP datagram, wait ~1.5 s, and read
    the ARP table again. `prefer`: when the MAC shows up at several addresses (a stale entry), return
    this one if it's among them. Never raises."""
    want = norm_mac(mac)
    if not want:
        return None
    probe = probe or Probe()
    net = _net(subnet) if subnet is not None else None
    try:
        hit = _pick(await asyncio.to_thread(probe.arp), want, net, host_ip(prefer))
        if hit or not sweep:
            return hit
        net = net or await asyncio.to_thread(lan_subnet, probe)
        hosts = _sweep_hosts(net) if net is not None else []
        if not hosts:
            return None
        await asyncio.to_thread(probe.nudge, hosts)
        await asyncio.sleep(SWEEP_SETTLE_S)
        return _pick(await asyncio.to_thread(probe.arp), want, net, host_ip(prefer))
    except Exception as e:
        log.debug("find_by_mac(%s) failed: %s", want, e)
        return None


# ============================================================================ the watcher the app holds


class NetWatch:
    """The app's live view of where the Mac is. `await refresh()` is cheap (four quick commands) and
    cached for CHECK_EVERY_S; `state` is the last answer. Holds no locks and never raises."""

    def __init__(self, devices_path: Path = DEVICES, probe: Probe | None = None,
                 clock: Callable[[], float] = time.time, every_s: float = CHECK_EVERY_S):
        self.path, self.probe, self.clock, self.every_s = Path(devices_path), probe or Probe(), clock, every_s
        self.state = NetState()
        self.since = clock()           # when `state.where` last changed
        self._said: tuple[str, str] | None = None
        self._busy = False

    @property
    def away(self) -> bool:
        return self.state.away

    def home(self) -> Home:
        return home_from_devices(read_devices(self.path))

    async def refresh(self, force: bool = False) -> NetState:
        now = self.clock()
        if self._busy or (not force and self.state.checked_at and now - self.state.checked_at < self.every_s):
            return self.state
        self._busy = True
        try:
            st = await asyncio.to_thread(assess, self.probe, self.home(), self.clock)
        except Exception as e:
            log.warning("network check failed: %s", e)
            st = NetState(message="Couldn't read this computer's network settings", checked_at=now)
        finally:
            self._busy = False
        if st.where != self.state.where:
            self.since = now
        self.state = st
        key = (st.where, ",".join(st.stolen))
        if key != self._said:
            self._said = key
            (log.warning if st.where == "away" or st.stolen else log.info)("Network: %s", st.message)
        return st

    async def run(self) -> None:
        while True:
            await self.refresh()
            await asyncio.sleep(self.every_s)

    def view(self) -> dict:
        v = self.state.view()
        v["since"] = self.since
        return v

    def learn_into(self, d: dict, connected: Iterable[str]) -> bool:
        """At home with a saved stick answering: remember the home subnet, gateway and router MAC in
        devices.json (the caller writes the file). The router MAC is what tells a different network with
        the same numbers apart later. Returns True when `d` changed."""
        st = self.state
        if st.where != "home" or not st.home_subnet:
            return False
        subnet = _net(st.home_subnet)
        ips = {host_ip(h) for h in connected}
        if subnet is None or not any((a := _v4(ip)) is not None and a in subnet for ip in ips):
            return False
        gw = st.gateway if (a := _v4(st.gateway)) is not None and a in subnet else ""
        h = d.get("home") if isinstance(d.get("home"), dict) else {}
        new = {"subnet": str(subnet), "gateway": gw, "router_mac": st.router_mac if gw else ""}
        changed = {k: v for k, v in new.items() if v and not h.get(k)}
        if h.get("router_mac") and norm_mac(h["router_mac"]) == st.router_mac:
            # Same router, renumbered: follow its new subnet and gateway.
            changed.update({k: new[k] for k in ("subnet", "gateway") if new[k] and h.get(k) != new[k]})
        if not changed:
            return False
        d["home"] = {**h, **changed}
        return True


def fix_route(st: NetState, run: Callable[[list[str]], int] | None = None) -> tuple[bool, str]:
    """Put the home route back on the Wi-Fi interface. Needs admin rights: from a terminal this asks
    for your password (sudo); the background service can't, so it only reports the command."""
    if not st.stolen or not st.fix:
        return True, "Nothing to fix: traffic to the home devices already leaves through " + (st.iface or "the LAN")
    cmd = st.fix.split()
    if os.geteuid() == 0:
        cmd = cmd[1:]   # already root: no sudo
    elif not sys.stdin.isatty() and run is None:
        return False, f"Needs your password: run `{st.fix}` in Terminal"
    runner = run or (lambda c: subprocess.run(c, timeout=60).returncode)
    try:
        rc = runner(cmd)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"Couldn't run `{st.fix}`: {e}"
    return rc == 0, ("Home route added" if rc == 0 else f"`{st.fix}` failed (exit {rc})")


# ============================================================================ command line


async def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m redzone.network", description=__doc__.split("\n\n")[0])
    ap.add_argument("--devices", type=Path, default=DEVICES)
    ap.add_argument("--find", metavar="MAC", help="print the address this MAC has right now")
    ap.add_argument("--subnet", help="where to look for --find (default: the home network, else this LAN)")
    ap.add_argument("--fix", action="store_true", help="add the missing home route (uses sudo)")
    ap.add_argument("--save-tv-mac", metavar="MAC",
                    help="save the TV's MAC in devices.json so the app can follow it to new addresses")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.save_tv_mac:
        mac = norm_mac(a.save_tv_mac)
        if not mac:
            print(f"{a.save_tv_mac!r} isn't a MAC address")
            return 2
        d = read_devices(a.devices)
        d["tv"] = {**(d.get("tv") if isinstance(d.get("tv"), dict) else {}), "mac": mac}
        a.devices.parent.mkdir(parents=True, exist_ok=True)
        tmp = a.devices.with_name(f".{a.devices.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(d, indent=1))
        os.replace(tmp, a.devices)
        print(f"Saved the TV's MAC {mac}")
        return 0
    probe = Probe()
    home = home_from_devices(read_devices(a.devices))
    if a.find:
        subnet = a.subnet or (home.subnet if home.known else None)
        ip = await find_by_mac(a.find, subnet, probe)
        print(json.dumps({"mac": norm_mac(a.find), "ip": ip}) if a.json else (ip or "not found"))
        return 0 if ip else 1
    st = assess(probe, home)
    if a.json:
        print(json.dumps(st.view(), indent=1))
    else:
        print(f"{st.where.upper()}: {st.message}")
        if st.tailscale:
            print("  Tailscale (or another tunnel on 100.64.0.0/10) is running.")
    if a.fix:
        ok, msg = fix_route(st)
        print(msg)
        return 0 if ok else 1
    return 0 if st.where == "home" and not st.stolen else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
