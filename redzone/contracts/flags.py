"""Feature flag names and degraded modes: one per subsystem.

Owner: INTEGRATION (the list).

STATUS (audit R3, 2026-11-08; F-01, 2026-11-12): NOT WIRED, and NOT A CONFIG KEY.  `enabled()` below is read by
no production code; only tests/test_contracts.py calls it.  The 15-key `flags:` block that used to sit in
default_config.yaml switched nothing, so F-01 removed it (a `flags.<name>` in data/config.yaml is now rejected as an
unknown setting, and `enabled()` on the real Config always returns its default).  Each subsystem is gated by its OWN
key: brain.enabled, reasoner.enabled, reel.enabled / reel.play, replay.*, tuners.*, noggin.*, hardening.*,
two_stick.*, decider.* (see default_config.yaml).  Use those to turn a part off.  Wiring `enabled()` into each guard
is a larger change that is not scheduled; until it is, DEGRADED documents what each part does when it is off or
missing, and the Flag names are kept for that table.  To wire one: add its key to default_config.yaml in the same PR
that reads it (tests/test_config_literal_keys.py fails a literal read whose key the YAML lacks).

Why: first added in 418ffc56 (2026-11-03) as "Architecture contract v1: interfaces, stubs, contract tests, ownership, status board"; no separate decision is recorded for this module.
"""
from __future__ import annotations

from enum import Enum


class Flag(str, Enum):
    BRAIN_V3 = "brain_v3"            # MomentModel v3 (off -> legacy brain / rules)
    HYPE_V2 = "hype_v2"
    BREAK_MODEL = "break_model"      # learned break lengths (off -> breaks.LEN medians)
    REASONER = "reasoner"            # LLM advisory views (off -> no opinions)
    WATCHDOG_MODEL = "watchdog_model"  # LLM watchdog (off -> rules only)
    REEL = "reel"                    # catch-up reel candidates
    HIGHLIGHT_JUMP = "highlight_jump"
    DRIVE_PRIORITY = "drive_priority"
    TRIP_BUDGET = "trip_budget"
    NHL_RULES = "nhl_rules"
    TV_FLIP = "tv_flip"              # two-stick input flips (off -> one stick)
    PRELOAD = "preload"
    SYNC_PREDICTOR = "sync_predictor"  # learned per-channel delay (off -> taps/defaults)
    AUTO_TUNE = "auto_tune"
    FASTCAST = "fastcast"


# What each subsystem does when its flag is off or its part is missing (tested in the night test).
DEGRADED: dict[str, str] = {
    "brain_v3": "Scorer uses the rule-based moment score; MomentPrediction.available is False.",
    "hype_v2": "Hype term is 0; rule moment score only.",
    "break_model": "BreakForecast lengths come from breaks.LEN medians; q10/q90 = 0.6x/1.6x median.",
    "reasoner": "No ReasonerView: no lock-ins, no value bonus.",
    "watchdog_model": "agent/policy.py rules + guard only; unknown errors -> notify.",
    "reel": "No reel candidates; break visits and catch-up replays only.",
    "highlight_jump": "Reel only plays as a break filler, never as a jump.",
    "drive_priority": "NFL drives scored like any other moment.",
    "trip_budget": "Break visits use the legacy fit check.",
    "nhl_rules": "NHL scored by the generic rules.",
    "tv_flip": "One-stick mode: every switch is a TUNE or IN_APP path.",
    "preload": "Hidden stick is not cued ahead; flips only when it already shows the target.",
    "sync_predictor": "SyncEstimate from taps/boundaries, else config delay defaults.",
    "auto_tune": "Config stays as set; recommendations only.",
    "fastcast": "Polling only.",
    # parts that can go missing at runtime (not flags)
    "one_stick": "Stick dropped: two-stick mode off, TUNE paths only, recovers when it returns.",
    "feed_down": "Games with stale feeds are candidates for nothing; the guard holds the current game.",
    "away_from_home": "No device control; HealthReport.away_from_home, phone says 'away from home'.",
    "tv_unreachable": "Input state unknown: no flips until readback, tunes on the visible stick only.",
}


def enabled(cfg, flag: Flag | str, default: bool = True) -> bool:
    name = flag.value if isinstance(flag, Flag) else flag
    v = cfg.get(f"flags.{name}", default) if cfg is not None else default
    return bool(default if v is None else v)
