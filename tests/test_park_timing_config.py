"""[PARK-TIMING-CONFIG] park.pad_k and park.timing.* are settings the Config accepts, read through to the PARK timing.

Before: TimingParams.from_config read `park.pad_k` and `park.timing.<field>`, but default_config.yaml had neither, so
Config dropped them as unknown settings and a data/config.yaml could never change a game's padding. Now:
- the YAML default of every key equals the code's own default (a default config is decision-identical);
- a config file that sets them is read through, to the padding the guard enforces;
- `park.timing.live_lag` stays unset by default, so `park.steps.live_lag_s` still decides it.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
import yaml

from redzone.config import Config
from redzone.noggin.k_table import K_SD_MIN
from redzone.noggin.park_show import ShowTiming
from redzone.noggin.park_timing import TIMING, GameK, GameTiming, TimingParams


def cfg_with(tmp_path: Path, park: dict | None = None) -> Config:
    path = tmp_path / "config.yaml"
    if park is not None:
        path.write_text(yaml.safe_dump({"park": park}))
    return Config(user_path=path, history_dir=tmp_path / "history")


def test_default_config_is_the_code_default(tmp_path):
    c = cfg_with(tmp_path)
    assert c.rejected == []
    assert TimingParams.from_config(c.get) == TimingParams() == TIMING
    assert c.get("park.timing.k_sd_min") == K_SD_MIN
    for f in dataclasses.fields(TimingParams):
        key = "park.pad_k" if f.name == "pad_k" else f"park.timing.{f.name}"
        if f.name == "live_lag":
            assert c.get(key, "absent") is None          # unset: park.steps.live_lag_s decides (ParkHands._live_lag)
        else:
            assert c.get(key, "absent") == f.default, key


def test_a_config_setting_them_is_read_through(tmp_path):
    c = cfg_with(tmp_path, {"pad_k": 5, "timing": {"k_sd_min": 1.91, "prior_sd": 13.0, "live_lag": 22}})
    assert c.rejected == []
    tp = TimingParams.from_config(c.get)
    assert (tp.pad_k, tp.k_sd_min, tp.prior_sd, tp.live_lag) == (5.0, 1.91, 13.0, 22.0)
    assert tp.read_sd == TIMING.read_sd                     # what the file leaves out keeps the code's value


@pytest.mark.parametrize("league", ["nba", "cbb", "nfl"])
def test_k_sd_min_191_pads_a_read_tune_with_k_8s_and_leaves_no_k_games_alone(tmp_path, league):
    tp = TimingParams.from_config(cfg_with(tmp_path, {"timing": {"k_sd_min": 1.91}}).get)
    known, unknown = GameTiming(league, GameK(0.0, 0.0), tp), GameTiming(league, None, tp)
    tm = ShowTiming([known, unknown], read=True, params=tp)
    base = ShowTiming([GameTiming(league, None, TIMING)], read=True)
    assert tm.pad(0, True) == 8
    assert tm.pad(1, True) == base.pad(0, True)              # no K: unchanged (the wide prior)


def test_out_of_range_values_are_rejected_not_used(tmp_path):
    c = cfg_with(tmp_path, {"pad_k": 2, "timing": {"k_sd_min": -1}})
    assert len(c.rejected) == 2
    assert TimingParams.from_config(c.get) == TIMING
