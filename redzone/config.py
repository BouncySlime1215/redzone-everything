"""Config: defaults + user overrides, validated, versioned, and undoable.

Guardrails:
- Every edit is checked against LIMITS before it's saved; bad values are rejected with a reason.
- Every save writes a timestamped copy to data/config_history/, so `rollback()` can undo it.
- Unknown keys are rejected so a typo can't silently do nothing.
"""
from __future__ import annotations

import copy
import logging
import time
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DEFAULTS = Path(__file__).with_name("default_config.yaml")
DATA = ROOT / "data"
USER = DATA / "config.yaml"
HISTORY = DATA / "config_history"

# (min, max) for numeric settings, matched by dotted-path prefix; most specific wins.
LIMITS: dict[str, tuple[float, float]] = {
    "server.port": (1024, 65535),
    "polling.live_s": (5, 60),
    "college.top_n": (1, 25),
    "polling.hot_s": (2, 30),
    "polling.idle_s": (30, 3600),
    "plan.verify_lead_min": (5, 90),
    "score.neutral_cap": (0.5, 1.0),
    "score.personal_cap": (0, 60),
    "score.stakes.min": (0.5, 1.0),
    "score.stakes.max": (1.0, 1.5),
    "score.garbage.moment_mult": (0.0, 1.0),
    "score.garbage.comeback_s": (0, 1800),
    "delay": (0, 180),
    "replay": (0, 7200),
    "reel.min_clips": (1, 12),
    "reel.min_s": (5, 120),
    "reel.max_s": (10, 180),
    "reel.parallel": (1, 4),
    "reel.jump.key_p_no_rate": (0.0, 1.0),
    "reel.jump.key_ratio": (1.0, 10.0),
    "reel.jump.tail_p": (0.0, 1.0),
    "reel.jump.min_gain": (0.0, 100.0),
    "reel.jump.margin_s": (0.0, 120.0),
    "reel.jump.p10_s": (0.0, 1800.0),
    "reel": (0, 100000),
    "fantasy.team_id": (0, 100),
    "fantasy.season": (2020, 2100),
    "break_lengths_s": (0, 1800),
    "score.league_weight": (0.5, 1.5),
    "score.personal.my_team_league": (0.0, 1.5),
    "score": (-100, 100),
    "decider": (0, 1000),
    "two_stick.min_gain": (0, 100),
    "two_stick.input_settle_s": (0, 10),
    "two_stick.park_max_s": (30, 1800),
    "two_stick": (0, 3600),
    "switch_speed.sync_every_s": (60, 3600),
    "autonomy.busy_score": (0, 100),
    "brain.break_weight": (0.0, 1.0),
    "brain.hot_reason_min": (50, 100),
    "brain": (0, 30),
    "supervisor.budget.window_nights": (1, 60),
    "supervisor.budget.failure_pct": (0, 100),
    "supervisor.budget.mid_play_cuts": (0, 100),
    "supervisor.budget.dead_air_s": (1, 300),
    "supervisor.budget.dead_air_pct": (0, 100),
    "supervisor.budget.safe_mode_min": (0, 1440),
}

# Former rate/dwell limits are deliberately inactive. Old saved files and lab
# recommendations can still load; preserve their other preferences while dropping these.
RETIRED_SETTINGS = frozenset({"decider.min_dwell_s", "decider.chase_min_dwell_s",
                              "decider.max_switches_per_5min", "decider.min_gap_s",
                              "decider.ping_pong_freeze_s"})


def without_retired(cfg: dict, path: str = "") -> dict:
    out = {}
    for key, value in cfg.items():
        dotted = f"{path}.{key}" if path else key
        if dotted not in RETIRED_SETTINGS:
            out[key] = without_retired(value, dotted) if isinstance(value, dict) else copy.deepcopy(value)
    return out


class ConfigError(ValueError):
    pass


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _limit_for(path: str) -> tuple[float, float] | None:
    best = None
    for prefix, rng in LIMITS.items():
        if path == prefix or path.startswith(prefix + "."):
            if best is None or len(prefix) > len(best[0]):
                best = (prefix, rng)
    return best[1] if best else None


def validate(cfg: dict, defaults: dict, path: str = "") -> None:
    for k, v in cfg.items():
        p = f"{path}.{k}" if path else k
        if k not in defaults:
            # Team lists and league toggles are open-ended; everything else must exist.
            if not path.startswith(("teams", "mode.leagues", "replay.apps")):
                raise ConfigError(f"Unknown setting '{p}'")
            continue
        d = defaults[k]
        if isinstance(d, dict):
            if not isinstance(v, dict):
                raise ConfigError(f"'{p}' must be a group of settings")
            validate(v, d, p)
        elif isinstance(d, bool):
            if not isinstance(v, bool):
                raise ConfigError(f"'{p}' must be on or off")
        elif isinstance(d, (int, float)):
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ConfigError(f"'{p}' must be a number")
            rng = _limit_for(p)
            if rng and not (rng[0] <= v <= rng[1]):
                raise ConfigError(f"'{p}' must be between {rng[0]} and {rng[1]} (got {v})")
        elif isinstance(d, str):
            if not isinstance(v, str):
                raise ConfigError(f"'{p}' must be text")
        elif isinstance(d, list):
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                raise ConfigError(f"'{p}' must be a list of names")


def prune(cfg: dict, defaults: dict, path: str = "") -> tuple[dict, list[str]]:
    """The valid part of cfg, and why each dropped setting was dropped. One bad setting (a typo, an
    old key, a value out of range) costs only that setting, never the rest of your file."""
    kept, dropped = {}, []
    for k, v in cfg.items():
        p = f"{path}.{k}" if path else k
        d = defaults.get(k) if isinstance(defaults, dict) else None
        if isinstance(d, dict) and isinstance(v, dict):
            sub, why = prune(v, d, p)
            kept[k] = sub
            dropped += why
            continue
        try:
            validate({k: v}, defaults, path)
        except ConfigError as e:
            dropped.append(str(e))
            continue
        kept[k] = v
    return kept, dropped


class Config:
    def __init__(self, user_path: Path = USER, history_dir: Path = HISTORY):
        self.user_path = user_path
        self.history_dir = history_dir
        self.defaults = yaml.safe_load(DEFAULTS.read_text())
        self.overrides: dict = {}
        self.rejected: list[str] = []   # settings dropped at load (the scorecards refuse to grade on these)
        if user_path.exists():
            try:
                raw = yaml.safe_load(user_path.read_text()) or {}
                if not isinstance(raw, dict):
                    raise ConfigError(f"{user_path.name} isn't a group of settings")
            except (yaml.YAMLError, ConfigError, OSError) as e:
                # An unreadable file never takes the app down: defaults, and say so.
                log.error("config: %s unreadable, using defaults: %s", user_path, e)
                raw, self.rejected = {}, [f"unreadable: {e}"]
            loaded = without_retired(raw)
            # A broken setting never takes the app down, and never takes your other settings with it.
            self.overrides, dropped = prune(loaded, self.defaults)
            self.rejected += dropped
            for why in dropped:
                log.warning("config: ignoring a setting in %s: %s", user_path.name, why)
        self.data = _deep_merge(self.defaults, self.overrides)

    def get(self, dotted: str, default=None):
        node = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def update(self, changes: dict, record: bool = True) -> dict:
        """Apply a partial change (nested dict). Raises ConfigError if invalid.
        record=False is for values the app measured itself (switch times): they're validated and
        saved like any edit, but kept out of the undo history so Undo still undoes YOUR last change."""
        new_over = without_retired(_deep_merge(self.overrides, changes))
        validate(new_over, self.defaults)
        self._save(new_over, record=record)
        return self.data

    def rollback(self) -> bool:
        versions = sorted(self.history_dir.glob("*.yaml")) if self.history_dir.exists() else []
        if len(versions) < 2:
            self._save({}, record=False)
            return bool(versions)
        versions[-1].unlink()
        prev = yaml.safe_load(versions[-2].read_text()) or {}
        self._save(prev, record=False)
        return True

    def _save(self, over: dict, record: bool = True) -> None:
        over = without_retired(over)
        self.user_path.parent.mkdir(parents=True, exist_ok=True)
        text = yaml.safe_dump(over, sort_keys=False)
        tmp = self.user_path.with_suffix(".tmp")
        tmp.write_text(text)
        tmp.replace(self.user_path)  # atomic: a crash mid-save can't corrupt the file
        if record:
            self.history_dir.mkdir(parents=True, exist_ok=True)
            # Keep revisions ordered across rapid saves, clock corrections and reloads.
            # The prefix sorts after legacy timestamp names, which remain undoable.
            revisions = [int(p.stem[3:]) for p in self.history_dir.glob("v2-*.yaml")
                         if p.stem[3:].isdigit()]
            revision = max(time.time_ns(), max(revisions, default=0) + 1)
            while True:
                try:
                    with (self.history_dir / f"v2-{revision:020d}.yaml").open("x") as version:
                        version.write(text)
                    break
                except FileExistsError:
                    revision += 1
        self.overrides = over
        self.data = _deep_merge(self.defaults, over)
