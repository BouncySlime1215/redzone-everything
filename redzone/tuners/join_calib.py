"""[Join self-calibration, Nick's Nov 7 order item 5] Per channel and stick: join-error and delay stats, and a
bias that updates itself from data/joins.jsonl, inside hard bounds. OFF (join.calib.enabled false).

What is calibrated (the only thing a join controls):
  * YouTube TV: the LEAD of a scrub. A join of n LEFTs from `before` lands at before + 15 n + lead. The shipped lead
    is 11.7 s (Nick's 9 label-measured runs, Nov 7: errors -4.3 -3.9 -5.3 -1.6 -4.6 -4.6 -7.7 +1.0 +1.6 at targets
    of 60/75/90 -> T - 3.3 s). The calibrated lead moves Nick's n rule with it: n = round((T - before - n_offset')
    / 15), n_offset' = n_offset + (lead' - lead), so at the shipped lead the plan is exactly Nick's rule.
  * Prime: the BIAS of a pause. A hold of h s from `before` lands at before + h + bias; the calibrated bias shortens
    or lengthens the hold so the landing is the target (shipped bias 0, accurate to ~1-2 s).

Each join row with a MEASURED landing (label / timecode, never the helper's own estimate and never media_session,
which is wrong after a seek) gives one observation: lead_seen = pred_error_s + the lead the plan used.

Estimate, per key, most specific first:  app:channel:stick  ->  app:channel  ->  app  ->  shipped default.
  1. the last `window` measured rows of that key, oldest dropped first;
  2. gross rows (|pred_error| > gross_s, e.g. the -18.7 s overlay hold, a program-boundary label reset, a stick that
     was not at the live edge: "asked 90, read 23") are counted and reported but never move the bias;
  3. the rest are clipped to parent +- clip_k * expected error (one bad read can't drag it);
  4. shrunk toward the parent with prior_n pseudo-rows: est = (sum + prior_n * parent) / (n + prior_n), so one join
     on a new channel moves it a fifth of the way, ten joins most of the way;
  5. clamped to shipped +- max_shift_s (YouTube TV 5 s, Prime 3 s). The expected error is the residuals' robust sd
     (1.4826 MAD), clamped to [err_min_s, err_max_s].
A key whose newest `shift_n` rows all sit more than shift_s from its estimate on the same side raises a SHIFT alert:
something changed (an app update, a new overlay, a stick swap). The bias does NOT chase it on its own beyond the
bounds; the canary and the night report say it loud.

Delay stats: rows may carry d_edge_s (the channel's live edge behind real life, measured where the caller has it).
They are summarised per channel and stick the same way (median, robust sd, n) with the shipped prior 25.9 s for
on-time YouTube TV channels, bounded to [edge_min_s, edge_max_s]. Info for the night report; the delay model's own
calibrated edge (redzone/calibration.py delay_s:<target>, calib.auto) is unchanged.

Numbers and channel / stick NAMES only. A stick address never enters a key: hosts are mapped to "stick?" here.
"""
from __future__ import annotations

import json
import logging
import re
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("redzone.join_calib")

CALIB_DEFAULTS = {
    "enabled": False,
    "window": 20,          # newest measured rows per key
    "max_age_days": 21,    # older rows are ignored (UI / app changes)
    "prior_n": 4,          # pseudo-rows of the parent estimate
    "gross_s": 12.0,       # |pred_error| above this: counted, never used
    "clip_k": 2.0,         # residuals clipped to parent +- clip_k * expected error
    "shift_n": 3,          # newest rows all off the same side ...
    "shift_s": 5.0,        # ... by more than this -> SHIFT alert
    "max_shift_s": {"youtubetv": 5.0, "prime": 3.0},   # calibrated value stays within shipped +- this
    "err_min_s": 1.0,
    "err_max_s": 6.0,
    "edge_prior_s": 25.9,  # D_edge of an on-time YouTube TV channel (Nov 7)
    "edge_min_s": 15.0,
    "edge_max_s": 45.0,
    "use_canary": True,    # the daily canary's joins count (they are real joins on the hidden stick)
}
SHIPPED = {"youtubetv": ("yttv_lead_s", 11.7, "yttv_err_s", 3.0), "prime": ("prime_bias_s", 0.0, "prime_err_s", 2.0)}
_ADDR = re.compile(r"\d+\.\d+\.\d+\.\d+|:\d{2,5}$")


def _ccfg(cfg, key: str):
    v = cfg.get(f"join.calib.{key}") if cfg is not None and hasattr(cfg, "get") else None
    return CALIB_DEFAULTS[key] if v is None else v


def enabled(cfg) -> bool:
    return bool(_ccfg(cfg, "enabled"))


def shipped(app: str, cfg=None) -> tuple[float, float]:
    """(lead/bias, expected error) from the join config: what a plan uses with calibration off."""
    from redzone.tuners.join import _cfg as jcfg
    k_b, _d_b, k_e, _d_e = SHIPPED[app]
    return float(jcfg(cfg, k_b)), float(jcfg(cfg, k_e))


def safe_name(s) -> str:
    """A stick / channel name for a key. Anything that looks like an address becomes 'stick?'."""
    s = str(s or "").strip()
    return "stick?" if (not s or _ADDR.search(s)) else s[:32]


def observation(row: dict, cfg=None) -> float | None:
    """The lead (YouTube TV) / bias (Prime) a measured row saw, or None when the row measured nothing."""
    app = str(row.get("app") or "").split(":", 1)[0]
    # an off-target READ landing is ok=False since #401 (lost press guard) but still a measurement: the gross filter
    # below decides whether it is used
    read_ok = row.get("ok") or row.get("verdict") in ("off_target", "out_of_range")
    if app not in SHIPPED or not read_ok or row.get("measured_s") is None or row.get("pred_error_s") is None:
        return None
    if row.get("method") not in ("scrub", "pause"):
        return None
    used = row.get("bias_used_s")
    if used is None:   # rows logged before calibration existed used the shipped value
        used = shipped(app, cfg)[0]
    lead = float(row["pred_error_s"]) + float(used)
    if app == "youtubetv" and row.get("method") == "scrub" and row.get("read_s") is None:
        # rows logged before join.yttv_read_s: their prediction left the label read's own ~7.5 s out, so their
        # error carries it. Take it off, or the lead learns the read and the plan (which now adds it) counts it twice.
        from redzone.tuners.join import _cfg as _jcfg
        lead -= float(_jcfg(cfg, "yttv_read_s"))
    return lead


@dataclass
class KeyStats:
    key: str
    n: int = 0                  # measured rows used (after gross removal)
    gross: int = 0              # measured rows too far off to use
    unmeasured: int = 0         # joins with no label / timecode read
    failed: int = 0             # joins that did not complete
    value: float | None = None  # calibrated lead / bias
    err_s: float | None = None
    source: str = "shipped"
    mean_error_s: float | None = None    # measured - target, the number Nick reads
    mean_abs_pred_s: float | None = None
    shift: str = ""             # "" or why a SHIFT alert is up
    obs: list[float] = field(default_factory=list, repr=False)


@dataclass
class Bias:
    value: float
    err_s: float
    source: str          # "shipped" | "app" | "app:channel" | "app:channel:stick"
    n: int
    clamped: bool = False


class JoinCalib:
    """Calibration state built from join rows. Pure: give it rows (or a path) and a config."""

    def __init__(self, rows: list[dict] | None = None, cfg=None, now: float | None = None):
        self.cfg = cfg
        self.now = time.time() if now is None else float(now)
        self.rows = [r for r in (rows or []) if self._keep(r)]
        self._by: dict[str, list[dict]] = {}
        for r in self.rows:
            app = str(r.get("app") or "").split(":", 1)[0]
            if app not in SHIPPED:
                continue
            ch, st = safe_name(r.get("channel")) if r.get("channel") else "", safe_name(r.get("stick"))
            for k in (app, f"{app}:{ch}" if ch else None, f"{app}:{ch}:{st}" if ch else None):
                if k:
                    self._by.setdefault(k, []).append(r)
        self._memo: dict[str, KeyStats] = {}

    @classmethod
    def load(cls, path: Path | str | None = None, cfg=None, now: float | None = None) -> "JoinCalib":
        if path is None:
            from redzone.config import DATA
            path = DATA / "joins.jsonl"
        rows = []
        try:
            for line in Path(path).read_text().splitlines():
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
        except OSError:
            pass
        return cls(rows, cfg, now)

    def _keep(self, r: dict) -> bool:
        ts = r.get("ts")
        if isinstance(ts, (int, float)) and self.now - ts > float(_ccfg(self.cfg, "max_age_days")) * 86400:
            return False
        if r.get("source") == "canary" and not _ccfg(self.cfg, "use_canary"):
            return False
        return True

    # ---------------------------------------------------------------- estimates
    def _parent(self, key: str) -> tuple[float, float, str, int]:
        parts = key.split(":")
        app = parts[0]
        if len(parts) == 1:
            v, e = shipped(app, self.cfg)
            return v, e, "shipped", 0
        up = self.stats(":".join(parts[:-1]))
        if up.value is None:
            return self._parent(":".join(parts[:-1]))
        return up.value, up.err_s, up.source, up.n

    def stats(self, key: str) -> KeyStats:
        if key in self._memo:
            return self._memo[key]
        app = key.split(":", 1)[0]
        ks = KeyStats(key=key)
        p_val, p_err, p_src, _ = self._parent(key)
        ks.value, ks.err_s, ks.source = p_val, p_err, p_src
        rows = self._by.get(key, [])
        meas, errs, abs_pred = [], [], []
        for r in rows:
            if not r.get("ok") and r.get("verdict") not in ("off_target", "out_of_range"):
                ks.failed += 1
                continue
            o = observation(r, self.cfg)
            if o is None:
                ks.unmeasured += 1
                continue
            if abs(float(r["pred_error_s"])) > float(_ccfg(self.cfg, "gross_s")):
                ks.gross += 1
                continue
            meas.append(o)
            abs_pred.append(abs(float(r["pred_error_s"])))
            if r.get("error_s") is not None:
                errs.append(float(r["error_s"]))
        meas = meas[-int(_ccfg(self.cfg, "window")):]
        ks.obs = meas
        ks.n = len(meas)
        if errs:
            ks.mean_error_s = round(statistics.fmean(errs[-len(meas):] if meas else errs), 2)
        if abs_pred:
            ks.mean_abs_pred_s = round(statistics.fmean(abs_pred[-len(meas):] if meas else abs_pred), 2)
        if meas:
            clip = float(_ccfg(self.cfg, "clip_k")) * p_err
            xs = [min(p_val + clip, max(p_val - clip, x)) for x in meas]
            k = float(_ccfg(self.cfg, "prior_n"))
            est = (sum(xs) + k * p_val) / (len(xs) + k)
            ship, ship_err = shipped(app, self.cfg)
            lim = float((_ccfg(self.cfg, "max_shift_s") or {}).get(app, 3.0))
            ks.value = round(min(ship + lim, max(ship - lim, est)), 2)
            if len(xs) >= 3:
                med = statistics.median(xs)
                sd = 1.4826 * statistics.median(abs(x - med) for x in xs)
                w = len(xs) / (len(xs) + k)          # shrink the spread toward the parent's, too
                sd = w * sd + (1 - w) * p_err
            else:
                sd = p_err
            ks.err_s = round(min(float(_ccfg(self.cfg, "err_max_s")), max(float(_ccfg(self.cfg, "err_min_s")), sd)), 2)
            ks.source = ("app", "app:channel", "app:channel:stick")[min(2, key.count(":"))]
            sn, ss = int(_ccfg(self.cfg, "shift_n")), float(_ccfg(self.cfg, "shift_s"))
            tail = meas[-sn:]
            if len(tail) >= sn and (all(x - ks.value > ss for x in tail) or all(ks.value - x > ss for x in tail)):
                side = "later" if tail[-1] > ks.value else "earlier"
                ks.shift = (f"last {sn} joins landed {side} than the calibrated {ks.value:.1f} s by > {ss:g} s "
                            f"(app update / overlay change / stick swap?)")
        self._memo[key] = ks
        return ks

    def bias(self, app: str, channel: str = "", stick: str = "") -> Bias:
        """The lead (YouTube TV) / bias (Prime) and expected error a plan should use for this channel and stick."""
        app = app.split(":", 1)[0]
        if app not in SHIPPED:
            return Bias(0.0, 0.0, "shipped", 0)
        ch = safe_name(channel) if channel else ""
        key = f"{app}:{ch}:{safe_name(stick)}" if ch else app
        ks = self.stats(key)
        ship = shipped(app, self.cfg)[0]
        lim = float((_ccfg(self.cfg, "max_shift_s") or {}).get(app, 3.0))
        return Bias(ks.value, ks.err_s, ks.source, ks.n, clamped=abs(ks.value - ship) >= lim - 1e-9)

    # ---------------------------------------------------------------- reporting
    def keys(self) -> list[str]:
        return sorted(self._by)

    def table(self) -> list[dict]:
        out = []
        for k in self.keys():
            s = self.stats(k)
            out.append({"key": k, "n": s.n, "gross": s.gross, "unmeasured": s.unmeasured, "failed": s.failed,
                        "value_s": s.value, "err_s": s.err_s, "source": s.source,
                        "mean_error_s": s.mean_error_s, "mean_abs_pred_s": s.mean_abs_pred_s, "shift": s.shift})
        return out

    def alerts(self) -> list[str]:
        return [f"{k}: {self.stats(k).shift}" for k in self.keys() if self.stats(k).shift]

    def edge_stats(self) -> list[dict]:
        """D_edge per channel and per channel:stick, from rows that carry d_edge_s (bounded, robust)."""
        lo, hi = float(_ccfg(self.cfg, "edge_min_s")), float(_ccfg(self.cfg, "edge_max_s"))
        by: dict[str, list[float]] = {}
        for r in self.rows:
            d = r.get("d_edge_s")
            if not isinstance(d, (int, float)) or not (lo <= d <= hi) or not r.get("channel"):
                continue
            ch, st = safe_name(r.get("channel")), safe_name(r.get("stick"))
            for k in (ch, f"{ch}:{st}"):
                by.setdefault(k, []).append(float(d))
        out = []
        for k in sorted(by):
            xs = by[k]
            med = statistics.median(xs)
            sd = 1.4826 * statistics.median(abs(x - med) for x in xs) if len(xs) >= 3 else None
            out.append({"key": k, "n": len(xs), "d_edge_s": round(med, 2), "sd_s": None if sd is None else round(sd, 2),
                        "vs_prior_s": round(med - float(_ccfg(self.cfg, "edge_prior_s")), 2)})
        return out


def replay(rows: list[dict], cfg=None) -> dict:
    """Online, out-of-sample check of the calibrator: each row is predicted with only the rows before it, the way
    the live app would have. Returns mean |pred error| shipped vs calibrated, and the per-row trace."""
    trace, before = [], []
    for r in rows:
        o = observation(r, cfg)
        if o is None:
            before.append(r)
            continue
        app = str(r.get("app")).split(":", 1)[0]
        used = r.get("bias_used_s")
        used = shipped(app, cfg)[0] if used is None else float(used)
        b = JoinCalib(before, cfg, now=r.get("ts") or time.time()).bias(app, r.get("channel") or "", r.get("stick") or "")
        trace.append({"seen": round(o, 2), "shipped_err": round(o - shipped(app, cfg)[0], 2),
                      "calib_err": round(o - b.value, 2), "calib_value": b.value, "source": b.source})
        before.append(r)
    if not trace:
        return {"n": 0}
    return {"n": len(trace),
            "shipped_mae_s": round(statistics.fmean(abs(t["shipped_err"]) for t in trace), 2),
            "calib_mae_s": round(statistics.fmean(abs(t["calib_err"]) for t in trace), 2),
            "trace": trace}


def main(argv: list[str] | None = None) -> int:
    """python -m redzone.tuners.join_calib [joins.jsonl] [--json]: the per-key table, alerts and D_edge stats."""
    import sys
    a = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in a
    a = [x for x in a if not x.startswith("--")]
    c = JoinCalib.load(a[0] if a else None)
    out = {"joins": c.table(), "alerts": c.alerts(), "d_edge": c.edge_stats(), "replay": {
        k: v for k, v in replay(c.rows).items() if k != "trace"}}
    if as_json:
        print(json.dumps(out, indent=1))
        return 0
    print(f"{'key':34} {'n':>3} {'gross':>5} {'unmeas':>6} {'fail':>4} {'value':>7} {'err':>5} {'mean_err':>8}  source")
    for t in out["joins"]:
        print(f"{t['key']:34} {t['n']:3d} {t['gross']:5d} {t['unmeasured']:6d} {t['failed']:4d} {t['value_s']:7.2f} "
              f"{t['err_s']:5.2f} {'' if t['mean_error_s'] is None else t['mean_error_s']:>8}  {t['source']}")
    for al in out["alerts"]:
        print("SHIFT", al)
    for d in out["d_edge"]:
        print(f"d_edge {d['key']:28} n={d['n']} {d['d_edge_s']} s (prior {d['vs_prior_s']:+} s)")
    print("replay (online, out of sample):", out["replay"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
