"""Picker v2 step-0 report: re-fit Nick's Game Picker answers offline and print aggregates (redzone/lab/taste).

Why: WORKPLAN v2 D-01 / track D experiment D1 step 0: the page fit, a league-only baseline and the factorised
Bradley-Terry model scored by grouped cross-validation, plus the active-vs-random replay. The answers live outside the
repo (they are Nick's); this script reads them by path and prints only counts, accuracies and weights.

    python scripts/picker_report.py PICKS.json [--stated stated.json] [--page-fit app_value_page_fit.json] [--out r.json]
    python scripts/picker_report.py --package-pick-schema       # the package-pick label fields (shared with E-03)

Small CPU fits: about a minute on the Mac for 129 answers, 3 seeds, 5 folds.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from redzone.lab.taste.package_pick import PackageClip, PackagePickLabel  # noqa: E402
from redzone.lab.taste.picker_report import build_report  # noqa: E402


def _load_answers(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    if isinstance(data, dict):
        data = data.get("answers") or data.get("docs") or []
    return [a for a in data if isinstance(a, dict)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("picks", nargs="?", type=Path, help="the exported answers (a JSON list)")
    ap.add_argument("--stated", type=Path, help="stated.json: Nick's stated sport ranking, used as the sport prior")
    ap.add_argument("--page-fit", type=Path, help="app_value_page_fit.json: the page's own export, for the port check")
    ap.add_argument("--out", type=Path, help="write the full report JSON here")
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--n-boot", type=int, default=2000)
    ap.add_argument("--package-pick-schema", action="store_true")
    a = ap.parse_args(argv)
    if a.package_pick_schema:
        print(json.dumps({cls.__name__: [f.name for f in dataclasses.fields(cls)] for cls in (PackageClip, PackagePickLabel)},
                         indent=1))
        return 0
    if not a.picks:
        ap.error("PICKS.json is required")
    answers = _load_answers(a.picks)
    prior = json.loads(a.stated.read_text()).get("priorMeans") if a.stated else None
    exported = json.loads(a.page_fit.read_text()) if a.page_fit else None
    rep = build_report(answers, prior, exported, seeds=tuple(range(a.seeds)), k=a.folds, n_boot=a.n_boot)
    text = json.dumps(rep, indent=1)
    if a.out:
        a.out.write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
