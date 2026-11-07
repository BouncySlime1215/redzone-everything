"""[Join watchdog + drift monitor] Nick's Nov 7 sync order, items (1) and (3). Flags off; Rule #1 unchanged.

(1) JOIN WATCHDOG. After every entry (redzone/tuners/join.py, or control's scrub entry) the landing is checked
against the plan on the one truth source we have: YouTube TV's overlay time label (yttv_label.LabelProbe), Prime's
overlay timecode (prime_pause). Never media_session: it is wrong after a seek and goes stale (Nov 6-7).

    verify(app, target, predicted, measured) ->  verified     |measured - predicted| <= margin and within the target
                                                               band: the stick IS buffered at `measured`
                                                 off_target   read, but outside the margin -> re-seek once, then live
                                                 out_of_range read, but <= min_behind_s or past max_s + over_s
                                                 unmeasured   no reading -> never buffered (fall back to live)
                                                 join_failed  the entry itself failed closed
                                                 visible      the stick became visible: never pressed again here

  The honest display state (`state` on the Outcome) is "buffered" ONLY for a verified reading. A fallback whose
  relink worked is "live" (a fresh tune lands at live +-0.5 s); a relink that failed is "unknown". Nothing else
  may write control.held: the phone shows control.held, so it cannot show buffered when it is not.

  Margins from the measured errors (overseer's 9 label runs Nov 7: measured - predicted -4.4 .. +4.9 s; Prime pause
  +-1-2 s): youtubetv 6 s, prime 4 s around the PREDICTED landing; and the landing must also be within
  target_band_s of the TARGET (YouTube TV lands on 15 s steps, so the band is half a step + its error).

  Re-seek: Prime short of target -> pause the rest from where it is (the timecode measures the gain); anything else
  -> relink to the live edge first (a known position; #391: a same-channel deep link does NOT reset a stick that is
  behind, so relink() must go through another channel, as control._tune does with Stick.behind_on), then join again.
  At most max_reseeks, then the fallback.

(3) DRIFT MONITOR. After a verified join the playhead plays at 1x, so the position is TRUSTED, not re-read: a read
costs ~7.5 s on YouTube TV (the overlay freezes the picture, so each read also ADDS ~7.5 s behind; 31-min ESPN soak
Nov 7: 82, 89, ... 119 s at 5-min reads) and needs the overlay, so reads are only ever taken on the HIDDEN stick.
    * reads are rare: first after first_s, then the interval grows x backoff up to max_interval_s while it holds;
    * every read's wall time is counted against budget_s_per_h (rolling hour); no budget -> no read;
    * a read is compared with what we believe (last reading + the read's own freeze, read_adds_s, on YouTube TV);
      the depth reads add is planned (Nick 14:11Z), so only drift counts:
          ok        |drift_s| <= drift_margin_s (3 s) and not past join max_s
          reenter   drifted more than that, or the reads have pushed it past max_s
          snapped   at the live edge (<= min_behind_s): the buffer is gone (the plain-pause snap, Nov 6)
          unknown   no reading (program boundary resets the label, banner, OCR): NOT buffered until re-entered
    * reenter / snapped / unknown -> the caller re-enters the buffer off screen (control.label_stale -> _rescrub).

Every verdict is one row in data/joins.jsonl (kind "watchdog" / "drift"): numbers, app, channel and a stick NAME
only, never an address or a frame. Nothing here raises into the switch path.
"""
from __future__ import annotations

import json
import logging
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from redzone.tuners.join import _default_log as _join_log   # one data/joins.jsonl for join, watchdog, drift

log = logging.getLogger("redzone.join_watchdog")

WATCHDOG_DEFAULTS = {
    "enabled": False,
    "margin_s": {"youtubetv": 6.0, "prime": 4.0},        # around the PREDICTED landing
    "target_band_s": {"youtubetv": 10.5, "prime": 5.0},  # around the TARGET (YouTube TV: half a 15 s step + 3 s)
    # [Nick 14:03Z, #405's 16.8 s LEFTs: T120 plans k=5 -> 112.5, landed 112-114, was re-seeked to live] with a plan,
    # the verdict is the PREDICTED margin; the target only catches gross misses (nearest step up to 8.4 s away, the
    # one-LEFT floor ~46 s for T30)
    "target_gross_s": {"youtubetv": 20.0, "prime": 8.0},
    "min_behind_s": 5.0,       # a "buffered" reading at or below this is the live edge
    "max_s": 150.0,            # join.max_s
    "over_s": 15.0,            # a reading past max_s + this is a misread / runaway (ACC Network stopped near 150 s)
    "max_reseeks": 1,
    # [Nick's cross-channel run, Nov 7 13:02Z: 25/26 verified, the 1 miss was a post-commit label read that returned
    # nothing] on "unmeasured" after a committed join, read the label ONCE more before falling back (~7.5 s more
    # behind; the plan and target move by that one extra read). OFF.
    "reprobe_unmeasured": False,
    "fallback": "live",        # live = relink to the live edge (known position); keep = leave it, state unknown
}
DRIFT_DEFAULTS = {
    "enabled": False,
    "first_s": 300.0,          # first check 5 min after a verified join (the soak read every 5 min and held)
    "backoff": 3.0,            # [Nick 14:11Z: it holds, read rarely] then 15 min, then every max_interval_s
    "max_interval_s": 1200.0,  # never trust longer than this without a read (scrub_trust_s, the 20-min proof)
    "budget_s_per_h": 45.0,    # wall seconds of reads per stick per rolling hour (~4 YouTube TV reads at ~10.5 s)
    # [Nick 14:11Z] the depth each read adds is PLANNED (it is in expected_s); only a drift beyond this re-enters.
    # His rerun: +1.0, +1.7 s per read with read_adds 7.5 (= the freeze running ~8.5 s), the stick itself ~+0.5 s.
    "drift_margin_s": 3.0,
    "read_cost_s": {"youtubetv": 10.5, "prime": 8.0},   # wall time of one read (budget; Nick: 9.8, 10.4, 10.1 s)
    "read_adds_s": {"youtubetv": 8.5, "prime": 0.0},    # ...and how much it pushes the picture back (frozen overlay)
}


def _app(app: str) -> str:
    return (app or "").split(":", 1)[0]


def _get(cfg, prefix: str, defaults: dict, key: str, app: str | None = None):
    v = cfg.get(f"{prefix}.{key}") if cfg is not None and hasattr(cfg, "get") else None
    v = defaults[key] if v is None else v
    if isinstance(v, dict):
        d = defaults[key] if isinstance(defaults[key], dict) else {}
        return v.get(_app(app or ""), d.get(_app(app or ""), max(d.values()) if d else 0.0))
    return v


def wcfg(cfg, key: str, app: str | None = None):
    return _get(cfg, "join.watchdog", WATCHDOG_DEFAULTS, key, app)


def dcfg(cfg, key: str, app: str | None = None):
    return _get(cfg, "join.drift", DRIFT_DEFAULTS, key, app)


def enabled(cfg) -> bool:
    return bool(wcfg(cfg, "enabled"))


# ----------------------------------------------------------------------------------------------- (1) verify
@dataclass
class Verdict:
    status: str                    # verified | off_target | out_of_range | unmeasured | join_failed | visible
    action: str                    # accept | reseek | fallback | none
    app: str
    target_s: float
    predicted_s: float | None = None
    measured_s: float | None = None
    off_pred_s: float | None = None    # measured - predicted
    off_target_s: float | None = None  # measured - target
    reason: str = ""

    @property
    def buffered(self) -> bool:
        return self.status == "verified"


def verify(app: str, target_s: float, measured_s: float | None, predicted_s: float | None = None, *,
           join_ok: bool = True, visible: bool = False, reseeks_left: int = 0, cfg=None,
           read_adds_s: float = 0.0) -> Verdict:
    """Is the stick where we meant it to be? Pure: no stick, no clock. See the module docstring.
    predicted_s given (join.plan_join): within margin_s of it, and within target_gross_s of the target (gross misses
    only: the nearest YouTube TV step can sit 8.4 s from the target). predicted_s None (control's scrub entry plans
    its own presses, the re-probe): only the target band applies.
    read_adds_s: how far the measuring read itself pushed the stick back (the YouTube TV label probe freezes the
    picture ~7.5 s and its reading INCLUDES that: Nick's Nov 7 run, planned 71.7 without it, read 79.52). The TARGET
    is moved by it before comparing; predicted_s already includes it (join.plan_join, JoinPlan.read_s)."""
    a = _app(app)
    adds = float(read_adds_s or 0.0)
    pred = float(predicted_s) if predicted_s is not None else float(target_s) + adds
    v = Verdict("unmeasured", "fallback", a, round(float(target_s), 2), round(pred, 2),
                None if measured_s is None else round(float(measured_s), 2))
    if visible:
        v.status, v.action, v.reason = "visible", "none", "the stick is on screen: never pressed or relinked here"
        return v
    if not join_ok:
        v.status, v.reason = "join_failed", "the entry failed closed"
        return v
    if measured_s is None:
        v.reason = "no reading (label / timecode): an estimate is never shown as buffered"
        return v
    m = float(measured_s)
    v.off_pred_s, v.off_target_s = round(m - pred, 2), round(m - float(target_s) - adds, 2)
    if m <= float(wcfg(cfg, "min_behind_s")) or m > float(wcfg(cfg, "max_s")) + float(wcfg(cfg, "over_s")):
        v.status = "out_of_range"
        v.reason = f"read {m:.1f}s behind live: the live edge or a runaway, not a buffer"
    elif (abs(m - pred) > float(wcfg(cfg, "margin_s", a)) or abs(m - float(target_s)) > float(wcfg(cfg, "target_gross_s", a))
          if predicted_s is not None else
          abs(m - float(target_s) - adds) > float(wcfg(cfg, "target_band_s", a))):
        v.status = "off_target"
        v.reason = f"landed {m:.1f}s, planned {pred:.1f}s with the read (target {float(target_s):.0f}s)"
    else:
        v.status, v.action, v.reason = "verified", "accept", f"landed {m:.1f}s (planned {pred:.1f}s)"
        return v
    v.action = "reseek" if reseeks_left > 0 else "fallback"
    return v


@dataclass
class Outcome:
    state: str                         # buffered | live | unknown: what the phone may say
    behind_s: float | None             # the READ seconds behind live when buffered; 0.0 live; None unknown
    target_s: float
    attempts: int = 0
    verdicts: list[dict] = field(default_factory=list)
    fallback: str = ""                 # "" | "live" | "live_failed" | "keep"
    reprobes: int = 0                  # extra label reads after an unmeasured committed join (reprobe_unmeasured)
    took_s: float = 0.0

    @property
    def buffered(self) -> bool:
        return self.state == "buffered"


def log_row(row: dict, path: Path | str | None = None) -> None:
    """One row to data/joins.jsonl (or `path`); path "" = don't log (tests, dry runs)."""
    if path == "":
        return
    try:
        p = Path(path) if path is not None else _join_log()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as f:
            f.write(json.dumps({"ts": round(time.time(), 1), **row}) + "\n")
    except Exception as e:   # logging never breaks a join
        log.warning("watchdog log: %r", e)


async def watched_join(stick, app: str, target_s: float, *, relink: Callable[[], Awaitable[bool]],
                       join_fn: Callable[..., Awaitable[Any]] | None = None, cfg=None,
                       still_hidden: Callable[[], bool] | None = None, log_path: Path | None = None,
                       label: str = "", channel: str = "", clock: Callable[[], float] | None = None,
                       **join_kw) -> Outcome:
    """join -> verify -> (re-seek | fall back) until verified or out of tries. Never raises.
    relink(): put the stick back at the live edge through another channel (a known position); True = done.
    join_fn defaults to redzone.tuners.join.join (its JoinResult: ok, measured_s, plan.predicted_s)."""
    clock = clock or time.monotonic
    t0 = clock()
    still_hidden = still_hidden or (lambda: True)
    if join_fn is None:
        from redzone.tuners import join as _join
        join_fn = _join.join
    a = _app(app)
    max_re = int(wcfg(cfg, "max_reseeks"))
    out = Outcome("unknown", None, round(float(target_s), 2))
    before = float(join_kw.pop("behind_before_s", 0.0) or 0.0)
    v: Verdict | None = None
    try:
        while True:
            out.attempts += 1
            r = await join_fn(stick, app, target_s, cfg=cfg, still_hidden=still_hidden, behind_before_s=before,
                              log_path=log_path, label=label, channel=channel, clock=clock, **join_kw)
            plan = getattr(r, "plan", None)
            # the measuring read's own cost is in plan.predicted_s (join.yttv_read_s, the one place it lives): only
            # the TARGET is moved by it here
            v = verify(a, target_s, getattr(r, "measured_s", None), getattr(plan, "predicted_s", None),
                       read_adds_s=float(getattr(plan, "read_s", 0.0) or 0.0),
                       # a join that READ an off-target landing is ok=False (join.py) but measured: re-seek it
                       join_ok=bool(getattr(r, "ok", False)) or getattr(r, "verdict", "") in ("off_target",
                                                                                            "out_of_range"),
                       visible=not still_hidden(),
                       reseeks_left=max_re - (out.attempts - 1), cfg=cfg)
            out.verdicts.append(asdict(v))
            reader = join_kw.get("read_behind")
            if v.status == "unmeasured" and bool(wcfg(cfg, "reprobe_unmeasured")) and bool(getattr(r, "ok", False)) \
                    and reader is not None and plan is not None and not out.reprobes and still_hidden():
                # the join committed but its one label read came back empty: read once more. Whether that empty read
                # froze the picture is unknown (Nick's #404 run, Nov 7 13:19Z: ESPN 65.83 and ESPN2 55.44 for target
                # 60, plan 64.2 with one read), so the plan is not used: the TARGET band alone judges it, centred
                # half a read deep (one read certain, the other unknown).
                out.reprobes += 1
                try:
                    again = await reader(stick)
                except Exception as e:
                    log.warning("re-probe failed: %r", e)
                    again = None
                rs = float(getattr(plan, "read_s", 0.0) or 0.0)
                v = verify(a, target_s, again, None, read_adds_s=rs / 2.0,
                           visible=not still_hidden(), reseeks_left=max_re - (out.attempts - 1), cfg=cfg)
                v.reason = f"re-probe: {v.reason}"
                out.verdicts.append(asdict(v))
            if v.action == "accept":
                out.state, out.behind_s = "buffered", v.measured_s
                break
            if v.action == "none":
                out.state = "unknown"
                break
            if v.action == "reseek":
                # Prime short of target: pause the rest from where it is (measured); anything else: from live
                if a == "prime" and v.measured_s is not None and v.off_target_s is not None and v.off_target_s < 0:
                    before = float(v.measured_s)
                    continue
                if not still_hidden():
                    out.state = "unknown"
                    break
                if await relink():
                    before = 0.0
                    continue
                out.state, out.fallback = "unknown", "live_failed"
                break
            # fallback
            if str(wcfg(cfg, "fallback")) == "keep" or not still_hidden():
                out.state, out.fallback = "unknown", "keep"
                break
            ok = await relink()
            out.state, out.behind_s, out.fallback = ("live", 0.0, "live") if ok else ("unknown", None, "live_failed")
            break
    except Exception as e:   # a bug must still fail closed: never claim a buffer
        log.warning("watched_join failed: %r", e)
        out.state, out.behind_s = "unknown", None
        out.verdicts.append({"status": "exception", "reason": f"{e!r}"[:160]})
    out.took_s = round(clock() - t0, 2)
    log_row({"kind": "watchdog", "app": a, "stick": label, "channel": channel, "target_s": out.target_s,
             "state": out.state, "behind_s": out.behind_s, "attempts": out.attempts, "fallback": out.fallback,
             "reprobes": out.reprobes,
             "status": [d.get("status") for d in out.verdicts],
             "off_pred_s": [d.get("off_pred_s") for d in out.verdicts], "took_s": out.took_s}, log_path)
    return out


# ------------------------------------------------------------------------------------------- (3) drift monitor
@dataclass
class DriftVerdict:
    action: str                 # ok | reenter | snapped | unknown | skip
    expected_s: float | None = None
    measured_s: float | None = None
    drift_s: float | None = None
    reason: str = ""


@dataclass
class _Track:
    app: str
    target_s: float
    behind_s: float
    at: float
    interval: float
    next_at: float
    reads: int = 0
    read_cost_s: float = 0.0
    drifts: list = field(default_factory=list)


class DriftMonitor:
    """Rare, budgeted reads of a verified buffer (hidden stick only). Clock injected; the reading itself is the
    caller's (LabelProbe.probe on YouTube TV, prime_pause.ocr_timecode pair on Prime). See the module docstring."""

    def __init__(self, cfg=None, clock: Callable[[], float] | None = None, log_path: Path | None = None):
        self.cfg = cfg
        self.clock = clock or time.monotonic
        self.log_path = log_path
        self.tracks: dict[str, _Track] = {}
        self.spent: dict[str, deque] = {}          # key -> deque[(at, wall s of a read)]
        self.totals = {"reads": 0, "read_cost_s": 0.0, "ok": 0, "reenter": 0, "snapped": 0, "unknown": 0,
                       "no_budget": 0}

    def anchor(self, key: str, app: str, target_s: float, behind_s: float, now: float | None = None) -> None:
        """A verified join (or a re-entry): trust the playhead from here, first check after first_s."""
        now = self.clock() if now is None else now
        first = float(dcfg(self.cfg, "first_s"))
        self.tracks[key] = _Track(_app(app), float(target_s), float(behind_s), now, first, now + first)

    def forget(self, key: str) -> None:
        self.tracks.pop(key, None)

    def believed(self, key: str) -> float | None:
        t = self.tracks.get(key)
        return None if t is None else t.behind_s

    def budget_left(self, key: str, now: float | None = None) -> float:
        now = self.clock() if now is None else now
        q = self.spent.setdefault(key, deque())
        while q and now - q[0][0] > 3600.0:
            q.popleft()
        return float(dcfg(self.cfg, "budget_s_per_h")) - sum(c for _, c in q)

    def due(self, key: str, *, hidden: bool, now: float | None = None) -> bool:
        """A read is due: tracked, hidden (never read the stick on screen), interval elapsed, budget for one."""
        now = self.clock() if now is None else now
        t = self.tracks.get(key)
        if t is None or not hidden or now < t.next_at:
            return False
        if self.budget_left(key, now) < float(dcfg(self.cfg, "read_cost_s", t.app)):
            self.totals["no_budget"] += 1
            return False
        return True

    def record(self, key: str, measured_s: float | None, *, cost_s: float | None = None,
               now: float | None = None, channel: str = "", label: str = "") -> DriftVerdict:
        """One read was taken: what it says and what to do. Spends its cost; reschedules on ok."""
        now = self.clock() if now is None else now
        t = self.tracks.get(key)
        if t is None:
            return DriftVerdict("skip", reason="not tracked")
        cost = float(dcfg(self.cfg, "read_cost_s", t.app)) if cost_s is None else float(cost_s)
        self.spent.setdefault(key, deque()).append((now, cost))
        t.reads += 1
        t.read_cost_s += cost
        self.totals["reads"] += 1
        self.totals["read_cost_s"] = round(self.totals["read_cost_s"] + cost, 2)
        expected = round(t.behind_s + float(dcfg(self.cfg, "read_adds_s", t.app)), 2)
        v = DriftVerdict("unknown", expected, None if measured_s is None else round(float(measured_s), 2))
        if measured_s is None:
            v.reason = "no reading (program boundary, banner, OCR): not buffered until re-entered"
        else:
            m = float(measured_s)
            v.drift_s = round(m - expected, 2)
            t.drifts.append(v.drift_s)
            if m <= float(wcfg(self.cfg, "min_behind_s")):
                v.action, v.reason = "snapped", f"read {m:.1f}s: back at the live edge"
            elif abs(v.drift_s) > float(dcfg(self.cfg, "drift_margin_s")):
                # [Nick 14:11Z] the depth the reads add is planned (in expected); only real drift re-enters
                v.action, v.reason = "reenter", f"drift {v.drift_s:+.1f}s (read {m:.1f}s, expected {expected:.1f}s)"
            elif m > float(wcfg(self.cfg, "max_s")):
                v.action, v.reason = "reenter", f"read {m:.1f}s: deeper than join.max_s after {t.reads} reads"
            else:
                v.action, v.reason = "ok", f"held (drift {v.drift_s:+.1f}s)"
                t.behind_s, t.at = m, now
                t.interval = min(t.interval * float(dcfg(self.cfg, "backoff")), float(dcfg(self.cfg, "max_interval_s")))
                t.next_at = now + t.interval
        self.totals[v.action] += 1
        if v.action != "ok":
            self.forget(key)        # the caller re-enters; a new anchor comes with the verified re-entry
        log_row({"kind": "drift", "app": t.app, "stick": label, "channel": channel, "target_s": t.target_s,
                 "action": v.action, "expected_s": v.expected_s, "measured_s": v.measured_s, "drift_s": v.drift_s,
                 "read_cost_s": round(cost, 2), "reads": t.reads}, self.log_path)
        return v

    def report(self) -> dict:
        drifts = [d for t in self.tracks.values() for d in t.drifts]
        return {**self.totals, "tracked": len(self.tracks),
                "max_abs_drift_s": max((abs(d) for d in drifts), default=None)}
