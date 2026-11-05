"""Automatic per-channel sync: each channel's screen delay tracked by a small Kalman filter, no taps.

What is tracked, per channel (tuner target):

    d   the screen delay now: how far the frame on screen runs behind real life (s)
    r   its drift (s per s): a stream that slowly falls behind (buffering, a slow CDN) or catches up
    o   the channel's stream origin, relative to a reference: wall time of stream position 0 on that
        channel's timeline (boundary.py). It is a property of the channel, not of a tune.

Signals, each a linear measurement of that state:

    show-title flip on the quarter hour (boundary.py)   d          ~3 s (networks flip a few s off)
    score bug: a new score's first frame - play wall   d          ~2 s (half the capture gap + feed wall rounding)
    score bug clock OCR vs the feed's play clock       d          ~1.5 s (see clock_sample)
    "I just saw it" tap / crowd roar (mic)             d          4 s / 3 s
    stick playback position, clock pinned (YTTV)       o + d      the clock pin's half width (~0.5 s)
    MediaSession burst on a stick (YTTV, see burst())  d (rel.)   ~0.3 s against the other stick's

YouTube TV's position is a per-TUNE clock, not the channel's timeline (live lab, Nov 4: every fresh
tune starts near the same position, 46,790 s, whatever the hour). So the origin o belongs to one
tune of a channel: once a flip, a score or an anchor has fixed it, every later read on that tune,
after any seek or pause, gives the delay at once (d = wall - origin - position). A new tune forgets
it. A seek or pause on the same tune keeps it: that is the read that times a rewind or a hold.

A new tune, a seek or a pause makes the delay unknown again (re-sync): d's doubt is reset to the
predictor's prior (delaymodel.py) and the drift is forgotten. Only a new tune forgets the origin. A measurement more than GATE
sigmas from the prediction is not used and the channel is flagged uncertain; two in a row from
different moments mean the stream really jumped, and the filter restarts from the newest one.

Pure python, a few multiply-adds per measurement: nothing here costs the Mac anything.
"""
from __future__ import annotations

import json
import logging
import math
import time
from pathlib import Path
from dataclasses import dataclass, field

log = logging.getLogger("redzone.sync")

SIGMA = {"flip": 3.0, "score": 2.0, "clock": 1.5, "tap": 4.0, "mic": 3.0, "break": 8.0}
DRIFT_ALARM_S = 5.0           # [SYNC 6] the delay moved this far from what it was when it synced: alarm
Q_SCALE_MAX = 25.0            # [SYNC 4] worst network condition: the delay wanders this much faster
POSITION_SIGMA_S = 0.5        # default doubt of one position read once the stick clock is pinned
Q_DELAY = 0.02 ** 2           # delay random walk, s^2 per s (about 1 s per 40 min)
Q_DRIFT = 1e-5 ** 2           # drift random walk, (s/s)^2 per s
R0_SD = 2e-3                  # prior doubt of the drift: 2 ms per s (7 s an hour), rarely more
O0_SD = 1e4                   # origin unknown until a delay and a position meet
GATE = 3.0                    # innovations beyond this many sigmas are not used
SYNC_TARGET_S = 2.0           # "synced": the stated doubt (1 sd) is under this
UNCERTAIN_HOLD_S = 300.0      # a disagreement flags the channel this long
STALE_S = 1800.0              # no measurement this long: not synced any more
POS_LOG_S = 10.0              # data/sync.jsonl: at most one position row per channel this often
LOG_MAX_BYTES = 20_000_000    # ... and the file rolls over at 20 MB (one old copy kept)


def _mat(n):
    return [[0.0] * n for _ in range(n)]


@dataclass
class ChannelSync:
    """One channel's filter. State x = [d, r, o], covariance P (3x3)."""
    target: str
    t: float                                   # time of the state
    x: list = field(default_factory=lambda: [40.0, 0.0, 0.0])
    P: list = field(default_factory=lambda: _mat(3))
    o_ref: float | None = None                 # o is stored relative to this (wall s) to keep numbers small
    updated: float | None = None               # last accepted measurement (wall)
    synced_at: float | None = None             # first time sd fell under SYNC_TARGET_S since the last tune
    tuned_at: float | None = None
    uncertain_until: float = 0.0
    rejected: int = 0
    accepted: int = 0
    _last_reject: tuple | None = None          # (t, kind, z) of the last rejected measurement
    has_origin: bool = False
    q_scale: float = 1.0                       # [SYNC 4] network condition: process noise multiplier
    plan_mu: float | None = None               # [SYNC 6] the delay when it (re)synced: drift is measured from it

    # ------------------------------------------------------------------ model
    def reset(self, t: float, mu: float, sd: float, new_stream: bool = False) -> None:
        """A tune or a seek: the delay is unknown again (prior mu +- sd), the drift too. The origin is
        kept over a seek or pause on the same tune and forgotten on a new tune (YTTV's position is a
        per-tune clock: before this, a re-tune's first reads were rejected and the channel was flagged
        uncertain for UNCERTAIN_HOLD_S)."""
        self._predict(t)
        if new_stream:
            self.has_origin, self.o_ref = False, None
            self.x[2] = 0.0
        self.x[0], self.x[1] = mu, 0.0
        for i in range(3):
            self.P[0][i] = self.P[i][0] = 0.0
            self.P[1][i] = self.P[i][1] = 0.0
        self.P[0][0], self.P[1][1] = sd ** 2, R0_SD ** 2
        if not self.has_origin:
            self.P[2][2] = O0_SD ** 2
        self.tuned_at, self.synced_at, self._last_reject = t, None, None
        self.plan_mu = None

    def _predict(self, t: float) -> None:
        dt = t - self.t
        if dt <= 0:
            return
        x, P = self.x, self.P
        x[0] += x[1] * dt
        # P = F P F' + Q with F = [[1, dt, 0], [0, 1, 0], [0, 0, 1]]
        p00 = P[0][0] + 2 * dt * P[0][1] + dt * dt * P[1][1]
        p01 = P[0][1] + dt * P[1][1]
        p02 = P[0][2] + dt * P[1][2]
        P[0][0], P[0][1], P[1][0], P[0][2], P[2][0] = p00, p01, p01, p02, p02
        P[0][0] += Q_DELAY * self.q_scale * dt
        P[1][1] += Q_DRIFT * dt
        self.t = t

    def _update(self, h: list, z: float, r_sd: float, t: float, kind: str) -> bool:
        self._predict(t)
        x, P = self.x, self.P
        Ph = [sum(P[i][j] * h[j] for j in range(3)) for i in range(3)]
        s = sum(h[i] * Ph[i] for i in range(3)) + r_sd ** 2
        innov = z - sum(h[i] * x[i] for i in range(3))
        if innov * innov > GATE * GATE * s and s < (GATE * 20) ** 2:
            # Disagrees with what the other signals said. Twice in a row, from two moments: the stream
            # really moved (an unseen seek, a restart). Start over from the newest measurement.
            self.rejected += 1
            self.uncertain_until = t + UNCERTAIN_HOLD_S
            prev = self._last_reject
            self._last_reject = (t, kind, z)
            log.info("%s: %s says %.1f, the filter %.1f +- %.1f; not used (flagged uncertain)",
                     self.target, kind, z, z - innov, math.sqrt(s))
            if prev is None or t - prev[0] < 1.0:
                return False
            self._restart_on(h, z, r_sd, t)
            return True
        self._last_reject = None
        k = [Ph[i] / s for i in range(3)]
        for i in range(3):
            x[i] += k[i] * innov
        newP = _mat(3)
        for i in range(3):
            for j in range(3):
                newP[i][j] = P[i][j] - k[i] * Ph[j]
        for i in range(3):                      # keep it symmetric, positive
            newP[i][i] = max(newP[i][i], 1e-9)
            for j in range(i):
                newP[i][j] = newP[j][i] = (newP[i][j] + newP[j][i]) / 2
        self.P = newP
        self._accepted(t)
        return True

    def _restart_on(self, h: list, z: float, r_sd: float, t: float) -> None:
        log.info("%s: two signals in a row disagree with the filter; re-syncing from the newest", self.target)
        plan = self.plan_mu        # [SYNC 6] the stream moved under us: that IS drift, keep measuring from the plan
        if h[2]:                                # a position read: the origin is suspect, the delay too
            self.has_origin = False
            self.reset(t, self.x[0], 30.0)
            self.P[2][2] = O0_SD ** 2
            self.plan_mu = plan
        else:
            self.reset(t, z, r_sd)
            self.plan_mu = plan
            self._accepted(t)
            return
        self._update(h, z, r_sd, t, "restart")

    def _accepted(self, t: float) -> None:
        self.accepted += 1
        self.updated = t
        if self.P[2][2] < 100.0:
            self.has_origin = True
        if self.synced_at is None and self.sd(t) <= SYNC_TARGET_S:
            self.synced_at = t
        if self.plan_mu is None and self.synced_at is not None:
            self.plan_mu = self.x[0]

    def shift(self, t: float, delta: float, sd: float) -> None:
        """[SYNC 1] A KNOWN move of the screen delay (a stall measured on the stick's own clock, a hold of
        a measured length): the estimate moves by delta and gets sd more doubt. A re-sync (reset) would
        throw away a good estimate for a 1 s buffering blip. The plan moves too: a booked stall isn't drift."""
        self._predict(t)
        self.x[0] += delta
        self.P[0][0] += sd * sd
        if self.plan_mu is not None:
            self.plan_mu += delta

    # ------------------------------------------------------------------ measurements
    def delay(self, t: float, z: float, kind: str, sd: float | None = None) -> bool:
        """A direct measurement of the screen delay at t (flip, score, clock, tap, mic)."""
        return self._update([1.0, 0.0, 0.0], z, sd if sd is not None else SIGMA.get(kind, 4.0), t, kind)

    def position(self, t: float, position_s: float, sd: float = POSITION_SIGMA_S) -> bool:
        """A stick playback read: the frame at stream position `position_s` was on screen at t."""
        y = t - position_s                       # = origin + delay
        if self.o_ref is None:
            self.o_ref = y - self.x[0]
        return self._update([1.0, 0.0, 1.0], y - self.o_ref, sd, t, "position")

    # ------------------------------------------------------------------ outputs
    def mu(self, t: float) -> float:
        return self.x[0] + self.x[1] * max(0.0, t - self.t)

    def sd(self, t: float) -> float:
        dt = max(0.0, t - self.t)
        v = self.P[0][0] + 2 * dt * self.P[0][1] + dt * dt * self.P[1][1] + Q_DELAY * self.q_scale * dt
        return math.sqrt(max(v, 0.0))

    def uncertain(self, t: float) -> bool:
        return t < self.uncertain_until

    def synced(self, t: float) -> bool:
        """Known to SYNC_TARGET_S (1 sd), recently measured, no disagreement outstanding."""
        return (self.updated is not None and t - self.updated <= STALE_S and not self.uncertain(t)
                and self.sd(t) <= SYNC_TARGET_S)

    def drift(self, t: float) -> float | None:
        """[SYNC 6] How far the delay has moved since it synced (None = not synced since the tune)."""
        return None if self.plan_mu is None else self.mu(t) - self.plan_mu

    def status(self, t: float) -> dict:
        d = self.drift(t)
        return {"target": self.target, "delay_s": round(self.mu(t), 1), "error_s": round(self.sd(t), 2),
                "drift_s": None if d is None else round(d, 1), "q_scale": round(self.q_scale, 2),
                "synced": self.synced(t), "uncertain": self.uncertain(t), "origin": self.has_origin,
                "since_tune_s": None if self.tuned_at is None else round(t - self.tuned_at),
                "synced_after_s": None if self.synced_at is None or self.tuned_at is None
                else round(self.synced_at - self.tuned_at, 1),
                "accepted": self.accepted, "rejected": self.rejected}


class SyncBook:
    """Every channel's filter. `prior(target) -> (mu, sd)` is the per-channel predictor (DelayBook)."""

    def __init__(self, prior, clock=time.time, on_change=None, log_path: str | Path | None = None):
        self._prior = prior
        self._clock = clock
        self.on_change = on_change or (lambda target: None)
        self.filters: dict[str, ChannelSync] = {}
        # One JSON line per signal and per re-sync, with the estimate +- error after it: the record
        # scripts/eval_sync.py replays to grade sync on a real night (data/sync.jsonl).
        self.log_path = Path(log_path) if log_path else None
        self._was: dict[str, tuple[bool, bool]] = {}
        self._pos_logged: dict[str, float] = {}
        self.behind_of = None   # [calib.auto] target -> s this tune is deliberately behind live (DelayBook)

    def _log(self, kind: str, target: str, t: float, **kw) -> None:
        f = self.filters.get(target)
        state = (f.synced(t), f.uncertain(t)) if f else (False, False)
        if f is not None and state != self._was.get(target):
            self._was[target] = state
            log.info("%s: delay %.1fs +- %.1f%s", target, f.mu(t), f.sd(t),
                     " (synced)" if state[0] else " (uncertain: signals disagree)" if state[1] else "")
        if self.log_path is None:
            return
        if kind == "position":                  # reads come every few seconds: log one per POS_LOG_S
            if t - self._pos_logged.get(target, float("-inf")) < POS_LOG_S and kw.get("used", True):
                return
            self._pos_logged[target] = t
        row = {"t": round(t, 3), "target": target, "kind": kind, **kw}
        if self.behind_of is not None:
            row["behind"] = round(float(self.behind_of(target) or 0.0), 2)
        if f is not None:
            row.update(mu=round(f.mu(t), 2), sd=round(f.sd(t), 2), synced=state[0], uncertain=state[1])
        try:
            if self.log_path.exists() and self.log_path.stat().st_size > LOG_MAX_BYTES:
                self.log_path.replace(self.log_path.with_suffix(".jsonl.1"))   # keep one old file: disk is tight
            with self.log_path.open("a") as fh:
                fh.write(json.dumps(row) + "\n")
        except OSError:
            log.debug("could not append to %s", self.log_path)

    def get(self, target: str) -> ChannelSync:
        f = self.filters.get(target)
        if f is None:
            t = self._clock()
            mu, sd = self._prior(target)
            f = self.filters[target] = ChannelSync(target, t)
            f.P[2][2] = O0_SD ** 2
            f.reset(t, mu, sd)
        return f

    def tuned(self, target: str, t: float | None = None, new_stream: bool = True) -> None:
        """A new tune (new_stream), or a seek or pause on this one (same stream: origin kept): re-sync."""
        t = self._clock() if t is None else t
        mu, sd = self._prior(target)
        self.get(target).reset(t, mu, sd, new_stream=new_stream)
        self._log("tune" if new_stream else "seek", target, t, prior=round(mu, 2), prior_sd=round(sd, 2))
        self.on_change(target)

    def shift(self, target: str, delta: float, sd: float = 0.5, t: float | None = None) -> None:
        """[SYNC 1] A known move of this channel's delay (see ChannelSync.shift)."""
        t = self._clock() if t is None else t
        self.get(target).shift(t, delta, sd)
        self._log("shift", target, t, delta=round(delta, 2), shift_sd=round(sd, 2))
        self.on_change(target)

    def condition(self, target: str, q_scale: float) -> None:
        """[SYNC 4] Network condition on the stick playing this channel (SyncProbe.condition): a weak or
        stalling link makes the delay wander faster, so its error bar widens faster between anchors."""
        f = self.filters.get(target)
        if f is None:
            return
        t = self._clock()
        f._predict(t)                          # the old rate applies up to now
        f.q_scale = min(Q_SCALE_MAX, max(1.0, float(q_scale)))
        self.on_change(target)

    def alarms(self, t: float | None = None, limit_s: float = DRIFT_ALARM_S,
               predicted_q: float = 9.0) -> list[dict]:
        """[SYNC 6] Channels whose delay moved more than limit_s since they synced ("measured"), or
        whose network condition says it is about to ("predicted": q_scale >= predicted_q)."""
        t = self._clock() if t is None else t
        out = []
        for target, f in self.filters.items():
            d = f.drift(t)
            if d is not None and abs(d) > limit_s:
                out.append({"target": target, "why": "measured", "drift_s": round(d, 1)})
            elif f.q_scale >= predicted_q:
                out.append({"target": target, "why": "predicted", "q_scale": round(f.q_scale, 1)})
        return out

    def measure(self, target: str, z: float, kind: str, t: float | None = None, sd: float | None = None,
                stick: str | None = None) -> bool:
        if not 2.0 <= z <= 180.0:
            return False
        t = self._clock() if t is None else t
        f = self.get(target)
        try:   # [live nights] the anchor's error: what this anchor measured minus what we predicted
            err = round(z - f.mu(t), 2)
        except Exception:
            err = None
        ok = f.delay(t, z, kind, sd)
        self._log(kind, target, t, z=round(z, 2), used=ok, err=err, **({"stick": stick} if stick else {}))
        self.on_change(target)
        return ok

    def position(self, target: str, position_s: float, t: float | None = None,
                 sd: float = POSITION_SIGMA_S) -> bool:
        t = self._clock() if t is None else t
        ok = self.get(target).position(t, position_s, sd)
        self._log("position", target, t, position=round(position_s, 3), pos_sd=round(sd, 3), used=ok)
        self.on_change(target)
        return ok

    def estimate(self, target: str, t: float | None = None,
                 better_than: float | None = None) -> tuple[float, float] | None:
        """(mu, sd) when this channel is synced, or (with better_than) when its filter, measured since
        the last tune, recently and without a disagreement outstanding, is surer than better_than (the
        predictor's own sigma). Else None: the predictor's estimate stands."""
        f = self.filters.get(target)
        t = self._clock() if t is None else t
        if f is None:
            return None
        if f.synced(t):
            return f.mu(t), f.sd(t)
        fresh = (f.updated is not None and t - f.updated <= STALE_S and not f.uncertain(t)
                 and (f.tuned_at is None or f.updated >= f.tuned_at))
        if better_than is not None and fresh and f.sd(t) < better_than:
            return f.mu(t), f.sd(t)
        return None

    def status(self) -> list[dict]:
        t = self._clock()
        return [f.status(t) for f in self.filters.values()]


def clock_sample(bug_clock_s: float, frame_wall: float, plays: list[tuple[float, float]],
                 tol_s: float = 1.5) -> float | None:
    """Delay from the game clock on the score bug, against the feed's plays.

    plays: (wallclock, game clock s remaining) of the current period's plays, oldest first. Between
    two consecutive plays whose wall gap matches their clock gap (to tol_s), the clock ran the whole
    time, so the real wall time at which it read `bug_clock_s` is interpolated; the frame showing it
    was on screen at frame_wall. None when no such pair brackets the reading (clock stopped, gaps)."""
    for (w0, c0), (w1, c1) in zip(plays, plays[1:]):
        if not (c1 <= bug_clock_s <= c0) or c0 <= c1:
            continue
        if abs((w1 - w0) - (c0 - c1)) > tol_s:
            continue
        wall = w0 + (c0 - bug_clock_s)
        d = frame_wall - wall
        return d if 2.0 <= d <= 180.0 else None
    return None


class KalmanSync:
    """[contracts] SyncModel over the delay book with its Kalman filters attached (DelayBook.attach_sync).

    estimate(): the delay book's figure for the channel (the filter's when it has the channel, else
    the per-channel predictor's), with the filter's uncertain flag. observe(): taps / score bug /
    show changes go to both the book and the filter; clock and mic samples to the filter only (a mic
    surge can't identify a play, so it never becomes a book sample)."""

    def __init__(self, book):
        if book.sync is None:
            book.attach_sync()
        self.book = book

    def estimate(self, target: str, now: float):
        from redzone.contracts.sync import SyncEstimate
        d = self.book.get(target)
        f = self.book.sync.filters.get(target)
        src = d.source if d.source in ("tap", "screen", "app", "boundary", "predicted", "default", "sync") else "default"
        return SyncEstimate(target, float(d.mu), float(d.current_sigma(now)), src, d.calibrated_at,
                            float(d.behind), bool(f is not None and f.uncertain(now)))

    def observe(self, target: str, sample_s: float, kind: str, at: float) -> None:
        s = self.book.sync
        if kind == "tap":
            s.measure(target, sample_s, "tap", t=at)
            self.book.calibrate(target, sample_s)
        elif kind == "boundary":
            s.measure(target, sample_s, "flip", t=at)
            self.book.boundary(target, sample_s, at)
        elif kind == "screen":
            s.measure(target, sample_s, "score", t=at)
            self.book.screen(target, sample_s, at)
        elif kind in ("clock", "mic"):
            s.measure(target, sample_s, kind, t=at)
        else:
            raise ValueError(f"KalmanSync can't take {kind!r} samples")
