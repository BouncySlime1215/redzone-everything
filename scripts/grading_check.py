"""Grading rules in CI (docs/grading.md; Nick, Nov 4 2026 6:40 PM ET: "grading even SHARPER").

    python scripts/grading_check.py                  # exit 1 on any FAIL
    python scripts/grading_check.py --base origin/integration/next   # also: no moved goalposts
    python scripts/grading_check.py --refreeze       # INTEGRATOR ONLY: re-stamp docs/grader_freeze.json

Checks:
  G1 grader freeze   every grader file matches its sha256 in docs/grader_freeze.json. Graders belong to
                     the integrator (rule 1): a lane PR that edits one goes red here until the integrator
                     reviews it and re-freezes in the merge.
  G2 pre-registered  every docs/experiments.md row whose status is running/done names its North Star
                     line, success bar and nights needed (rule 2). PENDING while the table has no
                     "success bar" / "nights needed" columns.
  G3 no moved goals  with --base: a row already running in base keeps its hypothesis, line, success bar
                     and nights needed (rule 2).
Serves: release gate (every line). Owner: integrator.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FREEZE = "docs/grader_freeze.json"
EXPERIMENTS = "docs/experiments.md"
# The graders: what turns a night into a number. Add a file here only with a re-freeze.
GRADERS = (
    "redzone/grading.py",
    "scripts/collect_live_test.py",
    "scripts/license_check.py",
    "scripts/label_leak_check.py",
    "redzone/lab/syncgrade.py",
    "redzone/lab/primegrade.py",
    "redzone/lab/delaygrade.py",
    "scripts/delay_grade.py",
    "scripts/clock_truth_grade.py",
    "scripts/buffer_shadow_grade.py",
    "scripts/rc8_gate_report.py",
    "scripts/entry_autopsy.py",
    "scripts/attention_labels.py",
    "scripts/wv_aux_label.py",
    "redzone/lab/outcome_label.py",
    "scripts/sync_grade.py",
    "agent/distill/evaluate.py",
    "agent/distill/gold.py",
    "scripts/catchup_ratchet.py",
    "redzone/lab/reactgrade.py",
    "scripts/reactgrade.py",
    "scripts/data_landing_grade.py",
    "scripts/family_holm.py",
    "docs/families.json",
    "redzone/lab/backtest.py",
    "redzone/lab/cutgrade.py",
    "redzone/lab/sportsgrade.py",
    "redzone/lab/oracle.py",
    "redzone/lab/oracle_window.py",
    "scripts/gi_v3_nba.py",
    "redzone/lab/leverage.py",
    "redzone/lab/splits.py",
    "redzone/lab/evaldata.py",
    "scripts/northstar.py",
    "scripts/release_gate.py",
    "scripts/eval.py",
    "scripts/eval_sports.py",
    "scripts/ci_scorecard.py",
    "scripts/leak_check.py",
    "scripts/grading_check.py",
    "redzone/lab/worth.py",       # EXP-040 worth line (#205), integrator-frozen Nov 5
    "scripts/worth_line.py",
)
FROZEN_COLS = ("hypothesis", "north star line", "success bar", "nights needed")
LIVE_STATUS = re.compile(r"^\s*(running|done)\b", re.I)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def g1_freeze(root: Path) -> list[tuple[str, str]]:
    f = root / FREEZE
    if not f.exists():
        return [("FAIL", f"G1 {FREEZE} missing: the integrator runs --refreeze")]
    frozen = json.loads(f.read_text()).get("files", {})
    out = []
    for p in GRADERS:
        if not (root / p).exists():
            out.append(("FAIL", f"G1 grader {p} is gone but listed"))
        elif p not in frozen:
            out.append(("FAIL", f"G1 grader {p} is not frozen yet"))
        elif sha(root / p) != frozen[p]:
            out.append(("FAIL", f"G1 grader {p} changed since the freeze: graders belong to the integrator "
                                f"(docs/grading.md rule 1); it reviews the change and re-freezes in the merge"))
    return out or [("PASS", f"G1 {len(GRADERS)} graders match the freeze")]


def refreeze(root: Path) -> None:
    f = root / FREEZE
    old = json.loads(f.read_text()) if f.exists() else {}
    doc = {"version": int(old.get("version", 0)) + 1,
           "frozen_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
           "by": "integrator",
           "files": {p: sha(root / p) for p in GRADERS}}
    f.write_text(json.dumps(doc, indent=2) + "\n")
    print(f"froze {len(GRADERS)} graders as version {doc['version']}")


def rows(text: str) -> list[dict]:
    """Every table row with an EXP id, keyed by lower-cased header."""
    out, head = [], None
    for line in text.splitlines():
        if not line.startswith("|"):
            head = None
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if head is None:
            head = [c.lower() for c in cells]
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        r = dict(zip(head, cells))
        if re.match(r"EXP-\d+", r.get("id", "")):
            out.append(r)
    return out


def g2_prereg(text: str) -> list[tuple[str, str]]:
    rs = rows(text)
    if not rs:
        return [("PENDING", "G2 no experiment rows yet")]
    if not all(c in rs[0] for c in FROZEN_COLS):
        missing = [c for c in FROZEN_COLS if c not in rs[0]]
        return [("PENDING", f"G2 {EXPERIMENTS} has no {', '.join(missing)} column(s) yet (rule 2)")]
    out = []
    for r in rs:
        if LIVE_STATUS.match(r.get("status", "")):
            empty = [c for c in FROZEN_COLS if not r.get(c) or r[c] in ("-", "?", "TBD")]
            if empty:
                out.append(("FAIL", f"G2 {r['id']} is {r['status'].split()[0]} without {', '.join(empty)}: "
                                    f"pre-register before running (rule 2)"))
    return out or [("PASS", "G2 every running/done experiment is pre-registered")]


def g3_goalposts(text: str, base_text: str) -> list[tuple[str, str]]:
    now = {r["id"]: r for r in rows(text)}
    out = []
    for r in rows(base_text):
        if not LIVE_STATUS.match(r.get("status", "")) or r["id"] not in now:
            continue
        moved = [c for c in FROZEN_COLS if c in r and now[r["id"]].get(c) != r[c]]
        if moved:
            out.append(("FAIL", f"G3 {r['id']} was already running and its {', '.join(moved)} changed: "
                                f"no moving goalposts (rule 2); register a new EXP id instead"))
    return out or [("PASS", "G3 no running experiment moved its goalposts")]


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", help="git ref to compare pre-registrations against (G3)")
    ap.add_argument("--refreeze", action="store_true", help="integrator only: re-stamp the grader freeze")
    a = ap.parse_args(argv)
    if a.refreeze:
        refreeze(root)
        return 0
    res = g1_freeze(root)
    exp = root / EXPERIMENTS
    text = exp.read_text() if exp.exists() else ""
    res += g2_prereg(text)
    if a.base:
        base = subprocess.run(["git", "show", f"{a.base}:{EXPERIMENTS}"], cwd=root,
                              capture_output=True, text=True)
        res += g3_goalposts(text, base.stdout) if base.returncode == 0 else [("PENDING", "G3 no base file")]
    for state, msg in res:
        print(f"{state:7} {msg}")
    return 1 if any(s == "FAIL" for s, _ in res) else 0


if __name__ == "__main__":
    sys.exit(main())
