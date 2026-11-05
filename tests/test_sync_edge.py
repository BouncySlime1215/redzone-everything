"""[SYNC A/B, EXP-800/801] live-edge meter: the math, tunes, rows, re-link flags, and that it only reads."""
import asyncio
import importlib.util
import json
from pathlib import Path

from redzone import sync_edge as se
from redzone import sync_probe as sp
from redzone.tuners.adb import EDGE_READ, RunResult, parse_playback

LAND = 46803.0   # ESPN's landing position (overseer, Nov 5: 46,803.0 s ±0.3 over 7 tunes)
T0 = 1_700_000_000.0


def ms(pos_s, upd_s, state=3, speed=1.0):
    return parse_playback(f"  package=com.amazon.firetv.youtube.tv\n    active=true\n"
                          f"    state=PlaybackState {{state={state}, position={int(round(pos_s * 1000))}, buffered "
                          f"position=0, speed={speed}, updated={int(round(upd_s * 1000))}, actions=0}}\n")


def test_uptime_and_position_now():
    assert se.parse_uptime("12345.67 23456.78\n  package=x\n") == 12345.67
    assert se.parse_uptime("garbage 12 34") is None
    assert se.position_now(ms(100.0, 1000.0), 1012.5) == 112.5                       # playing: runs on
    assert se.position_now(ms(100.0, 1000.0, state=2, speed=0.0), 1012.5) == 100.0   # paused: stays
    assert se.position_now({"position": None}, 1.0) is None


def test_meter_tracks_pauses_stalls_and_rejoin():
    tr = se.EdgeTracker()
    tgt = "youtubetv:espn"
    # tune at uptime 500 (wall T0), loading, then first plays at 502 from the landing position
    r = tr.read("hdmi1", "visible", tgt, ms(0.0, 500.0, state=6, speed=0.0), 500.0, T0, tune_t=T0)
    assert "behind_live_s" not in r and r["uptime_ms"] == 500000
    r = tr.read("hdmi1", "visible", tgt, ms(LAND, 502.0), 502.0, T0 + 2, tune_t=T0)
    assert r["behind_live_s"] == 0.0 and r["tune_to_playing_s"] == 2.0 and r["land_pos_s"] == LAND
    # 60 s later, still playing (the session's position was NOT refreshed): extrapolated, not behind
    r = tr.read("hdmi1", "visible", tgt, ms(LAND, 502.0), 562.0, T0 + 62, tune_t=T0)
    assert r["pos_now_s"] == LAND + 60 and r["behind_live_s"] == 0.0
    # paused 30 s (overseer: a 30 s pause read +30.01 s)
    r = tr.read("hdmi1", "visible", tgt, ms(LAND + 60, 562.0, state=2, speed=0.0), 592.0, T0 + 92, tune_t=T0)
    assert r["behind_live_s"] == 30.0 and r["err_s"] == -30.0
    # resumed: the app knows it held 30 s -> no error
    r = tr.read("hdmi1", "visible", tgt, ms(LAND + 60, 592.0), 600.0, T0 + 100, pred_behind_s=30.0, tune_t=T0)
    assert r["behind_live_s"] == 30.0 and r["err_s"] == 0.0
    # YouTube TV rejoins live on its own: the position jumps to the edge -> 0 behind, same tune
    r = tr.read("hdmi1", "visible", tgt, ms(LAND + 98, 600.0), 600.0, T0 + 100, tune_t=T0)
    assert r["behind_live_s"] == 0.0


def test_new_tune_by_tune_time_and_target_and_landing_check():
    tr = se.EdgeTracker()
    tgt = "youtubetv:espn"
    for k in range(4):                             # four tunes of ESPN; the 4th lands 2 s early
        tt = T0 + 1000 * k
        land = LAND - (2.0 if k == 3 else 0.0)
        r = tr.read("hdmi2", "hidden", tgt, ms(land, 10.0 + 1000 * k), 10.0 + 1000 * k, tt + 1, tune_t=tt)
        assert r["behind_live_s"] == 0.0
    assert r["landing_off_s"] == 2.0               # leave-one-out median of the earlier three
    r = tr.read("hdmi2", "hidden", "prime:tnf", ms(3000.0, 9000.0), 9000.0, T0 + 9000, tune_t=T0 + 8999)
    assert r["app"] == "prime" and r["channel"] == "tnf" and r["behind_live_s"] == 0.0
    assert "landing_off_s" not in r


def test_rows_go_to_the_edge_subfolder(tmp_path):
    se.write_rows([{"night": "2026-11-05", "t": 1.0, "stick": "hdmi1"}], tmp_path)
    assert (tmp_path / "sync_truth" / "edge" / "2026-11-05.jsonl").exists()
    assert not list((tmp_path / "sync_truth").glob("*.jsonl"))   # the v10 grader's glob stays clean


class Stick:
    def __init__(self, host, pos):
        self.host, self.cmds = host, []
        self.pos, self.up = pos, 500.25
        self.upd = self.up

    async def run(self, *args, timeout=8.0):
        cmd = args[-1]
        self.cmds.append(cmd)
        out = (f"{self.up:.2f} 900.00\n  package=com.amazon.firetv.youtube.tv\n    active=true\n"
               f"    state=PlaybackState {{state=3, position={int(round(self.pos * 1000))}, buffered position=0, "
               f"speed=1.0, updated={int(round(self.upd * 1000))}, actions=0}}\n")
        return RunResult(True, out, 0.01)

    async def key(self, *codes):
        raise AssertionError("the edge reader pressed a key")


class Control:
    def __init__(self):
        # paused app (no live control) and mid-switch: the edge reader still reads (read-only)
        self.live_control, self.two_stick, self._flipping, self.input_unknown = False, True, True, False
        self.main, self.hidden = Stick("10.0.0.5", LAND), Stick("10.0.0.6", LAND)
        self.hdmi = {"10.0.0.5": "HDMI1", "10.0.0.6": "HDMI2"}
        self.on_target, self.hidden_target = "youtubetv:espn", "youtubetv:nbc"
        self.held = {"youtubetv:nbc": 10.0}
        self.tuned_at = {"10.0.0.5": T0 - 5, "10.0.0.6": T0 - 5}
        self.behind_live = {}

    def _busy(self):
        return True


class Cfg(dict):
    def get(self, k, d=None):
        return super().get(k, d)


def probe(tmp_path, **flags):
    cfg = {"mode.dry_run": False, "sync.edge.enabled": True}
    cfg.update({f"sync.edge.{k}": v for k, v in flags.items()})
    return sp.SyncProbe(Cfg(cfg), Control(), tmp_path)


def later(c, wall_s, played_s):
    """`wall_s` passes on both sticks, of which they play only `played_s` (a stall)."""
    for s in (c.main, c.hidden):
        s.up += wall_s
        s.upd = s.up
        s.pos += played_s


def test_edge_tick_reads_only_logs_by_hdmi_and_feeds_the_controller(tmp_path):
    p = probe(tmp_path)
    c = p.control
    p.delay_of = lambda target: (26.0, 3.0)
    rows = asyncio.run(p.edge_tick(now=T0))
    assert [r["stick"] for r in rows] == ["hdmi1", "hdmi2"]
    assert rows[1]["pred_behind_s"] == 10.0 and rows[0]["delay_mu_s"] == 26.0
    assert rows[0]["tune_t"] == T0 - 5 and rows[0]["behind_live_s"] == 0.0
    assert c.behind_live == {"10.0.0.5": 0.0, "10.0.0.6": 0.0}
    for s in (c.main, c.hidden):
        assert s.cmds == [EDGE_READ]
    text = next((tmp_path / "sync_truth" / "edge").glob("*.jsonl")).read_text()
    assert "10.0.0." not in text                 # never a stick's address in the log
    assert len(text.splitlines()) == 2 and json.loads(text.splitlines()[0])["app"] == "youtubetv"


def test_edge_tick_off_unless_enabled(tmp_path):
    for cfg in ({"mode.dry_run": False}, {"mode.dry_run": True, "sync.edge.enabled": True}):
        p = sp.SyncProbe(Cfg(cfg), Control(), tmp_path)
        assert asyncio.run(p.edge_tick()) == [] and p.control.main.cmds == []
    p = probe(tmp_path)
    p.control.input_unknown = True
    assert asyncio.run(p.edge_tick()) == [] and p.control.main.cmds == []


def test_relink_needs_its_flag_and_respects_the_apps_hold(tmp_path):
    calls = []
    for flags, want in (({}, []), ({"force_live": True}, ["hidden"]),
                        ({"force_live": True, "force_live_visible": True}, ["visible", "hidden"])):
        p = probe(tmp_path, **flags)
        calls.clear()
        p.on_force_live = lambda target, row: calls.append(row["role"]) or True
        c = p.control
        c.held = {}
        asyncio.run(p.edge_tick(now=T0))             # landed
        later(c, 10.0, 5.0)                          # 10 s on it has played only 5: 5 s behind
        rows = asyncio.run(p.edge_tick(now=T0 + 10))
        assert all(r["behind_live_s"] == 5.0 and r["would_force"] for r in rows)
        assert calls == want, flags
        assert c.behind_live["10.0.0.6"] == 5.0
    # a hold the app asked for is not "behind": 5 s behind with a 5 s hold -> nothing
    p = probe(tmp_path, force_live=True, force_live_visible=True)
    calls.clear()
    p.on_force_live = lambda target, row: calls.append(row["role"]) or True
    c = p.control
    c.held = {"youtubetv:nbc": 5.0, "youtubetv:espn": 5.0}
    asyncio.run(p.edge_tick(now=T0))
    later(c, 10.0, 5.0)
    rows = asyncio.run(p.edge_tick(now=T0 + 10))
    assert calls == [] and not any(r.get("would_force") for r in rows)


def test_shipped_defaults_read_on_relink_off():
    from redzone import config
    assert config.default("sync.edge.enabled") is True
    assert config.default("sync.edge.force_live") is False and config.default("sync.edge.force_live_visible") is False
    assert config.default("sync.edge.force_live_s") == 2.0


def test_controller_relinks_refuse_what_they_must_not_touch():
    from redzone.control import Controller

    class C(Controller):
        live_control = True
        two_stick = True
        hidden = object()

        def __init__(self):   # only what the two re-link calls read
            self.input_unknown, self._flipping, self.hidden_target, self.hidden_ready = False, False, "y:a", True
            self.parked, self.on_target, self.held, self._asleep_until = None, "y:b", {}, 0.0
            self._task = self._preload_task = self._resume_task = None
            self._lock = asyncio.Lock()
            self.started, self.dead = [], False
            self.dead_ball = lambda target: self.dead

        def can_tune(self, target):
            return True

        def _cancel_refresh(self):
            pass

        async def _preload(self, *a):
            self.started.append(a)

        async def restore_visible(self, target, relink=False, why=""):
            self.started.append((target, relink))

    c = C()
    assert not c.force_live_hidden("y:b")          # not what the hidden stick plays
    c.held = {"y:a": 30.0}
    assert not c.force_live_hidden("y:a")          # a hold the app asked for
    c.held = {}

    async def go():
        ok = c.force_live_hidden("y:a")
        await c._preload_task
        no_play = c.relink_visible_live("y:b")      # the stick on screen while a play is live: no
        c.dead = True
        yes = c.relink_visible_live("y:b")
        await asyncio.sleep(0)
        return ok, no_play, yes
    ok, no_play, yes = asyncio.run(go())
    assert ok and c.hidden_ready is False and not no_play and yes and ("y:b", True) in c.started


def test_report_per_stick_and_app():
    path = Path(__file__).resolve().parent.parent / "scripts/sync_edge_report.py"
    spec = importlib.util.spec_from_file_location("ser", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    tr = se.EdgeTracker()
    rows = [tr.read("hdmi1", "visible", "youtubetv:espn", ms(LAND, 10.0), 10.0 + k, T0 + k, tune_t=T0 - 2)
            for k in range(3)]
    rep = m.report(rows)["hdmi1/youtubetv"]
    assert rep["rows"] == 3 and rep["tunes"] == 1 and rep["err_abs_p95"] == 0.0 and rep["behind_p95"] == 0.0


def test_prime_epoch_position_is_absolute_behind_real_life():
    tr = se.EdgeTracker()
    wall = 1_762_330_000.0
    frame_ms = (wall - 6.4 - 2.0) * 1000          # stamped 2 s before the read, 6.4 s behind real life then
    r = tr.read("hdmi2", "hidden", "prime:nba", ms(frame_ms / 1000, 900.0), 902.0, wall, tune_t=wall - 30,
                delay=(7.0, 1.0))
    assert r["epoch"] is True and abs(r["abs_behind_s"] - 6.4) < 0.01 and abs(r["abs_err_s"] - 0.6) < 0.01
    # a 24 h counter (the other stick's Prime content) is not an epoch
    r = tr.read("hdmi1", "visible", "prime:tnf", ms(86368.0, 900.0), 902.0, wall, tune_t=wall - 30)
    assert "epoch" not in r and "abs_behind_s" not in r


def test_k_fuses_arena_minus_frame_clock_and_gates_outliers():
    """[Nick 3:30 AM] K = score-bug delay (behind the arena) - E (behind the frame clock), inverse-variance."""
    tr = se.EdgeTracker()
    tgt = "prime:tnf"
    wall = 1_762_330_000.0
    assert tr.frame_k(tgt, 16.0, wall, 1.5, pair_s=30.0) is None          # no epoch read yet: nothing to pair
    tr.read("hdmi2", "hidden", tgt, ms(wall - 6.4, 900.0), 900.0, wall, tune_t=wall - 30)
    assert tr.frame_k(tgt, 16.0, wall + 31, 1.5, pair_s=30.0) is None     # too old to pair
    samples = [15.8, 16.2, 14.9, 17.1, 16.0, 15.5, 16.6, 15.9, 16.3, 15.7] * 2
    for i, d in enumerate(samples):
        assert abs(tr.frame_k(tgt, d, wall + 1, 1.5, pair_s=30.0) - (d - 6.4)) < 1e-6, i
    k, sd, n = tr.k_of(tgt)
    assert n == 20 and abs(k - 9.6) < 0.05 and sd <= 0.5                   # Nick's target: sd <= 0.5 after ~20
    assert tr.frame_k(tgt, 40.0, wall + 1, 1.5, pair_s=30.0) is None and tr.k_out[tgt] == 1   # a misread
    r = tr.read("hdmi2", "hidden", tgt, ms(wall + 4 - 6.4, 904.0), 904.0, wall + 4, tune_t=wall - 30)
    assert r["k_n"] == 20 and abs(r["epoch_delay_s"] - 16.0) < 0.06


def epoch_probe(tmp_path, **flags):
    p = probe(tmp_path, **flags)
    c = p.control
    c.two_stick, c.on_target = False, "prime:tnf"
    c.main.pos = T0 - 6.4                           # Prime: the position is the frame's wall clock
    return p, c


def test_epoch_signal_feeds_the_filter_only_with_its_flag_and_a_tight_k(tmp_path):
    fed = []
    for flags, want in (({}, 0), ({"epoch_signal": True}, 1)):
        p, c = epoch_probe(tmp_path, **flags)
        p.on_epoch = lambda target, z, t, sd: fed.append((target, z, sd))
        fed.clear()
        rows = asyncio.run(p.edge_tick(now=T0))
        assert rows[0]["epoch"] and "k_s" not in rows[0]
        for _ in range(4):                          # 4 reads: K exists but isn't trusted yet (k_min_reads 5)
            p.frame_k("prime:tnf", 16.0, T0, 1.5)
        later(c, 1.0, 1.0)
        rows = asyncio.run(p.edge_tick(now=T0 + 1))
        assert rows[0]["k_n"] == 4 and not rows[0].get("epoch_ready") and fed == []
        for _ in range(16):
            p.frame_k("prime:tnf", 16.0, T0 + 1, 1.5)
        later(c, 1.0, 1.0)
        rows = asyncio.run(p.edge_tick(now=T0 + 2))
        assert rows[0]["epoch_ready"] and rows[0]["k_sd_s"] <= 0.5
        later(c, 1.0, 1.0)
        asyncio.run(p.edge_tick(now=T0 + 3))       # inside epoch_every_s: not fed twice
        assert len(fed) == want, flags
    target, z, sd = fed[0]
    assert target == "prime:tnf" and abs(z - 16.0) < 0.05 and 0.5 <= sd < 0.65


def test_screencap_band_rebuilds_a_full_size_png_from_raw_rows():
    import struct
    import zlib

    from redzone.tuners import adb
    w, h = 8, 6
    frame = bytes((x * 10, y * 10, 7, 255)[i] for y in range(h) for x in range(w) for i in range(4))
    header = struct.pack("<III", w, h, 1)
    s = adb.Stick("dev")
    seen = []

    async def exec_out(cmd, timeout):
        seen.append(cmd)
        if "head -c 12" in cmd:
            return header
        n = int(cmd.rsplit(" ", 1)[1])
        return (header + frame)[-n:]
    s._exec_out = exec_out
    png = asyncio.run(s.screencap_band(2 / 3, timeout=1))
    assert png.startswith(b"\x89PNG\r\n\x1a\n") and struct.unpack(">II", png[16:24]) == (w, h)
    raw = zlib.decompress(png[png.index(b"IDAT") + 4:png.index(b"IEND") - 8])
    rows = [raw[i * (w * 4 + 1) + 1:(i + 1) * (w * 4 + 1)] for i in range(h)]
    assert rows[0] == b"\x00\x00\x00\xff" * w and rows[3] == b"\x00\x00\x00\xff" * w   # above the band: black
    assert rows[4] == frame[4 * w * 4:5 * w * 4] and rows[5] == frame[5 * w * 4:]      # the band, in place
    assert seen == ["screencap | head -c 12", f"screencap | tail -c {2 * w * 4}"]
    asyncio.run(s.screencap_band(2 / 3, timeout=1))
    assert len(seen) == 3                            # the frame's size is read once

    async def short(cmd, timeout):
        return b"" if "tail" in cmd else header
    s._exec_out = short
    assert asyncio.run(s.screencap_band(2 / 3, timeout=1)) == b"" and s._raw_geom is None
    s._raw_geom, s._exec_out = None, lambda cmd, timeout: asyncio.sleep(0, struct.pack("<III", w, h, 5))
    assert asyncio.run(s.screencap_band(2 / 3, timeout=1)) == b""   # not RGBA_8888: the caller falls back
