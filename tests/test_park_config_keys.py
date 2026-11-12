"""[PARK-CONFIG-KEYS] Every park.* setting the app's code reads is a key in default_config.yaml, at the code's default.

The Config rejects every key the YAML lacks, so a setting the code reads but the YAML lacks can never be turned on
(Nov 11: `park.picture_grade.enabled` and `park.catchup.enabled` read MISSING; PR 431 found the same for `park.pad_k`).
Two kinds of read are checked: a literal "park.a.b" in redzone/, and the dataclass families read field by field
(`ShowParams`, `FactsParams`, `TimingParams`, `CatchupParams`, `PackParams`). `ParkParams` (park_core) is the replay
simulator's and is not read from the config by the app.
"""
from __future__ import annotations

import math
import re
from dataclasses import fields
from pathlib import Path

import pytest

from redzone.config import Config
from redzone.noggin.catchup_pack import PackParams
from redzone.noggin.park_catchup import CatchupParams
from redzone.noggin.park_facts import FactsParams
from redzone.noggin.park_show import STEP_HOME, ShowParams
from redzone.noggin.park_timing import TimingParams

ROOT = Path(__file__).resolve().parents[1]
MISSING = object()
# Keys the YAML set on purpose to a value other than the dataclass default (the app runs these; the replay's own
# FactsParams() keeps the old "off" values): the feed-quality caps of PARK-CONTROLLER.
SHIPPED_OVERRIDES = {"park.possession_cap_s", "park.late_row_s"}


@pytest.fixture(scope="module")
def cfg():
    return Config(user_path=ROOT / "tests" / "no-such-config.yaml")


def test_every_literal_park_key_in_the_code_is_in_the_yaml(cfg):
    missing = set()
    for p in (ROOT / "redzone").rglob("*.py"):
        for m in re.finditer(r"[\"'](park\.[a-z0-9_]+(?:\.[a-z0-9_]+)*)[\"']", p.read_text()):
            if cfg.get(m.group(1), MISSING) is MISSING:
                missing.add(f"{m.group(1)} ({p.relative_to(ROOT)})")
    assert not missing, sorted(missing)


def _same(a, b) -> bool:
    if isinstance(a, float) and math.isinf(a) and b is None:
        return True                                             # null in the YAML = the code's "no cap" (inf)
    if isinstance(a, float) and isinstance(b, (int, float)) and not isinstance(b, bool):
        return a == b or (math.isinf(a) and math.isinf(b))
    return a == b or (a == () and b in ([], ()))


FAMILIES = [
    (ShowParams, lambda n: "park.max_park_s" if n == "max_park" else f"park.show.{n}", set(STEP_HOME)),
    (FactsParams, lambda n: f"park.{n}", {f.name for f in fields(FactsParams) if not isinstance(f.default, float)}),
    (TimingParams, lambda n: "park.pad_k" if n == "pad_k" else f"park.timing.{n}", {"live_lag"}),
    (CatchupParams, lambda n: f"park.catchup.{n}", {"pack"}),
    (PackParams, lambda n: f"park.catchup.{n}", set()),
]


@pytest.mark.parametrize("cls,key,skip", FAMILIES, ids=[f[0].__name__ for f in FAMILIES])
def test_every_field_read_from_the_config_is_in_the_yaml_at_its_code_default(cfg, cls, key, skip):
    bad = []
    for f in fields(cls):
        if f.name in skip:
            continue
        v = cfg.get(key(f.name), MISSING)
        if v is MISSING:
            bad.append(f"{key(f.name)} missing")
        elif key(f.name) == "park.max_park_s" or key(f.name) in SHIPPED_OVERRIDES:
            continue                                            # one key with its own value, on purpose
        elif not _same(f.default, v):
            bad.append(f"{key(f.name)}: yaml {v!r} != code {f.default!r}")
    assert not bad, bad


@pytest.mark.parametrize("cls", [ShowParams, FactsParams, TimingParams, CatchupParams, PackParams])
def test_reading_the_shipped_config_gives_the_code_defaults(cfg, cls):
    got, want = cls.from_config(cfg.get), cls()
    skip = {"stop_max", "possession_cap_s", "late_row_s"} if cls is FactsParams else set()   # shipped on purpose
    assert {f.name: getattr(got, f.name) for f in fields(cls) if f.name not in skip} == \
           {f.name: getattr(want, f.name) for f in fields(cls) if f.name not in skip}


def test_the_shipped_config_is_plain_json(cfg):
    """The phone's settings view sends the whole config as JSON: no inf or nan anywhere (PR 438 CI)."""
    import json
    json.dumps(cfg.data, allow_nan=False)


def test_the_new_features_stay_off(cfg):
    assert cfg.rejected == []
    assert cfg.get("park.picture_grade.enabled") is False and cfg.get("park.picture_check.live") is False
    assert cfg.get("park.catchup.enabled") is False
