"""The night, rolled up: what it showed, what it missed, what broke, what got fixed, and the error budget.

Inputs (all already on disk, nothing new to keep running):
  switches rows + their traces       data/redzone.db (redzone/trace.py)
  cut grades                         data/supervisor_cuts.jsonl (Supervisor.grade_cuts, ~90 s after a cut)
  key moments seen/missed            data/supervisor_moments.jsonl
  supervisor fixes, safe-mode spans  data/supervisor.jsonl
  watchdog actions                   data/agent.jsonl (agent/watchdog.py, its own process)

Outputs: extra sections for data/reports/YYYY-MM-DD.md, a metrics sidecar data/reports/YYYY-MM-DD.json
(the error budget's rolling window reads these back), and a short phone body for the ntfy push.

Error budget: four service-level objectives over the last `budget.window_nights` nights that had any
switches. Each has an allowance; "used" is how much of it the bad events ate.

  switch_failures   failed switches (the source's or our fault, not a held-back cut) plus switches whose
                    picture never got confirmed, as a share of live switches   allowed: failure_pct %
  left_mid_play     cuts that left a play on screen, judged from the feed      allowed: mid_play_cuts
  dead_air          switches whose old-picture-gone -> new-picture-up took
                    longer than dead_air_s, as a share of live cuts            allowed: dead_air_pct %
  safe_mode         minutes in safe mode                                       allowed: safe_mode_min a night
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

from redzone import trace as tr

BUDGET_DEFAULTS = {
    "window_nights": 7,
    "failure_pct": 5.0,
    "mid_play_cuts": 0,
    "dead_air_s": 10.0,
    "dead_air_pct": 10.0,
    "safe_mode_min": 10.0,
}
SEGMENT_CAP_S = 4 * 3600.0      # one screen segment never counts for more than this (a forgotten TV)
WARN_AT = 75.0                  # % of a budget used -> "low"


def _fmt_dur(s: float) -> str:
    m = int(round(s / 60))
    return f"{m // 60}h{m % 60:02d}m" if m >= 60 else f"{m}m"


def _read_jsonl(path: Path, t0: float, t1: float) -> list[dict]:
    from redzone.supervisor import read_jsonl
    return read_jsonl(path, t0, t1)


def showed(traces: list[dict], t0: float, t1: float, end_at: float | None) -> list[tuple[str, float]]:
    """(game label, seconds on screen), most first: each confirmed live picture lasts until the next
    one comes up (or the cut, if one came first), the last one until `end_at` (last sign of life)."""
    ups = sorted((t for t in traces if t.get("ok") and not t.get("dry_run") and t.get("picture_at")
                  and t.get("path") != "already"), key=lambda t: t["picture_at"])
    secs: Counter = Counter()
    for i, t in enumerate(ups):
        start = float(t["picture_at"])
        if i + 1 < len(ups):
            nxt = ups[i + 1]
            stop = float(nxt.get("cut_at") or nxt["picture_at"])
        else:
            stop = float(end_at) if end_at else start
        stop = min(stop, t1, start + SEGMENT_CAP_S)
        if stop > start >= t0:
            label = "catch-up replay" if t.get("action") == "replay" else (t.get("to_label") or t.get("to") or "?")
            secs[label] += stop - start
    return secs.most_common()


def safe_mode_seconds(events: list[dict], t0: float, t1: float) -> tuple[float, int]:
    """(seconds in safe mode, episodes) from the supervisor's on/off records; an open one ends at t1."""
    total, n, on = 0.0, 0, None
    for e in sorted((e for e in events if e.get("kind") == "episode" and e.get("rule") == "safe_mode"),
                    key=lambda e: float(e.get("ts") or 0)):
        ts = float(e.get("ts") or 0)
        if e.get("state") == "on" and on is None:
            on, n = ts, n + 1
        elif e.get("state") == "off" and on is not None:
            total += max(0.0, ts - on)
            on = None
    if on is not None:
        total += max(0.0, t1 - on)
    return total, n


def watchdog_actions(root: Path, t0: float, t1: float) -> list[dict]:
    rows = _read_jsonl(root / "agent.jsonl", t0, t1)
    return [r for r in rows if r.get("kind") == "check" and r.get("action") not in (None, "none")]


def metrics(night: date, rows: list[dict], root: Path, t0: float, t1: float, *,
            end_at: float | None, held_prefixes: tuple, cfg: dict | None = None) -> dict:
    """Everything the budget and the phone body need, as plain numbers (saved as the .json sidecar)."""
    b = {**BUDGET_DEFAULTS, **(cfg or {})}
    traces = [t for t in (tr.of_row(r) for r in rows) if t is not None]
    live = [r for r in rows if not r.get("dry_run")]
    held = [r for r in live if not r.get("ok") and
            str(_det(r).get("detail") or "").lower().startswith(held_prefixes)]
    failed = [r for r in live if not r.get("ok") and r not in held]
    live_tr = [t for t in traces if not t.get("dry_run")]
    unconfirmed = [t for t in live_tr if t.get("ok") and not t.get("picture_confirmed") and t.get("path") != "already"]
    cuts = [t for t in live_tr if t.get("cut_at") is not None]
    dead = [float(t["spans"]["dead_air"]) for t in cuts if (t.get("spans") or {}).get("dead_air") is not None]
    to_pic = [float(t["spans"]["to_picture"]) for t in live_tr
              if (t.get("spans") or {}).get("to_picture") is not None and t.get("path") != "already"]
    grades = _read_jsonl(root / "supervisor_cuts.jsonl", t0, t1)
    left_mid = [g for g in grades if g.get("leave") == "play"]
    landed_mid = [g for g in grades if g.get("enter") == "play"]
    events = _read_jsonl(root / "supervisor.jsonl", t0, t1)
    safe_s, safe_n = safe_mode_seconds(events, t0, t1)
    moments = _read_jsonl(root / "supervisor_moments.jsonl", t0, t1)
    by_path: dict[str, list[float]] = defaultdict(list)
    for t in live_tr:
        if (t.get("spans") or {}).get("to_picture") is not None and t.get("path") != "already":
            by_path[t.get("path") or "?"].append(float(t["spans"]["to_picture"]))
    return {
        "night": night.isoformat(),
        "switches": len(live), "failed": len(failed), "held": len(held), "unconfirmed": len(unconfirmed),
        "traced": len(live_tr), "cuts": len(cuts), "graded_cuts": len(grades),
        "left_mid_play": len(left_mid), "landed_mid_play": len(landed_mid),
        "left_unknown": sum(1 for g in grades if g.get("leave") == "unknown"),
        "dead_air_slow": sum(1 for x in dead if x > float(b["dead_air_s"])),
        "dead_air_p50": _r(tr.pct(dead, 0.5)), "dead_air_p90": _r(tr.pct(dead, 0.9)),
        "to_picture_p50": _r(tr.pct(to_pic, 0.5)), "to_picture_p90": _r(tr.pct(to_pic, 0.9)),
        "to_picture_by_path": {p: {"n": len(xs), "p50": _r(tr.pct(xs, 0.5)), "p90": _r(tr.pct(xs, 0.9))}
                               for p, xs in sorted(by_path.items())},
        "spans_median": tr.span_medians(live_tr),
        "safe_mode_s": round(safe_s, 1), "safe_mode_episodes": safe_n,
        "moments": len(moments), "moments_seen": sum(1 for m in moments if m.get("seen")),
        "showed": [[k, round(v)] for k, v in showed(traces, t0, t1, end_at)[:8]],
        "mid_play_list": [{"ts": g.get("ts"), "from": g.get("from_label") or g.get("from"), "why": g.get("leave_why")}
                          for g in left_mid[:5]],
    }


def _r(x):
    return None if x is None else round(float(x), 1)


def _det(r: dict) -> dict:
    from redzone.supervisor import _detail
    return _detail(r)


def load_sidecars(reports: Path, night: date, n: int) -> list[dict]:
    """The metrics of up to n-1 nights before `night` (only nights that have a sidecar)."""
    out = []
    for k in range(1, max(1, int(n))):
        p = reports / f"{(night - timedelta(days=k)).isoformat()}.json"
        try:
            m = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(m, dict):
            out.append(m)
    return out


def budget(tonight: dict, earlier: list[dict], cfg: dict | None = None) -> dict:
    """The error budget over tonight + earlier nights that had live switches (idle nights don't count)."""
    b = {**BUDGET_DEFAULTS, **(cfg or {})}
    nights = [m for m in [tonight, *earlier] if int(m.get("switches") or 0) > 0 or m is tonight]
    sw = sum(int(m.get("switches") or 0) - int(m.get("held") or 0) for m in nights)
    bad = sum(int(m.get("failed") or 0) + int(m.get("unconfirmed") or 0) for m in nights)
    cuts = sum(int(m.get("cuts") or 0) for m in nights)
    slow = sum(int(m.get("dead_air_slow") or 0) for m in nights)
    mid = sum(int(m.get("left_mid_play") or 0) for m in nights)
    safe_min = sum(float(m.get("safe_mode_s") or 0) for m in nights) / 60.0
    active = max(1, sum(1 for m in nights if int(m.get("switches") or 0) > 0))

    def line(used: float, allowed: float, unit: str, show_used: str) -> dict:
        if allowed <= 0:
            pct = 0.0 if used <= 0 else float("inf")
        else:
            pct = 100.0 * used / allowed
        status = "spent" if pct > 100.0 or (allowed <= 0 and used > 0) else ("low" if pct >= WARN_AT else "ok")
        return {"used": round(used, 2), "allowed": round(allowed, 2), "pct": None if pct == float("inf") else round(pct),
                "status": status, "unit": unit, "shown": show_used}

    out = {
        "nights": len(nights),
        "switch_failures": line(bad, sw * float(b["failure_pct"]) / 100.0, "switches",
                                f"{bad} of {sw}"),
        "left_mid_play": line(mid, float(b["mid_play_cuts"]), "cuts", f"{mid}"),
        "dead_air": line(slow, cuts * float(b["dead_air_pct"]) / 100.0, "switches",
                         f"{slow} of {cuts} over {float(b['dead_air_s']):g}s"),
        "safe_mode": line(safe_min, active * float(b["safe_mode_min"]), "min", f"{safe_min:.0f} min"),
    }
    out["spent"] = sorted(k for k, v in out.items() if isinstance(v, dict) and v["status"] == "spent")
    return out


NAMES = {"switch_failures": "Switch failures", "left_mid_play": "Mid-play cuts", "dead_air": "Slow dead air",
         "safe_mode": "Safe mode"}


def report_sections(m: dict, bud: dict, root: Path, t0: float, t1: float,
                    cfg: dict | None = None) -> tuple[list[str], list[str]]:
    """Markdown: (top: What it showed) and (rest: Switch timings, Cuts judged from the feed, What the
    watchdog did, Error budget)."""
    b = {**BUDGET_DEFAULTS, **(cfg or {})}
    top = ["## What it showed", ""]
    if m["showed"]:
        top += [f"- {k}: {_fmt_dur(v)}" for k, v in m["showed"]] + [""]
    else:
        top += ["Nothing confirmed on screen.", ""]

    lines = ["## Switch timings (traces)", ""]
    if not m["traced"]:
        lines += ["No traced switches.", ""]
    else:
        if m["to_picture_p50"] is not None:
            lines.append(f"- Decision to picture: median {m['to_picture_p50']:.1f}s, p90 {m['to_picture_p90']:.1f}s")
        if m["dead_air_p50"] is not None:
            lines.append(f"- Dead air (old picture gone, new one not up): median {m['dead_air_p50']:.1f}s, "
                         f"p90 {m['dead_air_p90']:.1f}s, {m['dead_air_slow']} over {float(b['dead_air_s']):g}s")
        if m["unconfirmed"]:
            lines.append(f"- Picture never confirmed after the cut: {m['unconfirmed']}")
        if m["to_picture_by_path"]:
            lines += ["", "| Path | Switches | Median | p90 |", "|---|---|---|---|"]
            for p, v in m["to_picture_by_path"].items():
                lines.append(f"| {p} | {v['n']} | {v['p50']:.1f}s | {v['p90']:.1f}s |")
        if m["spans_median"]:
            lines += ["", "Median step times: " + ", ".join(f"{k.replace('_', ' ')} {v:.1f}s"
                                                            for k, v in m["spans_median"].items())]
        lines.append("")

    lines += ["## Cuts, judged from the feed afterwards", ""]
    if not m["graded_cuts"]:
        lines += ["No cuts graded.", ""]
    else:
        lines.append(f"- Left mid-play: {m['left_mid_play']} of {m['graded_cuts']}"
                     + (f" ({m['left_unknown']} the feed couldn't tell)" if m["left_unknown"] else ""))
        lines.append(f"- Landed mid-play: {m['landed_mid_play']}")
        for g in m["mid_play_list"]:
            from redzone.supervisor import _clock_et
            lines.append(f"  - {_clock_et(float(g['ts'] or 0))} left {g['from']}: {g.get('why') or 'a play on screen'}")
        lines.append("")

    acts = watchdog_actions(root, t0, t1)
    lines += ["## What the watchdog did", ""]
    if not acts:
        lines += ["Nothing.", ""]
    else:
        for act, n in Counter(a.get("action") for a in acts).most_common(5):
            last = next(a for a in reversed(acts) if a.get("action") == act)
            lines.append(f"- {act}: {n} (last: {str(last.get('why') or '')[:80]}"
                         + (f" -> {str(last.get('result'))[:60]}" if last.get("result") else "") + ")")
        lines.append("")

    lines += [f"## Error budget (last {bud['nights']} night{'s' if bud['nights'] != 1 else ''} with switches)", "",
              "| Objective | Used | Of allowance | Status |", "|---|---|---|---|"]
    for k in ("switch_failures", "left_mid_play", "dead_air", "safe_mode"):
        v = bud[k]
        pct = "—" if v["pct"] is None else f"{v['pct']}%"
        lines.append(f"| {NAMES[k]} | {v['shown']} | {pct} of {v['allowed']:g} {v['unit']} | {v['status']} |")
    lines.append("")
    return top, lines


def phone_body(m: dict, bud: dict, fixes: int, fix_rules: Counter, causes: Counter, missed: list[dict],
               watchdog: int) -> str:
    """The ntfy push: five short lines a phone shows without opening anything (< 1 KB)."""
    out = []
    if m["showed"]:
        out.append("Showed: " + ", ".join(f"{k} {_fmt_dur(v)}" for k, v in m["showed"][:3]))
    else:
        out.append("Showed: nothing confirmed")
    miss = f"Missed: {m['moments'] - m['moments_seen']} of {m['moments']} big plays"
    if missed:
        last = max(missed, key=lambda x: float(x.get("ts") or 0))
        miss += f" (latest: {str(last.get('matchup', ''))} {str(last.get('label', ''))[:50]})"
    out.append(miss)
    broke = [f"{m['failed']} failed switch{'es' if m['failed'] != 1 else ''}"]
    if causes:
        broke[0] += " (" + ", ".join(f"{c} {n}" for c, n in causes.most_common(2)) + ")"
    if m["unconfirmed"]:
        broke.append(f"{m['unconfirmed']} picture unconfirmed")
    broke.append(f"{m['left_mid_play']} mid-play cuts")
    if m["safe_mode_episodes"]:
        broke.append(f"safe mode {m['safe_mode_s'] / 60:.0f} min")
    if m["dead_air_p90"] is not None:
        broke.append(f"dead air p90 {m['dead_air_p90']:.1f}s")
    out.append("Broke: " + ", ".join(broke))
    fixed = f"Fixed: {fixes + watchdog}"
    parts = [f"{r} {n}" for r, n in fix_rules.most_common(3)]
    if watchdog:
        parts.append(f"watchdog {watchdog}")
    if parts:
        fixed += " (" + ", ".join(parts) + ")"
    out.append(fixed)
    bl = []
    for k in ("switch_failures", "left_mid_play", "dead_air", "safe_mode"):
        v = bud[k]
        bl.append(f"{NAMES[k].lower()} {'SPENT' if v['status'] == 'spent' else (str(v['pct']) + '%' if v['pct'] is not None else 'ok')}")
    out.append(f"Budget ({bud['nights']}n): " + ", ".join(bl))
    return "\n".join(out)[:1000]
