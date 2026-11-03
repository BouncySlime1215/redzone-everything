"""Feature flags / kill switches: one per subsystem.

Owner: INTEGRATION (the list); each subsystem owner reads its own flag. Flags live in config under
`flags.<name>` (bool), so the phone's settings page, config rollback and the auto-tuner all reach
them, and backtests A/B them by overriding the key. A flag that is off puts the subsystem in its
degraded mode (DEGRADED below), never in an error.
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
