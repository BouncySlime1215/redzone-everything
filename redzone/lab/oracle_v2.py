"""NBA hindsight oracle v2 (EXP-1722 PASS, Nov 6 2026; prereg docs/prereg/exp-1722.md, read docs/prereg/v3-reads/).
Information only beside v1 (redzone/lab/oracle.py stays the gate until Nick says otherwise). Reads oracle.py, never changes it.

big_v2 (NBA only): pre-play WP in [0.05, 0.95] AND (|dWP| >= P99_NBA OR (scoring or v1 big) AND |dWP| >= 0.02 OR v1 big).
Reward per step = sum max(|dWP|, 0.02) of big_v2 plays + 0.05 per crunch play + 100 per your-team crunch play
- 0.002 per dead step; 0.02 per switch. WP = ESPN summary winprobability (homeWinPercentage) by play id, the same
source as crew G's wp_home. personal-use, not for commercial release."""
from __future__ import annotations

import numpy as np

from redzone.lab import oracle as O, sportsgrade

P99_NBA = 0.127
OBJECTIVE_V2 = (1.0, 0.05, 100.0, 0.002, 0.02)   # big, crunch, your-team crunch, dead step, switch
LIVE_LO, LIVE_HI, MIN_D = 0.05, 0.95, 0.02


def wp_by_play(summary: dict) -> dict[str, float]:
    """ESPN winprobability rows -> {play id: home WP}."""
    return {str(w.get("playId")): float(w["homeWinPercentage"]) for w in summary.get("winprobability") or []
            if w.get("homeWinPercentage") is not None}


def is_big_v2(league: str, p: dict, prev: dict | None, prev_wp: float | None, cur_wp: float | None) -> tuple[bool, float]:
    """(big_v2?, its weight) for one play. Leagues other than NBA keep v1 (weight 1 when big)."""
    v1 = sportsgrade.is_big(league, p, prev)
    if league != "nba":
        return v1, 1.0 if v1 else 0.0
    d = abs(cur_wp - prev_wp) if cur_wp is not None and prev_wp is not None else 0.0
    live = prev_wp is not None and LIVE_LO <= prev_wp <= LIVE_HI
    scoring = bool(p.get("scoringPlay"))
    b = live and (d >= P99_NBA or ((scoring or v1) and d >= MIN_D) or v1)
    return b, (max(d, MIN_D) if b else 0.0)


def tables(n: O.Night, steps: np.ndarray, wp: dict[str, dict[str, float]], lag: float) -> dict:
    """v1 tables with the big row replaced by big_v2 weights. wp: {gid: wp_by_play(summary)}; lag as the harness used."""
    tab = O.tables(n, steps)
    big = np.zeros_like(tab["big"]); t0 = float(steps[0]); K = len(steps)
    for g, r in enumerate(n.replays):
        prev, prev_wp, plen, wg = None, None, sportsgrade.PLAY_LEN[r.league], wp.get(str(r.gid), {})
        for w, p in zip(r.wall, r.plays):
            cur = wg.get(str(p.get("id")))
            b, wt = is_big_v2(r.league, p, prev, prev_wp, cur)
            k = int((w + lag - plen - t0) // O.STEP_S)
            if b and 0 <= k < K:
                big[g, k] += wt
            prev, prev_wp = p, (cur if cur is not None else prev_wp)
    return dict(tab, big=big)


def register() -> str:
    """Adds the v2 objective to O.OBJECTIVES at runtime (the file is untouched); returns its key."""
    O.OBJECTIVES.setdefault("v2", OBJECTIVE_V2)
    return "v2"
