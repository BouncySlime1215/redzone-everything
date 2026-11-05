"""[EXP-803] Delay precision grader (Cycle 19 rescope, #295): does a tighter delay belief land entries late in the
break without lying about it? Frozen BEFORE lane 2's arms run (lane 3, Nov 5).

Input: `scripts/eval_sports.py` JSON outputs, one per (harness, seed, arm), each with `per_night` rows. Arms are
compared with the base arm on the SAME harness (plain / resync / ...), seeds of a night pooled into that night's
cluster, paired bootstrap over nights (the same resampled nights for every arm).

Per arm vs base, per harness:
  primary     entries mid-commercial per 100 entries (eval_sports `entries_mid_break`: landed in a break with more
              than cutgrade's BREAK_TAIL_BAR left)
  precision   delay band sigma p50 (sync_honesty.sigma_p50, entry-weighted mean over nights), reported vs base
  honesty     band coverage: pooled share of truth rows within 2 sigma >= HONEST_2SIGMA. A narrower band that stops
              covering the truth is a lie, so it FAILs the arm however good the entries look.
  hard zeros  mid-play entries: the arm may add none (pooled count <= base) -- rule #1
  guardrails  (EXP-728's, per seed, as pre-registered there): crunch live % and big live % not down by more than
              SEED_PTS on any seed; plays missed on entry up at most MISSED_REL relative (pooled); dead air not up
              (paired CI lower bound of arm - base <= 0)
Verdict:
  PENDING  fewer than MIN_NIGHTS_803 paired nights (EXP-803 row: >= 23 NBA dev nights, EXP-735)
  FAIL     a hard zero broken or band coverage under HONEST_2SIGMA
  WIN      mid-commercial CI upper bound (arm - base) < 0, sigma p50 below base, every guardrail held
  LOSE     mid-commercial CI lower bound > 0
  NO_WIN   anything else
  gate     reported beside the verdict: the row's bar (mid-commercial MIDCOM_GATE per 100) and EXP-728's
           (<= MIDCOM_BAR per 100) on the arm's point estimate; a WIN that misses the gate still ships OFF.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

import numpy as np

from redzone.lab.reactgrade import DRAWS

MIN_NIGHTS_803 = 23
HONEST_2SIGMA = 0.95   # pooled share of truth rows inside the 2-sigma band
MIDCOM_GATE = 0.0      # mid-commercial entries per 100: EXP-803's row
MIDCOM_BAR = 5.0       # EXP-728's pre-registered bar, reported beside it
SEED_PTS = 1.0         # crunch live / big live may not fall by more than this on any seed (EXP-728)
MISSED_REL = 0.05      # plays missed on entry may rise at most this much, relative (EXP-728)
DELAY_SEED = 803
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


def _paired_ci(base: dict[str, list[dict]], arm: dict[str, list[dict]], nights: list[str], key: str,
               draws: int = DRAWS, seed: int = DELAY_SEED) -> tuple[float, float] | None:
    rng = np.random.default_rng(seed)
    idx = np.arange(len(nights))
    diffs = []
    for _ in range(draws):
        pick = [nights[i] for i in rng.choice(idx, size=len(idx), replace=True)]
        a = _rates(_sums([x for n in pick for x in arm[n]]))[key]
        b = _rates(_sums([x for n in pick for x in base[n]]))[key]
        if a is not None and b is not None:
            diffs.append(a - b)
    if not diffs:
        return None
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    return round(float(lo), 2), round(float(hi), 2)


def grade(runs: list[dict], base_arm: str = "base", min_nights: int = MIN_NIGHTS_803) -> dict:
    """{harness: {arms: {arm: rates + per-seed deltas + CIs + verdict}, nights}}."""
    out = {}
    for harness in sorted({r["harness"] for r in runs}):
        hr = [r for r in runs if r["harness"] == harness]
        if not any(r["arm"] == base_arm for r in hr):
            out[harness] = {"error": f"base arm {base_arm!r} missing"}
            continue
        arms = sorted({r["arm"] for r in hr})
        nights_of = {a: _by_night([r for r in hr if r["arm"] == a]) for a in arms}
        nights = sorted(set.intersection(*(set(v) for v in nights_of.values())))
        b_n = nights_of[base_arm]
        base = _rates(_sums([x for n in nights for x in b_n[n]]))
        res = {base_arm: {**base, "verdict": "BASE"}}
        for a in arms:
            if a == base_arm:
                continue
            a_n = nights_of[a]
            m = _rates(_sums([x for n in nights for x in a_n[n]]))
            seeds = sorted({r["seed"] for r in hr if r["arm"] == a} & {r["seed"] for r in hr if r["arm"] == base_arm})
            per_seed = {}
            for s in seeds:
                ra = _rates(_sums([x for r in hr if r["arm"] == a and r["seed"] == s for x in r["per_night"]
                                   if str(x["night"]) in nights]))
                rb = _rates(_sums([x for r in hr if r["arm"] == base_arm and r["seed"] == s for x in r["per_night"]
                                   if str(x["night"]) in nights]))
                per_seed[s] = {k: (None if ra[k] is None or rb[k] is None else round(ra[k] - rb[k], 2))
                               for k in ("midcom_per_100", "crunch_live_pct", "big_live_pct", "dead_air_pct")}
            ci = {k: _paired_ci(b_n, a_n, nights, k) for k in ("midcom_per_100", "dead_air_pct")} if len(nights) >= 2 else {}
            guards = {
                "crunch_live": all((d["crunch_live_pct"] or 0) >= -SEED_PTS for d in per_seed.values()),
                "big_live": all((d["big_live_pct"] or 0) >= -SEED_PTS for d in per_seed.values()),
                "plays_missed": m["plays_missed_entry"] <= base["plays_missed_entry"] * (1 + MISSED_REL),
                "dead_air": bool(ci.get("dead_air_pct")) and ci["dead_air_pct"][0] <= 0}
            hard = m["entries_mid_play"] > base["entries_mid_play"]
            dishonest = m["coverage_2sigma"] is not None and m["coverage_2sigma"] < HONEST_2SIGMA
            mc = ci.get("midcom_per_100")
            if len(nights) < min_nights:
                v = "PENDING"
            elif hard or dishonest:
                v = "FAIL"
            elif mc and mc[1] < 0 and (m["sigma_p50"] or 0) < (base["sigma_p50"] or 0) and all(guards.values()):
                v = "WIN"
            elif mc and mc[0] > 0:
                v = "LOSE"
            else:
                v = "NO_WIN"
            mpc = m["midcom_per_100"]
            res[a] = {**m, "per_seed_delta": per_seed, "ci_vs_base": ci, "guardrails": guards,
                      "mid_play_added": hard, "band_dishonest": dishonest, "verdict": v,
                      "gate_803": mpc is not None and mpc <= MIDCOM_GATE, "bar_728": mpc is not None and mpc <= MIDCOM_BAR}
        out[harness] = {"nights": len(nights), "arms": res}
    return out


def markdown(rep: dict) -> str:
    lines = []
    for h, r in rep.items():
        if "error" in r:
            lines.append(f"### {h}: {r['error']}")
            continue
        lines += [f"### {h} ({r['nights']} nights)", "",
                  "| arm | midcom/100 | CI vs base | sigma p50 | 2-sigma coverage | mid-play | missed | crunch live | "
                  "big live | dead air | guardrails | verdict | <=5/100 |", "|" + "---|" * 13]
        for a, m in r["arms"].items():
            f = (lambda x, d=1: "-" if x is None else f"{x:.{d}f}")
            ci = (m.get("ci_vs_base") or {}).get("midcom_per_100")
            g = m.get("guardrails")
            lines.append(f"| {a} | {f(m['midcom_per_100'])} | {ci or '-'} | {f(m['sigma_p50'], 2)} | "
                         f"{f(m['coverage_2sigma'], 3)} | {m['entries_mid_play']} | {m['plays_missed_entry']} | "
                         f"{f(m['crunch_live_pct'])} | {f(m['big_live_pct'])} | {f(m['dead_air_pct'])} | "
                         f"{'-' if g is None else ', '.join(k for k, ok in g.items() if not ok) or 'held'} | "
                         f"{m['verdict']} | {m.get('bar_728', '-')} |")
        lines.append("")
    return "\n".join(lines)
