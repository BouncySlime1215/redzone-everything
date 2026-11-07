"""[edge model, Nick's Nov 6 order item 3] delay = D_edge(channel) + behind_live, behind_live off the overlay label.

Nov 6 stick tests: an overlay scrub (CENTER, LEFT x4, CENTER) holds a stick ~60 s behind live, the overlay
time label is the truth meter, and media_session's position is wrong after a seek. So on a scrubbed tune the
screen delay is the channel's live-edge delay plus the label's behind_live, and nothing else may move it; and
the supervisor's delay_doubt must not reset a measured channel (a reset dropped the tune's 60 s behind_live).
All behind delay.edge_model.enabled, OFF in the shipped config."""
import asyncio
import json
import math
from types import SimpleNamespace

import pytest

from redzone import supervisor as sup
from redzone import sync_probe as sp
from redzone.config import Config
from redzone.decider.decider import Decider
from redzone.decider.screen import SETTLED_DRIFT_S, DelayBook

EDGE_DEFAULT_SD_S, EDGE_LABEL_SD_S = 3.0, 1.5   # delay.edge_model.default_sd_s / label_sd_s as shipped
from redzone.supervisor import Supervisor
from conftest import FakeClock, nfl

T0 = 1_793_700_000.0
ESPN = "youtubetv:espn"


def book(on=True, clock=None, **cfg):
    b = DelayBook({"youtubetv": 45.0, "prime": 40.0}, 15.0, clock=clock or FakeClock(T0))
    vals = {"enabled": on, **cfg}
    b.edge_cfg = vals.get       # keys not given fall back to the shipped delay.edge_model defaults
    return b


def test_shipped_config_has_the_flag_off():
    cfg = Config()
    assert cfg.get("delay.edge_model.enabled") is False
    assert cfg.get("delay.edge_model.default") == {"youtubetv": 25}


def test_flag_off_a_label_read_changes_nothing():
    b = book(on=False)
    before = (b.get(ESPN).mu, b.get(ESPN).sigma, b.get(ESPN).behind)
    b.set_label_behind(ESPN, 60.0)
    d = b.get(ESPN)
    assert (d.mu, d.sigma, d.behind) == before
    assert not b.labelled(ESPN)
    b.set_behind(ESPN, 30.0)                       # today's path still works with the flag off
    assert b.get(ESPN).behind == 30.0


def test_scrubbed_tune_is_edge_plus_label():
    b = book()
    d = b.set_label_behind(ESPN, 61.0, ts=T0)
    assert d.mu == pytest.approx(25.0 + 61.0)
    # never narrower than the gates' tested 4 s floor (Rule #1: a too-narrow interval cuts plays mid-play)
    assert d.sigma == pytest.approx(max(4.0, math.hypot(EDGE_DEFAULT_SD_S, EDGE_LABEL_SD_S)))
    assert d.behind == 61.0 and d.source == "edge+label:default" and d.drift_cap == SETTLED_DRIFT_S


def test_edge_prefers_tonight_then_calibrated_then_default():
    b = book()
    assert b.edge(ESPN)[2] == "default"
    assert b.edge("prime:x") is None                # no Prime default: Prime keeps today's model
    b.calibrated[ESPN] = (27.5, 2.0)
    assert b.edge(ESPN) == (27.5, 2.0, "calib")
    assert b.set_label_behind(ESPN, 60.0).mu == pytest.approx(87.5)
    b.clear_label(ESPN)
    # tonight at the live edge: three agreeing score-bug samples on this channel (sigma 4 <= edge_max_sd_s)
    for i, x in enumerate((24.0, 24.5, 25.0)):
        b.screen(ESPN, x, T0 - 30 + i)
    assert b.edge(ESPN)[2] == "tonight" and b.edge(ESPN)[0] == pytest.approx(b.get(ESPN).mu)
    assert b.set_label_behind(ESPN, 60.0).mu == pytest.approx(b.edge(ESPN)[0] + 60.0)


def test_hold_timing_does_not_move_a_labelled_tune():
    """The pause 'hold' never held (Nov 6): once the label measured behind_live, set_behind leaves it."""
    b = book()
    b.set_label_behind(ESPN, 58.0)
    b.set_behind(ESPN, 90.0, known_sd=0.5)
    assert b.get(ESPN).behind == 58.0 and b.get(ESPN).mu == pytest.approx(83.0)
    b.held = {ESPN: 0.0}
    b.set_behind(ESPN, 0.0)                          # _land_behind resetting "every other tune"
    assert b.get(ESPN).behind == 58.0


def test_fresh_tune_clears_the_label():
    b = book()
    plain = b.get(ESPN).mu
    b.set_label_behind(ESPN, 60.0)
    b.clear_label(ESPN)
    d = b.get(ESPN)
    assert not b.labelled(ESPN) and d.behind == 0.0 and d.mu == plain


def test_a_stall_widens_a_labelled_tune():
    b = book()
    b.set_label_behind(ESPN, 60.0)
    b.buffered(ESPN)
    d = b.get(ESPN)
    assert d.behind == 63.0 and d.mu == pytest.approx(88.0)
    assert d.sigma == pytest.approx(math.hypot(EDGE_DEFAULT_SD_S, EDGE_LABEL_SD_S + 5.0))


# --- delay_doubt -------------------------------------------------------------------------------------------

def world(tmp_path, on):
    clock = FakeClock(T0)
    cfg = Config(user_path=tmp_path / "config.yaml", history_dir=tmp_path / "hist")
    cfg.update({"mode": {"dry_run": False}, "delay": {"edge_model": {"enabled": on}}}, record=False)
    delays = DelayBook(cfg.get("delay.default"), cfg.get("delay.sigma_default"), clock=clock)
    delays.edge_cfg = lambda k: cfg.get(f"delay.edge_model.{k}")
    e = SimpleNamespace(cfg=cfg, decider=Decider(cfg, delays, clock=clock), delays=delays, store=None,
                        merger=SimpleNamespace(games={"g1": nfl("g1")}))
    s = Supervisor(e, root=tmp_path, clock=clock, sleep=None, luma=None)
    return s, delays, clock


async def _doubt(s, clock):
    for _ in range(3):
        await s.rule_delay_doubt(clock())
        clock.t += 600


def _stall(delays, n):
    for _ in range(n):
        delays.buffered(ESPN)


def test_doubt_flag_off_still_resets_to_defaults_and_drops_behind(tmp_path):
    """Today's behaviour, kept with the flag off: the reset loses a 60 s behind_live (the bug item 3 fixes)."""
    s, delays, clock = world(tmp_path, on=False)
    delays.calibrated[ESPN] = (25.0, 3.0)
    delays.set_behind(ESPN, 60.0)
    _stall(delays, 2)
    asyncio.run(_doubt(s, clock))
    d = delays.get(ESPN)
    assert (d.mu, d.sigma, d.behind) == (45.0, 15.0, 0.0)


def test_doubt_reanchors_a_measured_channel_and_keeps_behind(tmp_path):
    s, delays, clock = world(tmp_path, on=True)
    delays.calibrated[ESPN] = (25.0, 3.0)            # a calibrated edge: this channel is measured
    delays.set_behind(ESPN, 60.0)
    _stall(delays, 2)
    assert delays.get(ESPN).current_sigma(clock()) > 20
    asyncio.run(_doubt(s, clock))
    d = delays.get(ESPN)
    assert d.behind == 60.0 and d.mu == pytest.approx(85.0) and d.source == "edge:calib"
    assert d.sigma < 15.0
    rows = [r for r in sup.read_jsonl(tmp_path / "supervisor.jsonl") if r["kind"] == "fix"]
    assert rows and rows[0]["action"] == "re-anchor measured delay" and "behind 60s" in rows[0]["detail"]


def test_doubt_on_a_labelled_tune_keeps_the_label_and_asks_for_a_reread(tmp_path):
    s, delays, clock = world(tmp_path, on=True)
    asked = []
    delays.on_label_stale = asked.append
    delays.set_label_behind(ESPN, 60.0)
    _stall(delays, 4)                                # 4 stalls: label doubt 1.5 + 20
    asyncio.run(_doubt(s, clock))
    d = delays.get(ESPN)
    assert d.behind == 72.0 and d.mu == pytest.approx(97.0) and delays.labelled(ESPN)
    assert asked == [ESPN]


def test_doubt_unmeasured_channel_still_falls_back_with_the_flag_on(tmp_path):
    s, delays, clock = world(tmp_path, on=True)
    d = delays.get(ESPN)
    d.mu, d.sigma = 61.0, 32.0
    asyncio.run(_doubt(s, clock))
    assert (delays.get(ESPN).mu, delays.get(ESPN).sigma) == (45.0, 15.0)


# --- sync probe: media_session after a seek ------------------------------------------------------------------

def test_edge_tick_hands_on_the_label_not_media_session(tmp_path):
    from test_sync_edge import Control, Cfg, later
    p = sp.SyncProbe(Cfg({"mode.dry_run": False, "sync.edge.enabled": True, "sync.edge.force_live": True,
                          "sync.edge.force_live_visible": True}), Control(), tmp_path)
    calls = []
    p.on_force_live = lambda target, row: calls.append(row["role"]) or True
    p.label_of = lambda target: 60.0 if target == "youtubetv:nbc" else None
    c = p.control
    c.held = {}
    asyncio.run(p.edge_tick(now=T0))
    later(c, 10.0, 5.0)                              # media_session says 5 s behind on both sticks
    rows = asyncio.run(p.edge_tick(now=T0 + 10))
    hidden = next(r for r in rows if r["role"] == "hidden")
    assert hidden["pos_after_seek"] and hidden["label_behind_s"] == 60.0 and not hidden.get("would_force")
    assert c.behind_live["10.0.0.6"] == 60.0         # the label, not media_session's 5 s
    assert calls == ["visible"]                      # the seeked stick is never re-linked off a wrong position


def test_label_resyncs_the_filter_around_edge_plus_label():
    """A scrub moves the picture: the Kalman filter re-syncs from edge + label, and its log rows carry the label's
    behind so calib.auto still fits the at-the-edge delay (z - behind)."""
    b = book()
    sync = b.attach_sync()
    b.calibrated[ESPN] = (25.0, 3.0)
    b.get(ESPN)
    b.set_label_behind(ESPN, 60.0)
    assert b._sync_prior(ESPN) == (pytest.approx(85.0), pytest.approx(4.0))
    assert sync.behind_of(ESPN) == 60.0
