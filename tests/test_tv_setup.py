"""scripts/tv_setup.py's logic against a fake home (no network, no TV, no sticks):
finding the TV by MAC (ARP, or only a stick can see it), the exact causes it prints, the model
check against the TV's own MAC, the input tests, and the whole run saving the path that works."""
import ipaddress
import json

import httpx

from redzone import tv as tvmod
from redzone import tvsetup
from test_tv_input import FakeCloud

TV_MAC = "8c:79:f5:8e:e2:5f"
STICKS = [{"host": "100.110.165.181:5555", "name": "old", "hdmi": "HDMI1"},
          {"host": "100.110.165.150:5555", "name": "new", "hdmi": "HDMI2"}]


class FakeHome(tvsetup.Home):
    def __init__(self, *, mac_net="100.110.165.130/26", arp=None, neigh="", info=True, ports=(),
                 keys_work=True, answers=None, prompts=None, pair_ok=True, cec="1"):
        super().__init__(STICKS)
        self.net = ipaddress.ip_network(mac_net, strict=False) if mac_net else None
        self._arp, self.neigh, self.info, self.ports = arp or {}, neigh, info, set(ports)
        self.keys_work, self.answers, self.prompts = keys_work, list(answers or []), list(prompts or [])
        self.pair_ok, self.active, self.cec, self.sent, self.swept, self.t = pair_ok, None, cec, [], [], 0.0
        self.shells = []

    def mac_network(self):
        return self.net

    async def sweep(self, net):
        self.swept.append(str(net))
        return []

    def arp(self):
        return self._arp

    async def find_by_mac(self, mac, subnet):
        await self.sweep(subnet)
        return next((ip for ip, m in self._arp.items() if m == mac), None)

    async def port(self, ip, port):
        return port in self.ports

    async def samsung(self, ip):
        if not self.info:
            return None
        return {"ip": ip, "name": "Samsung 6 Series (55)", "model": "UN55NU6900", "os": "Tizen", "token_auth": "true",
                "power": "on", "wifi_mac": TV_MAC if self.info is True else self.info}

    async def upnp(self, ip):
        return {"modelName": "UN55NU6900"}

    async def shell(self, host, cmd, timeout=6.0):
        self.shells.append((host, cmd))
        if cmd == "ip neigh":
            return True, self.neigh
        if "mIsActiveSource" in cmd:
            return True, "1" if self.active == host else "0"
        if cmd.startswith("settings put global hdmi_control_enabled"):
            self.cec = cmd.split()[-1]
            return True, ""
        if cmd == tvsetup.CEC_GET:
            return True, self.cec + "\n"
        return True, ""

    async def screencap(self, host):
        return b"\x89PNG"

    async def pair(self, ip, wait_s=30.0):
        return (True, "") if self.pair_ok else (False, "TimeoutError: no reply")

    async def key(self, ip, key):
        self.sent.append(key)
        if self.keys_work and key.startswith("KEY_HDMI"):
            port = key.removeprefix("KEY_")
            self.active = next(s["host"] for s in STICKS if s["hdmi"] == port)
        return True

    def ask(self, q):
        return self.answers.pop(0) if self.answers else None

    def prompt(self, q):
        return self.prompts.pop(0) if self.prompts else ""

    async def sleep(self, s):
        self.t += s

    def now(self):
        self.t += 0.1
        return self.t


def devices(tmp_path, **tv):
    f = tmp_path / "devices.json"
    f.write_text(json.dumps({"sticks": STICKS, "tv": {"ip": "100.110.165.151", "mac": TV_MAC, **tv}}))
    return f


async def test_finds_the_tv_by_mac_even_when_the_typed_ip_was_wrong():
    home, rep = FakeHome(arp={"100.110.165.140": TV_MAC}), tvsetup.Report()
    ip = await tvsetup.find_tv(home, TV_MAC.upper(), STICKS, "100.110.165.151", rep)
    assert ip == "100.110.165.140" and home.swept == ["100.110.165.0/24"]
    assert "was wrong" in rep.lines[-1]


async def test_only_a_stick_sees_the_tv_means_isolation():
    neigh = "100.110.165.177 dev wlan0 lladdr 8c:79:f5:8e:e2:5f REACHABLE\n100.110.165.129 dev wlan0 lladdr aa:bb:cc:dd:ee:ff STALE"
    home, rep = FakeHome(neigh=neigh), tvsetup.Report()
    assert await tvsetup.find_tv(home, TV_MAC, STICKS, None, rep) == "100.110.165.177"
    assert "isolation" in rep.causes


async def test_mac_on_another_network_is_told_to_join_the_home_wifi():
    home, rep = FakeHome(mac_net="10.23.4.5/16"), tvsetup.Report()
    assert await tvsetup.find_tv(home, TV_MAC, STICKS, None, rep) is None
    assert rep.causes == ["wrong subnet"] and not home.swept


async def test_tv_not_anywhere_prints_the_likely_causes():
    home, rep = FakeHome(), tvsetup.Report()
    assert await tvsetup.find_tv(home, TV_MAC, STICKS, "100.110.165.151", rep) is None
    assert "not on the network" in rep.causes and any("guest" in x for x in rep.lines)


async def test_model_is_verified_only_by_the_tvs_own_mac():
    rep = tvsetup.Report()
    ok = await tvsetup.read_model(FakeHome(), "1.2.3.4", TV_MAC, rep)
    assert ok["network_verified"] and ok["model"] == "UN55NU6900"
    other = await tvsetup.read_model(FakeHome(info="00:11:22:33:44:55"), "1.2.3.4", TV_MAC, rep)
    assert other["network_verified"] is False


async def test_closed_ports_and_ip_remote_off_are_told_apart():
    rep = tvsetup.Report()
    assert await tvsetup.read_model(FakeHome(info=False), "1.2.3.4", TV_MAC, rep) is None
    assert rep.causes == ["port closed"] and any("IP Remote" in x for x in rep.lines)
    rep = tvsetup.Report()
    assert await tvsetup.read_model(FakeHome(info=False, ports={8002}), "1.2.3.4", TV_MAC, rep) is None
    assert rep.causes == ["IP Remote off"]


async def test_input_test_measures_latency_and_needs_every_port_twice():
    home, rep = FakeHome(answers=[True] * 4), tvsetup.Report()
    res = await tvsetup.test_local_inputs(home, "1.2.3.4", STICKS, rep)
    assert home.sent == ["KEY_HDMI1", "KEY_HDMI2", "KEY_HDMI1", "KEY_HDMI2"]
    assert res["HDMI1"]["seen"] == 2 and res["HDMI2"]["cec"] == 2 and res["HDMI1"]["latency_s"]
    assert tvsetup.local_proven(res, STICKS, asked=True)
    res["HDMI2"]["no"] = 1
    assert not tvsetup.local_proven(res, STICKS, asked=True)


async def test_whole_run_local_path_becomes_the_default_and_cec_goes_off(tmp_path, monkeypatch):
    monkeypatch.setattr(tvmod, "TOKEN_FILE", tmp_path / "tok")
    f = devices(tmp_path)
    home = FakeHome(arp={"100.110.165.140": TV_MAC}, answers=[True] * 4 + [True])
    rep = await tvsetup.run(home, path=f)
    d = json.loads(f.read_text())["tv"]
    assert rep.ok == ["local"] and d["ip"] == "100.110.165.140" and d["network_verified"]
    assert d["local_input_verified"] and d["input_method"] == "local" and d["sticks_cec_off"]
    assert home.cec == "0"
    (tmp_path / "tok").write_text("t")
    t = tvmod.SamsungTV.from_devices(f)
    assert t.local_trusted and t.ready()


async def test_keys_ignored_falls_back_to_smartthings_oauth(tmp_path, monkeypatch):
    monkeypatch.setattr(tvmod, "TOKEN_FILE", tmp_path / "tok")
    monkeypatch.setattr(tvmod, "OAUTH_FILE", tmp_path / "st.json")
    monkeypatch.setattr(tvmod, "INPUT_LOG", tmp_path / "tv_input.jsonl")
    monkeypatch.setattr(tvmod, "VERIFY_GAP_S", 0.0)
    cloud = FakeCloud(lag=0)
    real = cloud.handler

    def handler(req):
        if req.url.path == "/v1/apps":
            return httpx.Response(200, json={"oauthClientId": "cid", "oauthClientSecret": "sec"})
        return real(req)
    f = devices(tmp_path)
    state = {}

    class Home2(FakeHome):
        def prompt(self, q):
            if "token" in q:
                return "pat-123"
            return f"https://httpbin.org/get?code=abc&state={state['s']}"
    monkeypatch.setattr(tvsetup.secrets, "token_urlsafe", lambda n: state.setdefault("s", "st8"))
    home = Home2(arp={"100.110.165.140": TV_MAC}, keys_work=False, answers=[False] * 4 + [None, True])
    rep = await tvsetup.run(home, path=f, transport=httpx.MockTransport(handler))
    d = json.loads(f.read_text())["tv"]
    assert rep.ok == ["smartthings"] and d["smartthings_device_id"] == "tv-1" and d["input_method"] == "smartthings"
    assert not d["local_input_verified"] and "HDMI keys ignored" in rep.causes
    assert json.loads((tmp_path / "st.json").read_text())["refresh_token"] == "ref1"


async def test_nothing_works_keeps_cec_on():
    home = FakeHome(info=False)
    rep = await tvsetup.run(home, path=__import__("pathlib").Path("/nonexistent/devices.json"), smartthings=False)
    assert not rep.ok and home.cec == "1" and not any("hdmi_control_enabled 0" in c for _, c in home.shells)


def test_parsers():
    assert tvsetup.parse_ip_neigh("10.0.0.9 dev wlan0 lladdr 8C:79:F5:8E:E2:5F REACHABLE") == {"10.0.0.9": TV_MAC}
    xml = ('<root xmlns="urn:schemas-upnp-org:device-1-0"><device><friendlyName>[TV] Samsung</friendlyName>'
           '<modelName>UN55NU6900</modelName></device></root>')
    assert tvsetup.parse_upnp(xml) == {"friendlyName": "[TV] Samsung", "modelName": "UN55NU6900"}
    assert tvsetup.parse_upnp("not xml") == {}
    assert tvsetup.code_from("https://httpbin.org/get?code=XyZ12&state=s1", "s1") == "XyZ12"
    assert tvsetup.code_from("https://httpbin.org/get?code=XyZ12&state=evil", "s1") is None
    assert tvsetup.code_from('{"args": {"code": "Q1w2E3"}}') == "Q1w2E3"
    assert tvsetup.code_from("") is None
    url = tvsetup.authorize_url("cid", tvsetup.DEFAULT_REDIRECT, "s1")
    assert "client_id=cid" in url and "response_type=code" in url and "x%3Adevices" in url


async def test_oauth_app_is_created_without_the_webhook_query_and_errors_are_shown():
    """SmartThings rejects ?signatureType=ST_PADLOCK&requireConfirmation=true for API_ONLY apps with a
    400 "Param(s) not supported for AppType"; the setup now sends none and shows the error body."""
    seen = []

    def ok(req):
        seen.append(req.url)
        return httpx.Response(200, json={"oauthClientId": "cid", "oauthClientSecret": "sec"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(ok)) as http:
        assert await tvsetup.create_oauth_app(http, "pat-123", "https://httpbin.org/get") == ("cid", "sec")
    assert seen[0].path == "/v1/apps" and not seen[0].query

    def bad(req):
        return httpx.Response(400, json={"error": {"code": "ConstraintViolationError",
                                                   "message": "Param(s) not supported for AppType pat-123"}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(bad)) as http:
        why = await tvsetup.create_oauth_app(http, "pat-123", "https://httpbin.org/get")
    assert why.startswith("HTTP 400") and "not supported for AppType" in why and "pat-123" not in why
