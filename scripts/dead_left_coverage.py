"""[timing.dead_v2] Coverage of the dead-time-left table on recorded dev nights: how often the real stoppage outlasted the bound.

    python scripts/dead_left_coverage.py                 # nfl nba nhl; valid nights (held out) and train nights (in-sample)
    python scripts/dead_left_coverage.py --verify-build  # first rebuild the table from the train nights: it must match the file

For every stoppage (scripts/measure_dead_left.py rows: feed stoppage to the real restart) and every elapsed landmark t it outlasted,
the reader (scoring/dead_left_lite.guaranteed_left) promises B seconds more at risk level alpha; the promise held when the
stoppage really ran at least t + B. Coverage = held / promised-on pairs, per league and class, at alpha 0.01 and 0.05. The
table was built from the TRAIN nights only (redzone.lab.splits, eval-data-v1 pin), so the VALID nights are held out and the
train nights are in-sample. The locked nights are never read. Numbers only: no game ids, no rows.
Why: the bound is only worth trusting on an open stoppage if it holds at least 99% / 95% of the time on nights it never saw.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from redzone.scoring import dead_left_lite as L  # noqa: E402

TARGETS = {0.01: 0.99, 0.05: 0.95}


def coverage(league: str, rows, alphas=tuple(TARGETS), marks=L.LANDMARKS, reader=L.guaranteed_left) -> dict:
    """(kind, class) -> {"n": stoppages, alpha: [pairs promised, pairs held, seconds promised]} over every landmark a stoppage
    outlasted. A pair is counted only when the promise is above 0 (a 0 promise always holds and lets nothing run). The kind's
    pooled class gets every stoppage of the kind."""
    out: dict = defaultdict(lambda: {"n": 0, **{a: [0, 0, 0.0] for a in alphas}})
    for kind, cls, length in rows:
        for c in dict.fromkeys((cls, kind)):
            cell = out[(kind, c)]
            cell["n"] += 1
            for t in marks:
                if length <= t:
                    break
                for a in alphas:
                    b = reader(league, c, float(t), a, kind=kind)
                    if b > 0.0:
                        cell[a][0] += 1
                        cell[a][1] += int(length >= t + b)
                        cell[a][2] += b
    return dict(out)


def rate(v) -> float | None:
    return None if not v[0] else v[1] / v[0]


def table_lines(league: str, which: str, cov: dict) -> list[str]:
    lines = []
    for (kind, cls), c in sorted(cov.items()):
        parts = []
        for a, need in TARGETS.items():
            r = rate(c[a])
            ok = "" if r is None else (" ok" if r >= need else " LOW")
            parts.append(f"{'-' if r is None else f'{100 * r:.1f}%'}{ok} of {c[a][0]}")
        lines.append(f"| {league} | {which} | {kind} / {cls} | {c['n']} | " + " | ".join(parts) + " |")
    return lines


def main() -> int:
    import measure_dead_left as mdl
    from redzone.models import League
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("leagues", nargs="*", default=["nfl", "nba", "nhl"])
    ap.add_argument("--verify-build", action="store_true", help="rebuild from train nights and compare with the table file")
    ap.add_argument("--json", type=Path, help="also write the counts here")
    a = ap.parse_args()
    table = json.loads(L.TABLE_FILE.read_text())
    print("| league | nights | kind / class | stoppages | 99% level (alpha 0.01): held of promised | "
          "95% level (alpha 0.05): held of promised |")
    print("|---|---|---|---|---|---|")
    dump, low = {}, 0
    for lg in a.leagues:
        for which in ("valid", "train"):
            rows, n_nights = mdl.stoppages(League(lg), which)
            if which == "train":
                if n_nights != table["train_nights"].get(lg):
                    print(f"{lg}: {n_nights} train nights now, the table says {table['train_nights'].get(lg)}: split moved")
                    return 2
                if a.verify_build and mdl.cells(rows) != table["table"][lg]:
                    print(f"{lg}: the train nights do not rebuild the table")
                    return 2
            cov = coverage(lg, rows)
            label = f"{which} ({n_nights} nights, {'held out' if which == 'valid' else 'in-sample'})"
            for line in table_lines(lg, label, cov):
                print(line)
            low += sum(1 for c in cov.values() for al, need in TARGETS.items() if (r := rate(c[al])) is not None and r < need)
            dump[f"{lg}/{which}"] = {f"{k}/{c}": {"n": v["n"], **{str(al): v[al] for al in TARGETS}} for (k, c), v in cov.items()}
    if a.verify_build:
        print("build check: every league's train nights rebuild its table exactly")
    if a.json:
        a.json.write_text(json.dumps(dump, indent=1) + "\n")
    print(f"cells under target: {low}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
