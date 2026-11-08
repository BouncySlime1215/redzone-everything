"""Disk guard for the Mac (~5 GB free): what is using space in this checkout, and a safe cleanup
of the transfer folders that cloud build threads leave behind.

    .venv/bin/python scripts/disk_guard.py                     # report only (default; changes nothing)
    .venv/bin/python scripts/disk_guard.py --json
    .venv/bin/python scripts/disk_guard.py --clean .rz_tmp .xfer         # shows what WOULD go
    .venv/bin/python scripts/disk_guard.py --clean .rz_tmp .xfer --yes   # deletes them

Only the transfer folders below can be cleaned, and only when nothing in them changed in the
last --min-age-h hours (a thread may still be copying). Never the archive, recordings, models,
config or venvs: those are reported so a person can decide.
Why: Keeps about 5 GB free on the Mac by cleaning transfer folders (docstring, resource governor commit).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Left by cloud threads that moved code and data between the Mac and the cloud (all git-ignored).
TRANSFERS = (".rz_tmp", ".xfer", ".claude-xfer", ".rz.bundle")
# Reported, never deleted here.
WATCHED = ("data/archive", "data/reels", "data/imgcache", "data/training", "data/demo", "data/reports",
           "data/redzone.db", "data/redzone.db-wal", "data/service.log", "agent/model", ".venv", ".venv-agent")
TRANSFER_LIMIT_MB = 300      # more than this in transfer folders is flagged
HF_MODELS = Path.home() / ".cache" / "huggingface" / "hub"


def usage(path: Path) -> tuple[int, float, int]:
    """(bytes, newest mtime, files) under path, without following symlinks."""
    try:
        st = path.lstat()
    except OSError:
        return 0, 0.0, 0
    if not path.is_dir() or path.is_symlink():
        return st.st_size, st.st_mtime, 1
    total, newest, files = 0, st.st_mtime, 0
    stack = [path]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        s = e.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    newest = max(newest, s.st_mtime)
                    if e.is_dir(follow_symlinks=False):
                        stack.append(Path(e.path))
                    else:
                        total += s.st_size
                        files += 1
        except OSError:
            continue
    return total, newest, files


def contents(path: Path, limit: int = 12) -> list[dict]:
    """The biggest entries directly inside a folder: what a person checks before deleting it."""
    if not path.is_dir() or path.is_symlink():
        return []
    rows = []
    try:
        for e in path.iterdir():
            b, m, n = usage(e)
            rows.append({"name": e.name + ("/" if e.is_dir() and not e.is_symlink() else ""),
                         "mb": round(b / 1e6, 1), "files": n, "modified": m})
    except OSError:
        return []
    rows.sort(key=lambda r: -r["mb"])
    return rows[:limit]


def report(root: Path = ROOT) -> dict:
    out: dict = {"root": str(root)}
    try:
        out["free_mb"] = round(shutil.disk_usage(root).free / 1e6)
    except OSError:
        out["free_mb"] = None
    rows = []
    for name in TRANSFERS:
        p = root / name
        if not p.exists() and not p.is_symlink():
            continue
        b, m, n = usage(p)
        rows.append({"path": name, "mb": round(b / 1e6, 1), "files": n, "modified": m, "contents": contents(p)})
    out["transfers"] = rows
    out["transfer_mb"] = round(sum(r["mb"] for r in rows), 1)
    out["watched"] = []
    for name in WATCHED:
        p = root / name
        if p.exists():
            b, m, n = usage(p)
            out["watched"].append({"path": name, "mb": round(b / 1e6, 1), "files": n})
    if HF_MODELS.is_dir():
        b, _, n = usage(HF_MODELS)
        out["watched"].append({"path": str(HF_MODELS), "mb": round(b / 1e6, 1), "files": n})
    try:
        from redzone.governor import load_options
        opts = load_options(root)
        free = out["free_mb"]
        out["level"] = (None if free is None else "critical" if free < opts["disk_critical_mb"]
                        else "low" if free < opts["disk_low_mb"] else "ok")
    except Exception:
        out["level"] = None
    return out


def clean(names: list[str], root: Path = ROOT, yes: bool = False, min_age_h: float = 2.0,
          now: float | None = None) -> list[str]:
    """Delete transfer folders by name. Returns one line per name saying what happened."""
    now = now or time.time()
    lines = []
    for name in names:
        if name not in TRANSFERS:
            lines.append(f"{name}: not a transfer folder; refusing (only {', '.join(TRANSFERS)})")
            continue
        p = root / name
        if not p.exists() and not p.is_symlink():
            lines.append(f"{name}: not there")
            continue
        if p.resolve().parent != root.resolve() and not p.is_symlink():
            lines.append(f"{name}: resolves outside the checkout; refusing")
            continue
        b, newest, n = usage(p)
        age_h = (now - newest) / 3600
        if age_h < min_age_h:
            lines.append(f"{name}: changed {age_h * 60:.0f} min ago (a thread may still be using it); skipped")
            continue
        what = f"{name}: {b / 1e6:.0f} MB in {n} files, untouched for {age_h:.1f} h"
        if not yes:
            lines.append(what + " -> would delete (add --yes)")
            continue
        try:
            if p.is_symlink() or not p.is_dir():
                p.unlink()          # a link is removed, never what it points to
            else:
                shutil.rmtree(p)
            lines.append(what + " -> deleted")
        except OSError as e:
            lines.append(f"{name}: delete failed ({e})")
    return lines


def show(r: dict) -> None:
    free = r["free_mb"]
    print(f"Free disk: {free / 1000:.1f} GB ({r['level']})" if free is not None else "Free disk: unknown")
    flag = "  <- clean these up when their threads are done" if r["transfer_mb"] > TRANSFER_LIMIT_MB else ""
    print(f"\nTransfer folders: {r['transfer_mb']:.0f} MB{flag}")
    for t in r["transfers"]:
        print(f"  {t['path']:<14} {t['mb']:>8.1f} MB  {t['files']} files, last change "
              f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(t['modified']))}")
        for c in t["contents"]:
            print(f"      {c['name']:<40} {c['mb']:>8.1f} MB")
    print("\nOther big things (never deleted by this script):")
    for w in sorted(r["watched"], key=lambda w: -w["mb"]):
        print(f"  {w['path']:<40} {w['mb']:>8.1f} MB")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--clean", nargs="+", metavar="FOLDER", help=f"transfer folders to remove: {', '.join(TRANSFERS)}")
    ap.add_argument("--yes", action="store_true", help="really delete (without it --clean only shows)")
    ap.add_argument("--min-age-h", type=float, default=2.0)
    a = ap.parse_args(argv)
    if a.clean:
        for line in clean(a.clean, yes=a.yes, min_age_h=a.min_age_h):
            print(line)
        return 0
    r = report()
    if a.json:
        print(json.dumps(r, indent=1))
    else:
        show(r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
