"""The per-night cards of one arm folded into ONE verdict: the V2-PLAN section 8.4 scorecard, paired by night against a
baseline arm, with run-registry metadata so the verdict can become a registry line later (F-02).

    v = build_verdict(arm_desc, cards, base_cards, meta, prereg=None, seed=0)
    text = dumps(v)            # canonical bytes: the same inputs always give the same text

Verdict rules (V2-PLAN 8.3 to 8.5), applied in this order:
    FAIL          any Rule #1 count (joins mid-play, leaves mid-play, high-leverage mid-play leaves, by the frozen
                  grader) higher than the baseline's on any night, or a pre-registered bar missed
    INCONCLUSIVE  no baseline to pair with; no pre-registered bars; uncommitted changes; any night inside the tuning
                  window (redzone/lab/fence.py: tuning evidence, not a held-out claim)
    PASS          none of the above and every bar met
Intervals resample nights (paired), ARMEVAL_BOOT draws from random.Random(seed): 90 % as the existing harness reports,
95 % for release decisions. Nothing here reads a clock, a path or the environment: the inputs fix the bytes.
Why: WORKPLAN v2 F-04 and V2-PLAN 8.4: one verdict per arm, comparable across arms, writable to the registry.
"""
from __future__ import annotations

import hashlib
import json
import random

ARMEVAL_SCHEMA = "armeval-verdict/1"
ARMEVAL_BOOT = 2000
ARMEVAL_RULE1 = ("joins_mid_play", "leaves_mid_play", "hl_leaves_mid_play")
# paired metrics: (path in the card, higher is better)
ARMEVAL_PAIRED = (("rule1.joins_mid_play", False), ("rule1.leaves_mid_play", False), ("rule1.hl_leaves_mid_play", False),
                  ("value.live_points", True), ("big.seen_live", True), ("big.must_sees_missed", False),
                  ("freshness.worst_unseen_s", False), ("freshness.mean_unseen_s", False),
                  ("pace.jumps_per_min", True), ("pace.warranted_share", True))
ARMEVAL_ENGINE = "park_replay: the show on fake sticks and a fake clock (scripts/park_replay.py build + drive)"


def canon_json(obj) -> str:
    """Canonical text: sorted keys, fixed separators, newline at the end."""
    return json.dumps(obj, sort_keys=True, indent=1, ensure_ascii=True, allow_nan=False) + "\n"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def get_path(card: dict, path: str):
    cur = card
    for k in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _mean(xs: list) -> float:
    return sum(xs) / len(xs)


def paired(deltas: list, seed: int) -> dict:
    """Mean of per-night deltas and percentile intervals from resampling nights (paired: one delta per night)."""
    n = len(deltas)
    rng = random.Random(seed)
    means = sorted(_mean([deltas[rng.randrange(n)] for _ in range(n)]) for _ in range(ARMEVAL_BOOT))

    def q(p: float) -> float:
        return round(means[min(len(means) - 1, max(0, int(p * (len(means) - 1) + 0.5)))], 4)
    return {"n": n, "mean_delta": round(_mean(deltas), 4), "ci90": [q(0.05), q(0.95)], "ci95": [q(0.025), q(0.975)]}


def totals(cards: list) -> dict:
    """The scorecard over all nights: counts summed, pace over all minutes, shares and freshness averaged per night."""
    mins = sum(c["minutes"] for c in cards)

    def s(p: str):
        return sum(get_path(c, p) or 0 for c in cards)

    def avg(p: str):
        v = [x for c in cards if (x := get_path(c, p)) is not None]
        return round(_mean(v), 4) if v else None
    jumps, wn, wok = s("pace.jumps"), s("pace.warrant_n"), s("pace.warranted")
    big_t, big_s = s("big.total"), s("big.seen_live")
    return {
        "nights": len(cards), "minutes": round(mins, 1),
        "rule1": {k: s(f"rule1.{k}") for k in (*ARMEVAL_RULE1, "joins_mid_play_strict", "joins", "leaves", "judge_mid_entry",
                                                "judge_mid_leave", "judge_hl_leave", "joins_after_miss")},
        "value": {"live_points": round(s("value.live_points"), 3), "live_share_mean": avg("value.live_share"),
                  "catchup_points": None},
        "big": {"seen_live": big_s, "total": big_t, "must_sees_missed": big_t - big_s,
                "seen_live_share": round(big_s / big_t, 4) if big_t else None},
        "freshness": {"worst_unseen_s": max((get_path(c, "freshness.worst_unseen_s") or 0) for c in cards) if cards else None,
                      "mean_unseen_s_mean": avg("freshness.mean_unseen_s"),
                      "worst_unseen_live_s": max((get_path(c, "freshness.worst_unseen_live_s") or 0) for c in cards) if cards else None},
        "pace": {"jumps": jumps, "jumps_per_min": round(jumps / mins, 3) if mins else None, "warrant_n": wn, "warranted": wok,
                 "warranted_share": round(wok / wn, 4) if wn else None, "peak_1m_max": max((get_path(c, "pace.peak_1m") or 0) for c in cards) if cards else None},
        "catchup": {"coverage": None, "lateness_s": None},
        "yes_rate": None,
        "cost": {"planner_searches": s("cost.planner_searches"), "show_seconds": s("cost.show_seconds")},
        "by_mix": {m: sum(1 for c in cards if c["mix"] == m) for m in sorted({c["mix"] for c in cards})},
    }


def rule1_rise(cards: list, base: list) -> list:
    """Nights where the arm's Rule #1 counts are above the baseline's own (any rise fails the arm)."""
    out = []
    for c, b in zip(cards, base):
        up = {k: [b["rule1"][k], c["rule1"][k]] for k in ARMEVAL_RULE1 if c["rule1"][k] > b["rule1"][k]}
        if up:
            out.append({"night": c["night"], "baseline_then_arm": up})
    return out


def bars_check(prereg: dict, comp: dict) -> list:
    """Pre-registered bars: {"bars": {"value.live_points": {"delta_at_least": 2, "ci90_low_above": 0}, ...}}.
    Returns one line per bar missed (or not measurable)."""
    miss = []
    for metric, bar in sorted((prereg.get("bars") or {}).items()):
        got = comp.get(metric)
        if got is None:
            miss.append(f"{metric}: not measured (no paired result)")
            continue
        if "delta_at_least" in bar and got["mean_delta"] < bar["delta_at_least"]:
            miss.append(f"{metric}: mean delta {got['mean_delta']} below the bar {bar['delta_at_least']}")
        if "ci90_low_above" in bar and got["ci90"][0] <= bar["ci90_low_above"]:
            miss.append(f"{metric}: 90% interval low end {got['ci90'][0]} not above {bar['ci90_low_above']}")
        if "ci95_low_above" in bar and got["ci95"][0] <= bar["ci95_low_above"]:
            miss.append(f"{metric}: 95% interval low end {got['ci95'][0]} not above {bar['ci95_low_above']}")
    return miss


def registry_record(arm_name: str, meta: dict, verdict: str, score: dict, manifest_sha256: str) -> dict:
    """The F-02 registry `register` fields for this verdict (validated by redzone.models_rt.registry.validate_record).
    artifact_path is relative to the verdict's own folder (raw/manifest.json); the writer (F-03) rebases it."""
    from redzone.models_rt import registry
    flat = {}

    def walk(prefix: str, d) -> None:
        for k, v in sorted(d.items()):
            key = f"{prefix}{k}"
            if isinstance(v, dict):
                walk(key + ".", v)
            elif isinstance(v, (int, float, str, bool)) and v is not None:
                flat[key] = v
    walk("", {k: v for k, v in score.items() if k != "by_mix"})
    rec = {"run_id": f"armeval-{arm_name}-{meta['config_hash'][:12]}", "git_sha": meta["git_sha"],
           "code_hash": meta["code_hash"], "config_hash": meta["config_hash"],
           "data_manifest_hash": meta["data_manifest_hash"], "prereg_hash": meta["prereg_hash"], "seed": meta["seed"],
           "metrics": flat, "verdict": verdict, "artifact_path": "raw/manifest.json", "artifact_sha256": manifest_sha256,
           "promoted": False, "gpu_type": "cpu", "hours": 0.0,
           "note": "F-04 arm verdict; artifact_path is relative to the verdict folder; CPU time is in timing.json"}
    return registry.validate_record(rec)


def build_verdict(arm_desc: dict, cards: list, base_cards: list | None, meta: dict, *, prereg: dict | None = None,
                  seed: int = 0, raw_manifest: dict | None = None, extra: dict | None = None) -> dict:
    """The one JSON verdict. base_cards: the baseline arm's cards on the same nights in the same order (or None)."""
    if base_cards is not None and [c["night"] for c in cards] != [b["night"] for b in base_cards]:
        raise ValueError("the baseline must be scored on the same nights, in the same order")
    score = totals(cards)
    comp, reasons, fails = {}, [], []
    if base_cards is not None:
        for path, _up in ARMEVAL_PAIRED:
            ds = [(get_path(c, path), get_path(b, path)) for c, b in zip(cards, base_cards)]
            if all(a is not None and b is not None for a, b in ds):
                comp[path] = {**paired([a - b for a, b in ds], seed), "higher_is_better": _up}
        rise = rule1_rise(cards, base_cards)
        if rise:
            fails.append(f"Rule #1 rose above the baseline on {len(rise)} night(s)")
    else:
        rise = None
        reasons.append("no baseline arm to pair with")
    if prereg is not None:
        fails += bars_check(prereg, comp)
    else:
        reasons.append("no pre-registered bars: exploratory")
    if meta.get("dirty"):
        reasons.append("uncommitted changes in the checkout: exploratory")
    if meta.get("fence", {}).get("tuning_nights"):
        reasons.append("scored on tuning-window nights: tuning evidence, not a held-out claim (V2-PLAN 8.1)")
    verdict = "FAIL" if fails else ("INCONCLUSIVE" if reasons else "PASS")
    manifest = raw_manifest or {}
    manifest_sha = sha256_text(canon_json(manifest))
    out = {
        "schema": ARMEVAL_SCHEMA, "engine": ARMEVAL_ENGINE, "arm": arm_desc, "baseline": meta.get("baseline"),
        "nights": [c["night"] for c in cards], "meta": meta, "scorecard": score, "per_night": cards,
        "baseline_scorecard": totals(base_cards) if base_cards is not None else None, "baseline_per_night": base_cards,
        "paired_vs_baseline": comp, "rule1_rise": rise, "verdict": verdict, "fails": fails, "why_not_pass": reasons,
        "raw_logs": manifest, "raw_manifest_sha256": manifest_sha,
        "registry_record": registry_record(arm_desc["name"], meta, verdict, score, manifest_sha),
    }
    if extra:
        out.update(extra)
    return out
