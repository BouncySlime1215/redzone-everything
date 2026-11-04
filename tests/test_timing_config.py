"""Timing constants live in default_config.yaml (two_stick.shadow.*, demo.*), with today's values."""
import ast
from pathlib import Path

import pytest

from redzone import config, standby
from redzone.config import ConfigError, validate

ROOT = Path(__file__).resolve().parent.parent
OLD = {"two_stick.shadow.flip_s": 3.0, "two_stick.shadow.tune_s": 7.0, "two_stick.shadow.keep_s": 2.5,
       "demo.feed_lag_s": 8.0, "demo.play_len_s": 7.0, "demo.tune_s": 5.0, "demo.calibrated_sigma_s": 3.0}


class Stub:
    """Config-like: get(dotted, default)."""

    def __init__(self, values: dict):
        self.values = values

    def get(self, dotted, default=None):
        return self.values.get(dotted, default)


@pytest.mark.parametrize("key,value", sorted(OLD.items()))
def test_defaults_keep_the_old_literals(key, value):
    assert config.default(key) == value


def test_default_missing_key_falls_back():
    assert config.default("demo.nope", 1.5) == 1.5


def _shadow(s):
    return (s.FLIP_S, s.tune_s, s.keep_s)


def test_shadow_stick_defaults_unchanged():
    assert _shadow(standby.ShadowStick()) == (3.0, 7.0, 2.5)
    assert _shadow(standby.ShadowStick(cfg=Stub({}))) == (3.0, 7.0, 2.5)
    assert _shadow(standby.ShadowStick(cfg=config.Config(user_path=Path("/nonexistent/x.yaml")))) == (3.0, 7.0, 2.5)


def test_shadow_stick_cfg_and_args_override():
    s = standby.ShadowStick(cfg=Stub({"two_stick.shadow.flip_s": 2.0, "two_stick.shadow.tune_s": 9.0,
                                      "two_stick.shadow.keep_s": 1.0}))
    assert _shadow(s) == (2.0, 9.0, 1.0)
    assert _shadow(standby.ShadowStick(tune_s=4, keep_s=1, flip_s=2, cfg=Stub({}))) == (2.0, 4.0, 1.0)


@pytest.mark.parametrize("path", ["redzone/standby.py", "redzone/demo/engine.py"])
def test_no_numeric_literal_assigned_to_a_seconds_name(path):
    bad = []
    for node in ast.walk(ast.parse((ROOT / path).read_text())):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, (int, float)) and not isinstance(node.value.value, bool):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                name = t.id if isinstance(t, ast.Name) else t.attr if isinstance(t, ast.Attribute) else ""
                if name.endswith("_S"):
                    bad.append((node.lineno, name))
    assert not bad, f"{path}: timing literals belong in default_config.yaml: {bad}"


def test_validate_accepts_new_keys_and_rejects_out_of_range():
    defaults = config.Config(user_path=Path("/nonexistent/x.yaml")).defaults
    validate({"two_stick": {"shadow": {"flip_s": 2.0, "tune_s": 9.0, "keep_s": 1.0}},
              "demo": {"feed_lag_s": 10.0, "play_len_s": 6.0, "tune_s": 4.0, "calibrated_sigma_s": 2.0}}, defaults)
    for bad in ({"two_stick": {"shadow": {"flip_s": 0.0}}}, {"two_stick": {"shadow": {"tune_s": 61}}},
                {"demo": {"feed_lag_s": -1}}, {"demo": {"tune_s": 61}}):
        with pytest.raises(ConfigError):
            validate(bad, defaults)
