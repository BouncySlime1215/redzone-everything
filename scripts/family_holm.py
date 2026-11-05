"""Family-wise correction for experiments that share dev nights (PROPOSAL, test writers, EXP-722). Not frozen.

Many crews now test candidate labels and inputs on the SAME dev nights (attention labels EXP-1701/1702, recap labels
EXP-1800, clip head EXP-713, market volatility EXP-1301, importance EXP-091/1802 ...). Each alone passes at "CI excludes
0", but with k tries one passes by luck about k x 2.5% of the time. This grader takes each arm's night-bootstrap
deltas (arm minus its baseline, one value per bootstrap draw, drawn over the SAME night resamples when possible) and
applies Holm's step-down correction at family level alpha.

    python scripts/family_holm.py family.json [--alpha 0.05] [--json]

family.json: {"family": "wv-aux-labels", "arms": [{"id": "EXP-1702-wiki", "deltas": [..]}, ...]}
p (one-sided, H1: delta > 0) = (1 + #draws with delta <= 0) / (1 + #draws). An arm PASSES the family only if its
Holm-adjusted p <= alpha. Arms with fewer than 200 draws are PENDING (underpowered bootstrap).
Self-contained: no imports from training or decider code.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

MIN_DRAWS = 200


def p_one_sided(deltas: list[float]) -> float:
    n = len(deltas)
    return (1 + sum(1 for d in deltas if d <= 0)) / (1 + n)


def holm(pvals: dict[str, float]) -> dict[str, float]:
    """Holm step-down adjusted p-values (monotone, capped at 1)."""
    order = sorted(pvals, key=pvals.get)
    m = len(order)
    out, running = {}, 0.0
    for i, k in enumerate(order):
        running = max(running, min(1.0, (m - i) * pvals[k]))
        out[k] = running
    return out


def grade(family: dict, alpha: float = 0.05) -> list[dict]:
    arms = family.get("arms", [])
    ready = {a["id"]: p_one_sided(a["deltas"]) for a in arms if len(a.get("deltas", [])) >= MIN_DRAWS}
    adj = holm(ready) if ready else {}
    res = []
    for a in arms:
        if a["id"] not in ready:
            res.append({"id": a["id"], "status": "PENDING", "draws": len(a.get("deltas", [])),
                        "note": f"needs >= {MIN_DRAWS} bootstrap draws"})
            continue
        res.append({"id": a["id"], "status": "PASS" if adj[a["id"]] <= alpha else "FAIL", "p": round(ready[a["id"]], 4),
                    "p_holm": round(adj[a["id"]], 4), "family_size": len(ready)})
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("family", type=Path)
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    fam = json.loads(a.family.read_text())
    res = grade(fam, a.alpha)
    if a.json:
        print(json.dumps({"family": fam.get("family"), "alpha": a.alpha, "arms": res}, indent=1))
    else:
        print(f"family {fam.get('family')} (alpha {a.alpha}, Holm)")
        for r in res:
            print(f"{r['status']:8} {r['id']}  " + (f"p {r['p']} -> holm {r['p_holm']}" if "p" in r else r["note"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
