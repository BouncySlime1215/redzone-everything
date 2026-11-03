"""Score reasoner answers against what actually happened, on held-out (test) nights. Stdlib only.

Any source is scored the same way against the export's hindsight (y, target, t_big):
``today`` (the brain's "key soon" odds printed in the brief) and ``head`` (the reasoner thread's fast
lock-in head, out-of-fold) mapped to lock-in levels with cuts fitted on VALID nights; ``teacher`` (the
blind teacher answers) and ``student`` (the adapter's answers to the same briefs).

Lines (what Nick asked for: does must_see precede real big plays, and is leaving ever unsafe):
  must_see_precision   P(big play within the window | must_see)
  must_see_recall      P(must_see | a big play came within the window)
  must_see_lift        precision / base rate of big plays
  lock_acc             exact lock_in match vs the hindsight label
  unsafe_leave_pct     said safe_to_leave but a big play followed (the costly mistake: missed live)
  over_cautious_pct    said not safe but nothing big followed
  eta_mae_s            |time named in 'next' - actual seconds to the next big play| where both exist
  answered_pct         rows this source answered at all (a partial teacher run shows here)
  usable_json_pct      answered rows that parsed into a valid reasoner answer
"""
from __future__ import annotations

import re
from typing import Iterable

from agent.distill.teacher import clean_reasoner, eta_s, extract_json

_KEY = re.compile(r"key soon (\d+)%")
MISSING = {}          # sentinel: the source was never asked about this row (vs. asked but unusable = None)


def big_soon(r: dict) -> bool:
    """Truth: a scorecard big or crunch play within the league's horizon, not garbage (export's y)."""
    return bool(r.get("y"))


def truth_lock(r: dict) -> str:
    return r["target"]["lock_in"]


def truth_safe(r: dict) -> bool:
    return r["target"]["safe_to_leave"] == "yes"


def score_of(source: str, r: dict) -> float | None:
    """The number a baseline ranks moments by: today's brain key-soon odds, or the fast head's odds."""
    if source == "head":
        return r.get("head_p")
    m = _KEY.search(r.get("brief", ""))
    return int(m.group(1)) / 100.0 if m else None


def fit_thresholds(source: str, valid_rows: list[dict]) -> tuple[float, float]:
    """On VALID nights: must_see cut maximizes must_see F1 vs big plays; watch cut matches the watch rate."""
    pts = [(p, big_soon(r), truth_lock(r)) for r in valid_rows if (p := score_of(source, r)) is not None]
    if not pts:
        return 0.6, 0.3
    best, hi = -1.0, 0.6
    vals = sorted({round(p, 3) for p, _, _ in pts})
    for c in [vals[0]] + [(a + b) / 2 for a, b in zip(vals, vals[1:])]:     # cut between observed values
        tp = sum(1 for p, b, _ in pts if p >= c and b)
        fp = sum(1 for p, b, _ in pts if p >= c and not b)
        fn = sum(1 for p, b, _ in pts if p < c and b)
        f1 = 2 * tp / max(1, 2 * tp + fp + fn)
        if f1 > best:
            best, hi = f1, c
    not_none = sum(1 for *_, lk in pts if lk != "none") / len(pts)
    ps = sorted(p for p, _, _ in pts)
    lo = ps[max(0, min(len(ps) - 1, int((1 - not_none) * len(ps))))]
    return hi, min(lo, hi)


def answers_for(source: str, rows: list[dict], answers: dict[str, dict] | None = None,
                thresholds: dict[str, tuple[float, float]] | None = None) -> dict[str, dict | None]:
    """{row id: cleaned answer or None} for one source. thresholds: per league (baselines only)."""
    out: dict[str, dict | None] = {}
    for r in rows:
        if source in ("today", "head"):
            p = score_of(source, r)
            if p is None:
                out[r["id"]] = MISSING
                continue
            hi, lo = (thresholds or {}).get(r.get("league"), (0.6, 0.3))
            lk = "must_see" if p >= hi else "watch" if p >= lo else "none"
            # no leave signal of their own: leaving is "safe" unless the game is must_see
            out[r["id"]] = {"lock_in": lk, "safe_to_leave": "no" if lk == "must_see" else "yes", "next": ""}
            continue
        a = (answers or {}).get(f"{r['id']}|blind") or (answers or {}).get(r["id"])
        if a is None:
            out[r["id"]] = MISSING
            continue
        d = a if "lock_in" in a else extract_json(a.get("text", ""))
        out[r["id"]] = clean_reasoner(d)
    return out


def score(rows: list[dict], preds: dict[str, dict | None]) -> dict:
    n = len(rows)
    answered = sum(1 for r in rows if r["id"] in preds and preds[r["id"]] is not MISSING)
    usable = [(r, p) for r in rows if (p := preds.get(r["id"])) and p is not MISSING]
    base = sum(big_soon(r) for r in rows)
    ms = [(r, p) for r, p in usable if p["lock_in"] == "must_see"]
    big = [(r, p) for r, p in usable if big_soon(r)]
    ms_hit = sum(big_soon(r) for r, _ in ms)
    safe_said = [r for r, p in usable if p["safe_to_leave"] == "yes"]
    stay_said = [r for r, p in usable if p["safe_to_leave"] == "no"]
    errs = []
    for r, p in usable:
        e = eta_s(p.get("next", ""))
        if e is not None and big_soon(r) and r.get("t_big") is not None and p["lock_in"] != "none":
            errs.append(abs(e - float(r["t_big"])))
    pct = lambda a, b: round(100.0 * a / b, 1) if b else None  # noqa: E731
    prec = ms_hit / len(ms) if ms else None
    return {
        "n": n, "answered_pct": pct(answered, n), "usable_json_pct": pct(len(usable), answered),
        "big_base_rate_pct": pct(base, n),
        "must_see_pct": pct(len(ms), len(usable)),
        "must_see_precision": pct(ms_hit, len(ms)),
        "must_see_recall": pct(sum(p["lock_in"] == "must_see" for _, p in big), len(big)),
        "must_see_lift": round(prec / (base / n), 2) if (prec is not None and base) else None,
        "lock_acc": pct(sum(p["lock_in"] == truth_lock(r) for r, p in usable), len(usable)),
        "unsafe_leave_pct": pct(sum(not truth_safe(r) for r in safe_said), len(safe_said)),
        "over_cautious_pct": pct(sum(truth_safe(r) for r in stay_said), len(stay_said)),
        "eta_mae_s": round(sum(errs) / len(errs), 1) if errs else None,
    }


def compare(rows: Iterable[dict], sources: dict[str, dict[str, dict] | None]) -> dict:
    """{source: {'all' | league: score}} on TEST nights; baselines are fitted on VALID nights first."""
    rows = list(rows)
    test = [r for r in rows if r.get("split") == "test"]
    valid = [r for r in rows if r.get("split") == "valid"]
    res: dict = {"thresholds": {}, "test_nights": sorted({str(r.get("night")) for r in test})}
    for name, ans in sources.items():
        th = None
        if name in ("today", "head"):     # cuts fitted per league on valid nights, never on test
            th = {lg: fit_thresholds(name, [r for r in valid if r.get("league") == lg])
                  for lg in sorted({r.get("league") for r in test})}
            res["thresholds"][name] = {lg: {"must_see_at": h, "watch_at": w} for lg, (h, w) in th.items()}
        preds = answers_for(name, test, ans, th)
        by = {"all": score(test, preds)}
        for lg in sorted({r.get("league") for r in test}):
            by[lg] = score([r for r in test if r.get("league") == lg], preds)
        res[name] = by
    return res


def table(res: dict, league: str = "all") -> str:
    """Markdown table (rows = lines, columns = sources) for the PR."""
    srcs = [k for k in res if k not in ("thresholds", "test_nights")]
    keys = ["n", "answered_pct", "usable_json_pct", "big_base_rate_pct", "must_see_pct", "must_see_precision", "must_see_recall",
            "must_see_lift", "lock_acc", "unsafe_leave_pct", "over_cautious_pct", "eta_mae_s"]
    out = ["| line | " + " | ".join(srcs) + " |", "|---|" + "---|" * len(srcs)]
    for k in keys:
        out.append(f"| {k} | " + " | ".join(str(res[s].get(league, {}).get(k, "")) for s in srcs) + " |")
    return "\n".join(out)
