"""[EXP-040] The end-of-night "worth it?" rating as an evaluation line (watch-science playbook §6).

EVALUATION ONLY. data/worth.jsonl is Nick's own answer to "was tonight worth it?" (1-5, one tap on the
Slate complete card, phone.worth). It grades a release; it is never a training label, a reward or a
feature (self-taught rule: Nick's taps are controls and evaluation, never training signal). Nothing in
redzone/noggin reads it, and tests/test_worth.py fails if anything there starts to.

The line "worth it >= live": nights rated while the CANDIDATE drove the TV against nights rated while
the LIVE release drove it, the night being the unit (one rating per night, the last tap wins).
Pre-registered bar (docs/lanes/experience.md, EXP-040):
  PASS     >= MIN_NIGHTS rated nights per side and the 95 % bootstrap LOWER bound of
           mean(candidate) - mean(live) >= -MARGIN (non-inferior by at most a quarter point)
  FAIL     the UPPER bound < -MARGIN (clearly worse)
  PENDING  anything else, including too few nights. Pending never counts as passing.
Warning rule (§6): watch time up while the rating is down blocks a change; `warning()` flags it.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

MIN_NIGHTS = 4      # rated nights per side before the line can PASS or FAIL
MARGIN = 0.25       # on the 1-5 scale: the candidate may trail live by at most this much
BOOT_DRAWS = 2000


def load(path: Path) -> dict[str, dict]:
    """night -> its last row ({night, ts, rating, agency?, regret?}). Bad lines are skipped."""
    out: dict[str, dict] = {}
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        r = row.get("rating") if isinstance(row, dict) else None
        if isinstance(r, int) and 1 <= r <= 5 and row.get("night"):
            out[str(row["night"])] = row
    return out


def ratings(rows: dict[str, dict], nights) -> list[int]:
    return [rows[n]["rating"] for n in sorted(set(nights)) if n in rows]


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def compare(live: list[int], cand: list[int], draws: int = BOOT_DRAWS, seed: int = 7) -> dict:
    """The "worth it >= live" line from per-night ratings on each side."""
    out = {"live_n": len(live), "cand_n": len(cand),
           "live_mean": round(_mean(live), 2) if live else None,
           "cand_mean": round(_mean(cand), 2) if cand else None,
           "diff": None, "lo": None, "hi": None, "status": "PENDING", "margin": MARGIN, "min_nights": MIN_NIGHTS}
    if not live or not cand:
        return out
    rng = random.Random(seed)
    diffs = sorted(_mean([cand[rng.randrange(len(cand))] for _ in cand])
                   - _mean([live[rng.randrange(len(live))] for _ in live]) for _ in range(draws))
    lo, hi = diffs[int(0.025 * draws)], diffs[min(draws - 1, int(0.975 * draws))]
    out.update(diff=round(_mean(cand) - _mean(live), 2), lo=round(lo, 2), hi=round(hi, 2))
    if hi < -MARGIN:
        out["status"] = "FAIL"
    elif len(live) >= MIN_NIGHTS and len(cand) >= MIN_NIGHTS and lo >= -MARGIN:
        out["status"] = "PASS"
    return out


def warning(watch_delta_pct: float | None, line: dict) -> bool:
    """§6 warning rule: watch time up while the rating is down (wanting over liking) blocks the change."""
    return bool(watch_delta_pct is not None and watch_delta_pct > 0 and line.get("diff") is not None
                and line["diff"] < 0)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="EXP-040: worth it >= live, from data/worth.jsonl (evaluation only)")
    ap.add_argument("--log", default=str(Path(__file__).resolve().parents[2] / "data" / "worth.jsonl"))
    ap.add_argument("--live", nargs="*", default=[], help="nights the live release drove the TV (YYYY-MM-DD)")
    ap.add_argument("--candidate", nargs="*", default=[], help="nights the candidate drove the TV")
    a = ap.parse_args(argv)
    rows = load(Path(a.log))
    print(json.dumps({"rated_nights": len(rows), **compare(ratings(rows, a.live), ratings(rows, a.candidate))}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
