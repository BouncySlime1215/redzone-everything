#!/usr/bin/env python3
"""Switch the default eval-data pin to a newly published release (Fri Nov 6: eval-data-v3), in one go.

    python scripts/switch_eval_pin.py --tag eval-data-v3 --dry-run   # what would change, nothing written
    python scripts/switch_eval_pin.py --tag eval-data-v3             # rewrite the pin, then open a PR

Runs in a cloud session (release assets download fine; no ESPN calls). It:
  1. downloads the release's `<tag>.lock.json` (eval-data-v2.yml publishes it next to the tarball) and checks
     it names the tag and the tarball's sha256;
  2. writes it over eval-data.lock.json (the default pin; RZ_EVAL_LOCK still picks another);
  3. points CI at the tag (ci.yml EVAL_DATA_TAG; northstar.yml's step label; CLAUDE.md's run line);
  4. fetches the tarball against the new pin (scripts/fetch_eval_data.py --force) and runs leak_check.py:
     a FAIL (a locked night that was dev in the old pin, a model trained past the locked start) stops here;
  5. writes agent/kaggle/assets/locked_<tag>.json: the new locked nights and games per league, the list the
     Kaggle builds must never download (build_moments_kaggle.py --locked).
Then it prints what is left by hand: the decisions.md line (CI enforces it), the docs/eval-data.md row, the
--locked default, and the new baseline numbers for the PR. Nothing is pushed.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

PIN = ROOT / "eval-data.lock.json"
EDITS = [  # (file, pattern, replacement with {tag})
    (".github/workflows/ci.yml", r"(EVAL_DATA_TAG: )eval-data-v\d+", r"\g<1>{tag}"),
    (".github/workflows/northstar.yml", r"pinned eval data \(eval-data-v\d+", r"pinned eval data ({tag}"),
    ("CLAUDE.md", r"(# pinned eval data )eval-data-v\d+", r"\g<1>{tag}"),
]


def fetch_lock(tag: str, repo: str) -> dict:
    import fetch_eval_data as fe
    with tempfile.TemporaryDirectory() as d:
        dest = Path(d) / f"{tag}.lock.json"
        fe.download(f"https://github.com/{repo}/releases/download/{tag}/{tag}.lock.json", dest)
        lock = json.loads(dest.read_text())
    if lock.get("tag") != tag or not re.fullmatch(r"[0-9a-f]{64}", str(lock.get("sha256", ""))):
        raise SystemExit(f"{tag}.lock.json does not pin {tag} with a sha256: {lock.get('tag')!r}")
    return lock


def edit_files(tag: str, dry: bool) -> list[str]:
    done = []
    for rel, pat, rep in EDITS:
        p = ROOT / rel
        s = p.read_text()
        new, n = re.subn(pat, rep.format(tag=tag), s)
        if n and new != s:
            done.append(f"{rel}: {n} line(s)")
            if not dry:
                p.write_text(new)
    return done


def locked_assets(lock: dict) -> dict:
    """The new pin's locked nights and games per league (the newest TEST_FRAC of the window's nights)."""
    from redzone.lab import splits
    out = {"source": f"{lock['tag']} (sha {lock['sha256'][:8]}) split by redzone/lab/splits.py",
           "nights": {}, "games": {}}
    for lg, w in sorted((lock.get("window") or {}).items()):
        an = {n: g for n, g in splits.archive_nights(ROOT / "data" / "archive" / lg).items()
              if w["start"] <= n <= w["end"]}
        s = splits.split_nights(an, dev_before=(lock.get("dev_before") or {}).get(lg))
        out["nights"][lg] = list(s.test)
        out["games"][lg] = sorted(g for n in s.test for g in an[n])
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", required=True, help="the published release, e.g. eval-data-v3")
    ap.add_argument("--repo", default="BouncySlime1215/redzone-everything")
    ap.add_argument("--dry-run", action="store_true", help="download and report only; write nothing")
    a = ap.parse_args(argv)
    old = json.loads(PIN.read_text()) if PIN.exists() else {}
    lock = fetch_lock(a.tag, a.repo)
    print(f"pin: {old.get('tag')} -> {lock['tag']} (sha {lock['sha256'][:12]}, {lock.get('files')} files)")
    for lg, w in sorted((lock.get("window") or {}).items()):
        print(f"  {lg}: {w['start']}..{w['end']}")
    for line in edit_files(a.tag, a.dry_run):
        print(("would edit " if a.dry_run else "edited ") + line)
    if a.dry_run:
        print("dry run: nothing written")
        return 0
    if old:
        prev = ROOT / f"{old['tag']}.lock.json"
        if not prev.exists():
            prev.write_text(json.dumps(old, indent=1) + "\n")    # keep the old pin by name (RZ_EVAL_LOCK)
            print(f"kept the old pin as {prev.name}")
    PIN.write_text(json.dumps(lock, indent=1) + "\n")
    py = sys.executable
    subprocess.run([py, "scripts/fetch_eval_data.py", "--force"], cwd=ROOT, check=True)
    lc = subprocess.run([py, "scripts/leak_check.py"], cwd=ROOT)
    if lc.returncode != 0:
        print("leak_check FAILED on the new pin: fix that before switching (see above)")
        return 1
    asset = ROOT / "agent" / "kaggle" / "assets" / f"locked_{a.tag.replace('eval-data-', '')}.json"
    asset.write_text(json.dumps(locked_assets(lock), indent=1) + "\n")
    print(f"wrote {asset.relative_to(ROOT)}")
    print("\nLeft by hand, in the same PR:\n"
          f"  - docs/memory/decisions.md: the default pin is now {a.tag} (CI enforces a line)\n"
          f"  - docs/eval-data.md: the release row and URL -> {a.tag}\n"
          f"  - agent/kaggle/build_moments_kaggle.py --locked default -> {asset.relative_to(ROOT)}\n"
          "  - the new baseline: python scripts/northstar.py --split both (dev + locked, locked read once)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
