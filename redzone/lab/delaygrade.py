"""[EXP-803] Delay precision grader, matching EXP-803's pre-registration exactly (#296, rescoped Cycle 19 #295).
Frozen BEFORE lane 2's arms run (lane 3, Nov 5).

Input: `scripts/eval_sports.py` JSON outputs, one per (harness, seed, arm), named <harness>-<seed>-<arm>.json, each
with `per_night` rows. Arms (#296): A0 control (flags off), A1 finer tick, A2 delay precision, A3 TV-timeout split,
A4 = A1 + A2 + A3 (+ EXP-737/738). Each arm is compared with A0 on the SAME harness (plain / resync), seeds of a
night pooled into that night's cluster; the night bootstrap resamples the same nights for every arm.

Primary: NBA entries mid-commercial (eval_sports `entries_mid_break`, cutgrade's > 10 s left) per 100 entries.
Guardrails, EVERY seed of the harness (#296): mid-play entries 0 added; plays_missed_entry up <= MISSED_REL relative;
  big seen live (eval_sports `big_live`: big plays seen live) and crunch live not down > SEED_PTS; dead air not up
  > DEAD_UP_PTS. Per seed, on that seed's pooled nights (arm minus A0).
Honesty (lane 3, kept by the integrator): band coverage, the pooled share of truth rows within 2 sigma, must stay
  >= HONEST_2SIGMA. A narrower band that stops covering the truth FAILs the arm.
Verdict per arm:
  PENDING  fewer than MIN_NIGHTS_803 paired nights (EXP-803 row: >= 23 NBA dev nights, EXP-735)
  FAIL     a mid-play entry added on any seed, or band coverage < HONEST_2SIGMA
  A4 (the gated, combined arm, unadjusted): PASS when mid-commercial <= MIDCOM_GATE per 100 AND its night-bootstrap
           upper bound is below A0's lower bound AND every guardrail held; else NO_PASS
  A1-A3 (one family, declared by the integrator in docs/families.json as FAMILY, ids EXP-803:<arm>): reported alone,
           with a Holm-adjusted one-sided p (H1: fewer mid-commercial entries than A0) from scripts/family_holm.py
           over the family's declared arms. LIFT when p_holm <= HOLM_ALPHA and every guardrail held; else NO_LIFT.
  Reported beside, never the verdict: sigma p50, EXP-728's <= MIDCOM_BAR per 100, the paired CI of arm - A0.
"""
from __future__ import annotations

import importlib.util
import json
import re
from collections import defaultdict
from pathlib import Path

import numpy as np

from redzone.lab.reactgrade import DRAWS

MIN_NIGHTS_803 = 23
HONEST_2SIGMA = 0.95   # pooled share of truth rows inside the 2-sigma band
MIDCOM_GATE = 10.0     # A4 passes at <= this many mid-commercial entries per 100 (#296)
MIDCOM_BAR = 5.0       # EXP-728's pre-registered bar, reported beside it
SEED_PTS = 1.0         # big seen live / crunch live may not fall by more than this on any seed (#296)
DEAD_UP_PTS = 0.5      # dead air may not rise by more than this on any seed (#296)
MISSED_REL = 0.05      # plays missed on entry may rise at most this much, relative, on any seed (#296)
DELAY_SEED = 803
CONTROL_ARM = "A0"
GATED_ARM = "A4"
FAMILY = "nba-delay-precision"
FAMILY_ROW = "EXP-803"
HOLM_ALPHA = 0.05
_HOLM = Path(__file__).resolve().parents[2] / "scripts" / "family_holm.py"
_NAME = re.compile(r"^(?P<harness>[A-Za-z0-9_]+)-(?P<seed>\d+)-(?P<arm>[A-Za-z0-9_@.]+)\.json$")


def load_dir(d: Path) -> list[dict]:
    """Every `<harness>-<seed>-<arm>.json` in d -> {harness, seed, arm, per_night}."""
    runs = []
    for p in sorted(d.glob("*.json")):
        m = _NAME.match(p.name)
        if m:
            runs.append({**m.groupdict(), "seed": int(m["seed"]), "per_night": json.loads(p.read_text())["per_night"]})
    return runs


def _sums(rows: list[dict]) -> dict:
    """Pooled counts over per-night rows (any seeds, any nights)."""
    out = defaultdict(float)
    for r in rows:
        for k in ("entries", "entries_mid_break", "entries_mid_play", "plays_missed_entry", "crunch", "crunch_live",
                  "big", "big_live", "dead_s", "watched_s"):
            out[k] += float(r.get(k) or 0)
        h = r.get("sync_honesty") or {}
        n = float(h.get("n") or 0)
        out["sync_n"] += n
        out["sync_in"] += n * float(h.get("within_2sigma") or 0)
        out["sigma_w"] += float(r.get("entries") or 0)
        out["sigma_sum"] += float(r.get("entries") or 0) * float(h.get("sigma_p50") or 0)
    return out


def _rates(s: dict) -> dict:
    per = (lambda a, b: 100.0 * s[a] / s[b] if s[b] else None)
    return {"midcom_per_100": per("entries_mid_break", "entries"), "crunch_live_pct": per("crunch_live", "crunch"),
            "big_live_pct": per("big_live", "big"), "dead_air_pct": per("dead_s", "watched_s"),
            "coverage_2sigma": s["sync_in"] / s["sync_n"] if s["sync_n"] else None,
            "sigma_p50": s["sigma_sum"] / s["sigma_w"] if s["sigma_w"] else None,
            "entries_mid_play": int(s["entries_mid_play"]), "plays_missed_entry": int(s["plays_missed_entry"]),
            "entries": int(s["entries"])}


def _by_night(runs: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for r in runs:
        for n in r["per_night"]:
            out[str(n["night"])].append(n)
    return out


def _boot(nights_of: dict[str, dict[str, list[dict]]], nights: list[str], key: str, draws: int = DRAWS,
          seed: int = DELAY_SEED) -> dict[str, list[float]]:
    """Per arm, `key` on each night-bootstrap resample; the SAME resampled nights for every arm."""
    rng = np.random.default_rng(seed)
    out: dict[str, list[float]] = {a: [] for a in nights_of}
    for _ in range(draws):
        pick = [nights[i] for i in rng.choice(len(nights), size=len(nights), replace=True)]
        for a, by in nights_of.items():
            out[a].append(_rates(_sums([x for n in pick for x in by[n]]))[key])
    return out


def _ci(xs: list) -> tuple[float, float] | None:
    v = [x for x in xs if x is not None]
    if not v:
        return None
    lo, hi = np.percentile(v, [2.5, 97.5])
    return round(float(lo), 2), round(float(hi), 2)


def _holm_module():
    spec = importlib.util.spec_from_file_location("family_holm", _HOLM)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed_checks(hr: list[dict], arm: str, base_arm: str, nights: list[str]) -> dict:
    """Arm minus A0 on every seed both ran, on the paired nights: the #296 guardrails per seed."""
    per_seed = {}
    for s in sorted({r["seed"] for r in hr if r["arm"] == arm} & {r["seed"] for r in hr if r["arm"] == base_arm}):
        ra, rb = (_rates(_sums([x for r in hr if r["arm"] == a and r["seed"] == s for x in r["per_night"]
                                if str(x["night"]) in nights])) for a in (arm, base_arm))
        d = (lambda k: None if ra[k] is None or rb[k] is None else round(ra[k] - rb[k], 2))
        per_seed[s] = {"midcom_per_100": d("midcom_per_100"), "big_seen_live_pct": d("big_live_pct"),
                       "crunch_live_pct": d("crunch_live_pct"), "dead_air_pct": d("dead_air_pct"),
                       "mid_play_added": ra["entries_mid_play"] - rb["entries_mid_play"],
                       "missed_ok": ra["plays_missed_entry"] <= rb["plays_missed_entry"] * (1 + MISSED_REL)}
    v = list(per_seed.values())
    guards = {"mid_play": all(x["mid_play_added"] <= 0 for x in v),
              "plays_missed": all(x["missed_ok"] for x in v),
              "big_seen_live": all((x["big_seen_live_pct"] or 0) >= -SEED_PTS for x in v),
              "crunch_live": all((x["crunch_live_pct"] or 0) >= -SEED_PTS for x in v),
              "dead_air": all((x["dead_air_pct"] or 0) <= DEAD_UP_PTS for x in v)}
    return {"per_seed_delta": per_seed, "guardrails": guards}


def grade(runs: list[dict], base_arm: str = CONTROL_ARM, min_nights: int = MIN_NIGHTS_803,
          families: dict | None = None, gated: str = GATED_ARM) -> dict:
    """{harness: {nights, arms: {arm: rates + per-seed deltas + CIs + verdict}, family}}."""
    holm = _holm_module()
    fams = holm.load_families() if families is None else families
    out = {}
    for harness in sorted({r["harness"] for r in runs}):
        hr = [r for r in runs if r["harness"] == harness]
        if not any(r["arm"] == base_arm for r in hr):
            out[harness] = {"error": f"control arm {base_arm!r} missing"}
            continue
        arms = sorted({r["arm"] for r in hr})
        nights_of = {a: _by_night([r for r in hr if r["arm"] == a]) for a in arms}
        nights = sorted(set.intersection(*(set(v) for v in nights_of.values())))
        boot = _boot(nights_of, nights, "midcom_per_100") if len(nights) >= 2 else {a: [] for a in arms}
        base = _rates(_sums([x for n in nights for x in nights_of[base_arm][n]]))
        res = {base_arm: {**base, "ci_rate": _ci(boot[base_arm]), "verdict": "CONTROL"}}
        fam_in = {"family": FAMILY, "arms": []}
        for a in arms:
            if a == base_arm:
                continue
            m = _rates(_sums([x for n in nights for x in nights_of[a][n]]))
            chk = _seed_checks(hr, a, base_arm, nights)
            diffs = [x - y for x, y in zip(boot[a], boot[base_arm]) if x is not None and y is not None]
            if a != gated:
                fam_in["arms"].append({"id": f"{FAMILY_ROW}:{a}", "deltas": [-d for d in diffs]})  # H1: fewer
            res[a] = {**m, **chk, "ci_rate": _ci(boot[a]), "ci_vs_control": _ci(diffs),
                      "band_dishonest": m["coverage_2sigma"] is not None and m["coverage_2sigma"] < HONEST_2SIGMA,
                      "bar_728": m["midcom_per_100"] is not None and m["midcom_per_100"] <= MIDCOM_BAR}
        fam_out = {r["id"]: r for r in holm.grade(fam_in, fams, HOLM_ALPHA)} if fam_in["arms"] else {}
        for a, m in res.items():
            if a == base_arm:
                continue
            fam = fam_out.get(f"{FAMILY_ROW}:{a}") or fam_out.get(FAMILY)
            m["holm"] = None if a == gated else fam
            if len(nights) < min_nights:
                m["verdict"] = "PENDING"
            elif not m["guardrails"]["mid_play"] or m["band_dishonest"]:
                m["verdict"] = "FAIL"
            elif a == gated:
                lo0, hi4 = (res[base_arm]["ci_rate"] or (None, None))[0], (m["ci_rate"] or (None, None))[1]
                ok = (m["midcom_per_100"] is not None and m["midcom_per_100"] <= MIDCOM_GATE and lo0 is not None
                      and hi4 is not None and hi4 < lo0 and all(m["guardrails"].values()))
                m["verdict"] = "PASS" if ok else "NO_PASS"
            else:
                lift = bool(fam) and fam.get("status") == "PASS" and all(m["guardrails"].values())
                m["verdict"] = "LIFT" if lift else "NO_LIFT"
        out[harness] = {"nights": len(nights), "arms": res, "family": FAMILY,
                        "family_declared": FAMILY in fams}
    return out


def markdown(rep: dict) -> str:
    lines = []
    for h, r in rep.items():
        if "error" in r:
            lines.append(f"### {h}: {r['error']}")
            continue
        lines += [f"### {h} ({r['nights']} nights)", "",
                  "| arm | midcom/100 | rate CI | CI vs A0 | Holm p | sigma p50 | 2-sigma coverage | mid-play | missed | "
                  "crunch live | big seen live | dead air | guardrails | verdict | <=5/100 |", "|" + "---|" * 15]
        for a, m in r["arms"].items():
            f = (lambda x, d=1: "-" if x is None else f"{x:.{d}f}")
            g, hp = m.get("guardrails"), m.get("holm") or {}
            lines.append(f"| {a} | {f(m['midcom_per_100'])} | {m.get('ci_rate') or '-'} | {m.get('ci_vs_control') or '-'} | "
                         f"{hp.get('p_holm', hp.get('status', '-')) if hp else '-'} | {f(m['sigma_p50'], 2)} | "
                         f"{f(m['coverage_2sigma'], 3)} | {m['entries_mid_play']} | {m['plays_missed_entry']} | "
                         f"{f(m['crunch_live_pct'])} | {f(m['big_live_pct'])} | {f(m['dead_air_pct'])} | "
                         f"{'-' if g is None else ', '.join(k for k, ok in g.items() if not ok) or 'held'} | "
                         f"{m['verdict']} | {m.get('bar_728', '-')} |")
        if not r.get("family_declared", True):
            lines.append(f"Family {r['family']} is not declared in docs/families.json yet: A1-A3 cannot read LIFT.")
        lines.append("")
    return "\n".join(lines)
