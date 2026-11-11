"""File claims: one writer per file across parallel agents (docs/coordination.md "Claims").

A claim is docs/claims/<card>.json, committed on the agent's branch before its first edit:
  {"card": "...", "session": "...", "branch": "...", "files": ["redzone/decider/cuts.py", "tests/test_cuts*.py"],
   "started": <epoch>, "expires": <epoch>}

    python scripts/claims.py new CARD --files 'redzone/x.py' 'tests/test_x*.py' [--hours 24] [--session NAME]
    python scripts/claims.py list
    python scripts/claims.py done CARD
    python scripts/claims.py check --base origin/integration/next      # CI: exit 1 on overlap

`check` fails when the branch changes a file that another open (unexpired) claim covers. Claims are
optimistic: nothing blocks editing, CI catches overlap (Cursor found blocking locks made 20 agents act like 2-3).
Shared hot files (decisions.md, lessons.md, status.md, citymap.*, state-machines.md) are never claimed:
agents append fragments and the integration train folds them in.
Stdlib only.
Why: One writer per file across parallel agents, via claim files (docstring, coordination rules commit).
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "docs" / "claims"
# Shared, generated or append-only files are not claimable (one writer: the integration train).
UNCLAIMABLE = ["docs/memory/decisions.md", "docs/memory/lessons.md", "docs/status.md", "docs/handoff.md",
               "docs/citymap.*", "docs/state-machines.md", "docs/README.md", "docs/research/README.md",
               "docs/claims/*"]


def load(d: Path | None = None, now: float | None = None) -> list[dict]:
    d = DIR if d is None else d
    now = time.time() if now is None else now
    out = []
    for p in sorted(d.glob("*.json")):
        try:
            c = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        c.setdefault("card", p.stem)
        c["open"] = float(c.get("expires", 0)) > now
        out.append(c)
    return out


def covers(claim: dict, path: str) -> bool:
    return any(fnmatch.fnmatch(path, g) for g in claim.get("files", []))


def overlaps(changed: list[str], claims: list[dict], own: set[str]) -> list[tuple[str, str]]:
    """(file, card) pairs where a changed file sits in someone else's open claim."""
    hits = []
    for f in changed:
        if any(fnmatch.fnmatch(f, u) for u in UNCLAIMABLE):
            continue
        for c in claims:
            if c["open"] and c["card"] not in own and covers(c, f):
                hits.append((f, c["card"]))
    return hits


def changed_files(base: str) -> list[str]:
    r = subprocess.run(["git", "diff", "--name-only", f"{base}...HEAD"], cwd=ROOT, capture_output=True, text=True)
    return [x for x in r.stdout.splitlines() if x]


def own_cards(changed: list[str]) -> set[str]:
    return {Path(f).stem for f in changed if f.startswith("docs/claims/") and f.endswith(".json")}


def git_branch() -> str:
    r = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    return r.stdout.strip()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new")
    n.add_argument("card")
    n.add_argument("--files", nargs="+", required=True)
    n.add_argument("--hours", type=float, default=24)
    n.add_argument("--session", default="")
    sub.add_parser("list")
    d = sub.add_parser("done")
    d.add_argument("card")
    ch = sub.add_parser("check")
    ch.add_argument("--base", default="origin/integration/next")
    ch.add_argument("--card", action="append", default=[])
    a = ap.parse_args(argv)

    if a.cmd == "new":
        bad = [f for f in a.files if any(fnmatch.fnmatch(f, u) for u in UNCLAIMABLE)]
        if bad:
            print(f"not claimable (shared, append fragments instead): {bad}")
            return 1
        mine = {a.card}
        clash = [(f, c["card"]) for f in a.files for c in load() if c["open"] and c["card"] not in mine
                 and (covers(c, f) or any(fnmatch.fnmatch(g, f) for g in c.get("files", [])))]
        if clash:
            print("overlaps open claims: " + ", ".join(f"{f} ({c})" for f, c in clash))
            return 1
        DIR.mkdir(parents=True, exist_ok=True)
        now = time.time()
        rec = {"card": a.card, "session": a.session, "branch": git_branch(), "files": a.files,
               "started": round(now), "expires": round(now + a.hours * 3600)}
        (DIR / f"{a.card}.json").write_text(json.dumps(rec, indent=2) + "\n")
        print(f"claimed {a.files} for {a.card} until {time.strftime('%Y-%m-%d %H:%M', time.localtime(rec['expires']))}")
        return 0
    if a.cmd == "list":
        for c in load():
            print(f"{'open   ' if c['open'] else 'expired'} {c['card']:<24} {c.get('branch', ''):<28} {', '.join(c.get('files', []))}")
        return 0
    if a.cmd == "done":
        p = DIR / f"{a.card}.json"
        if p.exists():
            p.unlink()
        print(f"released {a.card}")
        return 0
    changed = changed_files(a.base)
    hits = overlaps(changed, load(), own_cards(changed) | set(a.card))
    if hits:
        for f, c in hits:
            print(f"OVERLAP {f} is claimed by {c}")
        print("Wait for that claim, or coordinate through the planner (docs/coordination.md).")
        return 1
    print(f"claims ok ({len(changed)} changed files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
