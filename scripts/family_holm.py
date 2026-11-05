"""Family-wise correction for experiments that share dev nights (PROPOSAL, test writers, EXP-722). Not frozen.

Many crews test candidate labels and inputs on the SAME dev nights (attention labels EXP-1702, recap labels EXP-1800,
clip head EXP-713, market volatility EXP-1301, importance EXP-091/1802 ...). Each alone passes at "CI excludes 0",
but with k tries one passes by luck about k x 2.5% of the time. This grader applies Holm's step-down correction over a
family DECLARED in docs/families.json (frozen with the grader by the integrator), never over whatever arms a run
happens to bring.

    python scripts/family_holm.py results.json [--families docs/families.json] [--alpha 0.05] [--json]

results.json: {"family": "wv-aux-labels", "arms": [{"id": "EXP-1800", "deltas": [..]}, ...]}
deltas = arm minus its baseline, one value per night-bootstrap draw (shared resamples when possible).
p (one-sided, H1: delta > 0) = (1 + #draws with delta <= 0) / (1 + #draws).

Rules:
- The family size m is the number of DECLARED arms. An arm with no result yet, or < 200 draws, keeps p = 1 and
  stays in m, so dropping a weak arm never makes the others easier to pass. It reads PENDING.
- An arm in results.json that the family does not declare FAILs (it was added after the fact).
- An unknown family FAILs.
- An arm PASSES only if its Holm-adjusted p <= alpha.
Self-contained: no imports from training or decider code.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAMILIES = ROOT / "docs" / "families.json"
MIN_DRAWS = 200


def p_one_sided(deltas: list[float]) -> float:
    n = len(deltas)
    return (1 + sum(1 for d in deltas if d <= 0)) / (1 + n)


def holm(pvals: dict[str, float]) -> dict[str, float]:
    """Holm step-down adjusted p-values (monotone, capped at 1)."""
    order = sorted(pvals, key=lambda k: (pvals[k], k))
    m = len(order)
    out, running = {}, 0.0
    for i, k in enumerate(order):
        running = max(running, min(1.0, (m - i) * pvals[k]))
        out[k] = running
    return out


def load_families(path: Path = FAMILIES) -> dict[str, dict]:
    doc = json.loads(Path(path).read_text())
    return {f["name"]: f for f in doc.get("families", [])}


def grade(results: dict, families: dict[str, dict], alpha: float = 0.05) -> list[dict]:
    name = results.get("family")
    fam = families.get(name)
    if fam is None:
        return [{"id": name, "status": "FAIL", "note": f"family {name!r} is not declared in docs/families.json"}]
    declared = list(fam["arms"])
    got = {a["id"]: a.get("deltas", []) for a in results.get("arms", [])}
    res = [{"id": k, "status": "FAIL", "note": "arm not declared in its family (added after the fact)"}
           for k in got if k not in declared]
    p = {k: (p_one_sided(got[k]) if len(got.get(k, [])) >= MIN_DRAWS else 1.0) for k in declared}
    adj = holm(p)
    for k in declared:
        n = len(got.get(k, []))
        if n < MIN_DRAWS:
            res.append({"id": k, "status": "PENDING", "draws": n, "family_size": len(declared),
                        "note": f"no result or < {MIN_DRAWS} draws (counted in m with p = 1)"})
        else:
            res.append({"id": k, "status": "PASS" if adj[k] <= alpha else "FAIL", "p": round(p[k], 4),
                        "p_holm": round(adj[k], 4), "family_size": len(declared)})
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("results", type=Path)
    ap.add_argument("--families", type=Path, default=FAMILIES)
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    results = json.loads(a.results.read_text())
    res = grade(results, load_families(a.families), a.alpha)
    if a.json:
        print(json.dumps({"family": results.get("family"), "alpha": a.alpha, "arms": res}, indent=1))
    else:
        print(f"family {results.get('family')} (alpha {a.alpha}, Holm over declared arms)")
        for r in res:
            print(f"{r['status']:8} {r['id']}  " + (f"p {r['p']} -> holm {r['p_holm']}" if "p" in r else r["note"]))
    return 1 if any(r["status"] == "FAIL" and "note" in r for r in res) else 0


if __name__ == "__main__":
    sys.exit(main())
