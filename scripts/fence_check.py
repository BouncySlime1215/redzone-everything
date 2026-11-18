"""Leak-fence report: which shipped model files the fence would refuse, and whether the pinned archive's
train/valid and locked games are disjoint by content hash (redzone/lab/fence.py).

Why: WORKPLAN v2 task F-09: the fence must be checkable from the command line, next to the frozen
scripts/leak_check.py (which this does not change).

    python scripts/fence_check.py              # report; exit 1 only on a hash collision between train and locked
    python scripts/fence_check.py --strict     # also exit 1 when any model file would be refused
    python scripts/fence_check.py --json

Model lines say REFUSED or OK; unstamped models are REFUSED (they carry no training cutoff), which is
the state of every shipped model today, so the default run reports them without failing.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ARCHIVE = ROOT / "data" / "archive"
LEAGUES = ("nfl", "nba", "nhl")
MODEL_GLOBS = ("redzone/**/models/*.npz", "redzone/**/brain_models/*.npz", "models/**/*.npz")


def model_rows(root: Path = ROOT) -> list[tuple[str, str, str]]:
    from redzone.lab import fence
    rows = []
    seen: set[Path] = set()
    for pat in MODEL_GLOBS:
        for p in sorted(root.glob(pat)):
            if p in seen:
                continue
            seen.add(p)
            try:
                bad = fence.model_problems(fence.read_meta(p))
            except Exception as e:     # an unreadable meta is a refusal, not a crash
                bad = [f"meta unreadable: {type(e).__name__}"]
            rows.append(("REFUSED" if bad else "OK", f"model {p.relative_to(root)}", "; ".join(bad) or "fence clear"))
    return rows


def archive_rows(archive: Path = ARCHIVE) -> list[tuple[str, str, str]]:
    from redzone.lab import evaldata, fence, splits
    rows = []
    for lg in LEAGUES:
        d = archive / lg
        if not d.exists():
            rows.append(("PENDING", f"hash_disjoint_{lg}", "no data/archive (python scripts/fetch_eval_data.py)"))
            continue
        an = splits.archive_nights(d, evaldata.window_end(lg, None))
        s = splits.league_split(lg, an)
        hits = fence.disjoint_by_hash(fence.game_files(d, an, s.train + s.valid), fence.game_files(d, an, s.test))
        rows.append(("FAIL" if hits else "PASS", f"hash_disjoint_{lg}",
                     f"{len(hits)} locked game file(s) also in train/valid: {', '.join(sorted(hits.values())[:3])}"
                     if hits else f"{len(s.train) + len(s.valid)} fit nights vs {len(s.test)} locked nights, no shared file"))
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="fence_check")
    ap.add_argument("--strict", action="store_true", help="exit 1 when any model file would be refused")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    rows = archive_rows() + model_rows()
    if a.json:
        print(json.dumps([{"status": s, "check": c, "detail": d} for s, c, d in rows], indent=1))
    else:
        for s, c, d in rows:
            print(f"{s:8} {c}: {d}")
    fail = any(r[0] == "FAIL" for r in rows) or (a.strict and any(r[0] == "REFUSED" for r in rows))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
