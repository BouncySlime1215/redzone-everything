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


def test_ad_edges_from_logcat_lines_for_yttv():
    """[yttv anchors] YouTube TV exposes no UI text (live lab Nov 4): ad edges come from logcat -v epoch."""
    from redzone.sync_probe import log_ad_edges
    lines = ["1793805150.123  1234  1300 I Cobalt  : SCTE35 cue out duration=120",
             "1793805270.500  1234  1300 I Cobalt  : SCTE35 cue in",
             "1793805271.000  1234  1300 I Cobalt  : bitrate changed to 6000",
             "no timestamp: cue out"]
    assert log_ad_edges(lines) == [(1793805150.123, "break_start"), (1793805270.5, "break_end")]


def test_log_pattern_keeps_youtube_tv_state_bursts_not_the_ifa_timer():
    """Live lab Nov 4: content-anchored onPlaybackStateChanged bursts are kept; the 600 s
    advertising-ID timer is not an ad break and stays out."""
    import re
    from redzone import sync_probe as sp
    rx = re.compile(sp.LOG_RE, re.I)
    burst = ("1793808834.6  2066  2066 I whad-MediaSession: onPlaybackStateChanged: starboard_d98, "
             "state=PlaybackState {state=3, position=47832337, buffered position=0, speed=1.0, updated=1}")
    timer = "1793808031.984  1146  1313 I starboard: Successfully retrieved Advertising ID (IfA)."
    assert rx.search(burst) and not rx.search(timer)


def test_burst_onsets_are_the_content_anchor_not_tunes_or_lone_updates():
    """[burst anchor] Live lab Nov 4 (adwatch.jsonl, shapes copied): a tune, a pause/resume and a lone
    update are not bursts; a burst's onset is its first update, confirmed by a second within 60 s."""
    from redzone.sync_probe import BurstWatch

    def line(t, state, pos, speed):
        return (f"  {T0 + t:.3f}  2066  2066 I whad-MediaSession: onPlaybackStateChanged: starboard_x, "
                f"state=PlaybackState {{state={state}, position={pos}, buffered position=0, speed={speed}, updated=1,")
    T0 = 1_793_800_000.0
    w = BurstWatch()
    lines = [line(7790.37, 3, 0, 0.0), line(7792.09, 3, 46790137, 1.0),          # a tune settling
             line(7802.56, 2, 46796456, 0.0), line(7893.83, 3, 46796636, 1.0),   # a pause, then PLAY
             line(7894.03, 3, 46796738, 1.0),
             line(8513.80, 3, 46909706, 1.0)]                                    # a lone update
    assert w.feed(lines) == []
    burst = [line(8834.61, 3, 47832337, 1.0), line(8849.54, 3, 47847270, 1.0), line(8864.55, 3, 47862285, 1.0)]
    assert w.feed(burst[:1]) == []                    # not confirmed yet (reads arrive every 15 s)
    assert w.feed(burst[1:]) == [(T0 + 8834.61, 47832.337)]
    assert w.feed([line(8894.46, 3, 47892195, 1.0)]) == []          # the same burst's tail


def test_burst_align_lines_bursts_up_with_the_feed(tmp_path):
    """[burst anchor] scripts/burst_align.py: a burst 20 s (real life) after each timeout shows a steady gap."""
    import json
    import importlib.util
    spec = importlib.util.spec_from_file_location("burst_align", "scripts/burst_align.py")
    ba = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ba)
    T0, delay = 1_793_800_000.0, 45.0
    plays, probe = [], []
    for k in range(4):
        w = T0 + 1200 * k
        plays.append({"game": "g", "play": f"p{k}", "type": "Full Timeout", "league": "nba", "wall": w})
        plays.append({"game": "g", "play": f"q{k}", "type": "Jump Shot", "league": "nba", "wall": w + 300})
        for j in range(3):
            t = w + 20 + delay + 15 * j
            probe.append({"host": "h", "kind": "log", "line": f"  {t:.3f}  1  1 I whad-MediaSession: onPlaybackStateChanged:"
                          f" x, state=PlaybackState {{state=3, position={int(4.7e7 + 1e6 * k + 15000 * j)}, buffered position=0,"
                          f" speed=1.0, updated=1,"})
    (tmp_path / "plays.jsonl").write_text("\n".join(json.dumps(r) for r in plays))
    (tmp_path / "probe.jsonl").write_text("\n".join(json.dumps(r) for r in probe))
    assert ba.main(["--probe", str(tmp_path / "probe.jsonl"), "--plays", str(tmp_path / "plays.jsonl"),
                    "--game", "g", "--delay", str(delay)]) == 0
    gaps = [g for _, t in ba.onsets(ba.probe_lines(tmp_path / "probe.jsonl", None))
            for g in [t - delay - ba.last_before(ba.dead_balls(tmp_path / "plays.jsonl", "g"), t - delay, 240, True)[0]]]
    assert [round(g, 1) for g in gaps] == [20.0] * 4
