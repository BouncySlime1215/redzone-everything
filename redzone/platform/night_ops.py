"""Night ops report: joins, errors, fallbacks, drift and Mac health for one night (Nick's Nov 7 order, item 7).

Reads only numbers the app already writes under data/ (no frames, no addresses):
  joins.jsonl       one row per buffered join (redzone/tuners/join.py, PR #393): app, method, target_s,
                    predicted_s, expected_err_s, ok, measured_s (None = not read), error_s = measured - target,
                    pred_error_s = measured - predicted, reason, took_s; optional stick / channel / league.
  fallbacks.jsonl   one row per fallback ladder step (item 2): {"ts", "stick", "target", "step", "why"} with
                    step one of buffered | live | other_stick | hold. Rows in joins.jsonl with
                    "kind": "fallback" count too.
  drift.jsonl       one row per drift read (item 3): {"ts", "stick", "channel", "drift_s", "read_cost_s",
                    "action"} with action ok | reenter | live | snapped | unknown (snapped and unknown count as
                    back to live). Rows in joins.jsonl with "kind": "drift" (#395) count too.
  joins.jsonl rows with "kind": "watchdog" (#395): state buffered | live | unknown, attempts, fallback.
  supervisor.jsonl  invariant faults (stuck_lock, stick_hung, stale_feed, ...), fixes that didn't work, and
                    Mac health alerts (redzone/platform/mac_health.py).
A file that isn't there yet is simply "none" in the report: the sections fill in as items 1-3 land.

In the supervisor's nightly report (data/reports/YYYY-MM-DD.md + phone push) behind
supervisor.night_ops.enabled (default OFF). By hand, read-only, any time:
    python -m redzone.platform.night_ops [YYYY-MM-DD] [--json]
"""
from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any

NIGHT_OPS_DEFAULTS = {"enabled": False, "join_margin_s": 10.0}
LADDER_STEPS = ("buffered", "live", "other_stick", "hold")


def options(over: Any = None) -> dict:
    out = dict(NIGHT_OPS_DEFAULTS)
    if isinstance(over, dict):
        if isinstance(over.get("enabled"), bool):
            out["enabled"] = over["enabled"]
        m = over.get("join_margin_s")
        if isinstance(m, (int, float)) and not isinstance(m, bool) and m > 0:
            out["join_margin_s"] = float(m)
    return out


def _rows(path: Path, t0: float, t1: float) -> list[dict]:
    out: list[dict] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and t0 <= float(r.get("ts") or 0) < t1:
                    out.append(r)
    except OSError:
        pass
    return out


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))]


def _joins(rows: list[dict], margin: float) -> dict:
    by_app: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_app[str(r.get("app") or "?")].append(r)
    out = {}
    for app, rs in sorted(by_app.items()):
        errs = [e for e in (_num(r.get("error_s")) for r in rs) if e is not None]
        pred = [e for e in (_num(r.get("pred_error_s")) for r in rs) if e is not None]
        took = [t for t in (_num(r.get("took_s")) for r in rs) if t is not None]
        fails = Counter(str(r.get("reason") or "?")[:60] for r in rs if not r.get("ok"))
        a = [abs(e) for e in errs]
        out[app] = {
            "joins": len(rs), "ok": sum(1 for r in rs if r.get("ok")), "measured": len(errs),
            "abs_err_p50": round(statistics.median(a), 2) if a else None,
            "abs_err_p90": round(_pct(a, 0.9), 2) if a else None,
            "abs_err_max": round(max(a), 2) if a else None,
            "bias_s": round(statistics.fmean(errs), 2) if errs else None,
            "pred_bias_s": round(statistics.fmean(pred), 2) if pred else None,
            "within_margin": sum(1 for x in a if x <= margin),
            "took_p50_s": round(statistics.median(took), 1) if took else None,
            "failures": dict(fails.most_common(3)),
        }
    return out


def summarize(root: Path, t0: float, t1: float, margin: float = NIGHT_OPS_DEFAULTS["join_margin_s"]) -> dict:
    """Everything the night's ops section says, as numbers (also the .json sidecar's "ops")."""
    root = Path(root)
    jrows = _rows(root / "joins.jsonl", t0, t1)
    joins = [r for r in jrows if r.get("kind") in (None, "join")]
    falls = _rows(root / "fallbacks.jsonl", t0, t1) + [r for r in jrows if r.get("kind") == "fallback"]
    drift = _rows(root / "drift.jsonl", t0, t1) + [r for r in jrows if r.get("kind") == "drift"]
    wd = [r for r in jrows if r.get("kind") == "watchdog"]
    wd_state = Counter(str(r.get("state") or "unknown") for r in wd)
    wd_fall = Counter(str(r.get("fallback")) for r in wd if r.get("fallback"))
    sup = _rows(root / "supervisor.jsonl", t0, t1)

    steps = Counter(str(r.get("step") or "?") for r in falls)
    why = Counter(str(r.get("why") or "?")[:60] for r in falls)
    d = [abs(x) for x in (_num(r.get("drift_s")) for r in drift) if x is not None]
    cost = [c for c in (_num(r.get("read_cost_s")) for r in drift) if c is not None]
    # #395: "snapped" (the app jumped back to live) and "unknown" (couldn't read) both end on live, not buffered
    actions = Counter({"snapped": "live", "unknown": "live"}.get(str(r.get("action") or "ok"), str(r.get("action") or "ok"))
                      for r in drift)
    faults = Counter(str(r.get("code")) for r in sup if r.get("kind") == "invariant")
    broken = Counter(str(r.get("rule")) for r in sup if r.get("kind") == "fix" and r.get("ok") is False)
    mac = [r for r in sup if r.get("kind") == "mac_health" and not r.get("cleared")]
    return {
        "margin_s": margin,
        "joins": _joins(joins, margin),
        "joins_total": len(joins),
        "fallbacks": {"total": len(falls), "steps": {k: steps[k] for k in LADDER_STEPS if steps[k]}
                      | {k: n for k, n in steps.items() if k not in LADDER_STEPS},
                      "why": dict(why.most_common(3))},
        "drift": {"reads": len(drift), "read_cost_s": round(sum(cost), 1),
                  "abs_drift_p50": round(statistics.median(d), 2) if d else None,
                  "abs_drift_max": round(max(d), 2) if d else None,
                  "reentries": actions["reenter"], "to_live": actions["live"]},
        "watchdog": {"joins": len(wd), "buffered": wd_state["buffered"], "live": wd_state["live"],
                     "unknown": wd_state["unknown"], "reseeks": sum(1 for r in wd if int(r.get("attempts") or 1) > 1),
                     "fallbacks": dict(wd_fall.most_common())},
        "errors": {"faults": dict(faults.most_common()), "fixes_failed": dict(broken.most_common())},
        "mac": dict(Counter(str(r.get("code")) for r in mac).most_common()),
        "mac_last": {str(r.get("code")): str(r.get("text") or "") for r in mac},
    }


def _f(v: Any, unit: str = "s") -> str:
    return "–" if v is None else f"{v:g}{unit}"


def lines(s: dict) -> list[str]:
    """The markdown section for the nightly report."""
    out = ["## Sync and health", ""]
    m = s["margin_s"]
    if not s["joins"]:
        out += ["Joins: none logged (data/joins.jsonl).", ""]
    else:
        out += [f"| Joins | n | ok | read | within {m:g}s | abs err p50 / p90 / max | bias | vs predicted | took p50 |",
                "|---|---|---|---|---|---|---|---|---|"]
        for app, j in s["joins"].items():
            within = f"{j['within_margin']}/{j['measured']}" if j["measured"] else "–"
            out.append(f"| {app} | {j['joins']} | {j['ok']} | {j['measured']} | {within} | "
                       f"{_f(j['abs_err_p50'])} / {_f(j['abs_err_p90'])} / {_f(j['abs_err_max'])} | "
                       f"{_f(j['bias_s'])} | {_f(j['pred_bias_s'])} | {_f(j['took_p50_s'])} |")
        fails = Counter()
        for j in s["joins"].values():
            fails.update(j["failures"])
        if fails:
            out.append("")
            out.append("Join failures: " + "; ".join(f"{k} ({n})" for k, n in fails.most_common(3)))
        out.append("")
    wd = s["watchdog"]
    out.append("Join watchdog: " + ("none" if not wd["joins"] else
               f"{wd['joins']} joins checked: {wd['buffered']} buffered, {wd['live']} live, {wd['unknown']} unknown; "
               f"{wd['reseeks']} re-seeked" + ("; fallbacks " + ", ".join(f"{k} {n}" for k, n in wd["fallbacks"].items())
                                               if wd["fallbacks"] else "")))
    fb = s["fallbacks"]
    out.append("Fallbacks: " + ("none" if not fb["total"] else
               f"{fb['total']} (" + ", ".join(f"{k.replace('_', ' ')} {n}" for k, n in fb["steps"].items()) + ")"
               + ("; why: " + "; ".join(f"{k} ({n})" for k, n in fb["why"].items()) if fb["why"] else "")))
    dr = s["drift"]
    out.append("Drift: " + ("no reads" if not dr["reads"] else
               f"{dr['reads']} reads costing {dr['read_cost_s']:g}s; abs drift p50 {_f(dr['abs_drift_p50'])}, "
               f"max {_f(dr['abs_drift_max'])}; {dr['reentries']} re-entries, {dr['to_live']} back to live"))
    er = s["errors"]
    out.append("Errors: " + (", ".join(f"{k} {n}" for k, n in er["faults"].items()) or "no invariant faults")
               + (("; fixes that didn't work: " + ", ".join(f"{k} {n}" for k, n in er["fixes_failed"].items()))
                  if er["fixes_failed"] else ""))
    out.append("Mac: " + (", ".join(f"{k.replace('_', ' ')} {n}" for k, n in s["mac"].items()) or "no alerts"))
    for code, text in s["mac_last"].items():
        out.append(f"- {code}: {text}")
    out.append("")
    return out


def phone_line(s: dict) -> str:
    """One line for the morning push."""
    parts = []
    n = s["joins_total"]
    if n:
        read = sum(j["measured"] for j in s["joins"].values())
        within = sum(j["within_margin"] for j in s["joins"].values())
        parts.append(f"Joins {n} ({within}/{read} within {s['margin_s']:g}s)")
    if s["watchdog"]["joins"]:
        w = s["watchdog"]
        parts.append(f"watchdog {w['buffered']}/{w['joins']} buffered")
    if s["fallbacks"]["total"]:
        parts.append(f"fallbacks {s['fallbacks']['total']}")
    if s["drift"]["reads"]:
        parts.append(f"drift reads {s['drift']['reads']} ({s['drift']['reentries']} re-entries)")
    if s["mac"]:
        parts.append("Mac: " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in s["mac"].items()))
    return "Sync: " + ("; ".join(parts) if parts else "no joins, fallbacks or Mac alerts")


def main(argv: list[str] | None = None) -> int:
    import sys
    import time
    from datetime import timedelta
    from redzone.supervisor import DATA, night_of, night_window
    args = list(sys.argv[1:] if argv is None else argv)
    rest = [a for a in args if not a.startswith("--")]
    night = date.fromisoformat(rest[0]) if rest else night_of(time.time()) - timedelta(days=1)
    s = summarize(DATA, *night_window(night))
    print(json.dumps(s, indent=1) if "--json" in args else "\n".join(lines(s)) + "\n" + phone_line(s))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
