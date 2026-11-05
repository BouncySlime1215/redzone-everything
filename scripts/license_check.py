"""F8 license-tag check (Test Factory; Nick, Nov 4 2026 11:20 PM ET, data B).

Every dataset row, pack manifest and model card (grades.json) names its source(s) and a license. A row whose
source has restrictive terms (docs/datasources/terms.json) must carry the tag
"personal-use, not for commercial release", in "tag", "tags" or "license". An unknown source is flagged
until the scouts classify it. Never PASSes a row with no license at all.

  python scripts/license_check.py                     # the repo's JSON ledgers (docs/datasources/**/*.json)
  python scripts/license_check.py out/manifest.json   # a pack, a shard index or a grades.json (Kaggle/Colab)
Exit 1 on any FAIL. Unknown sources are a WARN (exit 0) unless --strict.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TERMS = ROOT / "docs/datasources/terms.json"
SKIP = {"terms.json"}


def _norm(s) -> str:
    return str(s or "").strip().lower().replace("-", "_").replace(" ", "_")


def _sources(row: dict) -> list[str]:
    v = row.get("sources", row.get("source"))
    if isinstance(v, str):
        return [v]
    if isinstance(v, list):
        return [str(x) for x in v]
    return []


def _tagged(row: dict, tag: str) -> bool:
    vals = [row.get("tag"), row.get("license"), *(row.get("tags") or [])]
    return any(isinstance(x, str) and tag in x for x in vals)


def _rows(doc) -> list[dict]:
    """The records a file holds: a top-level dict with sources, or rows/shards/datasets/files lists."""
    if isinstance(doc, list):
        return [r for r in doc if isinstance(r, dict)]
    if not isinstance(doc, dict):
        return []
    # a model card names its sources as strings; a ledger may list them as records under "sources"
    srcs = doc.get("sources")
    card = "source" in doc or (isinstance(srcs, list) and all(isinstance(x, str) for x in srcs)) or isinstance(srcs, str)
    out = [doc] if card else []
    for k in ("rows", "shards", "datasets", "files", "sources_used", "sources"):
        if k == "sources" and card:
            continue
        if isinstance(doc.get(k), list):
            out += [r for r in doc[k] if isinstance(r, dict)]
    return out


def check(paths: list[Path], terms: dict) -> list[tuple[str, str, str]]:
    """[(level, where, why)] with level FAIL or WARN."""
    cls = {_norm(k): v for k, v in (terms.get("sources") or {}).items()}
    tag = terms.get("tag", "personal-use, not for commercial release")
    out = []
    for p in paths:
        try:
            doc = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        for i, r in enumerate(_rows(doc)):
            where = f"{p.relative_to(ROOT) if p.is_relative_to(ROOT) else p}[{i}]"
            srcs = _sources(r)
            if not srcs:
                continue
            if not r.get("license") and not _tagged(r, tag):
                out.append(("FAIL", where, f"{', '.join(srcs)}: no license line"))
                continue
            for s in srcs:
                c = cls.get(_norm(s))
                if c is None:
                    out.append(("WARN", where, f"{s}: not classified in docs/datasources/terms.json"))
                elif c == "restrictive" and not _tagged(r, tag):
                    out.append(("FAIL", where, f"{s} has restrictive terms: needs the tag '{tag}'"))
    return out


def repo_files() -> list[Path]:
    d = ROOT / "docs/datasources"
    return sorted(p for p in d.rglob("*.json") if p.name not in SKIP) if d.is_dir() else []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="*", type=Path)
    ap.add_argument("--strict", action="store_true", help="an unclassified source fails too")
    a = ap.parse_args(argv)
    terms = json.loads(TERMS.read_text()) if TERMS.exists() else {}
    found = check([p.resolve() for p in a.paths] or repo_files(), terms)
    for lvl, where, why in found:
        print(f"{lvl:5} {where}: {why}")
    bad = [f for f in found if f[0] == "FAIL" or (a.strict and f[0] == "WARN")]
    print(f"license check: {len(bad)} failing, {sum(f[0] == 'WARN' for f in found)} unclassified")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
