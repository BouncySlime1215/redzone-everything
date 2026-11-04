"""WatchValue contract (HEAT 2.0, Nick Nov 4 3:30 PM): ONE learned score per game, recomputed every play.

Owner: NOGGIN TRAINING (lane 1). Implementation: redzone/noggin/watch.py (models in
redzone/noggin/models/watch_<league>.v1.npz). Called by: the Scorer (redzone/scoring/engine.py), once per
scored live game, behind ``scoring.watch_value`` (default off). Read by: the decider (lane 2, non-crunch
only: be on the highest WV game, switch only at dead balls, pre-tune the game whose WV is rising) and the
phone (GET /api/v2/watch via Scored.watch_value / watch_why). The old scorers (engine, drive heat, brain,
priority, reasoner) are FEATURE PROVIDERS for WV (see FEATURES), never competing deciders.
Crunch = stay sits ABOVE WV: no WV value may take the screen off a crunch window.

Units: watch_value 0..100 on a calibrated scale: 50 = the league's median live moment on train nights,
90 = its 95th percentile (top 5% = must-see), 100 = the top. Self-taught: the label is what happened next
(the realized win-probability swing over the next ``HORIZON_S``, plus scoring chances), never Nick's taps.
Stakes (your teams, bets, fantasy) are PREFERENCE multipliers applied after the model, never labels.
Cost: < 5 ms per game, numpy only, no network.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

HORIZON_S = {"nfl": 180.0, "nba": 180.0, "nhl": 180.0, "mlb": 300.0}

# The model's inputs: the numbers the existing scorers already produce (redzone/reasoner/brief.py facts:
# game clock and score, the brain's win-probability track, story counters, the state flags), per league.
FEATURES_COMMON = ("period", "clock_s", "frac_left", "is_ot", "abs_diff", "wp_lev", "wp_delta", "wp_vol_8",
                   "wp_vol_w", "lead_changes", "run_max", "pts_w", "in_break", "s_since_break", "ev_since_break",
                   "crunch", "garbage", "late", "recent_scores", "recent_big", "plays_since_big", "events")
FEATURES_EXTRA = {
    "nfl": ("possession_trailing", "yardline_100", "down", "ydstogo", "red_zone", "goal_to_go", "two_min",
            "drive_plays", "def_timeouts"),
    "nba": ("trail_timeouts",),
    "nhl": ("pp", "pp_5v3", "empty_net"),
    "mlb": ("outs", "runners"),
}
# Feature providers that WV reads (the "city map" edges): who computes each input today.
PROVIDERS = {"brief.facts": "redzone/reasoner/brief.py", "brain.track": "redzone/scoring/brain.py (wp_*)",
             "engine.state": "redzone/scoring/engine.py (crunch, garbage, late)"}
TERMS = ("p_big", "impact", "leverage", "stakes_mult", "story", "garbage", "crunch_p")
CRUNCH_STATES = ("entering", "in", "leaving")
# What every scored game carries (lane 5 #161 + lane 2 read these exact names on redzone.scoring.engine.Scored)
SCORED_FIELDS = {"watch_value": "float | None, 0..100", "watch_why": "str", "crunch_state": "str | None, CRUNCH_STATES",
                 "watch_terms": "dict, TERMS", "watch_rising": "float | None, points per minute"}


def features_for(league_base: str) -> list[str]:
    return list(FEATURES_COMMON) + list(FEATURES_EXTRA.get(league_base, ()))


@dataclass(frozen=True)
class WatchReading:
    game_id: str
    watch_value: float | None             # 0..100 calibrated; None = no model / flag off (use today's score)
    watch_why: str = ""                   # one line for the UI, from the largest contributions
    watch_terms: dict = field(default_factory=dict)   # TERMS: p_big 0..1, impact 0..1, leverage 0..1,
    #                                                   stakes_mult >= 1, story 0..1, garbage 0..1, crunch_p 0..1
    watch_rising: float | None = None     # d(watch_value)/dt in points per minute over the last 2 readings
    model_version: str = ""
    # [Nick 4:15 PM] crunch as a LEARNED state with entry and exit (hysteresis inside, never flickers); None =
    # no crunch head. It is a strong prior for the decider, not a wall: crunch vs crunch is decided by value.
    # One of CRUNCH_STATES: "entering" (this reading turned it on), "in", "leaving" (this reading turned it off),
    # or None (not in crunch, or no crunch head).
    crunch_state: str | None = None

    @property
    def available(self) -> bool:
        return self.watch_value is not None


@runtime_checkable
class WatchModel(Protocol):
    def read(self, game_id: str, league: str, facts: dict, now: float, stakes_mult: float = 1.0) -> WatchReading: ...
    def forget(self, game_id: str) -> None: ...
