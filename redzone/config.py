"""Config: defaults + user overrides, validated, versioned, and undoable.

Why: knobs change often on a live app: one store rejects bad values before saving and rollback() undoes a save (see lessons: unreadable config fell back to dry run).

Guardrails:
- Every edit is checked against LIMITS before it's saved; bad values are rejected with a reason.
- Every save writes a timestamped copy to data/config_history/, so `rollback()` can undo it.
- Unknown keys are rejected so a typo can't silently do nothing.
"""
from __future__ import annotations

import contextlib
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
    "legal.leave_wait_s": (1, 180),
    "park.football_slider": (0, 100),
    "noggin.priority.prefs.bets_weight": (0, 2),
    "noggin.priority.prefs.rivalry_points": (0, 15),
    "noggin.priority.prefs.nhl_pp_ot_boost": (0, 2),
    "noggin.priority.prefs.switch_frequency": (0.5, 2),
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
    "delay.sync.min_sigma_s": (1, 15),   # [auto sync] below ~1 s the gates would trust a filter more than any signal can be
    "delay": (0, 180),
    "delay.edge_model.default_sd_s": (1, 15),   # [edge model] never surer than one stick-to-stick edge spread
    "delay.edge_model.label_sd_s": (0.5, 10),
    "delay.edge_model.edge_max_sd_s": (2, 15),
    "delay.edge_prior.refresh.min_gap_s": (600, 86400),   # [edge prior] never more often than every 10 min
    "delay.edge_prior.refresh.max_age_s": (600, 86400),
    "delay.edge_prior.refresh.keep": (1, 50),
    "delay.edge_prior.refresh.band_s": (1, 30),
    "delay.edge_prior.refresh.max_label_gap_s": (10, 600),
    "delay.edge_prior.refresh.boundary_s": (900, 3600),
    "delay.edge_prior.refresh": (0, 60),
    "delay.edge_prior.prior": (0.2, 180),
    "delay.edge_prior.drift_cap_s": (0, 10),
    "replay": (0, 7200),
    "reel.min_clips": (1, 12),
    "reel.min_s": (5, 120),
    "reel.max_s": (10, 180),
    "reel.parallel": (1, 4),
    "reel": (0, 100000),
    "redzone_mode.hot_heat": (0.0, 1.0),
    "redzone_mode.dead_damp": (0.0, 1.0),
    "redzone_mode": (0, 100),
    "fantasy.team_id": (0, 100),
    "fantasy.season": (2020, 2100),
    "break_lengths_s": (0, 1800),
    "score.league_weight": (0.5, 1.5),
    "score.personal.my_team_league": (0.0, 1.5),
    "score": (-100, 100),
    "decider": (0, 1000),
    "jump_mode": (0, 600),
    "tuners.prime_tiles": (1, 3),
    "tuners": (0, 10),
    "two_stick.min_gain": (0, 100),
    "two_stick.input_settle_s": (0, 10),
    "two_stick.park_max_s": (30, 1800),
    "two_stick.shadow": (0.1, 60),   # replay/backtest stick timings (standby.ShadowStick)
    "two_stick": (0, 3600),
    "switch_speed.sync_every_s": (60, 3600),
    "autonomy.busy_score": (0, 100),
    "governor.every_s": (5, 600),
    "governor.calm_s": (0, 3600),
    "governor.busy_load": (0.2, 4.0),
    "governor.battery_low_pct": (0, 100),
    "governor": (100, 16000),
    "brain.break_weight": (0.0, 1.0),
    "brain.break_blend": (-10.0, 10.0),
    "brain.hot_reason_min": (50, 100),
    "brain.version": (2, 3),
    "brain.v3_entry_q": (10, 50),
    "brain": (0, 30),
    "reasoner.min_interval_s": (5, 600),
    "reasoner.max_tokens": (32, 400),
    "reasoner": (0, 15),
    "supervisor.budget.window_nights": (1, 60),
    "supervisor.budget.failure_pct": (0, 100),
    "supervisor.budget.mid_play_cuts": (0, 100),
    "supervisor.budget.dead_air_s": (1, 300),
    "supervisor.budget.dead_air_pct": (0, 100),
    "supervisor.budget.safe_mode_min": (0, 1440),
    "phone.why_tag_min": (0, 100),
    "phone.worth_probe_days": (1, 60),
    "phone.orient_beat_s": (1, 10),
    "phone.catchup_visible": (1, 5),
    "phone.orient_override_s": (5, 300), "fast_feeds.nba_s": (1, 30), "fast_feeds.nhl_s": (1, 30), "fast_feeds.mlb_s": (1, 30), "fast_feeds.board_s": (5, 120), "fast_feeds.max_games": (1, 15), "phone.last_night_hours": (1, 18),
    "timing.behind_live_s.adaptive.floor_s": (10, 15),   # [RC8] Nick: a hard floor of ~10-15 s, never 0
    "timing.behind_live_s.adaptive.ratchet_step": (0, 15),   # gate v28: at most 15 s down per release
    "timing.behind_live_s.adaptive": (0, 180),
    "timing.behind_live_s.rc8_fixed_s": (0, 90),
    "timing.behind_live_s.max_s": (0, 90),             # Nick's cap: never more than 90 s behind live
    "demo": (0, 60),   # replay simulator timings (redzone/demo/engine.py)
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


_DEFAULTS: dict | None = None


def default(dotted: str, fallback=None):
    """A value from default_config.yaml, for code that has no live Config (read once, cached)."""
    global _DEFAULTS
    if _DEFAULTS is None:
        _DEFAULTS = yaml.safe_load(DEFAULTS.read_text())
    node = _DEFAULTS
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return fallback
        node = node[part]
    return copy.deepcopy(node)


class ConfigError(ValueError):
    pass


_MISSING = object()


def for_league(cfg, dotted: str, league: str | None, default=None):
    """[league overrides, Nick Nov 4 8:20 PM: promote per league] The value of a setting for a game of
    this league: league_overrides.<league> (a flat map of dotted keys) wins over the global setting,
    so a league can run a graded set while the others keep live behavior. A key that names a group
    (decider.entry.max_wait_s) replaces that whole group for the league."""
    if league:
        over = cfg.get(f"league_overrides.{league}") or {}
        if over:
            parts = dotted.split(".")
            for i in range(len(parts), 0, -1):
                key = ".".join(parts[:i])
                if key in over:
                    node = over[key]
                    for part in parts[i:]:
                        if not isinstance(node, dict) or part not in node:
                            return default
                        node = node[part]
                    return node
    return cfg.get(dotted, default)


def any_league(cfg, dotted: str) -> bool:
    """[league overrides] The setting is on globally or for any league (a gate that a per-league check
    then narrows, e.g. the time-shift buffer's controller side)."""
    if cfg.get(dotted):
        return True
    return any(bool(for_league(cfg, dotted, lg)) for lg in (cfg.get("league_overrides") or {}))


def _validate_league_overrides(over, defaults: dict) -> None:
    if not isinstance(over, dict):
        raise ConfigError("'league_overrides' must be a group of settings")
    for lg, keys in over.items():
        if not isinstance(keys, dict):
            raise ConfigError(f"'league_overrides.{lg}' must be a group of settings")
        for dotted, v in keys.items():
            parts = str(dotted).split(".")
            node = defaults
            for part in parts[:-1]:
                if not isinstance(node, dict) or part not in node:
                    raise ConfigError(f"Unknown setting 'league_overrides.{lg}.{dotted}'")
                node = node[part]
            if not isinstance(node, dict) or parts[-1] not in node:
                raise ConfigError(f"Unknown setting 'league_overrides.{lg}.{dotted}'")
            validate({parts[-1]: v}, node, ".".join(parts[:-1]))


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
        if p == "league_overrides":
            _validate_league_overrides(v, defaults)
            continue
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
        if p == "league_overrides" and isinstance(v, dict):
            # [league overrides] one bad key costs only that key, as everywhere else
            sub = {}
            for lg, keys in v.items():
                for dotted, val in (keys.items() if isinstance(keys, dict) else []):
                    try:
                        _validate_league_overrides({lg: {dotted: val}}, defaults)
                    except ConfigError as e:
                        dropped.append(str(e))
                        continue
                    sub.setdefault(lg, {})[dotted] = val
                if not isinstance(keys, dict):
                    dropped.append(f"'league_overrides.{lg}' must be a group of settings")
            kept[k] = sub
            continue
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


def _nest(dotted: str, value) -> dict:
    out = value
    for part in reversed(dotted.split(".")):
        out = {part: out}
    return out


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
                # An unreadable file never takes the app down. Your last saved edit from the history
                # comes back if one reads (defaults would mean dry run: no TV control all night);
                # else defaults. Either way, say so.
                raw, restored = self._last_good_version()
                log.error("config: %s unreadable (%s); %s", user_path, e,
                          f"restored your last saved settings ({restored})" if restored else "using defaults")
                self.rejected = [f"unreadable: {e}" + (f"; restored {restored}" if restored else "")]
            loaded = without_retired(raw)
            # A broken setting never takes the app down, and never takes your other settings with it.
            self.overrides, dropped = prune(loaded, self.defaults)
            self.rejected += dropped
            for why in dropped:
                log.warning("config: ignoring a setting in %s: %s", user_path.name, why)
        self.data = _deep_merge(self.defaults, self.overrides)

    def _last_good_version(self) -> tuple[dict, str | None]:
        """The newest history version that reads as a group of settings, or ({}, None)."""
        try:
            versions = sorted(self.history_dir.glob("*.yaml"), reverse=True) if self.history_dir.exists() else []
        except OSError:
            return {}, None
        for v in versions:
            try:
                raw = yaml.safe_load(v.read_text())
            except (yaml.YAMLError, OSError):
                continue
            if isinstance(raw, dict):
                return raw, v.name
        return {}, None

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

    # Values the app measures itself and saves with record=False: Undo must not revert them (P-12).
    UNRECORDED = ("decider.switch_cost",)
    HISTORY_KEEP = 200   # recorded versions kept on disk (P-16); older ones are pruned at each save

    def rollback(self) -> bool:
        """Undo the last recorded change. Never wipes the saved settings on a thin history: with no
        version there is nothing to undo (the file is left alone), and values the app measured itself
        (UNRECORDED) survive. `self.rollback_note` says what happened, for the phone (P-12)."""
        versions = sorted(self.history_dir.glob("*.yaml")) if self.history_dir.exists() else []
        if not versions:
            self.rollback_note = "Nothing to undo: no earlier version is saved"
            return False
        keep = {k: v for k in self.UNRECORDED if (v := self._path(self.overrides, k)) is not None}
        if len(versions) < 2:
            # The only recorded version is the first edit ever (a save with older settings under it records
            # them first, see _save): undoing it returns to defaults plus what the app measured.
            prev: dict = {}
        else:
            prev = yaml.safe_load(versions[-2].read_text()) or {}
            if not isinstance(prev, dict):
                self.rollback_note = "Nothing to undo: the earlier version isn't readable"
                return False
        versions[-1].unlink()
        for dotted, v in keep.items():
            prev = _deep_merge(prev, _nest(dotted, v))
        self._save(prev, record=False)
        self.rollback_note = "Undid the last change"
        return True

    rollback_note = ""

    @staticmethod
    def _path(d: dict, dotted: str):
        node = d
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return node

    def _save(self, over: dict, record: bool = True) -> None:
        over = without_retired(over)
        self.user_path.parent.mkdir(parents=True, exist_ok=True)
        text = yaml.safe_dump(over, sort_keys=False)
        tmp = self.user_path.with_suffix(".tmp")
        tmp.write_text(text)
        tmp.replace(self.user_path)  # atomic: a crash mid-save can't corrupt the file
        if record:
            self.history_dir.mkdir(parents=True, exist_ok=True)
            if self.overrides and not any(self.history_dir.glob("*.yaml")):
                # Settings that predate the history (a fresh checkout, a pruned history) become its first
                # version, so the first Undo goes back to them and not to defaults (P-12).
                (self.history_dir / "v2-00000000000000000000.yaml").write_text(
                    yaml.safe_dump(without_retired(self.overrides), sort_keys=False))
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
            old = sorted(self.history_dir.glob("*.yaml"))
            for p in old[:-self.HISTORY_KEEP]:   # one file per save, forever (P-16)
                with contextlib.suppress(OSError):
                    p.unlink()
        self.overrides = over
        self.data = _deep_merge(self.defaults, over)
