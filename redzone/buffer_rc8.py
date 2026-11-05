"""[RC8] The time-shift buffer ON release, per league (Nick, Nov 5 2026 9:10 / 9:35 AM).

Three pure pieces, shared by the runtime, tonight's shadow log, the gate report and the flip tool:

- ``adaptive_buffer_s``: Nick's 9:35 AM formula. B = feed-lag p95 + sync error + tune/seek + margin, less any
  credited stream delay, never below the last value a LOCKED gate approved (the ratchet; with none approved, never
  below the fixed ``timing.behind_live_s.by_league`` value), clamped to [floor (10-15 s), cap (90 s)].
- ``gate_league``: the pre-registered RC8 gate (docs/plans/2026-11-05-rc8-buffer-gate.md) read per league as
  PASS / FAIL / PENDING. It never loosens a bar: one shadow night can only veto, and < 8 sim nights read PENDING.
- ``flip`` / ``rollback``: the per-league change Nick makes himself (scripts/rc8_flip.py). Only that league's
  ``league_overrides`` block is touched, never a global key.

The flag is ``timing.behind_live_s.rc8_on`` (false everywhere in the shipped defaults). It is separate from
``timing.behind_live_s.enabled`` (the hidden-stick hold already in RC6.2 for NBA/NFL), which RC8 leaves alone.
"""
from __future__ import annotations

import copy
import math
from typing import Any

from redzone import config

RC8_FLAG = "timing.behind_live_s.rc8_on"
RC8_FIXED = "timing.behind_live_s.rc8_fixed_s"
CAP = float(config.LIMITS["timing.behind_live_s.max_s"][1])              # Nick's cap (s), enforced on settings too
FLOOR_RANGE = config.LIMITS["timing.behind_live_s.adaptive.floor_s"]      # Nick: "a hard floor (start ~10-15 s)"
RATCHET_STEP = float(config.default("timing.behind_live_s.adaptive.ratchet_step", 15))   # gate v28 (s)
GATE_MIN_NIGHTS = 8              # the gate's floor per league per harness
HARNESSES = ("honest", "resync")
# Turned on with RC8 for the flipped league (planner, Nov 5): the editor runs with start_timed on; without it an
# NBA first pick joins mid-play (#318). Rollback restores the shipped value for that league.
COMPANIONS = {"decider.entry.start_timed": True}
SYNC_BAR = float(config.default("timing.behind_live_s.adaptive.sync_err_s", 2.0))   # gate item 5 (EXP-800, s)
PASS, FAIL, PENDING = "PASS", "FAIL", "PENDING"


def _num(name: str, v: Any) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number (got {v!r})") from None
    if math.isnan(x) or math.isinf(x) or x < 0:
        raise ValueError(f"{name} must be a finite number >= 0 (got {v!r})")
    return x


def adaptive_buffer_s(league: str, feed_lag_p95_s: float, sync_err_s: float, tune_seek_s: float,
                      margin_s: float, *, floor_s: float = 15.0, cap_s: float = CAP,
                      stream_delay_credit_s: float = 0.0, approved_min_s: float | None = None) -> float:
    """Seconds to hold this league's stream behind live. See the module doc for the formula."""
    fl, sy, tu, ma, cr = (_num(n, v) for n, v in (("feed_lag_p95_s", feed_lag_p95_s), ("sync_err_s", sync_err_s),
                                                  ("tune_seek_s", tune_seek_s), ("margin_s", margin_s),
                                                  ("stream_delay_credit_s", stream_delay_credit_s)))
    floor_s = _num("floor_s", floor_s)
    if not FLOOR_RANGE[0] <= floor_s <= FLOOR_RANGE[1]:
        raise ValueError(f"floor_s must be within {FLOOR_RANGE} s (got {floor_s}); the buffer never reaches 0")
    cap_s = min(_num("cap_s", cap_s), CAP)
    b = fl + sy + tu + ma - cr
    if approved_min_s is not None:
        b = max(b, _num("approved_min_s", approved_min_s))   # ratchet: never below what a locked gate approved
    return round(max(floor_s, min(cap_s, b)), 1)


def ship_b(cfg, league: str, sweep_b: float | None, mid_play_night: bool = False) -> tuple[float, str]:
    """Gate v28: RC8 B = max(sweep point, formula, floor), capped at 90; straight back to the cap after any
    mid-play night. Returns (B, why)."""
    b, parts = buffer_for(cfg, league)
    if mid_play_night:
        return parts["cap_s"], "a mid-play night: straight back up to the cap"
    if sweep_b is None:
        return b, "no sweep point yet: formula/floor only"
    out = round(min(parts["cap_s"], max(float(sweep_b), b)), 1)
    return out, f"max(sweep {float(sweep_b):g}, formula/ratchet/floor {b:g})"


def is_on(cfg, league: str | None) -> bool:
    """RC8 runs for this league (only ever via league_overrides; a global true is ignored, see validate_scope)."""
    if not league:
        return False
    over = (cfg.get("league_overrides") or {}).get(league) or {}
    return bool(over.get(RC8_FLAG, False))


def buffer_for(cfg, league: str) -> tuple[float, dict]:
    """(B, parts) for a league from timing.behind_live_s.adaptive (+ the league's fixed pin, if any)."""
    a = lambda k, d=None: config.for_league(cfg, f"timing.behind_live_s.adaptive.{k}", league, d)  # noqa: E731
    cap = min(float(cfg.get("timing.behind_live_s.max_s", CAP) or CAP), CAP)
    by = cfg.get("timing.behind_live_s.by_league") or {}
    fixed_live = float(by.get(league, cfg.get("timing.behind_live_s.default", 60)) or 0.0)
    lags = a("feed_lag_p95_s", {}) or {}
    approved = float((a("approved_min_s", {}) or {}).get(league, 0) or 0)
    # the league's live B today: only a league that already holds its stream (RC6.2 NBA/NFL) has one
    live_b = fixed_live if config.for_league(cfg, "timing.behind_live_s.enabled", league, False) else 0.0
    # gate v28: ratchet down one step of at most RATCHET_STEP per release, and only after a locked read held
    if approved > 0:
        ratchet, src = max(approved, live_b - RATCHET_STEP), "locked_gate"
    else:
        ratchet, src = live_b, ("live_hold" if live_b > 0 else "none")
    parts = {"league": league,
             "feed_lag_p95_s": float(lags.get(league, max(lags.values()) if lags else 75.0)),
             "sync_err_s": float(a("sync_err_s", 2.0)), "tune_seek_s": float(a("tune_seek_s", 6.0)),
             "margin_s": float(a("margin_s", 5.0)), "stream_delay_credit_s": float(a("stream_delay_credit_s", 0.0)),
             "floor_s": float(a("floor_s", 15.0)), "cap_s": cap,
             "ratchet_min_s": ratchet, "ratchet_source": src}
    parts["formula_s"] = round(parts["feed_lag_p95_s"] + parts["sync_err_s"] + parts["tune_seek_s"]
                               + parts["margin_s"] - parts["stream_delay_credit_s"], 1)
    b = adaptive_buffer_s(league, parts["feed_lag_p95_s"], parts["sync_err_s"], parts["tune_seek_s"],
                          parts["margin_s"], floor_s=parts["floor_s"], cap_s=cap,
                          stream_delay_credit_s=parts["stream_delay_credit_s"],
                          approved_min_s=parts["ratchet_min_s"])
    pin = float(config.for_league(cfg, RC8_FIXED, league, 0) or 0)
    if pin > 0:
        b = round(max(parts["floor_s"], min(cap, pin)), 1)
        parts["pinned_s"] = pin
    parts["buffer_s"] = b
    return b, parts


def validate_scope(cfg) -> list[str]:
    """Problems with where rc8_on is set: it must never be on globally (global keys leak into ungraded leagues)."""
    return [f"{RC8_FLAG} is on globally; RC8 is per league only (league_overrides.<league>)"] if cfg.get(RC8_FLAG) else []


# ----------------------------------------------------------------------------------------------- the gate
def gate_league(league: str, sim_runs: list[dict] | None, shadow_rows: list[dict] | None,
                sweep: dict | None = None, sync: dict | None = None, locked: dict | None = None) -> tuple[str, dict]:
    """The pre-registered RC8 gate for one league, items 1-5. Never loosened here.

    sim_runs:    lane 3's sweep runs [{b, arm, harness, seed, nights, entries_mid_play, clock_truth_live}]
    shadow_rows: tonight's shadow entries graded by scripts/buffer_shadow_grade.py
                 [{league, label: dead|live|ambiguous|pending, clock_truth: dead|live|ambiguous|None}]
    sweep:       lane 3's verdict for the league {verdict, chosen_b, needs, fit_nights, primary, guardrails}
    sync:        {p95_err_s, n} measured behind-live vs the buffer the brain assumed (shadow logs)
    locked:      {entries_mid_play, nights, decisions} the one locked read at the cut (None until then)
    """
    runs = [r for r in (sim_runs or [])]
    sh = [r for r in (shadow_rows or []) if r.get("league") in (None, league)]
    items: dict[str, dict] = {}

    # 1. Rule #1, hard: 0 mid-play on every run, both harnesses, both NBA graders; 0 on the shadow night.
    mid = sum(int(r.get("entries_mid_play") or 0) for r in runs)
    ct = sum(int(r.get("clock_truth_live") or 0) for r in runs)
    sh_live = sum(1 for r in sh if r.get("label") == "live" or r.get("clock_truth") == "live")
    # first picks (and after restarts): own line; None = the runs don't report it (never read as 0)
    first = (sum(int(r.get("first_pick_mid_play") or 0) for r in runs)
             if any("first_pick_mid_play" in r for r in runs) else None)
    sh_first = sum(1 for r in sh if r.get("first_pick") and (r.get("label") == "live" or r.get("clock_truth") == "live"))
    harn = {h: sum(1 for r in runs if r.get("harness") == h) for h in HARNESSES}
    if mid or ct or sh_live or first:
        s1 = FAIL
    elif not runs or not all(harn.values()) or not sh or first is None:   # a Rule #1 line never passes on absent data
        s1 = PENDING
    else:
        s1 = PASS
    items["1_rule1"] = {"state": s1, "sim_runs": len(runs), "runs_per_harness": harn, "sim_mid_play": mid,
                        "clock_truth_live": ct, "first_pick_mid_play": first, "shadow_first_pick_live": sh_first,
                        "shadow_entries": len(sh), "shadow_live": sh_live}

    # 2. Beats live on the sweep (lane 3's pre-registered bars; >= 8 fit nights).
    if not sweep:
        s2 = PENDING
    else:
        v = str(sweep.get("verdict", "PENDING")).upper()
        nights = int(sweep.get("fit_nights") or 0)
        s2 = PASS if v == "ON" and nights >= GATE_MIN_NIGHTS else (FAIL if v == "LIVE" else PENDING)
    items["2_beats_live"] = {"state": s2, **{k: (sweep or {}).get(k) for k in
                                             ("verdict", "chosen_b", "needs", "fit_nights", "primary")}}

    # 3. Tonight's shadow confirms: can only veto (GATE_MIN_NIGHTS); 0 graded entries = PENDING.
    graded = [r for r in sh if r.get("label") in ("dead", "live")]
    s3 = FAIL if sh_live else PENDING   # one night never PASSes a rate on its own
    items["3_shadow"] = {"state": s3, "graded_entries": len(graded), "live": sh_live,
                         "note": "one shadow night can only veto (gate item 3)"}

    # 4. Locked once, at the cut.
    if not locked:
        s4 = PENDING
    elif int(locked.get("entries_mid_play") or 0) > 0:
        s4 = FAIL
    elif int(locked.get("nights") or 0) < GATE_MIN_NIGHTS or int(locked.get("decisions") or 0) < 60:
        s4 = PENDING
    else:
        s4 = PASS
    items["4_locked"] = {"state": s4, **(locked or {})}

    # 5. Sync honesty: real behind-live within 2 s p95 of the assumed buffer.
    if not sync or sync.get("p95_err_s") is None:
        s5 = PENDING
    else:
        s5 = PASS if float(sync["p95_err_s"]) <= SYNC_BAR else FAIL
    items["5_sync"] = {"state": s5, **(sync or {})}

    states = [i["state"] for i in items.values()]
    overall = FAIL if FAIL in states else (PASS if all(s == PASS for s in states) else PENDING)
    return overall, {"league": league, "items": items}


# ----------------------------------------------------------------------------------------------- flip
def flip(cfg_data: dict, league: str, seconds: float | None = None) -> dict:
    """A new league_overrides with RC8 ON for this league only (input not mutated)."""
    if not league or not isinstance(league, str):
        raise ValueError("league required")
    over = copy.deepcopy((cfg_data or {}).get("league_overrides") or {})
    blk = dict(over.get(league) or {})
    blk[RC8_FLAG] = True
    blk.update(COMPANIONS)
    if seconds is not None:
        s = _num("seconds", seconds)
        if not 0 < s <= CAP:
            raise ValueError(f"seconds must be in (0, {CAP}] (got {seconds})")
        blk[RC8_FIXED] = s
    else:
        blk[RC8_FIXED] = 0   # explicit: Config.update deep-merges, so a stale pin would otherwise survive
    over[league] = blk
    return over


def _shipped(league: str, key: str):
    """The shipped default of a key for a league (its league_overrides block, else the global default)."""
    over = (config.default("league_overrides", {}) or {}).get(league) or {}
    return over[key] if key in over else config.default(key)


def rollback(cfg_data: dict, league: str) -> dict:
    """A new league_overrides with RC8 back OFF for this league only: the league's other keys are kept.
    Written as explicit false / 0 (not deleted), because Config.update deep-merges league_overrides: a deleted
    key would leave the flip ON once applied."""
    if not league or not isinstance(league, str):
        raise ValueError("league required")
    over = copy.deepcopy((cfg_data or {}).get("league_overrides") or {})
    blk = dict(over.get(league) or {})
    blk[RC8_FLAG] = False
    blk[RC8_FIXED] = 0
    for k in COMPANIONS:
        blk[k] = _shipped(league, k)
    over[league] = blk
    return over
