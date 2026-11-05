"""[WV loop, Nick Nov 5 ~5 PM] Mistake ledger: after a graded night, compare every step the app showed with the
windowed-hindsight oracle (redzone/lab/oracle_window.py) and write one row per mistake episode: what we showed,
what we should have shown, value lost (the oracle's own reward units) and WHY, by cause.

Causes (checked in this order; the first that explains most of the loss wins):
  bad_label         the loss exists under the frozen reward but not under the alternative reward (EXP-1721 v2): the
                    label, not the brain, disagrees
  switch_cost       most of the loss is black picture from a switch the oracle did not make
  stay_leave_timing app and oracle went to the same game, at a different time (left / joined early or late)
  break_estimate    the app sat on a game in a real break while the oracle's game was live
  feed_lag          the oracle's gain came from plays the app could not know yet at its measured feed lag
  sync_error        the app's switch landed with a sync error above the threshold (needs sync truth rows)
  wrong_game_value  everything else: the app valued the wrong game

Locked nights never enter a ledger (Nick's rule): `assert_not_locked` is called by every writer.
Reads only tables already built for grading; changes no grader. personal-use, not for commercial release.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np

MISTAKE_CAUSES = ("bad_label", "switch_cost", "stay_leave_timing", "break_estimate", "feed_lag", "sync_error",
          "wrong_game_value")


def assert_not_locked(night: str, locked: set[str]) -> None:
    if str(night) in {str(x) for x in locked}:
        raise ValueError(f"night {night} is LOCKED: it never enters the mistake ledger")


def on_screen(K: int, t0: float, step_s: float, switches, gid_index: dict, blk: int) -> np.ndarray:
    """Game index on screen at each step (-1 = black / nothing), the same timeline schedule_value uses."""
    on = np.full(K, -1)
    sw = sorted(switches, key=lambda s: s.t)
    for j, s in enumerate(sw):
        k0 = int(round((s.t - t0) / step_s)) + blk
        k1 = int(round((sw[j + 1].t - t0) / step_s)) if j + 1 < len(sw) else K
        if s.to in gid_index:
            on[max(0, k0):max(0, min(K, k1))] = gid_index[s.to]
    return on


def _val(R: np.ndarray, on: np.ndarray, k: int) -> float:
    return float(R[on[k], k]) if on[k] >= 0 else 0.0


@dataclass
class Mistake:
    night: str
    league: str
    k0: int
    k1: int
    t0: float
    t1: float
    showed: str | None
    should: str | None
    value_lost: float
    cause: str
    detail: dict


def ledger(night: str, league: str, R: np.ndarray, tab: dict, gids: list[str], app_on: np.ndarray,
           oracle_on: np.ndarray, t0: float, step_s: float, *, locked: set[str] = frozenset(),
           R_alt: np.ndarray | None = None, app_switch_steps: set[int] = frozenset(),
           oracle_switch_steps: set[int] = frozenset(), blk: int = 1, window_known: int | None = None,
           window_actual: int | None = None, sync_err_at: dict[int, float] | None = None,
           sync_thresh_s: float = 2.0) -> list[Mistake]:
    """One row per maximal run of steps where the app's screen differs from the oracle's and the oracle gained."""
    assert_not_locked(night, locked)
    K = len(app_on)
    diff = np.array([_val(R, oracle_on, k) - _val(R, app_on, k) for k in range(K)])
    out: list[Mistake] = []
    k = 0
    while k < K:
        if app_on[k] == oracle_on[k]:
            k += 1
            continue
        k1 = k
        while k1 < K and app_on[k1] != oracle_on[k1]:
            k1 += 1
        lost = float(diff[k:k1].sum())
        if lost > 1e-9:
            out.append(_explain(night, league, R, tab, gids, app_on, oracle_on, diff, k, k1, lost, t0, step_s, R_alt,
                                app_switch_steps, oracle_switch_steps, blk, window_known, window_actual,
                                sync_err_at or {}, sync_thresh_s))
        k = k1
    return out


def _mode(a: np.ndarray) -> int:
    a = a[a >= 0]
    return int(np.bincount(a).argmax()) if len(a) else -1


def _explain(night, league, R, tab, gids, app_on, oracle_on, diff, k0, k1, lost, t0, step_s, R_alt, a_sw, o_sw, blk,
             w_known, w_actual, sync_err_at, sync_thresh) -> Mistake:
    a, o = _mode(app_on[k0:k1]), _mode(oracle_on[k0:k1])
    det: dict = {"steps": k1 - k0}
    cause = "wrong_game_value"
    black = float(diff[k0:k1][app_on[k0:k1] < 0].sum())
    if R_alt is not None:
        lost_alt = sum((float(R_alt[oracle_on[k], k]) if oracle_on[k] >= 0 else 0.0)
                       - (float(R_alt[app_on[k], k]) if app_on[k] >= 0 else 0.0) for k in range(k0, k1))
        det["lost_alt"] = round(lost_alt, 4)
    near_app_sw = any(k0 - blk - 1 <= s <= k1 for s in a_sw)
    near_or_sw = any(k0 - blk - 1 <= s <= k1 for s in o_sw)
    before, after = (app_on[k0 - 1] if k0 > 0 else -2), (app_on[k1] if k1 < len(app_on) else -2)
    if R_alt is not None and det["lost_alt"] <= 0:
        cause = "bad_label"
    elif black >= 0.5 * lost and near_app_sw and not near_or_sw:
        cause = "switch_cost"
    elif o >= 0 and (o == before or o == after) and (near_app_sw or near_or_sw):
        cause = "stay_leave_timing"
    elif a >= 0 and tab.get("dead") is not None and tab["dead"][a, k0:k1].mean() >= 0.5 \
            and o >= 0 and tab["dead"][o, k0:k1].mean() < 0.5:
        cause = "break_estimate"
    elif w_known is not None and w_actual is not None and w_actual < w_known and \
            diff[k0:min(k1, k0 + w_known)][max(0, w_actual):].sum() >= 0.5 * lost:
        cause = "feed_lag"
    elif any(abs(sync_err_at.get(s, 0.0)) > sync_thresh for s in a_sw if k0 - blk - 1 <= s <= k1):
        cause = "sync_error"
    det["black_loss"] = round(black, 4)
    return Mistake(str(night), league, k0, k1, t0 + k0 * step_s, t0 + k1 * step_s,
                   gids[a] if a >= 0 else None, gids[o] if o >= 0 else None, round(lost, 4), cause, det)


def summarize(rows: list[Mistake]) -> dict:
    """Value lost per cause, largest first, and the share of all value lost."""
    tot = sum(r.value_lost for r in rows) or 1.0
    by: dict[str, dict] = {}
    for r in rows:
        b = by.setdefault(r.cause, {"episodes": 0, "value_lost": 0.0})
        b["episodes"] += 1
        b["value_lost"] += r.value_lost
    for b in by.values():
        b["share"] = round(b["value_lost"] / tot, 3)
        b["value_lost"] = round(b["value_lost"], 4)
    return dict(sorted(by.items(), key=lambda kv: -kv[1]["value_lost"]))


def sample_weights(rows: list[Mistake], K: int, floor: float = 1.0) -> np.ndarray:
    """Per-step training weight: 1 + value lost of the episode covering the step (hard-case mining)."""
    w = np.full(K, floor)
    for r in rows:
        w[r.k0:r.k1] += r.value_lost / max(1, r.k1 - r.k0) * (r.k1 - r.k0) ** 0.5
    return w


def to_jsonl(rows: list[Mistake]) -> str:
    import json
    return "".join(json.dumps(asdict(r)) + "\n" for r in rows)


__all__ = ["MISTAKE_CAUSES", "Mistake", "assert_not_locked", "ledger", "on_screen", "sample_weights", "summarize", "to_jsonl",
           "math"]
