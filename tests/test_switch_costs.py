"""[switch costs] One source of truth: default_config.yaml's measured costs, priced by
contracts/switching.ConfigSwitchPaths exactly as the live Controller falls back to them."""
from pathlib import Path

import pytest
import yaml

from redzone import control as ctl
from redzone.channels import ChannelMap
from redzone.contracts.switching import (IN_APP_TUNE, TV_FLIP, TUNE_APPS, ConfigSwitchPaths, SwitchPaths,
                                         deadline, half_step)
from redzone.lab import evaldata

ROOT = Path(__file__).resolve().parent.parent


class _TV:
    def ready(self):
        return True

    def paths(self):
        return ["smartthings"]


def test_shipped_flip_cost_is_the_measured_smartthings_switch(cfg):
    p = ConfigSwitchPaths(cfg)
    assert p.flip().kind == TV_FLIP
    assert p.flip().cost_s == pytest.approx(1.89 + 1.0)       # SmartThings median (live lab, 64/64) + HDMI settle
    assert p.flip().cost_p90_s == pytest.approx(2.02 + 1.0)   # its measured max + settle
    # The table the decider plans with says the same thing, so the two can't drift apart.
    assert cfg.defaults["decider"]["switch_cost"]["hdmi_flip"] == half_step(1.89 + 1.0) == p.costs()["hdmi_flip"]


def test_controller_and_config_paths_agree_before_any_sample(cfg, tmp_path):
    c = ctl.Controller(cfg, ChannelMap(tmp_path / "ch.json"))
    c.tv = _TV()
    p = ConfigSwitchPaths(cfg)
    assert c.flip_estimate() == pytest.approx(p.flip().cost_s)
    assert c._flip_upper() == pytest.approx(p.flip().cost_p90_s)
    for app in TUNE_APPS:
        assert c.tune_estimate(app) == p.tune(app)


def test_shipped_tune_costs_are_the_pinned_measurements(cfg):
    pinned = ROOT / "data" / "eval_config.yaml"
    if not pinned.exists():
        pytest.skip("pinned eval data not fetched (python scripts/fetch_eval_data.py)")
    meas = (yaml.safe_load(pinned.read_text()) or {}).get("decider", {}).get("switch_cost", {})
    for app in TUNE_APPS:
        assert cfg.defaults["decider"]["switch_cost"][app] == pytest.approx(float(meas[app])), app


def test_config_paths_follow_the_switch_paths_contract(cfg):
    p = ConfigSwitchPaths(cfg, two_stick=True, on_hidden=lambda t: t == "prime:game1")
    assert isinstance(p, SwitchPaths) and not p.busy()
    flip = p.flip()
    (hit,) = p.paths("prime:game1")
    assert hit.preloaded and hit.kind == TV_FLIP and hit.cost_s == flip.cost_s
    (miss,) = p.paths("youtubetv:espn")
    assert miss.kind == IN_APP_TUNE and miss.cost_s == pytest.approx(5.5 + flip.cost_s)
    assert deadline(p.paths("youtubetv:espn")) == pytest.approx(5.5 + flip.cost_p90_s)
    (one,) = ConfigSwitchPaths(cfg, two_stick=False).paths("prime:game1")
    assert one.cost_s == one.cost_p90_s == 27.0
    assert p.paths("") == () and deadline(()) == float("inf")


def test_config_paths_read_a_users_own_measurements(cfg):
    cfg.update({"decider": {"switch_cost": {"prime": 12.5}}, "two_stick": {"tv_flip_prior_s": {"median": 1.5}}},
               record=False)
    p = ConfigSwitchPaths(cfg)
    assert p.tune("prime") == 12.5 and p.flip().cost_s == pytest.approx(2.5)


def test_scorecards_price_switches_from_the_shipped_table(cfg):
    cfg.data["decider"]["switch_cost"] = {"youtubetv": 1.0, "prime": 99.0, "espn": 1.0, "hdmi_flip": 9.0}
    got = evaldata.use_shipped_switch_costs(cfg)
    assert cfg.get("decider.switch_cost") == got == cfg.defaults["decider"]["switch_cost"]
