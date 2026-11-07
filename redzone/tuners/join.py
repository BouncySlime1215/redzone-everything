"""[Join API] Put a HIDDEN stick a chosen time behind live, say where it should land, measure where it did, log both.

Nick's goal (Nov 7, 2:07 AM ET): join a chosen spot accurately; which plays to join is the model's job, later.
Measured (overseer, 9 runs on NBA TV, ESPN, FS1, ACCN; targets 60/75/90; label truth):
  * YouTube TV: the overlay scrub with n = round((T - 10) / 15) LEFTs landed T - 3.3 s on average (errors -4.3,
    -3.9, -5.3, -1.6, -4.6, -4.6, -7.7, +1.0, +1.6), so it lands at 15 n + ~11.7 s, +-3 s. Its targets are
    therefore steps of 15 s. Holding the overlay to hit an in-between target was worse (+2..+10 s, one -18.7 s):
    never done here.
  * Prime: pause for the gap (prime_pause), accurate to ~1-2 s at any target.

  * [Nick's depth sweep, Nov 7 13:48Z, #403 RIGHT probe, retuned before each join] the landing grows 16.8 s per real
    LEFT k, not 15: each LEFT also keeps the overlay (frozen picture) up ~1.4-1.8 s longer. landed = 16.8 k + 28.5 (the
    label read's 7.5 s included) fits k = 1, 2, 4, 6 (45.3, 62.1, 95.7, 129.3) within 1 s; 15 n + 19.2 drifted -3.9 at
    45 to +4.9 at 120 (one T120 off_target). k = round((T - 28.5) / 16.8), at least 1: the shallowest scrub join is
    ~45 s (T30 used to plan zero LEFTs, a no-op scrub that landed at 10-14 s).

plan_join(app, target_s) says how (presses or hold) and the predicted landing. join(stick, app, target_s, ...) runs
it on the hidden stick (yttv_seek.enter_buffer with that fixed press count, or prime_pause.enter_buffer_pause) and
returns a JoinResult with target, predicted, measured (label / timecode; None when nothing measured) and the errors,
appended as one row to data/joins.jsonl (numbers and channel names only: no addresses, no frames). Never raises.
Flag join.enabled (off): the caller checks it. Rule #1 is unchanged: a flip still waits for a dead ball.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

log = logging.getLogger("redzone.join")

JOIN_DEFAULTS = {"enabled": False, "yttv_step_s": 15.0, "yttv_left_s": 16.8, "yttv_lead_s": 21.0,
                 "yttv_err_s": 3.0, "prime_bias_s": 0.0, "prime_err_s": 2.0, "max_s": 150.0, "yttv_read_s": 7.5}
APPS = {"youtubetv": "scrub", "prime": "pause"}


def _cfg(cfg, key: str):
    v = cfg.get(f"join.{key}") if cfg is not None and hasattr(cfg, "get") else None
    return JOIN_DEFAULTS[key] if v is None else v


@dataclass
class JoinPlan:
    app: str
    method: str                       # "scrub" (YouTube TV) | "pause" (Prime) | "none"
    target_s: float                   # the behind_live asked for
    presses: int = 0                  # scrub: keys for yttv_seek's fixed count (the probe + lefts)
    lefts: int = 0                    # scrub: real LEFTs that move the knob (k; the probe at live moves nothing)
    hold_s: float = 0.0               # pause: seconds paused
    predicted_s: float | None = None  # where it should land (behind live)
    expected_err_s: float | None = None
    reason: str = ""
    bias_used_s: float | None = None  # the lead (scrub: landing - 16.8 k - read) / bias (pause): calibration reads it
    bias_source: str = "shipped"      # "shipped" or the calibration key level (join_calib, join.calib.enabled)
    read_s: float = 0.0               # what the measuring read itself adds (YouTube TV label probe: frozen overlay)


def plan_join(app: str, target_s: float, behind_before_s: float = 0.0, cfg=None, bias=None) -> JoinPlan:
    """How to get a stick that is `behind_before_s` behind live to `target_s`, and where that should land.
    bias: a join_calib.Bias for this channel and stick (None = the shipped lead / bias, i.e. calibration off)."""
    app = app.split(":", 1)[0]
    t, before = float(target_s), float(behind_before_s)
    method = APPS.get(app, "none")
    p = JoinPlan(app=app, method=method, target_s=round(t, 2))
    if method == "none":
        p.reason = f"no join method for {app}"
        return p
    if t > float(_cfg(cfg, "max_s")):
        p.method, p.reason = "none", f"target {t:.0f}s is past join.max_s"
        return p
    if t - before < 1.0:
        p.predicted_s, p.expected_err_s, p.reason = before, 0.0, "already there (nothing to do)"
        return p
    if method == "scrub":
        per = float(_cfg(cfg, "yttv_left_s"))
        lead = float(_cfg(cfg, "yttv_lead_s"))
        err = float(_cfg(cfg, "yttv_err_s"))
        # [Nick's stick tests, Nov 7 11:35Z / 11:40Z] the label probe that measures the join freezes the picture
        # ~7.5 s and its reading includes that. The ONE place this cost lives: the canary and the watchdog both read
        # predicted_s; calibration reads read_s.
        p.read_s = float(_cfg(cfg, "yttv_read_s"))
        if bias is not None:
            lead, err, p.bias_source = bias.value, bias.err_s, bias.source
        # [Nick's depth sweep, Nov 7] landed = before + 16.8 k + lead + read (lead 21.0 + read 7.5 = his 28.5).
        # At least one LEFT: k = 0 is a scrub that seeks nothing (T30 landed 10-14 s, off_target).
        p.lefts = max(1, int(round((t - before - lead - p.read_s) / per)))
        p.presses = p.lefts + 1          # yttv_seek sends a fixed count n as the probe + (n - 1) LEFTs
        p.predicted_s = round(before + per * p.lefts + lead + p.read_s, 2)
        p.expected_err_s, p.bias_used_s = err, round(lead, 2)
        if p.predicted_s - t > per / 2:
            p.reason = f"shallowest scrub join is ~{p.predicted_s:.0f}s (one LEFT)"
    else:
        b = float(_cfg(cfg, "prime_bias_s"))
        err = float(_cfg(cfg, "prime_err_s"))
        p.hold_s = round(t - before, 2)
        if bias is not None:
            # calibrated: the pause lands at before + hold + bias, so hold for the gap less the bias
            b, err, p.bias_source = bias.value, bias.err_s, bias.source
            p.hold_s = round(max(0.0, t - before - b), 2)
        p.predicted_s = round(before + p.hold_s + b, 2)
        p.expected_err_s, p.bias_used_s = err, round(b, 2)
    return p


def achievable(app: str, target_s: float, cfg=None) -> float | None:
    """The nearest landing a join from the live edge can be expected to hit (YouTube TV: one per 16.8 s LEFT,
    the shallowest ~45 s: a target under ~37 s returns that, never a no-op)."""
    p = plan_join(app, target_s, 0.0, cfg)
    return p.predicted_s if p.method != "none" else None


@dataclass
class JoinResult:
    ok: bool
    plan: JoinPlan
    measured_s: float | None = None   # behind live after the join, as READ (label / Prime timecode); None = unread
    estimate_s: float | None = None   # the helper's own estimate when nothing was read (never used as truth)
    error_s: float | None = None      # measured - target
    pred_error_s: float | None = None  # measured - predicted (what calibrates yttv_lead_s / prime_bias_s)
    reason: str = ""
    took_s: float = 0.0
    steps: list[str] = field(default_factory=list)
    verdict: str = ""                  # join_watchdog.verify status of the READ landing ("" = nothing read)

    def row(self, **extra) -> dict:
        d = {"ts": round(time.time(), 1), **{k: v for k, v in asdict(self.plan).items()},
             "ok": self.ok, "measured_s": self.measured_s, "estimate_s": self.estimate_s, "error_s": self.error_s,
             "pred_error_s": self.pred_error_s, "reason": self.reason[:160], "took_s": round(self.took_s, 2),
             "verdict": self.verdict}
        d.update(extra)
        return d


def _default_log() -> Path:
    """data/joins.jsonl of the checkout the code runs from, or $RZ_JOINS_LOG. [Nick's stick test, Nov 7] a run from a
    scratch worktree wrote its rows to THAT worktree's data/, not the Mac repo's: set RZ_JOINS_LOG to collect them."""
    import os
    env = os.environ.get("RZ_JOINS_LOG", "").strip()
    if env:
        return Path(env).expanduser()
    from redzone.config import DATA
    return DATA / "joins.jsonl"


def log_join(res: JoinResult, path: Path | None = None, **extra) -> None:
    try:
        path = Path(path) if path is not None else _default_log()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(res.row(**extra)) + "\n")
    except Exception as e:   # logging never breaks a join
        log.warning("join log: %r", e)


async def join(stick, app: str, target_s: float, *, cfg=None, pkg: str | None = None,
               still_hidden: Callable[[], bool] | None = None,
               relink: Callable[[], Awaitable[bool]] | None = None,
               read_behind: Callable[[Any], Awaitable[float | None]] | None = None,
               read_overlay=None, read_timecode=None, behind_before_s: float = 0.0,
               log_path: Path | None = None, label: str = "", channel: str = "",
               clock: Callable[[], float] | None = None, calib=None, source: str = "") -> JoinResult:
    """Join `target_s` behind live on a hidden stick of `app`. See the module docstring. Never raises.
    label/channel go to the log row (a stick name such as "stickB", never its address).
    calib: a join_calib.JoinCalib; None loads one from the join log when join.calib.enabled (OFF), else the plan
    uses the shipped lead / bias. source tags the row (e.g. "canary")."""
    clock = clock or time.monotonic
    t0 = clock()
    bias = None
    try:
        from redzone.tuners import join_calib
        if calib is None and join_calib.enabled(cfg):
            calib = join_calib.JoinCalib.load(log_path, cfg)
        if calib is not None:
            bias = calib.bias(app, channel, label)
    except Exception as e:   # calibration never blocks a join: shipped values
        log.warning("join calibration: %r", e)
        bias = None
    plan = plan_join(app, target_s, behind_before_s, cfg, bias)
    res = JoinResult(ok=False, plan=plan)
    try:
        if plan.method == "none":
            res.reason = plan.reason
        elif plan.predicted_s is not None and plan.presses == 0 and plan.hold_s == 0.0:
            res.ok, res.reason, res.measured_s = True, plan.reason, None
        elif plan.method == "scrub":
            from redzone.tuners import yttv_seek
            kw = {"read_overlay": read_overlay} if read_overlay is not None else {}
            r = await yttv_seek.enter_buffer(stick, plan.target_s, cfg=cfg, pkg=pkg, still_hidden=still_hidden,
                                             read_behind=read_behind, relink=relink,
                                             behind_before_s=behind_before_s, presses=plan.presses, clock=clock,
                                             **kw)
            res.ok, res.reason, res.steps = bool(r.ok), r.reason, list(r.steps)
            res.measured_s = r.behind_s if r.ok else None
            res.estimate_s = None if r.measured else r.behind_live_s
        else:
            from redzone.tuners import prime_pause
            kw = {"read_timecode": read_timecode} if read_timecode is not None else {}
            r = await prime_pause.enter_buffer_pause(stick, plan.hold_s, cfg=cfg, pkg=pkg, still_hidden=still_hidden,
                                                     relink=relink, behind_before_s=behind_before_s, clock=clock,
                                                     **kw)
            res.ok, res.reason, res.steps = bool(r.ok), r.reason, list(r.steps)
            res.measured_s = r.behind_live_s if (r.ok and r.measured) else None
            res.estimate_s = None if r.measured else r.behind_live_s
    except Exception as e:
        log.warning("join failed: %r", e)
        res.ok, res.reason = False, f"exception: {e!r}"[:160]
    if res.measured_s is not None:
        res.measured_s = round(float(res.measured_s), 2)
        res.error_s = round(res.measured_s - plan.target_s, 2)
        if plan.predicted_s is not None:
            res.pred_error_s = round(res.measured_s - plan.predicted_s, 2)
        # [Nick's canary, Nov 7 11:50Z] a LOST PRESS landed one 15 s step short (52.12 vs 64.2) and the row still said
        # ok: true. ok now means "landed where the plan said", as read: off by more than the watchdog margin -> False.
        try:
            from redzone.tuners import join_watchdog as jw
            v = jw.verify(app, plan.target_s, res.measured_s, plan.predicted_s, read_adds_s=plan.read_s, cfg=cfg)
            res.verdict = v.status
            if res.ok and not v.buffered:
                short = plan.method == "scrub" and res.pred_error_s is not None and res.pred_error_s <= -float(
                    _cfg(cfg, "yttv_step_s")) + float(jw.wcfg(cfg, "margin_s", app))
                res.ok = False
                res.reason = (f"{v.status}: {v.reason}" + (" -- about one 15 s step short: a lost press?" if short
                                                           else ""))[:160]
        except Exception as e:   # the verdict never breaks a join; ok stays as the keys said
            log.warning("join verdict: %r", e)
    res.took_s = clock() - t0
    extra = {"source": source} if source else {}
    log_join(res, log_path, stick=label, channel=channel, **extra)
    return res
