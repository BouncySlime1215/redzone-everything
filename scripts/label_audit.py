"""[EXP-302] Is a lock label our own clock rule? Clock-only AUC per label and league (Nick, Nov 5 1:50 AM).

    python scripts/label_audit.py [--leagues nba nfl nhl] [--archive data/archive] [--json out.json]

For every archived play: the old label `y` (a big or crunch play within reasoner_data.HORIZON_S, the rule
the head re-learns) and the outcome label `y_out` with its parts (redzone.lab.outcome_label). Rates per bin
of period + clock minute + margin are learned on train+valid nights; the AUC is read on test nights, with a
95% bootstrap over test nights. Exit 1 if a GRADED label (y_out) reaches outcome_label.CONTEXT_AUC_MAX:
then the label says nothing the clock doesn't, and grading lock calls on it measures our rule.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path


def rows_for(lg, d: Path) -> tuple[list[dict], object]:
    from redzone.lab import outcome_label as O, sportsgrade, splits
    from redzone.lab.backtest import Replay
    from redzone.lab.reasoner_data import HORIZON_S
    from redzone.sources import espn
    nights = splits.archive_nights(d)
    night_of = {g: n for n, gs in nights.items() for g in gs}
    out: list[dict] = []
    for f in sorted(d.glob("summary_*.json")):
        gid = f.stem.split("_")[-1]
        if gid not in night_of:
            continue
        try:
            r = Replay.load(lg, f)
        except (OSError, ValueError):
            continue
        if r is None or len(r.plays) < 20:
            continue
        lab = O.label_game(lg, r.plays, r.wall)
        hot, prev = [], None
        for p in r.plays:
            hot.append(sportsgrade.is_big(lg, p, prev) or sportsgrade.is_crunch(lg, p))
            prev = p
        h, n, j = HORIZON_S[lg.base.value], len(r.plays), 0
        for i, p in enumerate(r.plays):
            j = max(j, i)
            while j + 1 < n and r.wall[j + 1] <= r.wall[i] + h:
                j += 1
            hs, as_ = espn._int(p.get("homeScore")), espn._int(p.get("awayScore"))
            out.append({"night": night_of[gid], "period": espn._int(espn._get(p, "period", "number")),
                        "clock_s": O._left_s(lg, p), "abs_diff": abs(hs - as_) if hs is not None and as_ is not None else None,
                        "y": int(any(hot[i + 1:j + 1])), **lab[i]})
    return out, splits.split_nights(nights)


def audit(rows: list[dict], split, draws: int = 300, seed: int = 302) -> dict:
    from redzone.lab import outcome_label as O
    fit = set(split.train) | set(split.valid)
    tr = [r for r in rows if r["night"] in fit]
    te = [r for r in rows if r["night"] in set(split.test)]
    by: dict[str, list[dict]] = {}
    for r in te:
        by.setdefault(r["night"], []).append(r)
    nights = sorted(by)
    rng = random.Random(seed)
    res = {"test_nights": len(nights), "test_rows": len(te)}
    for lab in ("y", "y_out", "flip", "wp_swing", "close_finish"):
        a = O.context_auc(tr, te, lab)
        boots = sorted(b for _ in range(draws if len(nights) > 1 else 0)
                       if (b := O.context_auc(tr, [x for _ in nights for x in by[rng.choice(nights)]], lab)) == b)
        ci = [round(boots[int(0.025 * len(boots))], 4), round(boots[min(len(boots) - 1, int(0.975 * len(boots)))], 4)] if boots else None
        res[lab] = {"rate": round(sum(r[lab] for r in te) / max(1, len(te)), 4), "context_auc": round(a, 4), "ci95": ci}
    return res


def main(argv=None) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from redzone.lab import outcome_label as O
    from redzone.models import League
    ap = argparse.ArgumentParser()
    ap.add_argument("--leagues", nargs="+", default=["nba", "nfl", "nhl"])
    ap.add_argument("--archive", type=Path, default=Path("data/archive"))
    ap.add_argument("--json", type=Path)
    a = ap.parse_args(argv)
    out, bad = {}, []
    for name in a.leagues:
        d = a.archive / name
        if not d.exists():
            print(f"{name}: no archive at {d} (scripts/fetch_eval_data.py)")
            continue
        rows, split = rows_for(League(name), d)
        out[name] = audit(rows, split)
        print(name, json.dumps(out[name]))
        if out[name]["y_out"]["context_auc"] >= O.CONTEXT_AUC_MAX:
            bad.append(name)
    if a.json:
        a.json.write_text(json.dumps(out, indent=1) + "\n")
    for name in bad:
        print(f"FAIL {name}: y_out is readable off the clock (AUC >= {O.CONTEXT_AUC_MAX})")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
