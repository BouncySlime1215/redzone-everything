"""HEAT 2.0: the learned watch value (contract: redzone/contracts/watch.py).

One small tree model per league (scripts/train_watch_value.py), walked with the brain's plain-numpy Trees:
  wv  regression: the realized "want to see" over the next ~3 min (win-probability swing at stake +
      scoring chances, 0..1), learned from what happened next on archived nights
  pb  binary: P(a big swing play, |dWP| >= BIG_SWING in one play) within the horizon
  cr  binary: P(the game is in CRUNCH) as a learned state (Nick 4:15 PM): late AND the rest of the game still
      swings the win probability, so the model learns the EXIT too (blowout, decided, clock gone); the live
      state uses hysteresis (on at CRUNCH_ON, off at CRUNCH_OFF) so it never flickers
The raw wv maps to the calibrated 0..100 scale through the league's train-night quantiles
(50 = median live moment, 90 = p95). Stakes multiply AFTER the model (preference, never a label).
A league without a model file gives watch_value None: the caller keeps today's score.
"""
from __future__ import annotations

import logging
import math
from pathlib import Path

import numpy as np

from redzone.contracts.watch import WatchReading

log = logging.getLogger(__name__)

WV_MODEL_DIR = Path(__file__).resolve().parent / "models"
BIG_SWING = 0.08          # one play moving the win probability this much is a "big play" for WV
CRUNCH_ON, CRUNCH_OFF = 0.6, 0.35
SCALE_PCT = (0.0, 50.0, 95.0, 100.0)    # train-night percentile ...
SCALE_VAL = (0.0, 50.0, 90.0, 100.0)    # ... -> watch value

WHY = {"abs_diff": "close game", "wp_lev": "close game", "frac_left": "time left", "clock_s": "late clock",
       "period": "late in the game", "is_ot": "overtime", "lead_changes": "lead changes", "run_max": "a big run",
       "wp_delta": "momentum swing", "wp_vol_8": "back-and-forth", "wp_vol_w": "back-and-forth",
       "recent_scores": "scoring flurry", "recent_big": "big plays lately", "red_zone": "red zone",
       "goal_to_go": "goal to go", "down": "big down", "ydstogo": "big down", "two_min": "two-minute drill",
       "possession_trailing": "trailing team has the ball", "yardline_100": "field position", "pp": "power play",
       "pp_5v3": "5-on-3", "empty_net": "empty net", "runners": "runners on", "outs": "outs", "crunch": "crunch time",
       "late": "late in the game", "trail_timeouts": "timeouts left", "events": "action"}


def calibrate(raw: float, qs: list[float], pcts: list[float]) -> float:
    """raw model output -> 0..100 on the league's train-night scale."""
    p = float(np.interp(raw, qs, pcts))
    return float(np.interp(p, SCALE_PCT, SCALE_VAL))


class WatchValue:
    def __init__(self, model_dir: Path | None = None, version: str = "v1"):
        self.model_dir = model_dir or WV_MODEL_DIR
        self.version = version
        self._m: dict[str, tuple | None] = {}
        self._broken: set[str] = set()
        self._hist: dict[str, tuple[float, float]] = {}
        self._crunch: dict[str, bool] = {}

    def model(self, league: str):
        if league not in self._m:
            from redzone.models import League
            from redzone.scoring.brain import load
            m = None
            base = League(league).base.value if league in {x.value for x in League} else league
            for name in dict.fromkeys((league, base)):     # college reads its own file first, then the pro one
                p = self.model_dir / f"watch_{name}.{self.version}.npz"
                if p.exists():
                    try:
                        trees, info = load(p)
                        m = (trees["wv"], trees.get("pb"), trees.get("cr"), info)
                    except Exception:  # noqa: BLE001 - a bad file means "no WV", never a crash
                        log.exception("watch: could not load %s", p)
                    break
            self._m[league] = m
        return self._m[league]

    def available(self, league: str) -> bool:
        return league not in self._broken and self.model(league) is not None

    def read(self, game_id: str, league: str, facts: dict, now: float, stakes_mult: float = 1.0) -> WatchReading:
        m = None if league in self._broken else self.model(league)
        if m is None:
            return WatchReading(game_id, None)
        wv, pb, cr, info = m
        try:
            x = np.array([_num(facts.get(f)) for f in wv.features], np.float64)
            raw, contrib = wv.predict(x, explain=True)
            p_big = pb.predict(np.array([_num(facts.get(f)) for f in pb.features]))[0] if pb is not None else None
            p_cr = cr.predict(np.array([_num(facts.get(f)) for f in cr.features]))[0] if cr is not None else None
        except Exception:  # noqa: BLE001
            log.exception("watch: model failed for %s; off for this league from now on", league)
            self._broken.add(league)
            return WatchReading(game_id, None)
        value = calibrate(raw, info["cal_q"], info["cal_pct"])
        value = max(0.0, min(100.0, value * max(1.0, float(stakes_mult or 1.0))))
        terms = {"p_big": None if p_big is None else round(float(p_big), 3),
                 "impact": round(max(0.0, min(1.0, raw)), 3),
                 "leverage": round(_clip01(facts.get("wp_lev")), 3),
                 "stakes_mult": round(max(1.0, float(stakes_mult or 1.0)), 2),
                 "story": round(_clip01(_num(facts.get("lead_changes"), 0.0) / 8.0 + _num(facts.get("run_max"), 0.0) / 20.0), 3),
                 "garbage": round(_clip01(facts.get("garbage")), 3),
                 "crunch_p": None if p_cr is None else round(float(p_cr), 3)}
        was = self._crunch.get(game_id, False)
        on = None if p_cr is None else (p_cr > CRUNCH_OFF if was else p_cr >= CRUNCH_ON)
        state = None
        if on is not None:
            self._crunch[game_id] = on
            state = ("in" if was else "entering") if on else ("leaving" if was else None)
        prev = self._hist.get(game_id)
        rising = None
        if prev is not None and now > prev[0]:
            rising = round((value - prev[1]) / max(1e-6, (now - prev[0]) / 60.0), 2)
        self._hist[game_id] = (now, value)
        return WatchReading(game_id, round(value, 1), why(wv.features, contrib, terms, value), terms, rising,
                            f"watch_{league}.{self.version}", state)

    def forget(self, game_id: str) -> None:
        self._hist.pop(game_id, None)
        self._crunch.pop(game_id, None)


def why(features: list[str], contrib, terms: dict, value: float) -> str:
    if terms.get("garbage", 0) >= 0.5:
        return "decided game"
    if contrib is None:
        return ""
    order = np.argsort(-np.asarray(contrib))
    words = []
    for i in order[:4]:
        if contrib[i] <= 0:
            break
        w = WHY.get(features[i])
        if w and w not in words:
            words.append(w)
        if len(words) == 2:
            break
    if terms.get("stakes_mult", 1.0) > 1.0:
        words.append("stakes")
    head = "must-see" if value >= 90 else "worth a look" if value >= 65 else "quiet" if value < 35 else "live"
    return head + (": " + ", ".join(words) if words else "")


def _num(v, default: float = float("nan")) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def _clip01(v) -> float:
    f = _num(v, 0.0)
    return max(0.0, min(1.0, f))
