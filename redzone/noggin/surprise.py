"""EXP-027: surprise (KL over the full outcome distribution) values catch-up replays, never cut-ins.

A big play you missed is worth more as a replay when it moved the game's outcome distribution a lot.
Surprise here is Bayesian surprise in bits: KL(after || before), where before / after are the
distributions over six final outcomes, just before and just after the play's score change:

    home wins in regulation | home wins in OT | home wins in a shootout
    away wins in regulation | away wins in OT | away wins in a shootout

so a tying goal late (mass moves into OT / shootout), an OT winner and a shootout winner all register,
which a home-WP delta alone can miss. The distributions come from the analytic WP models in
redzone/scoring/gameflow.py (the same normal / Poisson models; football uses the basketball normal
model on a stretched clock, exactly as scoring.brain.analytic_wp does). The world-model rarity bonus
is out of scope (the queue says it comes later).

Where it acts: ONLY on Missed.value of a missed big play already in the Decider's catch-up queue.
Live candidates, their scores and every switch / stay rule are untouched, so surprise can reorder or
re-value replays, but it can never start a switch into live play (no cut-ins). It is wired in by
install(), which the EXP-027 harness calls; the live app never calls it, and even when installed it
does nothing unless `noggin.surprise.enabled` is true (OFF by default; the key is absent from
default_config.yaml, so the default is False here).

    outcome_dist(league, diff, period, clock_s)   six-outcome distribution (sums to 1)
    kl_bits(p, q)                                 KL(p || q) in bits
    play_surprise(league, before, after, period, clock_s)
    event_surprise(tl, pid)                       surprise of a timeline play, from its own scores/clocks
    bonus(cfg, bits)                              replay value points for that surprise
    install(decider_cls) -> uninstall             wrap Decider._scan_missed (the only write: Missed.value)
"""
from __future__ import annotations

import math
from typing import Callable

import numpy as np

from redzone.scoring.gameflow import SPEC, phi

FLAG = "noggin.surprise.enabled"
POINTS_PER_BIT = 20.0      # pre-registered (docs/prereg/exp-027.md): replay points per bit of surprise
CAP_BITS = 2.0             # pre-registered: surprise counted up to this many bits (bonus <= 40 points)

OUTCOMES = ("home_reg", "home_ot", "home_so", "away_reg", "away_ot", "away_so")
H_REG, H_OT, H_SO, A_REG, A_OT, A_SO = range(6)

# Analytic-model constants, the same as gameflow.wp_basketball / wp_hockey.
_SIGMA = {"nba": 16.5, "cbb": 14.5, "wcbb": 14.5, "nfl": 16.5, "cfb": 16.5}
_HOCKEY_RATE = 3.05 / 3600.0
_HOCKEY_HOME_EXTRA = 0.52          # gameflow.wp_hockey: a tie goes to OT at 52/48
# NHL regular-season 3-on-3 OT: ~60 % of OTs end before the shootout -> total OT goal rate per s.
_NHL_OT_S = float(SPEC["nhl"]["ot_s"])   # regular-season OT length (gameflow.SPEC)
_NHL_OT_RATE = -math.log(0.4) / _NHL_OT_S
_EPS = 1e-9
_FIRST_SCORE_MAX = {"nfl": 8, "cfb": 8, "nba": 4, "cbb": 4, "wcbb": 4, "nhl": 1}


def _base(league: str) -> str:
    lg = str(getattr(league, "value", league)).lower()
    return lg if lg in SPEC else lg


def _normal_split(diff: float, s: float) -> tuple[float, float, float]:
    """(P home ahead, P tied, P away ahead) of a final integer margin ~ N(diff, s^2)."""
    if s <= 1e-6:
        return (1.0, 0.0, 0.0) if diff > 0 else (0.0, 0.0, 1.0) if diff < 0 else (0.0, 1.0, 0.0)
    hi = float(phi((diff - 0.5) / s))         # margin >= 1
    lo = float(phi((diff + 0.5) / s))         # margin >= 0
    return hi, max(0.0, lo - hi), max(0.0, 1.0 - lo)


def _poisson(lam: float, kmax: int = 12) -> list[float]:
    lam = max(lam, 1e-12)
    return [math.exp(k * math.log(lam) - lam - math.lgamma(k + 1)) for k in range(kmax + 1)]


def _hockey_reg(diff: float, t_rem: float) -> tuple[float, float, float]:
    """(win, tie, loss) for the rest of regulation: independent Poisson scoring (gameflow.wp_hockey)."""
    t = max(0.0, t_rem)
    ph, pa = _poisson(_HOCKEY_RATE * 1.03 * t), _poisson(_HOCKEY_RATE * 0.97 * t)
    win = tie = 0.0
    for j, qa in enumerate(pa):
        for i, qh in enumerate(ph):
            m = diff + i - j
            if m > 0:
                win += qa * qh
            elif m == 0:
                tie += qa * qh
    tot = max(_EPS, sum(ph) * sum(pa))
    win, tie = win / tot, tie / tot
    return win, tie, max(0.0, 1.0 - win - tie)


def _norm(p: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(p, float), 0.0, None)
    s = p.sum()
    return p / s if s > 0 else np.full(len(OUTCOMES), 1.0 / len(OUTCOMES))


def outcome_dist(league: str, diff: float, period: int, clock_s: float) -> np.ndarray:
    """The six-outcome final-result distribution at (home - away margin, period, seconds left in it).
    Basketball and football put every extra-time result in the OT outcomes (no shootout)."""
    lg = _base(league)
    sp = SPEC[lg]
    P, n = float(sp["period_s"]), int(sp["n_reg"])
    period = int(period or 1)
    clock_s = max(0.0, float(clock_s or 0.0))
    out = np.zeros(len(OUTCOMES))
    if lg == "nhl":
        if period > n + 1:                          # shootout: a coin flip until it's decided
            if diff:
                out[H_SO if diff > 0 else A_SO] = 1.0
            else:
                out[H_SO] = out[A_SO] = 0.5
            return out
        if period == n + 1:                         # 3-on-3 OT: next goal wins, else a shootout
            if diff:
                out[H_OT if diff > 0 else A_OT] = 1.0
                return out
            goal = 1.0 - math.exp(-_NHL_OT_RATE * clock_s)
            out[H_OT], out[A_OT] = goal * _HOCKEY_HOME_EXTRA, goal * (1 - _HOCKEY_HOME_EXTRA)
            out[H_SO] = out[A_SO] = 0.5 * (1.0 - goal)
            return out
        t_rem = (n - period) * P + min(clock_s, P)
        win, tie, loss = _hockey_reg(diff, t_rem)
        goal = 1.0 - math.exp(-_NHL_OT_RATE * _NHL_OT_S)
        out[H_REG], out[A_REG] = win, loss
        out[H_OT], out[A_OT] = tie * goal * _HOCKEY_HOME_EXTRA, tie * goal * (1 - _HOCKEY_HOME_EXTRA)
        out[H_SO] = out[A_SO] = tie * (1.0 - goal) * 0.5
        return _norm(out)
    if lg not in _SIGMA:
        raise ValueError(f"no outcome model for {lg}")
    sigma = _SIGMA[lg]
    football = lg in ("nfl", "cfb")
    total = 3600.0 if football else P * n          # football: brain.analytic_wp's stretched clock
    if period > n:                                  # in extra time: win it now, or another OT
        left = clock_s if sp["ot_s"] else 300.0      # CFB's untimed OT: about one 5-min OT of scoring
        h, t, a = _normal_split(diff, sigma * math.sqrt(left / total))
        out[H_OT], out[A_OT] = h + 0.5 * t, a + 0.5 * t
        return _norm(out)
    left = (n - period) * P + min(clock_s, P)
    h, t, a = _normal_split(diff, sigma * math.sqrt(max(0.0, left) / total))
    out[H_REG], out[A_REG] = h, a
    out[H_OT] = out[A_OT] = 0.5 * t
    return _norm(out)


def kl_bits(p, q) -> float:
    """KL(p || q) in bits, with q floored at 1e-9 (an outcome the model called impossible)."""
    p = _norm(p)
    q = _norm(np.maximum(np.asarray(q, float), _EPS))
    m = p > 0
    return float(max(0.0, np.sum(p[m] * np.log2(p[m] / q[m]))))


def play_surprise(league: str, before: tuple[int, int], after: tuple[int, int], period: int,
                  clock_s: float) -> float:
    """Bits of surprise of a play that took the score from `before` to `after` (home, away) at
    (period, clock_s). The clock is the same on both sides, so only the play itself counts."""
    d0 = float(before[0]) - float(before[1])
    d1 = float(after[0]) - float(after[1])
    return kl_bits(outcome_dist(league, d1, period, clock_s), outcome_dist(league, d0, period, clock_s))


def _clock_at(tl, t: float, max_gap_s: float = 180.0) -> tuple[int, float] | None:
    best = None
    for wall, period, clock in getattr(tl, "clocks", ()) or ():
        if wall is None or period is None or clock is None:
            continue
        gap = abs(float(wall) - t)
        if gap <= max_gap_s and (best is None or gap < best[0]):
            best = (gap, int(period), float(clock))
    return (best[1], best[2]) if best else None


def event_surprise(tl, pid: str) -> float | None:
    """Surprise (bits) of the timeline's big play `pid`, read from the timeline's own score changes and
    clock samples. None when the state can't be read (no clock near the play, an unknown first score,
    a league without a model). A big play with no score change (a turnover) reads 0: the analytic
    models see only score and clock (a known limit, docs/prereg/exp-027.md)."""
    lg = _base(getattr(tl, "league", ""))
    if lg not in SPEC or lg == "mlb":
        return None
    ev = next((e for e in reversed(tl.events) if e.kind == "play" and (e.pid or e.label) == pid), None)
    if ev is None:
        return None
    scores = [s for s in tl.scores if s.score is not None]
    idx = next((i for i in range(len(scores) - 1, -1, -1) if ev.pid and scores[i].pid == ev.pid), None)
    if idx is None and not ev.pid:   # a bare "score change" event: the score change it was made from
        idx = next((i for i in range(len(scores) - 1, -1, -1)
                    if abs(scores[i].seen - ev.seen) <= max(5.0, ev.spread + 5.0)), None)
    if idx is None:
        prior = [s for s in scores if s.seen <= ev.seen]
        if not prior:
            return None
        after = before = tuple(prior[-1].score)
    else:
        after = tuple(scores[idx].score)
        if idx > 0:
            before = tuple(scores[idx - 1].score)
        elif sum(after) <= _FIRST_SCORE_MAX.get(lg, 0):
            before = (0, 0)
        else:
            return None
    clk = _clock_at(tl, ev.seen)
    if clk is None:
        return None
    try:
        return play_surprise(lg, before, after, clk[0], clk[1])
    except (ValueError, ZeroDivisionError):
        return None


def enabled(cfg) -> bool:
    return bool(cfg.get(FLAG, False))


def bonus(cfg, bits: float | None) -> float:
    """Replay value points for `bits` of surprise: POINTS_PER_BIT per bit, counted up to CAP_BITS."""
    if not bits or bits <= 0:
        return 0.0
    per = float(cfg.get("noggin.surprise.points_per_bit", POINTS_PER_BIT))
    cap = float(cfg.get("noggin.surprise.cap_bits", CAP_BITS))
    return per * min(cap, float(bits))


def apply(decider) -> int:
    """Value every not-yet-valued, missed (not seen live) play in the catch-up queue by its surprise.
    The ONLY write is Missed.value (plus the surprise_bits / surprise_bonus tags). Returns how many
    were bonused. No-op unless the flag is on."""
    if not enabled(decider.cfg):
        return 0
    n = 0
    for m in decider.queue:
        if m.seen_live or m.replayed or hasattr(m, "surprise_bits"):
            continue
        tl = decider.timelines.get(m.game_id)
        bits = event_surprise(tl, m.pid) if tl is not None else None
        m.surprise_bits = bits
        m.surprise_bonus = bonus(decider.cfg, bits)
        if m.surprise_bonus > 0:
            m.value += m.surprise_bonus
            n += 1
    return n


def install(decider_cls=None, sink: dict | None = None) -> Callable[[], None]:
    """Wrap decider_cls._scan_missed (default: the real Decider) so each scan ends with apply().
    Also counts decisions that carried a surprise-valued replay, per Decider instance
    (`surprise_log`) and summed into `sink` when given: {"valued": plays bonused, "replays": replay
    starts carrying one, "holds": stays on that replay, "cutins": any OTHER action carrying one}. "cutins" must stay 0.
    Returns the uninstall function."""
    if decider_cls is None:
        from redzone.decider.decider import Decider as decider_cls
    scan, decide = decider_cls._scan_missed, decider_cls.decide

    keys = ("valued", "replays", "holds", "cutins")
    if sink is not None:
        for k in keys:
            sink.setdefault(k, 0)

    def _log(self) -> list[dict]:
        own = self.__dict__.setdefault("surprise_log", dict.fromkeys(keys, 0))
        return [own] + ([sink] if sink is not None else [])

    def _scan_missed(self, cands, now):
        scan(self, cands, now)
        n = apply(self)
        for log in _log(self):
            log["valued"] = log.get("valued", 0) + n

    def _decide(self, *args, **kwargs):
        d = decide(self, *args, **kwargs)
        m = getattr(d, "replay", None)
        if m is not None and getattr(m, "surprise_bonus", 0) > 0:
            # "replay" starts one; a "stay" of kind "replay" holds the one on screen; anything else
            # carrying it would be surprise reaching live play: a cut-in.
            key = "replays" if d.action == "replay" else "holds" if d.kind == "replay" else "cutins"
            for log in _log(self):
                log[key] = log.get(key, 0) + 1
        return d

    decider_cls._scan_missed, decider_cls.decide = _scan_missed, _decide

    def uninstall() -> None:
        decider_cls._scan_missed, decider_cls.decide = scan, decide

    return uninstall
