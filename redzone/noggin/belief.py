"""[EXP-023] Belief tracker: a per-game particle filter for the TV lag and the feed lag, with an online
Bayesian model of each broadcast's break rhythm.

Flag `noggin.belief.enabled`, OFF. Nothing live reads this module yet: today only the offline harness
(scripts/exp023_belief.py) swaps it in for the Kalman sync (redzone/decider/sync.py), so live behavior
is unchanged. It is a drop-in for SyncBook: same calls (tuned, shift, measure, position, estimate,
condition, alarms, status, filters), so DelayBook uses it unchanged.

What it does differently from the Kalman sync (sync.py):

1. **It remembers the channel across tunes.** sync.py throws the delay away on every tune or seek and
   starts again from the predictor's prior. Here each channel's screen delay is split into
       screen delay = base + behind + j
   base    the channel's own lag behind real life tonight (kept across tunes; a slow random walk)
   behind  the rewind the app itself applied (DelayBook.behind, known exactly, so never estimated)
   j       this tune's landing offset (a YouTube TV tune lands 0-6 s off the live edge, syncsim.py):
           redrawn on every tune or seek, zero mean.
   So every anchor on a channel tonight keeps counting after the next tune.
2. **The prior is heavy-tailed.** The kickoff calibration is a mixture: mostly the stated mu +- sigma,
   with a share at the config's wide default doubt, so a confidently wrong kickoff figure (the stress
   scorecard's 35 s vs a real 41 s) can still be learned away by anchors.
3. **Break anchors use each broadcast's own rhythm.** A break anchor measures delay + (the whistle ->
   ad-on-screen gap - its assumed mean). The gap's spread per broadcast is learned online (a
   conjugate inverse-gamma posterior on its variance), and the anchor likelihood is the posterior
   predictive Student-t: a broadcast that goes to break at a steady beat narrows fast, a messy one
   stays wide, and an odd 80 s gap is down-weighted instead of dragging the estimate. The break
   cadence (breaks per minute, gamma-Poisson) is tracked for the report and for later work.
   The gap's MEAN is not learned: with break anchors alone it cannot be told from the delay.
4. **Feed lag**: FeedLagBelief tracks each game's feed lag (arrival - play wallclock) the same way.
   Report-only here; the decider does not read it.

Pure python, ~300 particles per channel, a few thousand multiply-adds per anchor (anchors come a few
times a quarter): nothing here costs the Mac anything.

Why: first added in 290c5f3f (2026-11-04) as "EXP-023: pre-registration + belief tracker (flag noggin.belief.enabled, OFF) and dev-night harness"; no separate decision is recorded for this module.
"""
from __future__ import annotations

import math
import random
import time
import zlib
from dataclasses import dataclass, field

from redzone.decider.screen import DelayBook

FLAG = "noggin.belief.enabled"


def enabled(cfg) -> bool:
    """The experiment's flag (default_config: noggin.belief.enabled false)."""
    return bool(cfg.get(FLAG, False)) if cfg is not None else False


@dataclass(frozen=True)
class BeliefParams:
    """Every knob, fixed BEFORE the first run (docs/prereg/exp-023.md); none is tuned on any split."""
    particles: int = 300
    tune_jitter: float = 6.0        # width of a tune's landing offset j (syncsim.py: YouTube TV 0-6 s)
    wide_share: float = 0.2         # prior mixture: share of particles at the wide doubt
    wide_sd: float = 15.0           # = delay.sigma_default in default_config.yaml
    walk_sd_per_s: float = 0.02     # base random walk, s per sqrt(s) (= sync.py's Q_DELAY)
    rhythm_k0: float = 4.0          # prior strength of a broadcast's gap spread, in pseudo-anchors
    resid_cap: float = 60.0         # a residual counts at most this much into the rhythm update
    ess_frac: float = 0.5           # resample when the effective sample size drops under this share
    stale_after: float = 1800.0     # no anchor this long: not "synced" any more (as sync.py)
    synced_sd: float = 2.0          # "synced": 1 sd under this (as sync.py's target)
    z_lo: float = 2.0               # anchors outside [z_lo, z_hi] are not delays (as SyncBook.measure)
    z_hi: float = 180.0


def _t_logpdf(r: float, nu: float, scale: float) -> float:
    """Student-t log density (location 0)."""
    return (math.lgamma((nu + 1) / 2) - math.lgamma(nu / 2) - 0.5 * math.log(nu * math.pi) - math.log(scale)
            - (nu + 1) / 2 * math.log1p((r / scale) ** 2 / nu))


class Rhythm:
    """One broadcast's break rhythm, learned online.

    gap spread: the anchor residual r ~ N(0, s^2), s^2 ~ InvGamma(a, b), prior centred on the stated
        anchor sd with k0 pseudo-anchors. The predictive is Student-t(2a, sqrt(b/a)).
    cadence: breaks per second ~ Gamma(alpha, beta) from the breaks seen over the time watched."""

    def __init__(self, sd0: float, k0: float = 4.0, cap: float = 60.0):
        self.a = k0 / 2.0
        self.b = k0 / 2.0 * sd0 * sd0
        self.cap = cap
        self.n = 0
        self.alpha, self.beta = 1.0, 600.0    # cadence prior: ~1 break per 10 min, one pseudo-break
        self._last: float | None = None

    @property
    def nu(self) -> float:
        return 2.0 * self.a

    @property
    def scale(self) -> float:
        return math.sqrt(self.b / self.a)

    def logpdf(self, r: float) -> float:
        return _t_logpdf(r, self.nu, self.scale)

    def update(self, r: float, t: float | None = None) -> None:
        r = max(-self.cap, min(self.cap, r))
        self.a += 0.5
        self.b += 0.5 * r * r
        self.n += 1
        if t is not None:
            if self._last is not None and t > self._last:
                self.beta += min(t - self._last, 3600.0)
            self.alpha += 1.0
            self._last = t

    def mean_interval(self) -> float:
        """Posterior mean seconds between breaks."""
        return self.beta / max(self.alpha - 1.0, 1e-9) if self.alpha > 1.0 else self.beta

    def status(self) -> dict:
        return {"anchors": self.n, "gap_sd": round(self.scale, 2), "nu": round(self.nu, 1),
                "break_every": round(self.mean_interval(), 1)}


@dataclass
class ChannelBelief:
    """One channel's particle cloud over (base, j). Same read API as sync.ChannelSync."""
    target: str
    t: float
    p: BeliefParams
    rng: random.Random
    base: list = field(default_factory=list)
    j: list = field(default_factory=list)
    w: list = field(default_factory=list)
    behind: float = 0.0
    updated: float | None = None
    tuned_at: float | None = None
    synced_at: float | None = None
    uncertain_until: float = 0.0
    accepted: int = 0
    rejected: int = 0
    plan_mu: float | None = None
    q_scale: float = 1.0
    has_origin: bool = False

    def init(self, mu_base: float, sd: float) -> None:
        n = self.p.particles
        sd = max(sd, 0.5)
        self.base = [self.rng.gauss(mu_base, self.p.wide_sd if self.rng.random() < self.p.wide_share else sd)
                     for _ in range(n)]
        self.j = [0.0] * n            # the kickoff calibration was taken on this tune
        self.w = [1.0 / n] * n
        self.tuned_at = self.t

    # ------------------------------------------------------------------ dynamics
    def _predict(self, t: float) -> None:
        dt = t - self.t
        if dt <= 0:
            return
        sd = self.p.walk_sd_per_s * math.sqrt(dt * self.q_scale)
        g = self.rng.gauss
        self.base = [b + g(0.0, sd) for b in self.base]
        self.t = t

    def retune(self, t: float, behind: float) -> None:
        """A tune or a seek: base is kept, the landing offset is redrawn."""
        self._predict(t)
        h = self.p.tune_jitter / 2.0
        u = self.rng.uniform
        self.j = [u(-h, h) for _ in self.j]
        self.behind = behind
        self.tuned_at, self.synced_at, self.plan_mu = t, None, None

    def shift(self, t: float, delta: float, sd: float) -> None:
        """A KNOWN move of the screen delay (sync.ChannelSync.shift): every particle moves."""
        self._predict(t)
        g = self.rng.gauss
        self.j = [x + delta + g(0.0, sd) for x in self.j]
        if self.plan_mu is not None:
            self.plan_mu += delta

    # ------------------------------------------------------------------ update
    def _resample(self) -> None:
        n = len(self.w)
        ess = 1.0 / sum(x * x for x in self.w)
        if ess >= self.p.ess_frac * n:
            return
        m, var = self._moments_base()
        rough = 0.1 * math.sqrt(var) + 0.05      # a little roughening keeps the cloud from collapsing
        step, u0 = 1.0 / n, self.rng.random() / n
        cum, i, nb, nj = self.w[0], 0, [], []
        for k in range(n):
            u = u0 + k * step
            while u > cum and i < n - 1:
                i += 1
                cum += self.w[i]
            nb.append(self.base[i] + self.rng.gauss(0.0, rough))
            nj.append(self.j[i])
        self.base, self.j, self.w = nb, nj, [step] * n

    def update(self, t: float, z: float, loglik) -> bool:
        """Weight every particle by loglik(z - its screen delay)."""
        self._predict(t)
        mu, sd = self.mu(t), self.sd(t)
        lw = [math.log(max(w, 1e-300)) + loglik(z - (b + j + self.behind))
              for w, b, j in zip(self.w, self.base, self.j)]
        top = max(lw)
        w = [math.exp(x - top) for x in lw]
        s = sum(w)
        if not s > 0 or not math.isfinite(s):
            self.rejected += 1
            return False
        self.w = [x / s for x in w]
        if abs(z - mu) > 4.0 * max(sd, 1.0):
            self.rejected += 1          # counted (status), but the heavy tail already down-weighted it
        self._resample()
        self.accepted += 1
        self.updated = t
        if self.synced_at is None and self.sd(t) <= self.p.synced_sd:
            self.synced_at = t
        if self.plan_mu is None and self.synced_at is not None:
            self.plan_mu = self.mu(t)
        return True

    # ------------------------------------------------------------------ outputs
    def _moments_base(self) -> tuple[float, float]:
        m = sum(w * b for w, b in zip(self.w, self.base))
        v = sum(w * (b - m) ** 2 for w, b in zip(self.w, self.base))
        return m, v

    def _moments(self) -> tuple[float, float]:
        m = sum(w * (b + j) for w, b, j in zip(self.w, self.base, self.j))
        v = sum(w * (b + j - m) ** 2 for w, b, j in zip(self.w, self.base, self.j))
        return m, v

    def mu(self, t: float) -> float:
        return self._moments()[0] + self.behind

    def sd(self, t: float) -> float:
        dt = max(0.0, t - self.t)
        return math.sqrt(max(self._moments()[1], 0.0) + self.p.walk_sd_per_s ** 2 * dt * self.q_scale)

    def uncertain(self, t: float) -> bool:
        return t < self.uncertain_until

    def synced(self, t: float) -> bool:
        return (self.updated is not None and t - self.updated <= self.p.stale_after
                and not self.uncertain(t) and self.sd(t) <= self.p.synced_sd)

    def drift(self, t: float) -> float | None:
        return None if self.plan_mu is None else self.mu(t) - self.plan_mu

    def status(self, t: float) -> dict:
        d = self.drift(t)
        return {"target": self.target, "delay_s": round(self.mu(t), 1), "error_s": round(self.sd(t), 2),
                "drift_s": None if d is None else round(d, 1), "q_scale": round(self.q_scale, 2),
                "synced": self.synced(t), "uncertain": self.uncertain(t), "origin": False,
                "since_tune_s": None if self.tuned_at is None else round(t - self.tuned_at),
                "accepted": self.accepted, "rejected": self.rejected, "model": "belief"}


class BeliefBook:
    """Every channel's belief; a drop-in for sync.SyncBook. `prior(target) -> (mu, sd)` is DelayBook's
    predictor; `behind(target)` the rewind the app applied to that channel (DelayBook.get().behind)."""

    def __init__(self, prior, behind=None, clock=time.time, on_change=None, params: BeliefParams | None = None,
                 seed: int = 0):
        self._prior = prior
        self._behind = behind or (lambda target: 0.0)
        self._clock = clock
        self.on_change = on_change or (lambda target: None)
        self.p = params or BeliefParams()
        self.seed = seed
        self.filters: dict[str, ChannelBelief] = {}
        self.rhythms: dict[str, Rhythm] = {}
        self.log_path = None
        self.behind_of = None   # [calib.auto] parity with SyncBook: logged rows carry the tune's offset

    def _log(self, kind: str, target: str, t: float, **kw) -> None:
        """[wired, lane 2] The same data/sync.jsonl rows SyncBook writes (eval_sync, sync_truth read them)."""
        if self.log_path is None:
            return
        import json
        from pathlib import Path
        row = {"t": round(t, 3), "target": target, "kind": kind, "tracker": "belief", **kw}
        if self.behind_of is not None:
            row["behind"] = round(float(self.behind_of(target) or 0.0), 2)
        f = self.filters.get(target)
        if f is not None:
            row.update(mu=round(f.mu(t), 2), sd=round(f.sd(t), 2), synced=f.synced(t), uncertain=f.uncertain(t))
        try:
            with Path(self.log_path).open("a") as fh:
                fh.write(json.dumps(row) + "\n")
        except OSError:
            pass

    def get(self, target: str) -> ChannelBelief:
        f = self.filters.get(target)
        if f is None:
            t = self._clock()
            mu, sd = self._prior(target)
            behind = float(self._behind(target) or 0.0)
            rng = random.Random(self.seed * 1_000_003 + zlib.crc32(target.encode()))
            f = self.filters[target] = ChannelBelief(target, t, self.p, rng, behind=behind)
            f.init(mu - behind, sd)
        return f

    def rhythm(self, target: str, sd0: float) -> Rhythm:
        r = self.rhythms.get(target)
        if r is None:
            r = self.rhythms[target] = Rhythm(sd0, self.p.rhythm_k0, self.p.resid_cap)
        return r

    def tuned(self, target: str, t: float | None = None, new_stream: bool = True) -> None:
        """A new tune, or (new_stream False) a seek / pause on this one: either way the base is kept and
        only the landing offset re-drawn with the channel's current rewind (SyncBook parity)."""
        t = self._clock() if t is None else t
        f = self.filters.get(target)
        if f is None:
            self.get(target)
        else:
            f.retune(t, float(self._behind(target) or 0.0))
        self._log("tune" if new_stream else "seek", target, t)
        self.on_change(target)

    def shift(self, target: str, delta: float, sd: float = 0.5, t: float | None = None) -> None:
        t = self._clock() if t is None else t
        self.get(target).shift(t, delta, sd)
        self.on_change(target)

    def condition(self, target: str, q_scale: float) -> None:
        f = self.filters.get(target)
        if f is None:
            return
        f._predict(self._clock())
        f.q_scale = min(25.0, max(1.0, float(q_scale)))
        self.on_change(target)

    def alarms(self, t: float | None = None, limit_s: float = 5.0, predicted_q: float = 9.0) -> list[dict]:
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
        if not self.p.z_lo <= z <= self.p.z_hi:
            return False
        t = self._clock() if t is None else t
        f = self.get(target)
        if kind == "break":
            rh = self.rhythm(target, float(sd) if sd else 10.0)
            resid = z - f.mu(t)
            ok = f.update(t, z, rh.logpdf)
            if ok:
                rh.update(resid, t)
            self._log(kind, target, t, z=round(z, 2), used=ok, err=round(resid, 2), **({"stick": stick} if stick else {}))
        else:
            s = float(sd) if sd else 4.0
            err = round(z - f.mu(t), 2)
            ok = f.update(t, z, lambda r: -0.5 * (r / s) ** 2)
            self._log(kind, target, t, z=round(z, 2), used=ok, err=err, **({"stick": stick} if stick else {}))
        self.on_change(target)
        return ok

    def position(self, target: str, position_s: float, t: float | None = None, sd: float = 0.5) -> bool:
        """Stick playback reads are not modelled in this experiment: ignored (the predictor stands)."""
        return False

    def estimate(self, target: str, t: float | None = None,
                 better_than: float | None = None) -> tuple[float, float] | None:
        """(mu, sd) once this channel has had one anchor tonight. Unlike sync.py there is no
        "surer than the predictor" gate: the predictor's figure IS this cloud's prior, so the posterior
        already holds it, and a confidently wrong kickoff figure (sigma 3 s, 6 s off) would otherwise
        never be overruled. The anchors need not be from THIS tune either: the base is carried across
        tunes, with the new tune's landing doubt added. `better_than` is accepted for SyncBook parity."""
        f = self.filters.get(target)
        t = self._clock() if t is None else t
        if f is None or f.updated is None or f.uncertain(t):
            return None
        return f.mu(t), f.sd(t)

    def status(self) -> list[dict]:
        t = self._clock()
        return [f.status(t) for f in self.filters.values()]


class BeliefDelayBook(DelayBook):
    """DelayBook whose sync is the belief tracker. Built only where the flag (or the harness) asks."""

    belief_params: BeliefParams | None = None
    belief_seed: int = 0

    def attach_sync(self, min_sigma_s: float = 4.0, log_path=None):
        return attach(self, min_sigma_s, log_path, self.belief_params, self.belief_seed)


def attach(book: DelayBook, min_sigma_s: float = 4.0, log_path=None, params: BeliefParams | None = None,
           seed: int = 0) -> BeliefBook:
    """[wired, lane 2] Give a DelayBook the belief tracker as its sync (engine, when noggin.belief.enabled)."""
    book._sync_floor = float(min_sigma_s)
    book.sync = BeliefBook(book._sync_prior, behind=lambda tg: book.get(tg).behind, clock=book._clock,
                           on_change=book._sync_changed, params=params, seed=seed)
    book.sync.log_path = log_path
    book.sync.behind_of = lambda tg: book.book[tg].behind if tg in book.book else 0.0
    return book.sync


class FeedLagBelief:
    """One game's feed lag (play arrival - its wallclock), as a particle cloud on a slow random walk
    with a Student-t spread learned online (report-only in EXP-023)."""

    def __init__(self, mu0: float = 8.0, sd0: float = 6.0, particles: int = 200, walk_sd_per_s: float = 0.01,
                 seed: int = 0):
        self.rng = random.Random(seed)
        # heavy-tailed like the channel prior: a fifth of the cloud at 4x the doubt
        self.x = [self.rng.gauss(mu0, sd0 * (4.0 if self.rng.random() < 0.2 else 1.0)) for _ in range(particles)]
        self.w = [1.0 / particles] * particles
        self.rh = Rhythm(sd0, 4.0, 120.0)
        self.walk = walk_sd_per_s
        self.t: float | None = None
        self.n = 0

    def observe(self, lag: float, t: float) -> None:
        if self.t is not None and t > self.t:
            sd = self.walk * math.sqrt(t - self.t)
            self.x = [v + self.rng.gauss(0.0, sd) for v in self.x]
        self.t = t
        mu = self.estimate()[0]
        lw = [math.log(max(w, 1e-300)) + self.rh.logpdf(lag - v) for w, v in zip(self.w, self.x)]
        top = max(lw)
        w = [math.exp(v - top) for v in lw]
        s = sum(w)
        self.w = [v / s for v in w]
        self.rh.update(lag - mu)
        self.n += 1
        n = len(self.w)
        if 1.0 / sum(v * v for v in self.w) < 0.5 * n:
            step, u0 = 1.0 / n, self.rng.random() / n
            cum, i, nx = self.w[0], 0, []
            for k in range(n):
                u = u0 + k * step
                while u > cum and i < n - 1:
                    i += 1
                    cum += self.w[i]
                nx.append(self.x[i] + self.rng.gauss(0.0, 0.25))
            self.x, self.w = nx, [step] * n

    def estimate(self) -> tuple[float, float]:
        m = sum(w * v for w, v in zip(self.w, self.x))
        return m, math.sqrt(sum(w * (v - m) ** 2 for w, v in zip(self.w, self.x)))

    def predictive_sd(self) -> float:
        return math.sqrt(self.estimate()[1] ** 2 + self.rh.scale ** 2)
