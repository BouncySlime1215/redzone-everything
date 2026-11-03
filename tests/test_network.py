"""Network awareness: away from home, Tailscale taking the home routes, and finding devices by MAC.
Everything runs on fakes: no real network, works on Linux."""
import asyncio
import ipaddress
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.policy import Obs, guard, rules, Verdict
from redzone import network as nw
from redzone.autonomy import DeviceHealer, Hooks
from redzone.network import NetState, NetWatch, assess, find_by_mac, home_from_devices, norm_mac

HOME_DEVICES = {"sticks": [{"host": "100.110.165.150:5555", "mac": "aa:bb:cc:00:01:50"},
                           {"host": "100.110.165.181:5555", "mac": "aa:bb:cc:00:01:81"}],
                "tv": {"ip": "100.110.165.151", "mac": "8c:79:f5:8e:e2:5f"}}

# Real macOS shapes (trimmed).
IFCONFIG_HOME = """lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384
\tinet 127.0.0.1 netmask 0xff000000
en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tether 3c:22:fb:11:22:33
\tinet 100.110.165.20 netmask 0xffffff00 broadcast 100.110.165.255
utun4: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST> mtu 1280
\tinet 100.101.7.9 --> 100.101.7.9 netmask 0xffffffff
"""
IFCONFIG_CAMPUS = IFCONFIG_HOME.replace("100.110.165.20 netmask 0xffffff00 broadcast 100.110.165.255",
                                        "10.23.4.5 netmask 0xfffff000 broadcast 10.23.15.255")
ROUTE_DEFAULT_HOME = """   route to: default
destination: default
       mask: default
    gateway: 100.110.165.1
  interface: en0
"""
ARP_HOME = """? (100.110.165.1) at 0:11:22:33:44:55 on en0 ifscope [ethernet]
? (100.110.165.150) at aa:bb:cc:0:1:50 on en0 ifscope [ethernet]
? (100.110.165.99) at (incomplete) on en0 ifscope [ethernet]
"""


class FakeProbe(nw.Probe):
    """A Mac whose interfaces, routes and ARP table the test sets. nudge() can 'reveal' ARP entries."""

    def __init__(self, ifconfig=IFCONFIG_HOME, default=("100.110.165.1", "en0"), arp=None, routes=None,
                 reveal=None):
        self.ifc, self.default, self.table = ifconfig, default, dict(arp or {})
        self.routes, self.reveal, self.nudged = dict(routes or {}), dict(reveal or {}), []

    def interfaces(self):
        return nw.parse_ifconfig(self.ifc)

    def default_route(self):
        return self.default

    def route_iface(self, ip):
        return self.routes.get(ip, "en0")

    def arp(self):
        return dict(self.table)

    def nudge(self, hosts):
        self.nudged = list(hosts)
        self.table.update(self.reveal)


@pytest.fixture(autouse=True)
def fast_sweep(monkeypatch):
    monkeypatch.setattr(nw, "SWEEP_SETTLE_S", 0)


# --- parsing --------------------------------------------------------------------------------

def test_parsers_read_real_macos_output():
    ifs = nw.parse_ifconfig(IFCONFIG_HOME)
    assert ifs["en0"] == [("100.110.165.20", 24)] and ifs["utun4"] == [("100.101.7.9", 32)]
    assert nw.parse_route_get(ROUTE_DEFAULT_HOME) == ("100.110.165.1", "en0")
    arp = nw.parse_arp(ARP_HOME)
    assert arp == {"100.110.165.1": "00:11:22:33:44:55", "100.110.165.150": "aa:bb:cc:00:01:50"}
    assert norm_mac("8C:79:F5:8E:E2:5F") == "8c:79:f5:8e:e2:5f"
    assert norm_mac("0:0:0:0:0:0") == "" and norm_mac("ff:ff:ff:ff:ff:ff") == "" and norm_mac("nope") == ""


def test_home_is_learned_block_else_the_saved_devices_slash_24():
    h = home_from_devices(HOME_DEVICES)
    assert str(h.subnet) == "100.110.165.0/24" and h.label() == "100.110.165.x" and not h.learned
    h = home_from_devices({**HOME_DEVICES, "home": {"subnet": "100.110.165.0/24", "router_mac": "0:11:22:33:44:55"}})
    assert h.learned and h.router_mac == "00:11:22:33:44:55"
    assert not home_from_devices({}).known and not home_from_devices({"sticks": [{"host": "garbage"}]}).known
    assert not home_from_devices("not a dict").known


# --- where am I ---------------------------------------------------------------------------

def test_at_home():
    st = assess(FakeProbe(arp=nw.parse_arp(ARP_HOME)), home_from_devices(HOME_DEVICES))
    assert st.where == "home" and st.ip == "100.110.165.20" and st.iface == "en0"
    assert st.router_mac == "00:11:22:33:44:55" and not st.stolen and st.tailscale


def test_at_school_is_away_not_offline():
    st = assess(FakeProbe(IFCONFIG_CAMPUS, ("10.23.0.1", "en0")), home_from_devices(HOME_DEVICES))
    assert st.away and "10.23.4.5" in st.message and "100.110.165.x" in st.message
    assert "offline" not in st.message.lower()


def test_same_numbers_different_router_is_away():
    d = {**HOME_DEVICES, "home": {"subnet": "100.110.165.0/24", "gateway": "100.110.165.1",
                                  "router_mac": "00:11:22:33:44:55"}}
    st = assess(FakeProbe(arp={"100.110.165.1": "de:ad:be:ef:00:01"}), home_from_devices(d))
    assert st.away and "router isn't yours" in st.message
    # The router's MAC not in ARP yet: can't tell, trust the subnet.
    assert assess(FakeProbe(arp={}), home_from_devices(d)).where == "home"


def test_full_tunnel_vpn_on_home_wifi_is_still_home():
    p = FakeProbe(default=("100.101.7.9", "utun4"))
    st = assess(p, home_from_devices(HOME_DEVICES))
    assert st.where == "home" and st.iface == "en0"


def test_no_network_and_unreadable_and_nothing_saved():
    assert assess(FakeProbe("lo0: flags=8049<UP>\n\tinet 127.0.0.1 netmask 0xff000000\n", ("", "")),
                  home_from_devices(HOME_DEVICES)).where == "offline"
    assert assess(FakeProbe("", ("", "")), home_from_devices(HOME_DEVICES)).where == "unknown"
    assert assess(FakeProbe(), home_from_devices({})).where == "unknown"

    class Broken(FakeProbe):
        def interfaces(self):
            raise RuntimeError("no ifconfig on this platform")
    assert assess(Broken(), home_from_devices(HOME_DEVICES)).where == "unknown"


def test_tailscale_taking_the_home_routes_is_named_with_the_fix():
    p = FakeProbe(routes={"100.110.165.150": "utun4", "100.110.165.181": "utun4", "100.110.165.151": "utun4"})
    st = assess(p, home_from_devices(HOME_DEVICES))
    assert st.where == "home" and st.routes_broken
    assert st.stolen == ["100.110.165.150", "100.110.165.181", "100.110.165.151"] and st.stolen_via == "utun4"
    assert st.fix == "sudo route -n add -net 100.110.165.0/24 -interface en0"
    assert st.message.startswith("Tailscale is sending") and st.fix in st.message
    # One device routed oddly is still reported (it can't be reached either).
    st = assess(FakeProbe(routes={"100.110.165.181": "utun4"}), home_from_devices(HOME_DEVICES))
    assert st.stolen == ["100.110.165.181"]


def test_fix_route_runs_the_command_or_explains(monkeypatch):
    st = assess(FakeProbe(routes={"100.110.165.150": "utun4"}), home_from_devices(HOME_DEVICES))
    ran = []
    monkeypatch.setattr(nw.os, "geteuid", lambda: 501)
    ok, msg = nw.fix_route(st, run=lambda c: ran.append(c) or 0)
    assert ok and ran == [["sudo", "route", "-n", "add", "-net", "100.110.165.0/24", "-interface", "en0"]]
    assert nw.fix_route(NetState(where="home"))[0]
    monkeypatch.setattr(nw.sys.stdin, "isatty", lambda: False)
    ok, msg = nw.fix_route(st)
    assert not ok and st.fix in msg


# --- find by MAC ------------------------------------------------------------------------------

async def test_find_by_mac_from_arp_then_after_a_nudge():
    p = FakeProbe(arp=nw.parse_arp(ARP_HOME), reveal={"100.110.165.77": "8c:79:f5:8e:e2:5f"})
    assert await find_by_mac("AA:BB:CC:0:1:50", "100.110.165.0/24", p) == "100.110.165.150"
    assert p.nudged == []
    assert await find_by_mac("8c:79:f5:8e:e2:5f", "100.110.165.0/24", p) == "100.110.165.77"
    assert len(p.nudged) == 254 and p.nudged[0] == "100.110.165.1"


async def test_find_by_mac_misses_cleanly():
    p = FakeProbe()
    assert await find_by_mac("8c:79:f5:8e:e2:5f", "100.110.165.0/24", p) is None
    assert await find_by_mac("8c:79:f5:8e:e2:5f", "100.110.165.0/24", p, sweep=False) is None
    assert await find_by_mac("not-a-mac", None, p) is None
    # A campus /16 is never swept; without a subnet the Mac's own LAN is clamped to a /24.
    assert await find_by_mac("8c:79:f5:8e:e2:5f", "10.23.0.0/16", p) is None
    p2 = FakeProbe(IFCONFIG_CAMPUS, ("10.23.0.1", "en0"))
    await find_by_mac("8c:79:f5:8e:e2:5f", None, p2)
    assert len(p2.nudged) == 254 and p2.nudged[0] == "10.23.4.1"


async def test_find_by_mac_prefers_the_saved_address_over_a_stale_duplicate():
    p = FakeProbe(arp={"100.110.165.151": "8c:79:f5:8e:e2:5f", "100.110.165.140": "8c:79:f5:8e:e2:5f"})
    assert await find_by_mac("8c:79:f5:8e:e2:5f", "100.110.165.0/24", p, prefer="100.110.165.151") == "100.110.165.151"
    # Outside the subnet asked for: not ours.
    p = FakeProbe(arp={"10.23.4.9": "8c:79:f5:8e:e2:5f"})
    assert await find_by_mac("8c:79:f5:8e:e2:5f", "100.110.165.0/24", p, sweep=False) is None


async def test_find_by_mac_never_raises():
    class Broken(FakeProbe):
        def arp(self):
            raise OSError("boom")
    assert await find_by_mac("8c:79:f5:8e:e2:5f", "100.110.165.0/24", Broken()) is None


# --- NetWatch ---------------------------------------------------------------------------------

def _devices(tmp_path, d=HOME_DEVICES):
    p = tmp_path / "devices.json"
    p.write_text(json.dumps(d))
    return p


async def test_netwatch_caches_and_tracks_changes(tmp_path):
    t = [1000.0]
    probe = FakeProbe(arp=nw.parse_arp(ARP_HOME))
    w = NetWatch(_devices(tmp_path), probe, clock=lambda: t[0])
    assert (await w.refresh()).where == "home" and not w.away
    probe.ifc, probe.default = IFCONFIG_CAMPUS, ("10.23.0.1", "en0")
    t[0] += 5
    assert (await w.refresh()).where == "home"           # cached
    t[0] += 30
    assert (await w.refresh()).away and w.since == t[0]
    v = w.view()
    assert v["where"] == "away" and v["away"] and not v["routes_broken"] and v["since"] == t[0]


def test_learn_into_only_at_home_with_a_stick_answering(tmp_path):
    w = NetWatch(_devices(tmp_path), FakeProbe())
    w.state = assess(FakeProbe(arp=nw.parse_arp(ARP_HOME)), home_from_devices(HOME_DEVICES))
    d = json.loads(json.dumps(HOME_DEVICES))
    assert not w.learn_into(d, [])
    assert w.learn_into(d, ["100.110.165.150:5555"])
    assert d["home"] == {"subnet": "100.110.165.0/24", "gateway": "100.110.165.1", "router_mac": "00:11:22:33:44:55"}
    assert not w.learn_into(d, ["100.110.165.150:5555"])  # nothing new
    w.state = NetState(where="away", home_subnet="100.110.165.0/24")
    d2 = json.loads(json.dumps(HOME_DEVICES))
    assert not w.learn_into(d2, ["100.110.165.150:5555"]) and "home" not in d2


# --- the device healer ----------------------------------------------------------------------

class FakeNet:
    def __init__(self, arp=None, scan=None, samsung=None, macs=None):
        self.arp_t, self.scan_hosts, self.samsung_info, self.macs = arp or {}, scan or [], samsung or {}, macs or {}
        self.scans, self.mac_calls = [], []

    async def arp(self):
        return dict(self.arp_t)

    async def network(self):
        return ipaddress.ip_network("10.23.0.0/22")

    async def scan(self, net, port):
        self.scans.append((str(net), port))
        return list(self.scan_hosts)

    async def samsung(self, ip):
        return self.samsung_info.get(ip)

    async def find_mac(self, mac, subnet, sweep=True, prefer=""):
        self.mac_calls.append((mac, str(subnet), sweep))
        return self.macs.get(mac)

    def stick(self, host):
        raise AssertionError("no stick should be connected in this test")


def _stick(host, state="offline"):
    return SimpleNamespace(host=host, state=state, name="", model="AFTKA", os="", packages={})


def _healer(tmp_path, state, net, sticks=None, d=HOME_DEVICES, clock=None):
    watch = SimpleNamespace(state=state, refresh=AsyncMock(return_value=state),
                            learn_into=lambda d, up: False)
    c = SimpleNamespace(sticks=sticks or [_stick("100.110.165.150:5555"), _stick("100.110.165.181:5555")],
                        hdmi={}, adb_ok=True, tv=SimpleNamespace(host="100.110.165.151"))
    t = clock or [10_000.0]
    return DeviceHealer(Hooks(c, None, network=watch), _devices(tmp_path, d), net, lambda: t[0]), t, c


HOME_STATE = NetState(where="home", home="100.110.165.x", home_subnet="100.110.165.0/24", iface="en0")
AWAY_STATE = NetState(where="away", home="100.110.165.x", home_subnet="100.110.165.0/24",
                      message="Away from home: this Mac is on 10.23.4.5, not 100.110.165.x.")


async def test_away_from_home_no_scans_and_says_away(tmp_path):
    net = FakeNet()
    h, t, _ = _healer(tmp_path, AWAY_STATE, net)
    for _ in range(3):
        await h.check()
        t[0] += 600
    assert net.scans == [] and net.mac_calls == [] and h.offline_since == {}
    ok, msg = h.summary()
    assert ok is None and msg.startswith("Away from home") and "offline" not in msg


async def test_broken_routes_no_scans_and_says_why(tmp_path):
    st = NetState(where="home", home_subnet="100.110.165.0/24", stolen=["100.110.165.150"],
                  message="Tailscale is sending traffic for the home devices ...")
    net = FakeNet()
    h, t, _ = _healer(tmp_path, st, net)
    await h.check()
    t[0] += 600
    await h.check()
    assert net.scans == [] and h.summary() == (False, st.message)


async def test_lost_sticks_found_by_mac_skip_the_port_scan(tmp_path):
    net = FakeNet(macs={"aa:bb:cc:00:01:50": "100.110.165.160", "aa:bb:cc:00:01:81": "100.110.165.181"})
    h, t, _ = _healer(tmp_path, HOME_STATE, net)
    await h.check()
    t[0] += 121
    found = []

    async def find(saved, cands, arp, notes):
        found.append(list(cands))
        return None
    h._find = find
    await h.check()
    # .181 kept its address (adb itself is down there): MAC lookup can't help, so the HOME net is scanned.
    assert net.scans == [("100.110.165.0/24", 5555), ("100.110.165.0/24", 8001)]   # both on HOME, not campus
    net.macs["aa:bb:cc:00:01:81"] = "100.110.165.190"
    h.sticks_scan.next_at = 0
    net.scans.clear()
    await h.check()
    assert ("100.110.165.0/24", 5555) not in net.scans and found[-1] == ["100.110.165.160:5555", "100.110.165.190:5555"]


async def test_tv_followed_by_mac_even_with_its_control_port_shut(tmp_path):
    net = FakeNet(macs={"8c:79:f5:8e:e2:5f": "100.110.165.140"})
    h, t, c = _healer(tmp_path, HOME_STATE, net, sticks=[_stick("100.110.165.150:5555", "connected")])
    h.learned["100.110.165.150:5555"] = t[0]
    notes = await h.check()
    assert notes == ["The TV moved from 100.110.165.151 to 100.110.165.140 (found by its MAC) — following it"]
    assert json.loads((tmp_path / "devices.json").read_text())["tv"]["ip"] == "100.110.165.140"
    assert c.tv.host == "100.110.165.140"
    # Same address, port still shut: present on the network, say so instead of "not answering".
    t[0] += 200
    assert await h.check() == []
    assert h.tv_on_lan and "control port isn't answering" in h.summary()[1]


async def test_tv_mac_lookup_needs_a_known_home(tmp_path):
    net = FakeNet(macs={"8c:79:f5:8e:e2:5f": "10.23.4.77"})
    h, t, _ = _healer(tmp_path, NetState(where="unknown"), net, sticks=[_stick("100.110.165.150:5555", "connected")])
    h.learned["100.110.165.150:5555"] = t[0]
    await h.check()
    assert net.mac_calls == []


async def test_healer_without_a_netwatch_behaves_as_before(tmp_path):
    net = FakeNet()
    c = SimpleNamespace(sticks=[_stick("100.110.165.150:5555")], hdmi={}, adb_ok=True)
    t = [10_000.0]
    h = DeviceHealer(SimpleNamespace(controller=c), _devices(tmp_path, {"sticks": HOME_DEVICES["sticks"]}),
                     net, lambda: t[0])
    await h.check()
    t[0] += 121
    await h.check()
    assert net.scans == [("10.23.0.0/22", 5555)] and net.mac_calls == []


# --- watchdog policy ------------------------------------------------------------------------

def test_watchdog_does_not_reconnect_or_nag_while_away():
    o = Obs(away=True, sticks_offline=["100.110.165.150:5555"], offline_min=300)
    assert rules(o).action == "none"
    assert guard(Verdict("reconnect_stick", "model said so", "100.110.165.150:5555", "model"), o).action == "none"


def test_watchdog_reports_stolen_routes():
    o = Obs(route_issue="Tailscale is sending traffic ...", sticks_offline=["100.110.165.150:5555"])
    v = rules(o)
    assert v.action == "notify" and v.why.startswith("Tailscale")


def test_watchdog_unchanged_at_home():
    o = Obs(sticks_offline=["100.110.165.150:5555"], offline_min=1)
    assert rules(o).action == "reconnect_stick"


# --- engine view ------------------------------------------------------------------------------

def test_engine_marks_unreachable_sticks_away_not_offline():
    from redzone.engine import Engine
    rows = [{"host": "a", "state": "offline"}, {"host": "b", "state": "connected"}]
    e = SimpleNamespace(control=SimpleNamespace(status=lambda: [dict(r) for r in rows]),
                        network=SimpleNamespace(away=True))
    assert [r["state"] for r in Engine._sticks_view(e)] == ["away", "connected"]
    e.network.away = False
    assert [r["state"] for r in Engine._sticks_view(e)] == ["offline", "connected"]


def test_cli_find_and_status(monkeypatch, tmp_path, capsys):
    probe = FakeProbe(arp=nw.parse_arp(ARP_HOME))
    monkeypatch.setattr(nw, "Probe", lambda: probe)
    p = _devices(tmp_path)
    assert asyncio.run(nw._main(["--devices", str(p), "--find", "aa:bb:cc:00:01:50"])) == 0
    assert capsys.readouterr().out.strip() == "100.110.165.150"
    assert asyncio.run(nw._main(["--devices", str(p)])) == 0
    assert capsys.readouterr().out.startswith("HOME:")


def test_own_router_with_new_numbers_is_home_on_the_new_subnet():
    d = {**HOME_DEVICES, "home": {"subnet": "100.110.165.0/24", "gateway": "100.110.165.1",
                                  "router_mac": "00:11:22:33:44:55"}}
    ifc = IFCONFIG_HOME.replace("100.110.165.20 netmask 0xffffff00 broadcast 100.110.165.255",
                                "192.168.1.20 netmask 0xffffff00 broadcast 192.168.1.255")
    p = FakeProbe(ifc, ("192.168.1.1", "en0"), arp={"192.168.1.1": "00:11:22:33:44:55"})
    st = assess(p, home_from_devices(d))
    assert st.where == "home" and st.home_subnet == "192.168.1.0/24" and "192.168.1.x" in st.message
    w = NetWatch(Path("/nonexistent/devices.json"), p)
    w.state = st
    dd = json.loads(json.dumps(d))
    assert w.learn_into(dd, ["192.168.1.30:5555"])
    assert dd["home"]["subnet"] == "192.168.1.0/24" and dd["home"]["gateway"] == "192.168.1.1"
    # A different router on other numbers is still away.
    p.table = {"192.168.1.1": "de:ad:be:ef:00:01"}
    assert assess(p, home_from_devices(d)).away


def test_cli_saves_the_tv_mac(tmp_path):
    p = _devices(tmp_path)
    assert asyncio.run(nw._main(["--devices", str(p), "--save-tv-mac", "8C:79:F5:8E:E2:5F"])) == 0
    tv = json.loads(p.read_text())["tv"]
    assert tv == {"ip": "100.110.165.151", "mac": "8c:79:f5:8e:e2:5f"}
    assert asyncio.run(nw._main(["--devices", str(p), "--save-tv-mac", "zz"])) == 2
