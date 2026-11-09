"""Per-channel stream-delay predictor: how far each channel's picture runs behind real life.

Why: channels of one app don't share a delay: first YouTube TV samples Nov 2 were truTV 22 s, ESPN 26.4 s, FS1 42 s (commit 2026-11-03).

Channels of one app do NOT share one delay. The first real samples on the sticks (Nov 2 2026, YouTube
TV) were truTV 22 s and ESPN 26.4 s (show changes, boundary.py) and FS1 42 s (a tap): 20 s apart on
one app. The old book pooled an app's taps into one figure and handed it to every channel without
its own taps, with a margin of 2 s; truTV, measured at 22 s by its own show change, was told 42
(+-16) because a tap on FS1 "won". A channel outside that interval is how plays get cut mid-play.

The model, per app, is a two-level one:

    channel delay      d_c ~ N(m_app, tau^2)          channels of an app differ by about tau
    sample             x   ~ N(d_c, s_kind^2)          tap 4 s, score bug 4 s, show change 6 s

- Each measured channel gives one centre (a recency-weighted upper median of its samples, the safe
  late side, as before), so a channel tapped twelve times doesn't outvote three others.
- m_app is the OTHER channels' centre (mean of up to two, else upper median; channels measured
  directly beat show-change ones); tau is learned from how far all measured channels' centres
  spread, starting from a prior (TAU_PRIOR_S, worth TAU_PRIOR_N channels) below the spread seen on
  the sticks. Until several channels agree, an unmeasured channel
  keeps a wide interval (and so waits for official breaks) instead of borrowing another's.
- A channel's own show-change samples are combined with that prior (precision-weighted), so they
  move its estimate even when another channel has taps.
- A channel with its own taps or score-bug samples uses those, exactly as before: direct
  measurements of that channel are not shrunk toward other channels.

The predictor only ever widens relative to its inputs' own disagreement; the floors (tap 4 s,
show change 6 s) and the margins for another night / stale samples are unchanged.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

# Between-channel spread assumed before measuring it. Real YouTube TV channels, Nov 2 2026:
# truTV 22, ESPN 26.4, FS1 42 (sd 10.6). 8 s, worth two channels of evidence, is below that on
# purpose: it only sets how many agreeing channels it takes to trust the pool (three or so).
TAU_PRIOR_S = 8.0
TAU_PRIOR_N = 2.0


@dataclass
class Centre:
    """One channel's own estimate from its samples."""
    mu: float
    spread: float     # half the range its recent samples cover (6 s for a single sample)
    newest: float     # ts of its newest sample
    n: int
    kinds: frozenset


@dataclass
class AppPrior:
    """What the other channels of an app say about a channel never measured itself."""
    m: float          # their centre (upper median)
    tau: float        # how far channels of this app differ (learned, with the prior)
    se: float         # doubt about m itself
    n: int            # channels it's built from
    newest: float     # newest sample among them
    spread: float     # half the range of their centres

    @property
    def sd(self) -> float:
        """Predictive sd for an unmeasured channel: channel-to-channel spread plus doubt about m."""
        return math.sqrt(self.tau ** 2 + self.se ** 2)


def upper_median(vals: list[tuple[float, float]]) -> float:
    """Weighted median of (value, weight); ties lean late (the safe side)."""
    order = sorted(vals)
    half, acc = sum(w for _, w in order) / 2, 0.0
    for v, w in order:
        acc += w
        if acc > half:
            return v
    return order[-1][0]


def tau_estimate(centres: list[float]) -> float:
    """Between-channel sd: the prior, worth TAU_PRIOR_N channels, pooled with the centres' spread."""
    n = len(centres)
    if n < 2:
        return TAU_PRIOR_S
    mean = sum(centres) / n
    ss = sum((c - mean) ** 2 for c in centres)
    return math.sqrt((TAU_PRIOR_N * TAU_PRIOR_S ** 2 + ss) / (TAU_PRIOR_N + n - 1))


def app_prior(centres: dict[str, Centre], exclude: str) -> AppPrior | None:
    """The other measured channels' view of `exclude`'s delay, or None when none are measured."""
    others = [c for t, c in centres.items() if t != exclude]
    if not others:
        return None
    tau = tau_estimate([c.mu for c in centres.values()])   # spread from every measured channel
    # The centre itself from channels measured directly (taps, score bug) when there are any: those
    # beat show changes, as they always have. Show-change channels still widen tau above.
    direct = [c for c in others if "direct" in c.kinds]
    others = direct or others
    vals = [c.mu for c in others]
    # Two or fewer: their mean (the upper median of two is just the later one). More: the upper
    # median, so one odd channel can't drag every unmeasured channel with it.
    m = sum(vals) / len(vals) if len(vals) < 3 else upper_median([(v, 1.0) for v in vals])
    return AppPrior(m, tau, tau / math.sqrt(len(others)), len(others), max(c.newest for c in others),
                    (max(vals) - min(vals)) / 2)


def combine(prior_mu: float, prior_sd: float, x: float, x_sd: float) -> tuple[float, float]:
    """Precision-weighted combination of a prior and a measurement: (mean, sd)."""
    a, b = 1.0 / prior_sd ** 2, 1.0 / x_sd ** 2
    return (prior_mu * a + x * b) / (a + b), 1.0 / math.sqrt(a + b)


def replay_allows_floor(replay: dict | None, min_folds: int = 3) -> bool:
    """May a lower sigma floor ship for an app? Only on a forward-chaining replay over whole nights where NO fold's
    mid-play cuts rose: {"method": "forward_chaining", "folds": [{"base_mid_play": n, "new_mid_play": m}, ...]}."""
    if not isinstance(replay, dict) or replay.get("method") != "forward_chaining":
        return False
    folds = replay.get("folds") or []
    try:
        return len(folds) >= min_folds and all(int(f["new_mid_play"]) <= int(f["base_mid_play"]) for f in folds)
    except (KeyError, TypeError, ValueError):
        return False


class FeedBias:
    """How late each feed's wall clock runs against a feed with true event times, per league and source. The same
    scoring play reported by two sources (a game's score only happens once) gives one difference; the median of those is
    the bias. Reference: NBA liveData's timeActual ("nba_cdn"). Learned in shadow; adjust() applies it only with apply on."""

    def __init__(self, reference: str = "nba_cdn", min_n: int = 5, keep: int = 100, apply: bool = False):
        self.reference, self.min_n, self.keep, self.apply = reference, int(min_n), int(keep), bool(apply)
        self._walls: dict[tuple, dict[str, float]] = {}
        self.diffs: dict[tuple[str, str], list[float]] = {}

    def add(self, game, league: str, score: tuple, source: str, wall: float) -> None:
        """One scoring play as one source timed it."""
        if not source or not wall:
            return
        key = (game, score)
        got = self._walls.setdefault(key, {})
        if source in got:
            return
        got[source] = float(wall)
        for other, t in got.items():
            if other == source:
                continue
            if source == self.reference:
                self._diff(league, other, t - float(wall))
            elif other == self.reference:
                self._diff(league, source, float(wall) - t)
        while len(self._walls) > 2000:
            self._walls.pop(next(iter(self._walls)))

    def _diff(self, league: str, source: str, d: float) -> None:
        ds = self.diffs.setdefault((league, source), [])
        ds.append(d)
        del ds[:-self.keep]

    def bias(self, league: str, source: str) -> tuple[float, float, int] | None:
        """(median s late vs the reference, its robust sd, pairs), or None until min_n pairs."""
        ds = self.diffs.get((league, source))
        if not ds or len(ds) < self.min_n:
            return None
        med = statistics.median(ds)
        return med, 1.4826 * statistics.median(abs(x - med) for x in ds), len(ds)

    def adjust(self, league: str, source: str, wall: float) -> float:
        """The wall clock with the source's bias taken out, when apply is on and the bias is learned."""
        b = self.bias(league, source) if self.apply else None
        return wall if b is None else wall - b[0]

    def status(self) -> list[dict]:
        out = []
        for (league, source), ds in sorted(self.diffs.items()):
            b = self.bias(league, source)
            out.append({"league": league, "source": source, "pairs": len(ds),
                        "bias_s": None if b is None else round(b[0], 2), "sd_s": None if b is None else round(b[1], 2)})
        return out

