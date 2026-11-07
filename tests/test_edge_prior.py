"""[edge prior, Nick Nov 7] YouTube TV's live edge is ONE delay for every channel: 17 hidden-stick reads on 7 channels,
34.3-35.5 s, mean 34.9, sd ~0.4 (delay = (capture - EPG start) - label at the live edge). The prior replaces other
nights' per-channel guesses (youtubetv:fs1 42 +-8) and the edge model's 25 s default, refreshed by the probe at
most every 30 min, empty label reads skipped. All behind delay.edge_prior.enabled / .refresh.enabled, OFF."""

import pytest

from redzone.config import Config
from redzone.decider.screen import DelayBook
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


def test_shipped_config_has_the_flag_off_and_nicks_numbers():
    cfg = Config()
    assert cfg.get("delay.edge_prior.enabled") is False     # Nick turns it on at runtime (/api/settings)
    assert cfg.get("delay.edge_prior.prior") == {"youtubetv": {"mu_s": 34.9, "sd_s": 0.6}}
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


def test_runtime_setting_turns_it_on(tmp_path):
    """What Nick does on the Mac: delay.edge_prior.enabled true through the settings API (validated, undoable)."""
    cfg = Config(tmp_path / "config.yaml")
    cfg.update({"delay": {"edge_prior": {"enabled": True}}})
    b = DelayBook({"youtubetv": 45.0}, 15.0, clock=FakeClock(T0))
    b.edge_prior_cfg = lambda key: cfg.get(f"delay.edge_prior.{key}")
    d = b.get("youtubetv:cbssn")
    assert d.mu == 34.9 and d.current_sigma(T0) <= 8.0


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


