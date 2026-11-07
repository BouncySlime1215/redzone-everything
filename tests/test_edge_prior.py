"""[edge prior, Nick Nov 7] YouTube TV's live edge is ONE delay for every channel: 17 hidden-stick reads on 7 channels,
34.3-35.5 s, mean 34.9, sd ~0.4 (delay = (capture - EPG start) - label at the live edge). The prior replaces other
nights' per-channel guesses (youtubetv:fs1 42 +-8) and the edge model's 25 s default, refreshed by the probe at
most every 30 min, empty label reads skipped. All behind delay.edge_prior.enabled / .refresh.enabled, OFF."""
import asyncio
from types import SimpleNamespace

import pytest

from redzone.config import Config
from redzone.decider.screen import DelayBook
from redzone.tuners import yttv_edge_probe as ep
from redzone.tuners.yttv_label import Obs
from conftest import FakeClock

T0 = 1_793_700_000.0          # 2026-11-03 ... a :00/:30 boundary is a multiple of 1800
FS1, ESPN = "youtubetv:fs1", "youtubetv:espn"
NICK_READS = [34.8, 34.6, 35.2, 34.5, 34.6, 35.5, 35.4, 35.1, 35.3, 34.5, 34.9, 35.0, 34.5, 34.9, 34.9, 34.7, 35.5,
              34.3,   # the Nov 7 cable reads as posted (7 channels)
              34.4, 34.9, 35.2, 35.3, 34.5, 35.0, 35.3, 35.0, 35.0, 34.4]   # locals by search: FOX 50, CBS 17, ABC 11, WRAL


def book(on=True, edge_on=False, clock=None, **cfg):
    b = DelayBook({"youtubetv": 45.0, "prime": 40.0}, 15.0, clock=clock or FakeClock(T0))
    vals = {"enabled": on, **cfg}
    b.edge_prior_cfg = vals.get                     # missing keys fall back to the shipped delay.edge_prior defaults
    b.edge_cfg = {"enabled": edge_on}.get
    return b


def tap(b, target, x, ts):
    """An "I just saw it" sample taken at ts (DelayBook.calibrate stamps now)."""
    b.get(target)
    assert b._add(target, ts, x)
    b._refresh_app(b.app(target))


def test_shipped_config_has_both_flags_off_and_nicks_numbers():
    cfg = Config()
    assert cfg.get("delay.edge_prior.enabled") is False
    assert cfg.get("delay.edge_prior.refresh.enabled") is False
    assert cfg.get("delay.edge_prior.prior") == {"youtubetv": {"mu_s": 34.9, "sd_s": 0.6}}
    assert cfg.get("delay.edge_prior.refresh.min_gap_s") == 1800
    assert abs(sum(NICK_READS) / len(NICK_READS) - 34.9) < 0.1


def test_flag_off_changes_nothing():
    off, legacy = book(on=False), DelayBook({"youtubetv": 45.0, "prime": 40.0}, 15.0, clock=FakeClock(T0))
    for b in (off, legacy):
        tap(b, FS1, 42.0, T0 - 2 * 86400)               # Nov 2's FS1 tap, another night
    assert (off.get(FS1).mu, off.get(FS1).sigma) == (legacy.get(FS1).mu, legacy.get(FS1).sigma)
    assert off.app_edge("youtubetv") is None and not off.add_edge_read("youtubetv", 35.0)


def test_unmeasured_channel_gets_the_prior_with_the_4s_floor():
    b = book()
    d = b.get(ESPN)
    assert d.mu == pytest.approx(34.9) and d.source == "edge_prior"
    # Rule #1: the stated doubt never goes under the gates' tested floor (the 0.6 s is the edge's spread
    # against the schedule clock, not proof the picture sits within 0.6 s of the feed)
    assert d.sigma == pytest.approx(4.0)


def test_prior_counts_as_calibrated_all_night():
    """Nick's live problem, Nov 7 12:40: CBSSN at sigma 15 > decider.uncalibrated_sigma_s 8 kept the leave gate shut
    ('delay not calibrated yet'). On the prior every YouTube TV channel is calibrated, and stays so for hours."""
    clock = FakeClock(T0)
    b = book(clock=clock)
    d = b.get("youtubetv:cbssn")
    assert d.current_sigma(T0) <= 8.0
    assert d.current_sigma(T0 + 6 * 3600) <= 6.0 + 1e-9
    assert book(on=False).get("youtubetv:cbssn").current_sigma(T0) == 15.0     # today, flag off


def test_other_nights_fs1_tap_is_replaced_tonights_tap_is_kept():
    b = book()
    tap(b, FS1, 42.0, T0 - 2 * 86400)
    assert b.get(FS1).mu == pytest.approx(34.9)           # was 42 +-8 from Nov 2's tap
    tap(b, FS1, 37.0, T0 - 60)                              # a tap tonight on this channel wins
    assert b.get(FS1).source == "tap" and b.get(FS1).mu == pytest.approx(37.0)
    assert b.get(ESPN).mu == pytest.approx(34.9)          # other channels keep the shared edge


def test_behind_live_adds_on_top():
    b = book()
    b.set_behind(ESPN, 60.0)
    assert b.get(ESPN).mu == pytest.approx(94.9)
    b.set_behind(ESPN, 0.0)
    assert b.get(ESPN).mu == pytest.approx(34.9)


def test_edge_model_d_edge_uses_the_prior_over_calib_and_the_25s_default():
    b = book(edge_on=True)
    b.calibrated[ESPN] = (27.5, 2.0)
    assert b.edge(ESPN) == (34.9, 0.6, "prior")
    d = b.set_label_behind(ESPN, 61.0, ts=T0)
    assert d.mu == pytest.approx(34.9 + 61.0) and d.sigma >= 4.0
    assert b.edge("prime:x") is None                       # Prime keeps today's model
    assert b.edge_measured(FS1)                            # delay_doubt re-anchors instead of resetting to 45


def test_sync_prior_is_the_edge_not_calib():
    b = book()
    b.calibrated[ESPN] = (27.5, 2.0)
    mu, sd = b._sync_prior(ESPN)
    assert mu == pytest.approx(34.9) and sd == pytest.approx(4.0)


def test_probe_reads_refresh_every_channel_and_age_out():
    clock = FakeClock(T0)
    b = book(clock=clock)
    b.get(ESPN), b.get(FS1)
    assert b.add_edge_read("youtubetv", 36.0, ts=T0)
    assert b.add_edge_read("youtubetv", 36.4, ts=T0)
    assert b.app_edge("youtubetv")[2] == "probe"
    assert b.get(ESPN).mu == pytest.approx(36.2) and b.get(FS1).mu == pytest.approx(36.2)
    assert b.get(FS1).source == "edge_probe"
    assert not b.add_edge_read("youtubetv", 49.0)          # > band_s (10) from the prior: a misread, dropped
    assert not b.add_edge_read("youtubetv", float("nan"))
    clock.t = T0 + 10801                                   # max_age_s 3 h: back to the prior
    b._refresh_app("youtubetv")
    assert b.get(ESPN).mu == pytest.approx(34.9)


def test_probe_sd_widens_with_disagreeing_reads():
    b = book()
    for x in (31.0, 39.0):
        b.add_edge_read("youtubetv", x, ts=T0)
    mu, sd, src = b.app_edge("youtubetv")
    assert src == "probe" and mu == pytest.approx(35.0) and sd == pytest.approx(4.0)


# ------------------------------------------------------------------------------------------------ the probe
def test_edge_delay_on_the_boundary_and_from_the_epg():
    # GameDay 9:00 ET read at ~10:55: label 1:54:25, capture 34.8 s later than start + label
    start = T0 - (T0 % 1800)
    cap = start + 6865 + 34.8
    d, s, src = ep.edge_delay(cap, 6865.0)
    assert d == pytest.approx(34.8) and s == start and src == "boundary"
    assert ep.edge_delay(cap, 6865.0, prog_start=start)[0] == pytest.approx(34.8)
    # a 7:10 tip (not on the boundary): 10 min past :00 + 35 s -> skipped, not a 635 s delay
    assert ep.edge_delay(start + 600 + 100 + 35.0, 100.0)[0] is None
    assert ep.edge_delay(cap, -1.0)[0] is None


class FakeStick:
    def __init__(self, label="1:54:25", window=False):
        self.keys = []
        self.label, self.window = label, window
        self.host = "10.0.0.9:5555"

    async def shell(self, cmd, timeout=4):
        self.keys.append(cmd.split("KEYCODE_")[-1])
        return SimpleNamespace(ok=True)

    async def screencap(self):
        return b"png"


def recognizer(stick):
    def rec(path):
        obs = []
        if stick.label:
            obs.append(SimpleNamespace(text=stick.label, confidence=0.95, x=0.48, y=0.45, width=0.05, height=0.03))
        return SimpleNamespace(status="ok", observations=obs, focus_boxes=())
    return rec


async def _nosleep(_s):
    return None


def run(coro):
    return asyncio.run(coro)


def probe(stick, tmp_path, clock=None):
    return ep.EdgeProbe(None, clock=clock or FakeClock(T0), recognize=recognizer(stick), sleep=_nosleep,
                        log_path=tmp_path / "edge_probe.jsonl")


def test_probe_presses_center_right_back_and_reads_the_delay(tmp_path, monkeypatch):
    stick = FakeStick("1:54:25")
    p = probe(stick, tmp_path)
    start = T0 - (T0 % 1800)
    monkeypatch.setattr(ep.yttv_label.time, "time", lambda: start + 6865 + 34.9)
    tuned = []

    async def relink():
        tuned.append(1)
        return True
    r = run(p.measure(stick, relink=relink, still_hidden=lambda: True, channel="espn"))
    assert r.ok and r.delay_s == pytest.approx(34.9) and r.label_s == 6865.0
    assert tuned == [1] and stick.keys == ["DPAD_CENTER", "DPAD_RIGHT", "BACK"]
    row = (tmp_path / "edge_probe.jsonl").read_text()
    assert "1:54:25" not in row and "10.0.0.9" not in row      # numbers only: no label text, no address


def test_empty_label_is_skipped_and_still_closes_the_overlay(tmp_path):
    stick = FakeStick(label="")
    r = run(probe(stick, tmp_path).measure(stick, relink=None, still_hidden=lambda: True))
    assert not r.ok and r.delay_s is None and r.status in ("no_overlay", "no_label")
    assert stick.keys == ["DPAD_CENTER", "DPAD_RIGHT", "DPAD_LEFT", "BACK"]   # one LEFT fallback, then skipped


def test_no_label_after_right_falls_back_to_one_left(tmp_path, monkeypatch):
    """Nick's noon run, Nov 7: FOX 50's 4K-capable game feed showed no label after RIGHT (3/3); LEFT shows it."""
    stick = FakeStick("1:54:25")
    rec = recognizer(stick)

    def fox(path):                     # the label shows only once a LEFT has landed
        return rec(path) if "DPAD_LEFT" in stick.keys else SimpleNamespace(status="ok", observations=[], focus_boxes=())
    p = ep.EdgeProbe(None, clock=FakeClock(T0), recognize=fox, sleep=_nosleep, log_path=tmp_path / "e.jsonl")
    start = T0 - (T0 % 1800)
    monkeypatch.setattr(ep.yttv_label.time, "time", lambda: start + 6865 + 35.1)
    r = run(p.measure(stick, relink=None, still_hidden=lambda: True, channel="fox"))
    assert r.ok and r.probe == "LEFT" and r.delay_s == pytest.approx(35.1)
    assert stick.keys == ["DPAD_CENTER", "DPAD_RIGHT", "DPAD_LEFT", "BACK"]     # BACK: no commit


def test_right_label_needs_no_left(tmp_path, monkeypatch):
    stick = FakeStick("1:54:25")
    start = T0 - (T0 % 1800)
    monkeypatch.setattr(ep.yttv_label.time, "time", lambda: start + 6865 + 35.0)
    r = run(probe(stick, tmp_path).measure(stick, relink=None, still_hidden=lambda: True))
    assert r.probe == "RIGHT" and "DPAD_LEFT" not in stick.keys


def test_failed_tune_presses_nothing(tmp_path):
    stick = FakeStick()

    async def relink():
        return False
    r = run(probe(stick, tmp_path).measure(stick, relink=relink, still_hidden=lambda: True))
    assert r.status == "tune_failed" and stick.keys == []


def test_visible_stick_is_never_pressed(tmp_path):
    stick = FakeStick()
    r = run(probe(stick, tmp_path).measure(stick, relink=None, still_hidden=lambda: False))
    assert r.status == "not_hidden" and stick.keys == []


def test_due_at_most_every_30_min_tried_or_not(tmp_path):
    clock = FakeClock(T0)
    stick = FakeStick(label="")
    p = probe(stick, tmp_path, clock)
    assert p.due()
    run(p.measure(stick, relink=None, still_hidden=lambda: True))     # an empty read still spends the slot
    assert not p.due()
    clock.t = T0 + 1799
    assert not p.due()
    clock.t = T0 + 1800
    assert p.due()


def test_engine_wires_a_read_into_the_book():
    from redzone.engine import Engine
    b = book()
    eng = SimpleNamespace(delays=b)
    assert Engine.edge_read(eng, "youtubetv", 35.4, T0) is True
    assert b.get(ESPN).mu == pytest.approx(35.4)



# ------------------------------------------------------------------------------------- the hidden-stick tick
from tests.test_control import two  # noqa: E402,F401  (fixture)


class TickProbe:
    def __init__(self, status="ok", delay=35.2):
        self.status, self.delay, self.calls = status, delay, []

    def due(self):
        return True

    async def measure(self, stick, *, relink, still_hidden, channel="", app="youtubetv"):
        assert still_hidden()
        ok = await relink()
        self.calls.append((stick.host, channel, ok))
        st = self.status if ok else "tune_failed"
        return SimpleNamespace(ok=st == "ok", status=st, delay_s=self.delay if st == "ok" else None, cap_wall=T0)


async def _preload(c, target):
    assert c.preload(target)
    await c._preload_task


def test_tick_reads_the_hidden_stick_through_a_fresh_edge_and_stays_ready(two):  # noqa: F811
    c, tuner = two
    c.cfg.update({"delay": {"edge_prior": {"refresh": {"relink_gap_s": 0}}}})
    run(_preload(c, "youtubetv:fox"))
    assert c.hidden_ready and c.hidden_target == "youtubetv:fox"
    got = []
    c.on_edge_read = lambda app, d, ts: got.append((app, d, ts))
    c.edge_probe = TickProbe()
    tuner.calls.clear()
    run(c._edge_probe_tick())
    # a fresh live edge: another channel first, then back (#391), on the HIDDEN stick only
    assert tuner.calls == [("b:5555", "youtubetv:espn"), ("b:5555", "youtubetv:fox")]
    assert got == [("youtubetv", 35.2, T0)]
    assert c.hidden_ready and c.hidden_target == "youtubetv:fox" and c.hidden.behind_on is None


def test_tick_never_touches_a_buffered_standby_or_the_visible_stick(two):  # noqa: F811
    c, tuner = two
    run(_preload(c, "youtubetv:fox"))
    c.edge_probe = TickProbe()
    c.held["youtubetv:fox"] = 60.0                  # held / scrubbed behind live: a fresh tune would throw it away
    tuner.calls.clear()
    run(c._edge_probe_tick())
    assert tuner.calls == [] and c.edge_probe.calls == []


def test_tick_waits_between_the_reset_tune_and_the_tune_back(two, monkeypatch):  # noqa: F811
    """Nick's 61fdcc84 run: 2/12 tunes back right after the reset said 'video never started'."""
    import redzone.control as ctl
    c, tuner = two
    run(_preload(c, "youtubetv:fox"))
    slept, real = [], asyncio.sleep

    async def sleep(s):
        slept.append((s, [x[1] for x in tuner.calls]))
        await real(0)
    monkeypatch.setattr(ctl.asyncio, "sleep", sleep)
    c.edge_probe = TickProbe()
    tuner.calls.clear()
    run(c._edge_probe_tick())
    assert (3.0, ["youtubetv:espn"]) in slept           # after the reset tune, before the tune back
    assert [x[1] for x in tuner.calls] == ["youtubetv:espn", "youtubetv:fox"]


def test_tick_skips_a_failed_read_and_drops_ready_on_a_failed_tune(two):  # noqa: F811
    c, tuner = two
    c.cfg.update({"delay": {"edge_prior": {"refresh": {"relink_gap_s": 0}}}})
    run(_preload(c, "youtubetv:fox"))
    got = []
    c.on_edge_read = lambda *a: got.append(a)
    c.edge_probe = TickProbe(status="no_label")
    run(c._edge_probe_tick())
    assert got == [] and c.hidden_ready             # skipped, the stick still plays fox at the live edge

    async def fail(*a, **kw):
        from redzone.tuners.tuners import TuneResult
        return TuneResult(False, "youtubetv:espn", 0.0, [], "boom")
    tuner.tune = fail
    run(c._edge_probe_tick())
    assert got == [] and not c.hidden_ready        # the next preload re-tunes it
