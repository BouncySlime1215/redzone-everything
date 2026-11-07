"""[Join API] Put a HIDDEN stick a chosen time behind live, say where it should land, measure where it did, log both.

Nick's goal (Nov 7, 2:07 AM ET): join a chosen spot accurately; which plays to join is the model's job, later.
Measured (overseer, 9 runs on NBA TV, ESPN, FS1, ACCN; targets 60/75/90; label truth):
  * YouTube TV: the overlay scrub with n = round((T - 10) / 15) LEFTs landed T - 3.3 s on average (errors -4.3,
    -3.9, -5.3, -1.6, -4.6, -4.6, -7.7, +1.0, +1.6), so it lands at 15 n + ~11.7 s, +-3 s. Its targets are
    therefore steps of 15 s. Holding the overlay to hit an in-between target was worse (+2..+10 s, one -18.7 s):
    never done here.
  * Prime: pause for the gap (prime_pause), accurate to ~1-2 s at any target.

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

JOIN_DEFAULTS = {"enabled": False, "yttv_step_s": 15.0, "yttv_n_offset_s": 10.0, "yttv_lead_s": 11.7,
                 "yttv_err_s": 3.0, "prime_bias_s": 0.0, "prime_err_s": 2.0, "max_s": 150.0}
APPS = {"youtubetv": "scrub", "prime": "pause"}


def _cfg(cfg, key: str):
    v = cfg.get(f"join.{key}") if cfg is not None and hasattr(cfg, "get") else None
    return JOIN_DEFAULTS[key] if v is None else v


@dataclass
class JoinPlan:
    app: str
    method: str                       # "scrub" (YouTube TV) | "pause" (Prime) | "none"
    target_s: float                   # the behind_live asked for
    presses: int = 0                  # scrub: LEFTs, the probe included
    hold_s: float = 0.0               # pause: seconds paused
    predicted_s: float | None = None  # where it should land (behind live)
    expected_err_s: float | None = None
    reason: str = ""
    bias_used_s: float | None = None  # the lead (scrub) / bias (pause) this plan used: calibration reads it back
    bias_source: str = "shipped"      # "shipped" or the calibration key level (join_calib, join.calib.enabled)


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
        step = float(_cfg(cfg, "yttv_step_s"))
        lead = float(_cfg(cfg, "yttv_lead_s"))
        err = float(_cfg(cfg, "yttv_err_s"))
        n_off = float(_cfg(cfg, "yttv_n_offset_s"))
        if bias is not None:
            # Nick's rule moves with the calibrated lead: at the shipped lead it is exactly round((T - 10) / 15)
            n_off, lead, err, p.bias_source = n_off + (bias.value - lead), bias.value, bias.err_s, bias.source
        p.presses = max(1, int(round((t - before - n_off) / step)))
        p.predicted_s = round(before + step * p.presses + lead, 2)
        p.expected_err_s, p.bias_used_s = err, round(lead, 2)
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
    """The nearest landing a join from the live edge can be expected to hit (YouTube TV: one per 15 s step)."""
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

    def row(self, **extra) -> dict:
        d = {"ts": round(time.time(), 1), **{k: v for k, v in asdict(self.plan).items()},
             "ok": self.ok, "measured_s": self.measured_s, "estimate_s": self.estimate_s, "error_s": self.error_s,
             "pred_error_s": self.pred_error_s, "reason": self.reason[:160], "took_s": round(self.took_s, 2)}
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
    res.took_s = clock() - t0
    extra = {"source": source} if source else {}
    log_join(res, log_path, stick=label, channel=channel, **extra)
    return res
