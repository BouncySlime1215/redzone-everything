"""What's on your screen right now, estimated from the data and the stream delay.

Event times come from the feed's wallclock. When a feed has no wallclock,
we retain an interval covering its possible arrival lag, not one guessed time.
The delay is how far your screen runs behind real life; the "I just saw it"
button measures it directly (press_time - wallclock of the play).

    event airs on screen at  t + delay  (± 2·sigma)

This estimates what may be airing when the data runs ahead of the stream.
It does not inspect TV pixels, identify a broadcast replay, or turn a media
player's PLAYING state into evidence that the ball is live.
"""
from __future__ import annotations

import math
import statistics
import time
from collections import deque
from dataclasses import dataclass, field

from redzone.decider.cuts import DeadTracker
from redzone.decider.delaymodel import Centre, app_prior, combine
from redzone.models import REAL_BREAKS, ByLeague, GameState, League, Phase, Stoppage, pro_period

# Leagues where the ball/puck is live continuously; only official stoppages are gaps.
CONTINUOUS = {League.NBA, League.NHL, League.CBB, League.WCBB}
FEED_LAG_GUESS_S = 6.0  # used only when a feed gives no wallclock
PLAY_LEN_S = ByLeague({League.NFL: 10.0, League.MLB: 8.0, League.NBA: 0.0, League.NHL: 0.0})
AFTER_PLAY_BUFFER_S = 3.0
FIRST_POLL_RECENT_S = 60.0  # on a game's first poll, plays newer than this may not have aired yet
# [mid-play guard] A play with no wallclock happened somewhere in [arrived - MAX, arrived - MIN].
# Timing it at one guessed point can put its on-screen window in the wrong place entirely.
FEED_LAG_MIN_S = 2.0
FEED_LAG_MAX_S = 30.0
# Per source, for plays with no wallclock: (min, max) seconds the feed runs behind the ice. NHL
# api-web's live play-by-play ran 16.7 s p50 behind the event (ANA@VAN, Nov 3); its gamecenter
# publishes every ~20 s, so a 3 s poll sees a play 2..~45 s late. 60 s leaves room for a slow batch.
SOURCE_LAG_S = {"nhl": (2.0, 60.0)}
LAG_EST_DEFAULT_S = 10.0   # feed lag assumed until we've measured some
# [switch timing] A lag sample ("not in the data at the last fresh poll") is a lower bound. It was capped
# at 45 s, under the measured NHL median (50 s, docs/timing.md), so the gate believed its data covered a
# 43 s stream: 5 NHL mid-play cuts on the scorecard graded with the measured feed lag (--real-lag).
LAG_SAMPLE_CAP_S = 180.0
# The least doubt "I just saw it" taps can earn. Taps that agree to the second still share a bias
# (reaction time, the feed's wallclock rounding): stated 2 s, a real 6 s error cut 3 plays mid-play
# (eval.py --sigma 2 --true-delay 41); 4 s held 0 cuts out to 8 s of error for ~0.3 pt dead air.
MIN_TAP_SIGMA_S = 4.0
STALE_DATA_S = 20.0        # older than this, "no play in the data" no longer means "no play on screen"
# Conservative break durations used by the model (stoppage entry -> next snap). From 270 archived
# NFL games: end of quarter p1 171 s, halftime p1 816 s, two-minute warning p1 55 s. Timeouts and
# score breaks can be 5-15 s in the data, so they get no floor. Used only when we can't see the resume;
# these empirical bounds are not direct observation of the broadcast.
MIN_BREAK_S = ByLeague({
    League.NFL: {Stoppage.END_PERIOD: 120.0, Stoppage.HALFTIME: 800.0,
                 Stoppage.TWO_MINUTE_WARNING: 50.0},
    League.NBA: {Stoppage.HALFTIME: 800.0},
    # NHL 1st/2nd intermission: 5th percentile 1120 s over 190 archived intermissions.
    # NHL TV timeout: 220 of 221 fit-split timeouts ran >= 130 s (5-fold forward CV: 0 below 120 s in
    # every fold; the one short row is a mis-stamped faceoff, the next shot came 214 s later).
    # With the measured 35-50 s feed lag the data never covers the screen, so this floor is what
    # lets us leave during a TV timeout at all (docs/timing.md, A3).
    League.NHL: {Stoppage.INTERMISSION: 1080.0, Stoppage.TIMEOUT: 120.0},
    # A baseball inning change can be shorter than an NFL quarter break.
    # No minimum is assumed without feed coverage of the current screen.
})
# NFL halftime p5 829 s (200 games), NBA halftime p5 931 s (92 games): HALFTIME 800 is safe for both.


# Remembered taps ("I just saw it"), shared across channels and nights. A tap times one tune of one
# channel; another channel of the same app, or the same channel another night, lands a few seconds
# differently behind the live edge, so those estimates carry an extra margin. Too LOW a delay cuts
# plays mid-play, so stale or disagreeing taps always widen the interval, never narrow it.
TAP_HISTORY_S = 30 * 86400     # taps older than this are forgotten
TAP_HALF_LIFE_S = 7 * 86400    # a week-old tap counts half as much as tonight's
TAP_KEEP = 12                  # most recent taps used per channel / per app
CROSS_NIGHT_S = 6 * 3600       # newest tap older than this: it was measured on another night
STALE_TAP_S = 7 * 86400        # newest tap older than this: stale
SHARED_MARGIN_S = 2.0          # app-level or another night's estimate
STALE_MARGIN_S = 4.0           # stale estimate
SETTLED_SAMPLES = 3            # this many taps for an app ...
SETTLED_SPREAD_S = 6.0         # ... agreeing within this (half the range): stop asking for taps
SETTLED_DRIFT_S = 4.0          # settled delays proved stable: doubt grows to at most this
# Delay measured automatically from show changes on the quarter hour (redzone/screen/boundary.py).
# Weaker than a tap: a network may switch the title a few seconds off the hour, a show may start
# late. Each sample counts with this sigma; one alone never narrows the interval below the default.
BOUNDARY_SIGMA_S = 6.0
BOUNDARY_MIN_SAMPLES = 2
# Delay measured from the broadcast's own score bug (redzone/screen/scorebug.py): the first frame
# showing a new score, against the feed wallclock of the play that made it. Frame-accurate to half
# the ~2 s capture gap, so stronger than a tap: this many agreeing samples (pooled with any taps)
# lower the floor to SCREEN_SIGMA_S and settle the app on their own.
SCREEN_SIGMA_S = 4.0
# [edge model, Nick's Nov 6 order item 3, flag delay.edge_model.enabled, OFF] On a YouTube TV stick that was
# scrubbed back (yttv_seek enter_buffer), the screen delay is D_edge(channel) + behind_live, where behind_live
# is read off YouTube TV's own overlay time label. media_session's position is wrong after a seek and the old
# pause "hold" never held (Nov 6 stick tests), so neither may set behind_live on a seeked tune. Every number
# (edge default 25 s +-3, label sd, edge_max_sd_s) lives in delay.edge_model in default_config.yaml.


@dataclass
class Delay:
    mu: float
    sigma: float
    calibrated_at: float | None = None
    samples: deque = field(default_factory=lambda: deque(maxlen=TAP_KEEP))
    source: str = "default"    # "tap" | "screen" (this channel) | "app" (another channel of the same app) | "boundary" | "default"
    drift_cap: float = 6.0
    live: float | None = None  # this tune's own delay from the channel's stream origin (boundary.py)
    # [cut timing] this tune was rewound this far behind the live edge on entry (YouTube TV DVR) so
    # the picture came up on a dead ball; already included in mu. A fresh tune clears it.
    behind: float = 0.0
    # [auto sync] the per-channel predictor's own figure, kept while a synced filter overrides mu/sigma
    base: tuple | None = None

    # [self-calibrating doubt] the app's measured error (NoiseCal), when it has enough residuals; never widens
    cal_sigma: float | None = None

    def current_sigma(self, now: float) -> float:
        if self.calibrated_at is None:
            model = self.sigma
        else:
            # Stream delay is stable unless the stream buffers (see buffered()); doubt grows slowly.
            model = self.sigma + min(self.drift_cap, max(0.0, now - self.calibrated_at) / 900.0)
        return model if self.cal_sigma is None else min(model, self.cal_sigma)


class NoiseCal:
    """[self-calibrating doubt] How wrong a fresh tune's delay really is, learned from the filter's own
    innovations, so it works live without ground truth. The first anchor after each tune is compared
    with the delay the app was using: residual r = z - mu = (true error) + (anchor noise, sd R). Then
    E[r^2] - R^2 is the app's real squared error, mean included. The stated doubt is the 95 % upper
    bound of that (chi-square on n residuals), floored at min_sigma_s, and only once n >= min_n. It
    persists across nights (the pool lives as long as the object), so a night starts from the last."""

    def __init__(self, min_n: int = 20, min_sigma_s: float = 2.0, keep: int = 400):
        self.min_n, self.min_sigma_s, self.keep = int(min_n), float(min_sigma_s), int(keep)
        self.pool: dict[str, deque] = {}       # app -> (r, R)

    def add(self, app: str, r: float, anchor_sd: float) -> None:
        self.pool.setdefault(app, deque(maxlen=self.keep)).append((float(r), float(anchor_sd)))

    def sigma(self, app: str) -> float | None:
        rows = self.pool.get(app) or ()
        n = len(rows)
        if n < self.min_n:
            return None
        ms = sum(r * r for r, _ in rows) / n
        noise = sum(sd * sd for _, sd in rows) / n
        # one-sided 95 % upper bound on E[r^2] (Wilson-Hilferty chi-square quantile, n dof)
        z = 1.645
        lo_q = n * (1 - 2 / (9 * n) - z * (2 / (9 * n)) ** 0.5) ** 3
        upper = ms * n / max(1e-9, lo_q)
        return max(self.min_sigma_s, math.sqrt(max(0.0, upper - noise)))


def _estimate(taps: list[tuple[float, float]], now: float) -> tuple[float, float, float]:
    """(mu, spread, newest ts) of the most recent taps: recency-weighted upper median (one sloppy
    tap can't wreck it; ties lean late, the safe side) and half the range they cover."""
    recent = sorted(taps)[-TAP_KEEP:]
    w = {i: 0.5 ** (max(0.0, now - ts) / TAP_HALF_LIFE_S) for i, (ts, _) in enumerate(recent)}
    order = sorted(range(len(recent)), key=lambda i: recent[i][1])
    half, acc, mu = sum(w.values()) / 2, 0.0, recent[order[-1]][1]
    for i in order:
        acc += w[i]
        if acc > half:
            mu = recent[i][1]
            break
    vals = [x for _, x in recent]
    spread = (max(vals) - min(vals)) / 2 if len(vals) > 1 else 6.0
    return mu, spread, recent[-1][0]


class DelayBook:
    """Per-channel delay estimates. Key is the tuner target, e.g. 'youtubetv:espn'.

    Each channel uses its own taps (or score-bug samples). Without any, model "channel" (the default,
    delaymodel.py) predicts it from the app's other channels, each counted once with the learned
    channel-to-channel spread, combined with the channel's own show-change samples; model "legacy"
    hands it the pooled taps of its app (all 'youtubetv:*'). With nothing measured, the config
    default. Samples persist in the store and are reloaded at start (load)."""

    MODELS = ("channel", "legacy")

    def __init__(self, defaults: dict[str, float], sigma_default: float, clock=time.time,
                 model: str = "channel"):
        self._defaults = defaults
        self._sigma_default = sigma_default
        self._clock = clock
        self.model = model if model in self.MODELS else "channel"
        self.book: dict[str, Delay] = {}
        self.taps: dict[str, list[tuple[float, float]]] = {}   # target -> [(ts, sample)]
        self.bounds: dict[str, list[tuple[float, float]]] = {}  # target -> [(ts, sample)] from show changes
        self.screens: dict[str, list[tuple[float, float]]] = {}  # target -> [(ts, sample)] from the score bug
        self.live: dict[str, float] = {}                         # target -> this tune's delay (origin model)
        self.sync = None                                         # [auto sync] SyncBook, see attach_sync
        self.held: dict[str, float] = {}                         # [time-shift buffer] target -> s held behind live
        self._sync_floor = MIN_TAP_SIGMA_S
        self.sync_floor_for = None   # [league overrides] target -> its floor (engine), else _sync_floor
        self.cal: NoiseCal | None = None                         # [self-calibrating doubt] see attach_cal
        self._fresh: set[str] = set()                            # tuned, no anchor yet since
        self.calibrated: dict[str, tuple[float, float]] = {}     # [calib.auto] target -> (fresh-tune delay, sd)
        # [edge model] delay = D_edge(channel) + behind_live (label), config delay.edge_model
        self.edge_cfg = None                                      # key -> delay.edge_model.<key> (engine), else defaults
        self.labels: dict[str, tuple[float, float, float]] = {}   # target -> (behind_live_s, sd, ts) off the overlay label
        self.edge_seen: dict[str, tuple[float, float, float]] = {}  # target -> (edge s, sd, ts) tonight at behind 0
        self.on_label_stale = None                                # target -> re-read its label (wired by yttv_seek)
        # [edge prior, Nick Nov 7] one shared live-edge delay per app (delay.edge_prior, OFF), refreshed by the probe
        self.edge_prior_cfg = None                                # key -> delay.edge_prior.<key> (engine), else defaults
        self.edge_reads: dict[str, list[tuple[float, float]]] = {}  # app -> [(ts, delay_s)] from the edge probe

    @staticmethod
    def app(target: str) -> str:
        return target.split(":", 1)[0]

    def _app_taps(self, app: str) -> list[tuple[float, float]]:
        """Direct measurements for this app: taps and score-bug samples, all its channels."""
        return [x for d in (self.taps, self.screens) for t, v in d.items() if self.app(t) == app for x in v]

    def _own(self, target: str) -> list[tuple[float, float]]:
        return (self.taps.get(target) or []) + (self.screens.get(target) or [])

    def _floor(self, pool: list[tuple[float, float]]) -> float:
        """The tap floor, or the score-bug floor once enough of the recent pool came from the bug."""
        screen = {x for v in self.screens.values() for x in v}
        n = sum(1 for x in sorted(pool)[-TAP_KEEP:] if x in screen)
        return SCREEN_SIGMA_S if n >= SETTLED_SAMPLES else MIN_TAP_SIGMA_S

    def get(self, target: str) -> Delay:
        if target not in self.book:
            self.book[target] = Delay(self._defaults.get(self.app(target), 40.0), self._sigma_default)
            if self.cal is not None:
                self.book[target].cal_sigma = self.cal.sigma(self.app(target))
            self._refresh(target)
        return self.book[target]

    # ------------------------------------------------------------------ [auto sync]
    def attach_sync(self, min_sigma_s: float = MIN_TAP_SIGMA_S, log_path=None):
        """Track every channel with a Kalman filter (sync.py) that fuses the automatic signals; a synced
        channel's delay comes from it. Its sigma never drops below min_sigma_s: the gates were tuned on
        a 4 s floor (MIN_TAP_SIGMA_S) and a too-narrow interval is what cuts plays mid-play."""
        from redzone.decider.sync import SyncBook
        self._sync_floor = float(min_sigma_s)
        self.sync = SyncBook(self._sync_prior, clock=self._clock, on_change=self._sync_changed, log_path=log_path)
        # [calib.auto] each logged row carries the tune's deliberate offset, so a night's anchors give the
        # channel's fresh-tune delay (calibration.delay_fit)
        self.sync.behind_of = lambda t: self.book[t].behind if t in self.book else 0.0
        return self.sync

    def _sync_prior(self, target: str) -> tuple[float, float]:
        d = self.get(target)
        if self.labelled(target):
            return d.mu, d.sigma   # [edge model] D_edge + the label's behind_live: the filter re-syncs around it
        cal = self.calibrated.get(target)
        if cal is not None and self.app_edge(self.app(target)) is None:
            # [calib.auto] last nights' measured fresh-tune delay for this channel, plus what this tune is held
            # / rewound behind live. A fresh tune's edge still varies ~4 s between sticks (live lab, Nov 4).
            # The sd arrives floored at calib.sigma_floor_s (calibration.sync_prior).
            return cal[0] + d.behind, float(cal[1])
        mu, sigma = (d.base[0], d.base[1]) if d.base is not None else (d.mu, d.sigma)
        return mu, sigma

    def _sync_changed(self, target: str) -> None:
        if target in self.book:
            self._refresh(target)

    def tuned(self, target: str) -> None:
        """A new tune or a seek on this channel: its filter re-syncs (no-op without sync)."""
        self._fresh.add(target)
        if self.sync is not None:
            self.sync.tuned(target)

    def attach_cal(self, cal: "NoiseCal") -> "NoiseCal":
        """[self-calibrating doubt] Learn the real error from anchor innovations (NoiseCal)."""
        self.cal = cal
        for t, d in self.book.items():
            d.cal_sigma = cal.sigma(self.app(t))
        return cal

    def anchor(self, target: str, z: float, kind: str, t: float, sd: float) -> bool:
        """An absolute delay sample (a break anchor): its innovation feeds NoiseCal (first one after a
        tune only: the error of the delay a fresh tune starts from), then the filter."""
        if self.cal is not None and target in self._fresh:
            self._fresh.discard(target)
            self.cal.add(self.app(target), z - self.get(target).mu, sd)
            c = self.cal.sigma(self.app(target))
            for tt, d in self.book.items():
                if self.app(tt) == self.app(target):
                    d.cal_sigma = c
        return bool(self.sync.measure(target, z, kind, t, sd)) if self.sync is not None else False

    def _refresh(self, target: str) -> None:
        self._refresh_base(target)
        self._refresh_edge(target)

    def _refresh_base(self, target: str) -> None:
        d = self.book[target]
        if d.base is not None:                 # undo the sync / edge overlay: the model recomputes from its base
            d.mu, d.sigma, d.calibrated_at, d.source, d.drift_cap = d.base
            d.base = None
        self._refresh_model(target)
        self._apply_edge_prior(target)
        if self.sync is None:
            return
        est = self.sync.estimate(target, better_than=d.sigma)
        if est is None:
            return
        d.base = (d.mu, d.sigma, d.calibrated_at, d.source, d.drift_cap)
        f = self.sync.filters[target]
        floor = self.sync_floor_for(target) if self.sync_floor_for is not None else self._sync_floor
        d.mu, d.sigma, d.calibrated_at = est[0], max(floor, est[1]), f.updated
        d.source, d.drift_cap = "sync", SETTLED_DRIFT_S

    # ------------------------------------------------------------------ [edge model] D_edge + behind_live
    def _ecfg(self, key: str):
        """delay.edge_model.<key> from the live config (engine), else the shipped default."""
        v = None
        if self.edge_cfg is not None:
            try:
                v = self.edge_cfg(key)
            except Exception:
                v = None
        if v is None:
            from redzone import config
            v = config.default(f"delay.edge_model.{key}")
        return v

    @property
    def edge_model(self) -> bool:
        """delay.edge_model.enabled (OFF in the shipped config): a seeked tune's delay is D_edge + label."""
        return bool(self._ecfg("enabled"))

    def edge(self, target: str) -> tuple[float, float, str] | None:
        """D_edge(channel): how far this channel's live edge runs behind real life, with its sd and source.

        tonight   this channel measured tonight at the live edge (sync filter / taps / score bug, behind 0)
        probe / prior  [delay.edge_prior, flag on] the app's one live-edge delay: recent probe reads, else Nick's
                  Nov 7 measurement (YouTube TV 34.9 s +-0.6)
        calib     last nights' fitted fresh-tune delay for this channel (calib.auto, edge-relative)
        default   delay.edge_model.default[app] (YouTube TV 25 s +-3, Nov 6 research)
        None when nothing is known for the app."""
        seen = self.edge_seen.get(target)
        if seen is not None:
            return seen[0], seen[1], "tonight"
        pa = self.app_edge(self.app(target))      # [edge prior] the app's shared, probe-measured edge (flag on)
        if pa is not None:
            return pa[0], pa[1], pa[2]
        cal = self.calibrated.get(target)
        if cal is not None:
            return float(cal[0]), float(cal[1]), "calib"
        defaults = self._ecfg("default") or {}
        v = defaults.get(self.app(target))
        if v is None:
            return None
        return float(v), float(self._ecfg("default_sd_s")), "default"

    # ------------------------------------------------------------------ [edge prior] one live-edge delay per app
    def _pcfg(self, key: str):
        """delay.edge_prior.<key> from the live config (engine), else the shipped default."""
        v = None
        if self.edge_prior_cfg is not None:
            try:
                v = self.edge_prior_cfg(key)
            except Exception:
                v = None
        if v is None:
            from redzone import config
            v = config.default(f"delay.edge_prior.{key}")
        return v

    def _edge_prior(self, app: str) -> tuple[float, float] | None:
        """(mu, sd) of delay.edge_prior.prior[app] when the flag is on, else None."""
        if not self._pcfg("enabled"):
            return None
        p = (self._pcfg("prior") or {}).get(app)
        if not isinstance(p, dict) or p.get("mu_s") is None:
            return None
        return float(p["mu_s"]), float(p.get("sd_s") or 0.6)

    def app_edge(self, app: str) -> tuple[float, float, str] | None:
        """[edge prior, Nick Nov 7] How far this app's live edge runs behind, one figure for every channel:
        the median of the newest probe reads (delay.edge_prior.refresh.keep, younger than max_age_s), else the
        configured prior. Its sd is the prior's sd or the reads' spread, whichever is wider. None with the flag
        off or no prior for the app (Prime, ESPN+ keep today's model)."""
        prior = self._edge_prior(app)
        if prior is None:
            return None
        now = self._clock()
        max_age = float(self._pcfg("refresh.max_age_s") or 10800)
        keep = max(1, int(self._pcfg("refresh.keep") or 6))
        xs = [x for t, x in self.edge_reads.get(app, []) if now - t <= max_age][-keep:]
        if not xs:
            return prior[0], prior[1], "prior"
        spread = statistics.pstdev(xs) if len(xs) > 1 else 0.0
        return float(statistics.median(xs)), max(prior[1], spread), "probe"

    def add_edge_read(self, app: str, delay_s: float, ts: float | None = None) -> bool:
        """One edge probe read (yttv_edge_probe): the app's live edge delay right now. A read further than
        refresh.band_s from the prior is a misread and is dropped (False). Every channel of the app re-prices."""
        prior = self._edge_prior(app)
        if prior is None:
            return False
        band = float(self._pcfg("refresh.band_s") or 10)
        if not math.isfinite(delay_s) or abs(float(delay_s) - prior[0]) > band:
            return False
        reads = self.edge_reads.setdefault(app, [])
        reads.append((self._clock() if ts is None else float(ts), float(delay_s)))
        del reads[:-50]
        self._refresh_app(app)
        return True

    def _apply_edge_prior(self, target: str) -> None:
        """[edge prior] An unmeasured YouTube TV channel (no tap / score bug on it tonight) gets the app's one
        live-edge delay plus what this tune is behind, in place of other nights' taps (fs1 42 +-8) or the default.
        Its stated doubt keeps the sync floor (4 s, what the gates were tuned on): the 0.6 s is the edge's spread
        against the schedule clock, not proof the screen sits within 0.6 s of the feed's play times."""
        e = self.app_edge(self.app(target))
        if e is None:
            return
        now = self._clock()
        if any(now - ts <= CROSS_NIGHT_S for ts, _ in self._own(target)):
            return                                  # tonight's own taps / score bug on this channel win
        d = self.book[target]
        floor = self.sync_floor_for(target) if self.sync_floor_for is not None else self._sync_floor
        d.mu, d.sigma = e[0] + d.behind, max(float(floor), e[1])
        d.calibrated_at = max((t for t, _ in self.edge_reads.get(self.app(target), [])), default=now) \
            if e[2] == "probe" else now
        # drift: at most delay.edge_prior.drift_cap_s (+2 s: 4 -> 6 s), so a long night on the prior stays under decider.uncalibrated_sigma_s (8);
        # a stall still widens it (buffered) and tonight's anchors take over through the sync filter
        d.source, d.drift_cap, d.live = f"edge_{e[2]}", float(self._pcfg("drift_cap_s") or 2.0), None

    def set_label_behind(self, target: str, behind_s: float, sd: float | None = None,
                         ts: float | None = None) -> Delay:
        """behind_live read off YouTube TV's overlay time label for the tune now on this target (yttv_seek /
        the label reader). It stays this tune's behind_live until the next fresh tune (clear_label) or the
        next label read; media_session and the pause-hold timing no longer move it."""
        behind_s = max(0.0, float(behind_s))
        sd = float(self._ecfg("label_sd_s")) if sd is None else max(0.1, float(sd))
        prev = self.labels.get(target)
        self.labels[target] = (behind_s, sd, self._clock() if ts is None else float(ts))
        d = self.get(target)
        self._refresh(target)
        if self.edge_model and self.sync is not None and (prev is None or abs(prev[0] - behind_s) > 2.0):
            # the picture moved: the filter re-syncs (its prior is now edge + label) instead of averaging
            # the scrubbed tune's anchors into the at-the-edge estimate
            self.sync.tuned(target, new_stream=False)
        return d

    def clear_label(self, target: str) -> None:
        """A fresh tune (deep link to the live edge): the label read belonged to the old tune."""
        if self.labels.pop(target, None) is not None and target in self.book:
            self.book.pop(target, None)   # rebuilt at behind 0 from the channel's samples / filter
            self.get(target)

    def labelled(self, target: str) -> bool:
        """The edge model owns this target's delay: flag on and a label read for this tune."""
        return self.edge_model and target in self.labels

    def edge_measured(self, target: str) -> bool:
        """This channel's delay was measured, not guessed: a label read, tonight's edge, or a calibrated edge.
        The supervisor's delay_doubt re-anchors such a channel instead of resetting it to the defaults."""
        return target in self.labels or target in self.edge_seen or target in self.calibrated \
            or self.app_edge(self.app(target)) is not None

    def _refresh_edge(self, target: str) -> None:
        d = self.book[target]
        if not self.edge_model:
            return
        lab = self.labels.get(target)
        if lab is None:
            # remember tonight's edge: a tune at the live edge, measured (not a default), and sure enough
            if d.behind == 0.0 and not (self.held or {}).get(target) and d.source in ("sync", "tap", "screen") \
                    and d.sigma <= float(self._ecfg("edge_max_sd_s")):
                self.edge_seen[target] = (float(d.mu), float(d.sigma), float(d.calibrated_at or self._clock()))
            return
        e = self.edge(target)
        if e is None:
            return
        behind, lsd, ts = lab
        if d.base is None:
            d.base = (d.mu, d.sigma, d.calibrated_at, d.source, d.drift_cap)
        floor = self.sync_floor_for(target) if self.sync_floor_for is not None else self._sync_floor
        d.behind = behind
        d.mu = e[0] + behind
        d.sigma = max(float(floor), math.hypot(e[1], lsd))
        d.calibrated_at = ts
        d.source, d.drift_cap = f"edge+label:{e[2]}", SETTLED_DRIFT_S

    def reanchor(self, target: str) -> Delay | None:
        """[delay_doubt, edge model] Rebuild a measured channel's delay from its edge + behind_live, keeping
        the tune's behind_live (a reset that drops it would put a 60 s-behind picture 60 s "early")."""
        if target not in self.book or not self.edge_measured(target):
            return None
        d = self.book[target]
        behind = d.behind
        lab = self.labels.get(target)
        e = self.edge(target)
        if e is None:
            return None
        self.book.pop(target, None)
        nd = self.get(target)          # rebuilds from samples; a labelled target gets edge + label in _refresh
        if lab is not None and self.on_label_stale is not None:
            try:
                self.on_label_stale(target)   # stalls widened the label read: ask for a fresh one (yttv_seek)
            except Exception:
                pass
        if lab is None:
            if nd.base is None:
                nd.base = (nd.mu, nd.sigma, nd.calibrated_at, nd.source, nd.drift_cap)
            nd.behind = behind
            nd.mu = e[0] + behind
            nd.sigma = max(float(self._sync_floor), e[1] + (0.0 if e[2] == "tonight" else SHARED_MARGIN_S))
            nd.calibrated_at, nd.source, nd.drift_cap = self._clock(), f"edge:{e[2]}", 6.0
        return nd

    def _refresh_model(self, target: str) -> None:
        if self.model == "channel" and not self._own(target):
            self._refresh_predicted(target)
            return
        d, now = self.book[target], self._clock()
        own = self._own(target)
        taps = own or self._app_taps(self.app(target))
        if not taps:
            self._refresh_boundary(target)   # taps win; without any, show changes may measure it
            return
        mu, spread, newest = _estimate(taps, now)
        sigma = max(self._floor(taps), spread)   # contradicting taps widen the interval
        age = now - newest
        if age > STALE_TAP_S:
            sigma += STALE_MARGIN_S
        elif not own or age > CROSS_NIGHT_S:
            sigma += SHARED_MARGIN_S
        d.mu, d.sigma, d.calibrated_at = mu + d.behind, sigma, newest
        d.source = ("tap" if self.taps.get(target) else "screen") if own else "app"
        d.samples.clear()
        d.samples.extend(x for _, x in sorted(taps)[-TAP_KEEP:])
        d.drift_cap = SETTLED_DRIFT_S if self.settled(self.app(target)) else 6.0
        d.live = None

    def centres(self, app: str) -> dict[str, Centre]:
        """Each measured channel of this app, once: its taps and score-bug samples, else its show changes."""
        now, out = self._clock(), {}
        for t in {t for d in (self.taps, self.screens, self.bounds) for t in d if self.app(t) == app}:
            own = self._own(t)
            pool = own or self.bounds.get(t) or []
            if not pool:
                continue
            mu, spread, newest = _estimate(pool, now)
            out[t] = Centre(mu, spread, newest, len(pool), frozenset(("direct",) if own else ("boundary",)))
        return out

    def _refresh_predicted(self, target: str) -> None:
        """[per-channel delay] No taps or score-bug samples on this channel: predict it.

        From the app's other measured channels (their centre, widened by how far channels of this app
        differ), combined with this channel's own show-change samples. Never below the floors, never
        without the other-night / stale margins; with nothing to go on, the default stays."""
        d, now, app = self.book[target], self._clock(), self.app(target)
        direct = self._app_taps(app)
        if not direct and len(self._app_bounds(app)) < BOUNDARY_MIN_SAMPLES:
            return                               # one show change alone never narrows the default
        prior = app_prior(self.centres(app), target)
        if prior is None:                        # only this channel measured, by its show changes
            self._refresh_boundary(target)
            return
        own_b = self.bounds.get(target) or []
        floor = self._floor(direct) if direct else max(MIN_TAP_SIGMA_S, BOUNDARY_SIGMA_S)
        if own_b:
            cb, spread_b, newest = _estimate(own_b, now)
            x_sd = max(BOUNDARY_SIGMA_S / math.sqrt(len(own_b)), spread_b if len(own_b) > 1 else 0.0)
            mu, post = combine(prior.m, prior.sd, cb, x_sd)
            sigma = max(MIN_TAP_SIGMA_S, BOUNDARY_SIGMA_S, post, spread_b if len(own_b) > 1 else 0.0)
            shared = len(own_b) < BOUNDARY_MIN_SAMPLES
            d.source, pool = "boundary", own_b
        else:
            mu, newest = prior.m, prior.newest
            sigma = max(floor, prior.spread, prior.sd)
            shared = True
            # labelled by what the app's measurements are: taps / score bug, or show changes only
            d.source, pool = ("app", direct) if direct else ("boundary", self._app_bounds(app))
        age = now - newest
        if age > STALE_TAP_S:
            sigma += STALE_MARGIN_S
        elif shared or age > CROSS_NIGHT_S:
            sigma += SHARED_MARGIN_S
        d.mu, d.sigma, d.calibrated_at = mu + d.behind, sigma, newest
        d.samples.clear()
        d.samples.extend(x for _, x in sorted(pool)[-TAP_KEEP:])
        d.drift_cap = SETTLED_DRIFT_S if self.settled(app) and not own_b else 6.0
        live = self.live.get(target)
        d.live = live if live is not None and abs(live - mu) <= 2 * sigma else None
        if d.live is not None:
            d.mu = d.live + d.behind      # this tune's own delay re-centres; it never narrows

    def _app_bounds(self, app: str) -> list[tuple[float, float]]:
        return [x for t, v in self.bounds.items() if self.app(t) == app for x in v]

    def _refresh_boundary(self, target: str) -> None:
        """No taps for this app: use show-change samples (this channel's, else the app's) once there
        are two. Never below the boundary sigma (> the tap floor); disagreeing samples widen it."""
        d, now = self.book[target], self._clock()
        own = self.bounds.get(target) or []
        pool = own if len(own) >= BOUNDARY_MIN_SAMPLES else self._app_bounds(self.app(target))
        if len(pool) < BOUNDARY_MIN_SAMPLES:
            return
        mu, spread, newest = _estimate(pool, now)
        sigma = max(MIN_TAP_SIGMA_S, BOUNDARY_SIGMA_S, spread)
        age = now - newest
        if age > STALE_TAP_S:
            sigma += STALE_MARGIN_S
        elif pool is not own or age > CROSS_NIGHT_S:
            sigma += SHARED_MARGIN_S
        d.mu, d.sigma, d.calibrated_at = mu + d.behind, sigma, newest
        d.source = "boundary"
        d.samples.clear()
        d.samples.extend(x for _, x in sorted(pool)[-TAP_KEEP:])
        d.drift_cap = 6.0
        # This tune's own delay (origin model) re-centres the interval when it agrees with the
        # pooled samples; it never narrows it.
        live = self.live.get(target)
        d.live = live if live is not None and abs(live - mu) <= 2 * sigma else None
        if d.live is not None:
            d.mu = d.live + d.behind

    def boundary_count(self, app: str) -> int:
        """Show-change samples remembered for this app (all its channels)."""
        return len(self._app_bounds(app))

    def boundary(self, target: str, sample_s: float, ts: float | None = None) -> Delay:
        """One delay sample measured from a show change on the quarter hour (no tap)."""
        d = self.get(target)
        if not self._add(target, self._clock() if ts is None else ts, sample_s, self.bounds):
            return d
        self._refresh_app(self.app(target))
        return d

    def screen(self, target: str, sample_s: float, ts: float | None = None) -> Delay:
        """One delay sample from the score bug: a new score's first frame minus its play's wallclock."""
        d = self.get(target)
        if not self._add(target, self._clock() if ts is None else ts, sample_s, self.screens):
            return d
        self._refresh_app(self.app(target))
        return d

    def set_live(self, target: str, delay_s: float | None) -> None:
        """This tune's own delay from the channel's stream origin, or None when it's unknown."""
        if delay_s is None:
            if self.live.pop(target, None) is None:
                return
        else:
            self.live[target] = delay_s
        if target in self.book:
            self._refresh(target)

    def _refresh_app(self, app: str) -> None:
        for t in self.book:
            if self.app(t) == app:
                self._refresh(t)

    def settled(self, app: str) -> bool:
        """Enough recent, agreeing taps (or score-bug samples) for this app that asking for more is nagging."""
        taps = sorted(self._app_taps(app))[-TAP_KEEP:]
        if len(taps) < SETTLED_SAMPLES or self._clock() - taps[-1][0] > STALE_TAP_S:
            return False
        vals = [x for _, x in taps]
        return (max(vals) - min(vals)) / 2 <= SETTLED_SPREAD_S

    def measured(self, target: str) -> bool:
        """This channel was tapped itself, or its app is settled: no need to ask for a tap.

        [per-channel delay] Model "channel": an app counts as settled for a channel never measured
        itself only once SETTLED_SAMPLES other channels agree; one channel tapped three times says
        nothing about how far the others differ (truTV 22 s vs FS1 42 s on one app)."""
        if self._own(target):
            return True
        if self.sync is not None and self.sync.estimate(target) is not None:
            return True                        # [auto sync] synced from the automatic signals: no tap needed
        app = self.app(target)
        if not self.settled(app):
            return False
        if self.model == "legacy":
            return True
        prior = app_prior(self.centres(app), target)
        return prior is not None and prior.n >= SETTLED_SAMPLES and prior.spread <= SETTLED_SPREAD_S

    def _add(self, target: str, ts: float, sample_s: float, into: dict | None = None) -> bool:
        into = self.taps if into is None else into
        if not 2.0 <= sample_s <= 180.0 or ":" not in target:
            return False
        into.setdefault(target, []).append((ts, sample_s))
        into[target] = sorted(into[target])[-TAP_KEEP:]
        return True

    def load(self, rows) -> int:
        """Samples remembered from earlier runs: (ts, target, sample[, kind]) rows from the store.
        kind 'tap' ("I just saw it"; older rows have none), 'boundary' (a show change) or 'screen'
        (the score bug)."""
        cutoff, n = self._clock() - TAP_HISTORY_S, 0
        for row in rows:
            ts, target, sample = row[:3]
            kind = (row[3] if len(row) > 3 else None) or "tap"
            into = {"tap": self.taps, "boundary": self.bounds, "screen": self.screens}.get(kind)
            if into is not None and ts is not None and ts >= cutoff and target and sample is not None:
                n += self._add(str(target), float(ts), float(sample), into)
        for app in {self.app(t) for t in (*self.taps, *self.bounds, *self.screens)}:
            self._refresh_app(app)
        return n

    def calibrate(self, target: str, sample_s: float) -> Delay:
        """One "I just saw it" measurement. Outliers are clipped, not trusted blindly."""
        d = self.get(target)
        if not self._add(target, self._clock(), sample_s):
            return d
        self._refresh_app(self.app(target))   # every channel of this app without its own taps learns too
        return d

    def set_behind(self, target: str, behind_s: float, known_sd: float | None = None) -> None:
        """[cut timing] This tune now runs behind_s further behind the live edge (a rewind on entry),
        or 0 again (a fresh tune, a jump back to live). known_sd: the move was MEASURED (a hold or a
        stall timed on the stick's clock): the sync filter shifts by it instead of re-syncing."""
        d = self.get(target)
        if self.labelled(target):
            # [edge model] the overlay label measured this tune's behind_live; the hold timing does not move it
            self._refresh(target)
            return
        behind_s = max(0.0, behind_s)
        delta = behind_s - d.behind
        d.mu += delta
        d.behind = behind_s
        if d.base is not None:
            d.base = (d.base[0] + delta,) + d.base[1:]
        if delta and self.sync is not None:
            if known_sd is not None and behind_s > 0:
                self.sync.shift(target, delta, known_sd)   # [SYNC 1] a measured move: keep the sync
            else:
                self.sync.tuned(target, new_stream=False)   # a seek: the screen delay moved, re-sync

    def buffered(self, target: str) -> None:
        """The stream stalled: it's now further behind, and we're less sure by how much."""
        d = self.get(target)
        if self.labelled(target):
            # [edge model] the label read is stale by the stall: behind +3 s, its doubt +5 s, until re-read
            b, sd, ts = self.labels[target]
            self.labels[target] = (b + 3.0, sd + 5.0, ts)
            self._refresh(target)
            if self.sync is not None:
                self.sync.tuned(target, new_stream=False)
            return
        d.mu += 3.0
        d.sigma += 5.0
        if d.base is not None:
            d.base = (d.base[0] + 3.0, d.base[1] + 5.0) + d.base[2:]
        if self.sync is not None:
            self.sync.tuned(target, new_stream=False)   # stalled: re-sync from the next signal


_MARKERS = ("timeout", "end_", "two-minute", "period_end", "period_start", "start_inning",
            "start_batter", "end_batter", "coin_toss", "end_game", "end_period", "stoppage", "lineup_change")
# Dead-ball entries that happen DURING a whistle, not action that ends it. Without these, every
# free throw "ended" the stoppage and NBA whistles became too short to ever cut in. Per league on
# purpose: "foul" in baseball is a live pitch ("foul ball") — matching it everywhere caused a cut.
# Basketball: after a timeout/inbound the ball is live with nothing logged until the next shot,
# foul or turnover — up to a full shot clock. Play may have resumed that long before the first
# logged event (backtests: assuming 2s caused 7 cuts once break visits got more frequent).
SILENT_LIVE_S = ByLeague({League.NBA: 24.0, League.CBB: 30.0, League.WCBB: 30.0})  # college: 30 s shot clock


def resume_lead(league: League) -> float:
    """How long before the first logged event after a stoppage play may already have restarted."""
    return PLAY_LEN_S.get(league, 0.0) + 2.0 + SILENT_LIVE_S.get(league, 0.0)


_DEAD_BALL = ByLeague({
    League.NBA: ("foul", "substitution", "violation", "kicked_ball", "technical", "flagrant", "delay", "team_rebound",
                 "free_throw_-_1_of_2", "free_throw_-_1_of_3", "free_throw_-_2_of_3"),
    League.NHL: ("hooking", "holding", "tripping", "roughing", "interference", "slashing", "high-sticking",
                 "fighting", "unsportsmanlike", "cross-checking", "too_many", "delay"),
})


def _is_marker(kind: str, league: League | None = None) -> bool:
    """Bookkeeping entries in play-by-play (timeouts, period ends, dead-ball entries), not action."""
    k = (kind or "").lower()
    return any(m in k for m in _MARKERS) or any(m in k for m in _DEAD_BALL.get(league, ()))


def _t(p, now: float) -> float:
    return p.wallclock or p.seen_at or now - FEED_LAG_GUESS_S


def _bounds(p, now: float, prev_fresh: float | None = None) -> tuple[float, float]:
    """(earliest, latest) real time this play could have happened.

    With a wallclock it's exact. Without one, all we know is it reached us after the last fresh
    poll and before now, and the feed runs MIN..MAX seconds behind real life."""
    if p.wallclock:
        return p.wallclock, p.wallclock
    arrived = p.seen_at or now
    lo_lag, hi_lag = SOURCE_LAG_S.get(getattr(p, "source", ""), (FEED_LAG_MIN_S, FEED_LAG_MAX_S))
    return min(arrived, prev_fresh or arrived) - hi_lag, arrived - lo_lag


def _stoppage(label: str) -> Stoppage | None:
    try:
        return Stoppage(label)
    except ValueError:
        return None


def _is_game_end(kind: str) -> bool:
    k = (kind or "").lower()
    return "game" in k and "end" in k


@dataclass
class Event:
    seen: float      # when it happened (the LATEST it could have, if unsure)
    kind: str        # "play" | "stop_start" | "stop_end"
    label: str = ""
    big: bool = False
    pid: str = ""
    play_kind: str = ""
    spread: float = 0.0  # unsure by this much: it happened in [seen - spread, seen]
    score: tuple[int, int] | None = None  # "score" events: (home, away) right after the play

    @property
    def lo(self) -> float:
        return self.seen - self.spread


class Timeline:
    """Per-game event history, fed every poll from the merged GameState."""

    def __init__(self, league: League, clock=time.time):
        self.league = league
        self._clock = clock
        # [EXP-803 A5] the feed lag assumed until this game's lag is measured (delay.feed_lag_prior_s,
        # set per league by the decider)
        self.lag_prior = LAG_EST_DEFAULT_S
        # [EXP-212] break floors added on top of MIN_BREAK_S for this game (decider.leave.break_floor_s,
        # set per league by the decider): {Stoppage: s}. Empty = MIN_BREAK_S only.
        self.extra_floors: dict = {}
        self.events: deque[Event] = deque(maxlen=200)
        self.scores: deque[Event] = deque(maxlen=60)   # score changes (kind "score"), oldest first
        # [auto sync] (wallclock, period, game clock s left) of timed plays: the score bug's clock is
        # timed against these (sync.clock_sample)
        self.clocks: deque[tuple] = deque(maxlen=80)
        self._seen_ids: deque[str] = deque(maxlen=400)
        self._last_score: tuple | None = None
        self._stopped = False
        self.stoppage: Stoppage = Stoppage.UNKNOWN
        # [mid-play guard] freshness, measured feed lag, and the final whistle
        self.fresh_at: float | None = None    # when this game's data was last fresh
        self._lags: deque[float] = deque(maxlen=12)
        self.final_at: float | None = None    # when we first saw it Final
        self.dead = DeadTracker(league)       # [cut timing] dead windows: where to leave, where to land
        self.end_t: float | None = None       # latest real time the game could have ended
        # Break-length hints for planning break visits (Decider only; no leave gate reads these).
        self.break_kind = ""                  # ESPN kind of the play that started the current break
        self.period_timeouts: int | None = None  # timeouts this period so far; None = joined mid-period
        self.live = None                      # [noggin PICK] pick.LiveGame when noggin.pick is on (or shadow)

    def lag_est(self) -> float:
        """How far behind real life this game's feed runs (recent worst case)."""
        return max(self._lags) if self._lags else self.lag_prior

    def _nba_clutch(self, p, g: GameState) -> bool:
        """NBA highlight: a field goal in the last 2:00 of Q4/OT within 5 (what the scorecard counts).
        ESPN's NBA kinds are never "turnover"/"td", so without this no NBA play was ever big."""
        if self.league.base != League.NBA or not p.scoring or "free_throw" in (p.kind or ""):
            return False
        if pro_period(g.league, g.period, g.clock_s) < 4 or g.clock_s is None or g.clock_s > 120:
            return False
        hs, as_ = self._last_score or (g.home.score, g.away.score)
        return hs is not None and as_ is not None and abs(hs - as_) <= 5

    def observe(self, g: GameState) -> None:
        now = self._clock()
        prev_fresh = self.fresh_at
        self.fresh_at = g.updated_at or now
        score = (g.home.score, g.away.score)
        # Every play we haven't recorded yet, not just the latest: two plays can land in one poll.
        plays = list(g.recent_plays) or ([g.last_play] if g.last_play else [])
        if g.last_play and plays and plays[-1].id != g.last_play.id:
            plays.append(g.last_play)
        new = [p for p in plays if p.id and p.id not in self._seen_ids]
        if self.live is not None:
            self.live.matchup = g.matchup or self.live.matchup
            self.live.add(plays)
        first_poll = not self._seen_ids
        action = [p for p in new if not _is_marker(p.kind, self.league)]
        scorers = [p for p in new if p.scoring]
        for p in action:
            # First poll: skip history, but keep plays recent enough to still be airing
            # (an opening-kickoff return TD is in the very first poll of a game).
            if first_poll and _t(p, now) < now - FIRST_POLL_RECENT_S:
                continue
            # In basketball every basket "scores"; only flag true highlights there.
            big = p.kind in ("td", "turnover", "goal", "hr") or (p.scoring and self.league.base != League.NBA) \
                or self._nba_clutch(p, g)
            lo, hi = _bounds(p, now, prev_fresh)
            self.events.append(Event(hi, "play", (p.text or p.kind)[:80], big, p.id, p.kind, hi - lo))
            if p.wallclock and prev_fresh is not None and not first_poll:
                # It wasn't in the data at the last fresh poll, so the feed ran at least this far behind.
                self._lags.append(min(LAG_SAMPLE_CAP_S, max(0.0, prev_fresh - p.wallclock)))
        for p in scorers:
            # Every score change with its feed time, free throws included (they're dead-ball markers
            # above), for timing the broadcast's score bug (redzone/screen/scorebug.py).
            sc = p.score
            if sc is None and len(scorers) == 1 and None not in score:
                sc = score   # the poll's only new score: the game's score is what it made
            if sc is not None and not (first_poll and _t(p, now) < now - FIRST_POLL_RECENT_S):
                lo, hi = _bounds(p, now, prev_fresh)
                self.scores.append(Event(hi, "score", (p.text or p.kind)[:80], pid=p.id, play_kind=p.kind,
                                         spread=hi - lo, score=tuple(sc)))
        for p in new:
            if p.wallclock and getattr(p, "clock", None) is not None and getattr(p, "period", None) is not None:
                self.clocks.append((p.wallclock, p.period, p.clock))
            self._seen_ids.append(p.id)
            lo, hi = _bounds(p, now, prev_fresh)
            self.dead.add(p.id, lo, hi, p.kind, p.text, getattr(p, "type_text", ""),
                          getattr(p, "clock", None), getattr(p, "period", None))
        if not new and self._last_score is not None and score != self._last_score and None not in score:
            # No play-by-play from this source: a score change is still a play (time unknown: bound it).
            lo, hi = (prev_fresh or now) - FEED_LAG_MAX_S, now - FEED_LAG_MIN_S
            self.events.append(Event(hi, "play", "score change", True, spread=hi - lo))
        self._last_score = score

        lead = resume_lead(self.league)  # play restarts before the first logged event after a stoppage
        last_start = self.last("stop_start")
        # Real action after a stoppage means the stoppage ended, even if a new one started since
        # (timeout -> touchdown -> break). Missing this once caused a mid-play cut in backtests.
        if self._stopped and action and not first_poll:
            # [mid-play guard] earliest of the new plays (they can land out of order), never before the
            # stoppage it ends: an "end" older than its start reads as a break that never ended.
            t_end = min(_bounds(p, now, prev_fresh)[0] for p in action) - lead
            self.events.append(Event(max(t_end, last_start.lo) if last_start else t_end, "stop_end"))
            self._stopped = False
        lp = g.last_play
        # [mid-play guard] a break starts on screen no earlier than the LATEST it could have started.
        stop_lo, at = _bounds(lp, now, prev_fresh) if lp else (
            (prev_fresh or now) - FEED_LAG_MAX_S, now - FEED_LAG_MIN_S)
        stopped = g.stoppage not in (Stoppage.NONE, Stoppage.UNKNOWN)
        if stopped and not self._stopped:
            self.events.append(Event(at, "stop_start", g.stoppage.value,
                                     pid=lp.id if lp else "", spread=at - stop_lo))
        elif not stopped and self._stopped and g.stoppage == Stoppage.NONE:
            # [mid-play guard] Resumed with no play to time it by. It was still stopped in the data at the
            # last fresh poll; assume the EARLIEST resume consistent with that (was now - 6 - lead,
            # which can be later than the real resume, so a live play looked like a break).
            t_end = (prev_fresh or now) - self.lag_est() - lead
            self.events.append(Event(max(t_end, last_start.lo) if last_start else t_end, "stop_end"))
        self._stopped = stopped if g.stoppage != Stoppage.UNKNOWN else self._stopped
        self._note_break(g, lp, first_poll)
        self.stoppage = g.stoppage
        self.dead.seen(self.fresh_at, self.lag_est())

        if g.phase == Phase.POST and self.final_at is None:
            # [mid-play guard] Final in the data. The last play hasn't necessarily aired yet; remember the
            # latest it could have happened so the decider stays for it.
            self.final_at = now
            ends = [_bounds(p, now, prev_fresh)[1] for p in new if _is_game_end(p.kind)]
            known = [e.seen for e in self.events if e.kind == "play"]
            self.end_t = max(ends + known + ([] if ends else [now - FEED_LAG_MIN_S]))

    def _note_break(self, g: GameState, lp, first_poll: bool) -> None:
        """What kind of break this is, for guessing how long it runs (NBA: the first two timeouts
        of a period are the ~3 min TV breaks, later ones ~1.5 min; NFL: an official timeout is a
        commercial, a team timeout usually isn't). A timeout called during a whistle counts too."""
        if first_poll and (g.period or 0) <= 1:
            self.period_timeouts = 0
        if g.stoppage == self.stoppage or g.stoppage not in REAL_BREAKS:
            return
        self.break_kind = lp.kind if lp else ""
        if g.stoppage == Stoppage.TIMEOUT and self.period_timeouts is not None:
            self.period_timeouts += 1
        elif g.stoppage in (Stoppage.END_PERIOD, Stoppage.HALFTIME, Stoppage.INTERMISSION):
            self.period_timeouts = 0

    def last(self, kind: str) -> Event | None:
        for e in reversed(self.events):
            if e.kind == kind:
                return e
        return None


@dataclass
class Gate:
    safe: bool
    why: str
    on_screen_break: bool = False


def _stoppage_reached(tl: Timeline, start: Event, mu: float, margin: float) -> float:
    """From when the screen has nothing left to show before this stoppage.

    The stoppage itself airs at start + delay, but a timeout is often logged well after the
    play before it. Once that last play has surely finished airing, no action can reach the
    screen until the stoppage ends (the feed is ~35s ahead), so waiting longer only shows
    the huddle and then the ad. Not in continuous sports: the ball is live between logged plays.
    """
    reached = start.seen + mu + margin
    if tl.league in CONTINUOUS:
        return reached
    last = max((e.seen for e in tl.events if e.kind == "play" and e.seen <= start.seen), default=None)
    if last is not None:
        reached = min(reached, last + mu + margin + AFTER_PLAY_BUFFER_S)
    return reached


def leave_gate(tl: Timeline, delay: Delay, now: float, uncalibrated_sigma: float,
               tune_s: float = 6.0) -> Gate:
    """Is it safe to cut away from this game *as your screen shows it right now*?"""
    mu = delay.mu
    sig = delay.current_sigma(now)
    margin = 2 * sig
    play_len = PLAY_LEN_S.get(tl.league, 0.0)

    if tl.fresh_at is None or not tl.events:
        return Gate(False, "no observed play timing — screen state unknown")

    # [mid-play guard] Does our data still cover what your screen is showing? The screen can be
    # showing real time up to now - (mu - margin); a play that started by then was logged by
    # + play_len and reached our feed by + lag, so it's in our data only if that's before fresh_at.
    # If the feed is slow (lag), quiet (dropped source) or the delay is uncertain, "no play in the
    # data" stops meaning "no play on screen".
    age = max(0.0, now - tl.fresh_at)
    # how far our data runs ahead of your screen (basketball can be live ~a shot clock with no event)
    ahead = mu - margin - play_len - SILENT_LIVE_S.get(tl.league, 0.0) - tl.lag_est()
    covered = age <= ahead

    # 1. Is an official stoppage showing on screen (or is the screen done with action before it)?
    start, end = tl.last("stop_start"), tl.last("stop_end")
    in_break = False
    if start is not None:
        resume = end.seen if end is not None and end.seen >= start.lo else None
        # [mid-play guard] A play logged after the stoppage began proves play resumed, even if the
        # stop_end was mistimed or never recorded (late / out-of-order plays, missing wallclock).
        lead = resume_lead(tl.league)
        for e in reversed(tl.events):
            if (e.kind == "play" and e.seen > start.lo
                    and not (start.pid and e.pid == start.pid)):
                resume = e.lo - lead if resume is None else min(resume, e.lo - lead)
            # Arrival order is not event order. A very late old entry cannot
            # hide a newer play earlier in this bounded (200-event) deque.
        # The break is on screen once the play before it has surely finished airing — waiting for
        # the stoppage entry itself to air only shows the huddle and then the ad.
        aired_start = now >= _stoppage_reached(tl, start, mu, margin)
        if resume is not None:
            safe_until = resume + mu - margin                     # the snap that ends it can't be on screen yet
            not_ended = now <= safe_until - tune_s
            if aired_start and not not_ended and now <= safe_until and tl.league not in CONTINUOUS:
                # Still safe to cut (it's the break you're seeing), just too late to visit and come back.
                # Not in continuous sports: play resumes before the first logged event after it.
                return Gate(True, "break ending on screen")
        else:
            # No resume in the data: trust that only if the data covers your screen, or this kind
            # of break always runs long enough that it can't have ended on screen yet.
            kind = _stoppage(start.label)
            floor = max(MIN_BREAK_S.get(tl.league, {}).get(kind, 0.0),
                        float(getattr(tl, "extra_floors", {}).get(kind, 0.0)))
            not_ended = covered or now <= start.lo + floor + mu - margin - tune_s
        in_break = aired_start and not_ended
    if in_break:
        real = start.label in {b.value for b in REAL_BREAKS}
        return Gate(True, "break on screen" if real else "whistle on screen", real)

    # 2. Continuous-play sports, or we don't trust our delay: only breaks are safe.
    if tl.league in CONTINUOUS:
        return Gate(False, "play is live (only leave at a stoppage)")
    if sig > uncalibrated_sigma:
        return Gate(False, "delay not calibrated yet — waiting for an official break")
    # [mid-play guard] Stale or slow data: same deal, only official stoppages.
    if age > STALE_DATA_S:
        return Gate(False, f"no fresh data for {age:.0f}s — waiting for an official break")
    if not covered:
        return Gate(False, "data is running behind your screen — waiting for an official break")

    # 3. Discrete sports: make sure no seen play is airing (or about to) on screen.
    for e in reversed(tl.events):
        if e.kind != "play":
            continue
        end_air = e.seen + mu
        start_air = e.lo + mu - play_len   # a play with no wallclock could be anywhere in its spread
        if start_air - margin - 1.0 <= now <= end_air + margin + AFTER_PLAY_BUFFER_S:
            return Gate(False, "a play is on screen")
        # Check the whole bounded deque: an old event delivered last does not
        # imply the entries before it have already aired.
    return Gate(True, "between plays on screen")


def screen_evidence(tl: Timeline | None, delay: Delay, now: float,
                    uncalibrated_sigma: float) -> dict:
    """Expose the timing model's evidence without pretending to observe TV pixels.

    A provider's PLAYING state confirms transport activity only. It is never
    input to this sports-play estimate, and these statuses are not probabilities.
    """
    observed = bool(tl is not None and tl.fresh_at is not None and tl.events)
    gate = leave_gate(tl, delay, now, uncalibrated_sigma) if observed else Gate(
        False, "no observed play timing — screen state unknown")
    if not observed:
        status = "unknown"
    elif gate.safe:
        status = "break_estimated" if gate.on_screen_break or "break" in gate.why or "whistle" in gate.why else "gap_estimated"
    elif gate.why in ("a play is on screen", "play is live (only leave at a stoppage)"):
        status = "play_possible"
    else:
        status = "unknown"
    return {"status": status, "safe_to_leave": gate.safe, "reason": gate.why,
            "basis": "sports_feed_and_delay_model",
            "feed_age_s": round(max(0.0, now - tl.fresh_at), 1) if tl is not None and tl.fresh_at is not None else None,
            "delay_s": delay.mu, "delay_margin_s": 2 * delay.current_sigma(now),
            "calibrated": delay.calibrated_at is not None}


def leave_window(tl: Timeline, delay: Delay, now: float, uncalibrated_sigma: float,
                 required_s: float) -> Gate:
    """Require an open exit window for the whole command/HDMI latency interval.

    Checking only the instant a command is sent can cut a play that begins
    before the TV reacts. Checking two endpoints also misses a complete play
    between them, so test intersection with every observed play interval.
    Future coverage uses the same received data; it never assumes another poll.
    """
    if not math.isfinite(required_s) or required_s < 0:
        return Gate(False, "switch duration unknown — screen state unknown")
    until = now + required_s
    # Freeze the widest uncertainty across the interval. Reusing the mutable
    # estimate at 'now' alone would omit drift during a long pending operation.
    sigma = max(delay.current_sigma(now), delay.current_sigma(until))
    bounded = Delay(delay.mu, sigma)
    start = leave_gate(tl, bounded, now, uncalibrated_sigma, tune_s=required_s)
    if not start.safe or required_s == 0:
        return start
    end = leave_gate(tl, bounded, until, uncalibrated_sigma, tune_s=0)
    if not end.safe:
        return Gate(False, "exit window closes before switch completes: " + end.why)
    margin = 2 * sigma
    length = PLAY_LEN_S.get(tl.league, 0.0)
    for event in tl.events:
        if event.kind != "play":
            continue
        play_start = event.lo + delay.mu - length - margin - 1.0
        play_end = event.seen + delay.mu + margin + AFTER_PLAY_BUFFER_S
        if play_start <= until and play_end >= now:
            return Gate(False, "a play may air before switch completes")
    return start


_REAL = {b.value for b in REAL_BREAKS}


def resume_play(tl: Timeline) -> Event | None:
    """The snap that ended the latest stoppage (None while it's still stopped)."""
    start, end = tl.last("stop_start"), tl.last("stop_end")
    if end is None or (start is not None and end.seen < start.lo):
        return None
    return min((e for e in tl.events if e.kind == "play" and e.seen > end.seen),
               key=lambda e: e.seen, default=None)


def break_on_arrival(tl: Timeline, delay: Delay, now: float, at: float) -> bool:
    """Would you land in a commercial if the picture came up at `at`?

    A break fills the screen from when the play before it has aired until the first snap
    after it has aired: landing during that snap still shows mostly ad and a huddle."""
    start, end = tl.last("stop_start"), tl.last("stop_end")
    if start is None or start.label not in _REAL:
        return False
    margin = 2 * delay.current_sigma(now)
    if at < min(start.seen + delay.mu - margin, _stoppage_reached(tl, start, delay.mu, margin)):
        return False
    if end is None or end.seen < start.lo:
        return True
    snap = resume_play(tl)
    snap_end = snap.seen if snap else end.seen + PLAY_LEN_S.get(tl.league, 8.0) + 2.0
    return at < snap_end + delay.mu


def final_play_aired(tl: Timeline, delay: Delay, now: float) -> bool:
    """A finished game is safe to leave once its last play has finished on screen: the data
    says "final" a full stream delay before your screen shows the last kneel-down."""
    last = max((e.seen for e in tl.events if e.kind == "play"), default=None)
    return last is not None and now > last + delay.mu + 2 * delay.current_sigma(now) + AFTER_PLAY_BUFFER_S


def catchable(tl: Timeline, delay: Delay, now: float, tune_s: float) -> Event | None:
    """A big play that already happened in the data but hasn't aired yet:
    switch now and you'll see it live."""
    play_len = PLAY_LEN_S.get(tl.league, 8.0) or 8.0
    for e in reversed(tl.events):
        if e.kind == "play" and e.big:
            start_air = e.lo + delay.mu - play_len   # earliest it could start airing
            if now + tune_s + 2 * delay.current_sigma(now) < start_air:
                return e
            return None
    return None
