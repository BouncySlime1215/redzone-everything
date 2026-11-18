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
  leave_allowed_pct    how often it says leaving is safe (the freedom the decider gets)
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

# Leagues whose lock_acc is out of every scorecard. v17: the old truth (target.lock_in via gold.py) is must_see whenever
# the CURRENT crunch flag holds, so lock_acc rewarded copying our crunch rule (CBB context-only AUC 0.960, NBA 0.995;
# Nick, Nov 5 1:50 AM). v19: truth_lock comes from real outcomes (EXP-302 y_out / y_out2), and a league leaves this
# list only when docs/label_checks/reasoner_lock_<lg>.json and reasoner_watch_<lg>.json PASS
# (scripts/label_leak_check.py; a test enforces it). NBA 0.851/0.868, NFL 0.752/0.749, NHL 0.771/0.756 (train/valid).
_WHY = "no outcome-label (y_out) rows checked yet; the old lock truth copies the crunch rule (context-only AUC >= 0.95)"
LOCK_EXCLUDED = {lg: _WHY for lg in ("cfb", "cbb", "mlb")}


def pct(a, b):
    return round(100.0 * a / b, 1) if b else None


MISSING = {}          # sentinel: the source was never asked about this row (vs. asked but unusable = None)


def big_soon(r: dict) -> bool:
    """Truth: a scorecard big or crunch play within the league's horizon, not garbage (export's y)."""
    return bool(r.get("y"))


def truth_lock(r: dict) -> str:
    """Hindsight lock truth from REAL outcomes (EXP-302, grader v19): a lead change/catch or a >= 0.20 win-probability
    swing within the league horizon N is must_see, within 2N is watch, else none. Never the crunch/clock rule (gold.py
    stays the student's training target, never grading truth). Rows built before #278 keep the old label and are
    left out of lock_acc (lock_graded)."""
    if r.get("y_out") is None:
        return r["target"]["lock_in"]
    return "must_see" if r["y_out"] else ("watch" if r.get("y_out2") else "none")


def lock_graded(r: dict) -> bool:
    """Does this row count toward lock_acc? Only outcome-labelled rows in a league whose label check passed."""
    return r.get("y_out") is not None and r.get("league") not in LOCK_EXCLUDED


def truth_safe(r: dict) -> bool:
    return r["target"]["safe_to_leave"] == "yes"


# Down the stretch is sticky (Nick, Nov 4 3:15 PM): close + late = never leave. Margins per base league.
STICKY_MARGIN = {"nba": 6, "cbb": 6, "wcbb": 6, "nfl": 8, "cfb": 8, "nhl": 1, "mlb": 2}
# Nick 4:15 PM: crunch is a LEARNED state, not a hard rule. The graded answer is the model's own; the floor
# (never leave a high-leverage moment for a LOW-value game) lives in the decider, and the report shows the
# student with this prior applied too, as "floored", so the floor's effect stays visible.
CRUNCH_STAY_PRIOR = False


def sticky(r: dict) -> bool:
    """Observable now (no hindsight): a crunch state, or late and within the league's sticky margin."""
    if r.get("garbage"):
        return False
    if r.get("crunch"):
        return True
    d = r.get("abs_diff")
    return bool(r.get("late") and d is not None and d <= STICKY_MARGIN.get(r.get("league", ""), 3))


def high_leverage(r: dict) -> bool:
    """A leave here is a hard error under the 3:15 rule: sticky now, or a crunch play due inside the horizon."""
    return sticky(r) or bool(r.get("crunch_next") and not r.get("garbage"))


def apply_priors(r: dict, p: dict | None) -> dict | None:
    """crunch = stay: a model may never say safe_to_leave on a sticky row (and crunch is always must_see)."""
    if not p or p is MISSING or not sticky(r):
        return p
    q = dict(p, safe_to_leave="no")
    if r.get("crunch"):
        q["lock_in"] = "must_see"
    return q


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
                thresholds: dict[str, tuple[float, float]] | None = None,
                priors: bool | None = None) -> dict[str, dict | None]:
    """{row id: cleaned answer or None} for one source. thresholds: per league (baselines only)."""
    out: dict[str, dict | None] = {}
    priors = CRUNCH_STAY_PRIOR if priors is None else priors
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
        out[r["id"]] = apply_priors(r, clean_reasoner(d)) if priors else clean_reasoner(d)
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
    lock_rows = [(r, p) for r, p in usable if lock_graded(r)]
    return {
        "n": n, "answered_pct": pct(answered, n), "usable_json_pct": pct(len(usable), answered),
        "big_base_rate_pct": pct(base, n),
        "must_see_pct": pct(len(ms), len(usable)),
        "must_see_precision": pct(ms_hit, len(ms)),
        "must_see_recall": pct(sum(p["lock_in"] == "must_see" for _, p in big), len(big)),
        "must_see_lift": round(prec / (base / n), 2) if (prec is not None and base) else None,
        "lock_acc": pct(sum(p["lock_in"] == truth_lock(r) for r, p in lock_rows), len(lock_rows)),
        "lock_excluded_rows": len(usable) - len(lock_rows),
        "leave_allowed_pct": pct(len(safe_said), len(usable)),
        "unsafe_leave_pct": pct(sum(not truth_safe(r) for r in safe_said), len(safe_said)),
        # the 3:15 rule: a high-leverage unsafe leave is a hard 0; a low-leverage one is reported as info
        "unsafe_leave_hi_pct": pct(sum(not truth_safe(r) and high_leverage(r) for r in safe_said), len(safe_said)),
        "unsafe_leave_lo_pct": pct(sum(not truth_safe(r) and not high_leverage(r) for r in safe_said), len(safe_said)),
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
        by["all"]["rule_conformance_pct"], by["all"]["rule_rows"] = conformance(test, preds)
        by["all"]["confidence_ece"] = calibration(test, preds)
        for lg in sorted({r.get("league") for r in test}):
            by[lg] = score([r for r in test if r.get("league") == lg], preds)
        res[name] = by
    return res


def table(res: dict, league: str = "all") -> str:
    """Markdown table (rows = lines, columns = sources) for the PR."""
    srcs = [k for k in res if isinstance(res[k], dict) and "all" in res[k]]
    keys = ["n", "answered_pct", "usable_json_pct", "big_base_rate_pct", "must_see_pct", "must_see_precision", "must_see_recall",
            "must_see_lift", "lock_acc", "leave_allowed_pct", "unsafe_leave_pct", "over_cautious_pct", "eta_mae_s"]
    out = ["| line | " + " | ".join(srcs) + " |", "|---|" + "---|" * len(srcs)]
    for k in keys:
        out.append(f"| {k} | " + " | ".join(str(res[s].get(league, {}).get(k, "")) for s in srcs) + " |")
    return "\n".join(out)


# Ship gate (Nick, Nov 3, approved): valid answers 100%, lock_in >= 95% against the gold labels
# (Nick's rules applied exactly, agent/distill/gold.py), and a missed big play after "safe to leave"
# at most 0.5% on held-out nights.
# Nick Nov 4 3:15 PM: a high-leverage unsafe leave (sticky now, or a crunch play due) is a hard 0.
GATE = {"usable_json_pct": ("min", 100.0), "lock_acc": ("min", 95.0), "unsafe_leave_pct": ("max", 0.5),
        "unsafe_leave_hi_pct": ("max", 0.0)}
WATCHDOG_GATE = 95.0


def gate(s: dict, rules: dict = GATE) -> list[str]:
    """Lines of one score that fail the ship gate (empty = ships)."""
    bad = []
    for k, (kind, lim) in rules.items():
        v = s.get(k)
        if v is None or (kind == "min" and v < lim) or (kind == "max" and v > lim):
            bad.append(f"{k}={v} (needs {'>=' if kind == 'min' else '<='} {lim})")
    return bad


# ---- [V7, B11] the split reasoner gate -------------------------------------------------------------
# V6: lock_acc against gold folds hindsight in (a big play in the next horizon): even the 32B teacher
# reaches 73%, so 95% can't be passed by anyone. B11 splits it into what a model can be held to:
#   rule conformance   Nick's rules that the brief already decides (garbage, your team late, NFL drive,
#                      NHL power play): >= 99% of those rows answered by the rule
#   calibration        confidence vs being right about lock_in: ECE <= 0.10
#   non-inferiority    ON vs OFF (today's rules, fitted on valid): unsafe leaves no worse, must-see
#                      recall and lock_acc at most 2 points worse
#   abstain            a confidence cut that keeps unsafe leaves <= 0.5% answers >= 50% of rows
#   usable JSON        100% (constrained decoding makes it so)
SPLIT_GATE = {"usable_json_pct": 100.0, "rule_conformance_pct": 99.0, "confidence_ece": 0.10,
              "noninferior_pts": 2.0, "abstain_coverage_pct": 50.0}


def rule_expect(r: dict) -> str | None:
    """The lock_in the brief alone decides, or None (then only hindsight knows)."""
    from agent.distill.gold import nhl_pp
    from agent.distill.jump import live_drive
    if r.get("garbage"):
        return "none"
    if r.get("mine") and r.get("late"):
        return "must_see"
    if live_drive(r):
        return "must_see"
    if r.get("league") == "nhl" and nhl_pp(r.get("brief", "")):
        return "watch+"
    return None


def conformance(rows: list[dict], preds: dict) -> tuple[float | None, int]:
    hits = n = 0
    for r in rows:
        want = rule_expect(r)
        p = preds.get(r["id"])
        if want is None or not p or p is MISSING:
            continue
        n += 1
        hits += (p["lock_in"] in ("watch", "must_see")) if want == "watch+" else (p["lock_in"] == want)
    return pct(hits, n), n


def calibration(rows: list[dict], preds: dict, bins: int = 10) -> float | None:
    pts = []
    for r in rows:
        p = preds.get(r["id"])
        if not p or p is MISSING:
            continue
        try:
            c = min(1.0, max(0.0, float(p.get("confidence"))))
        except (TypeError, ValueError):
            continue
        pts.append((c, p["lock_in"] == truth_lock(r)))
    if not pts:
        return None
    ece = 0.0
    for b in range(bins):
        sub = [(c, y) for c, y in pts if b / bins <= c < (b + 1) / bins or (b == bins - 1 and c == 1.0)]
        if sub:
            ece += len(sub) / len(pts) * abs(sum(c for c, _ in sub) / len(sub) - sum(y for _, y in sub) / len(sub))
    return round(ece, 4)


def gate_split(res: dict, source: str, dont_know: dict | None = None) -> dict:
    """{line: (value, needs, ok)} for one source; ships only if every line is ok."""
    s, off = res[source]["all"], res.get("today", {}).get("all", {})
    g = SPLIT_GATE
    lines = {"usable_json_pct": (s.get("usable_json_pct"), f">= {g['usable_json_pct']}",
                                 (s.get("usable_json_pct") or 0) >= g["usable_json_pct"]),
             "rule_conformance_pct": (s.get("rule_conformance_pct"), f">= {g['rule_conformance_pct']}",
                                      (s.get("rule_conformance_pct") or 0) >= g["rule_conformance_pct"]),
             "confidence_ece": (s.get("confidence_ece"), f"<= {g['confidence_ece']}",
                                s.get("confidence_ece") is not None and s["confidence_ece"] <= g["confidence_ece"])}
    d = g["noninferior_pts"]
    for k, better in (("unsafe_leave_pct", -1), ("must_see_recall", 1), ("lock_acc", 1)):
        v, o = s.get(k), off.get(k)
        if v is None or o is None:
            lines[f"vs_off_{k}"] = (v, f"vs OFF {o}", False)
            continue
        ok = v <= o if better < 0 else v >= o - d
        lines[f"vs_off_{k}"] = (v, f"{'<=' if better < 0 else '>='} {o if better < 0 else round(o - d, 1)} (OFF {o})", ok)
    if dont_know is not None:
        c = dont_know.get("test_coverage_pct") or 0.0
        u = (dont_know.get("test_answered") or {}).get("unsafe_leave_pct")
        lines["abstain_coverage_pct"] = (c, f">= {g['abstain_coverage_pct']} with unsafe <= 0.5 (test {u})",
                                         dont_know.get("cut") is not None and c >= g["abstain_coverage_pct"]
                                         and u is not None and u <= 0.5)
    return lines
