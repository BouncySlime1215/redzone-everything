"""[SYNC probe] read-only, automatic: parsers, quiet during switches, rows on disk."""
import asyncio
import json

from redzone import sync_probe as sp
from redzone.tuners.adb import RunResult

WIFI = ('mWifiInfo SSID: "HomeNet", BSSID: aa:bb:cc:dd:ee:ff, MAC: 02:00:00:00:00:00, Supplicant state: '
        'COMPLETED, Wi-Fi standard: 5, RSSI: -61, Link speed: 390Mbps, Tx Link speed: 390Mbps, '
        'Max Supported Tx Link speed: 433Mbps, Rx Link speed: 351Mbps, Frequency: 5745MHz, Net ID: 3')
MS = ("  package=com.amazon.firetv.youtube\n    state=PlaybackState {state=3, position=50000, buffered position=0, "
      "speed=1.0, updated=90000, actions=0}\n")


def test_wifi_keeps_numbers_only():
    w = sp.parse_wifi(WIFI)
    assert w == {"rssi": -61, "link_mbps": 390, "tx_mbps": 390, "rx_mbps": 351, "freq_mhz": 5745}
    assert "HomeNet" not in json.dumps(w) and "aa:bb" not in json.dumps(w)


def test_ui_hints():
    assert sp.ui_hints("Ad 1 of 3 | 0:25 | Your game will resume shortly") == ["ad", "countdown"]
    assert set(sp.ui_hints("PHI 21 - 17 DAL | 2nd | 4:12")) >= {"clock", "countdown", "score"}
    assert sp.ui_hints("") == []


def test_stall_from_clock_vs_position():
    a = {"pkg": "p", "position": 50_000, "updated": 90_000}
    assert sp.stall_s(a, {"pkg": "p", "position": 54_000, "updated": 96_500}) == 2.5
    assert sp.stall_s(a, dict(a)) == 0.0
    assert sp.stall_s(a, {"pkg": "q", "position": 1, "updated": 1}) is None
    assert sp.stall_s(None, a) is None


class Stick:
    def __init__(self, host):
        self.host, self.cmds = host, []

    async def run(self, *args, timeout=8.0):
        cmd = args[-1]
        self.cmds.append(cmd)
        if "media_session" in cmd:
            return RunResult(True, MS, 0.01)
        if "dumpsys wifi" in cmd:
            return RunResult(True, WIFI, 0.01)
        if "uiautomator" in cmd:
            return RunResult(True, '<hierarchy><node text="Ad 1 of 2" /><node text="0:30" /></hierarchy>', 0.01)
        if "logcat" in cmd:
            return RunResult(True, "1730700000.123 I/Player: rebuffering started\n", 0.01)
        return RunResult(True, "", 0.01)

    async def key(self, *codes):              # the probe must never press anything
        raise AssertionError("probe pressed a key")


class Control:
    def __init__(self):
        self.live_control, self.two_stick, self._flipping, self.input_unknown = True, True, False, False
        self.main, self.hidden = Stick("a"), Stick("b")
        self.on_target, self.hidden_target = "youtubetv:espn", "prime:tnf"
        self.busy = False

    def _busy(self):
        return self.busy


def test_tick_logs_both_sticks_and_dumps_ui_only_on_the_hidden_one(cfg, tmp_path):
    cfg.update({"mode": {"dry_run": False}})
    c = Control()
    p = sp.SyncProbe(cfg, c, tmp_path)
    rows = asyncio.run(p.tick(now=1000.0))
    assert [r["role"] for r in rows] == ["visible", "hidden"]
    vis, hid = rows
    assert vis["wifi"]["rssi"] == -61 and vis["ms"]["state"] == "playing" and "ui" not in vis
    assert hid["ui"]["hints"] == ["ad", "countdown"] and hid["log"] == ["1730700000.123 I/Player: rebuffering started"]
    assert not any("uiautomator" in x for x in c.main.cmds)
    files = list(tmp_path.glob("recordings/*/sync_probe.jsonl"))
    assert len(files) == 1 and len(files[0].read_text().splitlines()) == 2
    again = asyncio.run(p.tick(now=1005.0))            # wifi/ui/log not due yet; same log line never twice
    assert "wifi" not in again[0] and "ui" not in again[1] and again[0]["stall_s"] == 0.0


def test_quiet_during_a_switch_and_in_dry_run(cfg, tmp_path):
    c = Control()
    assert asyncio.run(sp.SyncProbe(cfg, c, tmp_path).tick()) == []          # cfg fixture is dry run
    cfg.update({"mode": {"dry_run": False}})
    c.busy = True
    assert asyncio.run(sp.SyncProbe(cfg, c, tmp_path).tick()) == [] and c.main.cmds == []
    cfg.update({"sync": {"probe": {"enabled": False}}})
    c.busy = False
    assert asyncio.run(sp.SyncProbe(cfg, c, tmp_path).tick()) == []
