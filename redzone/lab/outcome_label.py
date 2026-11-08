"""[EXP-302] The lock label from REAL outcomes, not from our own clock/crunch rule (Nick, Nov 5 1:50 AM ET).

The old lock-in label (reasoner_data `y`: a scorecard big or crunch play in the next H s) is DEFINED by period,
clock and margin, the same numbers the head and the Teacher see, so a model only re-learns our rule (CBB:
head AUC 0.967 vs clock-only 0.960). This label asks what actually happened in the next `HORIZON[league]`
seconds of wall clock after the moment, and reads nothing the model sees at that moment except the score
it starts from:

  flip         the lead changed hands, or a team that led was caught (tie). A tie that turns into a lead
               does not count (0-0 -> 2-0 is not a story).
  wp_swing     |win probability at the window's end - now| >= WP_SWING, with a fixed label-only win
               probability: Phi(margin / (WP_SPREAD[league] * sqrt(game seconds left + 1))) (a Brownian
               score model; never the brain's model, which the head sees as a feature). A game that
               ends inside the window ends at 1 / 0 / 0.5. MLB has no game clock: no wp_swing.
  close_finish the game ended inside the window with a final margin of one score or less (FINISH_MARGIN).
               Reported only, NOT in y_out: on its own it is our rule again (clock-only AUC 0.98-0.99 on
               NBA and NFL valid nights), because near the end the margin already says it.

  y_out = flip or wp_swing

`context_auc`: how well period + clock + margin alone (binned, rates learned on one set of nights) predict a
label on another. A label at or above CONTEXT_AUC_MAX is our own rule again. CI runs the integrator's
scripts/label_leak_check.py; read either on train/valid nights only, never on locked ones.
"""
from __future__ import annotations

import math
from collections import defaultdict

from redzone.lab.leverage import _left_s
from redzone.models import PERIOD_S, REGULATION, League
from redzone.sources import espn

# Wall-clock seconds after the moment that the outcome may happen in (pre-registered, EXP-302).
HORIZON = {League.NBA: 180.0, League.CBB: 180.0, League.WCBB: 180.0,   # WCBB row: grader v47, F16 (was a KeyError)
           League.NHL: 300.0, League.NFL: 300.0, League.CFB: 300.0, League.MLB: 600.0}
# Spread of the final margin per second of game clock: the final-margin SD over a regulation game.
# MLB has no game clock, so it has no row here: it gets flip labels only (win_prob / game_left return None).
WP_SPREAD = {League.NBA: 12.0 / math.sqrt(2880), League.CBB: 11.0 / math.sqrt(2400),
             League.WCBB: 11.0 / math.sqrt(2400), League.NHL: 2.3 / math.sqrt(3600),
             League.NFL: 14.0 / math.sqrt(3600), League.CFB: 17.0 / math.sqrt(3600)}
FINISH_MARGIN = {League.NBA: 3, League.CBB: 3, League.WCBB: 3, League.NHL: 1, League.NFL: 8, League.CFB: 8,
                 League.MLB: 1}
WP_SWING = 0.20
CONTEXT_AUC_MAX = 0.95
MIN_BLIND = 600          # blind moments per league before a lock grade can PASS or FAIL (Nick: >= 600)


def game_left(league: League, p: dict) -> float | None:
    """Seconds of game clock left at this play (regulation remainder; in overtime, the period's clock)."""
    per = espn._int(espn._get(p, "period", "number"))
    if per is None or league not in WP_SPREAD:
        return None
    left = _left_s(league, p)
    if left is None:
        return None
    reg = REGULATION[league]
    return left + max(0, reg - per) * PERIOD_S[league] if per <= reg else left


def win_prob(league: League, margin_home: float, left: float | None) -> float | None:
    if left is None or league not in WP_SPREAD:
        return None
    if left <= 0:
        return 1.0 if margin_home > 0 else 0.0 if margin_home < 0 else 0.5
    z = margin_home / (WP_SPREAD[league] * math.sqrt(left + 1.0))
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _leader(d: int) -> int:
    return (d > 0) - (d < 0)


def label_game(league: League, plays: list[dict], wall: list[float], horizon: float | None = None) -> list[dict]:
    """Per play: {y_out, flip, wp_swing, close_finish}. Plays without a score carry the last one forward."""
    h = HORIZON[league] if horizon is None else horizon
    n = len(plays)
    diff, wp = [], []
    last = 0
    for p in plays:
        hs, as_ = espn._int(p.get("homeScore")), espn._int(p.get("awayScore"))
        last = hs - as_ if hs is not None and as_ is not None else last
        diff.append(last)
        wp.append(win_prob(league, last, game_left(league, p)))
    final = diff[-1] if n else 0
    # The archive's last play ends the game; a cut-off feed (clock still running) must not read as a finish.
    over = n > 0 and (league not in WP_SPREAD or game_left(league, plays[-1]) in (None, 0.0))
    end_wp = 1.0 if final > 0 else 0.0 if final < 0 else 0.5
    out = []
    j = 0
    for i in range(n):
        j = max(j, i)
        while j + 1 < n and wall[j + 1] <= wall[i] + h:
            j += 1
        lead = _leader(diff[i])
        flip = lead != 0 and any(_leader(diff[k]) != lead for k in range(i + 1, j + 1))
        ends = over and j == n - 1 and wall[-1] <= wall[i] + h
        close = bool(ends and abs(final) <= FINISH_MARGIN[league])
        later = end_wp if ends else wp[j]
        swing = wp[i] is not None and later is not None and abs(later - wp[i]) >= WP_SWING
        out.append({"y_out": int(flip or swing), "flip": int(flip), "wp_swing": int(swing),
                    "close_finish": int(close)})
    return out


def _bin(period, clock_s, abs_diff) -> tuple:
    c = -1 if clock_s is None or (isinstance(clock_s, float) and math.isnan(clock_s)) else int(clock_s // 60)
    d = -1 if abs_diff is None or (isinstance(abs_diff, float) and math.isnan(abs_diff)) else min(int(abs_diff), 25)
    return (period if period is not None else -1, c, d)


def auc(y: list[int], p: list[float]) -> float:
    """Mann-Whitney AUC with ties counted half (stdlib)."""
    pairs = sorted(zip(p, y))
    pos = sum(y)
    neg = len(y) - pos
    if not pos or not neg:
        return float("nan")
    rank_sum, k = 0.0, 0
    while k < len(pairs):
        m = k
        while m + 1 < len(pairs) and pairs[m + 1][0] == pairs[k][0]:
            m += 1
        avg = (k + m) / 2 + 1
        rank_sum += avg * sum(yy for _, yy in pairs[k:m + 1])
        k = m + 1
    return (rank_sum - pos * (pos + 1) / 2) / (pos * neg)


def context_auc(train: list[dict], test: list[dict], label: str = "y") -> float:
    """How well period + clock (1-min bins) + margin alone predict `label`: rates on train, AUC on test.
    Rows: {period, clock_s, abs_diff, <label>}. Unseen bins fall back to the train base rate."""
    tot, pos = defaultdict(int), defaultdict(int)
    for r in train:
        b = _bin(r.get("period"), r.get("clock_s"), r.get("abs_diff"))
        tot[b] += 1
        pos[b] += int(r[label])
    base = (sum(pos.values()) + 1) / (sum(tot.values()) + 2)
    p = [(pos[b] + base) / (tot[b] + 1) if (b := _bin(r.get("period"), r.get("clock_s"), r.get("abs_diff"))) in tot
         else base for r in test]
    return auc([int(r[label]) for r in test], p)


def _bal(rows: list[dict], calls: dict, key: str) -> tuple[float, float, float, int]:
    """(balanced accuracy, precision, recall, n) of must_see calls vs `key` (base rates are ~10%, so plain
    accuracy would reward always saying no)."""
    tp = fp = fn = tn = 0
    for r in rows:
        c = calls.get(r["id"])
        if c is None:
            continue
        say, y = c == "must_see", bool(r[key])
        tp += say and y
        fp += say and not y
        fn += (not say) and y
        tn += (not say) and not y
    n = tp + fp + fn + tn
    tpr = tp / (tp + fn) if tp + fn else float("nan")
    tnr = tn / (tn + fp) if tn + fp else float("nan")
    return (tpr + tnr) / 2, (tp / (tp + fp) if tp + fp else float("nan")), tpr, n


def lock_grade(rows: list[dict], calls: dict, key: str = "y_out", draws: int = 2000, seed: int = 302) -> dict:
    """Grade lock calls ({row id: 'must_see'|'watch'|'none'}) against an outcome label, with a 95% cluster
    bootstrap over nights. Rows: {id, night, <key>}. MIN_BLIND rows per league before it can PASS/FAIL."""
    import random
    ba, prec, rec, n = _bal(rows, calls, key)
    by_night: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_night[r["night"]].append(r)
    nights = sorted(by_night)
    rng = random.Random(seed)
    boots = []
    for _ in range(draws if len(nights) > 1 else 0):
        pick = [x for _ in nights for x in by_night[rng.choice(nights)]]
        b = _bal(pick, calls, key)[0]
        if not math.isnan(b):
            boots.append(b)
    boots.sort()
    ci = (boots[int(0.025 * len(boots))], boots[min(len(boots) - 1, int(0.975 * len(boots)))]) if boots else None
    return {"n": n, "nights": len(nights), "balanced_acc": ba, "must_see_precision": prec, "must_see_recall": rec,
            "ci95": ci, "base_rate": sum(int(r[key]) for r in rows) / max(1, len(rows)),
            "powered": n >= MIN_BLIND}

